# Telegram Bot - Railway Deployment

Bot Telegram dengan webhook + web status dashboard.
Deploy ke Railway menggunakan port 8080.

## Setup

1. Buat bot di [@BotFather](https://t.me/BotFather), dapatkan token
2. Fork/deploy repo ini ke Railway
3. Set environment variables di Railway:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Token dari @BotFather |
| `WEBHOOK_URL` | ✅ | Full URL: `https://xxx.up.railway.app/webhook` |
| `ADMIN_IDS` | ❌ | Comma-separated Telegram user IDs (admin) |
| `BOT_OWNER` | ❌ | Nama owner bot |
| `DATA_DIR` | ❌ | Path data (default: `/app/data`) |
| `PORT` | ❌ | Port server (default: `8080`) |

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Mulai bot |
| `/help` | Daftar perintah |
| `/ping` | Cek latency |
| `/info` | Info bot |
| `/profile` | Profil kamu |
| `/stats` | Statistik bot |
| `/echo <text>` | Echo pesan |
| `/id` | Get user ID |

### Admin Only

| Command | Description |
|---------|-------------|
| `/broadcast <text>` | Broadcast ke semua user |
| `/users` | Jumlah total user |
| `/restart` | Restart info |

## Local Development

```bash
pip install -r requirements.txt
export BOT_TOKEN="your_token"
export PORT=8080
python3 bot.py
```

> Note: Untuk local dev tanpa webhook, gunakan polling mode (tambahkan flag --polling).

## Structure

```
tg-bot-railway/
├── bot.py              # Main bot + web dashboard
├── requirements.txt    # Dependencies
├── Dockerfile          # Docker build
├── railway.json        # Railway config
└── README.md           # This file
```
