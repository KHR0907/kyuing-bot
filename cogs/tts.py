import discord
from discord import app_commands
from discord.ext import commands

import database
from config import (
    TTS_ENGINES, SUPERTONIC_VOICES, GOOGLE_VOICES,
    LANGUAGES, GOOGLE_TTS_FREE_LIMIT,
)
from tts_engines import get_engine


def _voices_for_engine(engine_name: str) -> dict[str, str]:
    return get_engine(engine_name).get_voices()


class TTSCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="engine", description="TTS 엔진을 변경합니다")
    @app_commands.choices(
        engine=[app_commands.Choice(name=v, value=k) for k, v in TTS_ENGINES.items()]
    )
    async def cmd_engine(self, interaction: discord.Interaction, engine: str):
        voices = _voices_for_engine(engine)
        default_voice = next(iter(voices))
        await database.set_user_setting(
            interaction.user.id, engine=engine, voice=default_voice,
        )
        await interaction.response.send_message(
            f"✅ 엔진 → **{TTS_ENGINES[engine]}**\n"
            f"보이스가 **{default_voice} ({voices[default_voice]})**(으)로 초기화되었습니다.",
            ephemeral=True,
        )

    @app_commands.command(name="voice", description="기본 보이스를 변경합니다")
    async def cmd_voice(self, interaction: discord.Interaction, voice: str):
        settings = await database.get_user_settings(interaction.user.id)
        engine_name = settings["engine"]
        voices = _voices_for_engine(engine_name)

        if voice not in voices:
            available = ", ".join(f"`{k}`" for k in voices)
            await interaction.response.send_message(
                f"❌ 현재 엔진(**{TTS_ENGINES[engine_name]}**)에서 사용할 수 없는 보이스입니다.\n"
                f"사용 가능: {available}",
                ephemeral=True,
            )
            return

        await database.set_user_setting(interaction.user.id, voice=voice)
        await interaction.response.send_message(
            f"✅ 보이스 → **{voice} ({voices[voice]})**", ephemeral=True,
        )

    @cmd_voice.autocomplete("voice")
    async def voice_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        settings = await database.get_user_settings(interaction.user.id)
        voices = _voices_for_engine(settings["engine"])
        return [
            app_commands.Choice(name=f"{k} ({v})", value=k)
            for k, v in voices.items()
            if current.lower() in k.lower() or current.lower() in v.lower()
        ][:25]

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
            f"✅ 언어 → **{LANGUAGES[lang]}**", ephemeral=True,
        )

    @app_commands.command(name="quality", description="품질(추론 스텝)을 변경합니다 (Supertonic 전용)")
    @app_commands.choices(
        steps=[
            app_commands.Choice(name="2 스텝 (빠름)", value=2),
            app_commands.Choice(name="5 스텝 (고품질)", value=5),
            app_commands.Choice(name="10 스텝 (최고품질)", value=10),
        ]
    )
    async def cmd_quality(self, interaction: discord.Interaction, steps: int):
        settings = await database.get_user_settings(interaction.user.id)
        if settings["engine"] != "supertonic":
            await interaction.response.send_message(
                "❌ 품질 설정은 **Supertonic-2** 엔진에서만 사용할 수 있습니다.", ephemeral=True,
            )
            return
        await database.set_user_setting(interaction.user.id, total_steps=steps)
        await interaction.response.send_message(f"✅ 품질 → **{steps} 스텝**", ephemeral=True)

    @app_commands.command(name="settings", description="현재 TTS 설정 확인")
    async def cmd_settings(self, interaction: discord.Interaction):
        s = await database.get_user_settings(interaction.user.id)
        engine_name = s["engine"]
        voices = _voices_for_engine(engine_name)

        embed = discord.Embed(title="🎤 내 TTS 설정", color=0x5865F2)
        embed.add_field(
            name="엔진", value=TTS_ENGINES.get(engine_name, engine_name), inline=True,
        )
        embed.add_field(
            name="보이스", value=f"{s['voice']} ({voices.get(s['voice'], '?')})", inline=True,
        )
        embed.add_field(name="속도", value=f"{s['speed']}x", inline=True)

        if engine_name == "supertonic":
            embed.add_field(
                name="언어", value=LANGUAGES.get(s["lang"], s["lang"]), inline=True,
            )
            embed.add_field(name="품질", value=f"{s['total_steps']} 스텝", inline=True)

        if engine_name == "google":
            usage = await database.get_tts_char_usage()
            used = usage.get("standard", 0)
            pct = used / GOOGLE_TTS_FREE_LIMIT * 100
            embed.add_field(
                name="이번 달 사용량",
                value=f"{used:,} / {GOOGLE_TTS_FREE_LIMIT:,}자 ({pct:.1f}%)",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="voices", description="보이스 목록")
    async def cmd_voices(self, interaction: discord.Interaction):
        settings = await database.get_user_settings(interaction.user.id)
        engine_name = settings["engine"]
        voices = _voices_for_engine(engine_name)

        embed = discord.Embed(
            title=f"🎙️ 보이스 목록 ({TTS_ENGINES[engine_name]})", color=0x5865F2,
        )

        if engine_name == "supertonic":
            male = "\n".join(f"  `{k}` {v}" for k, v in voices.items() if k.startswith("M"))
            female = "\n".join(f"  `{k}` {v}" for k, v in voices.items() if k.startswith("F"))
            embed.add_field(name="👨 남성", value=male, inline=True)
            embed.add_field(name="👩 여성", value=female, inline=True)
        else:
            lines = "\n".join(f"  `{k}` {v}" for k, v in voices.items())
            embed.add_field(name="보이스", value=lines, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="pronounce", description="이 텍스트가 어떻게 읽힐지 확인합니다")
    @app_commands.describe(text="확인할 메시지")
    async def cmd_pronounce(self, interaction: discord.Interaction, text: str):
        guild_id = interaction.guild.id if interaction.guild else 0
        resolved, scope = database.resolve_keyword_replacement(guild_id, text.strip())

        embed = discord.Embed(title="🔊 발음 미리보기", color=0x6c5ce7)
        embed.add_field(name="입력", value=f"```{text[:200]}```", inline=False)
        if scope:
            embed.add_field(
                name=f"치환됨 ({'서버 규칙' if scope == 'guild' else '전역 규칙'})",
                value=f"```{resolved[:200]}```",
                inline=False,
            )
        else:
            embed.add_field(
                name="치환 없음",
                value="이 메시지에 매칭되는 발음 규칙이 없습니다. 입력 그대로 읽힙니다.",
                inline=False,
            )
        embed.set_footer(text="규칙 추가는 대시보드에서 가능합니다")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="usage", description="Google TTS 이번 달 사용량 확인")
    async def cmd_usage(self, interaction: discord.Interaction):
        usage = await database.get_tts_char_usage()
        used = usage.get("standard", 0)
        remaining = max(0, GOOGLE_TTS_FREE_LIMIT - used)
        pct = used / GOOGLE_TTS_FREE_LIMIT * 100
        embed = discord.Embed(title="📊 Google TTS 사용량 (이번 달)", color=0x5865F2)
        embed.add_field(name="사용", value=f"**{used:,}**자", inline=True)
        embed.add_field(name="잔여", value=f"**{remaining:,}**자", inline=True)
        embed.add_field(name="사용률", value=f"**{pct:.1f}%**", inline=True)
        embed.set_footer(text=f"무료 한도: 월 {GOOGLE_TTS_FREE_LIMIT:,}자 (Standard)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTSCog(bot))
