"""
Range Trading Strategy

Logic:
  - Find swing high/low S/R zones over last N candles
  - Signal when price rejects from a zone with a confirmation candle
  - ATR-based stop placement
  - Avoids breakout, trend, low-liquidity regimes
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pandas as pd

from core.constants import (
    SIDE_BUY, SIDE_SELL,
    REGIME_STRONG_TREND, REGIME_BREAKOUT, REGIME_LOW_LIQUIDITY,
)
from core.logger import get_logger
from orchestration.plugin_validator import ConfigField
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

log = get_logger("strategy.range_trading")

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


class RangeTradingStrategy(BaseStrategy):

    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name        = "range_trading",
        version     = "1.0.0",
        description = "Support/Resistance zone rejection trading with ATR stops",
        symbols     = ["EURUSD"],
        timeframes  = ["H1", "H4"],
        regime_tags = ["ranging"],
        risk_profile= "medium",
        magic_offset= 200,
    )

    CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
        "sr_lookback_candles":   ConfigField(int,   required=False, default=50,
                                              min_val=10, max_val=500,
                                              description="Bars to look back for S/R zone detection"),
        "sr_zone_pips":          ConfigField(float, required=False, default=5.0,
                                              min_val=0.5, max_val=50.0,
                                              description="S/R zone width in pips"),
        "rejection_confirmation": ConfigField(bool, required=False, default=True,
                                              description="Require candle to close back inside zone"),
        "atr_period":            ConfigField(int,   required=False, default=14,
                                              min_val=5, max_val=50,
                                              description="ATR period for stop placement"),
        "stop_atr_mult":         ConfigField(float, required=False, default=1.0,
                                              min_val=0.3, max_val=5.0,
                                              description="SL = entry ± ATR × this multiplier"),
        "take_profit_atr_mult":  ConfigField(float, required=False, default=2.0,
                                              min_val=0.5, max_val=10.0,
                                              description="TP = entry ± ATR × this multiplier"),
    }

    # ─── Indicators ──────────────────────────────────────────────────────

    def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < 20:
            return {}

        atr_p    = self.cfg_int("atr_period", 14)
        lookback = self.cfg_int("sr_lookback_candles", 50)
        zone_pips= self.cfg_float("sr_zone_pips", 5.0)
        pip_val  = 0.0001

        atr = (
            ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=atr_p
            ).average_true_range()
            if TA_AVAILABLE
            else df["close"] * 0   # zero series fallback
        )

        support_levels, resistance_levels = self._find_sr_zones(
            df.tail(lookback), zone_pips * pip_val
        )

        return {
            "atr":        atr,
            "support":    support_levels,
            "resistance": resistance_levels,
        }

    # ─── Signal ──────────────────────────────────────────────────────────

    def generate_signal(
        self,
        df: pd.DataFrame,
        indicators: Dict[str, Any],
        regimes: List[str],
        spread_pips: float,
        tick: dict,
    ) -> Optional[TradeIntent]:

        if not indicators or not indicators.get("support"):
            return None

        blocked = {REGIME_STRONG_TREND, REGIME_BREAKOUT, REGIME_LOW_LIQUIDITY}
        if any(r in blocked for r in regimes):
            return None

        if self.has_open_position():
            return None

        close     = float(df["close"].iloc[-1])
        prev_low  = float(df["low"].iloc[-1])
        prev_high = float(df["high"].iloc[-1])
        atr_val   = float(indicators["atr"].iloc[-1])
        pip_val   = 0.0001
        zone_w    = self.cfg_float("sr_zone_pips", 5.0) * pip_val
        sl_mult   = self.cfg_float("stop_atr_mult", 1.0)
        tp_mult   = self.cfg_float("take_profit_atr_mult", 2.0)
        confirm   = self.cfg_bool("rejection_confirmation", True)
        sl_dist   = atr_val * sl_mult

        # ── Support rejection → Long ──────────────────────────────────────
        for sup in indicators["support"]:
            if abs(prev_low - sup) <= zone_w:
                if not confirm or close > sup:
                    entry = tick["ask"]
                    lots  = self._size_lots(sl_dist)
                    if lots <= 0:
                        continue
                    return TradeIntent(
                        strategy    = self.metadata.name,
                        symbol      = self._symbol,
                        side        = SIDE_BUY,
                        entry_price = entry,
                        sl          = entry - sl_dist,
                        tp          = entry + atr_val * tp_mult,
                        volume      = lots,
                        reason_code = "rt_support_rejection",
                        notes       = f"support={sup:.5f} close={close:.5f}",
                    )

        # ── Resistance rejection → Short ──────────────────────────────────
        for res in indicators["resistance"]:
            if abs(prev_high - res) <= zone_w:
                if not confirm or close < res:
                    entry = tick["bid"]
                    lots  = self._size_lots(sl_dist)
                    if lots <= 0:
                        continue
                    return TradeIntent(
                        strategy    = self.metadata.name,
                        symbol      = self._symbol,
                        side        = SIDE_SELL,
                        entry_price = entry,
                        sl          = entry + sl_dist,
                        tp          = entry - atr_val * tp_mult,
                        volume      = lots,
                        reason_code = "rt_resistance_rejection",
                        notes       = f"resistance={res:.5f} close={close:.5f}",
                    )

        return None

    def manage_open_positions(self, df, indicators, profile, tick) -> None:
        pass

    # ─── S/R Zone Detection ──────────────────────────────────────────────

    @staticmethod
    def _find_sr_zones(
        df: pd.DataFrame, zone_width: float
    ) -> Tuple[List[float], List[float]]:
        n     = 3
        highs = df["high"].values
        lows  = df["low"].values

        resistance: List[float] = []
        support: List[float]    = []

        for i in range(n, len(df) - n):
            if highs[i] == max(highs[i - n: i + n + 1]):
                resistance.append(float(highs[i]))
            if lows[i] == min(lows[i - n: i + n + 1]):
                support.append(float(lows[i]))

        resistance = _cluster_levels(resistance, zone_width)
        support    = _cluster_levels(support,    zone_width)
        return support, resistance


def _cluster_levels(levels: List[float], tolerance: float) -> List[float]:
    if not levels:
        return []
    levels = sorted(levels)
    clusters: List[float] = [levels[0]]
    for lvl in levels[1:]:
        if abs(lvl - clusters[-1]) > tolerance:
            clusters.append(lvl)
        else:
            clusters[-1] = (clusters[-1] + lvl) / 2
    return clusters
