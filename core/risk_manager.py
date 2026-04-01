"""
Risk Manager

Responsibilities:
  - Compute lot size from risk fraction + stop-loss distance
  - Enforce daily/weekly loss limits
  - Enforce drawdown limits
  - Enforce consecutive loss limits
  - Enforce per-session / per-hour trade frequency limits
  - Enforce spread-based trade rejection

All blocking decisions emit events via the event logger.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, date, timezone
from typing import Deque, Dict, Optional

from core.broker_profile import SymbolProfile
from core.constants import EVT_RISK_BLOCK
from core.exceptions import (
    RiskBlockError, DrawdownLimitError, DailyLossLimitError
)
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.settings import settings
from core.utils import safe_divide

log = get_logger("risk_manager")


class RiskManager:
    """
    Stateful risk guard.

    Call check_can_trade() before submitting any order.
    Call record_trade_open() when an order fills.
    Call record_trade_close() when a position is closed.
    """

    def __init__(self) -> None:
        self._cfg_risk  = settings.risk.get("risk", settings.risk)
        self._cfg_port  = settings.risk.get("portfolio", {})
        self._equity_hwm: float = 0.0
        self._daily_loss: float = 0.0
        self._weekly_loss: float = 0.0
        self._last_reset_date: Optional[date] = None
        self._consecutive_losses: int = 0
        self._trade_times: Deque[float] = deque(maxlen=500)
        self._hourly_trades: Deque[float] = deque(maxlen=100)
        self._session_trades: int = 0
        self._cooldown_until: float = 0.0
        self._el = None  # lazy

    @property
    def _event_log(self):
        if self._el is None:
            self._el = get_event_logger()
        return self._el

    # ─── Public gate ─────────────────────────────────────────────────────

    def check_can_trade(
        self,
        symbol: str,
        spread_pips: float,
        strategy_name: str,
        account_balance: float,
        account_equity: float,
    ) -> None:
        """
        Raise RiskBlockError (or subclass) if trading is not allowed.
        Silently returns if OK.
        """
        self._maybe_reset_daily(account_balance)
        self._update_hwm(account_equity)

        reason: Optional[str] = None

        # Cooldown after consecutive losses
        if time.monotonic() < self._cooldown_until:
            remaining = self._cooldown_until - time.monotonic()
            reason = f"Cooldown active: {remaining:.0f}s remaining after consecutive losses"

        # Daily loss limit — block at >= threshold (not just >)
        elif self._daily_loss_fraction(account_balance) >= self._max_daily_loss():
            reason = (
                f"Daily loss limit reached: {self._daily_loss:.2f} "
                f"({self._daily_loss_fraction(account_balance)*100:.2f}% of balance)"
            )

        # Drawdown limit
        elif self._drawdown_fraction(account_equity) > self._max_drawdown():
            reason = (
                f"Drawdown limit reached: equity {account_equity:.2f} "
                f"vs HWM {self._equity_hwm:.2f} "
                f"({self._drawdown_fraction(account_equity)*100:.2f}%)"
            )

        # Consecutive losses
        elif self._consecutive_losses >= self._max_consec_losses():
            reason = (
                f"Consecutive loss limit: {self._consecutive_losses} losses in a row"
            )
            self._start_cooldown()

        # Spread check
        elif spread_pips > self._spread_block_pips():
            reason = (
                f"Spread {spread_pips:.1f} pips exceeds block threshold "
                f"{self._spread_block_pips():.1f} pips"
            )

        # Hourly trade frequency
        elif self._hourly_trade_count() >= self._max_trades_per_hour():
            reason = f"Hourly trade limit reached: {self._max_trades_per_hour()}"

        if reason:
            log.warning("RISK BLOCK [%s]: %s", strategy_name, reason)
            self._event_log.write({
                "event":    EVT_RISK_BLOCK,
                "strategy": strategy_name,
                "symbol":   symbol,
                "reason":   reason,
            })
            raise RiskBlockError(reason)

    # ─── State updates ────────────────────────────────────────────────────

    def record_trade_open(self) -> None:
        now = time.monotonic()
        self._trade_times.append(now)
        self._hourly_trades.append(now)
        self._session_trades += 1

    def record_trade_close(self, pnl: float) -> None:
        """
        Call after a position closes.
        pnl is the realised P&L in account currency (negative = loss).
        """
        if pnl < 0:
            self._daily_loss  += abs(pnl)
            self._weekly_loss += abs(pnl)
            self._consecutive_losses += 1
            log.info(
                "Trade loss recorded: %.2f | consecutive=%d daily_loss=%.2f",
                pnl, self._consecutive_losses, self._daily_loss,
            )
        else:
            self._consecutive_losses = 0
            log.info("Trade win recorded: %.2f | consecutive reset", pnl)

    # ─── Lot sizing ──────────────────────────────────────────────────────

    def compute_lot_size(
        self,
        account_balance: float,
        sl_distance_pips: float,
        profile: SymbolProfile,
        risk_fraction: Optional[float] = None,
    ) -> float:
        """
        Compute lot size so that a full stop-loss hit costs exactly
        risk_fraction * account_balance.

        Returns 0.0 if sl_distance_pips is zero or profile data is unavailable.
        """
        if sl_distance_pips <= 0 or account_balance <= 0:
            return 0.0

        rf = risk_fraction if risk_fraction is not None else self._default_risk()
        risk_amount = account_balance * rf

        # Pip value in account currency for 1 standard lot
        # For USD account: pip_value_per_lot ≈ 10 for EURUSD
        pip_value_per_lot = (
            profile.pip_value
            * profile.trade_contract_size
            # TODO: multiply by FX rate for non-USD accounts
        )

        if pip_value_per_lot == 0:
            return 0.0

        raw_lots = risk_amount / (sl_distance_pips * pip_value_per_lot)
        from core.utils import round_to_step, clamp
        lots = round_to_step(raw_lots, profile.volume_step)
        lots = clamp(lots, profile.volume_min, profile.volume_max)
        return lots

    # ─── Config helpers ──────────────────────────────────────────────────

    def _default_risk(self) -> float:
        return float(self._cfg_risk.get("default_risk_per_trade", 0.005))

    def _max_daily_loss(self) -> float:
        return float(self._cfg_risk.get("max_daily_loss_fraction", 0.02))

    def _max_drawdown(self) -> float:
        return float(self._cfg_risk.get("max_drawdown_fraction", 0.08))

    def _max_consec_losses(self) -> int:
        return int(self._cfg_risk.get("max_consecutive_losses", 5))

    def _max_trades_per_hour(self) -> int:
        return int(self._cfg_risk.get("max_trades_per_hour", 6))

    def _spread_block_pips(self) -> float:
        return float(self._cfg_risk.get("spread_block_pips", 4.0))

    def _consec_loss_cooldown(self) -> float:
        return float(self._cfg_risk.get("consecutive_loss_cooldown_seconds", 1800))

    # ─── Internal helpers ────────────────────────────────────────────────

    def _maybe_reset_daily(self, balance: float) -> None:
        """
        Handle daily counter reset.

        Two cases are distinguished:

        1. _last_reset_date is None — first call ever (e.g. just after
           __init__ or process restart).  Initialize the date to today
           WITHOUT zeroing _daily_loss.  This preserves losses that may
           have been recorded via record_trade_close() before the first
           check_can_trade() call (e.g. in paper mode or after a fast
           sequence of closes at startup).

        2. _last_reset_date < today — real calendar day rollover.
           Reset _daily_loss and _session_trades, then update the date.

        Previously, both cases shared the same branch, causing _daily_loss
        to be wiped on the very first check_can_trade() call, defeating
        the daily loss protection.
        """
        today = datetime.now(timezone.utc).date()

        if self._last_reset_date is None:
            # First-time initialization — preserve any existing loss state.
            self._last_reset_date = today
            log.debug("Daily risk date initialized: %s (existing daily_loss=%.2f preserved)",
                      today, self._daily_loss)

        elif self._last_reset_date < today:
            # Real day rollover — safe to reset counters.
            self._daily_loss      = 0.0
            self._session_trades  = 0
            self._last_reset_date = today
            log.info("Daily risk counters reset for %s", today)
        # else: same day, nothing to do.

    def _update_hwm(self, equity: float) -> None:
        if equity > self._equity_hwm:
            self._equity_hwm = equity

    def _drawdown_fraction(self, equity: float) -> float:
        if self._equity_hwm <= 0:
            return 0.0
        return safe_divide(self._equity_hwm - equity, self._equity_hwm)

    def _daily_loss_fraction(self, balance: float) -> float:
        return safe_divide(self._daily_loss, balance)

    def _hourly_trade_count(self) -> int:
        cutoff = time.monotonic() - 3600
        while self._hourly_trades and self._hourly_trades[0] < cutoff:
            self._hourly_trades.popleft()
        return len(self._hourly_trades)

    def _start_cooldown(self) -> None:
        self._cooldown_until = time.monotonic() + self._consec_loss_cooldown()
        log.warning(
            "Consecutive loss cooldown started: %.0fs", self._consec_loss_cooldown()
        )


# Module-level singleton
risk_manager = RiskManager()
