from pathlib import Path
import sys

from loguru import logger

from config import LOG_PATH, LOG_RETENTION_DAYS


def configure_logging():
    log_path = Path(LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        str(log_path),
        level="INFO",
        rotation="00:00",
        retention=f"{LOG_RETENTION_DAYS} days",
        encoding="utf-8",
        backtrace=False,
        diagnose=False,
    )
