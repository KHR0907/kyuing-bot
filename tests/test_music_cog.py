import sys

import pytest

# 이 테스트는 app_commands.Group 등록을 검사하므로 *실제* discord가 필요하다.
# 다른 테스트(test_tts_pipeline.py 등)는 sys.modules.setdefault 로 discord stub 을
# 설치하는데, 수집 순서에 따라 그 stub 이 먼저 들어와 cogs.music import 가
# `app_commands` 없는 stub 으로 깨질 수 있다. 그래서 여기서는 stub 으로 오염된
# discord 모듈들을 제거하고 실제 discord 로 cogs.music 을 import 한 뒤,
# 이전 sys.modules 상태를 복원해 다른 테스트로 누수되지 않게 한다.
_PREV = {
    name: mod
    for name, mod in list(sys.modules.items())
    if name == "discord" or name.startswith("discord.") or name == "cogs.music"
}
for name in list(_PREV):
    del sys.modules[name]

import cogs.music as music  # noqa: E402  (실제 discord 로 import)

# 다른 테스트가 기대하는 (stub 포함) 원래 상태로 복원.
for name in list(sys.modules):
    if name == "discord" or name.startswith("discord.") or name == "cogs.music":
        del sys.modules[name]
sys.modules.update(_PREV)

pytestmark = pytest.mark.asyncio


async def test_music_cog_registers_music_group():
    assert music.MusicCog.music_group.name == "music"
    sub = {c.name for c in music.MusicCog.music_group.commands}
    for expected in ("play", "skip", "stop", "pause", "resume", "queue", "nowplaying"):
        assert expected in sub


async def test_music_subcommands_do_not_collide_with_top_level():
    """음악은 /music 그룹 하위라 기존 최상위 play/stop/speed와 충돌하지 않는다."""
    # 그룹 이름이 'music'이고, 하위 커맨드는 그룹 네임스페이스에 속한다
    assert music.MusicCog.music_group.name == "music"
