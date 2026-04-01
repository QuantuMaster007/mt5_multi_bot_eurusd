"""
Heartbeat

Each bot writes a small JSON file every N seconds so the orchestrator
and health_monitor can detect stalled bots without shared memory.
We use files because the framework uses a threaded model, but the
file-based approach also works transparently with multiprocessing.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Optional

from core.logger import get_logger
from core.utils import ts_now, ensure_dir

log = get_logger("heartbeat")

_HB_DIR = Path("data/heartbeats")
ensure_dir(_HB_DIR)


@dataclass
class HeartbeatPayload:
    strategy:       str
    status:         str         # enabled / paused / blocked / error
    ts:             str = ""
    last_signal:    Optional[str] = None
    last_trade_ts:  Optional[str] = None
    open_positions: int = 0
    last_error:     Optional[str] = None
    loop_count:     int = 0
    extra:          Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.ts:
            self.ts = ts_now()


class Heartbeat:
    """
    Writes heartbeat files to data/heartbeats/<strategy_name>.json.

    Usage in bot loop::

        hb = Heartbeat("mean_reversion")
        hb.beat(status="enabled", loop_count=n, open_positions=1)
    """

    def __init__(self, strategy: str, interval: float = 10.0) -> None:
        self._strategy = strategy
        self._interval = interval
        self._path = _HB_DIR / f"{strategy}.json"
        self._last_beat = 0.0
        self._loop_count = 0

    def beat(
        self,
        status: str = "enabled",
        open_positions: int = 0,
        last_signal: Optional[str] = None,
        last_trade_ts: Optional[str] = None,
        last_error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> None:
        """
        Write heartbeat if interval has elapsed (or force=True).
        Never raises — heartbeat failure must not crash a bot.
        """
        self._loop_count += 1
        now = time.monotonic()
        if not force and (now - self._last_beat) < self._interval:
            return

        payload = HeartbeatPayload(
            strategy=self._strategy,
            status=status,
            last_signal=last_signal,
            last_trade_ts=last_trade_ts,
            open_positions=open_positions,
            last_error=last_error,
            loop_count=self._loop_count,
            extra=extra or {},
        )
        try:
            with open(self._path, "w") as f:
                json.dump(asdict(payload), f, default=str)
            self._last_beat = now
        except Exception as exc:
            log.warning("Heartbeat write failed for %s: %s", self._strategy, exc)


def read_all_heartbeats() -> Dict[str, Dict]:
    """Read all heartbeat files. Used by health_monitor and weekly_review."""
    result: Dict[str, Dict] = {}
    for hb_file in _HB_DIR.glob("*.json"):
        try:
            with open(hb_file) as f:
                data = json.load(f)
            result[data.get("strategy", hb_file.stem)] = data
        except Exception:
            pass
    return result
