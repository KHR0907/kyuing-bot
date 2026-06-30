import discord
from discord import app_commands
from discord.ext import commands

import music_runtime

HELP_CATEGORIES = {
    "음성/채널": ["join", "leave", "stop", "setchannel", "unsetchannel", "channels"],
    "TTS 설정": ["engine", "voice", "speed", "lang", "quality", "settings", "voices", "pronounce", "usage"],
    "사운드보드": ["sound", "play"],
    "음악": ["music"],
}

CATEGORY_DETAIL = {
    "음성/채널": "`/join` 음성 채널 호출 · `/leave` 퇴장 · `/stop` 재생 중지 · `/setchannel`·`/unsetchannel` TTS 채널 설정 · `/channels` 목록",
    "TTS 설정": "`/engine` 엔진 · `/voice` 음성 · `/speed` 속도 · `/lang` 언어 · `/quality` 품질 · `/settings` 현재설정 · `/voices` 음성목록 · `/pronounce` 발음미리보기 · `/usage` 사용량",
    "사운드보드": "`/sound add|remove|list` 음원 등록/삭제/목록 · `/play <키워드>` 재생",
    "음악": "`/music play|skip|stop|pause|resume|queue|nowplaying|volume|remove|clear|loop|shuffle|seek|speed`",
}


class HelpCog(commands.Cog):
    """뀨잉봇 명령어 사용법 가이드."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _visible_categories(self):
        cats = dict(HELP_CATEGORIES)
        if not music_runtime.is_music_available():
            cats.pop("음악", None)
        return cats

    @app_commands.command(name="help", description="뀨잉봇 명령어 사용법을 봅니다")
    @app_commands.describe(category="자세히 볼 카테고리 (선택)")
    async def help(self, interaction: discord.Interaction, category: str | None = None):
        cats = self._visible_categories()
        embed = discord.Embed(title="🐤 뀨잉봇 도움말", color=0x5865F2)
        if category and category in cats:
            embed.add_field(name=category, value=CATEGORY_DETAIL[category], inline=False)
        else:
            for name in cats:
                embed.add_field(name=name, value=CATEGORY_DETAIL[name], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @help.autocomplete("category")
    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        cats = self._visible_categories()
        q = current.lower()
        return [app_commands.Choice(name=c, value=c) for c in cats if not q or q in c.lower()][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
