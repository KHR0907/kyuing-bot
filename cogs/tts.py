import discord
from discord import app_commands
from discord.ext import commands

import database
from config import VOICES, LANGUAGES


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="voice", description="기본 보이스를 변경합니다")
    @app_commands.choices(
        voice=[app_commands.Choice(name=f"{c} ({l})", value=c) for c, l in VOICES.items()]
    )
    async def cmd_voice(self, interaction: discord.Interaction, voice: str):
        await database.set_user_setting(interaction.user.id, voice=voice)
        await interaction.response.send_message(
            f"✅ 보이스 → **{voice} ({VOICES[voice]})**", ephemeral=True
        )

    @app_commands.command(name="speed", description="기본 속도를 변경합니다 (0.5~2.0)")
    async def cmd_speed(self, interaction: discord.Interaction, speed: float):
        if not 0.5 <= speed <= 2.0:
            await interaction.response.send_message("❌ 0.5 ~ 2.0 범위로 입력하세요.", ephemeral=True)
            return
        await database.set_user_setting(interaction.user.id, speed=speed)
        await interaction.response.send_message(f"✅ 속도 → **{speed}x**", ephemeral=True)

    @app_commands.command(name="lang", description="기본 언어를 변경합니다")
    @app_commands.choices(
        lang=[app_commands.Choice(name=f"{l} ({c})", value=c) for c, l in LANGUAGES.items()]
    )
    async def cmd_lang(self, interaction: discord.Interaction, lang: str):
        await database.set_user_setting(interaction.user.id, lang=lang)
        await interaction.response.send_message(
            f"✅ 언어 → **{LANGUAGES[lang]}**", ephemeral=True
        )

    @app_commands.command(name="quality", description="품질(추론 스텝)을 변경합니다")
    @app_commands.choices(
        steps=[
            app_commands.Choice(name="2 스텝 (빠름)", value=2),
            app_commands.Choice(name="5 스텝 (고품질)", value=5),
            app_commands.Choice(name="10 스텝 (최고품질)", value=10),
        ]
    )
    async def cmd_quality(self, interaction: discord.Interaction, steps: int):
        await database.set_user_setting(interaction.user.id, total_steps=steps)
        await interaction.response.send_message(f"✅ 품질 → **{steps} 스텝**", ephemeral=True)

    @app_commands.command(name="settings", description="현재 TTS 설정 확인")
    async def cmd_settings(self, interaction: discord.Interaction):
        s = await database.get_user_settings(interaction.user.id)
        embed = discord.Embed(title="🎤 내 TTS 설정", color=0x5865F2)
        embed.add_field(name="보이스", value=f"{s['voice']} ({VOICES.get(s['voice'], '?')})", inline=True)
        embed.add_field(name="속도", value=f"{s['speed']}x", inline=True)
        embed.add_field(name="언어", value=LANGUAGES.get(s["lang"], s["lang"]), inline=True)
        embed.add_field(name="품질", value=f"{s['total_steps']} 스텝", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="voices", description="보이스 목록")
    async def cmd_voices(self, interaction: discord.Interaction):
        male = "\n".join(f"  `{k}` {v}" for k, v in VOICES.items() if k.startswith("M"))
        female = "\n".join(f"  `{k}` {v}" for k, v in VOICES.items() if k.startswith("F"))
        embed = discord.Embed(title="🎙️ 보이스 목록", color=0x5865F2)
        embed.add_field(name="👨 남성", value=male, inline=True)
        embed.add_field(name="👩 여성", value=female, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTSCog(bot))
