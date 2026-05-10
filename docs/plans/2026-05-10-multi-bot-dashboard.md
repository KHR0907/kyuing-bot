# Multi Bot Dashboard Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Allow KYUING dashboard admins to add Discord bot tokens from the web UI, start additional bot worker processes, view project-wide and per-bot statistics, and keep pronunciation/keyword rules isolated per bot.

**Architecture:** Split the current single-process `bot.py` into a dashboard parent process plus bot worker processes. Store bots in SQLite, scope bot-owned data by `bot_id`, and use a process manager in the dashboard to start/stop/restart bot workers. Existing data is migrated to the default bot row (`id=1`).

**Tech Stack:** Python, discord.py, Quart, SQLite/aiosqlite, Docker Compose.

---

## Task 1: Add multi-bot schema support

**Objective:** Add a `bots` table and migrate existing one-bot tables to include `bot_id`.

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_multibot.py`

**Acceptance criteria:**
- `init_db()` creates `bots` and seeds default bot id=1 from env token when present.
- Existing tables are scoped by `bot_id`: `tts_channels`, `daily_stats`, `global_keyword_aliases`, `guild_keyword_aliases`, `pronunciation_audit`, `tts_char_usage`, `user_settings`.
- Existing data is preserved under `bot_id=1`.
- Caches are keyed by bot id.

## Task 2: Add bot CRUD APIs in database

**Objective:** Add database functions for creating, listing, enabling, disabling, and updating runtime status of bot records.

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_multibot.py`

**Required functions:**
- `get_bots()`
- `get_bot(bot_id)`
- `create_bot(name, token, created_by=None, discord_bot_user_id=None, discord_username=None)`
- `get_bot_token(bot_id)`
- `set_bot_enabled(bot_id, enabled)`
- `update_bot_runtime_status(bot_id, status, pid=None, last_error=None)`
- `get_enabled_bots()`

## Task 3: Thread bot_id through bot runtime

**Objective:** Make each Discord worker operate against exactly one `bot_id`.

**Files:**
- Modify: `bot.py`
- Modify: `cogs/channels.py`
- Modify: `cogs/tts.py`

**Acceptance criteria:**
- `bot.py` accepts `--bot-id`, `--worker`, and `--with-web`/default dashboard flags or equivalent.
- `_kill_existing_bots()` is removed or disabled by default.
- Message handling, TTS channel commands, pronunciation preview, stats, user settings, and char usage use `bot.bot_id`.

## Task 4: Add dashboard process manager

**Objective:** Add a safe parent-side process manager to start/stop/restart worker processes.

**Files:**
- Create: `bot_process_manager.py`
- Create or modify: `dashboard.py` or `bot.py` dashboard mode
- Modify: `Dockerfile`

**Acceptance criteria:**
- Dashboard starts all enabled bots on boot.
- Adding a bot starts it immediately.
- Stop/restart routes can control bot processes.
- Tokens are never passed as command-line args and never rendered back to UI.

## Task 5: Add bot management dashboard UI and routes

**Objective:** Add “봇 관리” section with add-bot form and process controls.

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/dashboard.html`

**Acceptance criteria:**
- Admin can enter bot name and Discord bot token.
- Token is validated via Discord API `GET /users/@me` with `Authorization: Bot <token>`.
- Duplicate Discord bot user IDs are rejected.
- Bot list shows status, pid, guild count, active channel count, today requests, and controls.

## Task 6: Change dashboard hierarchy to project > overall > per-bot

**Objective:** Add project-wide aggregate metrics plus selectable per-bot stats.

**Files:**
- Modify: `database.py`
- Modify: `web/routes.py`
- Modify: `web/templates/dashboard.html`

**Acceptance criteria:**
- Overall cards aggregate all bots.
- Per-bot page/filter shows bot-specific guilds, active channels, daily requests, keyword rules.
- Server/guild detail routes include bot id: `/bots/<bot_id>/servers/<guild_id>`.

## Task 7: Bot-specific keyword replacement

**Objective:** Make “global” pronunciation rules mean “global within selected bot”, not global across all bots.

**Files:**
- Modify: `database.py`
- Modify: `web/routes.py`
- Modify: `web/templates/dashboard.html`
- Modify: `cogs/tts.py`
- Modify: `bot.py`

**Acceptance criteria:**
- `resolve_keyword_replacement(bot_id, guild_id, text)` checks bot-specific guild then bot-specific global rules.
- API/forms include `bot_id`.
- CSV import/export includes `bot_id` and/or applies to selected bot.

## Task 8: Verification and deployment

**Objective:** Verify syntax, DB tests, and container build readiness.

**Commands:**
```bash
python -m py_compile bot.py database.py web/app.py web/routes.py cogs/*.py
python -m pytest tests/ -q
```

**Deployment notes:**
- Backup `data/bot.db` before first run.
- Existing bot becomes bot id=1.
- Existing docker port 5001 remains dashboard port.
- New bot workers do not open web ports.
