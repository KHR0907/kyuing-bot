# Lavalink Music Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 뀨잉봇에 Lavalink + Wavelink 기반 YouTube 음악 재생 기능을 추가하고, TTS가 끼어들면 음악을 연결 핸드오프로 일시정지·재개하는 인터럽트 덕킹을 구현한다.

**Architecture:** Lavalink를 docker-compose 서비스로 추가하고 각 봇 프로세스가 Wavelink Pool로 같은 노드에 연결한다. 음악은 `cogs/music.py`의 `/music` 슬래시 그룹으로 제공하고, TTS/사운드(`tts_engine.py`)는 음악 재생 중에만 `music_handoff.py`를 거쳐 연결을 잠시 가로채 재생 후 음악을 복구한다. 음악 기능은 `MUSIC_ENABLED`와 노드 연결 가용성에 따라 graceful하게 비활성된다.

**Tech Stack:** Python 3.11+, discord.py, wavelink>=3 (Lavalink v4), Lavalink 4-alpine + youtube-source 플러그인, Docker Compose, pytest / pytest-asyncio.

## Global Constraints

- loguru 사용: `from loguru import logger as log` (CLAUDE.md).
- 데이터 저장은 SQLite(`database.py`)만 — JSON/메모리 딕셔너리 금지. (음악 큐는 Wavelink 메모리 상태이며 영속 저장 대상이 아님 — DB 저장하지 않음.)
- 새 슬래시 명령어는 `cogs/`에 Cog 패턴으로 추가.
- `.env`에 환경변수 추가 시 `.env.example`도 placeholder로 함께 갱신 (CLAUDE.md).
- 핸드오프 중 곡 위치/트랙은 지역 변수로만 보관하고 사용 후 휘발 — DB·인스턴스 속성 저장 금지.
- 재개 위치 = `max(0, position_ms - 200)` (0.2초 에어백, 0 미만 clamp).
- 빈 채널 자동 퇴장은 기존 `disconnect_if_voice_channel_empty` 즉시 퇴장 로직 재사용 — 신규 타이머 금지.
- 멀티봇: 모든 상태는 `bot.bot_id` 스코프. 워커 종료 시 자기 Player만 정리.
- 음악을 한 번도 안 튼 길드의 TTS/사운드 코드 경로는 기존과 동일해야 함 (영향 0).
- Lavalink 기본값: host `lavalink`, port `2333`, password `youshallnotpass`, `_JAVA_OPTIONS=-Xmx512M`.

---

### Task 1: 인프라 — config, requirements, compose, application.yml

봇이 Wavelink로 Lavalink에 붙기 위한 환경 설정과 컨테이너를 마련한다. 코드 연결(Task 2)에 앞서 설정값과 인프라만 독립적으로 완성·검증한다.

**Files:**
- Modify: `config.py` (끝에 Lavalink/음악 설정 추가)
- Modify: `.env.example`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Create: `application.yml`
- Test: `tests/test_music_config.py`

**Interfaces:**
- Produces:
  - `config.LAVALINK_HOST: str` (기본 `"lavalink"`)
  - `config.LAVALINK_PORT: int` (기본 `2333`)
  - `config.LAVALINK_PASSWORD: str` (기본 `"youshallnotpass"`)
  - `config.LAVALINK_URI: str` = `f"http://{LAVALINK_HOST}:{LAVALINK_PORT}"`
  - `config.MUSIC_ENABLED: bool` (기본 `True`)

- [ ] **Step 1: Write the failing test**

`tests/test_music_config.py`:

```python
import importlib
import os


def _reload_config():
    import config
    return importlib.reload(config)


def test_lavalink_defaults(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    for key in ("LAVALINK_HOST", "LAVALINK_PORT", "LAVALINK_PASSWORD", "MUSIC_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    config = _reload_config()
    assert config.LAVALINK_HOST == "lavalink"
    assert config.LAVALINK_PORT == 2333
    assert config.LAVALINK_PASSWORD == "youshallnotpass"
    assert config.LAVALINK_URI == "http://lavalink:2333"
    assert config.MUSIC_ENABLED is True


def test_music_disabled_flag(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    config = _reload_config()
    assert config.MUSIC_ENABLED is False


def test_lavalink_uri_uses_env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("LAVALINK_HOST", "ll.example.com")
    monkeypatch.setenv("LAVALINK_PORT", "9999")
    config = _reload_config()
    assert config.LAVALINK_URI == "http://ll.example.com:9999"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_music_config.py -v`
