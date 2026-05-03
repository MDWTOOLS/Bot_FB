#!/usr/bin/env python3
"""
Telegram Bot - Railway Deployment
==================================
Bot Telegram dengan webhook support + web status dashboard.
Deploy ke Railway menggunakan port 8080.

Environment Variables:
  BOT_TOKEN     - Token bot dari @BotFather
  WEBHOOK_URL   - Full webhook URL (https://xxx.up.railway.app/webhook)
  PORT          - Port server (default: 8080)
  ADMIN_IDS     - Comma-separated list of Telegram user IDs (admin)
  BOT_OWNER     - Username/ID owner bot (untuk info)
"""

import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command, Filter
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiohttp import web

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8080))
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
BOT_OWNER = os.environ.get("BOT_OWNER", "Unknown")

# Data file
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATS_FILE = DATA_DIR / "stats.json"
USERS_FILE = DATA_DIR / "users.json"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tgbot")

# ============================================================
# STATS & DATA
# ============================================================

class BotData:
    """Thread-safe bot data storage."""

    def __init__(self):
        self.start_time = time.time()
        self.total_users = 0
        self.total_messages = 0
        self.command_usage: dict[str, int] = {}
        self.users: dict[int, dict] = {}
        self._load()

    def _load(self):
        if USERS_FILE.exists():
            try:
                with open(USERS_FILE, "r") as f:
                    self.users = json.load(f)
                self.total_users = len(self.users)
            except Exception:
                pass
        if STATS_FILE.exists():
            try:
                with open(STATS_FILE, "r") as f:
                    s = json.load(f)
                self.total_messages = s.get("total_messages", 0)
                self.command_usage = s.get("command_usage", {})
            except Exception:
                pass

    def save(self):
        try:
            with open(USERS_FILE, "w") as f:
                json.dump(self.users, f, indent=2, default=str)
            with open(STATS_FILE, "w") as f:
                json.dump({
                    "total_messages": self.total_messages,
                    "command_usage": self.command_usage,
                }, f, indent=2)
        except Exception as e:
            log.error("Save error: %s", e)

    def register_user(self, user_id: int, name: str, username: str = ""):
        if user_id not in self.users:
            self.users[user_id] = {
                "name": name,
                "username": username,
                "joined": datetime.now().isoformat(),
                "commands": 0,
            }
            self.total_users = len(self.users)
            self.save()

    def add_message(self):
        self.total_messages += 1
        if self.total_messages % 50 == 0:
            self.save()

    def add_command(self, cmd: str):
        self.command_usage[cmd] = self.command_usage.get(cmd, 0) + 1
        self.add_message()

    @property
    def uptime(self) -> str:
        elapsed = int(time.time() - self.start_time)
        d, remainder = divmod(elapsed, 86400)
        h, remainder = divmod(remainder, 3600)
        m, s = divmod(remainder, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        parts.append(f"{m}m")
        parts.append(f"{s}s")
        return " ".join(parts)

    def get_stats_text(self) -> str:
        top_cmds = sorted(self.command_usage.items(), key=lambda x: -x[1])[:5]
        cmds_str = "\n".join(f"  /{cmd}: {count}x" for cmd, count in top_cmds) or "  (belum ada)"
        return (
            f"📊 **Bot Statistics**\n\n"
            f"👤 Total Users: {self.total_users}\n"
            f"📨 Total Messages: {self.total_messages}\n"
            f"⏱️ Uptime: {self.uptime}\n\n"
            f"🔝 Top Commands:\n{cmds_str}"
        )


bot_data = BotData()

# ============================================================
# FILTERS
# ============================================================

class IsAdmin(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user and message.from_user.id in ADMIN_IDS

is_admin = IsAdmin()

# ============================================================
# BOT SETUP
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
router = Router()
dp = Dispatcher()
dp.include_router(router)

# ============================================================
# HANDLERS
# ============================================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handler /start - welcome message."""
    user = message.from_user
    if user:
        bot_data.register_user(user.id, user.full_name, user.username or "")
    bot_data.add_command("start")

    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Help", callback_data="help")
    kb.button(text="📊 Stats", callback_data="stats")
    kb.button(text="👤 Profile", callback_data="profile")
    kb.adjust(1)

    await message.answer(
        f"👋 Halo <b>{user.full_name}</b>!\n\n"
        f"Selamat datang di bot ini. "
        f"Gunakan menu di bawah atau ketik /help untuk melihat perintah yang tersedia.",
        reply_markup=kb.as_markup(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handler /help - list all commands."""
    bot_data.add_command("help")

    text = (
        "📖 <b>Help - Daftar Perintah</b>\n\n"
        "🔹 <code>/start</code> - Mulai bot\n"
        "🔹 <code>/help</code> - Tampilkan help\n"
        "🔹 <code>/ping</code> - Cek latency bot\n"
        "🔹 <code>/info</code> - Info bot\n"
        "🔹 <code>/profile</code> - Profil kamu\n"
        "🔹 <code>/stats</code> - Statistik bot\n"
        "🔹 <code>/echo &lt;text&gt;</code> - Echo pesan\n"
        "🔹 <code>/id</code> - Get user ID\n"
    )

    if message.from_user and message.from_user.id in ADMIN_IDS:
        text += (
            "\n\n🔒 <b>Admin Commands</b>\n"
            "🔹 <code>/broadcast &lt;text&gt;</code> - Broadcast pesan\n"
            "🔹 <code>/users</code> - Jumlah total user\n"
            "🔹 <code>/restart</code> - Restart info\n"
        )

    await message.answer(text)


@router.message(Command("ping"))
async def cmd_ping(message: Message):
    """Handler /ping - check bot latency."""
    bot_data.add_command("ping")
    start = time.time()
    msg = await message.answer("🏓 Pong!")
    end = time.time()
    latency = (end - start) * 1000
    await msg.edit_text(f"🏓 Pong! <b>{latency:.0f}ms</b>")


@router.message(Command("info"))
async def cmd_info(message: Message):
    """Handler /info - bot information."""
    bot_data.add_command("info")
    await message.answer(
        f"🤖 <b>Bot Info</b>\n\n"
        f"🔹 Owner: {BOT_OWNER}\n"
        f"🔹 Users: {bot_data.total_users}\n"
        f"🔹 Uptime: {bot_data.uptime}\n"
        f"🔹 Messages: {bot_data.total_messages}\n"
        f"🔹 Aiogram: ✅\n"
        f"🔹 Webhook: ✅ (port {PORT})"
    )


@router.message(Command("id"))
async def cmd_id(message: Message):
    """Handler /id - get user ID."""
    bot_data.add_command("id")
    user = message.from_user
    await message.answer(
        f"🆔 <b>Your ID</b>\n\n"
        f"🔹 User ID: <code>{user.id}</code>\n"
        f"🔹 Username: @{user.username or '-'}\n"
        f"🔹 Name: {user.full_name}"
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Handler /profile - user profile."""
    bot_data.add_command("profile")
    user = message.from_user
    udata = bot_data.users.get(user.id, {})
    joined = udata.get("joined", "Unknown")
    cmds = udata.get("commands", 0)

    await message.answer(
        f"👤 <b>Profile</b>\n\n"
        f"🔹 Name: {user.full_name}\n"
        f"🔹 Username: @{user.username or '-'}\n"
        f"🔹 ID: <code>{user.id}</code>\n"
        f"🔹 Joined: {joined}\n"
        f"🔹 Commands: {cmds}"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Handler /stats - bot statistics."""
    bot_data.add_command("stats")
    await message.answer(bot_data.get_stats_text())


@router.message(Command("echo"))
async def cmd_echo(message: Message):
    """Handler /echo - echo message."""
    bot_data.add_command("echo")
    text = message.text.split(maxsplit=1)
    if len(text) > 1:
        await message.answer(f"🔁 {text[1]}")
    else:
        await message.answer("⚠️ Usage: /echo <text>")


# --- Admin Commands ---

@router.message(Command("broadcast"), is_admin)
async def cmd_broadcast(message: Message):
    """Handler /broadcast - send message to all users."""
    bot_data.add_command("broadcast")
    text = message.text.split(maxsplit=1)

    if len(text) < 2:
        await message.answer("⚠️ Usage: /broadcast <text>")
        return

    broadcast_text = text[1]
    user_ids = list(bot_data.users.keys())
    sent = 0
    failed = 0

    status_msg = await message.answer(f"📡 Broadcasting ke {len(user_ids)} users...")

    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 <b>Announcement</b>\n\n{broadcast_text}")
            sent += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ Broadcast selesai!\n\n"
        f"📨 Terkirim: {sent}\n"
        f"❌ Gagal: {failed}"
    )


@router.message(Command("users"), is_admin)
async def cmd_users(message: Message):
    """Handler /users - total users count."""
    bot_data.add_command("users")
    await message.answer(f"👥 Total users: <b>{bot_data.total_users}</b>")


@router.message(Command("restart"), is_admin)
async def cmd_restart(message: Message):
    """Handler /restart - show restart info."""
    bot_data.add_command("restart")
    await message.answer(
        "🔄 <b>Restart</b>\n\n"
        "Bot akan otomatis restart via Railway.\n"
        "Atau restart manual dari Railway dashboard."
    )


# --- Fallback: any text message ---

@router.message(F.text)
async def handle_text(message: Message):
    """Handle any text message."""
    bot_data.add_message()
    user = message.from_user
    if user:
        bot_data.register_user(user.id, user.full_name, user.username or "")


# --- Callback Queries ---

@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    """Callback: Help button."""
    await call.message.edit_text(
        "📖 <b>Help - Daftar Perintah</b>\n\n"
        "🔹 <code>/start</code> - Mulai bot\n"
        "🔹 <code>/help</code> - Tampilkan help\n"
        "🔹 <code>/ping</code> - Cek latency bot\n"
        "🔹 <code>/info</code> - Info bot\n"
        "🔹 <code>/profile</code> - Profil kamu\n"
        "🔹 <code>/stats</code> - Statistik bot\n"
        "🔹 <code>/echo</code> <text> - Echo pesan\n"
        "🔹 <code>/id</code> - Get user ID\n",
    )
    await call.answer()


@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    """Callback: Stats button."""
    await call.message.edit_text(bot_data.get_stats_text())
    await call.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    """Callback: Profile button."""
    user = call.from_user
    udata = bot_data.users.get(user.id, {})
    joined = udata.get("joined", "Unknown")
    cmds = udata.get("commands", 0)

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Back", callback_data="back_menu")
    kb.adjust(1)

    await call.message.edit_text(
        f"👤 <b>Profile</b>\n\n"
        f"🔹 Name: {user.full_name}\n"
        f"🔹 Username: @{user.username or '-'}\n"
        f"🔹 ID: <code>{user.id}</code>\n"
        f"🔹 Joined: {joined}\n"
        f"🔹 Commands: {cmds}",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "back_menu")
async def cb_back_menu(call: CallbackQuery):
    """Callback: Back to main menu."""
    user = call.from_user

    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Help", callback_data="help")
    kb.button(text="📊 Stats", callback_data="stats")
    kb.button(text="👤 Profile", callback_data="profile")
    kb.adjust(1)

    await call.message.edit_text(
        f"👋 Halo <b>{user.full_name}</b>!\n\n"
        f"Gunakan menu di bawah atau ketik /help.",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


# ============================================================
# WEB DASHBOARD (Status Page)
# ============================================================

async def web_dashboard(request: web.Request) -> web.Response:
    """Web dashboard on port 8080."""
    stats = bot_data.get_stats_text().replace("<b>", "").replace("</b>", "")
    stats = stats.replace("<code>", "").replace("</code>", "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Bot Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 20px;
            padding: 40px;
            max-width: 600px;
            width: 100%;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, #00b4d8, #0077b6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
        }}
        .header .subtitle {{
            font-size: 14px;
            color: #666;
        }}
        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(0, 180, 100, 0.1);
            border: 1px solid rgba(0, 180, 100, 0.3);
            color: #00b464;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 24px;
        }}
        .status-dot {{
            width: 10px;
            height: 10px;
            background: #00b464;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
        }}
        .stat-card .value {{
            font-size: 32px;
            font-weight: 700;
            color: #00b4d8;
        }}
        .stat-card .label {{
            font-size: 13px;
            color: #666;
            margin-top: 4px;
        }}
        .stat-card:nth-child(2) .value {{ color: #e94560; }}
        .stat-card:nth-child(3) .value {{ color: #f4a261; }}
        .stat-card:nth-child(4) .value {{ color: #2ec4b6; }}
        .uptime-bar {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 24px;
        }}
        .uptime-bar .label {{
            font-size: 13px;
            color: #666;
            margin-bottom: 4px;
        }}
        .uptime-bar .time {{
            font-size: 20px;
            font-weight: 600;
            color: #e0e0e0;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            font-size: 14px;
        }}
        .info-row:last-child {{ border-bottom: none; }}
        .info-row .key {{ color: #666; }}
        .info-row .val {{ color: #e0e0e0; font-weight: 500; }}
        .footer {{
            text-align: center;
            margin-top: 24px;
            font-size: 12px;
            color: #444;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Telegram Bot</h1>
            <div class="subtitle">Powered by Aiogram + Railway</div>
        </div>

        <div style="text-align:center">
            <div class="status-badge">
                <div class="status-dot"></div>
                Online
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="value">{bot_data.total_users}</div>
                <div class="label">Total Users</div>
            </div>
            <div class="stat-card">
                <div class="value">{bot_data.total_messages}</div>
                <div class="label">Messages</div>
            </div>
            <div class="stat-card">
                <div class="value">{sum(bot_data.command_usage.values())}</div>
                <div class="label">Commands</div>
            </div>
            <div class="stat-card">
                <div class="value">{len(bot_data.command_usage)}</div>
                <div class="label">Unique Cmds</div>
            </div>
        </div>

        <div class="uptime-bar">
            <div class="label">Uptime</div>
            <div class="time">{bot_data.uptime}</div>
        </div>

        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px 20px;">
            <div class="info-row">
                <span class="key">Owner</span>
                <span class="val">{BOT_OWNER}</span>
            </div>
            <div class="info-row">
                <span class="key">Framework</span>
                <span class="val">Aiogram 3.x</span>
            </div>
            <div class="info-row">
                <span class="key">Webhook</span>
                <span class="val">Port {PORT}</span>
            </div>
            <div class="info-row">
                <span class="key">Status</span>
                <span class="val" style="color:#00b464;">Running</span>
            </div>
        </div>

        <div class="footer">
            Deployed on Railway
        </div>
    </div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html", status=200)


# ============================================================
# WEBHOOK & WEB SERVER
# ============================================================

async def webhook_handler(request: web.Request) -> web.Response:
    """Handle incoming Telegram webhook."""
    update = await bot.update()
    await dp.feed_update(bot, update)
    return web.Response(status=200)


async def on_startup(app: web.Application):
    """Setup webhook on startup."""
    log.info("Setting webhook: %s", WEBHOOK_URL)
    await bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True,
    )
    log.info("Webhook set! Bot is running on port %d", PORT)


async def on_shutdown(app: web.Application):
    """Cleanup on shutdown."""
    await bot.session.close()
    bot_data.save()
    log.info("Bot shutdown complete.")


def create_app() -> web.Application:
    """Create aiohttp web app."""
    app = web.Application()
    app.router.add_get("/", web_dashboard)
    app.router.add_get("/health", lambda r: web.Response(text="OK", status=200))
    app.router.add_post("/webhook", webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    if not BOT_TOKEN:
        log.error("BOT_TOKEN environment variable not set!")
        log.error("Set it with: export BOT_TOKEN=your_token")
        raise SystemExit(1)

    log.info("Starting Telegram Bot...")
    log.info("Owner: %s", BOT_OWNER)
    log.info("Admins: %s", ADMIN_IDS or "None")
    log.info("Port: %d", PORT)

    app = create_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
