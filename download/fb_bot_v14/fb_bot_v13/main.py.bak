#!/usr/bin/env python3
"""
FB Auto-Comment Bot v13 - Lightweight Live View + SSE
- Cookie-based auth (export FB='cookie')
- Live View: MJPEG stream (captures inside bot loop, always live)
- SSE push for status (NO polling = zero GET /api/status spam)
- Stream mode: find post -> comment -> next
- No limits: continuous scroll & comment
- Queue architecture: ALL Playwright ops single thread (safe)
- RC (remote control) with D-Pad
- Optimized for 2GB RAM (viewport 800x600, JPEG q=20, low FPS)
"""

import json, time, random, os, re, sys, datetime, threading, traceback, queue as _queue
import logging
from flask import Flask, render_template_string, request, jsonify, Response
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ===========================================================
#  CONFIG
# ===========================================================
ENV_COOKIE = os.environ.get("FB", "").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
BROWSER_DATA = os.path.join(DIR, "browser_data")
WEB_PORT = int(os.environ.get("PORT", "8080"))
MAX_RETRY = 2
RETRY_WAIT = 3
COMMENT_WAIT = 5
CEKLIST = os.path.join(DIR, "ceklist.txt")
RESTRICTED = os.path.join(DIR, "restricted.txt")

# Live View - ultra lightweight for 2GB RAM
LIVE_FPS = 0.5          # 1 frame per 2 seconds (very light)
LIVE_QUALITY = 20       # JPEG quality (small file size)
LIVE_VIEWPORT = {"width": 800, "height": 600}
CAPTURE_INTERVAL = 2.0  # min seconds between captures in bot loop

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
#  SHARED STATE + SSE EVENT
# ===========================================================
S = {
    "phase": "IDLE", "msg": "Menunggu cookie...", "err": "", "name": "",
    "logs": [],
    "ok": 0, "fail": 0, "blocked": 0, "cycle": 0,
    "live_frame": None,
    "live_clients": 0,
}
S_lock = threading.Lock()
_state_event = threading.Event()  # signals SSE clients on state change
_state_version = 0

def sget(k):
    with S_lock:
        return S[k]

def sset(k, v):
    global _state_version
    with S_lock:
        S[k] = v
    _state_version += 1
    _state_event.set()

