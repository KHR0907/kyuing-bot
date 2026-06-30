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
_connect_attempted = False


def mark_pool_connected(value: bool) -> None:
    global _pool_connected
    _pool_connected = value


def is_music_available() -> bool:
    return bool(config.MUSIC_ENABLED and WAVELINK_AVAILABLE and _pool_connected)


async def connect_pool(bot) -> bool:
    """Lavalink 노드에 연결. 실패/비활성 시 False 반환(예외 삼킴).

    on_ready는 재연결 시 여러 번 호출될 수 있으므로 한 번만 시도한다.
    봇이 Discord에 로그인을 마친 뒤(bot.user가 채워진 뒤) 호출해야 한다 —
    로그인 전에 호출하면 wavelink가 user-id 없이 핸드셰이크를 시도해
    빈 에러("")로 무한 재시도하게 된다.
    """
    global _connect_attempted
    if not config.MUSIC_ENABLED or not WAVELINK_AVAILABLE:
        log.info("음악 기능 비활성 (MUSIC_ENABLED={}, wavelink={})",
                 config.MUSIC_ENABLED, WAVELINK_AVAILABLE)
        return False
    if _connect_attempted:
        return _pool_connected
    _connect_attempted = True
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
