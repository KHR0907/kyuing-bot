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
