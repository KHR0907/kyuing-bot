# kyuing-bot

[한국어](README.ko.md)

`kyuing-bot` is a Discord TTS bot and web dashboard. It reads messages from configured text channels into Discord voice channels, supports Google Cloud Text-to-Speech by default, and can run multiple Discord bot tokens from one server/dashboard.

## Current architecture

A fresh installation starts a supervisor that owns the dashboard and every Discord bot worker:

```text
Docker container: kyuing-bot
└─ supervisor: python supervisor.py
   ├─ Web dashboard on WEB_PORT
   └─ worker: python bot.py --worker --bot-id 1
```

Additional bots are stored in SQLite and started as the same kind of worker subprocess:

```text
Docker container: kyuing-bot
├─ supervisor + Web dashboard + BotProcessManager
├─ worker: python bot.py --worker --bot-id 1
└─ worker: python bot.py --worker --bot-id <BOT_ID>
```

Bot tokens are **not** passed as command-line arguments. Workers receive only `--bot-id` and read their token from the database, avoiding token exposure in process listings.

## Features

- Read messages from configured text channels into a voice channel
- Google TTS as the default TTS engine (`ko-KR-Standard-A`)
- Optional Supertonic-3 engine support (31 languages + `na` auto-detect, expression tags like `<laugh>`)
- Per-user TTS preferences through slash commands
- Per-bot TTS channels, keyword replacements, pronunciation rules, usage stats, and dashboard metrics
- Multi-bot management from one dashboard: add, start, stop, restart, enable, and disable bots
- Discord OAuth login for the admin dashboard
- Daily usage snapshots and rotating application logs
- Soundboard: register short audio clips (8 seconds max, mp4/mp3/wav/ogg/webm) with keywords and play them with `/play`

## Slash commands

- `/join`: summon the bot to your current voice channel
- `/leave`: disconnect the bot from the voice channel
- `/stop`: stop current playback
- `/setchannel`: register the current text channel as a TTS channel
- `/unsetchannel`: remove TTS from the current text channel
- `/channels`: list registered TTS channels in the current guild
- `/engine`: change your TTS engine
- `/voice`: choose your voice for the selected engine
- `/speed`: set playback speed
- `/lang`: set the Supertonic language (31 languages + `na` auto-detect, chosen via autocomplete)
- `/quality`: set Supertonic synthesis quality (inference steps: 5 / 8 / 10 / 12, default 8)
- `/settings`: view your current TTS preferences
- `/voices`: list available voices for your selected engine
- `/pronounce`: preview keyword/pronunciation replacement
- `/usage`: view Google TTS monthly character usage
- `/sound add`: register an audio clip (max 8s) with a keyword in the current server
- `/sound remove`: delete a sound registered in the current server
- `/sound list`: list available sounds (server + global)
- `/play`: play a registered sound in your voice channel

## System requirements

- Docker and Docker Compose plugin
- Python 3.11+ if running without Docker
- FFmpeg, included in the Docker image
- For Google TTS: Google Cloud Text-to-Speech API key
- RAM: 1 GB+ is usually enough for Google TTS; 4 GB+ recommended if using Supertonic-3 because the model is loaded locally

## What a fresh clone contains

After cloning this repository, you only have code and deployment files.

Included:

```text
bot.py
bot_process_manager.py
supervisor.py
dashboard_context.py
audio_scheduler.py
worker_lock.py
config.py
database.py
logging_setup.py
tts_engine.py
tts_engines/
cogs/
web/
tests/
requirements.txt
Dockerfile
docker-compose.yml
.env.example
README.md
README.ko.md
```

Not included:

```text
.env
data/bot.db
data/sounds/
logs/app.log
Discord bot tokens
Google TTS API keys
Existing bot registrations
Existing dashboard data / TTS channels / keyword rules / usage stats
```

On first startup, SQLite creates `data/bot.db` and seeds one default bot record using `DISCORD_TOKEN` from `.env`:

```text
bot_id=1
name=Default Bot
token=<DISCORD_TOKEN from .env>
enabled=1
```

Additional bots must be added later from the dashboard.

## Discord setup

### 1. Create a Discord application and bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application.
3. Go to **Bot** and create/reset a bot token.
4. Enable **Message Content Intent**. This bot reads channel messages for TTS, so this intent is required.
5. Copy the bot token into `.env` as `DISCORD_TOKEN`.

