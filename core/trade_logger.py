"""
Trade Logger

Typed helpers for emitting trade lifecycle events to the JSONL trade log.
"""
from __future__ import annotations

from typing import Optional

from core.json_logger import get_trade_logger
from core.utils import ts_now


def log_trade_open(
    strategy: str,
    symbol: str,
    side: str,
    volume: float,
    price: float,
    sl: float,
    tp: float,
    ticket: int,
    magic: int,
    reason_code: str,
    mode: str = "live",
) -> None:
    get_trade_logger().write({
        "event":    "trade_open",
        "mode":     mode,
        "strategy": strategy,
        "symbol":   symbol,
        "side":     side,
        "volume":   volume,
        "price":    price,
        "sl":       sl,
        "tp":       tp,
        "ticket":   ticket,
        "magic":    magic,
        "reason":   reason_code,
        "open_time": ts_now(),
    })


def log_trade_close(
    strategy: str,
    symbol: str,
    side: str,
    volume: float,
    open_price: float,
    close_price: float,
    pnl: float,
    pnl_pips: float,
    ticket: int,
    magic: int,
    close_reason: str,
    open_time: str,
    mode: str = "live",
) -> None:
    get_trade_logger().write({
        "event":       "trade_close",
        "mode":        mode,
        "strategy":    strategy,
        "symbol":      symbol,
        "side":        side,
        "volume":      volume,
        "open_price":  open_price,
        "close_price": close_price,
        "pnl":         round(pnl, 2),
        "pnl_pips":    round(pnl_pips, 1),
        "ticket":      ticket,
        "magic":       magic,
        "close_reason": close_reason,
        "open_time":   open_time,
        "close_time":  ts_now(),
    })