Expected: FAIL (`AttributeError: module 'config' has no attribute 'LAVALINK_HOST'`)

- [ ] **Step 3: Add config values**

`config.py` 끝에 추가 (기존 `_env_flag` 헬퍼 재사용):

```python
# Lavalink / 음악
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "lavalink")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_URI = f"http://{LAVALINK_HOST}:{LAVALINK_PORT}"
MUSIC_ENABLED = _env_flag("MUSIC_ENABLED", True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_music_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Update requirements, .env.example, compose, application.yml**

`requirements.txt`에 한 줄 추가:

```
wavelink>=3
```

`.env.example`에 추가 (placeholder):

```
LAVALINK_HOST=lavalink
LAVALINK_PORT=2333
LAVALINK_PASSWORD=youshallnotpass
MUSIC_ENABLED=true
```

`application.yml` 생성 (프로젝트 루트):

```yaml
server:
  port: 2333
  address: 0.0.0.0
lavalink:
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:1.13.0"
      repository: "https://maven.lavalink.dev/releases"
  server:
    password: "youshallnotpass"
    sources:
      # youtube-source 플러그인이 대체하므로 내장 youtube는 끔
      youtube: false
      bandcamp: false
      soundcloud: false
      twitch: false
      vimeo: false
      http: true
      local: false
plugins:
  youtube:
    enabled: true
logging:
  level:
    root: INFO
    lavalink: INFO
```

`docker-compose.yml`에 `lavalink` 서비스 추가 + `app`에 `depends_on`. 기존 파일 전체를 다음으로 교체:

```yaml
services:
  app:
    build:
      context: .
    image: kyuing-bot:latest
    container_name: kyuing-bot
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - lavalink
    ports:
      - "${WEB_PORT:-5001}:${WEB_PORT:-5001}"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs

  lavalink:
    image: ghcr.io/lavalink-devs/lavalink:4-alpine
    container_name: kyuing-lavalink
    restart: unless-stopped
    environment:
      - _JAVA_OPTIONS=-Xmx512M
      - SERVER_PORT=2333
      - LAVALINK_SERVER_PASSWORD=${LAVALINK_PASSWORD:-youshallnotpass}
    volumes:
      - ./application.yml:/opt/Lavalink/application.yml
      - ./lavalink-plugins:/opt/Lavalink/plugins
    expose:
      - "2333"
```

- [ ] **Step 6: Verify compose config parses**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK` (no YAML/compose errors)

- [ ] **Step 7: Commit**

```bash
git add config.py .env.example requirements.txt docker-compose.yml application.yml tests/test_music_config.py
git commit -m "feat: add Lavalink config, compose service, and music settings"
```

---

### Task 2: Wavelink Pool 연결 + 음악 가용성 헬퍼

봇 시작 시 Wavelink Pool을 Lavalink에 연결하고, 연결 실패/비활성을 graceful하게 처리한다. 음악 가용성을 한 곳에서 판단하는 헬퍼를 만들어 이후 모든 태스크가 의존한다.

**Files:**
- Create: `music_runtime.py`
- Modify: `bot.py` (import + `main`의 cog 로딩 직후 Pool 연결, `EXTENSIONS`에 음악/help cog는 Task 4·6에서 추가)
- Test: `tests/test_music_runtime.py`

**Interfaces:**
- Consumes: `config.MUSIC_ENABLED`, `config.LAVALINK_URI`, `config.LAVALINK_PASSWORD` (Task 1).
- Produces:
  - `music_runtime.WAVELINK_AVAILABLE: bool` — import 시 wavelink 설치 여부.
  - `async music_runtime.connect_pool(bot) -> bool` — Pool.connect 시도, 성공 True / 실패·비활성 False (예외 삼킴, 로깅).
  - `music_runtime.is_music_available() -> bool` — `MUSIC_ENABLED and WAVELINK_AVAILABLE and _pool_connected`.
  - `music_runtime.mark_pool_connected(value: bool) -> None` — 내부 상태 토글 (이벤트 핸들러/테스트용).

- [ ] **Step 1: Write the failing test**

`tests/test_music_runtime.py`:

```python
import importlib


def test_is_music_available_false_when_disabled(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    music_runtime.mark_pool_connected(True)
    assert music_runtime.is_music_available() is False


def test_is_music_available_requires_pool(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "true")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    music_runtime.mark_pool_connected(False)
    assert music_runtime.is_music_available() is False
    if music_runtime.WAVELINK_AVAILABLE:
        music_runtime.mark_pool_connected(True)
        assert music_runtime.is_music_available() is True


async def test_connect_pool_returns_false_when_disabled(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    import config
    importlib.reload(config)
    import music_runtime
    importlib.reload(music_runtime)
    result = await music_runtime.connect_pool(object())
    assert result is False
```

