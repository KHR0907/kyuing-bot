"""
Discord TTS Bot (Supertonic-2 / Google TTS)
- 지정 채널에 메시지 치면 자동으로 읽어줌
- /engine, /voice, /speed, /lang, /quality 등 슬래시 명령어 지원
- 웹 대시보드로 운영 현황 모니터링
"""


from contextlib import suppress
import asyncio
import os
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
from web.app import create_app

# ── 봇 설정 ──
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

EXTENSIONS = ["cogs.tts", "cogs.channels", "cogs.voice"]


async def refresh_dashboard_snapshot() -> int:
    active_channel_count = await database.get_total_tts_channel_count()
    await database.record_daily_snapshot(len(bot.guilds), active_channel_count)
    return active_channel_count


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


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    guild_channels = database.get_tts_channels_cached(message.guild.id)
    if message.channel.id in guild_channels:
        text = message.content.strip()
        if not text or text.startswith("/"):
            return

        replaced_text, replacement_scope = database.resolve_keyword_replacement(message.guild.id, text)
        if replacement_scope:
            log.info(
                "TTS 키워드 치환 scope={} guild_id={} channel_id={} user_id={} keyword={} replacement={}",
                replacement_scope,
                message.guild.id,
                message.channel.id,
                message.author.id,
                text,
                replaced_text,
            )
            database.record_keyword_hit(
                replacement_scope,
                text,
                message.guild.id if replacement_scope == "guild" else None,
            )
            text = replaced_text

        if not message.author.voice or not message.author.voice.channel:
            await message.reply("먼저 음성 채널에 접속해주세요!")
            return

        await database.increment_daily_tts_requests()
        error = await tts_engine.do_tts(
            text=text[:500],
            voice_channel=message.author.voice.channel,
            guild=message.guild,
            user_id=message.author.id,
        )
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
    configured_guild_count = await database.get_all_tts_channel_count()
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


@bot.event
async def on_guild_remove(guild):
    log.info("서버 이탈: {} ({})", guild.name, guild.id)
    await refresh_dashboard_snapshot()


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


async def main():
    await database.init_db()

    for ext in EXTENSIONS:
        await bot.load_extension(ext)

    quart_app = create_app(bot)
    web_task = None
    flush_task = None

    try:
        async with bot:
            web_task = asyncio.create_task(
                quart_app.run_task(host="0.0.0.0", port=config.WEB_PORT),
                name="dashboard-web-server",
            )
            flush_task = asyncio.create_task(
                keyword_hits_flush_loop(),
                name="keyword-hits-flush",
            )
            await bot.start(config.DISCORD_TOKEN)
    finally:
        if flush_task is not None:
            flush_task.cancel()
            with suppress(asyncio.CancelledError):
                await flush_task
        if web_task is not None:
            web_task.cancel()
            with suppress(asyncio.CancelledError):
                await web_task
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


if __name__ == "__main__":
    _kill_existing_bots()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("종료 신호 수신")
