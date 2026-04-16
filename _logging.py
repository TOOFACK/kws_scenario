"""Общий настройщик логирования: stdout + ротируемый файл."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"
LOG_DIR = Path(os.environ.get(
    "VOICE_LOG_DIR",
    str(Path(__file__).parent / "logs"),
))


def setup_logger(name: str, filename: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    fmt = logging.Formatter(LOG_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    rotating = RotatingFileHandler(
        LOG_DIR / filename,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setFormatter(fmt)
    logger.addHandler(rotating)

    logger.info("log file: %s", LOG_DIR / filename)
    return logger