def slog(msg, tag="INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    e = f"[{ts}] [{tag}] {msg}"
    with S_lock:
        S["logs"].append(e)
        if len(S["logs"]) > 200:
            S["logs"] = S["logs"][-200:]
    pr(f"  {C.GR}{e}{C.R}")

def get_sd():
    with S_lock:
        return {k: S[k] for k in ["phase","msg","err","name","ok","fail","blocked","cycle","logs","live_clients"]}

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
#  COMMAND QUEUE
# ===========================================================
cmd_queue = _queue.Queue()

def cmd_put(action, data=None):
    cmd_queue.put((action, data, None))

# ===========================================================
#  GLOBAL PAGE REF (for capture from co_sleep)
# ===========================================================
_page_ref = None
_last_capture = 0

def capture_frame(page=None):
    global _last_capture
    p = page or _page_ref
    if not p:
        return
    now = time.time()
    if (now - _last_capture) < CAPTURE_INTERVAL:
        return
    try:
        ss_bytes = p.screenshot(type="jpeg", quality=LIVE_QUALITY)
        with S_lock:
            S["live_frame"] = ss_bytes
        _last_capture = now
    except:
        pass

# ===========================================================
#  COOKIE PARSER
# ===========================================================
def parse_cookie_string(cookie_str):
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
        cookie = {"name": name, "value": value, "domain": ".facebook.com", "path": "/"}
        cookies.append(cookie)
    return cookies

# ===========================================================
#  FACEBOOK FUNCTIONS
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
    home_selectors = [
        'a[aria-label="Home"]',
        'a[aria-label="Beranda"]',
        'a[aria-label="Beranda"][role="link"]',
    ]
    for sel in home_selectors:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                if el.is_visible():
                    href = el.get_attribute("href") or ""
                    if href == "/" or href.startswith("https://www.facebook.com/?"):
                        el.click()
                        slog("Klik tombol Home (aria-label)", "BOT")
                        return True
        except:
            pass
    try:
        for el in page.query_selector_all('div[role="navigation"] a[href="/"]'):
            if el.is_visible():
                el.click()
                slog("Klik tombol Home (navigation)", "BOT")
                return True
    except:
        pass
    try:
        for sel in ['a[href="/"][data-testid]', 'header a[href="/"]']:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                slog("Klik tombol Home (header)", "BOT")
                return True
    except:
        pass
    try:
        page.evaluate("window.location.href = '/'")
        slog("Navigasi ke Home (fallback)", "BOT")
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
    capture_frame(page)
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
    """Find post links visible on the current page. Robust multi-strategy."""
    collected = []
    seen_urls = set()
    skip_p = ["/groups/", "/watch/", "/reel/", "/stories/", "/settings/",
              "/messages/", "/notifications/", "/marketplace/", "/gaming/",
              "/login", "/composer/", "/videos/", "/events/", "/jobs/",
              "/fundraisers/", "/pages/", "/help/", "/privacy/",
              "/photo.php", "/ads/"]
    inc_p = ["story_fbid", "/posts/", "/permalink", "fbid=", "/photos/",
             "/p/", "/n/", "graphql"]

    def process_href(href):
        if not href or href == "#" or "javascript:" in href.lower():
            return None
        if "facebook.com" not in href and not href.startswith("/"):
            return None
        if href.startswith("/"):
            href = "https://www.facebook.com" + href
        cl = clean_url(href)
        ul = cl.lower()
        if any(s in ul for s in skip_p):
            return None
        if not any(pp in ul for pp in inc_p):
            return None
        if not re.search(r'\d{5,}', cl):
            return None
        pid = extract_id(cl)
        if pid and cl not in seen_urls:
            seen_urls.add(cl)
            return {"id": pid, "url": cl}
        return None

    # Strategy 1: Direct post link selectors
    link_selectors = [
        'a[href*="story_fbid"]',
        'a[href*="/posts/"]',
        'a[href*="/permalink"]',
        'a[href*="fbid="]',
    ]
    for sel in link_selectors:
        try:
            links = page.query_selector_all(sel)
            for lk in links:
                try:
                    href = lk.get_attribute("href") or ""
                    result = process_href(href)
                    if result:
                        collected.append(result)
                except:
                    continue
        except:
            continue

    # Strategy 2: Timestamp-based detection (most reliable for FB feed)
    try:
        time_links = page.evaluate("""() => {
            let results = [];
            let anchors = document.querySelectorAll('a[href]');
            for (let a of anchors) {
                let hasTime = a.querySelector('time') || a.querySelector('abbr');
                let href = a.getAttribute('href') || '';
                if (hasTime && href.length > 20) {
                    results.push(href);
                }
            }
            let previews = document.querySelectorAll('[data-ad-preview]');
            for (let p of previews) {
                let a = p.closest('a[href]');
                if (a) {
                    let href = a.getAttribute('href') || '';
                    if (href.length > 20) results.push(href);
                }
            }
            return [...new Set(results)];
        }""")
        for href in time_links:
            result = process_href(href)
            if result:
                collected.append(result)
    except:
        pass

    # Strategy 3: Feed story containers
    try:
        fb_links = page.evaluate("""() => {
            let results = [];
            let stories = document.querySelectorAll('[data-pagelet="FeedUnit"] a[href], [role="article"] a[href]');
            for (let a of stories) {
                let href = a.getAttribute('href') || '';
                if (href.includes('story_fbid') || href.includes('/posts/') || 
                    href.includes('/permalink') || href.includes('fbid=') ||
                    (href.match(/facebook\\.com\\/\\w+\\/(\\w+\\/){0,2}\\d{5,}/) && !href.includes('/groups/'))) {
                    results.push(href);
                }
            }
            return [...new Set(results)];
        }""")
        for href in fb_links:
            result = process_href(href)
            if result:
                collected.append(result)
    except:
        pass

    # Strategy 4: Scan ALL anchors for post patterns
    try:
        all_hrefs = page.evaluate("""() => {
            let results = [];
            let anchors = document.querySelectorAll('a[href]');
            for (let a of anchors) {
                let href = a.getAttribute('href') || '';
                if ((href.includes('story_fbid') || href.includes('/posts/') || 
                     href.includes('/permalink') || href.includes('fbid=')) &&
                    href.includes('facebook.com')) {
                    results.push(href);
                }
            }
            return [...new Set(results)];
        }""")
        for href in all_hrefs:
            result = process_href(href)
            if result:
                collected.append(result)
    except:
        pass

    # Deduplicate
    unique = []
    seen_ids = set()
    for p in collected:
        if p["id"] not in seen_ids:
            seen_ids.add(p["id"])
            unique.append(p)
    return unique

# ===========================================================
#  COOPERATIVE SLEEP (with frame capture during wait)
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
        capture_frame()  # capture during wait if interval passed
        time.sleep(0.5)
        if not _bot_running:
            return

# ===========================================================
#  PLAYWRIGHT THREAD
# ===========================================================
def playwright_thread_func():
    global _bot_running, _page_ref, _last_capture
    pw = sync_playwright().start()
    ctx = None
    page = None

    bargs = [
        "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--disable-dev-tools", "--disable-blink-features=AutomationControlled",
        "--disable-extensions", "--disable-background-networking",
        "--disable-default-apps", "--disable-sync",
        "--no-first-run", "--no-default-browser-check",
        "--disable-translate", "--mute-audio",
        "--single-process",
    ]
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Safari/537.36"

    def launch_browser():
        nonlocal ctx, page
        os.makedirs(BROWSER_DATA, exist_ok=True)
        ctx = pw.chromium.launch_persistent_context(
            BROWSER_DATA, headless=True, args=bargs,
            viewport=LIVE_VIEWPORT, user_agent=ua, locale="id-ID")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _page_ref = page

    def close_browser():
        nonlocal ctx, page
        try:
            if ctx:
                ctx.close()
        except:
            pass
        ctx = page = None
        _page_ref = None

    while True:
        try:
            if ENV_COOKIE and sget("phase") == "IDLE" and not ctx:
                pr(f"  {C.Y}Cookie ditemukan dari env FB, memuat otomatis...{C.R}")
                cmd_put("load_cookie", {"cookie": ENV_COOKIE})
                time.sleep(1)

            while True:
                try:
                    action, data, rh = cmd_queue.get_nowait()
                except _queue.Empty:
                    break

                if action == "load_cookie":
                    cookie_str = (data or {}).get("cookie", "")
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
                        cookies = parse_cookie_string(cookie_str)
                        if not cookies:
                            sset("err", "Format cookie tidak valid!")
                            sset("phase", "IDLE")
                            slog("Gagal parse cookie", "ERROR")
                            continue
                        slog(f"Berhasil parse {len(cookies)} cookie", "COOKIE")
                        ctx.clear_cookies()
                        for cookie in cookies:
                            try:
                                ctx.add_cookies([cookie])
                            except Exception as e:
                                slog(f"Gagal cookie '{cookie['name']}': {str(e)[:40]}", "WARNING")
                        sset("msg", "Memverifikasi cookie...")
                        page.goto("https://www.facebook.com/", timeout=60000, wait_until="domcontentloaded")
                        time.sleep(5)
                        dismiss_dialogs(page)
                        capture_frame(page)
                        if check_login(page):
                            sset("phase", "READY")
                            sset("msg", "Cookie valid! Bot siap.")
                            slog("Cookie valid - login berhasil!", "SUCCESS")
                            nm = get_account_name(page)
                            sset("name", nm)
                            slog(f"Login sebagai: {nm}", "SUCCESS")
                            capture_frame(page)
                        else:
                            if "login" in page.url.lower() and "facebook.com/login" in page.url.lower():
                                sset("phase", "IDLE")
                                sset("err", "Cookie tidak valid atau sudah expired!")
                                slog("Cookie tidak valid", "FAILED")
                            else:
                                sset("phase", "IDLE")
                                sset("err", "Cookie tidak valid! Cek kembali.")
                                slog("Cookie tidak valid - c_user/xs tidak ditemukan", "FAILED")
                            close_browser()
                    except Exception as e:
                        sset("err", f"Error: {str(e)[:80]}")
                        sset("phase", "IDLE")
                        slog(f"Cookie error: {str(e)[:60]}", "ERROR")

                elif action == "rc_click" and page:
                    try:
                        x = (data or {}).get("x", 50)
                        y = (data or {}).get("y", 50)
                        vps = page.viewport_size or LIVE_VIEWPORT
                        px = int(x * vps["width"] / 100)
                        py = int(y * vps["height"] / 100)
                        page.mouse.click(px, py)
                        time.sleep(0.3)
                        capture_frame(page)
                        slog(f"Klik di ({x:.0f}%, {y:.0f}%)", "RC")
                    except Exception as e:
                        slog(f"Klik error: {str(e)[:40]}", "ERROR")

                elif action == "rc_type" and page:
                    try:
                        txt = (data or {}).get("text", "")
                        page.keyboard.type(txt, delay=30)
                        time.sleep(0.3)
                        capture_frame(page)
                        slog(f"Ketik: {txt[:30]}", "RC")
                    except:
                        pass

                elif action == "rc_key" and page:
                    try:
                        key = (data or {}).get("key", "Enter")
                        page.keyboard.press(key)
                        time.sleep(0.3)
                        capture_frame(page)
                        slog(f"Tombol: {key}", "RC")
                    except:
                        pass

                elif action == "rc_scroll" and page:
                    try:
                        d = (data or {}).get("direction", "down")
                        amt = 300 if d == "down" else -300
                        page.mouse.wheel(0, amt)
                        time.sleep(0.3)
                        capture_frame(page)
                    except:
                        pass

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
                    seen_ids = set()
                    scroll_round = 0

                    for tu in targets:
                        if not _bot_running:
                            break
                        try:
                            page.goto(tu, timeout=60000, wait_until="domcontentloaded")
                            time.sleep(4)
                            dismiss_dialogs(page)
                            capture_frame(page)
                            slog(f"Membuka target: {tu}", "BOT")

                            no_new_count = 0
                            for _init_sc in range(3):
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(2)
                            page.evaluate("window.scrollTo(0, 0)")
                            time.sleep(2)
                            capture_frame(page)

                            while _bot_running and page:
                                scroll_round += 1
                                ckl = load_set(CEKLIST)
                                rst = load_set(RESTRICTED)

                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(3)

                                # capture frame after scroll
                                capture_frame(page)

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
                                    if no_new_count <= 3 or no_new_count % 5 == 0:
                                        slog(f"Scroll #{scroll_round}: tidak ada post baru ({no_new_count}x)", "BOT")

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
                                        slog(f"[{total_commented}] Success | \"{txt[:30]}\"", "SUCCESS")
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

                                    if total_commented > 0 and total_commented % 5 == 0:
                                        try:
                                            cookies = page.context.cookies()
                                            cn = [c["name"] for c in cookies]
                                            if "c_user" not in cn or "xs" not in cn:
                                                slog("SESSION EXPIRED!", "ERROR")
                                                sset("phase", "IDLE")
                                                sset("msg", "Session expired!")
                                                _bot_running = False
                                                break
                                        except:
                                            pass

                                    if _bot_running and idx < len(new_posts) - 1:
                                        delay = random.uniform(min_d, max_d)
                                        sset("msg", f"Komen #{total_commented} | tunggu {delay:.0f}s...")
                                        co_sleep(delay)

                                if not _bot_running:
                                    break
                                sset("msg", f"Scrolling... Total komentari: {total_commented}")
                                if no_new_count >= 15:
                                    slog("Tidak ada post baru, klik Home...", "BOT")
                                    click_home_button(page)
                                    time.sleep(4)
                                    dismiss_dialogs(page)
                                    capture_frame(page)
                                    no_new_count = 0
                        except Exception as e:
                            slog(f"Target error: {str(e)[:50]}", "ERROR")
                            continue

                    _bot_running = False
                    sset("phase", "READY")
                    sset("msg", f"Bot selesai. Total OK: {sget('ok')}")
                    slog(f"=== BOT BERHENTI | OK:{sget('ok')} FAIL:{sget('fail')} BLK:{sget('blocked')} ===", "BOT")
                    capture_frame(page)

                elif action == "bot_stop":
                    _bot_running = False
                    sset("msg", "Menghentikan bot...")
                    slog("Bot dihentikan oleh user", "BOT")

                elif action == "reset":
                    _bot_running = False
                    time.sleep(0.5)
                    close_browser()
                    for f in [CEKLIST, RESTRICTED]:
                        if os.path.exists(f):
                            os.remove(f)
                    sset("ok", 0); sset("fail", 0); sset("blocked", 0); sset("cycle", 0)
                    sset("phase", "IDLE"); sset("msg", "Reset done."); sset("name", ""); sset("err", "")
                    with S_lock:
                        S["live_frame"] = None
                    slog("Semua data berhasil direset", "INFO")

            # Idle capture (only when NOT in bot loop)
            capture_frame()
            time.sleep(0.3)

        except Exception as e:
            slog(f"Thread error: {str(e)[:60]}", "ERROR")
            traceback.print_exc()
            time.sleep(1)

# ===========================================================
#  HTML TEMPLATE (Tabbed UI: Start | Log | Console)
# ===========================================================
HTML = """<!DOCTYPE html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FB Bot v13</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:520px;margin:0 auto;padding:12px 10px}
.hdr{text-align:center;margin-bottom:10px}
.hdr h1{font-size:1.1rem;color:#58a6ff;margin-bottom:1px}
.hdr .sub{color:#484f58;font-size:.68rem}
.acct-bar{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:8px}
.acct-avatar{width:36px;height:36px;border-radius:50%;background:#21262d;border:2px solid #30363d;display:flex;align-items:center;justify-content:center;font-size:.9rem;color:#58a6ff;font-weight:700;flex-shrink:0}
.acct-info{flex:1;min-width:0}
.acct-name{font-size:.9rem;font-weight:600;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.acct-status{font-size:.7rem;color:#484f58;display:flex;align-items:center;gap:5px;margin-top:1px}
.status-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.sd-idle{background:#484f58}.sd-login{background:#ffd33d}.sd-ready{background:#3fb950}.sd-running{background:#58a6ff;animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.err-line{font-size:.75rem;color:#f85149;padding:2px 12px 8px;margin-bottom:6px}
.stats{display:flex;gap:5px;margin-bottom:8px}
.st{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:6px 4px;text-align:center;flex:1}
.st .n{font-size:1.2rem;font-weight:700}
.st .l{font-size:.55rem;color:#484f58;text-transform:uppercase;margin-top:1px;letter-spacing:.3px}
.s-ok .n{color:#3fb950}.s-fl .n{color:#f85149}.s-bk .n{color:#d29922}
.tab-bar{display:flex;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;margin-bottom:8px}
.tab-btn{flex:1;padding:10px 4px;text-align:center;font-size:.78rem;font-weight:600;color:#484f58;cursor:pointer;border:none;background:transparent;transition:all .2s;border-right:1px solid #30363d;display:flex;align-items:center;justify-content:center;gap:5px}
.tab-btn:last-child{border-right:none}
.tab-btn:hover{background:#1c2129;color:#c9d1d9}
.tab-btn.active{background:#0d419a;color:#fff;box-shadow:inset 0 -2px 0 #58a6ff}
.tab-btn svg{width:15px;height:15px;fill:currentColor;flex-shrink:0}
.tab-dot{width:6px;height:6px;border-radius:50%;background:#f85149;animation:blink 1s infinite;flex-shrink:0}
.tab-dot.off{background:#30363d;animation:none}
.tab-panel{display:none}.tab-panel.active{display:block}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;margin-bottom:8px}
.card h2{font-size:.75rem;color:#484f58;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.btn{padding:10px 18px;border:none;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .15s;display:inline-flex;align-items:center;justify-content:center;gap:6px}
.btn svg{width:16px;height:16px;fill:currentColor}
.btn-go{background:#238636;color:#fff}.btn-go:hover{background:#2ea043}
.btn-go:disabled{background:#1a3a1f;color:#3fb95050;cursor:not-allowed}
.btn-stop{background:#da3633;color:#fff}.btn-stop:hover{background:#f85149}
.btn-stop:disabled{background:#3d1114;color:#f8514950;cursor:not-allowed}
.btn-sec{background:#21262d;color:#c9d1d9;border:1px solid #30363d}.btn-sec:hover{background:#30363d}
.btn-full{width:100%;padding:12px;font-size:.9rem}
.btn-sm{padding:7px 14px;font-size:.78rem}
.row{display:flex;gap:6px;margin-top:8px}
input[type="text"],textarea{width:100%;padding:9px 11px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:.82rem;outline:none;font-family:inherit}
input:focus,textarea:focus{border-color:#58a6ff}
label{display:block;font-size:.68rem;color:#484f58;margin-bottom:3px;font-weight:600}
.hint{color:#484f58;font-size:.62rem;margin-top:3px;line-height:1.4}
.msg{padding:7px 10px;border-radius:8px;font-size:.78rem;margin-bottom:8px}
.msg-e{background:#3d1114;color:#f85149;border:1px solid #da3633}
.hid{display:none!important}
textarea.cookie-input{min-height:90px;resize:vertical;font-size:.72rem;font-family:'Cascadia Code',Consolas,monospace;line-height:1.4}
.log-wrap{position:relative}
.log-box{background:#010409;border:1px solid #21262d;border-radius:8px;padding:8px;height:420px;overflow-y:auto;font-family:'Cascadia Code',Consolas,monospace;font-size:.65rem;line-height:1.6;color:#484f58}
.log-box::-webkit-scrollbar{width:6px}
.log-box::-webkit-scrollbar-track{background:transparent}
.log-box::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
.log-box::-webkit-scrollbar-thumb:hover{background:#484f58}
.li{color:#484f58}.ls{color:#3fb950}.lf{color:#f85149}.lb{color:#d29922}.lw{color:#d29922}.le{color:#f85149}
.lbot{color:#58a6ff}.lrc{color:#bc8cff}.lsc{color:#79c0ff}.lck{color:#f0883e}
.log-scroll-bar{position:absolute;bottom:10px;right:10px;display:flex;gap:4px;z-index:5}
.log-scroll-btn{width:32px;height:32px;border-radius:8px;background:#21262d;color:#c9d1d9;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .15s}
.log-scroll-btn:hover{background:#30363d;border-color:#58a6ff;color:#58a6ff}
.log-scroll-btn svg{width:14px;height:14px;fill:currentColor}
.live-box{position:relative;width:100%;border-radius:8px;overflow:hidden;border:2px solid #30363d;background:#000;aspect-ratio:4/3;margin-bottom:8px}
.live-box img{width:100%;height:100%;object-fit:contain;display:block}
.live-overlay{position:absolute;top:0;left:0;width:100%;height:100%;z-index:2;cursor:crosshair}
.live-tag{position:absolute;top:6px;left:6px;z-index:3;background:rgba(220,38,38,.85);color:#fff;border-radius:5px;padding:2px 8px;font-size:.62rem;font-weight:700;display:flex;align-items:center;gap:4px;letter-spacing:.3px}
.live-tag .dot{width:6px;height:6px;background:#f85149;border-radius:50%;animation:blink 1s infinite}
.live-off-msg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:3;color:#484f58;font-size:.8rem;text-align:center}
.live-hint{position:absolute;bottom:6px;left:50%;transform:translateX(-50%);z-index:3;background:rgba(0,0,0,.6);color:#484f58;border-radius:5px;padding:2px 8px;font-size:.6rem;pointer-events:none}
.con-input-row{display:flex;gap:6px;align-items:center;margin-bottom:8px}
.con-input-row input{flex:1;font-size:.82rem;padding:9px 11px}
.con-btns{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
.con-btn{padding:6px 12px;background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;font-size:.72rem;cursor:pointer;white-space:nowrap;transition:all .15s;font-weight:500}
.con-btn:hover{background:#30363d;border-color:#484f58}
.con-divider{height:1px;background:#30363d;margin:6px 0}
.dpad-wrap{display:flex;justify-content:center;margin-bottom:6px}
.dpad-grid{display:grid;grid-template-columns:38px 38px 38px;grid-template-rows:38px 38px;gap:4px}
.dpad-b{width:38px;height:38px;display:flex;align-items:center;justify-content:center;background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;cursor:pointer;transition:all .15s;user-select:none;-webkit-user-select:none}
.dpad-b:hover{background:#30363d;border-color:#58a6ff;color:#58a6ff}
.dpad-b:active{background:#1f6feb33;transform:scale(.95)}
.dpad-b svg{width:14px;height:14px;fill:currentColor}
.con-scroll-row{display:flex;gap:6px}
.con-scroll-btn{flex:1;padding:8px;background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;cursor:pointer;text-align:center;font-size:.75rem;font-weight:500;transition:all .15s}
.con-scroll-btn:hover{background:#30363d;border-color:#58a6ff;color:#58a6ff}
.sse-badge{position:fixed;bottom:6px;right:6px;font-size:.55rem;padding:2px 7px;border-radius:5px;font-weight:600;z-index:99}
.sse-on{background:rgba(35,134,54,.7);color:#fff}
.sse-off{background:rgba(248,81,73,.7);color:#fff}
</style></head><body>
<div class="wrap">
<div class="hdr"><h1>FB Auto-Comment Bot</h1><div class="sub">v13 &middot; Stream Mode &middot; Lightweight</div></div>
<div class="acct-bar">
  <div class="acct-avatar" id="acctAvatar">?</div>
  <div class="acct-info">
    <div class="acct-name" id="acctName">Belum login</div>
    <div class="acct-status"><div class="status-dot sd-idle" id="statusDot"></div><span id="statusTxt">Menunggu cookie...</span></div>
  </div>
</div>
<div class="err-line hid" id="errBox"></div>
<div class="stats">
  <div class="st s-ok"><div class="n" id="sO">0</div><div class="l">Success</div></div>
  <div class="st s-fl"><div class="n" id="sF">0</div><div class="l">Failed</div></div>
  <div class="st s-bk"><div class="n" id="sB">0</div><div class="l">Blocked</div></div>
</div>
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('start')" id="tabBtnStart"><svg viewBox="0 0 24 24"><path d="M13 3L4 14h7l-2 7 9-11h-7l2-7z"/></svg> Start</button>
  <button class="tab-btn" onclick="switchTab('log')" id="tabBtnLog"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg> Log</button>
  <button class="tab-btn" onclick="switchTab('console')" id="tabBtnConsole"><svg viewBox="0 0 24 24"><path d="M4 4h16v16H4V4zm2 2v12h12V6H6zm2 2l4 4-4 4h2l4-4-4-4H8zm5 6h3v2h-3v-2z"/></svg> Console<div class="tab-dot off" id="consoleDot"></div></button>
</div>
<div class="tab-panel active" id="panelStart">
  <div class="card" id="cookieCard">
    <h2>Set Cookie Facebook</h2>
    <div id="loginMsg" class="msg msg-e hid"></div>
    <label>Cookie String</label>
    <textarea class="cookie-input" id="inCookie" placeholder="Paste cookie Facebook di sini...&#10;&#10;Contoh: sb=value; datr=value; c_user=123456; xs=abc123; ..."></textarea>
    <p class="hint">F12 > Application > Cookies > Copy semua. Pastikan ada <b>c_user</b> dan <b>xs</b>.<br>Atau <b>export FB='cookie'</b> di terminal.</p>
    <div class="row"><button class="btn btn-go btn-full" id="btnLogin" onclick="doLogin()">Load Cookie &amp; Login</button></div>
  </div>
  <div class="card hid" id="controlCard">
    <h2>Bot Control</h2>
    <button class="btn btn-go btn-full" id="btnStart" onclick="startBot()"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg> Start Auto Comment</button>
    <div class="row">
      <button class="btn btn-stop btn-full" id="btnStop" onclick="stopBot()" disabled><svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg> Stop Bot</button>
      <button class="btn btn-sec btn-sm" onclick="resetAll()">Reset</button>
    </div>
  </div>
</div>
<div class="tab-panel" id="panelLog">
  <div class="card" style="padding:8px">
    <div class="log-wrap">
      <div class="log-box" id="logBox">Menunggu koneksi...</div>
      <div class="log-scroll-bar">
        <button class="log-scroll-btn" onclick="logScrollTop()" title="Ke atas"><svg viewBox="0 0 24 24"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg></button>
        <button class="log-scroll-btn" onclick="logScrollBottom()" title="Ke bawah"><svg viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg></button>
      </div>
    </div>
  </div>
</div>
<div class="tab-panel" id="panelConsole">
  <div class="card" style="padding:8px">
    <div class="live-box" id="liveBox">
      <div class="live-tag hid" id="liveTag"><div class="dot"></div>LIVE</div>
      <div class="live-off-msg" id="liveOff">Browser tidak aktif</div>
      <img id="liveImg" src="" alt="Live">
      <div class="live-overlay" onclick="onLiveClick(event)"></div>
      <div class="live-hint">Klik layar untuk mengontrol browser</div>
    </div>
    <div class="con-input-row">
      <input type="text" id="rcInput" placeholder="Ketik teks untuk browser..." autocomplete="off">
      <button class="con-btn" onclick="rcSend()" style="background:#238636;color:#fff;border-color:#238636">Kirim</button>
    </div>
    <div class="con-btns">
      <button class="con-btn" onclick="rcKey('Enter')">Enter</button>
      <button class="con-btn" onclick="rcKey('Tab')">Tab</button>
      <button class="con-btn" onclick="rcKey('Escape')">Esc</button>
      <button class="con-btn" onclick="rcKey('Backspace')">Del</button>
      <button class="con-btn" onclick="rcKey('Space')">Space</button>
      <button class="con-btn" onclick="rcKey('F5')">F5</button>
    </div>
    <div class="con-divider"></div>
    <div class="dpad-wrap">
      <div class="dpad-grid">
        <div></div>
        <button class="dpad-b" onclick="rcKey('ArrowUp')" title="Atas"><svg viewBox="0 0 24 24"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg></button>
        <div></div>
        <button class="dpad-b" onclick="rcKey('ArrowLeft')" title="Kiri"><svg viewBox="0 0 24 24"><path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6z"/></svg></button>
        <button class="dpad-b" onclick="rcKey('ArrowDown')" title="Bawah"><svg viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg></button>
        <button class="dpad-b" onclick="rcKey('ArrowRight')" title="Kanan"><svg viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg></button>
      </div>
    </div>
    <div class="con-divider"></div>
    <div class="con-scroll-row">
      <button class="con-scroll-btn" onclick="rcScroll('up')">&#9650; Scroll Up</button>
      <button class="con-scroll-btn" onclick="rcScroll('down')">Scroll Down &#9660;</button>
    </div>
  </div>
</div>
</div>
<div class="sse-badge sse-off" id="sseBadge">SSE</div>
<script>
function api(u,m,d){var o={method:m||"GET",headers:{"Content-Type":"application/json"}};if(d)o.body=JSON.stringify(d);return fetch(u,o).then(function(r){return r.json()}).catch(function(){return null})}
var activeTab="start",liveActive=false,_logAtBottom=true;
function switchTab(n){activeTab=n;document.querySelectorAll(".tab-btn").forEach(function(b){b.classList.remove("active")});document.querySelectorAll(".tab-panel").forEach(function(p){p.classList.remove("active")});document.getElementById("tabBtn"+n.charAt(0).toUpperCase()+n.slice(1)).classList.add("active");document.getElementById("panel"+n.charAt(0).toUpperCase()+n.slice(1)).classList.add("active");if(n==="console")startLive();else stopLive()}
function startLive(){if(liveActive)return;document.getElementById("liveImg").src="/live";liveActive=true}
function stopLive(){if(!liveActive)return;document.getElementById("liveImg").src="";liveActive=false}
function renderLogs(logs){if(!logs||!logs.length)return;var box=document.getElementById("logBox");var wasAtBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-30;var html="";var last=Math.max(0,logs.length-200);for(var i=last;i<logs.length;i++){var line=logs[i];var c="li";if(line.indexOf("[SUCCESS]")>-1)c="ls";else if(line.indexOf("[FAILED]")>-1)c="lf";else if(line.indexOf("[BLOCKED]")>-1)c="lb";else if(line.indexOf("[WARNING]")>-1)c="lw";else if(line.indexOf("[ERROR]")>-1)c="le";else if(line.indexOf("[BOT]")>-1)c="lbot";else if(line.indexOf("[RC]")>-1)c="lrc";else if(line.indexOf("[SCRAPE]")>-1)c="lsc";else if(line.indexOf("[COOKIE]")>-1)c="lck";var s=line.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");html+='<div class="'+c+'">'+s+"</div>"}box.innerHTML=html;if(_logAtBottom||wasAtBottom)box.scrollTop=box.scrollHeight}
function logScrollTop(){document.getElementById("logBox").scrollTop=0}
function logScrollBottom(){var b=document.getElementById("logBox");b.scrollTop=b.scrollHeight}
function update(d){if(!d)return;document.getElementById("sO").textContent=d.ok;document.getElementById("sF").textContent=d.fail;document.getElementById("sB").textContent=d.blocked;if(d.name&&d.name!=="N/A"){document.getElementById("acctAvatar").textContent=d.name.charAt(0).toUpperCase();document.getElementById("acctName").textContent=d.name}else{document.getElementById("acctAvatar").textContent="?";document.getElementById("acctName").textContent="Belum login"}var dot=document.getElementById("statusDot");dot.className="status-dot sd-"+d.phase.toLowerCase();document.getElementById("statusTxt").textContent=d.msg||d.phase;var eb=document.getElementById("errBox");if(d.err){eb.textContent=d.err;eb.classList.remove("hid")}else{eb.classList.add("hid")}var sh=function(id){document.getElementById(id).classList.remove("hid")};var hi=function(id){document.getElementById(id).classList.add("hid")};if(d.phase==="IDLE"){sh("cookieCard");hi("controlCard");document.getElementById("loginMsg").classList.add("hid")}else if(d.phase==="LOGIN"){sh("cookieCard");hi("controlCard");var lm=document.getElementById("loginMsg");lm.textContent="Memverifikasi...";lm.className="msg msg-e";lm.classList.remove("hid");document.getElementById("btnLogin").disabled=true}else if(d.phase==="READY"){hi("cookieCard");sh("controlCard");document.getElementById("btnStart").disabled=false;document.getElementById("btnStart").innerHTML='<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor"><path d="M8 5v14l11-7z"/></svg> Start Auto Comment';document.getElementById("btnStop").disabled=true}else if(d.phase==="RUNNING"){hi("cookieCard");sh("controlCard");document.getElementById("btnStart").disabled=true;document.getElementById("btnStart").innerHTML='<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg> Bot Berjalan...';document.getElementById("btnStop").disabled=false}var cD=document.getElementById("consoleDot");var lT=document.getElementById("liveTag");var lO=document.getElementById("liveOff");if(d.phase==="READY"||d.phase==="RUNNING"||d.phase==="LOGIN"){cD.classList.remove("off");lO.classList.add("hid");lT.classList.remove("hid")}else{cD.classList.add("off");lO.classList.remove("hid");lT.classList.add("hid")}renderLogs(d.logs)}
function doLogin(){var c=document.getElementById("inCookie").value.trim();if(!c){alert("Paste cookie Facebook terlebih dahulu!");return}var b=document.getElementById("btnLogin");b.disabled=true;b.textContent="Memverifikasi...";api("/api/load-cookie","POST",{cookie:c}).then(function(d){update(d);b.disabled=false;b.textContent="Load Cookie & Login"})}
function startBot(){document.getElementById("btnStart").disabled=true;api("/api/bot-start","POST")}
function stopBot(){api("/api/bot-stop","POST")}
function resetAll(){if(!confirm("Reset semua data bot?"))return;stopLive();api("/api/reset","POST").then(function(){location.reload()})}
function onLiveClick(ev){var r=ev.currentTarget.getBoundingClientRect();var x=((ev.clientX-r.left)/r.width*100).toFixed(1);var y=((ev.clientY-r.top)/r.height*100).toFixed(1);api("/api/rc/click","POST",{x:+x,y:+y})}
function rcSend(){var t=document.getElementById("rcInput").value;if(!t)return;api("/api/rc/type","POST",{text:t});document.getElementById("rcInput").value=""}
function rcKey(k){api("/api/rc/key","POST",{key:k})}
function rcScroll(d){api("/api/rc/scroll","POST",{direction:d})}
document.getElementById("rcInput").addEventListener("keydown",function(e){if(e.key==="Enter"){e.preventDefault();rcSend()}});
(function(){var sE=document.getElementById("sseBadge");var es=null;var r=0;function c(){if(es){es.close();es=null}es=new EventSource("/api/stream");es.onopen=function(){r=0;sE.textContent="SSE Live";sE.className="sse-badge sse-on"};es.onmessage=function(e){try{update(JSON.parse(e.data))}catch(er){}};es.onerror=function(){sE.textContent="...";sE.className="sse-badge sse-off";es.close();es=null;r++;setTimeout(c,Math.min(r*2,10)*1000)}}api("/api/status").then(function(d){update(d);c()});window.addEventListener("beforeunload",function(){if(es)es.close()})})();
</script></body></html>"""

# ===========================================================
#  FLASK APP
# ===========================================================
app = Flask(__name__)

# Suppress Flask request logs (terminal cleaner)
log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/status")
def api_status():
    return jsonify(get_sd())

@app.route("/api/stream")
def api_stream():
    """SSE endpoint - push state changes to client (NO polling needed)."""
    def generate():
        global _state_version
        last_ver = _state_version
        try:
            while True:
                _state_event.wait(timeout=5)
                _state_event.clear()
                if _state_version != last_ver:
                    last_ver = _state_version
                    data = json.dumps(get_sd())
                    yield f"data: {data}\n\n"
                else:
                    yield ":keepalive\n\n"
        except GeneratorExit:
            pass
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})

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
    return jsonify({"ok": True})