(파일 상단에 `import pytest` 및 async 테스트용 `pytestmark = pytest.mark.asyncio` 추가.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_music_runtime.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'music_runtime'`)

- [ ] **Step 3: Write music_runtime.py**

`music_runtime.py`:

```python
"""Wavelink Pool 연결 및 음악 기능 가용성 판단 (단일 진실 공급원)."""
from loguru import logger as log

import config

try:
    import wavelink
    WAVELINK_AVAILABLE = True
except ImportError:  # wavelink 미설치 환경에서도 봇은 떠야 함
    wavelink = None
    WAVELINK_AVAILABLE = False

_pool_connected = False


def mark_pool_connected(value: bool) -> None:
    global _pool_connected
    _pool_connected = value


def is_music_available() -> bool:
    return bool(config.MUSIC_ENABLED and WAVELINK_AVAILABLE and _pool_connected)


async def connect_pool(bot) -> bool:
    """Lavalink 노드에 연결. 실패/비활성 시 False 반환(예외 삼킴)."""
    if not config.MUSIC_ENABLED or not WAVELINK_AVAILABLE:
        log.info("음악 기능 비활성 (MUSIC_ENABLED={}, wavelink={})",
                 config.MUSIC_ENABLED, WAVELINK_AVAILABLE)
        return False
    try:
        node = wavelink.Node(uri=config.LAVALINK_URI, password=config.LAVALINK_PASSWORD)
        await wavelink.Pool.connect(nodes=[node], client=bot, cache_capacity=100)
        mark_pool_connected(True)
        log.info("Wavelink Pool 연결 시도 완료 uri={}", config.LAVALINK_URI)
        return True
    except Exception as e:
        log.warning("Wavelink Pool 연결 실패 — 음악 기능 비활성: {}", e)
        mark_pool_connected(False)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_music_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Wire Pool connect + node-ready/disconnect events into bot.py**

`bot.py` import 블록에 추가 (line 27 `import tts_engine` 다음):

```python
import music_runtime
```

`bot.py`의 `main()`에서 cog 로딩 루프(`for ext in EXTENSIONS:`) 직후, `async with bot:` 진입 전에 한 줄 추가:

```python
    await music_runtime.connect_pool(bot)
```

`bot.py`의 이벤트 핸들러 영역(`on_ready` 위쪽)에 노드 상태 이벤트 추가. wavelink 미설치 시 데코레이터가 단순히 호출 안 될 뿐이므로 안전:

```python
@bot.event
async def on_wavelink_node_ready(payload):
    music_runtime.mark_pool_connected(True)
    log.info("Lavalink 노드 준비 완료: {}", getattr(payload, "node", None))
```

- [ ] **Step 6: Verify bot.py imports cleanly**

Run: `python -c "import ast; ast.parse(open('bot.py').read()); ast.parse(open('music_runtime.py').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 7: Commit**

```bash
git add music_runtime.py bot.py tests/test_music_runtime.py
git commit -m "feat: connect Wavelink pool and add music availability gate"
```

---

### Task 3: 핸드오프 모듈 — music_handoff.py

음악 재생 중 TTS/사운드가 끼어들 때 연결을 가로채고 음악을 복구하는 로직. TTS 엔진과 분리해 단위 테스트 가능하게 만든다. (Task 4 cog 전에 작성 — cog 없이도 핸드오프 로직 자체를 mock으로 검증 가능.)

**Files:**
- Create: `music_handoff.py`
- Test: `tests/test_music_handoff.py`

**Interfaces:**
- Consumes: `music_runtime.is_music_available()`, `music_runtime.WAVELINK_AVAILABLE` (Task 2).
- Produces:
  - `music_handoff.AIRBAG_MS = 200`
  - `music_handoff.compute_resume_ms(position_ms: int) -> int` = `max(0, position_ms - AIRBAG_MS)`.
  - `music_handoff.get_active_music_player(guild)` → `wavelink.Player` 또는 `None` (음악 비활성/미재생이면 None).
  - `async music_handoff.suspend_music(player) -> tuple` — `(resume_track, resume_ms)` 반환 후 `player.pause(True)` + `player.disconnect()`. player None이면 `(None, None)`.
  - `async music_handoff.resume_music(voice_channel, resume_track, resume_ms) -> None` — 트랙 있으면 Player로 재연결해 `start=resume_ms`부터 재생. 실패 시 로깅 후 삼킴.

- [ ] **Step 1: Write the failing test**

`tests/test_music_handoff.py`:

```python
import pytest

import music_handoff

pytestmark = pytest.mark.asyncio


def test_compute_resume_ms_airbag():
    assert music_handoff.compute_resume_ms(5000) == 4800
    assert music_handoff.AIRBAG_MS == 200


def test_compute_resume_ms_clamps_to_zero():
    assert music_handoff.compute_resume_ms(100) == 0
    assert music_handoff.compute_resume_ms(0) == 0


def test_get_active_music_player_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(music_handoff.music_runtime, "is_music_available", lambda: False)
    guild = type("G", (), {"voice_client": object()})()
    assert music_handoff.get_active_music_player(guild) is None


async def test_suspend_music_returns_position_and_pauses(monkeypatch):
    calls = {}

    class FakeTrack: ...
    track = FakeTrack()

    class FakePlayer:
        current = track
        position = 5000
        async def pause(self, value): calls["paused"] = value
        async def disconnect(self): calls["disconnected"] = True

    resume_track, resume_ms = await music_handoff.suspend_music(FakePlayer())
    assert resume_track is track
    assert resume_ms == 4800
    assert calls["paused"] is True
    assert calls["disconnected"] is True


async def test_suspend_music_handles_none():
    assert await music_handoff.suspend_music(None) == (None, None)


async def test_resume_music_noop_without_track():
    # 트랙이 없으면 아무 것도 하지 않고 조용히 반환
    await music_handoff.resume_music(object(), None, 0)


async def test_resume_music_reconnects_and_plays(monkeypatch):
    calls = {}

    class FakePlayer:
        async def play(self, track, start=0): calls["played"] = (track, start)

    fake_player = FakePlayer()

    class FakeChannel:
        async def connect(self, cls=None): 
            calls["connected_cls"] = cls
            return fake_player

    track = object()
    monkeypatch.setattr(music_handoff, "_player_cls", lambda: FakePlayer)
    await music_handoff.resume_music(FakeChannel(), track, 4800)
    assert calls["played"] == (track, 4800)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_music_handoff.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'music_handoff'`)

- [ ] **Step 3: Write music_handoff.py**

`music_handoff.py`:

```python
"""음악 재생 중 TTS/사운드 인터럽트를 위한 연결 핸드오프 (휘발성 상태)."""
from loguru import logger as log

import music_runtime

AIRBAG_MS = 200


def _player_cls():
    """테스트에서 monkeypatch 가능하도록 wavelink.Player를 함수로 노출."""
    import wavelink
    return wavelink.Player


def compute_resume_ms(position_ms: int) -> int:
    return max(0, position_ms - AIRBAG_MS)


def get_active_music_player(guild):
    """이 길드에 재생 중인 음악 Player를 반환, 없으면 None."""
    if not music_runtime.WAVELINK_AVAILABLE or not music_runtime.is_music_available():
        return None
    import wavelink
    player = getattr(guild, "voice_client", None)
    if isinstance(player, wavelink.Player) and player.playing:
        return player
    return None


async def suspend_music(player):
    """음악을 멈추고 연결을 해제. (resume_track, resume_ms) 휘발성 상태 반환."""
    if player is None:
        return (None, None)
    resume_track = player.current
    resume_ms = compute_resume_ms(player.position)
    await player.pause(True)
    await player.disconnect()
    return (resume_track, resume_ms)


async def resume_music(voice_channel, resume_track, resume_ms) -> None:
    """음악 Player로 재연결해 resume_ms 위치부터 재생. 실패는 삼킨다."""
    if resume_track is None:
        return
    # 대상 채널에 사람이 없으면(빈 채널) 재연결 생략
    members = getattr(voice_channel, "members", None)
    if members is not None and all(getattr(m, "bot", False) for m in members):
        return
    try:
        player = await voice_channel.connect(cls=_player_cls())
        await player.play(resume_track, start=resume_ms)
    except Exception as e:
        log.warning("음악 재개 실패 — 포기: {}", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_music_handoff.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add music_handoff.py tests/test_music_handoff.py
git commit -m "feat: add music handoff module with airbag resume"
```

---

### Task 4: TTS/사운드 재생에 핸드오프 통합

`tts_engine.py`의 `do_tts`/`play_sound`가 음악 재생 중이면 핸드오프를 거치도록 한다. 음악 비활성/미재생 시 기존 경로와 완전히 동일해야 한다.

**Files:**
- Modify: `tts_engine.py` (`do_tts` line 92-119, `play_sound` line 122-146)
- Test: `tests/test_tts_handoff_integration.py`

**Interfaces:**
- Consumes: `music_handoff.get_active_music_player`, `suspend_music`, `resume_music` (Task 3).
- Produces: 동작 변경만 — 외부 시그니처 불변 (`do_tts`, `play_sound` 인자 그대로).

- [ ] **Step 1: Write the failing test**

`tests/test_tts_handoff_integration.py`:

```python
import pytest

import tts_engine
import music_handoff

pytestmark = pytest.mark.asyncio


async def test_no_music_skips_handoff(monkeypatch):
    """음악 미재생이면 suspend/resume이 호출되지 않는다."""
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: None)

    suspend_called = {"n": 0}
    monkeypatch.setattr(music_handoff, "suspend_music",
                        lambda p: suspend_called.__setitem__("n", suspend_called["n"] + 1))

    # _play_with_handoff은 player None이면 곧장 base_play를 호출해야 한다
    played = {"n": 0}
    async def base_play():
        played["n"] += 1

    await tts_engine._play_with_handoff(guild=object(), voice_channel=object(), base_play=base_play)
    assert played["n"] == 1
    assert suspend_called["n"] == 0


async def test_music_active_triggers_suspend_and_resume(monkeypatch):
    """음악 재생 중이면 suspend → base_play → resume 순서로 실행."""
    order = []

    fake_player = object()
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: fake_player)

    async def fake_suspend(p):
        order.append("suspend")
        return ("TRACK", 4800)
    async def fake_resume(ch, track, ms):
        order.append(("resume", track, ms))
    monkeypatch.setattr(music_handoff, "suspend_music", fake_suspend)
    monkeypatch.setattr(music_handoff, "resume_music", fake_resume)

    async def base_play():
        order.append("play")

    ch = object()
    await tts_engine._play_with_handoff(guild=object(), voice_channel=ch, base_play=base_play)
    assert order == ["suspend", "play", ("resume", "TRACK", 4800)]


