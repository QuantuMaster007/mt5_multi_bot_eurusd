"""
Portfolio Manager

Prevents multiple strategies from blindly stacking the same
directional exposure on EURUSD.

Checks performed before approving any entry intent:
  - total open positions ≤ max_total_positions
  - per-strategy positions ≤ max_positions_per_strategy
  - gross lots ≤ max_gross_lots
  - net directional lots ≤ max_same_direction_lots
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core.constants import SIDE_BUY, SIDE_SELL, EVT_RISK_BLOCK
from core.exceptions import RiskBlockError
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.mt5_connector import connector
from core.settings import settings

log = get_logger("portfolio_manager")


class PortfolioManager:

    def __init__(self) -> None:
        self._cfg = settings.risk.get("portfolio", {})

    def check_can_open(
        self,
        symbol: str,
        side: str,
        volume: float,
        strategy_name: str,
        strategy_magic: int,
    ) -> None:
        """
        Raise RiskBlockError if opening this position would breach
        portfolio-level limits.
        """
        positions = list(connector.positions_get(symbol=symbol))

        total       = len(positions)
        max_total   = int(self._cfg.get("max_total_positions", 4))
        max_per_strat = int(self._cfg.get("max_positions_per_strategy", 1))
        max_gross   = float(self._cfg.get("max_gross_lots", 0.3))
        max_dir     = float(self._cfg.get("max_same_direction_lots", 0.2))

        # Count per-strategy open positions
        strat_count = sum(1 for p in positions if p.magic == strategy_magic)

        # Compute gross / net lots
        gross_lots, net_long, net_short = self._compute_exposure(positions)

        reason: str | None = None

        if total >= max_total:
            reason = f"Max total positions ({max_total}) already open"

        elif strat_count >= max_per_strat:
            reason = (
                f"Strategy {strategy_name} already has {strat_count} "
                f"open position(s) (max {max_per_strat})"
            )

        elif gross_lots + volume > max_gross:
            reason = (
                f"Gross lots {gross_lots:.3f}+{volume:.3f} > max {max_gross:.3f}"
            )

        elif side == SIDE_BUY and net_long + volume > max_dir:
            reason = (
                f"Net long {net_long:.3f}+{volume:.3f} > max {max_dir:.3f}"
            )

        elif side == SIDE_SELL and net_short + volume > max_dir:
            reason = (
                f"Net short {net_short:.3f}+{volume:.3f} > max {max_dir:.3f}"
            )

        if reason:
            log.warning("PORTFOLIO BLOCK [%s]: %s", strategy_name, reason)
            get_event_logger().write({
                "event":    EVT_RISK_BLOCK,
                "type":     "portfolio",
                "strategy": strategy_name,
                "symbol":   symbol,
                "reason":   reason,
            })
            raise RiskBlockError(reason)

    def current_exposure(self, symbol: str) -> Dict[str, float]:
        """Return a summary dict for monitoring/heartbeat."""
        positions = list(connector.positions_get(symbol=symbol))
        gross, net_long, net_short = self._compute_exposure(positions)
        return {
            "total_positions": len(positions),
            "gross_lots":      round(gross, 4),
            "net_long_lots":   round(net_long, 4),
            "net_short_lots":  round(net_short, 4),
        }

    @staticmethod
    def _compute_exposure(positions) -> Tuple[float, float, float]:
        """Returns (gross_lots, net_long_lots, net_short_lots)."""
        long_vol  = sum(p.volume for p in positions if p.type == 0)  # BUY
        short_vol = sum(p.volume for p in positions if p.type == 1)  # SELL
        return long_vol + short_vol, long_vol, short_vol


# Module-level singleton
portfolio_manager = PortfolioManager()
