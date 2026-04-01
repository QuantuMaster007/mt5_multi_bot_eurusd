"""
Synthetic Fill Model

Models realistic paper-trade fill behaviour with configurable
slippage, partial fills, and latency simulation.

This is intentionally conservative — we want paper results to
*underestimate* performance, not overestimate it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class SyntheticFill:
    """Result of a simulated fill."""
    filled:       bool
    fill_price:   float
    fill_volume:  float
    slippage_pips: float
    reason:       str = ""


class SyntheticFillModel:
    """
    Simulates market-order fills with realistic friction.

    Args:
        avg_slippage_pips:   Mean slippage in pips (positive = adverse).
        slippage_std_pips:   Standard deviation of slippage distribution.
        partial_fill_prob:   Probability that the order is only partially
                             filled (set 0 for no partial fills).
        rejection_prob:      Probability of fill rejection (requote sim).
        pip_value:           1 pip in price units (0.0001 for EURUSD).
    """

    def __init__(
        self,
        avg_slippage_pips:   float = 0.3,
        slippage_std_pips:   float = 0.2,
        partial_fill_prob:   float = 0.0,
        rejection_prob:      float = 0.01,
        pip_value:           float = 0.0001,
    ) -> None:
        self._avg_slip   = avg_slippage_pips
        self._std_slip   = slippage_std_pips
        self._partial    = partial_fill_prob
        self._rejection  = rejection_prob
        self._pip        = pip_value

    def fill(
        self,
        side: str,
        requested_price: float,
        requested_volume: float,
    ) -> SyntheticFill:
        """
        Simulate a fill.

        Args:
            side:              "buy" or "sell"
            requested_price:   The ask (buy) or bid (sell) at signal time.
            requested_volume:  Requested lot size.

        Returns:
            SyntheticFill describing the outcome.
        """
        # Rejection
        if random.random() < self._rejection:
            return SyntheticFill(
                filled=False,
                fill_price=0.0,
                fill_volume=0.0,
                slippage_pips=0.0,
                reason="synthetic_requote",
            )

        # Slippage (adverse = positive for both directions)
        raw_slip = random.gauss(self._avg_slip, self._std_slip)
        slip_pips = max(0.0, raw_slip)  # floor at 0 — no favourable slippage by default
        slip_price = slip_pips * self._pip

        if side == "buy":
            fill_price = requested_price + slip_price
        else:
            fill_price = requested_price - slip_price

        # Partial fill
        if self._partial > 0 and random.random() < self._partial:
            fill_volume = round(requested_volume * random.uniform(0.5, 0.99), 2)
            fill_volume = max(0.01, fill_volume)
        else:
            fill_volume = requested_volume

        return SyntheticFill(
            filled=True,
            fill_price=round(fill_price, 5),
            fill_volume=fill_volume,
            slippage_pips=round(slip_pips, 2),
            reason="synthetic_fill",
        )


# Default model (conservative — no partial fills, minimal rejection)
default_fill_model = SyntheticFillModel(
    avg_slippage_pips=0.3,
    slippage_std_pips=0.2,
    partial_fill_prob=0.0,
    rejection_prob=0.01,
)
