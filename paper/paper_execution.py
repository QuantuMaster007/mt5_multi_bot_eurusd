"""
Paper Execution Engine

FIX PP1: _close_position() now calls risk_manager.record_trade_close(pnl)
          so daily loss limits, drawdown guards, and consecutive loss
          pausing all work correctly in paper mode.
FIX PP2: metrics_store.record_trade() is called on every paper close
          so weekly reports reflect paper session results.
FIX PP3: update_positions() is called automatically by the orchestrator's
          supervision loop via paper_engine.update_positions(). A new
          module-level hook allows the orchestrator to register it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.constants import SIDE_BUY, SIDE_SELL
from core.json_logger import get_trade_logger
from core.logger import get_logger
from core.market_data import market_data
from core.metrics_store import metrics_store
from core.order_validator import OrderIntent, order_validator
from core.broker_profile import broker_profile
from core.risk_manager import risk_manager
from core.utils import ts_now

log = get_logger("paper_execution")


@dataclass
class PaperPosition:
    ticket:      int
    symbol:      str
    side:        str
    volume:      float
    open_price:  float
    sl:          float
    tp:          float
    magic:       int
    strategy:    str
    open_time:   str = field(default_factory=ts_now)
    pnl:         float = 0.0
    closed:      bool = False
    close_price: float = 0.0
    close_time:  str = ""
    close_reason: str = ""


@dataclass
class PaperResult:
    success:          bool
    ticket:           int   = 0
    fill_price:       float = 0.0
    volume:           float = 0.0
    order_id:         int   = 0
    volume_filled:    float = 0.0
    comment:          str   = ""
    error_description: str  = ""
    category:         str   = ""


class PaperExecutionEngine:
    """
    Simulates market-order fills at current bid/ask with full
    risk_manager and metrics integration so paper mode is
    statistically equivalent to demo mode for review purposes.
    """

    def __init__(self) -> None:
        self._positions: Dict[int, PaperPosition] = {}
        self._next_ticket = 100001
        self._tl = None

    @property
    def _trade_log(self):
        if self._tl is None:
            self._tl = get_trade_logger()
        return self._tl

    def send_market_order(self, intent: OrderIntent, strategy: str = "") -> PaperResult:
        """Simulate a market fill at current ask/bid."""
        profile   = broker_profile.get_symbol_profile(intent.symbol)
        tick      = market_data.get_tick(intent.symbol)
        if tick is None:
            return PaperResult(
                success=False,
                error_description="No tick data",
                category="data_error",
            )

        fill_price = tick["ask"] if intent.side == SIDE_BUY else tick["bid"]

        try:
            intent = order_validator.validate(intent, profile, fill_price)
        except Exception as exc:
            return PaperResult(
                success=False,
                error_description=str(exc),
                category="validation_error",
            )

        ticket = self._next_ticket
        self._next_ticket += 1

        pos = PaperPosition(
            ticket=ticket, symbol=intent.symbol, side=intent.side,
            volume=intent.volume, open_price=fill_price,
            sl=intent.sl, tp=intent.tp,
            magic=intent.magic, strategy=strategy,
        )
        self._positions[ticket] = pos

        self._trade_log.write({
            "event":    "trade_open",
            "mode":     "paper",
            "strategy": strategy,
            "ticket":   ticket,
            "symbol":   intent.symbol,
            "side":     intent.side,
            "volume":   intent.volume,
            "price":    fill_price,
            "sl":       intent.sl,
            "tp":       intent.tp,
            "magic":    intent.magic,
            "ts":       ts_now(),
        })

        log.info(
            "PAPER OPEN | %s %s vol=%.2f @ %.5f ticket=%d",
            intent.side.upper(), intent.symbol, intent.volume, fill_price, ticket,
        )

        return PaperResult(
            success=True,
            ticket=ticket,
            fill_price=fill_price,
            volume=intent.volume,
            order_id=ticket,
            volume_filled=intent.volume,
        )

    def update_positions(self) -> List[PaperPosition]:
        """
        Check all open paper positions against current prices.
        Close those that hit SL or TP.

        FIX PP3: This must be called on every orchestrator tick.
        In paper mode, the orchestrator calls this directly because
        strategies do not submit closes — MT5 normally handles SL/TP.
        Returns list of newly closed positions.
        """
        closed_now: List[PaperPosition] = []

        for ticket, pos in list(self._positions.items()):
            if pos.closed:
                continue

            tick = market_data.get_tick(pos.symbol)
            if tick is None:
                continue

            profile    = broker_profile.get_symbol_profile(pos.symbol)
            check_price = tick["bid"] if pos.side == SIDE_BUY else tick["ask"]

            close_reason: Optional[str] = None

            if pos.side == SIDE_BUY:
                if pos.sl and check_price <= pos.sl:
                    close_reason = "sl_hit"
                elif pos.tp and check_price >= pos.tp:
                    close_reason = "tp_hit"
            else:
                if pos.sl and check_price >= pos.sl:
                    close_reason = "sl_hit"
                elif pos.tp and check_price <= pos.tp:
                    close_reason = "tp_hit"

            if close_reason:
                self._close_position(pos, check_price, close_reason, profile)
                closed_now.append(pos)

        return closed_now

    def get_open_positions(self, magic: Optional[int] = None) -> List[PaperPosition]:
        return [
            p for p in self._positions.values()
            if not p.closed and (magic is None or p.magic == magic)
        ]

    def _close_position(
        self,
        pos: PaperPosition,
        close_price: float,
        reason: str,
        profile,
    ) -> None:
        pos.closed      = True
        pos.close_price = close_price
        pos.close_time  = ts_now()
        pos.close_reason = reason

        pnl_pips = self._compute_pnl_pips(pos, close_price, profile.pip_value)
        # pnl in account currency (assumes USD account; correct for EURUSD)
        pos.pnl  = pnl_pips * profile.pip_value * pos.volume * profile.trade_contract_size

        # FIX PP1: Notify risk manager so daily loss / drawdown guards work in paper mode
        risk_manager.record_trade_close(pos.pnl)

        # FIX PP2: Record in metrics store so weekly reports reflect paper results
        # hold_minutes approximated as 0 here (open_time parsing would give exact value)
        try:
            from datetime import datetime, timezone
            open_dt  = datetime.fromisoformat(pos.open_time)
            close_dt = datetime.fromisoformat(pos.close_time)
            hold_minutes = (close_dt - open_dt).total_seconds() / 60
        except Exception:
            hold_minutes = 0.0

        metrics_store.record_trade(
            strategy=pos.strategy,
            pnl=pos.pnl,
            hold_minutes=hold_minutes,
        )

        self._trade_log.write({
            "event":        "trade_close",
            "mode":         "paper",
            "strategy":     pos.strategy,
            "ticket":       pos.ticket,
            "symbol":       pos.symbol,
            "side":         pos.side,
            "volume":       pos.volume,
            "open_price":   pos.open_price,
            "close_price":  close_price,
            "pnl":          round(pos.pnl, 2),
            "pnl_pips":     round(pnl_pips, 1),
            "close_reason": reason,
            "open_time":    pos.open_time,
            "close_time":   pos.close_time,
            "ts":           ts_now(),
        })

        log.info(
            "PAPER CLOSE | %s %s @ %.5f | pnl=%.2f pips=%.1f [%s]",
            pos.symbol, pos.side.upper(), close_price,
            pos.pnl, pnl_pips, reason,
        )

    @staticmethod
    def _compute_pnl_pips(pos: PaperPosition, current_price: float, pip_val: float) -> float:
        if pip_val <= 0:
            return 0.0
        if pos.side == SIDE_BUY:
            return (current_price - pos.open_price) / pip_val
        else:
            return (pos.open_price - current_price) / pip_val


# Module-level singleton
paper_engine = PaperExecutionEngine()
