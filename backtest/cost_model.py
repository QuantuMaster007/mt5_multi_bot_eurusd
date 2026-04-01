"""
Cost Model for Backtesting

Estimates the realistic total cost of a trade:
  - spread cost (half-spread on entry, half on exit for market orders)
  - estimated slippage
  - commission (if broker charges per lot)

All values returned in account-currency units (assumes USD account).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostEstimate:
    spread_cost:     float = 0.0
    slippage_cost:   float = 0.0
    commission:      float = 0.0
    total:           float = 0.0


class CostModel:
    """
    Simple cost model for bar-data backtesting.

    Assumptions:
      - Market orders are filled at close of signal bar + spread/2
      - Slippage is a flat addition in pips
      - Commission is per lot round-turn

    These are conservative estimates. Real costs may be higher or lower
    depending on broker, time-of-day, and market conditions.
    """

    def __init__(
        self,
        spread_pips: float = 1.0,
        slippage_pips: float = 0.5,
        commission_per_lot: float = 7.0,   # USD round-turn per standard lot
        pip_value_per_lot: float = 10.0,   # USD per pip per standard lot (EURUSD/USD account)
    ) -> None:
        self.spread_pips         = spread_pips
        self.slippage_pips       = slippage_pips
        self.commission_per_lot  = commission_per_lot
        self.pip_value_per_lot   = pip_value_per_lot

    def estimate(self, volume_lots: float) -> CostEstimate:
        """
        Compute total round-trip cost for *volume_lots*.

        Returns CostEstimate with individual components and total.
        """
        spread_cost   = self.spread_pips    * self.pip_value_per_lot * volume_lots
        slippage_cost = self.slippage_pips  * self.pip_value_per_lot * volume_lots
        commission    = self.commission_per_lot * volume_lots

        total = spread_cost + slippage_cost + commission
        return CostEstimate(
            spread_cost=round(spread_cost, 4),
            slippage_cost=round(slippage_cost, 4),
            commission=round(commission, 4),
            total=round(total, 4),
        )

    def net_pnl(self, gross_pnl: float, volume_lots: float) -> float:
        """Subtract round-trip costs from gross P&L."""
        cost = self.estimate(volume_lots)
        return gross_pnl - cost.total
