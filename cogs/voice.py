import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log

from audio_scheduler import audio_scheduler


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_followup(self, interaction: discord.Interaction, message: str, *, ephemeral: bool = False):
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)

    @staticmethod
    def _can_control_voice(interaction: discord.Interaction, vc) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions and getattr(permissions, "manage_channels", False):
            return True
        user_voice = getattr(interaction.user, "voice", None)
        return bool(user_voice and vc and user_voice.channel == vc.channel)

    @app_commands.command(name="join", description="음성 채널 참가")
    async def cmd_join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await self._send_followup(interaction, "❌ 먼저 음성 채널에 접속해주세요!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ch = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        permissions = getattr(interaction.user, "guild_permissions", None)
        if vc is not None and vc.channel != ch and not (
            permissions and getattr(permissions, "move_members", False)
        ):
            await interaction.followup.send(
                "❌ 다른 음성 채널로 봇을 이동하려면 멤버 이동 권한이 필요합니다.", ephemeral=True
            )
            return
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
            if not self._can_control_voice(interaction, vc):
                await self._send_followup(
                    interaction, "❌ 봇과 같은 음성 채널에 있거나 채널 관리 권한이 필요합니다.", ephemeral=True
                )
                return
            audio_scheduler.clear_guild(interaction.guild.id)
            await vc.disconnect()
            log.info("/leave 성공 guild_id={} user_id={}", interaction.guild.id, interaction.user.id)
            await interaction.response.send_message("👋 퇴장!", ephemeral=True)
        else:
            await self._send_followup(interaction, "❌ 음성 채널에 없습니다.", ephemeral=True)

    @app_commands.command(name="stop", description="재생 정지")
    async def cmd_stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            if not self._can_control_voice(interaction, vc):
                await self._send_followup(
                    interaction, "❌ 봇과 같은 음성 채널에 있거나 채널 관리 권한이 필요합니다.", ephemeral=True
                )
                return
            cleared = audio_scheduler.clear_guild(interaction.guild.id)
            vc.stop()
            log.info("/stop 성공 guild_id={} user_id={} cleared={}", interaction.guild.id, interaction.user.id, cleared)
            suffix = f" 대기 작업 {cleared}개도 취소했습니다." if cleared else ""
            await interaction.response.send_message(f"⏹️ 정지!{suffix}", ephemeral=True)
        else:
            await self._send_followup(interaction, "재생 중이 아닙니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot))
