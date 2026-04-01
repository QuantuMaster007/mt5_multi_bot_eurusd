"""
News Filter

Provides a stub implementation of a high-impact news window guard.
In production, connect to a real economic calendar API (e.g., Forex Factory)
and populate the _news_events list.

Until integrated, the filter is conservative: it blocks trading if
the current time is within a configurable window of any news event
defined in news_events.yaml (not yet wired up — returns False by default).

TODO for production:
  1. Fetch events from a calendar API daily
  2. Store in data/state/news_events.json
  3. Call is_news_window() before every entry
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from core.logger import get_logger

log = get_logger("news_filter")


class NewsFilter:
    """
    Checks whether the current time falls within a high-impact news window.

    Without an external calendar feed, this always returns False (no block).
    Operators should extend this class or replace it with a real data source.
    """

    def __init__(self, buffer_minutes: int = 15) -> None:
        self._buffer_minutes = buffer_minutes
        self._events: List[datetime] = []   # populated externally

    def load_events(self, events: List[datetime]) -> None:
        """Load a list of high-impact event timestamps (UTC)."""
        self._events = sorted(events)
        log.info("News filter loaded %d events", len(events))

    def is_news_window(self) -> bool:
        """
        Return True if now is within ±buffer_minutes of any loaded event.
        Returns False if no events are loaded.
        """
        if not self._events:
            return False

        now = datetime.now(timezone.utc)
        from datetime import timedelta
        buf = timedelta(minutes=self._buffer_minutes)

        for event_ts in self._events:
            if abs((now - event_ts).total_seconds()) <= buf.total_seconds():
                log.info("News window active: event at %s", event_ts)
                return True
        return False


# Module-level singleton
news_filter = NewsFilter()
