import discord
from discord import app_commands
from discord.ext import commands

import database


class ChannelsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _refresh_dashboard_snapshot(self):
        active_channel_count = await database.get_total_tts_channel_count()
        await database.record_daily_snapshot(len(self.bot.guilds), active_channel_count)

    @app_commands.command(name="setchannel", description="현재 채널을 TTS 채널로 설정합니다")
    @app_commands.default_permissions(manage_channels=True)
    async def cmd_setchannel(self, interaction: discord.Interaction):
        added = await database.add_tts_channel(interaction.guild.id, interaction.channel.id)
        if not added:
            await interaction.response.send_message("이미 TTS 채널로 설정되어 있습니다.", ephemeral=True)
            return
        await self._refresh_dashboard_snapshot()
        await interaction.response.send_message(
            f"✅ <#{interaction.channel.id}> 을 TTS 채널로 설정했습니다."
        )

    @app_commands.command(name="unsetchannel", description="현재 채널의 TTS 설정을 해제합니다")
    @app_commands.default_permissions(manage_channels=True)
    async def cmd_unsetchannel(self, interaction: discord.Interaction):
        removed = await database.remove_tts_channel(interaction.guild.id, interaction.channel.id)
        if not removed:
            await interaction.response.send_message("TTS 채널이 아닙니다.", ephemeral=True)
            return
        await self._refresh_dashboard_snapshot()
        await interaction.response.send_message(
            f"✅ <#{interaction.channel.id}> TTS 채널 해제했습니다."
        )

    @app_commands.command(name="channels", description="TTS 채널 목록을 확인합니다")
    async def cmd_channels(self, interaction: discord.Interaction):
        channels = await database.get_tts_channels(interaction.guild.id)
        if not channels:
            await interaction.response.send_message("설정된 TTS 채널이 없습니다.", ephemeral=True)
            return
        ch_list = "\n".join(f"- <#{cid}>" for cid in channels)
        await interaction.response.send_message(f"**TTS 채널 목록**\n{ch_list}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelsCog(bot))
