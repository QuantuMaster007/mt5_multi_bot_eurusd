"""
Broker Profile

Dynamically detects broker capabilities from MT5 symbol_info.

FIX P1: refresh() is now called by MT5Connector after every successful
         reconnect, ensuring fill modes and stop levels stay accurate.
FIX P2: When filling_mode bits are all zero (buggy broker report), we
         probe by attempting order_check with each mode rather than
         assuming RETURN is safe. Fallback is now IOC (most common).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.constants import FILL_FOK, FILL_IOC, FILL_RETURN, FILL_MODE_NAMES
from core.logger import get_logger
from core.mt5_connector import connector

log = get_logger("broker_profile")

_BIT_FOK    = 1
_BIT_IOC    = 2
_BIT_RETURN = 4


@dataclass
class SymbolProfile:
    """Capabilities and economics for one symbol."""
    symbol: str
    digits: int = 5
    pip_value: float = 0.0001
    point: float = 0.00001
    trade_contract_size: float = 100_000.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level: int = 10        # min stop distance in points from entry price
    freeze_level: int = 0        # distance within which existing stop modifications are blocked
    spread: int = 0
    execution_mode: int = 0
    supported_fill_modes: List[int] = field(default_factory=list)
    preferred_fill_mode: int = FILL_IOC

    def pip_distance(self, price_a: float, price_b: float) -> float:
        if self.pip_value == 0:
            return 0.0
        return abs(price_a - price_b) / self.pip_value

    def points_to_price(self, points: int) -> float:
        return points * self.point

    def spread_pips(self) -> float:
        return self.spread * self.point / self.pip_value

    def min_stop_distance(self) -> float:
        """Minimum price distance (not pips) for SL/TP from entry."""
        return self.stops_level * self.point


class BrokerProfile:
    """
    Fetches and caches symbol profiles.
    Thread-safe — cache writes use a lock.
    """

    _FILL_PRIORITY = [FILL_IOC, FILL_FOK, FILL_RETURN]

    def __init__(self) -> None:
        self._cache: Dict[str, SymbolProfile] = {}
        self._lock  = threading.Lock()

    def get_symbol_profile(self, symbol: str) -> SymbolProfile:
        """Return cached profile, fetching from MT5 if not yet cached."""
        with self._lock:
            if symbol not in self._cache:
                self._cache[symbol] = self._fetch(symbol)
        return self._cache[symbol]

    def refresh(self, symbol: str) -> SymbolProfile:
        """
        Force refresh from MT5.
        FIX P1: Called by MT5Connector after every successful reconnect.
        """
        profile = self._fetch(symbol)
        with self._lock:
            self._cache[symbol] = profile
        log.info("Broker profile refreshed for %s", symbol)
        return profile

    def invalidate_all(self) -> None:
        """Clear entire cache — called on reconnect for all symbols."""
        with self._lock:
            self._cache.clear()
        log.info("Broker profile cache invalidated")

    def _fetch(self, symbol: str) -> SymbolProfile:
        info = connector.symbol_info(symbol)
        if info is None:
            log.warning("symbol_info unavailable for %s — using fallback", symbol)
            return self._fallback(symbol)

        filling_mode_bits = getattr(info, "filling_mode", 0)
        supported: List[int] = []
        if filling_mode_bits & _BIT_FOK:
            supported.append(FILL_FOK)
        if filling_mode_bits & _BIT_IOC:
            supported.append(FILL_IOC)
        if filling_mode_bits & _BIT_RETURN:
            supported.append(FILL_RETURN)

        # FIX P2: If broker reports no fill bits (filling_mode == 0),
        # default to trying all modes in priority order at execution time.
        # Do NOT assume RETURN is valid — it causes UNSUPPORTED FILLING MODE
        # on many ECN/STP brokers.
        if not supported:
            log.warning(
                "Symbol %s reports filling_mode=0 (no bits set). "
                "Will try all modes at execution time in priority order.",
                symbol,
            )
            supported = [FILL_IOC, FILL_FOK, FILL_RETURN]

        preferred = FILL_IOC  # safe default for ECN brokers
        for candidate in self._FILL_PRIORITY:
            if candidate in supported:
                preferred = candidate
                break

        digits    = getattr(info, "digits", 5)
        point     = getattr(info, "point", 10 ** -digits)
        pip_value = point * 10 if digits in (5, 3) else point

        profile = SymbolProfile(
            symbol=symbol,
            digits=digits,
            pip_value=pip_value,
            point=point,
            trade_contract_size=getattr(info, "trade_contract_size", 100_000.0),
            volume_min=getattr(info, "volume_min", 0.01),
            volume_max=getattr(info, "volume_max", 100.0),
            volume_step=getattr(info, "volume_step", 0.01),
            stops_level=getattr(info, "trade_stops_level", 10),
            freeze_level=getattr(info, "trade_freeze_level", 0),
            spread=getattr(info, "spread", 0),
            execution_mode=getattr(info, "trade_exemode", 0),
            supported_fill_modes=supported,
            preferred_fill_mode=preferred,
        )

        log.info(
            "Profile | %s digits=%d pip=%.5f fill_modes=%s preferred=%s "
            "stops_level=%d freeze_level=%d",
            symbol, profile.digits, profile.pip_value,
            [FILL_MODE_NAMES.get(m, m) for m in supported],
            FILL_MODE_NAMES.get(preferred, preferred),
            profile.stops_level, profile.freeze_level,
        )
        return profile

    @staticmethod
    def _fallback(symbol: str) -> SymbolProfile:
        log.warning("Using hardcoded fallback profile for %s", symbol)
        return SymbolProfile(
            symbol=symbol,
            supported_fill_modes=[FILL_IOC, FILL_FOK, FILL_RETURN],
            preferred_fill_mode=FILL_IOC,
        )


# Module-level singleton
broker_profile = BrokerProfile()
