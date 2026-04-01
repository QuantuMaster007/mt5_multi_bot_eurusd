"""
Structured JSON line logger.

Writes one JSON object per line to rotating JSONL files.
Used for events, trades, and policy decisions — all machine-readable
so weekly_review.py can parse and aggregate them without grep hacks.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any, Dict

from core.utils import ts_now, ensure_dir


class JsonLineLogger:
    """
    Appends structured dicts as JSON lines to a rotating file.

    Usage::

        jl = JsonLineLogger("data/events/events.jsonl")
        jl.write({"event": "order_sent", "symbol": "EURUSD", ...})
    """

    def __init__(self, path: str | Path, max_bytes: int = 20 * 1024 * 1024, backup_count: int = 14):
        self._path = Path(path)
        ensure_dir(self._path.parent)

        # Use Python's RotatingFileHandler as the underlying writer
        self._handler = logging.handlers.RotatingFileHandler(
            self._path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._logger = logging.getLogger(f"jsonl.{self._path.stem}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False          # don't pollute root logger
        if not self._logger.handlers:
            self._logger.addHandler(self._handler)

    def write(self, record: Dict[str, Any]) -> None:
        """Stamp with UTC timestamp and write as a single JSON line."""
        record.setdefault("ts", ts_now())
        try:
            self._logger.info(json.dumps(record, default=str))
        except Exception:
            pass  # never crash a trading loop over logging


# ─── Module-level singletons, lazily created ─────────────────────────────────

_event_logger: JsonLineLogger | None = None
_trade_logger: JsonLineLogger | None = None


def get_event_logger() -> JsonLineLogger:
    global _event_logger
    if _event_logger is None:
        _event_logger = JsonLineLogger("data/events/events.jsonl")
    return _event_logger


def get_trade_logger() -> JsonLineLogger:
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = JsonLineLogger("data/trades/trades.jsonl")
    return _trade_logger
