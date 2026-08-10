"""
Discord TTS Bot (Supertonic-3 / Google TTS)
- 지정 채널에 메시지 치면 자동으로 읽어줌
- /engine, /voice, /speed, /lang, /quality 등 슬래시 명령어 지원
- 웹 대시보드로 운영 현황 모니터링
"""


from contextlib import suppress
import argparse
import asyncio
import os
import re
import signal

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log

import config
from logging_setup import configure_logging

configure_logging()

import database
import tts_engine
from audio_scheduler import AudioCooldown, AudioQueueFull, audio_scheduler
from config import TTS_REQUIRE_VOICE_MEMBERSHIP, TTS_USER_COOLDOWN_SECONDS
from web.app import create_app
from bot_process_manager import BotProcessManager
from worker_lock import WorkerAlreadyRunning, WorkerFileLock

# ── 봇 설정 ──
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.bot_id = database.current_bot_id()

EXTENSIONS = ["cogs.tts", "cogs.channels", "cogs.voice", "cogs.sounds", "cogs.help"]


async def refresh_dashboard_snapshot() -> int:
    active_channel_count = await database.get_total_tts_channel_count(bot_id=bot.bot_id)
    await database.record_daily_snapshot(len(bot.guilds), active_channel_count, bot_id=bot.bot_id)
    await database.update_bot_runtime_status(bot.bot_id, "running", pid=os.getpid(), guild_count=len(bot.guilds))
    return active_channel_count


async def refresh_guild_snapshots() -> None:
    snapshots = []
    for guild in bot.guilds:
        voice_client = guild.voice_client
        voice_channel = voice_client.channel if voice_client and voice_client.channel else None
        snapshots.append({
            "id": guild.id,
            "name": guild.name,
            "icon_url": guild.icon.url if guild.icon else "",
            "member_count": guild.member_count or 0,
            "voice_channel_id": voice_channel.id if voice_channel else None,
            "voice_channel_name": voice_channel.name if voice_channel else None,
        })
    await database.sync_bot_guild_snapshots(bot.bot_id, snapshots)


async def guild_snapshot_loop(interval_seconds: float = 30.0):
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await refresh_guild_snapshots()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("서버 스냅샷 갱신 실패: {}", exc)


