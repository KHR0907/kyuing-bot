import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_followup(self, interaction: discord.Interaction, message: str, *, ephemeral: bool = False):
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)

    @app_commands.command(name="join", description="음성 채널 참가")
    async def cmd_join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await self._send_followup(interaction, "❌ 먼저 음성 채널에 접속해주세요!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ch = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        try:
            if vc is None:
                await ch.connect()
            elif vc.channel != ch:
                await vc.move_to(ch)
            log.info(
                "/join 성공 guild_id={} user_id={} channel_id={}",
                interaction.guild.id,
                interaction.user.id,
                ch.id,
            )
            await interaction.followup.send(f"✅ **{ch.name}** 참가!", ephemeral=True)
        except Exception as e:
            log.exception(
                "/join 실패 guild_id={} user_id={} channel_id={}",
                interaction.guild.id if interaction.guild else None,
                interaction.user.id,
                ch.id,
            )
            await interaction.followup.send(
                f"❌ 음성 채널 참가 중 오류가 발생했습니다: {e}",
                ephemeral=True,
            )

    @app_commands.command(name="leave", description="음성 채널 퇴장")
    async def cmd_leave(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
            log.info("/leave 성공 guild_id={} user_id={}", interaction.guild.id, interaction.user.id)
            await interaction.response.send_message("👋 퇴장!", ephemeral=True)
        else:
            await self._send_followup(interaction, "❌ 음성 채널에 없습니다.", ephemeral=True)

    @app_commands.command(name="stop", description="재생 정지")
    async def cmd_stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            log.info("/stop 성공 guild_id={} user_id={}", interaction.guild.id, interaction.user.id)
            await interaction.response.send_message("⏹️ 정지!", ephemeral=True)
        else:
            await self._send_followup(interaction, "재생 중이 아닙니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot))
