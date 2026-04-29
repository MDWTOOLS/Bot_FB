#!/usr/bin/env python3
"""
FB Auto-Comment Bot v11 - Cookie Auth Edition
- Cookie-based authentication (no email/password login)
- Stream mode: find post -> comment immediately -> next post
- No limits: continuous scrolling & commenting
- Queue architecture: ALL Playwright ops in single thread (thread-safe)
- Screenshot button (one-time, for checking)
- RC (remote control) with D-Pad arrow keys
"""

import json, time, random, os, re, sys, datetime, threading, base64, traceback, queue as _queue
from flask import Flask, render_template_string, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ===========================================================
#  CONFIG
# ===========================================================
# Auto-read cookie from terminal environment variable
ENV_COOKIE = os.environ.get("FB", "").strip()

DIR = os.path.dirname(os.path.abspath(__file__))
BROWSER_DATA = os.path.join(DIR, "browser_data")
WEB_PORT = int(os.environ.get("PORT", "8080"))
MAX_RETRY = 2
RETRY_WAIT = 3
COMMENT_WAIT = 5
CEKLIST = os.path.join(DIR, "ceklist.txt")
RESTRICTED = os.path.join(DIR, "restricted.txt")

# ===========================================================
#  COLORS (terminal)
# ===========================================================
class C:
    R="\033[0m"; B="\033[1m"; D="\033[2m"; G="\033[32m"
    Y="\033[33m"; CY="\033[36m"; M="\033[35m"
    RE="\033[31m"; GR="\033[90m"

def pr(msg):
    print(msg)

