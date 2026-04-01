"""
Mean Reversion Strategy

Signals:
  Long:  price touches lower BB + RSI oversold + above EMA + ATR filter
  Short: price touches upper BB + RSI overbought + below EMA + ATR filter

Blocked by: strong_trend, breakout regime, excessive spread
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from core.constants import SIDE_BUY, SIDE_SELL, REGIME_STRONG_TREND, REGIME_BREAKOUT
from core.logger import get_logger
from orchestration.plugin_validator import ConfigField
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

log = get_logger("strategy.mean_reversion")

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    log.warning("ta library not installed — MeanReversion indicators disabled")


class MeanReversionStrategy(BaseStrategy):

    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name        = "mean_reversion",
        version     = "1.0.0",
        description = "Bollinger Band + RSI mean reversion with EMA trend filter",
        symbols     = ["EURUSD"],
        timeframes  = ["M15", "M30", "H1"],
        regime_tags = ["ranging"],
        risk_profile= "medium",
        magic_offset= 100,
    )

    CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
        "bb_period":          ConfigField(int,   required=False, default=20,
                                           min_val=5, max_val=200,
                                           description="Bollinger Band period"),
        "bb_std":             ConfigField(float, required=False, default=2.0,
                                           min_val=0.5, max_val=5.0,
                                           description="Bollinger Band standard deviation multiplier"),
        "rsi_period":         ConfigField(int,   required=False, default=14,
                                           min_val=2, max_val=50,
                                           description="RSI period"),
        "rsi_oversold":       ConfigField(float, required=False, default=35.0,
                                           min_val=10.0, max_val=50.0,
                                           description="RSI level considered oversold (long signal)"),
        "rsi_overbought":     ConfigField(float, required=False, default=65.0,
                                           min_val=50.0, max_val=90.0,
                                           description="RSI level considered overbought (short signal)"),
        "ema_period":         ConfigField(int,   required=False, default=50,
                                           min_val=10, max_val=500,
                                           description="EMA period for trend filter"),
        "ema_trend_filter":   ConfigField(bool,  required=False, default=True,
                                           description="Enable EMA trend filter (long above EMA, short below)"),
        "atr_period":         ConfigField(int,   required=False, default=14,
                                           min_val=5, max_val=50,
                                           description="ATR period for volatility filter and sizing"),
        "atr_min_pips":       ConfigField(float, required=False, default=4.0,
                                           min_val=0.0, max_val=50.0,
                                           description="Skip signal if ATR in pips < this (quiet market)"),
        "stop_loss_atr_mult": ConfigField(float, required=False, default=1.5,
                                           min_val=0.3, max_val=5.0,
                                           description="SL = entry ± ATR × this multiplier"),
        "take_profit_atr_mult":ConfigField(float, required=False, default=2.5,
                                           min_val=0.5, max_val=10.0,
                                           description="TP = entry ± ATR × this multiplier"),
    }

    # ─── Indicators ──────────────────────────────────────────────────────

    def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        if not TA_AVAILABLE or len(df) < 50:
            return {}

        bb_period = self.cfg_int("bb_period", 20)
        bb_std    = self.cfg_float("bb_std",    2.0)
        rsi_p     = self.cfg_int("rsi_period", 14)
        ema_p     = self.cfg_int("ema_period", 50)
        atr_p     = self.cfg_int("atr_period", 14)
        close     = df["close"]

        bb   = ta.volatility.BollingerBands(close, window=bb_period, window_dev=bb_std)
        rsi  = ta.momentum.RSIIndicator(close, window=rsi_p).rsi()
        ema  = ta.trend.EMAIndicator(close, window=ema_p).ema_indicator()
        atr  = ta.volatility.AverageTrueRange(
            df["high"], df["low"], close, window=atr_p
        ).average_true_range()

        return {
            "bb_upper": bb.bollinger_hband(),
            "bb_lower": bb.bollinger_lband(),
            "bb_mid":   bb.bollinger_mavg(),
            "rsi":      rsi,
            "ema":      ema,
            "atr":      atr,
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

        # Regime gate
        if REGIME_STRONG_TREND in regimes or REGIME_BREAKOUT in regimes:
            self._log.debug("MR blocked by regime: %s", regimes)
            return None

        if self.has_open_position():
            return None

        # ATR filter (avoid dead, quiet markets)
        atr_val  = float(indicators["atr"].iloc[-1])
        pip_val  = 0.0001
        atr_pips = atr_val / pip_val
        if atr_pips < self.cfg_float("atr_min_pips", 4.0):
            self._log.debug("MR: ATR %.1f pips below minimum — skipping", atr_pips)
            return None

        close          = float(df["close"].iloc[-1])
        bb_lower       = float(indicators["bb_lower"].iloc[-1])
        bb_upper       = float(indicators["bb_upper"].iloc[-1])
        rsi_val        = float(indicators["rsi"].iloc[-1])
        ema_val        = float(indicators["ema"].iloc[-1])
        rsi_oversold   = self.cfg_float("rsi_oversold",   35.0)
        rsi_overbought = self.cfg_float("rsi_overbought", 65.0)
        ema_filter     = self.cfg_bool("ema_trend_filter", True)
        sl_mult        = self.cfg_float("stop_loss_atr_mult",    1.5)
        tp_mult        = self.cfg_float("take_profit_atr_mult",  2.5)
        sl_dist        = atr_val * sl_mult

        # ── Long ─────────────────────────────────────────────────────────
        if (close <= bb_lower
                and rsi_val <= rsi_oversold
                and (not ema_filter or close > ema_val)):
            entry = tick["ask"]
            lots  = self._size_lots(sl_dist)
            if lots <= 0:
                return None
            return TradeIntent(
                strategy    = self.metadata.name,
                symbol      = self._symbol,
                side        = SIDE_BUY,
                entry_price = entry,
                sl          = entry - sl_dist,
                tp          = entry + atr_val * tp_mult,
                volume      = lots,
                reason_code = "mr_bb_rsi_long",
                notes       = f"rsi={rsi_val:.1f} bb_lower={bb_lower:.5f} atr={atr_val:.5f}",
            )

        # ── Short ────────────────────────────────────────────────────────
        if (close >= bb_upper
                and rsi_val >= rsi_overbought
                and (not ema_filter or close < ema_val)):
            entry = tick["bid"]
            lots  = self._size_lots(sl_dist)
            if lots <= 0:
                return None
            return TradeIntent(
                strategy    = self.metadata.name,
                symbol      = self._symbol,
                side        = SIDE_SELL,
                entry_price = entry,
                sl          = entry + sl_dist,
                tp          = entry - atr_val * tp_mult,
                volume      = lots,
                reason_code = "mr_bb_rsi_short",
                notes       = f"rsi={rsi_val:.1f} bb_upper={bb_upper:.5f} atr={atr_val:.5f}",
            )

        return None

    def manage_open_positions(self, df, indicators, profile, tick) -> None:
        pass