async def test_resume_runs_even_if_base_play_raises(monkeypatch):
    """base_play 예외에도 음악 재개를 시도한다."""
    order = []
    monkeypatch.setattr(music_handoff, "get_active_music_player", lambda g: object())
    async def fake_suspend(p):
        return ("TRACK", 100)
    async def fake_resume(ch, track, ms):
        order.append("resume")
    monkeypatch.setattr(music_handoff, "suspend_music", fake_suspend)
    monkeypatch.setattr(music_handoff, "resume_music", fake_resume)

    async def base_play():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await tts_engine._play_with_handoff(guild=object(), voice_channel=object(), base_play=base_play)
    assert order == ["resume"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_handoff_integration.py -v`
Expected: FAIL (`AttributeError: module 'tts_engine' has no attribute '_play_with_handoff'`)

- [ ] **Step 3: Add _play_with_handoff helper to tts_engine.py**

`tts_engine.py` import 블록에 추가:

```python
import music_handoff
```

`do_tts`/`play_sound` 위에 헬퍼 추가:

```python
async def _play_with_handoff(guild, voice_channel, base_play):
    """음악 재생 중이면 핸드오프(suspend→base_play→resume), 아니면 base_play만.

    base_play: 실제 FFmpeg 재생을 수행하는 async 콜러블 (인자 없음).
    """
    player = music_handoff.get_active_music_player(guild)
    if player is None:
        await base_play()
        return

    resume_track, resume_ms = await music_handoff.suspend_music(player)
    try:
        await base_play()
    finally:
        await music_handoff.resume_music(voice_channel, resume_track, resume_ms)
        # resume_track/resume_ms 는 여기서 마지막 사용, 함수 종료로 휘발
```

- [ ] **Step 4: Refactor do_tts to use the helper**

`do_tts`의 `async with _locks[guild.id]:` 블록(현재 line 92-119)을 다음으로 교체. 합성은 음악을 건드리기 전에 수행한다:

```python
    async with _locks[guild.id]:
        tmp_path = None
        try:
            tmp_path = await engine.synthesize(
                text, voice=voice, speed=speed, lang=lang,
                total_steps=total_steps, bot_id=bot_id,
            )

            async def _base_play():
                vc = await _ensure_voice_client(guild, voice_channel)
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(tmp_path))
                while vc.is_playing():
                    await asyncio.sleep(0.5)

            await _play_with_handoff(guild, voice_channel, _base_play)
            return None
        except Exception as e:
            return f"TTS 오류: {str(e)}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