def flog(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(os.path.join(DIR, "bot_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except:
        pass

# ===========================================================
#  SHARED STATE
# ===========================================================
S = {
    "phase": "IDLE", "msg": "Menunggu cookie...", "err": "", "name": "",
    "ss_b64": None, "logs": [],
    "ok": 0, "fail": 0, "blocked": 0, "cycle": 0,
}
S_lock = threading.Lock()

def sget(k):
    with S_lock:
        return S[k]

def sset(k, v):
    with S_lock:
        S[k] = v

def slog(msg, tag="INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    e = f"[{ts}] [{tag}] {msg}"
    with S_lock:
        S["logs"].insert(0, e)
        if len(S["logs"]) > 100:
            S["logs"] = S["logs"][:100]
    pr(f"  {C.GR}{e}{C.R}")

def get_sd():
    with S_lock:
        return dict(S)

# ===========================================================
#  FILE I/O
# ===========================================================
def load_cfg():
    p = os.path.join(DIR, "config.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_comments():
    p = os.path.join(DIR, "comments.txt")
    with open(p, "r", encoding="utf-8") as f:
        raw = f.read()
    if "---" in raw:
        return [c.strip() for c in raw.split("---") if c.strip()]
    return [c.strip() for c in raw.split("\n") if c.strip()]

def load_set(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(l.strip() for l in f if l.strip())
    return set()

def append_line(path, line):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ===========================================================
#  COMMAND QUEUE (Flask -> Playwright Thread)
# ===========================================================
cmd_queue = _queue.Queue()

def cmd_put(action, data=None):
    cmd_queue.put((action, data, None))

# ===========================================================
#  COOKIE PARSER
# ===========================================================
def parse_cookie_string(cookie_str):
    """Parse 'name=value; name2=value2; ...' into list of cookie dicts."""
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, _, value = item.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookie = {
            "name": name,
            "value": value,
            "domain": ".facebook.com",
            "path": "/",
        }
        cookies.append(cookie)
    return cookies

# ===========================================================
#  FACEBOOK FUNCTIONS (called ONLY from Playwright thread)
# ===========================================================
def check_login(page):
    try:
        curl = page.url.lower()
        cookies = page.context.cookies()
        cn = [c["name"] for c in cookies]
        if "c_user" in cn and "xs" in cn and "login" not in curl:
            return True
    except:
        pass
    return False

def click_home_button(page):
    """Click Facebook Home button to refresh feed without reload (keeps cookie alive)."""
    selectors = [
        'a[href="/"], a[href="https://www.facebook.com/"]',
        'a[aria-label="Home"]', 'a[aria-label="Beranda"]',
        'div[role="navigation"] a[href="/"]',
        'span[data-pagelet="LeftNav"] a[href="/"]',
    ]
    for sel in selectors:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                if el.is_visible():
                    el.click()
                    return True
        except:
            pass
    # Fallback: navigate via URL (soft)
    try:
        page.evaluate("window.location.href = '/'")
        return True
    except:
        pass
    return False

def dismiss_dialogs(page):
    for t in ["Not Now", "Bukan Sekarang", "Lain Kali", "Maybe Later", "Not now",
              "Tidak sekarang", "Bukan untuk sekarang"]:
        try:
            page.click(f'text="{t}"', timeout=2000)
            time.sleep(0.5)
        except:
            pass

def get_account_name(page):
    try:
        page.goto("https://www.facebook.com/me", timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        nm = page.title().replace(" | Facebook", "").strip()
        page.goto("https://www.facebook.com/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        dismiss_dialogs(page)
        return nm
    except:
        return "N/A"

def take_ss(page):
    try:
        ss_path = os.path.join(DIR, ".ss_tmp.png")
        page.screenshot(path=ss_path)
        with open(ss_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        sset("ss_b64", b64)
    except:
        pass

def clear_ss():
    sset("ss_b64", None)

def clean_url(u):
    u = u.split("&__cft__")[0].split("&__tn__")[0]
    u = u.split("#")[0]
    u = re.sub(r"&idorvanity=[^&]*", "", u)
    return u

def extract_id(u):
    m = re.search(r"story_fbid[=:](\d+)", u)
    if m:
        m2 = re.search(r"[?&]id=(\d+)", u)
        return f"s_{m.group(1)}_{m2.group(1) if m2 else '0'}"
    m = re.search(r"fbid=(\d+)", u)
    if m:
        return f"p_{m.group(1)}"
    m = re.search(r"/posts/(\d+)", u)
    if m:
        return f"t_{m.group(1)}"
    return f"h_{hash(u)}"

def find_comment_box(page):
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
    except:
        pass

    selectors = [
        'div[contenteditable="true"][aria-label*="komentar" i]',
        'div[contenteditable="true"][aria-label*="comment" i]',
        'div[contenteditable="true"][aria-label*="Komentar"]',
        'div[contenteditable="true"][aria-label*="Comment"]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder*="Komentar" i]',
        'textarea[placeholder*="comment" i]',
        'textarea[aria-label*="Komentar" i]',
        'textarea[aria-label*="comment" i]',
        'textarea[placeholder*="Write a comment" i]',
        'textarea[name="comment_text"]',
    ]
    for sel in selectors:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                if el.is_visible():
                    return el
        except:
            pass

    try:
        vis = []
        for d in page.query_selector_all('div[contenteditable="true"]'):
            try:
                if d.is_visible():
                    al = (d.get_attribute("aria-label") or "").lower()
                    if "search" not in al and "cari" not in al:
                        vis.append(d)
            except:
                pass
        if vis:
            return vis[-1]
    except:
        pass

    try:
        for el in page.query_selector_all('div[role="button"], span[role="button"]'):
            txt = (el.inner_text() or "").strip().lower()
            if txt in ("komentar", "comment", "balas komentar", "reply"):
                el.click()
                time.sleep(2)
                for sel in ['div[contenteditable="true"][aria-label*="komentar" i]',
                           'div[contenteditable="true"][aria-label*="comment" i]',
                           'div[contenteditable="true"][role="textbox"]']:
                    for el2 in page.query_selector_all(sel):
                        if el2.is_visible():
                            return el2
                break
    except:
        pass
    return None

def is_restricted(page):
    try:
        bt = page.inner_text("body").lower()
        for w in ["komentar dinonaktifkan", "komentar dimatikan", "komentar ditutup",
                   "tidak bisa berkomentar", "comment is disabled", "comments are turned off",
                   "komentar ditolak", "comment rejected"]:
            if w in bt:
                return True, w
    except:
        pass
    return False, ""

def type_text(box, txt):
    lines = txt.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip():
            box.type(ln, delay=random.randint(15, 40))
            time.sleep(0.2)
        if i < len(lines) - 1:
            box.press("Shift+Enter")
            time.sleep(0.2)

def send_comment(page, box, txt):
    box.click()
    time.sleep(0.5)
    type_text(box, txt)
    time.sleep(1)

    done = False
    for sel in ['div[aria-label="Kirim"][role="button"]', 'div[aria-label="kirim"][role="button"]',
                'div[aria-label="Comment"][role="button"]', 'div[aria-label="Komentari"][role="button"]',
                'div[aria-label="Post"][role="button"]', 'div[aria-label="Kirimi"][role="button"]',
                'div[aria-label="Publish"][role="button"]']:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                if el.is_visible():
                    el.click()
                    done = True
                    break
            if done:
                break
        except:
            continue
    if not done:
        try:
            box.press("Enter")
        except:
            pass

    time.sleep(COMMENT_WAIT)
    try:
        rej, reason = is_restricted(page)
        if rej:
            return False, f"ditolak: {reason}"
    except:
        pass
    return True, "ok"

def comment_post(page, url, txt):
    page.goto(url, timeout=20000, wait_until="domcontentloaded")
    time.sleep(4)
    dismiss_dialogs(page)
    rj, reason = is_restricted(page)
    if rj:
        return False, f"blocked: {reason}"
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
    box = find_comment_box(page)
    if not box:
        return False, "box tidak ditemukan"
    return send_comment(page, box, txt)

def find_posts_on_page(page):
    """Find post links visible on the current page (no scrolling)."""
    link_sel = ('a[href*="story_fbid"], a[href*="/posts/"], '
                'a[href*="/photo/?fbid="], a[href*="/permalink"]')
    skip_p = ["/groups/", "/watch/", "/reel/", "/stories/", "/settings/",
              "/messages/", "/notifications/", "/marketplace/", "/gaming/",
              "/login", "/composer/", "/videos/"]
    inc_p = ["story_fbid", "/posts/", "/permalink", "fbid=", "/photos/"]

    collected = []
    links = page.query_selector_all(link_sel)
    for lk in links:
        try:
            href = lk.get_attribute("href") or ""
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.facebook.com" + href
            cl = clean_url(href)
            ul = cl.lower()
            if any(s in ul for s in skip_p):
                continue
            if not any(pp in ul for pp in inc_p):
                continue
            pid = extract_id(cl)
            if pid:
                collected.append({"id": pid, "url": cl})
        except:
            continue
    return collected

# ===========================================================
#  COOPERATIVE SLEEP
# ===========================================================
_bot_running = False

def co_sleep(seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            while True:
                cmd_queue.get_nowait()
        except _queue.Empty:
            pass
        time.sleep(0.2)
        if not _bot_running:
            return

# ===========================================================
#  PLAYWRIGHT THREAD (single thread for ALL browser operations)
# ===========================================================
def playwright_thread_func():
    global _bot_running

    pw = sync_playwright().start()
    ctx = None
    page = None

    bargs = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
             "--disable-gpu", "--disable-dev-tools", "--disable-blink-features=AutomationControlled"]
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Safari/537.36"
    vp = {"width": 1280, "height": 900}

    def launch_browser():
        nonlocal ctx, page
        os.makedirs(BROWSER_DATA, exist_ok=True)
        ctx = pw.chromium.launch_persistent_context(
            BROWSER_DATA, headless=True, args=bargs,
            viewport=vp, user_agent=ua, locale="id-ID")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

    def close_browser():
        nonlocal ctx, page
        try:
            if ctx:
                ctx.close()
        except:
            pass
        ctx = page = None

    while True:
        try:
            # Auto-load cookie from env variable FB if available
            if ENV_COOKIE and sget("phase") == "IDLE" and not ctx:
                pr(f"  {C.Y}Cookie ditemukan dari env FB, memuat otomatis...{C.R}")
                cmd_put("load_cookie", {"cookie": ENV_COOKIE})
                time.sleep(1)

            # Process ALL commands from queue
            while True:
                try:
                    action, data, rh = cmd_queue.get_nowait()
                except _queue.Empty:
                    break

                # ---- LOAD COOKIE ----
                if action == "load_cookie":
                    cookie_str = (data or {}).get("cookie", "")
                    # Prioritize env variable if available
                    if not cookie_str and ENV_COOKIE:
                        cookie_str = ENV_COOKIE
                    try:
                        if not cookie_str.strip():
                            sset("err", "Cookie kosong!")
                            sset("phase", "IDLE")
                            continue

                        if ctx is None:
                            launch_browser()

                        sset("phase", "LOGIN")
                        sset("msg", "Memuat cookie...")
                        sset("err", "")
                        slog("Memuat cookie ke browser...", "COOKIE")

                        # Parse and add cookies
                        cookies = parse_cookie_string(cookie_str)
                        if not cookies:
                            sset("err", "Format cookie tidak valid!")
                            sset("phase", "IDLE")
                            slog("Gagal parse cookie - format tidak valid", "ERROR")
                            continue

                        slog(f"Berhasil parse {len(cookies)} cookie", "COOKIE")

                        # Clear existing cookies and add new ones
                        ctx.clear_cookies()
                        for cookie in cookies:
                            try:
                                ctx.add_cookies([cookie])
                            except Exception as e:
                                slog(f"Gagal tambah cookie '{cookie['name']}': {str(e)[:40]}", "WARNING")

                        # Navigate to Facebook to verify
                        sset("msg", "Memverifikasi cookie...")
                        page.goto("https://www.facebook.com/", timeout=60000, wait_until="domcontentloaded")
                        time.sleep(5)
                        dismiss_dialogs(page)
                        take_ss(page)

                        if check_login(page):
                            sset("phase", "READY")
                            sset("msg", "Cookie valid! Bot siap.")
                            slog("Cookie valid - login berhasil!", "SUCCESS")
                            clear_ss()
                            nm = get_account_name(page)
                            sset("name", nm)
                            slog(f"Login sebagai: {nm}", "SUCCESS")
                        else:
                            bt = page.inner_text("body").lower()
                            if "login" in page.url.lower() and "facebook.com/login" in page.url.lower():
                                sset("phase", "IDLE")
                                sset("err", "Cookie tidak valid atau sudah expired!")
                                slog("Cookie tidak valid - diarahkan ke halaman login", "FAILED")
                            else:
                                sset("phase", "IDLE")
                                sset("err", "Cookie tidak valid! Cek kembali.")
                                slog("Cookie tidak valid - c_user/xs tidak ditemukan", "FAILED")
                            clear_ss()
                            close_browser()
                    except Exception as e:
                        sset("err", f"Error: {str(e)[:80]}")
                        sset("phase", "IDLE")
                        slog(f"Cookie error: {str(e)[:60]}", "ERROR")
                        clear_ss()

                # ---- RC CLICK ----
                elif action == "rc_click" and page:
                    try:
                        x = (data or {}).get("x", 50)
                        y = (data or {}).get("y", 50)
                        vps = page.viewport_size or vp
                        px = int(x * vps["width"] / 100)
                        py = int(y * vps["height"] / 100)
                        page.mouse.click(px, py)
                        time.sleep(0.3)
                        take_ss(page)
                        slog(f"Klik di ({x:.0f}%, {y:.0f}%)", "RC")
                    except Exception as e:
                        slog(f"Klik error: {str(e)[:40]}", "ERROR")

                # ---- RC TYPE ----
                elif action == "rc_type" and page:
                    try:
                        txt = (data or {}).get("text", "")
                        page.keyboard.type(txt, delay=30)
                        time.sleep(0.3)
                        take_ss(page)
                        slog(f"Ketik: {txt[:30]}", "RC")
                    except:
                        pass

                # ---- RC KEY ----
                elif action == "rc_key" and page:
                    try:
                        key = (data or {}).get("key", "Enter")
                        page.keyboard.press(key)
                        time.sleep(0.3)
                        take_ss(page)
                        slog(f"Tombol: {key}", "RC")
                    except:
                        pass

                # ---- RC SCROLL ----
                elif action == "rc_scroll" and page:
                    try:
                        d = (data or {}).get("direction", "down")
                        amt = 300 if d == "down" else -300
                        page.mouse.wheel(0, amt)
                        time.sleep(0.3)
                        take_ss(page)
                    except:
                        pass

                # ---- SCREENSHOT (one-time manual) ----
                elif action == "screenshot" and page:
                    try:
                        slog("Screenshot manual diambil", "INFO")
                        take_ss(page)
                        sset("ss_b64", sget("ss_b64"))  # keep it visible
                    except Exception as e:
                        slog(f"Screenshot error: {str(e)[:40]}", "ERROR")

                # ---- BOT START ----
                elif action == "bot_start":
                    if sget("phase") != "READY":
                        sset("err", "Masukkan cookie dulu!")
                        continue
                    if _bot_running:
                        sset("err", "Bot sudah berjalan!")
                        continue
                    _bot_running = True
                    sset("phase", "RUNNING")
                    sset("msg", "Bot dimulai...")
                    slog("=== BOT DIMULAI ===", "BOT")

                    comments = load_comments()
                    cfg = load_cfg()
                    min_d = cfg.get("min_delay", 5)
                    max_d = cfg.get("max_delay", 15)
                    targets = ["https://www.facebook.com/"]
                    for u in cfg.get("target_urls", []):
                        if u and u not in targets:
                            targets.append(u)

                    total_commented = 0
                    seen_ids = set()  # track post IDs in this session
                    scroll_round = 0

                    for tu in targets:
                        if not _bot_running:
                            break
                        try:
                            page.goto(tu, timeout=60000, wait_until="domcontentloaded")
                            time.sleep(4)
                            dismiss_dialogs(page)
                            slog(f"Membuka target: {tu}", "BOT")

                            # Stream mode: scroll -> find -> comment -> repeat
                            no_new_count = 0
                            while _bot_running and page:
                                scroll_round += 1
                                # Load ceklist & restricted periodically
                                ckl = load_set(CEKLIST)
                                rst = load_set(RESTRICTED)

                                # Find posts on current page
                                posts = find_posts_on_page(page)
                                new_posts = []
                                for p in posts:
                                    if p["id"] not in seen_ids and p["url"] not in ckl and p["url"] not in rst:
                                        seen_ids.add(p["id"])
                                        new_posts.append(p)

                                if new_posts:
                                    no_new_count = 0
                                    slog(f"Scroll #{scroll_round}: {len(new_posts)} post baru ditemukan", "SCRAPE")
                                else:
                                    no_new_count += 1
                                    slog(f"Scroll #{scroll_round}: tidak ada post baru", "BOT")

                                # Comment on each new post immediately
                                for idx, pst in enumerate(new_posts):
                                    if not _bot_running:
                                        break

                                    txt = random.choice(comments)
                                    ok = False
                                    msg = ""
                                    short_url = pst["url"][:50] + "..."

                                    slog(f"Mengomentari post {total_commented + idx + 1}...", "BOT")

                                    for att in range(1, MAX_RETRY + 1):
                                        if not _bot_running:
                                            break
                                        try:
                                            ok, msg = comment_post(page, pst["url"], txt)
                                            if ok:
                                                break
                                            else:
                                                co_sleep(RETRY_WAIT)
                                        except PlaywrightTimeout:
                                            msg = "timeout"
                                            co_sleep(RETRY_WAIT)
                                        except Exception as e:
                                            msg = str(e)[:60]
                                            co_sleep(RETRY_WAIT)

                                    if ok:
                                        sset("ok", sget("ok") + 1)
                                        total_commented += 1
                                        append_line(CEKLIST, pst["url"])
                                        slog(f"[{total_commented}] Success - Komentar terkirim | \"{txt[:30]}\"", "SUCCESS")
                                        flog(f"OK | {pst['url'][:80]}")
                                    else:
                                        if "blocked" in msg.lower() or "ditolak" in msg.lower():
                                            sset("blocked", sget("blocked") + 1)
                                            append_line(RESTRICTED, pst["url"])
                                            slog(f"[{total_commented}] Blocked - {msg}", "BLOCKED")
                                            flog(f"BLOCKED | {msg}")
                                        else:
                                            sset("fail", sget("fail") + 1)
                                            slog(f"[{total_commented}] Failed - {msg} | {short_url}", "FAILED")
                                            flog(f"FAIL | {msg}")

                                    # Check session every 5 comments
                                    if total_commented > 0 and total_commented % 5 == 0:
                                        try:
                                            # Quick check without navigating away
                                            cookies = page.context.cookies()
                                            cn = [c["name"] for c in cookies]
                                            if "c_user" not in cn or "xs" not in cn:
                                                slog("SESSION EXPIRED! Silakan masukkan cookie ulang.", "ERROR")
                                                sset("phase", "IDLE")
                                                sset("msg", "Session expired!")
                                                _bot_running = False
                                                break
                                        except:
                                            pass

                                    # Delay between comments
                                    if _bot_running and idx < len(new_posts) - 1:
                                        delay = random.uniform(min_d, max_d)
                                        sset("msg", f"Komen #{total_commented} | tunggu {delay:.0f}s...")
                                        co_sleep(delay)

                                if not _bot_running:
                                    break

                                # Scroll down to find more posts
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                sset("msg", f"Scrolling... Total komentari: {total_commented}")
                                time.sleep(3)

                                # If no new posts found for 10 consecutive scrolls, click Home button to refresh
                                if no_new_count >= 10:
                                    slog("Tidak ada post baru setelah 10 scroll, klik Home button...", "BOT")
                                    click_home_button(page)
                                    time.sleep(4)
                                    dismiss_dialogs(page)
                                    no_new_count = 0

                        except Exception as e:
                            slog(f"Target error: {str(e)[:50]}", "ERROR")
                            continue

                    _bot_running = False
                    sset("phase", "READY")
                    sset("msg", f"Bot selesai. Total OK: {sget('ok')}")
                    slog(f"=== BOT BERHENTI | Total: {sget('ok')} success, {sget('fail')} failed, {sget('blocked')} blocked ===", "BOT")

                # ---- BOT STOP ----
                elif action == "bot_stop":
                    _bot_running = False
                    sset("msg", "Menghentikan bot...")
                    slog("Bot dihentikan oleh user", "BOT")

                # ---- RESET ----
                elif action == "reset":
                    _bot_running = False
                    time.sleep(0.5)
                    close_browser()
                    for f in [CEKLIST, RESTRICTED]:
                        if os.path.exists(f):
                            os.remove(f)
                    sset("ok", 0); sset("fail", 0); sset("blocked", 0); sset("cycle", 0)
                    sset("phase", "IDLE"); sset("msg", "Reset done."); sset("name", ""); sset("err", "")
                    clear_ss()
                    slog("Semua data berhasil direset", "INFO")

            # Auto screenshot only during LOGIN phase (every loop ~0.3s)
            phase = sget("phase")
            if page and phase in ("LOGIN",):
                take_ss(page)

            time.sleep(0.3)

        except Exception as e:
            slog(f"Thread error: {str(e)[:60]}", "ERROR")
            traceback.print_exc()
            time.sleep(1)

# ===========================================================
#  HTML TEMPLATE
# ===========================================================
HTML = """<!DOCTYPE html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FB Bot v11 - Cookie Auth</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:800px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;color:#58a6ff;text-align:center;margin-bottom:2px}
.sub{color:#8b949e;font-size:.8rem;text-align:center;margin-bottom:14px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin-bottom:10px}
.card h2{font-size:.85rem;color:#c9d1d9;margin-bottom:8px}
.badge{display:inline-block;padding:3px 10px;border-radius:16px;font-size:.75rem;font-weight:600}
.b-idle{background:#30363d;color:#8b949e}
.b-login{background:#9e6a03;color:#ffd33d}
.b-ready{background:#238636;color:#3fb950}
.b-running{background:#1f6feb;color:#58a6ff}
.stats{display:flex;gap:8px;margin-bottom:10px}
.stat{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px;text-align:center;flex:1}
.stat .num{font-size:1.5rem;font-weight:700}
.stat .lbl{font-size:.65rem;color:#8b949e;text-transform:uppercase;margin-top:2px}
.stat-ok .num{color:#3fb950}
.stat-fail .num{color:#f85149}
.stat-bl .num{color:#d29922}
input[type="email"],input[type="password"],input[type="text"],textarea{
  width:100%;padding:9px 11px;background:#0d1117;border:1px solid #30363d;
  border-radius:8px;color:#e6edf3;font-size:.85rem;margin-bottom:7px;outline:none;
  font-family:inherit}
input:focus,textarea:focus{border-color:#58a6ff}
label{display:block;font-size:.75rem;color:#8b949e;margin-bottom:3px}
.btn{padding:10px 24px;border:none;border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer;transition:all .15s}
.btn-go{background:#238636;color:#fff}.btn-go:hover{background:#2ea043}
.btn-go:disabled{background:#1a3a1f;color:#3fb95080;cursor:not-allowed}
.btn-stop{background:#da3633;color:#fff}.btn-stop:hover{background:#f85149}
.btn-stop:disabled{background:#3d1114;color:#f8514980;cursor:not-allowed}
.btn-sec{background:#30363d;color:#e6edf3}.btn-sec:hover{background:#484f58}
.btn-warn{background:#9e6a03;color:#fff}.btn-warn:hover{background:#b35900}
.row{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center}
.msg{padding:7px 11px;border-radius:8px;font-size:.8rem;margin-bottom:7px}
.msg-e{background:#3d1114;color:#f85149;border:1px solid #da3633}
.hid{display:none!important}
.acct{color:#58a6ff;font-size:1.05rem;font-weight:600;text-align:center}
.btn-full{width:100%;padding:14px;font-size:1rem;letter-spacing:0.5px}
.ss-wrap{position:relative;width:100%;border-radius:8px;overflow:hidden;border:1px solid #30363d;background:#000;margin-bottom:6px}
.ss-wrap img{width:100%;display:block}
.ss-overlay{position:absolute;top:0;left:0;width:100%;height:100%;z-index:2;cursor:crosshair}
.logbox{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px;height:150px;overflow-y:auto;font-family:'Cascadia Code',Consolas,monospace;font-size:.68rem;line-height:1.6;color:#8b949e}
.logbox .l-info{color:#8b949e}
.logbox .l-success{color:#3fb950}
.logbox .l-failed{color:#f85149}
.logbox .l-blocked{color:#d29922}
.logbox .l-warning{color:#d29922}
.logbox .l-error{color:#f85149}
.logbox .l-bot{color:#58a6ff}
.logbox .l-rc{color:#bc8cff}
.logbox .l-scrape{color:#79c0ff}
.logbox .l-login{color:#3fb950}
.logbox .l-cookie{color:#f0883e}
.rc-area{margin-top:10px}
.rc-input-row{display:flex;gap:6px;align-items:center;margin-bottom:8px}
.rc-input-row input{flex:1;font-size:.82rem;padding:8px 10px;margin-bottom:0}
.rc-label{font-size:.72rem;color:#8b949e;margin-bottom:6px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px}
.rc-btns{display:flex;gap:6px;flex-wrap:wrap}
.rc-btn{padding:7px 14px;background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;font-size:.78rem;cursor:pointer;white-space:nowrap;transition:all .15s;font-weight:500}
.rc-btn:hover{background:#30363d;border-color:#484f58}
.rc-btn:active{background:#388bfd26;border-color:#388bfd}
.rc-divider{width:100%;height:1px;background:#30363d;margin:8px 0}
.dpad-label{font-size:.72rem;color:#8b949e;margin-bottom:6px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px}
.dpad{display:flex;justify-content:center;gap:6px;align-items:center}
.dpad-col{display:flex;flex-direction:column;gap:6px;align-items:center}
.dpad-col .dpad-mid{display:flex;gap:6px;align-items:center}
.dpad-btn{width:42px;height:42px;display:flex;align-items:center;justify-content:center;background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;font-size:1.1rem;cursor:pointer;transition:all .15s;user-select:none;-webkit-user-select:none}
.dpad-btn:hover{background:#30363d;border-color:#58a6ff;color:#58a6ff}
.dpad-btn:active{background:#1f6feb33;border-color:#58a6ff;transform:scale(0.95)}
.dpad-btn svg{width:18px;height:18px;fill:currentColor}
.ss-close{position:absolute;top:8px;right:8px;z-index:5;background:rgba(0,0,0,0.7);color:#f85149;border:1px solid #f85149;border-radius:6px;padding:4px 10px;font-size:.75rem;cursor:pointer;font-weight:600}
.ss-close:hover{background:#f85149;color:#fff}
textarea.cookie-input{min-height:120px;resize:vertical;font-size:.78rem;font-family:'Cascadia Code',Consolas,monospace;line-height:1.5}
.hint{color:#8b949e;font-size:.7rem;margin-top:2px;line-height:1.4}
</style></head><body>
<div class="wrap">
<h1>FB Auto-Comment Bot v11</h1>
<p class="sub">Cookie Auth Edition - Stream Mode</p>

<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
    <div>
      <p style="font-weight:600" id="pT">Initializing...</p>
      <p id="mT" style="color:#8b949e;font-size:.8rem">Loading...</p>
    </div>
    <span class="badge b-idle" id="badge">IDLE</span>
  </div>
</div>

<div class="stats">
  <div class="stat stat-ok"><div class="num" id="sO">0</div><div class="lbl">Success</div></div>
  <div class="stat stat-fail"><div class="num" id="sF">0</div><div class="lbl">Failed</div></div>
  <div class="stat stat-bl"><div class="num" id="sB">0</div><div class="lbl">Blocked</div></div>
</div>

<div class="card hid" id="acctCard">
  <p style="color:#8b949e;font-size:.7rem;text-align:center;margin-bottom:2px">Username Facebook</p>
  <p class="acct" id="acctName">-</p>
</div>

<!-- COOKIE LOGIN -->
<div class="card" id="loginCard">
  <h2>Masukkan Cookie Facebook</h2>
  <div id="loginMsg" class="msg msg-e hid"></div>
  <div id="loginForm">
    <label>Cookie String</label>
    <textarea class="cookie-input" id="inCookie" placeholder="Paste cookie Facebook di sini...&#10;&#10;Contoh: sb=value; datr=value; c_user=123456; xs=abc123; ..."></textarea>
    <p class="hint">Cara ambil cookie: Buka facebook.com di browser > F12 > Application > Cookies > Copy semua. Pastikan cookie mengandung <b>c_user</b> dan <b>xs</b>.</p>
    <div class="row">
      <button class="btn btn-go btn-full" id="btnLogin" onclick="doLogin()">Load Cookie & Login</button>
    </div>
  </div>
</div>

<!-- SCREENSHOT -->
<div class="card hid" id="ssCard">
  <h2>Screenshot Browser</h2>
  <div class="ss-wrap">
    <img id="ssImg" src="" alt="Browser">
    <div class="ss-overlay" onclick="onSsClick(event)"></div>
    <button class="ss-close" onclick="closeSs()">X Tutup</button>
  </div>
</div>

<!-- BOT CONTROL -->
<div class="card hid" id="botCard">
  <h2>Bot Control</h2>
  <button class="btn btn-go btn-full" id="btnStart" onclick="startBot()">Start Auto Comment</button>
  <div class="row" style="margin-bottom:0">
    <button class="btn btn-stop btn-full" id="btnStop" onclick="stopBot()" disabled>Stop Bot</button>
    <button class="btn btn-sec" onclick="resetAll()">Reset</button>
  </div>
  <div class="row">
    <button class="btn btn-warn" onclick="takeManualSs()">Screenshot Sekali</button>
  </div>
</div>

<!-- LOG + RC -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <h2 style="margin-bottom:0">Activity Log</h2>
    <button class="btn btn-sec" style="padding:5px 12px;font-size:.75rem" onclick="takeManualSs()">Screenshot</button>
  </div>
  <div class="logbox" id="logBox">Menunggu...</div>
  <div class="rc-area">
    <div class="rc-label">Remote Control</div>
    <div class="rc-input-row">
      <input type="text" id="rcInput" placeholder="Ketik teks atau perintah...">
      <button class="rc-btn" onclick="rcSend()">Kirim</button>
    </div>
    <div class="rc-btns">
      <button class="rc-btn" onclick="rcKey('Enter')">Enter</button>
      <button class="rc-btn" onclick="rcKey('Tab')">Tab</button>
      <button class="rc-btn" onclick="rcKey('Escape')">Esc</button>
      <button class="rc-btn" onclick="rcKey('Backspace')">Backspace</button>
      <button class="rc-btn" onclick="rcKey('Space')">Space</button>
      <button class="rc-btn" onclick="rcScroll('down')">Scroll Down</button>
      <button class="rc-btn" onclick="rcScroll('up')">Scroll Up</button>
    </div>
    <div class="rc-divider"></div>
    <div class="dpad-label">Arah Navigasi</div>
    <div class="dpad">
      <div class="dpad-col">
        <button class="dpad-btn" onclick="rcKey('ArrowUp')" title="Atas"><svg viewBox="0 0 24 24"><path d="M12 4l-8 8h5v8h6v-8h5z"/></svg></button>
        <div class="dpad-mid">
          <button class="dpad-btn" onclick="rcKey('ArrowLeft')" title="Kiri"><svg viewBox="0 0 24 24"><path d="M20 12l-8-8v5H4v6h8v5z"/></svg></button>
          <button class="dpad-btn" onclick="rcKey('ArrowDown')" title="Bawah"><svg viewBox="0 0 24 24"><path d="M12 20l8-8h-5V4H9v8H4z"/></svg></button>
          <button class="dpad-btn" onclick="rcKey('ArrowRight')" title="Kanan"><svg viewBox="0 0 24 24"><path d="M4 12l8-8v5h8v6h-8v5z"/></svg></button>
        </div>
      </div>
    </div>
  </div>
</div>
</div>

<script>
function api(u, m, d) {
  var o = {method: m, headers: {"Content-Type": "application/json"}};
  if (d) o.body = JSON.stringify(d);
  return fetch(u, o).then(function(r) { return r.json(); }).catch(function() { return null; });
}

function renderLogs(logs) {
  if (!logs || !logs.length) return;
  var html = "";
  for (var i = 0; i < logs.length; i++) {
    var line = logs[i];
    var cls = "l-info";
    if (line.indexOf("[SUCCESS]") > -1) cls = "l-success";
    else if (line.indexOf("[FAILED]") > -1) cls = "l-failed";
    else if (line.indexOf("[BLOCKED]") > -1) cls = "l-blocked";
    else if (line.indexOf("[WARNING]") > -1) cls = "l-warning";
    else if (line.indexOf("[ERROR]") > -1) cls = "l-error";
    else if (line.indexOf("[BOT]") > -1) cls = "l-bot";
    else if (line.indexOf("[RC]") > -1) cls = "l-rc";
    else if (line.indexOf("[SCRAPE]") > -1) cls = "l-scrape";
    else if (line.indexOf("[COOKIE]") > -1) cls = "l-cookie";
    var safe = line.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    html += '<div class="' + cls + '">' + safe + '</div>';
  }
  document.getElementById("logBox").innerHTML = html;
}

function update(d) {
  if (!d) return;
  document.getElementById("badge").textContent = d.phase;
  document.getElementById("badge").className = "badge b-" + d.phase.toLowerCase();
  document.getElementById("pT").textContent = phaseLabel(d.phase);
  document.getElementById("mT").textContent = d.msg || "";
  document.getElementById("sO").textContent = d.ok;
  document.getElementById("sF").textContent = d.fail;
  document.getElementById("sB").textContent = d.blocked;

  if (d.name) {
    document.getElementById("acctCard").classList.remove("hid");
    document.getElementById("acctName").textContent = d.name || "-";
  }

  var show = function(id) { document.getElementById(id).classList.remove("hid"); };
  var hide = function(id) { document.getElementById(id).classList.add("hid"); };

  hide("loginCard"); hide("ssCard"); hide("botCard");

  if (d.phase === "IDLE") {
    show("loginCard");
    document.getElementById("loginForm").classList.remove("hid");
  } else if (d.phase === "LOGIN") {
    show("loginCard");
    document.getElementById("loginForm").classList.add("hid");
    show("ssCard");
    if (d.ss_b64) {
      document.getElementById("ssImg").src = "data:image/png;base64," + d.ss_b64;
    }
  } else if (d.phase === "READY" || d.phase === "RUNNING") {
    show("botCard");
    if (d.ss_b64) {
      show("ssCard");
      document.getElementById("ssImg").src = "data:image/png;base64," + d.ss_b64;
    }
  }

  var lm = document.getElementById("loginMsg");
  if (d.err) { lm.textContent = d.err; lm.className = "msg msg-e"; lm.classList.remove("hid"); }
  else { lm.classList.add("hid"); }

  if (d.phase === "RUNNING") {
    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnStart").textContent = "Bot Sedang Berjalan...";
    document.getElementById("btnStop").disabled = false;
  } else if (d.phase === "READY") {
    document.getElementById("btnStart").disabled = false;
    document.getElementById("btnStart").textContent = "Start Auto Comment";
    document.getElementById("btnStop").disabled = true;
  }

  renderLogs(d.logs);
}

function phaseLabel(p) {
  var m = {
    IDLE:"Login Diperlukan",
    LOGIN:"Memverifikasi Cookie...",
    READY:"Siap - Mulai Bot",
    RUNNING:"Bot Sedang Berjalan"
  };
  return m[p] || p;
}

function doLogin() {
  var c = document.getElementById("inCookie").value.trim();
  if (!c) { alert("Paste cookie Facebook terlebih dahulu!"); return; }
  var b = document.getElementById("btnLogin");
  b.disabled = true; b.textContent = "Memverifikasi...";
  api("/api/load-cookie", "POST", {cookie:c}).then(function(d) {
    update(d); b.disabled = false; b.textContent = "Load Cookie & Login";
  });
}

function onSsClick(ev) {
  var r = ev.currentTarget.getBoundingClientRect();
  var x = ((ev.clientX - r.left) / r.width * 100).toFixed(1);
  var y = ((ev.clientY - r.top) / r.height * 100).toFixed(1);
  api("/api/rc/click", "POST", {x:+x, y:+y});
}

function closeSs() {
  document.getElementById("ssCard").classList.add("hid");
  api("/api/ss-clear", "POST");
}

function takeManualSs() {
  api("/api/screenshot", "POST").then(function(d) {
    if (d && d.ss_b64) {
      document.getElementById("ssCard").classList.remove("hid");
      document.getElementById("ssImg").src = "data:image/png;base64," + d.ss_b64;
    }
  });
}

function rcSend() {
  var t = document.getElementById("rcInput").value;
  if (!t) return;
  api("/api/rc/type", "POST", {text:t});
  document.getElementById("rcInput").value = "";
}
function rcKey(k) { api("/api/rc/key", "POST", {key:k}); }
function rcScroll(d) { api("/api/rc/scroll", "POST", {direction:d}); }

document.getElementById("rcInput").addEventListener("keydown", function(e) {
  if (e.key === "Enter") { e.preventDefault(); rcSend(); }
});

function startBot() {
  document.getElementById("btnStart").disabled = true;
  api("/api/bot-start", "POST").then(function(d) { update(d); });
}
function stopBot() { api("/api/bot-stop", "POST").then(function(d) { update(d); }); }
function resetAll() {
  if (!confirm("Reset semua data?")) return;
  api("/api/reset", "POST").then(function() { location.reload(); });
}

api("/api/status").then(function(d) { update(d); });
setInterval(function() { api("/api/status").then(function(d) { update(d); }); }, 2000);
</script></body></html>"""

# ===========================================================
#  FLASK APP
# ===========================================================
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/status")
def api_status():
    return jsonify(get_sd())

@app.route("/api/load-cookie", methods=["POST"])
def api_load_cookie():
    d = request.get_json()
    cookie_str = (d.get("cookie") or "").strip()
    if not cookie_str:
        sset("err", "Cookie kosong!")
        return jsonify(get_sd())
    cmd_put("load_cookie", {"cookie": cookie_str})
    return jsonify(get_sd())

@app.route("/api/rc/click", methods=["POST"])
def api_rc_click():
    d = request.get_json()
    cmd_put("rc_click", {"x": d.get("x", 50), "y": d.get("y", 50)})
    return jsonify(get_sd())

@app.route("/api/rc/type", methods=["POST"])
def api_rc_type():
    d = request.get_json()
    cmd_put("rc_type", {"text": d.get("text", "")})
    return jsonify(get_sd())

@app.route("/api/rc/key", methods=["POST"])
def api_rc_key():
    d = request.get_json()
    cmd_put("rc_key", {"key": d.get("key", "Enter")})
    return jsonify(get_sd())

@app.route("/api/rc/scroll", methods=["POST"])
def api_rc_scroll():
    d = request.get_json()
    cmd_put("rc_scroll", {"direction": d.get("direction", "down")})
    return jsonify(get_sd())

@app.route("/api/screenshot", methods=["POST"])
def api_screenshot():
    cmd_put("screenshot", {})
    time.sleep(0.5)
    return jsonify(get_sd())

@app.route("/api/ss-clear", methods=["POST"])
def api_ss_clear():
    sset("ss_b64", None)
    return jsonify(get_sd())

@app.route("/api/bot-start", methods=["POST"])
def api_bot_start():
    cmd_put("bot_start", {})
    return jsonify(get_sd())

@app.route("/api/bot-stop", methods=["POST"])
def api_bot_stop():
    cmd_put("bot_stop", {})
    return jsonify(get_sd())

@app.route("/api/reset", methods=["POST"])
def api_reset():
    cmd_put("reset", {})
    return jsonify(get_sd())

# ===========================================================
#  MAIN
# ===========================================================
def main():
    pr(f"\n{C.CY}{C.B}  FB AUTO-COMMENT BOT v11 - COOKIE AUTH{C.R}")
    pr(f"  {C.GR}Stream Mode | No Limits | Cookie-Based{C.R}\n")

    comments = load_comments()
    ceklist = load_set(CEKLIST)
    restricted = load_set(RESTRICTED)

    pr(f"  Komen   : {C.G}{len(comments)}{C.R}")
    pr(f"  Ceklist : {C.D}{len(ceklist)} post{C.R}")
    pr(f"  Blocked : {C.D}{len(restricted)} post{C.R}")

    if not comments:
        pr(f"  {C.RE}comments.txt kosong!{C.R}")
        sys.exit(1)

    threading.Thread(target=playwright_thread_func, daemon=True).start()
    time.sleep(2)

    pr(f"  {C.G}{C.B}DASHBOARD URL:{C.R}")
    pr(f"  {C.CY}http://localhost:{WEB_PORT}{C.R}\n")
    pr(f"  {C.GR}Bot berjalan. Buka dashboard untuk kontrol.{C.R}")

    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pr(f"\n  {C.Y}Bot dihentikan.{C.R}")
        pr(f"  {C.G}{sget('ok')} success  {C.RE}{sget('fail')} failed  {C.Y}{sget('blocked')} blocked{C.R}")
    except Exception as e:
        pr(f"\n  {C.RE}{e}{C.R}")
        traceback.print_exc()
