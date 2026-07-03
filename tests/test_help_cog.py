import sys

import pytest

# test_sounds_cog.py 와 동일한 stub 스냅샷/복원 패턴: stub 으로 오염된 discord
# 모듈을 제거하고 실제 discord 로 cogs.help 을 import 한 뒤 이전 상태를 복원해
# 다른 테스트로 누수되지 않게 한다.
_PREV = {
    name: mod
    for name, mod in list(sys.modules.items())
    if name == "discord" or name.startswith("discord.") or name == "cogs.help"
}
for name in list(_PREV):
    del sys.modules[name]

import cogs.help as helpmod  # noqa: E402  (실제 discord 로 import)

for name in list(sys.modules):
    if name == "discord" or name.startswith("discord.") or name == "cogs.help":
        del sys.modules[name]
sys.modules.update(_PREV)

pytestmark = pytest.mark.asyncio


async def test_help_categories_cover_known_commands():
    cats = helpmod.HELP_CATEGORIES
    flat = {name for cmds in cats.values() for name in cmds}
    for expected in ("join", "engine", "sound", "play"):
        assert expected in flat


async def test_help_cog_has_help_command():
    cog = helpmod.HelpCog(bot=None)
    names = {c.name for c in cog.walk_app_commands()}
    assert "help" in names
