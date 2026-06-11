import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log

import database
import sound_storage
import tts_engine
from config import SOUND_MAX_KEYWORD_LENGTH, SOUND_MAX_PER_GUILD


class SoundCog(commands.Cog):
    """사운드보드: 키워드로 짧은 음원(8초 이하)을 등록하고 /play로 재생."""

    sound_group = app_commands.Group(name="sound", description="사운드보드 음원 관리")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _keyword_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        sounds = await database.get_sounds_for_guild(interaction.guild.id, bot_id=self.bot.bot_id)
        query = current.lower()
        return [
            app_commands.Choice(name=f"{s['keyword']} ({s['duration_seconds']:.1f}초)", value=s["keyword"])
            for s in sounds
            if not query or query in s["keyword"].lower()
        ][:25]

    @sound_group.command(name="add", description="키워드에 음원을 등록합니다 (8초 이하)")
    @app_commands.describe(keyword="재생에 사용할 키워드", file="8초 이하의 오디오/비디오 파일 (mp4, mp3 등)")
    async def cmd_sound_add(self, interaction: discord.Interaction, keyword: str, file: discord.Attachment):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        keyword = keyword.strip()
        if not keyword or len(keyword) > SOUND_MAX_KEYWORD_LENGTH:
            await interaction.response.send_message(
                f"❌ 키워드는 1~{SOUND_MAX_KEYWORD_LENGTH}자여야 합니다.", ephemeral=True,
            )
            return
        if await database.get_guild_sound_count(interaction.guild.id, bot_id=self.bot.bot_id) >= SOUND_MAX_PER_GUILD:
            await interaction.response.send_message(
                f"❌ 서버당 음원은 최대 {SOUND_MAX_PER_GUILD}개까지 등록할 수 있습니다.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
            filename, duration = await sound_storage.save_sound_file(data, bot_id=self.bot.bot_id)
        except sound_storage.SoundValidationError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        try:
            created = await database.add_sound(
                "guild", keyword, filename, duration,
                guild_id=interaction.guild.id,
                original_filename=file.filename,
                created_by=interaction.user.id,
                bot_id=self.bot.bot_id,
            )
        except Exception:
            sound_storage.delete_sound_file(filename, bot_id=self.bot.bot_id)
            raise
        if created is None:
            sound_storage.delete_sound_file(filename, bot_id=self.bot.bot_id)
            await interaction.followup.send(f"❌ 이미 등록된 키워드입니다: `{keyword}`", ephemeral=True)
            return
        log.info(
            "사운드 등록 guild_id={} keyword={} duration={:.1f}s user_id={}",
            interaction.guild.id, keyword, duration, interaction.user.id,
        )
        await interaction.followup.send(
            f"✅ 음원 등록 완료: `{keyword}` ({duration:.1f}초) — `/play {keyword}` 로 재생", ephemeral=True,
        )

    @sound_group.command(name="remove", description="이 서버에 등록된 음원을 삭제합니다")
    @app_commands.describe(keyword="삭제할 키워드")
    async def cmd_sound_remove(self, interaction: discord.Interaction, keyword: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        removed = await database.remove_sound(
            "guild", keyword.strip(), guild_id=interaction.guild.id, bot_id=self.bot.bot_id,
        )
        if removed is None:
            await interaction.followup.send(
                f"❌ 이 서버에 등록된 키워드가 아닙니다: `{keyword}`", ephemeral=True,
            )
            return
        sound_storage.delete_sound_file(removed["filename"], bot_id=removed["bot_id"])
        log.info("사운드 삭제 guild_id={} keyword={} user_id={}", interaction.guild.id, keyword, interaction.user.id)
        await interaction.followup.send(f"🗑️ 음원 삭제 완료: `{keyword}`", ephemeral=True)

    @cmd_sound_remove.autocomplete("keyword")
    async def remove_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        sounds = await database.get_guild_sounds(interaction.guild.id, bot_id=self.bot.bot_id)
        query = current.lower()
        return [
            app_commands.Choice(name=s["keyword"], value=s["keyword"])
            for s in sounds
            if not query or query in s["keyword"].lower()
        ][:25]

    @sound_group.command(name="list", description="사용 가능한 음원 목록을 봅니다")
    async def cmd_sound_list(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        sounds = await database.get_sounds_for_guild(interaction.guild.id, bot_id=self.bot.bot_id)
        if not sounds:
            await interaction.response.send_message(
                "등록된 음원이 없습니다. `/sound add` 로 추가해보세요!", ephemeral=True,
            )
            return

        embed = discord.Embed(title="🔊 사운드보드", color=0x5865F2)
        guild_lines = [
            f"`{s['keyword']}` ({s['duration_seconds']:.1f}초, {s['play_count']}회)"
            for s in sounds if s["scope"] == "guild"
        ]
        global_lines = [
            f"`{s['keyword']}` ({s['duration_seconds']:.1f}초, {s['play_count']}회)"
            for s in sounds if s["scope"] == "global"
        ]
        if guild_lines:
            embed.add_field(name=f"이 서버 ({len(guild_lines)}개)", value="\n".join(guild_lines)[:1024], inline=False)
        if global_lines:
            embed.add_field(name=f"전역 ({len(global_lines)}개)", value="\n".join(global_lines)[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="play", description="등록된 음원을 재생합니다")
    @app_commands.describe(keyword="재생할 음원 키워드")
    async def cmd_play(self, interaction: discord.Interaction, keyword: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        keyword = keyword.strip()
        sound = await database.resolve_sound(keyword, guild_id=interaction.guild.id, bot_id=self.bot.bot_id)
        if sound is None:
            await interaction.response.send_message(f"❌ 등록되지 않은 키워드입니다: `{keyword}`", ephemeral=True)
            return

        user_voice = interaction.user.voice.channel if getattr(interaction.user, "voice", None) else None
        bot_voice_client = interaction.guild.voice_client
        target_channel = user_voice or (bot_voice_client.channel if bot_voice_client else None)
        if target_channel is None:
            await interaction.response.send_message("❌ 먼저 음성 채널에 접속해주세요!", ephemeral=True)
            return

        path = sound_storage.sound_path(sound["filename"], bot_id=sound["bot_id"])
        if not path.exists():
            await database.remove_sound_by_id(sound["id"])
            await interaction.response.send_message(
                "❌ 음원 파일이 유실되어 등록을 정리했습니다. 다시 등록해주세요.", ephemeral=True,
            )
            return

        await interaction.response.defer()
        error = await tts_engine.play_sound(str(path), target_channel, interaction.guild)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return
        try:
            await database.increment_sound_play_count(sound["id"])
        except Exception as e:
            log.warning("재생 카운트 갱신 실패 sound_id={}: {}", sound["id"], e)
        log.info("사운드 재생 guild_id={} keyword={} user_id={}", interaction.guild.id, keyword, interaction.user.id)
        await interaction.followup.send(f"🔊 `{keyword}` 재생 완료")

    @cmd_play.autocomplete("keyword")
    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._keyword_autocomplete(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundCog(bot))