### 2. Configure OAuth for the dashboard

In the same Discord application:

1. Go to **OAuth2**.
2. Copy the Client ID and Client Secret into `.env`.
3. Add a redirect URI that exactly matches `DISCORD_REDIRECT_URI`.

Local example:

```text
http://localhost:5001/callback
```

Production HTTPS example:

```text
https://your-domain.example/callback
```

### 3. Invite the bot to your Discord server

Use OAuth2 URL Generator or construct an invite with these scopes:

```text
bot
applications.commands
```

Recommended permissions:

```text
View Channels
Send Messages
Read Message History
Connect
Speak
Use Voice Activity
```

A commonly used permission integer for these permissions is:

```text
36768768
```

## Google Cloud Text-to-Speech setup

Google TTS is the default engine. To use it:

1. Create or select a Google Cloud project.
2. Enable **Cloud Text-to-Speech API**.
3. Create an API key.
4. Put it in `.env` as `GOOGLE_TTS_API_KEY`.

Google TTS input is limited by the Google API request limit:

```text
5,000 UTF-8 bytes per request
```

That is roughly:

- 5,000 ASCII characters
- about 1,666 Korean characters, because Korean characters are usually 3 bytes in UTF-8

## Quick start with Docker

### 1. Clone the repository

```bash
git clone https://github.com/KHR0907/kyuing-bot.git
cd kyuing-bot
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in real values.

Local development example:

```env
APP_ENV=production
DISCORD_TOKEN=your_discord_bot_token
DISCORD_CLIENT_ID=your_discord_client_id
DISCORD_CLIENT_SECRET=your_discord_client_secret
DISCORD_REDIRECT_URI=http://localhost:5001/callback
DASHBOARD_ADMIN_IDS=your_discord_user_id
WEB_SECRET_KEY=replace_with_a_long_random_string
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=Lax
GOOGLE_TTS_API_KEY=your_google_tts_api_key
```

Production HTTPS example differences:

```env
DISCORD_REDIRECT_URI=https://your-domain.example/callback
SESSION_COOKIE_SECURE=true
```

### 3. Create local data directories

```bash
mkdir -p data logs
```

### 4. Build and run

```bash
docker compose up -d --build
```

### 5. Verify startup

```bash
docker compose ps
docker compose logs -f app
```

Open the dashboard:

```text
http://localhost:5001/
```

or, on a server:

```text
http://<server-ip>:5001/
```

If you use a reverse proxy, point it to `127.0.0.1:5001`.

## Required environment variables

```env
APP_ENV=production
DISCORD_TOKEN=your_discord_bot_token
DISCORD_CLIENT_ID=your_discord_client_id
DISCORD_CLIENT_SECRET=your_discord_client_secret
DISCORD_REDIRECT_URI=https://your-domain.example/callback
DASHBOARD_ADMIN_IDS=123456789012345678,234567890123456789
WEB_SECRET_KEY=replace_with_a_long_random_string
WEB_PORT=5001
DATABASE_PATH=data/bot.db
DAILY_STATS_RETENTION_DAYS=365
LOG_PATH=logs/app.log
LOG_RETENTION_DAYS=30
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
GOOGLE_TTS_API_KEY=your_google_tts_api_key
```

## Environment variable details

- `DISCORD_TOKEN`: token for the first/default Discord bot.
- `DISCORD_CLIENT_ID`: Discord OAuth client ID for dashboard login.
- `DISCORD_CLIENT_SECRET`: Discord OAuth client secret for dashboard login.
- `DISCORD_REDIRECT_URI`: OAuth callback URL registered in the Discord Developer Portal.
- `DASHBOARD_ADMIN_IDS`: comma-separated Discord user IDs that are allowed to access the dashboard by default.
- `WEB_SECRET_KEY`: secret key for signing web sessions. Use a long random value in production.
- `WEB_PORT`: dashboard port. Docker Compose maps host `WEB_PORT` to container `WEB_PORT`.
- `DATABASE_PATH`: SQLite database path inside the container. With the default Compose file, `data/bot.db` persists to `./data/bot.db` on the host.
- `DAILY_STATS_RETENTION_DAYS`: daily statistics retention period.
- `LOG_PATH`: application log path inside the container. With the default Compose file, `logs/app.log` persists to `./logs/app.log` on the host.
- `LOG_RETENTION_DAYS`: application log retention period.
- `SESSION_COOKIE_SECURE`: set `true` behind HTTPS, set `false` for local HTTP testing.
- `SESSION_COOKIE_SAMESITE`: session cookie SameSite value. `Lax` is the default.
- `GOOGLE_TTS_API_KEY`: Google Cloud Text-to-Speech API key.
- `APP_ENV`: use `production` in production and `development` for local testing.
- `AUDIO_QUEUE_MAXSIZE`: maximum queued audio jobs per guild; defaults to 25.
- `AUDIO_QUEUE_MAX_PER_USER`: maximum pending jobs per user; defaults to 5.
- `AUDIO_QUEUE_JOB_TTL_SECONDS`: queued job expiry in seconds; defaults to 60.
- `TTS_USER_COOLDOWN_SECONDS`: minimum interval between a user's TTS requests; defaults to 2 seconds.
- `TTS_REQUIRE_VOICE_MEMBERSHIP`: when true, only users in the bot's voice channel can trigger TTS.

With `APP_ENV=production`, startup fails unless OAuth credentials, a 32+ character
`WEB_SECRET_KEY`, secure session cookies, and at least one `DASHBOARD_ADMIN_IDS` entry are configured.

## Adding more bots

After the first bot and dashboard are running:

1. Create another Discord application/bot in the Discord Developer Portal.
2. Enable **Message Content Intent** for the new bot.
3. Invite the new bot to the desired server with `bot` and `applications.commands` scopes.
4. Log in to the dashboard.
5. Open the bot management section.
6. Enter the new bot name and bot token.
7. The dashboard validates the token with Discord, stores it in SQLite, and starts a worker process.

The new bot will have separate `bot_id`-scoped settings:

```text
TTS channels
keyword replacements
pronunciation rules
usage statistics
dashboard metrics
user settings
```

On container restart, only enabled bots whose desired state is `running` are started.
Bots stopped from the dashboard stay stopped. Crashed workers restart with exponential backoff;
automatic restart pauses after more than five failures in ten minutes.

## Useful operations

Restart:

```bash
docker compose restart app
```

Update from git:

```bash
git pull --ff-only
docker compose build
docker compose up -d
```

View logs:

```bash
docker compose logs -f app
```

Check container status:

```bash
docker compose ps
```

Application readiness check:

```bash
curl -fsS http://127.0.0.1:${WEB_PORT:-5001}/health/ready
```

Every bot, including the default bot, runs as an independent supervisor-owned worker and can be
started, stopped, or restarted from the dashboard.

Run tests locally:

```bash
python -m pytest tests/ -q
```

## Data persistence and backups

The default Compose file persists these directories on the host:

```text
./data -> /app/data
./logs -> /app/logs
```

The important file is:

```text
./data/bot.db
```

Back it up before migrations or major updates:

```bash
mkdir -p data/backups
cp data/bot.db "data/backups/bot.db.backup-$(date +%Y%m%d-%H%M%S)"
```

## Troubleshooting

### `.env` missing or `DISCORD_TOKEN` empty

The app requires `DISCORD_TOKEN` for the initial bot. Create `.env` from `.env.example` and fill in the token.

### Dashboard login redirects fail

Check that `DISCORD_REDIRECT_URI` exactly matches the Redirect URI registered in Discord Developer Portal, including protocol, domain, port, and path.

### Login works locally but not in production

For HTTPS production deployments, use:

```env
SESSION_COOKIE_SECURE=true
```

For local HTTP testing, use:

```env
SESSION_COOKIE_SECURE=false
```

### Google TTS fails

Check that:

- `GOOGLE_TTS_API_KEY` is set
- Cloud Text-to-Speech API is enabled in Google Cloud
- The API key is allowed to call Text-to-Speech
- Your text does not exceed 5,000 UTF-8 bytes

### Permission errors on `data/` or `logs/`

The Docker container runs as a non-root `appuser`. If mounted directories are not writable:

```bash
mkdir -p data logs
chmod -R u+rwX,g+rwX data logs
```

If needed, adjust ownership for your server setup.

## Security notes

- Never commit `.env`.
- Never paste bot tokens or API keys into issues, logs, or screenshots.
- Bot tokens are stored in SQLite for multi-bot operation; protect `data/bot.db` like a secret.
- Use HTTPS and a strong `WEB_SECRET_KEY` in production.
