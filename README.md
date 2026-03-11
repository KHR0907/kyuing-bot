# kyuing-bot

[한국어](README.ko.md)

`kyuing-bot` is a Discord TTS bot that reads text messages in voice channels using the [Supertonic-2](https://github.com/jnjsoftweb/supertonic) TTS engine. It also runs a Quart-based web dashboard with Discord OAuth login and operational statistics.

## Features

- Automatically read messages from configured text channels into a voice channel
- Per-user TTS preferences through slash commands
- Register and remove TTS channels per guild
- Join, leave, and stop playback commands for voice control
- Admin dashboard with Discord OAuth login
- Daily usage snapshots and rotating application logs

## Slash Commands

- `/join`: summon the bot to your current voice channel
- `/leave`: disconnect the bot from the voice channel
- `/stop`: stop the current playback
- `/setchannel`: register the current text channel as a TTS channel
- `/unsetchannel`: remove TTS from the current text channel
- `/channels`: list registered TTS channels in the current guild
- `/voice`: choose the default voice
- `/speed`: set playback speed
- `/lang`: set the default language
- `/quality`: set the synthesis quality level
- `/settings`: view your current TTS preferences
- `/voices`: list available voices

## System Requirements

- Docker & Docker Compose
- Python 3.11+ (included in Docker image)
- FFmpeg (included in Docker image)
- RAM: 4 GB+ recommended (Supertonic-2 model is loaded into memory at startup)

## Quick Start

### 1. Prepare environment variables

Copy `.env.example` to `.env` and fill in the values.

```bash
cp .env.example .env
```

### 2. Run with Docker

```bash
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f app
```

## Required Environment Variables

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here
DISCORD_CLIENT_SECRET=your_discord_client_secret_here
DISCORD_REDIRECT_URI=https://your-domain.example/callback
DASHBOARD_ADMIN_IDS=123456789012345678,234567890123456789
WEB_SECRET_KEY=replace-with-a-long-random-secret
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

## Environment Variable Details

- `DISCORD_TOKEN`: Discord bot token
- `DISCORD_CLIENT_ID`: Discord OAuth client ID
- `DISCORD_CLIENT_SECRET`: Discord OAuth client secret
- `DISCORD_REDIRECT_URI`: OAuth callback URL registered in the Discord Developer Portal
- `DASHBOARD_ADMIN_IDS`: comma-separated Discord user IDs granted dashboard admin access by default
- `WEB_SECRET_KEY`: secret key used to sign web sessions
- `WEB_PORT`: web dashboard port
- `DATABASE_PATH`: SQLite database file path
- `DAILY_STATS_RETENTION_DAYS`: retention period for daily stats
- `LOG_PATH`: application log file path
- `LOG_RETENTION_DAYS`: retention period for logs
- `SESSION_COOKIE_SECURE`: should be `true` in HTTPS environments
- `SESSION_COOKIE_SAMESITE`: SameSite value for the session cookie

## Server Deployment

### 1. Install Docker

Ubuntu example:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

### 2. Deploy the app

```bash
git clone <repo-url>
cd kyuing-bot
cp .env.example .env
mkdir -p data
mkdir -p logs
docker compose up -d --build
```

## Operations

Restart:

```bash
docker compose restart app
```

Update:

```bash
git pull
docker compose up -d --build
```

Logs:

```bash
docker compose logs -f app
```

Application logs are written to `logs/app.log` and retained for 30 days by default. Daily dashboard statistics are retained for 365 days by default.
