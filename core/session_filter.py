"""
Session Filter

Determines whether the current UTC time falls within a preferred
trading session, with buffer zones around session open/close.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Dict, List, Tuple

from core.constants import EVT_SESSION_FILTER
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.settings import settings

log = get_logger("session_filter")


def _parse_time(t: str) -> time:
    """Parse 'HH:MM' string to datetime.time."""
    h, m = t.split(":")
    return time(int(h), int(m))


class SessionFilter:
    """
    Session-based gate.

    is_tradeable_now() returns True if at least one preferred session
    is currently active (with buffer margins).
    """

    def __init__(self) -> None:
        sym_cfg = settings.symbol
        raw_sessions: Dict = sym_cfg.get("sessions", {})
        self._sessions: Dict[str, Tuple[time, time]] = {}
        for name, window in raw_sessions.items():
            self._sessions[name] = (
                _parse_time(window["start"]),
                _parse_time(window["end"]),
            )
        self._preferred: List[str] = sym_cfg.get(
            "preferred_sessions", list(self._sessions.keys())
        )
        self._buffer_before = int(sym_cfg.get("avoid_sessions_before_open_minutes", 15))
        self._buffer_after  = int(sym_cfg.get("avoid_sessions_after_close_minutes", 15))

    def is_tradeable_now(
        self,
        allowed_sessions: List[str] | None = None,
        strategy_name: str = "",
    ) -> bool:
        """
        Return True if current UTC time is inside at least one
        allowed (and preferred) session, outside the buffer zones.
        """
        now_utc = datetime.now(timezone.utc).time()
        sessions_to_check = allowed_sessions if allowed_sessions else self._preferred

        for name in sessions_to_check:
            if name not in self._sessions:
                continue
            start, end = self._sessions[name]
            if self._within_session(now_utc, start, end):
                return True

        if strategy_name:
            get_event_logger().write({
                "event":    EVT_SESSION_FILTER,
                "strategy": strategy_name,
                "utc_time": now_utc.strftime("%H:%M:%S"),
                "reason":   "Outside allowed sessions",
            })
        return False

    def _within_session(
        self, now: time, start: time, end: time
    ) -> bool:
        """
        Check if *now* is within [start+buffer, end-buffer].
        Handles sessions that wrap midnight (rare but possible).
        """
        from datetime import timedelta
        from datetime import datetime as _dt

        base = _dt(2000, 1, 1)
        t_now   = base.replace(hour=now.hour,   minute=now.minute)
        t_start = base.replace(hour=start.hour, minute=start.minute)
        t_end   = base.replace(hour=end.hour,   minute=end.minute)

        if t_end <= t_start:
            t_end += timedelta(days=1)

        t_start_buf = t_start + timedelta(minutes=self._buffer_before)
        t_end_buf   = t_end   - timedelta(minutes=self._buffer_after)

        return t_start_buf <= t_now <= t_end_buf


# Module-level singleton
session_filter = SessionFilter()
