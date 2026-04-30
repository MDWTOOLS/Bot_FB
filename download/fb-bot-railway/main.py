#!/usr/bin/env python3
"""
Bot Facebook v14 - Railway Deployment
- Cookie-based auth (env FB='cookie')
- Live View: MJPEG stream (captures inside bot loop, always live)
- SSE push for status (NO polling = zero GET /api/status spam)
- Stream mode: find post -> comment -> next
- No limits: continuous scroll & comment
- Queue architecture: ALL Playwright ops single thread (safe)
- RC (remote control) with D-Pad
- Notes page: Success/Blocked URL viewer
- Persistent storage via Railway Volume (/app/data)
- Optimized for Railway (viewport 800x600, JPEG q=20, low FPS)
"""

import json, time, random, os, re, sys, datetime, threading, traceback, queue as _queue
import logging
from flask import Flask, render_template_string, request, jsonify, Response
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ===========================================================
#  CONFIG
# ===========================================================
# Persistent data directory:
# - Railway: set DATA_DIR=/app/data (Volume mount) in Railway env
# - Local/VPS: uses script directory (default)
DATA_DIR = os.environ.get("DATA_DIR", "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    DIR = DATA_DIR
else:
    DIR = os.path.dirname(os.path.abspath(__file__))

ENV_COOKIE = os.environ.get("FB", "").strip()
BROWSER_DATA = os.path.join(DIR, "browser_data")
WEB_PORT = int(os.environ.get("PORT", "8080"))
MAX_RETRY = 2
RETRY_WAIT = 3
COMMENT_WAIT = 5
CEKLIST = os.path.join(DIR, "ceklist.txt")
RESTRICTED = os.path.join(DIR, "restricted.txt")

# Live View - ultra lightweight for 2GB RAM
LIVE_FPS = 0.5
LIVE_QUALITY = 20
LIVE_VIEWPORT = {"width": 800, "height": 600}
CAPTURE_INTERVAL = 2.0

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
_state_event = threading.Event()
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
# APP_DIR = code directory (config, comments bundled with code)
APP_DIR = os.path.dirname(os.path.abspath(__file__))

def load_cfg():
    p = os.path.join(APP_DIR, "config.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_comments():
    p = os.path.join(APP_DIR, "comments.txt")
    with open(p, "r", encoding="utf-8") as f:
        raw = f.read()
    if "---" in raw:
        return [c.strip() for c in raw.split("---") if c.strip()]
    return [c.strip() for c in raw.split("\n") if c.strip()]

def save_comments(comments_list):
    p = os.path.join(APP_DIR, "comments.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n---\n".join(comments_list) + "\n")

def load_set(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(l.strip() for l in f if l.strip())
    return set()

def load_lines(path):
    """Load file as list of lines."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    return []

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
#  GLOBAL PAGE REF
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

    unique = []
    seen_ids = set()
    for p in collected:
        if p["id"] not in seen_ids:
            seen_ids.add(p["id"])
            unique.append(p)
    return unique

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
        capture_frame()
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

    def find_chromium():
        nonlocal ctx, page
        os.makedirs(BROWSER_DATA, exist_ok=True)
        try:
            ctx = pw.chromium.launch_persistent_context(
                BROWSER_DATA, headless=True, args=bargs,
                viewport=LIVE_VIEWPORT, user_agent=ua, locale="id-ID")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _page_ref = page
            slog("Browser berhasil dijalankan", "BROWSER")
            return True
        except Exception as e:
            slog(f"Browser gagal: {str(e)[:80]}", "ERROR")
            sset("err", f"Browser error: {str(e)[:60]}")
            sset("phase", "IDLE")
            return False

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
                time.sleep(30)  # Wait 30s before retry to prevent spam

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
                            if not find_chromium():
                                continue
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

            capture_frame()
            time.sleep(0.3)

        except Exception as e:
            slog(f"Thread error: {str(e)[:60]}", "ERROR")
            traceback.print_exc()
            time.sleep(1)

# ===========================================================
#  HTML TEMPLATE - MAIN PAGE (Tabbed UI + Gear Icon)
# ===========================================================
HTML = """<!DOCTYPE html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Facebook</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:520px;margin:0 auto;padding:12px 10px}
.hdr{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:10px;position:relative}
.hdr h1{font-size:1.1rem;color:#58a6ff}
.hdr .sub{color:#484f58;font-size:.65rem;text-align:center}
.gear-btn{position:absolute;right:0;top:50%;transform:translateY(-50%);width:36px;height:36px;border-radius:50%;background:#161b22;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;color:#484f58}
.gear-btn:hover{background:#21262d;border-color:#58a6ff;color:#58a6ff}
.gear-btn svg{width:18px;height:18px;fill:currentColor}
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
.gear-wrap{position:absolute;right:0;top:50%;transform:translateY(-50%)}.gear-btn{width:36px;height:36px;border-radius:50%;background:#161b22;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;color:#484f58}.gear-btn:hover{background:#21262d;border-color:#58a6ff;color:#58a6ff}.gear-btn svg{width:18px;height:18px;fill:currentColor}.gear-menu{position:absolute;right:0;top:42px;background:#161b22;border:1px solid #30363d;border-radius:10px;min-width:180px;z-index:50;display:none;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.4)}.gear-menu.open{display:block}.gear-menu a{display:flex;align-items:center;gap:10px;padding:10px 14px;color:#c9d1d9;text-decoration:none;font-size:.78rem;font-weight:500;transition:all .15s;border-bottom:1px solid #21262d}.gear-menu a:last-child{border-bottom:none}.gear-menu a:hover{background:#0d419a;color:#fff}.gear-menu a svg{width:16px;height:16px;fill:currentColor;flex-shrink:0}.gear-menu a .lbl{flex:1}.gear-menu a .badge{background:#21262d;padding:1px 8px;border-radius:8px;font-size:.6rem;color:#484f58}
</style></head><body>
<div class="wrap">
<div class="hdr">
  <div><h1>Bot Facebook</h1><div class="sub">Create by MDW</div></div>
  <div class="gear-wrap">
    <div class="gear-btn" onclick="toggleMenu()" title="Menu"><svg viewBox="0 0 24 24"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92-3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg></div>
    <div class="gear-menu" id="gearMenu">
      <a href="/notes"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg><span class="lbl">Note Activations</span><span class="badge" id="menuNotesBadge">0</span></a>
      <a href="/comments"><svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg><span class="lbl">Comments</span><span class="badge" id="menuCommentsBadge">0</span></a>
    </div>
  </div>
</div>
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
var activeTab="start",liveActive=false;
function switchTab(n){activeTab=n;document.querySelectorAll(".tab-btn").forEach(function(b){b.classList.remove("active")});document.querySelectorAll(".tab-panel").forEach(function(p){p.classList.remove("active")});document.getElementById("tabBtn"+n.charAt(0).toUpperCase()+n.slice(1)).classList.add("active");document.getElementById("panel"+n.charAt(0).toUpperCase()+n.slice(1)).classList.add("active");if(n==="console")startLive();else stopLive()}
function startLive(){if(liveActive)return;document.getElementById("liveImg").src="/live";liveActive=true}
function stopLive(){if(!liveActive)return;document.getElementById("liveImg").src="";liveActive=false}
function renderLogs(logs){if(!logs||!logs.length)return;var box=document.getElementById("logBox");var wasAtBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-30;var html="";var last=Math.max(0,logs.length-200);for(var i=last;i<logs.length;i++){var line=logs[i];var c="li";if(line.indexOf("[SUCCESS]")>-1)c="ls";else if(line.indexOf("[FAILED]")>-1)c="lf";else if(line.indexOf("[BLOCKED]")>-1)c="lb";else if(line.indexOf("[WARNING]")>-1)c="lw";else if(line.indexOf("[ERROR]")>-1)c="le";else if(line.indexOf("[BOT]")>-1)c="lbot";else if(line.indexOf("[RC]")>-1)c="lrc";else if(line.indexOf("[SCRAPE]")>-1)c="lsc";else if(line.indexOf("[COOKIE]")>-1)c="lck";var s=line.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");html+='<div class="'+c+'">'+s+"</div>"}box.innerHTML=html;if(wasAtBottom)box.scrollTop=box.scrollHeight}
function logScrollTop(){document.getElementById("logBox").scrollTop=0}
function logScrollBottom(){var b=document.getElementById("logBox");b.scrollTop=b.scrollHeight}
function update(d){if(!d)return;document.getElementById("sO").textContent=d.ok;document.getElementById("sF").textContent=d.fail;document.getElementById("sB").textContent=d.blocked;var mnb=document.getElementById("menuNotesBadge");if(mnb)mnb.textContent=(d.ok||0)+(d.blocked||0);var mcb=document.getElementById("menuCommentsBadge");if(mcb)mcb.textContent="";if(d.name&&d.name!=="N/A"){document.getElementById("acctAvatar").textContent=d.name.charAt(0).toUpperCase();document.getElementById("acctName").textContent=d.name}else{document.getElementById("acctAvatar").textContent="?";document.getElementById("acctName").textContent="Belum login"}var dot=document.getElementById("statusDot");dot.className="status-dot sd-"+d.phase.toLowerCase();document.getElementById("statusTxt").textContent=d.msg||d.phase;var eb=document.getElementById("errBox");if(d.err){eb.textContent=d.err;eb.classList.remove("hid")}else{eb.classList.add("hid")}var sh=function(id){document.getElementById(id).classList.remove("hid")};var hi=function(id){document.getElementById(id).classList.add("hid")};if(d.phase==="IDLE"){sh("cookieCard");hi("controlCard");document.getElementById("loginMsg").classList.add("hid")}else if(d.phase==="LOGIN"){sh("cookieCard");hi("controlCard");var lm=document.getElementById("loginMsg");lm.textContent="Memverifikasi...";lm.className="msg msg-e";lm.classList.remove("hid");document.getElementById("btnLogin").disabled=true}else if(d.phase==="READY"){hi("cookieCard");sh("controlCard");document.getElementById("btnStart").disabled=false;document.getElementById("btnStart").innerHTML='<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor"><path d="M8 5v14l11-7z"/></svg> Start Auto Comment';document.getElementById("btnStop").disabled=true}else if(d.phase==="RUNNING"){hi("cookieCard");sh("controlCard");document.getElementById("btnStart").disabled=true;document.getElementById("btnStart").innerHTML='<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg> Bot Berjalan...';document.getElementById("btnStop").disabled=false}var cD=document.getElementById("consoleDot");var lT=document.getElementById("liveTag");var lO=document.getElementById("liveOff");if(d.phase==="READY"||d.phase==="RUNNING"||d.phase==="LOGIN"){cD.classList.remove("off");lO.classList.add("hid");lT.classList.remove("hid")}else{cD.classList.add("off");lO.classList.remove("hid");lT.classList.add("hid")}renderLogs(d.logs)}
function doLogin(){var c=document.getElementById("inCookie").value.trim();if(!c){alert("Paste cookie Facebook terlebih dahulu!");return}var b=document.getElementById("btnLogin");b.disabled=true;b.textContent="Memverifikasi...";api("/api/load-cookie","POST",{cookie:c}).then(function(d){update(d);b.disabled=false;b.textContent="Load Cookie & Login"})}
function startBot(){document.getElementById("btnStart").disabled=true;api("/api/bot-start","POST")}
function stopBot(){api("/api/bot-stop","POST")}
function resetAll(){if(!confirm("Reset semua data bot?"))return;stopLive();api("/api/reset","POST").then(function(){location.reload()})}
function onLiveClick(ev){var r=ev.currentTarget.getBoundingClientRect();var x=((ev.clientX-r.left)/r.width*100).toFixed(1);var y=((ev.clientY-r.top)/r.height*100).toFixed(1);api("/api/rc/click","POST",{x:+x,y:+y})}
function rcSend(){var t=document.getElementById("rcInput").value;if(!t)return;api("/api/rc/type","POST",{text:t});document.getElementById("rcInput").value=""}
function rcKey(k){api("/api/rc/key","POST",{key:k})}
function rcScroll(d){api("/api/rc/scroll","POST",{direction:d})}
function toggleMenu(){var m=document.getElementById("gearMenu");m.classList.toggle("open")}document.addEventListener("click",function(e){var w=document.querySelector(".gear-wrap");if(w&&!w.contains(e.target)){document.getElementById("gearMenu").classList.remove("open")}});
document.getElementById("rcInput").addEventListener("keydown",function(e){if(e.key==="Enter"){e.preventDefault();rcSend()}});
(function(){var sE=document.getElementById("sseBadge");var es=null;var r=0;function c(){if(es){es.close();es=null}es=new EventSource("/api/stream");es.onopen=function(){r=0;sE.textContent="SSE Live";sE.className="sse-badge sse-on"};es.onmessage=function(e){try{update(JSON.parse(e.data))}catch(er){}};es.onerror=function(){sE.textContent="...";sE.className="sse-badge sse-off";es.close();es=null;r++;setTimeout(c,Math.min(r*2,10)*1000)}}api("/api/status").then(function(d){update(d);c()});window.addEventListener("beforeunload",function(){if(es)es.close()})})();
</script></body></html>"""

# ===========================================================
#  HTML TEMPLATE - NOTES PAGE (NOTE ACTIVATIONS)
# ===========================================================
HTML_NOTES = """<!DOCTYPE html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Note Activations - Bot Facebook</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:520px;margin:0 auto;padding:12px 10px}
.top-bar{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.back-btn{width:36px;height:36px;border-radius:50%;background:#161b22;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;color:#c9d1d9;text-decoration:none}
.back-btn:hover{background:#21262d;border-color:#58a6ff;color:#58a6ff}
.back-btn svg{width:18px;height:18px;fill:currentColor}
.page-title{flex:1}
.page-title h1{font-size:1rem;color:#58a6ff;font-weight:700;letter-spacing:.5px}
.page-title .sub{color:#484f58;font-size:.6rem;margin-top:1px}
.switch-bar{display:flex;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;margin-bottom:10px}
.switch-btn{flex:1;padding:10px 8px;text-align:center;font-size:.78rem;font-weight:600;color:#484f58;cursor:pointer;border:none;background:transparent;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:6px}
.switch-btn:first-child{border-right:1px solid #30363d}
.switch-btn:hover{background:#1c2129;color:#c9d1d9}
.switch-btn.active-s{background:#238636;color:#fff;box-shadow:inset 0 -2px 0 #3fb950}
.switch-btn.active-b{background:#da3633;color:#fff;box-shadow:inset 0 -2px 0 #f85149}
.switch-btn svg{width:15px;height:15px;fill:currentColor;flex-shrink:0}
.badge-count{display:inline-flex;align-items:center;justify-content:center;background:rgba(255,255,255,.15);padding:1px 8px;border-radius:10px;font-size:.68rem;font-weight:700;min-width:22px}
.editor-wrap{background:#010409;border:1px solid #21262d;border-radius:10px;overflow:hidden}
.editor-header{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:#161b22;border-bottom:1px solid #21262d}
.editor-header .title{font-size:.7rem;color:#484f58;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.total-badge{display:inline-flex;align-items:center;gap:5px;background:#0d1117;border:1px solid #30363d;padding:3px 10px;border-radius:8px}
.total-badge .icon{width:14px;height:14px}
.total-badge .num{font-size:.75rem;font-weight:700;color:#58a6ff}
.total-badge .lbl{font-size:.6rem;color:#484f58}
.editor-box{padding:0;height:60vh;overflow-y:auto;font-family:'Cascadia Code',Consolas,monospace;font-size:.65rem;line-height:1.8}
.editor-box::-webkit-scrollbar{width:6px}
.editor-box::-webkit-scrollbar-track{background:transparent}
.editor-box::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
.editor-box::-webkit-scrollbar-thumb:hover{background:#484f58}
.editor-line{display:flex;padding:0 8px;border-bottom:1px solid #0d1117}
.editor-line:hover{background:#0d11170a}
.line-num{width:40px;flex-shrink:0;text-align:right;padding-right:10px;color:#30363d;user-select:none;-webkit-user-select:none;font-size:.6rem}
.line-text{flex:1;color:#3fb950;word-break:break-all;white-space:pre-wrap}
.empty-msg{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:#30363d;gap:8px;padding:40px 20px;text-align:center}
.empty-msg svg{width:40px;height:40px;fill:#21262d}
.empty-msg .txt{font-size:.78rem}
.editor-footer{display:flex;justify-content:center;padding:8px;gap:6px}
.editor-scroll-btn{width:36px;height:36px;border-radius:8px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .15s}
.editor-scroll-btn:hover{background:#21262d;border-color:#58a6ff;color:#58a6ff}
.editor-scroll-btn svg{width:14px;height:14px;fill:currentColor}
</style></head><body>
<div class="wrap">
<div class="top-bar">
  <a href="/" class="back-btn" title="Kembali"><svg viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg></a>
  <div class="page-title"><h1>NOTE ACTIVATIONS</h1><div class="sub">Bot Facebook &middot; Create by MDW</div></div>
</div>
<div class="switch-bar">
  <button class="switch-btn active-s" id="swSuccess" onclick="switchView('success')">
    <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
    Success <span class="badge-count" id="badgeSuccess">0</span>
  </button>
  <button class="switch-btn" id="swBlocked" onclick="switchView('blocked')">
    <svg viewBox="0 0 24 24"><path d="M12 2C6.47 2 2 6.47 2 12s4.47 10 10 10 10-4.47 10-10S17.53 2 12 2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z"/></svg>
    Blocked <span class="badge-count" id="badgeBlocked">0</span>
  </button>
</div>
<div class="editor-wrap">
  <div class="editor-header">
    <span class="title" id="editorTitle">SUCCESS URLs</span>
    <div class="total-badge">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="#58a6ff" stroke-width="2"><path d="M13.828 10.172a4 4 0 0 0-5.656 0l-4 4a4 4 0 1 0 5.656 5.656l1.102-1.101"/><path d="M10.172 13.828a4 4 0 0 0 5.656 0l4-4a4 4 0 1 0-5.656-5.656l-1.102 1.101"/></svg>
      <span class="num" id="totalCount">0</span>
      <span class="lbl">URLs</span>
    </div>
  </div>
  <div class="editor-box" id="editorBox">
    <div class="empty-msg"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg><span class="txt">Belum ada data</span></div>
  </div>
  <div class="editor-footer">
    <button class="editor-scroll-btn" onclick="editorTop()" title="Ke atas"><svg viewBox="0 0 24 24"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg></button>
    <button class="editor-scroll-btn" onclick="editorBottom()" title="Ke bawah"><svg viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg></button>
  </div>
</div>
</div>
<script>
var successData=[];var blockedData=[];var currentView='success';
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function renderEditor(data){
  var box=document.getElementById('editorBox');
  if(!data||!data.length){
    box.innerHTML='<div class="empty-msg"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg><span class="txt">Belum ada data</span></div>';
    return;
  }
  var html='';
  for(var i=0;i<data.length;i++){
    html+='<div class="editor-line"><div class="line-num">'+(i+1)+'</div><div class="line-text">'+esc(data[i])+'</div></div>';
  }
  box.innerHTML=html;
}
function switchView(v){
  currentView=v;
  var swS=document.getElementById('swSuccess');
  var swB=document.getElementById('swBlocked');
  var title=document.getElementById('editorTitle');
  var total=document.getElementById('totalCount');
  if(v==='success'){
    swS.className='switch-btn active-s';
    swB.className='switch-btn';
    title.textContent='SUCCESS URLs';
    renderEditor(successData);
    total.textContent=successData.length;
  }else{
    swS.className='switch-btn';
    swB.className='switch-btn active-b';
    title.textContent='BLOCKED URLs';
    renderEditor(blockedData);
    total.textContent=blockedData.length;
  }
}
function editorTop(){document.getElementById('editorBox').scrollTop=0}
function editorBottom(){var b=document.getElementById('editorBox');b.scrollTop=b.scrollHeight}
function loadData(){
  fetch('/api/notes').then(function(r){return r.json()}).then(function(d){
    if(!d)return;
    successData=d.success||[];
    blockedData=d.blocked||[];
    document.getElementById('badgeSuccess').textContent=successData.length;
    document.getElementById('badgeBlocked').textContent=blockedData.length;
    switchView(currentView);
  }).catch(function(){});
}
loadData();
setInterval(loadData,5000);
</script></body></html>"""

# ===========================================================
#  HTML TEMPLATE - COMMENTS PAGE (Manage Comments)
# ===========================================================
HTML_COMMENTS = """<!DOCTYPE html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manage Comments - Bot Facebook</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.wrap{max-width:520px;margin:0 auto;padding:12px 10px}
.top-bar{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.back-btn{width:36px;height:36px;border-radius:50%;background:#161b22;border:1px solid #30363d;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;color:#c9d1d9;text-decoration:none}
.back-btn:hover{background:#21262d;border-color:#58a6ff;color:#58a6ff}
.back-btn svg{width:18px;height:18px;fill:currentColor}
.page-title{flex:1}
.page-title h1{font-size:1rem;color:#58a6ff;font-weight:700;letter-spacing:.5px}
.page-title .sub{color:#484f58;font-size:.6rem;margin-top:1px}
.add-bar{display:flex;gap:6px;margin-bottom:10px}
.add-bar textarea{flex:1;min-height:44px;max-height:120px;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:.8rem;outline:none;font-family:inherit;resize:vertical}
.add-bar textarea:focus{border-color:#58a6ff}
.btn{padding:10px 18px;border:none;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .15s;display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap}
.btn svg{width:16px;height:16px;fill:currentColor}
.btn-go{background:#238636;color:#fff}.btn-go:hover{background:#2ea043}
.btn-sm{padding:6px 12px;font-size:.75rem}
.btn-del{background:transparent;color:#f85149;border:1px solid #30363d;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:.7rem;font-weight:600;transition:all .15s}
.btn-del:hover{background:#3d1114;border-color:#f85149}
.btn-edit{background:transparent;color:#58a6ff;border:1px solid #30363d;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:.7rem;font-weight:600;transition:all .15s}
.btn-edit:hover{background:#0d419a22;border-color:#58a6ff}
.comment-list{display:flex;flex-direction:column;gap:6px}
.comment-item{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px 12px;transition:all .2s}
.comment-item:hover{border-color:#484f58}
.comment-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.comment-num{font-size:.65rem;color:#484f58;font-weight:600;background:#0d1117;padding:2px 8px;border-radius:6px}
.comment-actions{display:flex;gap:4px}
.comment-text{font-size:.82rem;color:#e6edf3;line-height:1.5;word-break:break-word;white-space:pre-wrap}
.comment-text.editing{display:none}
.edit-box{display:none}
.edit-box textarea{width:100%;min-height:60px;max-height:150px;padding:8px 10px;background:#0d1117;border:1px solid #58a6ff;border-radius:8px;color:#e6edf3;font-size:.82rem;outline:none;font-family:inherit;resize:vertical;margin-bottom:6px}
.edit-box textarea:focus{border-color:#58a6ff}
.edit-actions{display:flex;gap:6px;justify-content:flex-end}
.btn-save{background:#238636;color:#fff;padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-size:.75rem;font-weight:600;transition:all .15s}
.btn-save:hover{background:#2ea043}
.btn-cancel{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:.75rem;font-weight:600;transition:all .15s}
.btn-cancel:hover{background:#30363d}
.total-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 0;margin-bottom:8px;border-bottom:1px solid #21262d}
.total-bar .lbl{font-size:.7rem;color:#484f58}
.total-bar .num{font-size:.9rem;font-weight:700;color:#58a6ff}
.empty-state{display:flex;flex-direction:column;align-items:center;padding:40px 20px;color:#30363d;gap:8px;text-align:center}
.empty-state svg{width:40px;height:40px;fill:#21262d}
.empty-state .txt{font-size:.78rem}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:#238636;color:#fff;padding:8px 18px;border-radius:8px;font-size:.78rem;font-weight:600;z-index:99;transition:transform .3s;pointer-events:none}
.toast.show{transform:translateX(-50%) translateY(0)}
.toast.err{background:#da3633}
</style></head><body>
<div class="wrap">
<div class="top-bar">
  <a href="/" class="back-btn" title="Kembali"><svg viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg></a>
  <div class="page-title"><h1>MANAGE COMMENTS</h1><div class="sub">Bot Facebook &middot; Create by MDW</div></div>
</div>
<div class="total-bar"><span class="lbl">Total Comments</span><span class="num" id="totalCount">0</span></div>
<div class="add-bar">
  <textarea id="newComment" placeholder="Tulis komentar baru..."></textarea>
  <button class="btn btn-go" onclick="addComment()"><svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg> Add</button>
</div>
<div class="comment-list" id="commentList">
  <div class="empty-state"><svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg><span class="txt">Belum ada komentar</span></div>
</div>
</div>
<div class="toast" id="toast"></div>
<script>
var comments=[];
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function toast(msg,isErr){var t=document.getElementById('toast');t.textContent=msg;t.className=isErr?'toast err show':'toast show';setTimeout(function(){t.className='toast'},2000)}
function renderList(){
  var box=document.getElementById('commentList');
  var total=document.getElementById('totalCount');
  total.textContent=comments.length;
  if(!comments.length){box.innerHTML='<div class="empty-state"><svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg><span class="txt">Belum ada komentar</span></div>';return}
  var html='';
  for(var i=0;i<comments.length;i++){
    html+='<div class="comment-item" id="item'+i+'">';
    html+='<div class="comment-header"><span class="comment-num">#'+(i+1)+'</span><div class="comment-actions">';
    html+='<button class="btn-edit" onclick="editComment('+i+')">Edit</button>';
    html+='<button class="btn-del" onclick="deleteComment('+i+')">Delete</button>';
    html+='</div></div>';
    html+='<div class="comment-text" id="text'+i+'">'+esc(comments[i])+'</div>';
    html+='<div class="edit-box" id="editBox'+i+'"><textarea id="editInput'+i+'">'+esc(comments[i])+'</textarea>';
    html+='<div class="edit-actions"><button class="btn-cancel" onclick="cancelEdit('+i+')">Cancel</button><button class="btn-save" onclick="saveEdit('+i+')">Save</button></div></div>';
    html+='</div>';
  }
  box.innerHTML=html;
}
function addComment(){
  var ta=document.getElementById('newComment');
  var txt=ta.value.trim();
  if(!txt){toast('Tulis komentar dulu!',true);return}
  fetch('/api/comments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:txt})})
  .then(function(r){return r.json()})
  .then(function(d){if(d&&d.ok){ta.value='';loadComments();toast('Komentar ditambahkan!')}else{toast('Gagal menambahkan!',true)}})
  .catch(function(){toast('Error!',true)});
}
function deleteComment(idx){
  if(!confirm('Hapus komentar #'+(idx+1)+'?'))return;
  fetch('/api/comments/'+idx,{method:'DELETE'})
  .then(function(r){return r.json()})
  .then(function(d){if(d&&d.ok){loadComments();toast('Komentar dihapus!')}else{toast('Gagal menghapus!',true)}})
  .catch(function(){toast('Error!',true)});
}
function editComment(idx){
  document.getElementById('text'+idx).style.display='none';
  document.getElementById('editBox'+idx).style.display='block';
  document.getElementById('editInput'+idx).focus();
}
function cancelEdit(idx){
  document.getElementById('text'+idx).style.display='';
  document.getElementById('editBox'+idx).style.display='none';
}
function saveEdit(idx){
  var txt=document.getElementById('editInput'+idx).value.trim();
  if(!txt){toast('Komentar tidak boleh kosong!',true);return}
  fetch('/api/comments/'+idx,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:txt})})
  .then(function(r){return r.json()})
  .then(function(d){if(d&&d.ok){loadComments();toast('Komentar diperbarui!')}else{toast('Gagal update!',true)}})
  .catch(function(){toast('Error!',true)});
}
function loadComments(){
  fetch('/api/comments').then(function(r){return r.json()}).then(function(d){
    if(d&&d.comments){comments=d.comments;renderList()}
  }).catch(function(){});
}
document.getElementById('newComment').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();addComment()}});
loadComments();
</script></body></html>"""

# ===========================================================
#  FLASK APP
# ===========================================================
app = Flask(__name__)

log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/notes")
def notes_page():
    return render_template_string(HTML_NOTES)

@app.route("/comments")
def comments_page():
    return render_template_string(HTML_COMMENTS)

@app.route("/api/comments")
def api_get_comments():
    try:
        c = load_comments()
        return jsonify({"comments": c, "total": len(c)})
    except Exception as e:
        return jsonify({"comments": [], "total": 0, "error": str(e)})

@app.route("/api/comments", methods=["POST"])
def api_add_comment():
    d = request.get_json()
    txt = (d.get("text") or "").strip()
    if not txt:
        return jsonify({"ok": False, "error": "empty"}), 400
    comments = load_comments()
    comments.append(txt)
    save_comments(comments)
    return jsonify({"ok": True, "total": len(comments)})

@app.route("/api/comments/<int:idx>", methods=["PUT"])
def api_edit_comment(idx):
    d = request.get_json()
    txt = (d.get("text") or "").strip()
    if not txt:
        return jsonify({"ok": False, "error": "empty"}), 400
    comments = load_comments()
    if idx < 0 or idx >= len(comments):
        return jsonify({"ok": False, "error": "invalid index"}), 400
    comments[idx] = txt
    save_comments(comments)
    return jsonify({"ok": True, "total": len(comments)})

@app.route("/api/comments/<int:idx>", methods=["DELETE"])
def api_delete_comment(idx):
    comments = load_comments()
    if idx < 0 or idx >= len(comments):
        return jsonify({"ok": False, "error": "invalid index"}), 400
    comments.pop(idx)
    save_comments(comments)
    return jsonify({"ok": True, "total": len(comments)})

@app.route("/api/status")
def api_status():
    return jsonify(get_sd())

@app.route("/api/stream")
def api_stream():
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

@app.route("/api/notes")
def api_notes():
    """Return success (ceklist) and blocked (restricted) URL lists."""
    success_lines = load_lines(CEKLIST)
    blocked_lines = load_lines(RESTRICTED)
    return jsonify({"success": success_lines, "blocked": blocked_lines,
                    "total": len(success_lines) + len(blocked_lines)})

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
    pr(f"\n{C.CY}{C.B}  BOT FACEBOOK v14 - RAILWAY{C.R}")
    pr(f"  {C.GR}SSE Push | MJPEG Live | Cookie Auth | Notes | Railway{C.R}\n")

    comments = load_comments()
    ceklist = load_set(CEKLIST)
    restricted = load_set(RESTRICTED)

    pr(f"  Komen   : {C.G}{len(comments)}{C.R}")
    pr(f"  Ceklist : {C.D}{len(ceklist)} post{C.R}")
    pr(f"  Blocked : {C.D}{len(restricted)} post{C.R}")
    pr(f"  Live    : {C.D}{LIVE_FPS} fps, quality {LIVE_QUALITY}%, {LIVE_VIEWPORT['width']}x{LIVE_VIEWPORT['height']}{C.R}")
    pr(f"  SSE     : {C.G}enabled{C.R} (zero polling){C.R}")
    pr(f"  Data    : {C.D}{DIR}{C.R}")

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