```

- [ ] **Step 5: Refactor play_sound to use the helper**

`play_sound`의 `async with _locks[guild.id]:` 블록(현재 line 128-146)을 다음으로 교체:

```python
    async with _locks[guild.id]:
        try:
            async def _base_play():
                vc = await _ensure_voice_client(guild, voice_channel)
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(file_path))
                while vc.is_playing():
                    await asyncio.sleep(0.5)
                if vc.is_playing():
                    vc.stop()

            await _play_with_handoff(guild, voice_channel, _base_play)
            return None
        except Exception as e:
            return f"사운드 재생 오류: {str(e)}"
```

- [ ] **Step 6: Run all tts tests to verify pass + no regression**

Run: `python -m pytest tests/test_tts_handoff_integration.py tests/test_tts_pipeline.py -v`
Expected: PASS (신규 3개 + 기존 파이프라인 테스트 통과)

- [ ] **Step 7: Commit**

```bash
git add tts_engine.py tests/test_tts_handoff_integration.py
git commit -m "feat: route TTS and soundboard playback through music handoff"
```

---

### Task 5: 음악 Cog — 핵심 명령어 + 이벤트

`/music play|skip|stop|pause|resume|queue|nowplaying` 와 Wavelink 이벤트(now playing/track end). 나머지 명령어는 Task 6에서 같은 cog에 추가.

**Files:**
- Create: `cogs/music.py`
- Modify: `bot.py` (`EXTENSIONS`에 `"cogs.music"` 추가)
- Test: `tests/test_music_cog.py`

**Interfaces:**
- Consumes: `music_runtime.is_music_available` (Task 2), `bot.bot_id`.
- Produces: `MusicCog`, `setup(bot)`, `music_group = app_commands.Group(name="music", ...)`.

- [ ] **Step 1: Write the failing test (command name uniqueness + group name)**

`tests/test_music_cog.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_music_cog_registers_music_group():
    import cogs.music as music
    assert music.MusicCog.music_group.name == "music"
    sub = {c.name for c in music.MusicCog.music_group.commands}
    for expected in ("play", "skip", "stop", "pause", "resume", "queue", "nowplaying"):
        assert expected in sub