async def keyword_hits_flush_loop(interval_seconds: int = 60):
    """키워드 hit 누적분을 주기적으로 DB에 flush. 핫패스 commit 회피."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            flushed = await database.flush_keyword_hits()
            if flushed:
                log.debug("키워드 hit flush: {}개 키워드", flushed)
        except asyncio.CancelledError:
            # 종료 직전 마지막 flush 시도
            try:
                await database.flush_keyword_hits()
            except Exception as e:
                log.warning("종료 시 키워드 hit flush 실패: {}", e)
            raise
        except Exception as e:
            log.warning("키워드 hit flush 실패 (다음 주기에 재시도): {}", e)


async def config_cache_refresh_loop(interval_seconds: float = 2.0):
    """Pick up dashboard changes committed by another bot/dashboard process."""
    revision = await database.get_config_revision(bot.bot_id)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            new_revision = await database.refresh_cache_if_changed(revision, bot_id=bot.bot_id)
            if new_revision != revision:
                log.info(
                    "설정 캐시 갱신 bot_id={} revision={}→{}",
                    bot.bot_id,
                    revision,
                    new_revision,
                )
                revision = new_revision
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("설정 캐시 확인 실패 (다음 주기에 재시도): {}", exc)


async def refresh_dashboard_owner_ids():
    owner_ids = set(config.DASHBOARD_ADMIN_IDS)
    owner_ids.update(await database.get_dashboard_admin_ids())
    try:
        app_info = await bot.application_info()
        if getattr(app_info, "owner", None):
            bot.application_owner_id = app_info.owner.id
            owner_ids.add(app_info.owner.id)
    except Exception as e:
        log.warning("대시보드 소유자 조회 실패: {}", e)

    bot.dashboard_owner_ids = owner_ids
    log.info("대시보드 관리자 ID {}", sorted(owner_ids))


async def disconnect_if_voice_channel_empty(guild: discord.Guild):
    vc = guild.voice_client
    if vc is None or vc.channel is None:
        return

    human_members = [member for member in vc.channel.members if not member.bot]
    if human_members:
        return

    channel_name = vc.channel.name
    channel_id = vc.channel.id
    await vc.disconnect()
    log.info(
        "음성 채널 자동 퇴장 guild_id={} channel_id={} channel_name={}",
        guild.id,
        channel_id,
        channel_name,
    )


CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_~]+:\d+>")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")


def _replace_mentions(text: str, message: discord.Message) -> str:
    # 역할/채널 멘션을 먼저 치환 (<@&...>, <#...>가 사용자 멘션 패턴과 겹치지 않도록)
    role_names = {role.id: role.name for role in message.role_mentions}

    def role_repl(match: "re.Match[str]") -> str:
        name = role_names.get(int(match.group(1)))
        return f"{name} 역할을 언급했어요" if name else "역할을 언급했어요"

    text = ROLE_MENTION_RE.sub(role_repl, text)

    channel_names = {channel.id: channel.name for channel in message.channel_mentions}

    def channel_repl(match: "re.Match[str]") -> str:
        name = channel_names.get(int(match.group(1)))
        return f"{name} 채널을 언급했어요" if name else "채널을 언급했어요"

    text = CHANNEL_MENTION_RE.sub(channel_repl, text)

    user_names = {user.id: (user.display_name or user.name) for user in message.mentions}

    def user_repl(match: "re.Match[str]") -> str:
        name = user_names.get(int(match.group(1)))
        return f"{name}님을 언급했어요" if name else "누군가를 언급했어요"

    return USER_MENTION_RE.sub(user_repl, text)


def _describe_attachments(message: discord.Message) -> list[str]:
    descriptions: list[str] = []
    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower()
        filename = (attachment.filename or "").lower()
        if content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic")):
            descriptions.append("사진을 보냈어요")
        elif content_type.startswith("video/") or filename.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi")):
            descriptions.append("동영상을 보냈어요")
    return descriptions


def build_tts_text(message: discord.Message) -> str:
    parts: list[str] = []

    parts.extend(_describe_attachments(message))

    if message.stickers:
        parts.extend("이모티콘을 보냈어요" for _ in message.stickers)

    text = message.content.strip()
    if text:
        text = URL_RE.sub("링크를 보냈어요", text)
        text = _replace_mentions(text, message)
        stripped = CUSTOM_EMOJI_RE.sub("", text).strip()
        had_custom_emoji = stripped != text
        if stripped:
            parts.append(stripped)
        elif had_custom_emoji:
            parts.append("이모티콘을 보냈어요")

    return " ".join(parts).strip()


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    guild_channels = database.get_tts_channels_cached(message.guild.id, bot_id=bot.bot_id)
    if message.channel.id in guild_channels:
        if message.content.startswith("/"):
            return

        text = build_tts_text(message)
        if not text:
            return

        user_voice_channel = message.author.voice.channel if message.author.voice else None
        bot_voice_client = message.guild.voice_client
        bot_voice_channel = bot_voice_client.channel if bot_voice_client else None

        if bot_voice_channel is not None:
            if TTS_REQUIRE_VOICE_MEMBERSHIP and user_voice_channel != bot_voice_channel:
                return
            # Chat messages must never drag an already connected bot to another room.
            target_channel = bot_voice_channel
        else:
            target_channel = user_voice_channel
        if target_channel is None:
            await message.reply("먼저 음성 채널에 접속해주세요!")
            return

        async def run_tts_job():
            await database.increment_daily_tts_requests(bot_id=bot.bot_id)
            return await tts_engine.do_tts(
                text=text,
                voice_channel=target_channel,
                guild=message.guild,
                user_id=message.author.id,
                bot_id=bot.bot_id,
            )

        try:
            error = await audio_scheduler.run(
                guild_id=message.guild.id,
                user_id=message.author.id,
                runner=run_tts_job,
                cooldown_seconds=TTS_USER_COOLDOWN_SECONDS,
            )
        except AudioCooldown:
            return
        except AudioQueueFull:
            await message.reply("음성 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요.")
            return
        if error:
            await message.reply(error)

    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.voice_client is None:
        return

    watched_channel_id = member.guild.voice_client.channel.id if member.guild.voice_client.channel else None
    if watched_channel_id is None:
        return

    if before.channel and before.channel.id == watched_channel_id:
        await disconnect_if_voice_channel_empty(member.guild)
    elif after.channel and after.channel.id == watched_channel_id:
        await disconnect_if_voice_channel_empty(member.guild)


@bot.event
async def on_ready():
    log.info("봇 온라인: {} (ID: {})", bot.user, bot.user.id)
    await refresh_dashboard_owner_ids()
    active_channel_count = await refresh_dashboard_snapshot()
    await refresh_guild_snapshots()
    configured_guild_count = await database.get_all_tts_channel_count(bot_id=bot.bot_id)
    log.info("서버 {}개", len(bot.guilds))
    log.info("활성 TTS 채널 {}개", active_channel_count)
    log.info("TTS 활성 서버 {}개", configured_guild_count)
    try:
        synced = await bot.tree.sync()
        log.info("슬래시 커맨드 {}개 동기화", len(synced))
    except Exception as e:
        log.error("동기화 실패: {}", e)


@bot.event
async def on_guild_join(guild):
    log.info("서버 참가: {} ({})", guild.name, guild.id)
    await refresh_dashboard_snapshot()
    await refresh_guild_snapshots()


@bot.event
async def on_guild_remove(guild):
    log.info("서버 이탈: {} ({})", guild.name, guild.id)
    await refresh_dashboard_snapshot()
    await refresh_guild_snapshots()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    command_name = interaction.command.qualified_name if interaction.command else "unknown"
    log.exception(
        "슬래시 명령 실패 command={} guild_id={} user_id={}",
        command_name,
        interaction.guild.id if interaction.guild else None,
        interaction.user.id if interaction.user else None,
    )

    if interaction.response.is_done():
        await interaction.followup.send("❌ 명령 처리 중 오류가 발생했습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 명령 처리 중 오류가 발생했습니다.", ephemeral=True)


async def main(*, bot_id: int | None = None, run_web: bool = True):
    config.validate_runtime_config()
    if bot_id is not None:
        database.set_current_bot_id(bot_id)
        bot.bot_id = int(bot_id)
    await database.init_db()

    try:
        from tts_engines.supertonic_engine import prune_unused_model_caches

        prune_unused_model_caches()
    except Exception as exc:
        log.warning("supertonic 캐시 정리 중 예외 — 무시하고 계속: {}", exc)

    for ext in EXTENSIONS:
        await bot.load_extension(ext)

    web_task = None
    flush_task = None
    cache_refresh_task = None
    guild_snapshot_task = None

    try:
        async with bot:
            if run_web:
                quart_app = create_app(bot)
                # The primary bot lives in this process. The worker manager must never
                # start/stop it, otherwise the same Discord token can run twice.
                quart_app.bot_process_manager = BotProcessManager(protected_bot_ids={bot.bot_id})
                await quart_app.bot_process_manager.start_enabled_bots(exclude={bot.bot_id})
                web_task = asyncio.create_task(
                    quart_app.run_task(host="0.0.0.0", port=config.WEB_PORT),
                    name="dashboard-web-server",
                )
            flush_task = asyncio.create_task(
                keyword_hits_flush_loop(),
                name="keyword-hits-flush",
            )
            cache_refresh_task = asyncio.create_task(
                config_cache_refresh_loop(),
                name="config-cache-refresh",
            )
            guild_snapshot_task = asyncio.create_task(
                guild_snapshot_loop(),
                name="guild-snapshot-refresh",
            )
            token = await database.get_bot_token(bot.bot_id) or config.DISCORD_TOKEN
            await bot.start(token)
    finally:
        if guild_snapshot_task is not None:
            guild_snapshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await guild_snapshot_task
        if cache_refresh_task is not None:
            cache_refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await cache_refresh_task
        if flush_task is not None:
            flush_task.cancel()
            with suppress(asyncio.CancelledError):
                await flush_task
        if web_task is not None:
            web_task.cancel()
            with suppress(asyncio.CancelledError):
                await web_task
            manager = getattr(quart_app, "bot_process_manager", None)
            if manager is not None:
                await manager.stop_all()
        await database.close_db()


def _kill_existing_bots():
    """이미 실행 중인 bot.py 프로세스를 종료"""
    my_pid = os.getpid()
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "python.*bot\\.py"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().splitlines():
            pid = int(line.strip())
            if pid != my_pid:
                os.kill(pid, signal.SIGTERM)
                log.info("기존 bot.py 프로세스 종료: PID {}", pid)
    except Exception as e:
        log.warning("기존 프로세스 정리 실패: {}", e)


def parse_args():
    parser = argparse.ArgumentParser(description="KYUING Discord bot/dashboard")
    parser.add_argument("--bot-id", type=int, default=int(os.getenv("KYUING_BOT_ID", os.getenv("BOT_ID", "1"))))
    parser.add_argument("--worker", action="store_true", help="Run only the Discord bot worker without the web dashboard")
    parser.add_argument("--no-kill-existing", action="store_true", help="Do not terminate older bot.py processes")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.worker and not args.no_kill_existing:
        _kill_existing_bots()
    worker_lock = None
    try:
        if args.worker:
            worker_lock = WorkerFileLock(args.bot_id)
            worker_lock.acquire()
        asyncio.run(main(bot_id=args.bot_id, run_web=not args.worker))
    except WorkerAlreadyRunning as exc:
        log.error(str(exc))
        raise SystemExit(3) from exc
    except KeyboardInterrupt:
        log.info("종료 신호 수신")
    finally:
        if worker_lock is not None:
            worker_lock.release()
