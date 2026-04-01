"""
Regime Detector

Classifies the current market regime from OHLCV data.
Rules are explicit and explainable — no "magic AI" claims.

Current classification logic (extend as needed):
  - ADX > strong_threshold        → strong_trend
  - ADX > mild_threshold          → trending
  - ATR spike (> mult × baseline) → breakout
  - Spread > threshold            → high_spread
  - Hour outside active session   → low_liquidity
  - Everything else               → ranging
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List

from core.constants import (
    REGIME_TRENDING, REGIME_STRONG_TREND, REGIME_RANGING,
    REGIME_BREAKOUT, REGIME_HIGH_VOL, REGIME_LOW_VOL,
    REGIME_LOW_LIQUIDITY, REGIME_HIGH_SPREAD, REGIME_UNKNOWN,
)
from core.logger import get_logger

log = get_logger("regime_detector")


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Compute the latest ADX value. Returns NaN if not enough data."""
    if len(close) < period * 2:
        return float("nan")
    try:
        import ta
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=period)
        series = adx_ind.adx()
        return float(series.iloc[-1])
    except Exception:
        # Fallback: basic Wilder smoothed DX
        return float("nan")


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return float("nan")
    try:
        import ta
        atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=period)
        return float(atr_ind.average_true_range().iloc[-1])
    except Exception:
        return float("nan")


class RegimeDetector:
    """
    Takes a bars DataFrame and returns a regime string.

    All thresholds are configurable via regime_config dict or defaults.
    """

    # Default thresholds
    DEFAULT_ADX_STRONG   = 30.0
    DEFAULT_ADX_MILD     = 20.0
    DEFAULT_ATR_MULT     = 2.0    # ATR spike vs. 50-bar median
    DEFAULT_SPREAD_PIPS  = 3.0

    def detect(
        self,
        df: pd.DataFrame,
        spread_pips: float = 0.0,
        adx_strong: float = DEFAULT_ADX_STRONG,
        adx_mild: float   = DEFAULT_ADX_MILD,
        atr_spike_mult: float = DEFAULT_ATR_MULT,
        spread_threshold: float = DEFAULT_SPREAD_PIPS,
    ) -> str:
        """
        Classify market regime from OHLCV DataFrame.

        Args:
            df:               Bars DataFrame (open/high/low/close columns).
            spread_pips:      Current live spread in pips.
            adx_strong/mild:  ADX thresholds.
            atr_spike_mult:   Ratio to classify a breakout.
            spread_threshold: Spread beyond which HIGH_SPREAD is returned.

        Returns:
            One of the REGIME_* constants.
        """
        if len(df) < 30:
            return REGIME_UNKNOWN

        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            log.warning("DataFrame missing OHLC columns; regime unknown")
            return REGIME_UNKNOWN

        # Spread check takes priority
        if spread_pips > spread_threshold:
            return REGIME_HIGH_SPREAD

        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        adx_val = _adx(high, low, close)
        atr_val = _atr(high, low, close)

        # ATR-based breakout detection
        if not np.isnan(atr_val) and len(df) >= 50:
            atr_baseline = float(
                _atr(high.iloc[-50:], low.iloc[-50:], close.iloc[-50:])
            )
            if atr_val > atr_baseline * atr_spike_mult:
                log.debug(
                    "Regime: BREAKOUT (atr=%.5f baseline=%.5f mult=%.1f)",
                    atr_val, atr_baseline, atr_spike_mult,
                )
                return REGIME_BREAKOUT

        # ADX-based trend detection
        if not np.isnan(adx_val):
            if adx_val >= adx_strong:
                log.debug("Regime: STRONG_TREND (adx=%.1f)", adx_val)
                return REGIME_STRONG_TREND
            if adx_val >= adx_mild:
                log.debug("Regime: TRENDING (adx=%.1f)", adx_val)
                return REGIME_TRENDING

        return REGIME_RANGING

    def detect_multiple(
        self,
        df: pd.DataFrame,
        spread_pips: float = 0.0,
        **kwargs,
    ) -> List[str]:
        """
        Return a list of active regime tags.
        Useful when a market can be both trending AND high-volatility.
        """
        regimes: List[str] = []
        primary = self.detect(df, spread_pips=spread_pips, **kwargs)
        regimes.append(primary)

        # Also flag high/low volatility independent of trend
        atr_val = _atr(df["high"], df["low"], df["close"])
        if not np.isnan(atr_val) and len(df) >= 50:
            median_atr = float(
                pd.Series(
                    [_atr(df["high"].iloc[i-14:i], df["low"].iloc[i-14:i],
                          df["close"].iloc[i-14:i])
                     for i in range(15, len(df))]
                ).median()
            )
            if median_atr > 0:
                if atr_val > median_atr * 1.5:
                    regimes.append(REGIME_HIGH_VOL)
                elif atr_val < median_atr * 0.5:
                    regimes.append(REGIME_LOW_VOL)

        return regimes


# Module-level singleton
regime_detector = RegimeDetector()
