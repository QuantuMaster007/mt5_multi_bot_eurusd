"""
Backtest Metrics

Computes standard performance metrics from a list of trade P&L values.
No MT5 dependency — pure Python/numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class BacktestMetrics:
    trade_count:      int
    win_count:        int
    loss_count:       int
    win_rate:         float
    gross_pnl:        float
    avg_win:          float
    avg_loss:         float
    expectancy:       float
    profit_factor:    float
    max_drawdown:     float
    max_drawdown_pct: float
    sharpe_ratio:     float
    max_consec_losses: int
    max_consec_wins:  int


def compute_metrics(
    pnl_series: List[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> BacktestMetrics:
    """
    Compute performance metrics from a series of per-trade P&L values.

    Args:
        pnl_series:        List of P&L per trade (positive = win, negative = loss).
        risk_free_rate:    Annual risk-free rate (e.g. 0.05 for 5%).
        periods_per_year:  Number of trades per year for Sharpe annualisation.

    Returns:
        BacktestMetrics dataclass.
    """
    if not pnl_series:
        return BacktestMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    arr = np.array(pnl_series, dtype=float)
    n   = len(arr)

    wins   = arr[arr > 0]
    losses = arr[arr < 0]

    win_count  = len(wins)
    loss_count = len(losses)
    win_rate   = win_count / n if n else 0

    gross_pnl = float(arr.sum())
    avg_win   = float(wins.mean())  if len(wins)   else 0.0
    avg_loss  = float(abs(losses.mean())) if len(losses) else 0.0

    gross_win  = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0

    expectancy    = win_rate * avg_win - (1 - win_rate) * avg_loss
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Drawdown
    equity = np.cumsum(arr)
    peak   = np.maximum.accumulate(equity)
    dd     = peak - equity
    max_dd = float(dd.max()) if len(dd) else 0.0
    max_dd_pct = float(dd.max() / peak.max()) if peak.max() > 0 else 0.0

    # Sharpe ratio (annualised)
    if arr.std() > 0:
        sharpe = (
            (arr.mean() - risk_free_rate / periods_per_year)
            / arr.std()
            * math.sqrt(periods_per_year)
        )
    else:
        sharpe = 0.0

    # Consecutive wins/losses
    max_consec_w = max_consec_l = cur_w = cur_l = 0
    for pnl in pnl_series:
        if pnl > 0:
            cur_w += 1; cur_l = 0
            max_consec_w = max(max_consec_w, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_consec_l = max(max_consec_l, cur_l)

    return BacktestMetrics(
        trade_count=n,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=round(win_rate, 4),
        gross_pnl=round(gross_pnl, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        expectancy=round(expectancy, 4),
        profit_factor=round(profit_factor, 4),
        max_drawdown=round(max_dd, 4),
        max_drawdown_pct=round(max_dd_pct, 4),
        sharpe_ratio=round(sharpe, 4),
        max_consec_losses=max_consec_l,
        max_consec_wins=max_consec_w,
    )
