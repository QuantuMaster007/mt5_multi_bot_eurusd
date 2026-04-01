"""
Momentum Breakout Strategy
==========================

A complete, working example of a new strategy added to the framework.
This file demonstrates everything a plugin developer needs to do.

Logic:
  - Detects price breakouts above/below a rolling N-bar high/low
  - Filters with ADX to confirm trend momentum is present
  - Requires ATR expansion (volatility confirms breakout energy)
  - Avoids re-entering in the same direction within M bars
  - Fixed risk-% position sizing via base class helper

Entry:
  Long:  close > highest_high[lookback] + buffer  AND  adx > threshold
  Short: close < lowest_low[lookback]  - buffer  AND  adx > threshold

Exit: MT5 SL/TP (ATR-based placement)

Designed for: trending / breakout regimes
Blocked by:   ranging, high_spread, low_liquidity
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, ClassVar, Dict, List, Optional

from core.constants import (
    SIDE_BUY, SIDE_SELL,
    REGIME_RANGING, REGIME_HIGH_SPREAD, REGIME_LOW_LIQUIDITY,
)
from core.logger import get_logger
from orchestration.plugin_validator import ConfigField
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

log = get_logger("strategy.momentum_breakout")

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    log.warning("ta library not installed — momentum_breakout indicators unavailable")


class MomentumBreakoutStrategy(BaseStrategy):
    """
    Breakout strategy confirmed by ADX momentum and ATR expansion.
    Compatible with trending and breakout regimes.
    """

    # ─── Metadata ─────────────────────────────────────────────────────────

    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name        = "momentum_breakout",
        version     = "1.0.0",
        description = "Rolling high/low breakout confirmed by ADX and ATR expansion",
        symbols     = ["EURUSD"],
        timeframes  = ["M30", "H1"],
        regime_tags = ["trending", "breakout"],
        risk_profile= "medium",
        author      = "framework_example",
        magic_offset= 400,
    )

    # ─── Config schema ─────────────────────────────────────────────────────
    #
    # Every field here is validated at startup against the YAML config.
    # If a required field is missing from the YAML, the plugin is rejected
    # with a clear error pointing to the missing field and its description.
    #
    CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
        # Breakout detection
        "lookback_candles":  ConfigField(
            int, required=True, default=20,
            min_val=5, max_val=200,
            description="Candles to look back for rolling high/low",
        ),
        "buffer_pips":       ConfigField(
            float, required=False, default=1.0,
            min_val=0.0, max_val=10.0,
            description="Extra pips beyond the high/low to confirm breakout",
        ),
        # ADX filter
        "adx_period":        ConfigField(
            int, required=False, default=14,
            min_val=5, max_val=50,
            description="ADX indicator period",
        ),
        "adx_threshold":     ConfigField(
            float, required=True, default=25.0,
            min_val=10.0, max_val=60.0,
            description="Minimum ADX value to allow entry (trend strength filter)",
        ),
        # ATR
        "atr_period":        ConfigField(
            int, required=False, default=14,
            min_val=5, max_val=50,
            description="ATR period",
        ),
        "atr_expansion_mult":ConfigField(
            float, required=False, default=1.2,
            min_val=0.5, max_val=5.0,
            description="Current ATR must be > mult × median ATR to confirm energy",
        ),
        # Risk
        "stop_atr_mult":     ConfigField(
            float, required=True, default=1.5,
            min_val=0.5, max_val=5.0,
            description="SL = entry ± stop_atr_mult × ATR",
        ),
        "take_profit_rr":    ConfigField(
            float, required=True, default=2.0,
            min_val=0.5, max_val=10.0,
            description="TP risk/reward ratio (TP = entry ± rr × SL_distance)",
        ),
        # Overtrading protection
        "min_bars_between":  ConfigField(
            int, required=False, default=5,
            min_val=0, max_val=50,
            description="Minimum bars between consecutive entries",
        ),
    }

    # ─── Init ─────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        super().__init__()
        self._last_entry_bar: int = -999   # track overtrading guard

    # ─── Indicators ───────────────────────────────────────────────────────

    def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        if not TA_AVAILABLE or len(df) < 60:
            return {}

        lookback = self.cfg_int("lookback_candles", 20)
        adx_p    = self.cfg_int("adx_period",       14)
        atr_p    = self.cfg_int("atr_period",        14)

        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        # Rolling channel: highest high and lowest low over lookback
        # Use shift(1) so the current bar is not included (avoids look-ahead)
        roll_high = high.shift(1).rolling(lookback).max()
        roll_low  = low.shift(1).rolling(lookback).min()

        # ADX
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=adx_p)
        adx     = adx_ind.adx()

        # ATR
        atr = ta.volatility.AverageTrueRange(
            high, low, close, window=atr_p
        ).average_true_range()

        # ATR expansion: compare current ATR vs. 50-bar rolling median
        atr_median = atr.rolling(50).median()

        return {
            "roll_high":  roll_high,
            "roll_low":   roll_low,
            "adx":        adx,
            "atr":        atr,
            "atr_median": atr_median,
        }

    # ─── Signal logic ─────────────────────────────────────────────────────

    def generate_signal(
        self,
        df: pd.DataFrame,
        indicators: Dict[str, Any],
        regimes: List[str],
        spread_pips: float,
        tick: dict,
    ) -> Optional[TradeIntent]:

        if not indicators:
            return None

        # ── Gate 1: regime compatibility ─────────────────────────────────
        # This strategy needs breakout energy; ranging markets generate
        # too many false breaks.
        blocked_regimes = {REGIME_RANGING, REGIME_HIGH_SPREAD, REGIME_LOW_LIQUIDITY}
        if any(r in blocked_regimes for r in regimes):
            self._log.debug("Blocked by regime: %s", regimes)
            return None

        # ── Gate 2: no position stacking ─────────────────────────────────
        if self.has_open_position():
            return None

        # ── Gate 3: overtrading protection ───────────────────────────────
        min_bars = self.cfg_int("min_bars_between", 5)
        bars_since_last = (len(df) - 1) - self._last_entry_bar
        if bars_since_last < min_bars:
            self._log.debug("Too soon since last entry (%d < %d bars)", bars_since_last, min_bars)
            return None

        # ── Read indicator values ─────────────────────────────────────────
        close     = float(df["close"].iloc[-1])
        roll_high = float(indicators["roll_high"].iloc[-1])
        roll_low  = float(indicators["roll_low"].iloc[-1])
        adx_val   = float(indicators["adx"].iloc[-1])
        atr_val   = float(indicators["atr"].iloc[-1])
        atr_med   = float(indicators["atr_median"].iloc[-1])

        # Skip if any indicator is NaN (not enough history)
        if any(np.isnan(v) for v in [roll_high, roll_low, adx_val, atr_val, atr_med]):
            return None

        # ── Gate 4: ADX momentum filter ──────────────────────────────────
        adx_threshold = self.cfg_float("adx_threshold", 25.0)
        if adx_val < adx_threshold:
            self._log.debug("ADX=%.1f < threshold %.1f — no momentum", adx_val, adx_threshold)
            return None

        # ── Gate 5: ATR expansion confirms breakout energy ───────────────
        atr_mult = self.cfg_float("atr_expansion_mult", 1.2)
        if atr_med > 0 and atr_val < atr_med * atr_mult:
            self._log.debug(
                "ATR=%.5f < %.1f × median %.5f — no expansion", atr_val, atr_mult, atr_med
            )
            return None

        # ── Read risk parameters ──────────────────────────────────────────
        buffer_pips  = self.cfg_float("buffer_pips",    1.0)
        stop_mult    = self.cfg_float("stop_atr_mult",  1.5)
        rr_ratio     = self.cfg_float("take_profit_rr", 2.0)
        pip_val      = 0.0001   # EURUSD
        buffer_price = buffer_pips * pip_val

        sl_distance  = atr_val * stop_mult

        # ── LONG: close breaks above rolling high ─────────────────────────
        if close > roll_high + buffer_price:
            entry = tick["ask"]
            sl    = entry - sl_distance
            tp    = entry + sl_distance * rr_ratio
            lots  = self._size_lots(sl_distance)

            if lots <= 0:
                self._log.debug("Long: lot size computed to 0 — skipping")
                return None

            self._last_entry_bar = len(df) - 1
            self._log.info(
                "BREAKOUT LONG | close=%.5f > hi=%.5f  adx=%.1f  atr=%.5f",
                close, roll_high, adx_val, atr_val,
            )
            return TradeIntent(
                strategy=self.metadata.name,
                symbol=self._symbol,
                side=SIDE_BUY,
                entry_price=entry,
                sl=sl,
                tp=tp,
                volume=lots,
                reason_code="mb_breakout_long",
                notes=(
                    f"close={close:.5f} roll_high={roll_high:.5f} "
                    f"adx={adx_val:.1f} atr={atr_val:.5f}"
                ),
            )

        # ── SHORT: close breaks below rolling low ─────────────────────────
        if close < roll_low - buffer_price:
            entry = tick["bid"]
            sl    = entry + sl_distance
            tp    = entry - sl_distance * rr_ratio
            lots  = self._size_lots(sl_distance)

            if lots <= 0:
                self._log.debug("Short: lot size computed to 0 — skipping")
                return None

            self._last_entry_bar = len(df) - 1
            self._log.info(
                "BREAKOUT SHORT | close=%.5f < lo=%.5f  adx=%.1f  atr=%.5f",
                close, roll_low, adx_val, atr_val,
            )
            return TradeIntent(
                strategy=self.metadata.name,
                symbol=self._symbol,
                side=SIDE_SELL,
                entry_price=entry,
                sl=sl,
                tp=tp,
                volume=lots,
                reason_code="mb_breakout_short",
                notes=(
                    f"close={close:.5f} roll_low={roll_low:.5f} "
                    f"adx={adx_val:.1f} atr={atr_val:.5f}"
                ),
            )

        return None

    def manage_open_positions(self, df, indicators, profile, tick) -> None:
        """No active management — MT5 handles SL/TP."""
        pass
