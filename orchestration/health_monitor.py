"""
Health Monitor

Reads heartbeat files and flags bots that have not written a
heartbeat within the expected interval. Called periodically by
the orchestrator.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List

from core.heartbeat import read_all_heartbeats
from core.logger import get_logger

log = get_logger("health_monitor")

_MAX_HEARTBEAT_AGE_SECONDS = 60  # flag as stale after this


class HealthMonitor:

    def check(self) -> Dict[str, str]:
        """
        Return a dict of {strategy_name: status_string} for all running bots.
        Status is "ok", "stale", or "missing".
        """
        heartbeats = read_all_heartbeats()
        status: Dict[str, str] = {}
        now_ts = time.time()

        for name, hb in heartbeats.items():
            ts_str = hb.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
                age = now_ts - ts
                if age > _MAX_HEARTBEAT_AGE_SECONDS:
                    status[name] = f"stale ({age:.0f}s)"
                    log.warning("Bot %s heartbeat is stale: %.0fs old", name, age)
                else:
                    status[name] = f"ok ({hb.get('status', '?')})"
            except Exception:
                status[name] = "stale (parse error)"

        return status

    def get_stale_bots(self) -> List[str]:
        return [k for k, v in self.check().items() if "stale" in v]


# Module-level singleton
health_monitor = HealthMonitor()
