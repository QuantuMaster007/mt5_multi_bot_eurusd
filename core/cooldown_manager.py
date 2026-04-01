"""
Cooldown Manager

Centralised cooldown state tracker used by multiple modules.
Prevents repeated rapid actions after a failure or policy decision.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from core.logger import get_logger

log = get_logger("cooldown_manager")


class CooldownManager:
    """
    Thread-safe (GIL-protected dict ops are atomic in CPython) cooldown tracker.

    Usage::

        cooldown_mgr.set("mean_reversion.entry", 120)
        if not cooldown_mgr.is_active("mean_reversion.entry"):
            ...
    """

    def __init__(self) -> None:
        self._cooldowns: Dict[str, float] = {}   # key → monotonic expiry

    def set(self, key: str, seconds: float) -> None:
        """Set a cooldown for *key* lasting *seconds*."""
        expiry = time.monotonic() + seconds
        self._cooldowns[key] = expiry
        log.debug("Cooldown set: key=%s seconds=%.0f", key, seconds)

    def is_active(self, key: str) -> bool:
        """Return True if the cooldown for *key* is still active."""
        expiry = self._cooldowns.get(key, 0)
        return time.monotonic() < expiry

    def remaining(self, key: str) -> float:
        """Seconds remaining on cooldown, or 0 if not active."""
        expiry = self._cooldowns.get(key, 0)
        return max(0.0, expiry - time.monotonic())

    def clear(self, key: str) -> None:
        """Manually clear a cooldown."""
        self._cooldowns.pop(key, None)

    def clear_all(self) -> None:
        self._cooldowns.clear()

    def active_keys(self) -> Dict[str, float]:
        """Return {key: remaining_seconds} for all active cooldowns."""
        now = time.monotonic()
        return {
            k: round(v - now, 1)
            for k, v in self._cooldowns.items()
            if v > now
        }


# Module-level singleton
cooldown_manager = CooldownManager()
