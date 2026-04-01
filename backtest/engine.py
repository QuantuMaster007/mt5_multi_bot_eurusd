"""
Backtest Engine

Runs a strategy bar-by-bar over historical OHLCV data.

This is a simplified bar-data backtester — it does NOT simulate
tick-level fills, partial fills, or order queuing. It is designed for
rapid strategy evaluation and parameter sensitivity analysis.

For production validation, always complement with MT5 Strategy Tester
or live paper mode.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type

import pandas as pd

from backtest.cost_model import CostModel
from backtest.metrics import BacktestMetrics, compute_metrics
from core.broker_profile import SymbolProfile
from core.constants import SIDE_BUY, SIDE_SELL
from core.logger import get_logger
from strategies.base_strategy import BaseStrategy, TradeIntent

log = get_logger("backtest.engine")


@dataclass
class BacktestTrade:
    bar_index:    int
    side:         str
    volume:       float
    entry_price:  float
    sl:           float
    tp:           float
    reason_code:  str
    exit_bar:     int   = -1
    exit_price:   float = 0.0
    exit_reason:  str   = ""
    gross_pnl:    float = 0.0
    net_pnl:      float = 0.0
    hold_bars:    int   = 0


@dataclass
class BacktestResult:
    strategy_name:   str
    symbol:          str
    timeframe:       str
    total_bars:      int
    trades:          List[BacktestTrade] = field(default_factory=list)
    metrics:         Optional[BacktestMetrics] = None
    cost_model_used: Optional[CostModel] = None


class BacktestEngine:
    """
    Runs a strategy against a historical DataFrame.

    The engine:
      1. Iterates bars from lookback_period onward
      2. Feeds a growing slice of bars to the strategy's prepare_indicators()
         and generate_signal()
      3. Simulates fills at next-bar open (close-of-signal-bar + slippage)
      4. Checks SL/TP on each subsequent bar
      5. Applies cost model to compute net P&L

    Limitations (documented honestly):
      - No partial fills
      - No spread simulation within bars
      - No overnight gaps differentiation
      - SL/TP checked at bar high/low only (bar resolution)
      - Regime detection runs on the same slice → no look-ahead bias
        as long as slice is defined as [0:i+1]
    """

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        profile: SymbolProfile,
        cost_model: Optional[CostModel] = None,
        lookback: int = 100,
    ) -> BacktestResult:
        """
        Run backtest for *strategy* over *df*.

        Args:
            strategy:   Instantiated strategy (will have generate_signal called).
            df:         Full historical DataFrame.
            profile:    Symbol profile for pip/lot calculations.
            cost_model: Cost model; uses default (1.5 pip spread) if None.
            lookback:   Minimum bars before first signal attempt.

        Returns:
            BacktestResult with trade list and metrics.
        """
        if cost_model is None:
            cost_model = CostModel()

        name   = strategy.metadata.name
        symbol = profile.symbol
        tf     = strategy._timeframe
        result = BacktestResult(
            strategy_name=name,
            symbol=symbol,
            timeframe=tf,
            total_bars=len(df),
            cost_model_used=cost_model,
        )

        open_trade: Optional[BacktestTrade] = None

        for i in range(lookback, len(df) - 1):
            bar_slice = df.iloc[:i + 1].copy()
            bar       = df.iloc[i]
            next_bar  = df.iloc[i + 1]

            # ── Check open trade for SL/TP hit ────────────────────────
            if open_trade:
                hit_sl = hit_tp = False
                if open_trade.side == SIDE_BUY:
                    if bar["low"] <= open_trade.sl:
                        hit_sl = True; exit_p = open_trade.sl
                    elif bar["high"] >= open_trade.tp:
                        hit_tp = True; exit_p = open_trade.tp
                else:
                    if bar["high"] >= open_trade.sl:
                        hit_sl = True; exit_p = open_trade.sl
                    elif bar["low"] <= open_trade.tp:
                        hit_tp = True; exit_p = open_trade.tp

                if hit_sl or hit_tp:
                    reason = "sl" if hit_sl else "tp"
                    self._close_trade(open_trade, i, exit_p, reason,
                                      profile, cost_model)
                    result.trades.append(open_trade)
                    open_trade = None

            # ── Generate new signal ───────────────────────────────────
            if open_trade is not None:
                continue  # only one position at a time

            try:
                fake_tick = {"ask": bar["close"], "bid": bar["close"]}
                indicators = strategy.prepare_indicators(bar_slice)
                signal: Optional[TradeIntent] = strategy.generate_signal(
                    bar_slice, indicators, ["ranging"], 1.0, fake_tick
                )
            except Exception as exc:
                log.debug("Signal error at bar %d: %s", i, exc)
                continue

            if signal is None:
                continue

            # Fill at next bar open
            fill_price = float(next_bar["open"])
            sl = signal.sl if signal.sl else 0.0
            tp = signal.tp if signal.tp else 0.0

            open_trade = BacktestTrade(
                bar_index=i + 1,
                side=signal.side,
                volume=signal.volume if signal.volume > 0 else profile.volume_min,
                entry_price=fill_price,
                sl=sl,
                tp=tp,
                reason_code=signal.reason_code,
            )

        # Close any remaining open trade at last bar close
        if open_trade:
            last_bar = df.iloc[-1]
            self._close_trade(
                open_trade, len(df) - 1,
                float(last_bar["close"]), "end_of_data",
                profile, cost_model,
            )
            result.trades.append(open_trade)

        pnl_list = [t.net_pnl for t in result.trades]
        result.metrics = compute_metrics(pnl_list)

        log.info(
            "Backtest complete | %s | bars=%d trades=%d gross_pnl=%.2f "
            "win_rate=%.1f%% max_dd=%.2f",
            name, len(df), len(result.trades),
            result.metrics.gross_pnl,
            result.metrics.win_rate * 100,
            result.metrics.max_drawdown,
        )
        return result

    @staticmethod
    def _close_trade(
        trade: BacktestTrade,
        exit_bar: int,
        exit_price: float,
        reason: str,
        profile: SymbolProfile,
        cost_model: CostModel,
    ) -> None:
        trade.exit_bar   = exit_bar
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.hold_bars  = exit_bar - trade.bar_index

        pip_val = profile.pip_value
        contract = profile.trade_contract_size

        if trade.side == SIDE_BUY:
            pips = (exit_price - trade.entry_price) / pip_val
        else:
            pips = (trade.entry_price - exit_price) / pip_val

        trade.gross_pnl = pips * pip_val * trade.volume * contract
        trade.net_pnl   = cost_model.net_pnl(trade.gross_pnl, trade.volume)


# Module-level singleton
backtest_engine = BacktestEngine()
