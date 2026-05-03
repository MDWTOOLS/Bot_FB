#!/usr/bin/env python3
"""
C4Coins Auto Faucet Bot - Linux Terminal Edition
=================================================
Bot faucet otomatis untuk feyorra.top dengan TUI profesional.

Penggunaan:
  python3 bot_terminal.py              # Jalankan biasa
  python3 bot_terminal.py --reset      # Hapus config & mulai fresh
  python3 bot_terminal.py --debug      # Mode debug (verbose)

Requirements:
  pip3 install rich requests opencv-python-headless pytesseract numpy

  Tesseract OCR (system package):
    sudo apt install tesseract-ocr      # Debian/Ubuntu
    sudo dnf install tesseract          # Fedora
    sudo pacman -S tesseract            # Arch
"""

import os
import re
import sys
import signal
import time
import json
import random
import logging
import argparse
import threading
from datetime import datetime
from pathlib import Path

import requests
import cv2
import numpy as np
import pytesseract

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TimeElapsedColumn,
    TextColumn,
)
from rich.logging import RichHandler
from rich.prompt import Prompt
from rich.columns import Columns
from rich import box

# ============================================================
# CONSTANTS
# ============================================================

BASE_URL = "https://feyorra.top"
CONFIG_FILE = Path.home() / ".config" / "c4coins" / "config.json"
STATS_FILE = Path.home() / ".config" / "c4coins" / "stats.json"
LOG_FILE = Path.home() / ".config" / "c4coins" / "bot.log"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# Pastikan direktori config ada
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# RICH CONSOLE SETUP
# ============================================================

console = Console()

# ============================================================
# GLOBAL STATE (thread-safe)
# ============================================================

class BotState:
    """State global bot, thread-safe."""

    def __init__(self):
        self.running = True
        self.paused = False
        self.total_earned = 0.0
        self.total_claims = 0
        self.last_claim_msg = ""
        self.last_claim_time = ""
        self.status = "Starting..."
        self.balance = "N/A"
        self.uptime_start = time.time()
        self.captcha_fails = 0
        self.captcha_solves = 0
        self.connections_lost = 0
        self.activity_log: list[str] = []
        self._lock = threading.Lock()

    def add_log(self, msg: str):
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.activity_log.append(f"[{timestamp}] {msg}")
            # Simpan maks 50 entry terakhir
            if len(self.activity_log) > 50:
                self.activity_log = self.activity_log[-50:]

    def set_status(self, status: str):
        with self._lock:
            self.status = status

    def add_earned(self, amount: float, msg: str):
        with self._lock:
            self.total_earned += amount
            self.total_claims += 1
            self.last_claim_msg = msg
            self.last_claim_time = datetime.now().strftime("%H:%M:%S")
            self.add_log(f"[green]+{amount:.4f} Coins[/green] {msg}")

    @property
    def uptime(self) -> str:
        elapsed = int(time.time() - self.uptime_start)
        h, remainder = divmod(elapsed, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"

    def snapshot(self) -> dict:
        """Ambil snapshot state (thread-safe)."""
        with self._lock:
            return {
                "status": self.status,
                "balance": self.balance,
                "earned": self.total_earned,
                "claims": self.total_claims,
                "last_msg": self.last_claim_msg,
                "last_time": self.last_claim_time,
                "uptime": self.uptime,
                "captcha_solves": self.captcha_solves,
                "captcha_fails": self.captcha_fails,
                "connections_lost": self.connections_lost,
                "log": list(self.activity_log),
            }


state = BotState()

# ============================================================
# SIGNAL HANDLING
# ============================================================

def handle_signal(signum, frame):
    """Handle SIGINT (Ctrl+C) dan SIGTERM."""
    state.running = False
    state.set_status("Shutting down...")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ============================================================
# LOGGING
# ============================================================

def setup_logging(debug: bool = False):
    """Setup logging ke file dan console."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )

    log = logging.getLogger("c4coins")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.addHandler(file_handler)

    # Rich console handler
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
    )
    rich_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    log.addHandler(rich_handler)

    return log


# ============================================================
# CONFIG MANAGEMENT
# ============================================================

def load_config() -> dict:
    """Muat config dari file, atau buat baru."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            log.debug("Config dimuat dari %s", CONFIG_FILE)
            return config
        except json.JSONDecodeError:
            log.warning("Config file rusak, membuat baru...")

    # Interactive setup
    console.print(Panel(
        "[bold cyan]Setup Akun C4Coins[/bold cyan]\n\n"
        "Masukkan cookie dari browser (login ke feyorra.top,\n"
        "buka DevTools > Application > Cookies > copy ci_session).",
        title="Konfigurasi",
        border_style="cyan",
    ))

    cookie = Prompt.ask("[yellow]Cookie[/yellow]")
    if not cookie.strip():
        console.print("[red]Cookie wajib diisi![/red]")
        sys.exit(1)

    config = {
        "cookie": cookie.strip(),
        "user_agent": DEFAULT_USER_AGENT,
    }

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

    console.print(f"[green]Config tersimpan ke {CONFIG_FILE}[/green]")
    return config


def reset_config():
    """Hapus semua file config & stats."""
    for f in [CONFIG_FILE, STATS_FILE]:
        if f.exists():
            f.unlink()
    console.print("[yellow]Config & stats direset.[/yellow]")


def load_stats() -> tuple[float, int]:
    """Muat statistik harian."""
    today = datetime.now().strftime("%Y-%m-%d")
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return data.get("earned", 0.0), data.get("claims", 0)
        except (json.JSONDecodeError, KeyError):
            pass
    return 0.0, 0


def save_stats(earned: float, claims: int):
    """Simpan statistik harian."""
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_FILE, "w") as f:
        json.dump({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "earned": earned,
            "claims": claims,
        }, f, indent=4)