async def test_music_subcommands_do_not_collide_with_top_level():
    """음악은 /music 그룹 하위라 기존 최상위 play/stop/speed와 충돌하지 않는다."""
    import cogs.music as music
    # 그룹 이름이 'music'이고, 하위 커맨드는 그룹 네임스페이스에 속한다
    assert music.MusicCog.music_group.name == "music"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_music_cog.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'cogs.music'`)

- [ ] **Step 3: Write cogs/music.py core**

`cogs/music.py`:

```python
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
```

- [ ] **Step 4: Add cog to EXTENSIONS**

`bot.py` line 38을 수정:

```python
EXTENSIONS = ["cogs.tts", "cogs.channels", "cogs.voice", "cogs.sounds", "cogs.music"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_music_cog.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add cogs/music.py bot.py tests/test_music_cog.py
git commit -m "feat: add music cog with core playback commands and now-playing"
```

---

### Task 6: 음악 Cog — 나머지 명령어 + 빈 채널 큐 정리 + /help

풀 기능 잔여 명령어(volume/remove/clear/loop/shuffle/seek/speed), 빈 채널 퇴장 시 큐 정리 보강, 그리고 `/help` cog.

**Files:**
- Modify: `cogs/music.py` (명령어 추가)
- Modify: `bot.py` (`disconnect_if_voice_channel_empty`에 큐 정리 보강, `EXTENSIONS`에 `"cogs.help"`)
- Create: `cogs/help.py`
- Test: `tests/test_help_cog.py`, `tests/test_music_extra_commands.py`

**Interfaces:**
- Consumes: `MusicCog._player`, `_guard` (Task 5); `music_runtime.is_music_available` (Task 2).
- Produces: `HelpCog`, `HELP_CATEGORIES: dict[str, list[str]]`, `cogs.help.setup`.

- [ ] **Step 1: Write failing tests**

`tests/test_music_extra_commands.py`:

```python
async def test_extra_commands_registered():
    import cogs.music as music
    sub = {c.name for c in music.MusicCog.music_group.commands}
    for expected in ("volume", "remove", "clear", "loop", "shuffle", "seek", "speed"):
        assert expected in sub

import pytest
pytestmark = pytest.mark.asyncio
```

`tests/test_help_cog.py`:

```python
import pytest
pytestmark = pytest.mark.asyncio


async def test_help_categories_cover_known_commands():
    import cogs.help as helpmod
    cats = helpmod.HELP_CATEGORIES
    flat = {name for cmds in cats.values() for name in cmds}
    for expected in ("join", "engine", "sound", "play", "music"):
        assert expected in flat


async def test_help_cog_has_help_command():
    import cogs.help as helpmod
    cog = helpmod.HelpCog(bot=None)
    names = {c.name for c in cog.walk_app_commands()}
    assert "help" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_music_extra_commands.py tests/test_help_cog.py -v`
Expected: FAIL (extra commands not present; `No module named 'cogs.help'`)

- [ ] **Step 3: Add remaining music commands to cogs/music.py**

`MusicCog` 안, `on_wavelink_track_start` 위에 추가:

```python
    @music_group.command(name="volume", description="볼륨 조절 (0~100)")
    @app_commands.describe(value="0~100")
    async def volume(self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 100]):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        await player.set_volume(value)
        await interaction.response.send_message(f"🔊 볼륨 {value}")

    @music_group.command(name="remove", description="큐에서 특정 번호의 곡을 제거")
    @app_commands.describe(index="큐 번호 (/music queue 기준)")
    async def remove(self, interaction: discord.Interaction, index: app_commands.Range[int, 1, 1000]):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or len(player.queue) < index:
            await interaction.response.send_message("❌ 그 번호의 곡이 없어요.", ephemeral=True)
            return
        removed = player.queue[index - 1]
        del player.queue[index - 1]
        await interaction.response.send_message(f"🗑️ 제거: **{removed.title}**")

    @music_group.command(name="clear", description="큐를 비웁니다 (현재 곡 유지)")
    async def clear(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        player.queue.clear()
        await interaction.response.send_message("🧹 큐를 비웠어요.")

    @music_group.command(name="loop", description="반복 모드 설정")
    @app_commands.describe(mode="off / track / queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="track", value="track"),
        app_commands.Choice(name="queue", value="queue"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("❌ 재생 중이 아니에요.", ephemeral=True)
            return
        modes = {
            "off": wavelink.QueueMode.normal,
            "track": wavelink.QueueMode.loop,
            "queue": wavelink.QueueMode.loop_all,
        }
        player.queue.mode = modes[mode.value]
        await interaction.response.send_message(f"🔁 반복: {mode.value}")

    @music_group.command(name="shuffle", description="큐를 섞습니다")
    async def shuffle(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or len(player.queue) < 2:
            await interaction.response.send_message("❌ 섞을 곡이 부족해요.", ephemeral=True)
            return
        player.queue.shuffle()
        await interaction.response.send_message("🔀 큐를 섞었어요.")

    @music_group.command(name="seek", description="재생 위치 이동 (초)")
    @app_commands.describe(seconds="이동할 위치(초)")
    async def seek(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 86400]):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or not player.playing:
            await interaction.response.send_message("❌ 재생 중인 곡이 없어요.", ephemeral=True)
            return
        await player.seek(seconds * 1000)
        await interaction.response.send_message(f"⏩ {seconds}초로 이동")

    @music_group.command(name="speed", description="배속 (0.5~2.0)")
    @app_commands.describe(rate="0.5 ~ 2.0")
    async def speed(self, interaction: discord.Interaction, rate: app_commands.Range[float, 0.5, 2.0]):
        if not await self._guard(interaction):
            return
        player = self._player(interaction)
        if player is None or not player.playing:
            await interaction.response.send_message("❌ 재생 중인 곡이 없어요.", ephemeral=True)
            return
        filters = player.filters
        filters.timescale.set(speed=rate)
        await player.set_filters(filters)
        await interaction.response.send_message(f"⏱️ 배속 {rate}x")
```

- [ ] **Step 4: Reinforce empty-channel queue cleanup in bot.py**

`bot.py`의 `disconnect_if_voice_channel_empty` 함수에서 `await vc.disconnect()` 직전에 큐 정리 추가. wavelink 미설치/일반 VoiceClient면 조용히 건너뛴다:

```python
    try:
        import wavelink
        if isinstance(vc, wavelink.Player):
            vc.queue.clear()
    except Exception:
        pass
    await vc.disconnect()
```

(기존 `await vc.disconnect()` 한 줄을 위 블록으로 교체.)

- [ ] **Step 5: Write cogs/help.py**

`cogs/help.py`:

```python
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
```

- [ ] **Step 6: Add help cog to EXTENSIONS**

`bot.py`의 `EXTENSIONS`를 수정:

```python
EXTENSIONS = ["cogs.tts", "cogs.channels", "cogs.voice", "cogs.sounds", "cogs.music", "cogs.help"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_music_extra_commands.py tests/test_help_cog.py tests/test_music_cog.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add cogs/music.py cogs/help.py bot.py tests/test_music_extra_commands.py tests/test_help_cog.py
git commit -m "feat: add remaining music commands, queue cleanup, and /help"
```

---

### Task 7: 전체 테스트 + 문서 업데이트

전체 스위트를 돌려 회귀가 없는지 확인하고, README에 음악 기능·Lavalink 운영을 문서화한다.

**Files:**
- Modify: `README.md`, `README.ko.md`
- Modify: `docs/` (사운드보드 문서가 있던 곳에 음악 섹션 추가 — 있으면)

**Interfaces:** 없음 (문서/검증).

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체 통과, 신규 테스트 포함, 기존 테스트 회귀 없음)

- [ ] **Step 2: Document music feature in README.md**

`README.md`의 명령어 목록 섹션(사운드보드 `/sound` 설명 근처)에 음악 섹션 추가:

```markdown
## Music (Lavalink)

The bot can stream YouTube audio via a Lavalink node. Music shares the voice
connection with TTS: when a TTS message arrives during playback, the music is
paused and resumed automatically (resumes ~0.2s earlier as an airbag).

Commands (all under the `/music` group):
- `/music play <query|url>`: search YouTube and queue/play
- `/music skip|stop|pause|resume`
- `/music queue|nowplaying`
- `/music volume <0-100>` · `/music seek <seconds>` · `/music speed <0.5-2.0>`
- `/music remove <index>` · `/music clear` · `/music loop <off|track|queue>` · `/music shuffle`

Use `/help` for a categorized command guide.

### Running Lavalink

`docker compose up -d` starts both `app` and `lavalink`. Configure via env:
`LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_PASSWORD`, `MUSIC_ENABLED`.
Lavalink runs with `-Xmx512M`. Set `MUSIC_ENABLED=false` to disable music
without removing the service.
```

- [ ] **Step 3: Mirror the section in README.ko.md**

`README.ko.md`에 동일 내용을 한국어로 추가:

```markdown
## 음악 (Lavalink)

Lavalink 노드를 통해 YouTube 음원을 재생합니다. 음악은 TTS와 음성 연결을
공유하며, 재생 중 TTS 메시지가 오면 음악을 자동으로 일시정지했다가 재개합니다
(에어백으로 약 0.2초 앞에서 재개).

명령어 (모두 `/music` 그룹 하위):
- `/music play <검색어|URL>`: YouTube 검색 후 큐 추가/재생
- `/music skip|stop|pause|resume`
- `/music queue|nowplaying`
- `/music volume <0~100>` · `/music seek <초>` · `/music speed <0.5~2.0>`
- `/music remove <번호>` · `/music clear` · `/music loop <off|track|queue>` · `/music shuffle`

`/help` 로 카테고리별 명령어 가이드를 볼 수 있습니다.

### Lavalink 실행

`docker compose up -d` 로 `app` 과 `lavalink` 가 함께 뜹니다. 환경변수로 설정:
`LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_PASSWORD`, `MUSIC_ENABLED`.
Lavalink 는 `-Xmx512M` 로 동작합니다. `MUSIC_ENABLED=false` 로 서비스를 지우지
않고 음악만 끌 수 있습니다.
```

- [ ] **Step 4: Commit**

```bash
git add README.md README.ko.md docs/
git commit -m "docs: document Lavalink music feature and operations"
```

---

## 구현 순서 / 의존성

1 (인프라) → 2 (Pool 연결) → 3 (핸드오프 모듈) → 4 (TTS 통합) → 5 (음악 cog 핵심) → 6 (잔여 명령 + help) → 7 (테스트/문서)

Task 3은 Task 4의 의존이며, Task 5·6은 Task 2에 의존한다. Task 4와 Task 5는 서로 독립이나, 안정성을 위해 순서대로 진행한다.
