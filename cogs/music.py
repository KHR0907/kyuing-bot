import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as log

import music_runtime

try:
    import wavelink
except ImportError:
    wavelink = None

MUSIC_UNAVAILABLE_MSG = "🎵 음악 기능을 일시적으로 사용할 수 없어요."


class MusicCog(commands.Cog):
    """Lavalink 기반 YouTube 음악 재생. 모든 명령은 /music 그룹 하위."""

    music_group = app_commands.Group(name="music", description="음악 재생")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- 공통 가드 ---
    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("❌ 서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not music_runtime.is_music_available():
            await interaction.response.send_message(MUSIC_UNAVAILABLE_MSG, ephemeral=True)
            return False
        return True

    def _player(self, interaction) -> "wavelink.Player | None":
        vc = interaction.guild.voice_client
        if wavelink and isinstance(vc, wavelink.Player):
            return vc
        return None

    @music_group.command(name="play", description="YouTube 곡을 검색해 재생/큐 추가합니다")
    @app_commands.describe(query="검색어 또는 YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if not await self._guard(interaction):
            return
        user_voice = interaction.user.voice.channel if getattr(interaction.user, "voice", None) else None
        player = self._player(interaction)
        if player is None:
            if user_voice is None:
                await interaction.response.send_message("❌ 먼저 음성 채널에 들어와주세요!", ephemeral=True)
                return
            player = await user_voice.connect(cls=wavelink.Player)
            player.autoplay = wavelink.AutoPlayMode.partial
        if not hasattr(player, "home"):
            player.home = interaction.channel

        await interaction.response.defer()
        tracks = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.followup.send("❌ 검색 결과가 없어요.")
            return
        if isinstance(tracks, wavelink.Playlist):
            added = await player.queue.put_wait(tracks)
            msg = f"➕ 재생목록 **{tracks.name}** ({added}곡) 추가"
        else:
            track = tracks[0]
            await player.queue.put_wait(track)
            msg = f"➕ **{track.title}** 추가"
        if not player.playing:
            await player.play(player.queue.get(), volume=30)
        await interaction.followup.send(msg)

    @music_group.command(name="skip", description="현재 곡을 건너뜁니다")
    async def skip(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or not player.playing:
            await interaction.response.send_message("❌ 재생 중인 곡이 없어요.", ephemeral=True)
            return
        await player.skip(force=True)
        await interaction.response.send_message("⏭️ 스킵")

    @music_group.command(name="stop", description="재생을 멈추고 음성 채널에서 나갑니다")
    async def stop(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        player.queue.clear()
        await player.disconnect()
        await interaction.response.send_message("⏹️ 정지 및 퇴장")

    @music_group.command(name="pause", description="일시정지")
    async def pause(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or not player.playing:
            await interaction.response.send_message("❌ 재생 중인 곡이 없어요.", ephemeral=True)
            return
        await player.pause(True)
        await interaction.response.send_message("⏸️ 일시정지")

    @music_group.command(name="resume", description="재개")
    async def resume(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        await player.pause(False)
        await interaction.response.send_message("▶️ 재개")

    @music_group.command(name="queue", description="큐 목록을 봅니다")
    async def queue(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        lines = []
        if player.current:
            lines.append(f"▶️ **{player.current.title}**")
        for i, t in enumerate(list(player.queue)[:20], start=1):
            lines.append(f"{i}. {t.title}")
        body = "\n".join(lines) or "큐가 비어 있어요."
        embed = discord.Embed(title="🎵 큐", description=body[:4000], color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @music_group.command(name="nowplaying", description="현재 재생 중인 곡 정보")
    async def nowplaying(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or not player.current:
            await interaction.response.send_message("❌ 재생 중인 곡이 없어요.", ephemeral=True)
            return
        t = player.current
        embed = discord.Embed(title="▶️ Now Playing", description=f"**{t.title}**\n`{t.author}`", color=0x5865F2)
        if t.artwork:
            embed.set_thumbnail(url=t.artwork)
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload):
        player = payload.player
        if not player or not getattr(player, "home", None):
            return
        t = payload.track
        embed = discord.Embed(title="▶️ Now Playing", description=f"**{t.title}**\n`{t.author}`", color=0x5865F2)
        if t.artwork:
            embed.set_image(url=t.artwork)
        try:
            await player.home.send(embed=embed)
        except Exception as e:
            log.debug("now playing 전송 실패: {}", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
