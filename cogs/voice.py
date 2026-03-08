import discord
from discord import app_commands
from discord.ext import commands


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="join", description="음성 채널 참가")
    async def cmd_join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ 먼저 음성 채널에 접속해주세요!", ephemeral=True)
            return
        ch = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc is None:
            await ch.connect()
        elif vc.channel != ch:
            await vc.move_to(ch)
        await interaction.response.send_message(f"✅ **{ch.name}** 참가!")

    @app_commands.command(name="leave", description="음성 채널 퇴장")
    async def cmd_leave(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
            await interaction.response.send_message("👋 퇴장!")
        else:
            await interaction.response.send_message("❌ 음성 채널에 없습니다.", ephemeral=True)

    @app_commands.command(name="stop", description="재생 정지")
    async def cmd_stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏹️ 정지!")
        else:
            await interaction.response.send_message("재생 중이 아닙니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot))
