"""
Order Manager

Provides a unified view of all open MT5 positions and pending orders,
filtered by magic number so each strategy sees only its own trades.

Also provides helpers for building close/modify requests that correctly
derive fill mode and stops from the broker profile.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.broker_profile import broker_profile
from core.execution_engine import execution_engine, ExecutionResult
from core.logger import get_logger
from core.mt5_connector import connector

log = get_logger("order_manager")


class OrderManager:

    def get_positions(self, symbol: str, magic: int) -> List[Any]:
        """Return open MT5 positions for this symbol+magic."""
        all_pos = connector.positions_get(symbol=symbol)
        return [p for p in all_pos if p.magic == magic]

    def get_position_count(self, symbol: str, magic: int) -> int:
        return len(self.get_positions(symbol, magic))

    def close_all_positions(
        self, symbol: str, magic: int, comment: str = "close_all"
    ) -> List[ExecutionResult]:
        """Close every open position for symbol+magic. Returns results list."""
        positions = self.get_positions(symbol, magic)
        results = []
        for pos in positions:
            result = execution_engine.close_position(
                symbol=symbol,
                position_ticket=pos.ticket,
                volume=pos.volume,
                magic=magic,
                comment=comment,
            )
            results.append(result)
            if result.success:
                log.info("Closed position ticket=%d symbol=%s", pos.ticket, symbol)
            else:
                log.warning(
                    "Failed to close position ticket=%d: %s",
                    pos.ticket, result.error_description,
                )
        return results

    def position_pnl(self, symbol: str, magic: int) -> Tuple[float, float]:
        """
        Return (unrealised_pnl_sum, volume_sum) for all open positions.
        pnl is in account currency as reported by MT5.
        """
        positions = self.get_positions(symbol, magic)
        total_pnl = sum(getattr(p, "profit", 0.0) for p in positions)
        total_vol = sum(p.volume for p in positions)
        return total_pnl, total_vol

    def has_open_position(self, symbol: str, magic: int) -> bool:
        return self.get_position_count(symbol, magic) > 0


# Module-level singleton
order_manager = OrderManager()
