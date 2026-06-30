import sys

import pytest

# test_music_cog.py 와 동일한 stub 스냅샷/복원 패턴: stub 으로 오염된 discord
# 모듈을 제거하고 실제 discord 로 cogs.music 을 import 한 뒤 이전 상태를 복원해
# 다른 테스트로 누수되지 않게 한다.
_PREV = {
    name: mod
    for name, mod in list(sys.modules.items())
    if name == "discord" or name.startswith("discord.") or name == "cogs.music"
}
for name in list(_PREV):
    del sys.modules[name]

import cogs.music as music  # noqa: E402  (실제 discord 로 import)

for name in list(sys.modules):
    if name == "discord" or name.startswith("discord.") or name == "cogs.music":
        del sys.modules[name]
sys.modules.update(_PREV)

pytestmark = pytest.mark.asyncio


async def test_extra_commands_registered():
    sub = {c.name for c in music.MusicCog.music_group.commands}
    for expected in ("volume", "remove", "clear", "loop", "shuffle", "seek", "speed"):
        assert expected in sub
