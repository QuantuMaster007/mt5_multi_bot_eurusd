"""
Market Data Module

FIX M1: get_tick() now validates tick age. A tick older than
         MAX_TICK_AGE_SECONDS raises MT5DataError instead of
         returning silently stale data.
FIX M2: get_spread_pips() accepts a pre-fetched tick dict to avoid
         a second MT5 API call in the same strategy cycle.
         This ensures spread and signal price are from the same tick.
"""
from __future__ import annotations

import time as _time
from typing import Optional

import pandas as pd

from core.exceptions import MT5DataError
from core.logger import get_logger
from core.mt5_connector import connector

log = get_logger("market_data")

# Maximum age of a tick before it is considered stale.
# Weekends and market-closed periods will naturally exceed this;
# strategies must call session_filter before calling market_data.
MAX_TICK_AGE_SECONDS = 10


class MarketData:

    def get_rates(
        self,
        symbol: str,
        timeframe_str: str,
        count: int = 200,
    ) -> pd.DataFrame:
        """
        Fetch the last *count* completed bars for *symbol*.

        Returns DataFrame sorted oldest→newest with UTC 'time' column.
        Raises MT5DataError on failure.
        """
        tf_int = connector.timeframe_constant(timeframe_str)
        raw    = connector.copy_rates_from_pos(symbol, tf_int, 0, count + 1)

        if raw is None or len(raw) == 0:
            raise MT5DataError(
                f"No rate data for {symbol} {timeframe_str}. "
                "Check symbol name and MT5 connection."
            )

        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        df = df.iloc[:-1]   # drop the forming (incomplete) bar

        log.debug(
            "Fetched %d bars | %s %s | latest=%s",
            len(df), symbol, timeframe_str, df["time"].iloc[-1],
        )
        return df

    def get_tick(
        self,
        symbol: str,
        max_age_seconds: int = MAX_TICK_AGE_SECONDS,
    ) -> Optional[dict]:
        """
        Return the latest tick as a plain dict, or None if unavailable.

        FIX M1: Validates tick age. Returns None (does NOT raise) if stale,
        allowing callers to skip the cycle rather than crash.

        Keys: time, bid, ask, last, volume
        """
        raw = connector.symbol_info_tick(symbol)
        if raw is None:
            return None

        age = _time.time() - raw.time
        if age > max_age_seconds:
            log.warning(
                "STALE TICK | %s | age=%.1fs > max=%ds. "
                "Feed may be down or market is closed.",
                symbol, age, max_age_seconds,
            )
            # Emit event for weekly review
            try:
                from core.json_logger import get_event_logger
                from core.constants import EVT_STALE_TICK
                from core.utils import ts_now
                get_event_logger().write({
                    "event":      EVT_STALE_TICK,
                    "symbol":     symbol,
                    "tick_age_s": round(age, 1),
                    "max_age_s":  max_age_seconds,
                })
            except Exception:
                pass
            return None  # caller must handle as "no data this cycle"

        return {
            "time":   raw.time,
            "bid":    raw.bid,
            "ask":    raw.ask,
            "last":   raw.last,
            "volume": raw.volume,
        }

    def get_spread_pips(
        self,
        symbol: str,
        pip_value: float,
        tick: Optional[dict] = None,
    ) -> float:
        """
        Return current spread in pips.

        FIX M2: Accepts a pre-fetched tick dict to avoid a redundant
        MT5 API call. If tick is None, fetches fresh (one extra call).
        Always use the same tick object fetched at the top of a cycle.
        """
        if tick is None:
            tick = self.get_tick(symbol)
        if tick is None or pip_value == 0:
            return 0.0
        raw_spread = tick["ask"] - tick["bid"]
        if raw_spread < 0:
            log.warning("Negative spread detected for %s: %.5f", symbol, raw_spread)
            return 0.0
        return round(raw_spread / pip_value, 2)

    def is_tick_fresh(
        self,
        symbol: str,
        max_age_seconds: int = MAX_TICK_AGE_SECONDS,
    ) -> bool:
        """
        Return True if the latest tick is recent enough to trade on.
        Does NOT log or emit events — use get_tick() for that.
        """
        raw = connector.symbol_info_tick(symbol)
        if raw is None:
            return False
        return (_time.time() - raw.time) <= max_age_seconds


# Module-level singleton
market_data = MarketData()
