"""
Metrics Store

Maintains per-strategy rolling trade statistics.
All writes are in-memory for speed; a background flush writes to disk
every N minutes so nothing is lost on crash.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import get_logger
from core.utils import safe_divide, ensure_dir

log = get_logger("metrics_store")


@dataclass
class StrategyMetrics:
    strategy: str
    total_trades:       int   = 0
    wins:               int   = 0
    losses:             int   = 0
    gross_pnl:          float = 0.0
    gross_win:          float = 0.0
    gross_loss:         float = 0.0
    max_drawdown:       float = 0.0
    peak_pnl:           float = 0.0
    consecutive_wins:   int   = 0
    consecutive_losses: int   = 0
    max_consec_wins:    int   = 0
    max_consec_losses:  int   = 0
    total_hold_minutes: float = 0.0
    policy_blocks:      int   = 0
    risk_blocks:        int   = 0
    exec_failures:      int   = 0
    exec_attempts:      int   = 0
    spread_blocks:      int   = 0
    session_blocks:     int   = 0
    skipped_signals:    int   = 0
    pnl_series:         List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return safe_divide(self.wins, self.total_trades)

    @property
    def avg_win(self) -> float:
        return safe_divide(self.gross_win, self.wins)

    @property
    def avg_loss(self) -> float:
        return safe_divide(self.gross_loss, self.losses)

    @property
    def expectancy(self) -> float:
        """Expectancy = (WinRate × AvgWin) − (LossRate × AvgLoss)"""
        loss_rate = safe_divide(self.losses, self.total_trades)
        return self.win_rate * self.avg_win - loss_rate * self.avg_loss

    @property
    def profit_factor(self) -> float:
        return safe_divide(self.gross_win, self.gross_loss)

    @property
    def exec_failure_rate(self) -> float:
        return safe_divide(self.exec_failures, self.exec_attempts)

    @property
    def avg_hold_minutes(self) -> float:
        return safe_divide(self.total_hold_minutes, self.total_trades)

    def record_trade(self, pnl: float, hold_minutes: float = 0.0) -> None:
        self.total_trades += 1
        self.total_hold_minutes += hold_minutes
        self.gross_pnl += pnl
        self.pnl_series.append(pnl)

        # Update peak and drawdown
        if self.gross_pnl > self.peak_pnl:
            self.peak_pnl = self.gross_pnl
        dd = self.peak_pnl - self.gross_pnl
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        if pnl > 0:
            self.wins += 1
            self.gross_win += pnl
            self.consecutive_wins  += 1
            self.consecutive_losses = 0
            if self.consecutive_wins > self.max_consec_wins:
                self.max_consec_wins = self.consecutive_wins
        else:
            self.losses += 1
            self.gross_loss += abs(pnl)
            self.consecutive_losses += 1
            self.consecutive_wins    = 0
            if self.consecutive_losses > self.max_consec_losses:
                self.max_consec_losses = self.consecutive_losses

    def to_dict(self) -> dict:
        d = asdict(self)
        d["win_rate"]          = round(self.win_rate, 4)
        d["avg_win"]           = round(self.avg_win, 4)
        d["avg_loss"]          = round(self.avg_loss, 4)
        d["expectancy"]        = round(self.expectancy, 4)
        d["profit_factor"]     = round(self.profit_factor, 4)
        d["exec_failure_rate"] = round(self.exec_failure_rate, 4)
        d["avg_hold_minutes"]  = round(self.avg_hold_minutes, 2)
        return d


class MetricsStore:
    """
    Thread-safe metrics store.
    Each strategy gets its own StrategyMetrics instance.
    """

    def __init__(self, flush_interval: int = 300) -> None:
        self._metrics: Dict[str, StrategyMetrics] = {}
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._last_flush = time.monotonic()
        self._metrics_dir = Path("data/metrics")
        ensure_dir(self._metrics_dir)

    def get(self, strategy: str) -> StrategyMetrics:
        with self._lock:
            if strategy not in self._metrics:
                self._metrics[strategy] = StrategyMetrics(strategy=strategy)
            return self._metrics[strategy]

    def record_trade(
        self, strategy: str, pnl: float, hold_minutes: float = 0.0
    ) -> None:
        with self._lock:
            m = self._metrics.setdefault(
                strategy, StrategyMetrics(strategy=strategy)
            )
            m.record_trade(pnl, hold_minutes)
        self._maybe_flush()

    def record_policy_block(self, strategy: str) -> None:
        with self._lock:
            self._metrics.setdefault(
                strategy, StrategyMetrics(strategy=strategy)
            ).policy_blocks += 1

    def record_risk_block(self, strategy: str) -> None:
        with self._lock:
            self._metrics.setdefault(
                strategy, StrategyMetrics(strategy=strategy)
            ).risk_blocks += 1

    def record_exec_attempt(self, strategy: str, success: bool) -> None:
        with self._lock:
            m = self._metrics.setdefault(
                strategy, StrategyMetrics(strategy=strategy)
            )
            m.exec_attempts += 1
            if not success:
                m.exec_failures += 1

    def record_skipped_signal(self, strategy: str) -> None:
        with self._lock:
            self._metrics.setdefault(
                strategy, StrategyMetrics(strategy=strategy)
            ).skipped_signals += 1

    def all_summaries(self) -> Dict[str, dict]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._metrics.items()}

    def flush(self) -> None:
        """Write all metrics to disk as JSON."""
        with self._lock:
            snapshot = {k: v.to_dict() for k, v in self._metrics.items()}
        out_path = self._metrics_dir / "metrics_snapshot.json"
        try:
            with open(out_path, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
        except Exception as exc:
            log.warning("Metrics flush failed: %s", exc)

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush > self._flush_interval:
            self.flush()
            self._last_flush = time.monotonic()


# Module-level singleton
metrics_store = MetricsStore()
