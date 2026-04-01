"""
Logging setup for the framework.

Call `get_logger(name)` anywhere to obtain a configured logger.
All loggers share a single handler setup initialised once.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


_INITIALIZED = False
_LOG_DIR = Path("data/logs")
_LOG_FILE = _LOG_DIR / "system.log"
_LOG_LEVEL = logging.INFO
_FMT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"


def _ensure_log_dir() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def init_logging(
    level: str = "INFO",
    log_file: str | None = None,
    console: bool = True,
) -> None:
    """
    Initialise root logger.
    Call once at startup, then use get_logger() everywhere else.
    """
    global _INITIALIZED, _LOG_LEVEL
    if _INITIALIZED:
        return

    _ensure_log_dir()
    _LOG_LEVEL = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers filter individually

    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(_LOG_LEVEL)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    file_path = Path(log_file) if log_file else _LOG_FILE
    _ensure_log_dir()
    fh = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=7,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call init_logging() first."""
    return logging.getLogger(name)
