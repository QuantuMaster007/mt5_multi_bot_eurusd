"""
Scalping Strategy

Logic:
  - Fast/slow EMA crossover for direction
  - Momentum filter (rate of change) to confirm thrust
  - ATR/spread ratio filter for cost awareness
  - Strict trade frequency and bar-gap limits
  - Blocked during high-spread and low-liquidity regimes
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, ClassVar, Deque, Dict, List, Optional

import pandas as pd

from core.constants import SIDE_BUY, SIDE_SELL, REGIME_HIGH_SPREAD, REGIME_LOW_LIQUIDITY
from core.logger import get_logger
from orchestration.plugin_validator import ConfigField
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

log = get_logger("strategy.scalping")

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


class ScalpingStrategy(BaseStrategy):

    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name        = "scalping",
        version     = "1.0.0",
        description = "EMA crossover scalper with momentum and spread/cost filters",
        symbols     = ["EURUSD"],
        timeframes  = ["M5"],
        regime_tags = ["trending", "ranging"],
        risk_profile= "high",
        magic_offset= 300,
    )

    CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
        "fast_ema":            ConfigField(int,   required=False, default=9,
                                            min_val=2, max_val=50,
                                            description="Fast EMA period"),
        "slow_ema":            ConfigField(int,   required=False, default=21,
                                            min_val=3, max_val=200,
                                            description="Slow EMA period"),
        "momentum_period":     ConfigField(int,   required=False, default=10,
                                            min_val=2, max_val=50,
                                            description="Momentum (rate-of-change) period in bars"),
        "momentum_threshold":  ConfigField(float, required=False, default=0.0002,
                                            min_val=0.0, max_val=0.01,
                                            description="Minimum momentum magnitude to allow entry"),
        "atr_period":          ConfigField(int,   required=False, default=14,
                                            min_val=5, max_val=50,
                                            description="ATR period"),
        "max_spread_atr_ratio":ConfigField(float, required=False, default=0.3,
                                            min_val=0.05, max_val=1.0,
                                            description="Skip entry if spread > ratio × ATR"),
        "stop_loss_pips":      ConfigField(float, required=True,  default=8.0,
                                            min_val=2.0, max_val=50.0,
                                            description="Stop-loss distance in pips"),
        "take_profit_pips":    ConfigField(float, required=True,  default=12.0,
                                            min_val=2.0, max_val=100.0,
                                            description="Take-profit distance in pips"),
        "max_trades_per_hour": ConfigField(int,   required=False, default=4,
                                            min_val=1, max_val=20,
                                            description="Hard cap on trades per 60-minute window"),
        "min_bars_between":    ConfigField(int,   required=False, default=3,
                                            min_val=0, max_val=50,
                                            description="Minimum bars between consecutive entries"),
    }

    def __init__(self) -> None:
        super().__init__()
        self._recent_trades: Deque[float] = deque(maxlen=100)
        self._last_cross_bar: int = -999

    # ─── Indicators ──────────────────────────────────────────────────────

    def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        if not TA_AVAILABLE or len(df) < 30:
            return {}

        fast  = self.cfg_int("fast_ema",        9)
        slow  = self.cfg_int("slow_ema",        21)
        mom_p = self.cfg_int("momentum_period", 10)
        atr_p = self.cfg_int("atr_period",      14)
        close = df["close"]

        return {
            "ema_fast": ta.trend.EMAIndicator(close, window=fast).ema_indicator(),
            "ema_slow": ta.trend.EMAIndicator(close, window=slow).ema_indicator(),
            "momentum": close.diff(mom_p),
            "atr":      ta.volatility.AverageTrueRange(
                df["high"], df["low"], close, window=atr_p
            ).average_true_range(),
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

        if not indicators:
            return None

        if REGIME_HIGH_SPREAD in regimes or REGIME_LOW_LIQUIDITY in regimes:
            return None

        if self.has_open_position():
            return None

        # Hourly frequency cap
        max_per_hour = self.cfg_int("max_trades_per_hour", 4)
        if self._hourly_count() >= max_per_hour:
            self._log.debug("Scalper hourly cap reached (%d)", max_per_hour)
            return None

        # Bar gap guard
        min_bars = self.cfg_int("min_bars_between", 3)
        if (len(df) - 1) - self._last_cross_bar < min_bars:
            return None

        pip_val   = 0.0001
        atr_val   = float(indicators["atr"].iloc[-1])
        fast_now  = float(indicators["ema_fast"].iloc[-1])
        fast_prev = float(indicators["ema_fast"].iloc[-2])
        slow_now  = float(indicators["ema_slow"].iloc[-1])
        slow_prev = float(indicators["ema_slow"].iloc[-2])
        mom_val   = float(indicators["momentum"].iloc[-1])
        mom_thresh= self.cfg_float("momentum_threshold", 0.0002)

        # Spread vs ATR filter
        max_ratio = self.cfg_float("max_spread_atr_ratio", 0.3)
        if atr_val > 0 and (spread_pips * pip_val) > atr_val * max_ratio:
            self._log.debug(
                "Scalper: spread %.2f pips too wide vs ATR %.5f",
                spread_pips, atr_val,
            )
            return None

        sl_pips = self.cfg_float("stop_loss_pips",    8.0)
        tp_pips = self.cfg_float("take_profit_pips", 12.0)
        sl_dist = sl_pips * pip_val

        # ── Bullish crossover ─────────────────────────────────────────────
        if (fast_prev <= slow_prev and fast_now > slow_now and mom_val > mom_thresh):
            entry = tick["ask"]
            lots  = self._size_lots(sl_dist)
            if lots <= 0:
                return None
            self._record_trade()
            self._last_cross_bar = len(df) - 1
            return TradeIntent(
                strategy    = self.metadata.name,
                symbol      = self._symbol,
                side        = SIDE_BUY,
                entry_price = entry,
                sl          = entry - sl_dist,
                tp          = entry + tp_pips * pip_val,
                volume      = lots,
                reason_code = "scalp_ema_cross_bull",
                notes       = f"fast={fast_now:.5f} slow={slow_now:.5f} mom={mom_val:.5f}",
            )

        # ── Bearish crossover ─────────────────────────────────────────────
        if (fast_prev >= slow_prev and fast_now < slow_now and mom_val < -mom_thresh):
            entry = tick["bid"]
            lots  = self._size_lots(sl_dist)
            if lots <= 0:
                return None
            self._record_trade()
            self._last_cross_bar = len(df) - 1
            return TradeIntent(
                strategy    = self.metadata.name,
                symbol      = self._symbol,
                side        = SIDE_SELL,
                entry_price = entry,
                sl          = entry + sl_dist,
                tp          = entry - tp_pips * pip_val,
                volume      = lots,
                reason_code = "scalp_ema_cross_bear",
                notes       = f"fast={fast_now:.5f} slow={slow_now:.5f} mom={mom_val:.5f}",
            )

        return None

    def manage_open_positions(self, df, indicators, profile, tick) -> None:
        pass

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _hourly_count(self) -> int:
        cutoff = time.monotonic() - 3600
        while self._recent_trades and self._recent_trades[0] < cutoff:
            self._recent_trades.popleft()
        return len(self._recent_trades)

    def _record_trade(self) -> None:
        self._recent_trades.append(time.monotonic())