# ============================================================
# HTTP HELPERS
# ============================================================

def make_session() -> requests.Session:
    """Buat requests session dengan retry default."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        max_retries=3,
        pool_connections=5,
        pool_maxsize=5,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def headers_get(cookie: str, ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE_URL}/dashboard",
        "Cookie": cookie,
    }


def headers_post(cookie: str, ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE_URL}/faucet",
        "Cookie": cookie,
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def headers_img(cookie: str, ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "image/*;q=0.8",
        "Referer": f"{BASE_URL}/faucet",
        "Cookie": cookie,
    }


# ============================================================
# CAPTCHA SOLVER
# ============================================================

def solve_captcha(image_bytes: bytes) -> str | None:
    """
    Solve captcha 4-digit menggunakan OpenCV + Tesseract.

    Pipeline:
      1. Decode image -> grayscale
      2. Binary threshold (OTSU inverse)
      3. Morphological open (noise removal)
      4. Contour detection per digit
      5. Crop, pad, upscale, threshold tiap ROI
      6. OCR dengan digit-only whitelist
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w > 4 and h > 10:
                boxes.append((x, y, w, h))
        boxes.sort(key=lambda b: b[0])

        if len(boxes) < 2:
            return None

        ocr_config = r"--oem 3 --psm 10 -c tessedit_char_whitelist=0123456789"
        result = ""

        for i, (x, y, w, h) in enumerate(boxes):
            if i == 0:
                continue

            roi = cleaned[y:y+h, x:x+w]
            roi = cv2.copyMakeBorder(roi, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
            roi = cv2.bitwise_not(roi)
            roi = cv2.resize(roi, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            _, roi = cv2.threshold(roi, 150, 255, cv2.THRESH_BINARY)

            text = pytesseract.image_to_string(roi, config=ocr_config).strip()
            if text.isdigit():
                result += text
                if len(result) == 4:
                    break

        return result if len(result) == 4 else None

    except Exception as e:
        log.error("Captcha error: %s", e)
        return None


# ============================================================
# HTML PARSERS
# ============================================================

def parse_success(html: str) -> str | None:
    patterns = [
        r'title:\s*[\'"]([^\'"]+)[\'"]',
        r"([\d\.]+\s+Coins\s+has been added to your balance)",
        r"([\d\.]+\s+[A-Z]+\s+added to[^\']+)",
    ]
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def parse_wait(html: str) -> int:
    m = re.search(r"let wait = (\d+)", html)
    return int(m.group(1)) if m else 180


def parse_balance(html: str) -> str | None:
    m = re.search(r"<p>(.*?)</p>", html)
    return m.group(1) if m else None


# ============================================================
# PICK-A-BOX
# ============================================================

def play_pickabox(session: requests.Session, hdrs: dict, rounds: int = 5):
    """Mainkan Pick-a-Box game secara otomatis."""
    state.add_log("[cyan]Playing Pick-a-Box...[/cyan]")

    for r in range(1, rounds + 1):
        if not state.running:
            break
        try:
            resp = session.get(f"{BASE_URL}/pickabox", headers=hdrs, timeout=30)
            page = resp.text

            csrf = re.search(r'name="csrf_token_name" value="([^"]+)"', page)
            tok = re.search(r'name="token" value="([^"]+)"', page)
            grd = re.search(r'name="game_guard" value="([^"]+)"', page)

            if not all([csrf, tok, grd]):
                continue

            box = random.randint(1, 3)
            ph = hdrs.copy()
            ph["Content-Type"] = "application/x-www-form-urlencoded"
            ph["Origin"] = BASE_URL
            ph["Referer"] = f"{BASE_URL}/pickabox"

            session.post(f"{BASE_URL}/pickabox/play", data={
                "csrf_token_name": csrf.group(1),
                "token": tok.group(1),
                "game_guard": grd.group(1),
                "bet_amount": 1,
                "selected_box": box,
            }, headers=ph, timeout=30)

            log.debug("Box round %d: picked box %d", r, box)

            if r < rounds:
                time.sleep(2)

        except requests.RequestException:
            break

    # Refresh balance
    try:
        dr = session.get(f"{BASE_URL}/dashboard", headers=hdrs, timeout=30)
        b = parse_balance(dr.text)
        if b:
            state.balance = b
    except requests.RequestException:
        pass


# ============================================================
# TUI LAYOUT BUILDER
# ============================================================

def build_layout(snap: dict) -> Layout:
    """Bangun layout TUI dari snapshot state."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )

    # --- HEADER: Status Bar ---
    status_color = "green" if "Claiming" in snap["status"] or "Ready" in snap["status"] else "yellow"
    if "Error" in snap["status"] or "Lost" in snap["status"]:
        status_color = "red"
    if "Shutting" in snap["status"]:
        status_color = "red"

    header_text = Text()
    header_text.append(" C4COINS ", style="bold reverse")
    header_text.append(f" {snap['status']} ", style=f"bold {status_color} on default")
    header_text.append(f" Uptime: {snap['uptime']} ", style="dim")
    header_text.append(f" Balance: {snap['balance']} ", style="bold cyan")
    layout["header"].update(Panel(header_text, box=box.SIMPLE, style="dim"))

    # --- BODY: Stats + Log ---
    body = Layout()
    body.split_column(
        Layout(name="stats", size=12),
        Layout(name="logs", ratio=1),
    )

    # Stats table
    stats_table = Table(
        title="Statistics",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=False,
        title_style="bold cyan",
        expand=True,
    )
    stats_table.add_column("Metric", style="dim", width=20)
    stats_table.add_column("Value", style="bold", width=25)
    stats_table.add_column("Metric", style="dim", width=20)
    stats_table.add_column("Value", style="bold", width=25)

    stats_table.add_row("Total Earned", f"{snap['earned']:.4f} Coins", "Last Claim", snap["last_time"] or "-")
    stats_table.add_row("Total Claims", str(snap["claims"]), "Captcha Solved", str(snap["captcha_solves"]))
    stats_table.add_row("Captcha Fails", str(snap["captcha_fails"]), "Reconnects", str(snap["connections_lost"]))

    if snap["last_msg"]:
        stats_table.add_row("Last Reward", snap["last_msg"], "", "")

    body["stats"].update(stats_table)

    # Activity log
    log_lines = snap["log"][-15:] if snap["log"] else ["(no activity yet)"]
    log_panel = Panel(
        "\n".join(log_lines),
        title="Activity Log",
        border_style="green",
        box=box.ROUNDED,
        title_style="bold green",
        expand=True,
    )
    body["logs"].update(log_panel)

    layout["body"].update(body)

    # --- FOOTER ---
    footer = Text()
    footer.append(" [Q] Quit ", style="bold red")
    footer.append(" [P] Pause ", style="bold yellow")
    footer.append(" [R] Reset ", style="bold magenta")
    footer.append(" Ctrl+C graceful shutdown ", style="dim")
    layout["footer"].update(Panel(footer, box=box.SIMPLE, style="dim"))

    return layout


# ============================================================
# MAIN BOT LOGIC
# ============================================================

def bot_main_loop(
    session: requests.Session,
    cookie: str,
    ua: str,
    progress: Progress,
    task_id,
):
    """Loop utama bot (dijalankan di thread terpisah)."""
    hdrs = headers_get(cookie, ua)

    while state.running:
        try:
            # --- Validasi session ---
            state.set_status("Checking session...")
            try:
                resp = session.get(f"{BASE_URL}/dashboard", headers=hdrs, timeout=30)
                if "Dashboard" not in resp.text:
                    state.set_status("Session expired!")
                    state.add_log("[red]Session expired. Silakan update cookie.[/red]")
                    CONFIG_FILE.unlink(missing_ok=True)
                    time.sleep(5)
                    # Minta config baru
                    try:
                        config = load_config()
                        cookie = config["cookie"]
                        ua = config.get("user_agent", DEFAULT_USER_AGENT)
                        hdrs = headers_get(cookie, ua)
                        continue
                    except SystemExit:
                        state.running = False
                        break

                bal = parse_balance(resp.text)
                if bal:
                    state.balance = bal
                state.set_status("Session OK")

            except requests.RequestException as e:
                state.set_status(f"Connection error: {e}")
                state.connections_lost += 1
                state.add_log(f"[red]Connection error: {e}[/red]")
                time.sleep(5)
                session = make_session()
                continue

            # --- Faucet page ---
            state.set_status("Loading faucet...")
            resp = session.get(f"{BASE_URL}/faucet", headers=hdrs, timeout=30)
            page = resp.text

            # Cek limit
            if "Daily limit" in page or ("limit" in page and "Ready" not in page):
                state.set_status("Daily limit reached!")
                state.add_log("[bold red]Daily limit tercapai. Bot berhenti.[/bold red]")
                break

            if "complete shortlink" in page:
                state.set_status("Shortlink required!")
                state.add_log("[yellow]Selesaikan misi shortlink dulu.[/yellow]")
                break

            if "Ready To Claim" in page:
                # Parse form
                csrf = re.search(r'name="csrf_token_name" id="token" value="([^"]+)"', page)
                tok = re.search(r'name="token" value="([^"]+)"', page)
                img = re.search(r'<img id="Imageid" src="([^"]+)"', page)
                fld = re.search(
                    r'<input type="number" class="form-control border border-dark mb-3" name="([^"]+)"',
                    page,
                )

                if not all([csrf, tok, img, fld]):
                    state.set_status("Form parse failed")
                    time.sleep(2)
                    continue

                # Download captcha
                state.set_status("Downloading captcha...")
                img_resp = session.get(
                    img.group(1),
                    headers=headers_img(cookie, ua),
                    timeout=30,
                )

                if len(img_resp.content) < 100:
                    state.set_status("Playing Pick-a-Box...")
                    play_pickabox(session, hdrs)
                    time.sleep(2)
                    continue

                # Solve captcha
                state.set_status("Solving captcha...")
                digits = solve_captcha(img_resp.content)

                if not digits:
                    state.captcha_fails += 1
                    state.add_log("[yellow]Captcha gagal di-solve.[/yellow]")
                    time.sleep(1)
                    continue

                state.captcha_solves += 1
                log.debug("Captcha: %s", digits)

                # Submit claim
                state.set_status("Submitting claim...")
                session.post(
                    f"{BASE_URL}/faucet/verify",
                    data={
                        "csrf_token_name": csrf.group(1),
                        "token": tok.group(1),
                        fld.group(1): digits,
                    },
                    headers=headers_post(cookie, ua),
                    allow_redirects=False,
                    timeout=30,
                )

                time.sleep(2)

                # Cek hasil
                resp = session.get(f"{BASE_URL}/faucet", headers=hdrs, timeout=30)
                msg = parse_success(resp.text)

                if msg:
                    amt = re.search(r"([\d\.]+)\s+Coins", msg)
                    amount = float(amt.group(1)) if amt else 0.001
                    state.add_earned(amount, msg)

                    # Update file stats
                    earned, claims = load_stats()
                    save_stats(earned + amount, claims + 1)

                    # Refresh balance
                    try:
                        dr = session.get(f"{BASE_URL}/dashboard", headers=hdrs, timeout=30)
                        b = parse_balance(dr.text)
                        if b:
                            state.balance = b
                    except requests.RequestException:
                        pass

                    # Cooldown
                    wait = parse_wait(resp.text)
                    state.set_status(f"Cooldown {wait}s")
                    state.add_log(f"[dim]Cooldown {wait} detik...[/dim]")

                    for _ in range(wait):
                        if not state.running:
                            break
                        time.sleep(1)

                else:
                    state.add_log("[yellow]Claim gagal, coba lagi...[/yellow]")
                    time.sleep(2)

            else:
                # Tunggu
                wait = parse_wait(page)
                state.set_status(f"Waiting {wait}s")
                state.add_log(f"[dim]Tunggu {wait} detik...[/dim]")

                for _ in range(wait):
                    if not state.running:
                        break
                    time.sleep(1)

        except requests.ConnectionError:
            state.connections_lost += 1
            state.set_status("Reconnecting...")
            state.add_log("[red]Koneksi putus, reconnect...[/red]")
            time.sleep(10)
            session = make_session()
            hdrs = headers_get(cookie, ua)

        except Exception as e:
            log.error("Loop error: %s", e)
            state.set_status(f"Error: {e}")
            state.add_log(f"[red]{e}[/red]")
            time.sleep(5)


def input_listener():
    """Listener untuk keyboard input di thread terpisah."""
    while state.running:
        try:
            ch = sys.stdin.read(1).strip().lower()
            if ch == "q":
                state.running = False
                state.set_status("Quit by user")
                break
            elif ch == "p":
                state.paused = not state.paused
                if state.paused:
                    state.set_status("Paused")
                    state.add_log("[yellow]Bot di-pause.[/yellow]")
                else:
                    state.set_status("Resumed")
                    state.add_log("[green]Bot di-resume.[/green]")
            elif ch == "r":
                reset_config()
                state.set_status("Config reset")
                state.add_log("[magenta]Config direset.[/magenta]")
        except (EOFError, KeyboardInterrupt):
            break


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    global log

    # Parse arguments
    parser = argparse.ArgumentParser(
        description="C4Coins Auto Faucet Bot - Linux Terminal Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--reset", action="store_true", help="Hapus config & mulai fresh")
    parser.add_argument("--debug", action="store_true", help="Mode debug (verbose)")
    args = parser.parse_args()

    if args.reset:
        reset_config()

    # Setup logging
    log = setup_logging(debug=args.debug)

    # Print startup banner
    console.print()
    console.print(Panel(
        "[bold cyan]"
        "  _     _  __  __  ___  ___\n"
        " | |   (_)/ _|/ _||_ _|\n"
        " | |__  _| |_| |_  | |\n"
        " | '_ \\| |  _|  _| | |\n"
        " | |_) | | | | |__ | |\n"
        " |_.__/|_|_| |_|   |___|\n"
        "[/bold cyan]\n"
        "[dim]Auto Faucet Bot - Linux Terminal Edition[/dim]\n"
        "[dim]feyorra.top (C4Coins)[/dim]",
        box=box.DOUBLE_EDGE,
        border_style="cyan",
    ))

    # Load config
    try:
        config = load_config()
    except SystemExit:
        return

    cookie = config["cookie"]
    ua = config.get("user_agent", DEFAULT_USER_AGENT)

    # Restore stats
    earned, claims = load_stats()
    state.total_earned = earned
    state.total_claims = claims

    # Init session
    session = make_session()

    # Start input listener thread
    import select
    input_thread = threading.Thread(target=input_listener, daemon=True)
    input_thread.start()

    # Run TUI
    console.print("[green]Bot started. Press [Q] to quit, [P] to pause.[/green]\n")

    with Live(
        build_layout(state.snapshot()),
        console=console,
        refresh_per_second=2,
        screen=True,
    ) as live:
        # Start bot logic thread
        bot_thread = threading.Thread(
            target=bot_main_loop,
            args=(session, cookie, ua, None, None),
            daemon=True,
        )
        bot_thread.start()

        # Update TUI
        while state.running:
            if not state.paused:
                live.update(build_layout(state.snapshot()))
            time.sleep(0.5)

        live.update(build_layout(state.snapshot()))

    # Final message
    snap = state.snapshot()
    console.print()
    console.print(Panel(
        f"[bold]Session Summary[/bold]\n\n"
        f"  Total Earned : [green]{snap['earned']:.4f} Coins[/green]\n"
        f"  Total Claims : [cyan]{snap['claims']}[/cyan]\n"
        f"  Captcha Solved: [cyan]{snap['captcha_solves']}[/cyan]\n"
        f"  Captcha Failed: [red]{snap['captcha_fails']}[/red]\n"
        f"  Reconnects   : [yellow]{snap['connections_lost']}[/yellow]\n"
        f"  Uptime       : [dim]{snap['uptime']}[/dim]",
        title="C4Coins Bot",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    console.print("[dim]Log tersimpan di:[/dim] " + str(LOG_FILE))
    console.print("[dim]Config di:[/dim] " + str(CONFIG_FILE))


if __name__ == "__main__":
    main()
