import discord
from discord import app_commands
from discord.ext import commands

HELP_CATEGORIES = {
    "음성/채널": ["join", "leave", "stop", "setchannel", "unsetchannel", "channels"],
    "TTS 설정": ["engine", "voice", "speed", "lang", "quality", "settings", "voices", "pronounce", "usage"],
    "사운드보드": ["sound", "play"],
}

CATEGORY_DETAIL = {
    "음성/채널": "`/join` 음성 채널 호출 · `/leave` 퇴장 · `/stop` 재생 중지 · `/setchannel`·`/unsetchannel` TTS 채널 설정 · `/channels` 목록",
    "TTS 설정": "`/engine` 엔진 · `/voice` 음성 · `/speed` 속도 · `/lang` 언어 · `/quality` 품질 · `/settings` 현재설정 · `/voices` 음성목록 · `/pronounce` 발음미리보기 · `/usage` 사용량",
    "사운드보드": "`/sound add|remove|list` 음원 등록/삭제/목록 · `/play <키워드>` 재생",
}


class HelpCog(commands.Cog):
    """뀨잉봇 명령어 사용법 가이드."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="뀨잉봇 명령어 사용법을 봅니다")
    @app_commands.describe(category="자세히 볼 카테고리 (선택)")
    async def help(self, interaction: discord.Interaction, category: str | None = None):
        embed = discord.Embed(title="🐤 뀨잉봇 도움말", color=0x5865F2)
        if category and category in HELP_CATEGORIES:
            embed.add_field(name=category, value=CATEGORY_DETAIL[category], inline=False)
        else:
            for name in HELP_CATEGORIES:
                embed.add_field(name=name, value=CATEGORY_DETAIL[name], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @help.autocomplete("category")
    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        q = current.lower()
        return [
            app_commands.Choice(name=c, value=c)
            for c in HELP_CATEGORIES
            if not q or q in c.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