@app.route("/api/rc/type", methods=["POST"])
def api_rc_type():
    d = request.get_json()
    cmd_put("rc_type", {"text": d.get("text", "")})
    return jsonify({"ok": True})

@app.route("/api/rc/key", methods=["POST"])
def api_rc_key():
    d = request.get_json()
    cmd_put("rc_key", {"key": d.get("key", "Enter")})
    return jsonify({"ok": True})

@app.route("/api/rc/scroll", methods=["POST"])
def api_rc_scroll():
    d = request.get_json()
    cmd_put("rc_scroll", {"direction": d.get("direction", "down")})
    return jsonify({"ok": True})

@app.route("/live")
def live_stream():
    """MJPEG stream - live browser view."""
    def generate():
        with S_lock:
            S["live_clients"] += 1
        try:
            while True:
                frame = sget("live_frame")
                if frame:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                else:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                           b"\xff\xdb\x00C\x00\x03\x02\x02\x03\x02\x02\x03\x03\x03\x03\x04\x03\x03"
                           b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
                           b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                           b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9\r\n")
                time.sleep(1.0 / LIVE_FPS)
        except GeneratorExit:
            pass
        finally:
            with S_lock:
                S["live_clients"] = max(0, S["live_clients"] - 1)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

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
    pr(f"\n{C.CY}{C.B}  FB AUTO-COMMENT BOT v13 - LIGHTWEIGHT LIVE{C.R}")
    pr(f"  {C.GR}SSE Push | MJPEG Live | Cookie Auth | Stream Mode{C.R}\n")

    comments = load_comments()
    ceklist = load_set(CEKLIST)
    restricted = load_set(RESTRICTED)

    pr(f"  Komen   : {C.G}{len(comments)}{C.R}")
    pr(f"  Ceklist : {C.D}{len(ceklist)} post{C.R}")
    pr(f"  Blocked : {C.D}{len(restricted)} post{C.R}")
    pr(f"  Live    : {C.D}{LIVE_FPS} fps, quality {LIVE_QUALITY}%, {LIVE_VIEWPORT['width']}x{LIVE_VIEWPORT['height']}{C.R}")
    pr(f"  SSE     : {C.G}enabled{C.R} (zero polling){C.R}")

    if not comments:
        pr(f"  {C.RE}comments.txt kosong!{C.R}")
        sys.exit(1)

    if ENV_COOKIE:
        pr(f"  {C.G}Cookie env FB terdeteksi{C.R}")
    else:
        pr(f"  {C.Y}Export FB='cookie' untuk auto-login{C.R}")

    pr(f"\n  {C.G}Starting Playwright thread...{C.R}")
    t = threading.Thread(target=playwright_thread_func, daemon=True)
    t.start()

    pr(f"  {C.G}Starting web server on port {WEB_PORT}...{C.R}")
    pr(f"  {C.G}Buka http://0.0.0.0:{WEB_PORT}{C.R}\n")

    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, debug=False)

if __name__ == "__main__":
    main()
