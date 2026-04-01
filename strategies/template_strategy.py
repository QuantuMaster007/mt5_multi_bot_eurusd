"""
TEMPLATE STRATEGY
=================

Copy this file to strategies/my_strategy_name.py and fill in:
  1. Rename the class (MyTemplateStrategy → your name)
  2. Fill in the metadata fields
  3. Define CONFIG_SCHEMA for your indicator parameters
  4. Implement prepare_indicators()
  5. Implement generate_signal()
  6. Copy config/strategies/template.yaml → config/strategies/my_strategy_name.yaml
  7. Restart the orchestrator

The file is intentionally verbose so you have examples of every pattern.
Delete what you don't need.

NOTE: This file is automatically skipped by the plugin loader because
its name starts with "template". Rename it to make it discoverable.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

# Import the building blocks
from core.constants import SIDE_BUY, SIDE_SELL, REGIME_STRONG_TREND, REGIME_BREAKOUT
from core.logger import get_logger
from orchestration.plugin_validator import ConfigField
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

log = get_logger("strategy.my_template")

# Optional — import ta for indicators (pip install ta)
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


class MyTemplateStrategy(BaseStrategy):
    """
    Replace this docstring with a description of your strategy's logic.

    What regime does it work in?
    What indicators does it use?
    What is the entry and exit rationale?
    """

    # ─── REQUIRED: Metadata ───────────────────────────────────────────────
    #
    # name:        Unique snake_case id. Must match your config YAML filename.
    # version:     Semver string. Increment when you change signal logic.
    # description: One sentence describing the strategy.
    # symbols:     Which symbols this strategy supports.
    # timeframes:  Which MT5 timeframes it can run on.
    # regime_tags: Which market regimes it is COMPATIBLE with.
    #              Policy engine uses this for gating.
    # magic_offset:Unique integer 1-9999. Must not clash with other strategies.
    #              Also set in your config YAML — config takes precedence.
    #
    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name        = "my_template",         # ← CHANGE THIS
        version     = "1.0.0",
        description = "Describe what this strategy does in one sentence.",
        symbols     = ["EURUSD"],
        timeframes  = ["M15"],               # ← choose your timeframe
        regime_tags = ["ranging"],           # ← regimes you work in
        risk_profile= "medium",             # low | medium | high
        author      = "your_name",
        magic_offset= 400,                   # ← pick a unique number
    )

    # ─── RECOMMENDED: Config schema ───────────────────────────────────────
    #
    # Defines the parameters your strategy reads from its YAML config.
    # The plugin loader validates the YAML against this schema at startup.
    #
    # ConfigField args:
    #   type        Python type to coerce to (int, float, str, bool)
    #   required    If True, the field must be present in the YAML
    #   default     Value used when the field is absent (if not required)
    #   min_val     Minimum numeric value (optional)
    #   max_val     Maximum numeric value (optional)
    #   choices     List of allowed values (optional)
    #   description Human-readable explanation shown in validation errors
    #
    CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
        # Indicator parameters
        "fast_ema":        ConfigField(int,   required=True,  default=9,
                                       min_val=2, max_val=200,
                                       description="Fast EMA period"),
        "slow_ema":        ConfigField(int,   required=True,  default=21,
                                       min_val=2, max_val=500,
                                       description="Slow EMA period"),
        "rsi_period":      ConfigField(int,   required=False, default=14,
                                       min_val=2, max_val=50,
                                       description="RSI period"),
        # Risk parameters
        "stop_loss_pips":  ConfigField(float, required=True,  default=15.0,
                                       min_val=2.0, max_val=200.0,
                                       description="Stop-loss distance in pips"),
        "take_profit_pips":ConfigField(float, required=True,  default=25.0,
                                       min_val=2.0, max_val=500.0,
                                       description="Take-profit distance in pips"),
        # Filter parameters
        "max_spread_pips": ConfigField(float, required=False, default=2.0,
                                       min_val=0.1, max_val=10.0,
                                       description="Skip trade if spread > this"),
    }

    # ─── OPTIONAL: Class-level state ──────────────────────────────────────
    #
    # Add any instance variables your strategy needs to track between cycles.
    # Do NOT use module-level globals — use self._* attributes.
    #
    def __init__(self) -> None:
        super().__init__()
        # Example: track the bar index of the last trade to avoid overtrading
        self._last_signal_bar: int = -999

    # ─── RECOMMENDED: prepare_indicators() ───────────────────────────────
    #
    # Compute all indicator values from the OHLCV DataFrame.
    # Return a dict — whatever you return here is passed to generate_signal().
    # Separate from generate_signal() so indicators can be unit-tested.
    #
    def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        if not TA_AVAILABLE or len(df) < 50:
            return {}

        close = df["close"]

        # Read indicator parameters from config using the typed helpers
        fast = self.cfg_int("fast_ema", 9)
        slow = self.cfg_int("slow_ema", 21)
        rsi_p = self.cfg_int("rsi_period", 14)

        ema_fast = ta.trend.EMAIndicator(close, window=fast).ema_indicator()
        ema_slow = ta.trend.EMAIndicator(close, window=slow).ema_indicator()
        rsi      = ta.momentum.RSIIndicator(close, window=rsi_p).rsi()
        atr      = ta.volatility.AverageTrueRange(
            df["high"], df["low"], close, window=14
        ).average_true_range()

        return {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi":      rsi,
            "atr":      atr,
        }

    # ─── REQUIRED: generate_signal() ─────────────────────────────────────
    #
    # This is where your entry logic lives.
    # Return a TradeIntent if conditions are met, None otherwise.
    #
    # The base class handles:
    #   - Session filter  (before this is called)
    #   - Policy gating   (before this is called)
    #   - Risk sizing     (use self._size_lots() below)
    #   - Order submission(after you return the intent)
    #
    # You are responsible for:
    #   - Indicator conditions
    #   - Max position count check  (use self.has_open_position())
    #   - Strategy-specific spread filter
    #   - SL/TP placement
    #   - Volume calculation        (use self._size_lots())
    #
    def generate_signal(
        self,
        df: pd.DataFrame,
        indicators: Dict[str, Any],
        regimes: List[str],
        spread_pips: float,
        tick: dict,
    ) -> Optional[TradeIntent]:

        # Guard: no indicators (not enough bars or ta not installed)
        if not indicators:
            return None

        # Guard: no position stacking
        if self.has_open_position():
            return None

        # Guard: strategy-specific spread limit
        max_spread = self.cfg_float("max_spread_pips", 2.0)
        if spread_pips > max_spread:
            self._log.debug("Spread %.2f > limit %.2f — skip", spread_pips, max_spread)
            return None

        # Guard: regime incompatibility (example — skip during breakouts)
        if REGIME_BREAKOUT in regimes:
            return None

        # Read config values
        sl_pips = self.cfg_float("stop_loss_pips",   15.0)
        tp_pips = self.cfg_float("take_profit_pips", 25.0)
        pip_val = 0.0001  # EURUSD; derive from broker_profile for multi-symbol

        # Read the latest indicator values
        ema_fast_now  = float(indicators["ema_fast"].iloc[-1])
        ema_fast_prev = float(indicators["ema_fast"].iloc[-2])
        ema_slow_now  = float(indicators["ema_slow"].iloc[-1])
        ema_slow_prev = float(indicators["ema_slow"].iloc[-2])
        rsi_val       = float(indicators["rsi"].iloc[-1])

        # ── Example: bullish EMA crossover ───────────────────────────────
        bull_cross = (ema_fast_prev <= ema_slow_prev) and (ema_fast_now > ema_slow_now)
        if bull_cross and rsi_val < 60:
            entry = tick["ask"]
            sl    = entry - sl_pips * pip_val
            tp    = entry + tp_pips * pip_val
            lots  = self._size_lots(sl_pips * pip_val)
            if lots <= 0:
                return None
            self._last_signal_bar = len(df) - 1
            return TradeIntent(
                strategy=self.metadata.name,
                symbol=self._symbol,
                side=SIDE_BUY,
                entry_price=entry,
                sl=sl,
                tp=tp,
                volume=lots,
                reason_code="ema_cross_bull",
                notes=f"fast={ema_fast_now:.5f} slow={ema_slow_now:.5f} rsi={rsi_val:.1f}",
            )

        # ── Example: bearish EMA crossover ───────────────────────────────
        bear_cross = (ema_fast_prev >= ema_slow_prev) and (ema_fast_now < ema_slow_now)
        if bear_cross and rsi_val > 40:
            entry = tick["bid"]
            sl    = entry + sl_pips * pip_val
            tp    = entry - tp_pips * pip_val
            lots  = self._size_lots(sl_pips * pip_val)
            if lots <= 0:
                return None
            self._last_signal_bar = len(df) - 1
            return TradeIntent(
                strategy=self.metadata.name,
                symbol=self._symbol,
                side=SIDE_SELL,
                entry_price=entry,
                sl=sl,
                tp=tp,
                volume=lots,
                reason_code="ema_cross_bear",
                notes=f"fast={ema_fast_now:.5f} slow={ema_slow_now:.5f} rsi={rsi_val:.1f}",
            )

        return None

    # ─── OPTIONAL: manage_open_positions() ───────────────────────────────
    #
    # Called every cycle BEFORE generate_signal().
    # Override this to implement trailing stops, time exits, etc.
    # Leave it empty (pass) to rely entirely on MT5 SL/TP.
    #
    def manage_open_positions(self, df, indicators, profile, tick) -> None:
        pass  # No active management — MT5 handles SL/TP
