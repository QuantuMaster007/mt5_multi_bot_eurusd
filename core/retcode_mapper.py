"""
MT5 Retcode Mapper

Classifies MT5 trade server retcodes into categories:
  SUCCESS   → order accepted
  TRANSIENT → retry is reasonable (requote, price changed, server busy)
  HARD      → do NOT retry; log and apply cooldown
  UNKNOWN   → unrecognised; treat as hard

FIX K1: Removed the 10030 → FILL_ERROR alias.
  10030 is TRADE_RETCODE_LONG_ONLY in the MT5 spec — a hard broker
  restriction, not a fill-mode error.
  Fill-mode errors are detected by execution_engine._looks_like_fill_error()
  which inspects the order comment string for known patterns such as
  "unsupported filling mode". This is the correct detection method because
  MT5 brokers return RETCODE_REJECT (10006) for fill-mode failures, not
  a dedicated retcode.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Tuple

from core.constants import (
    RETCODE_OK, RETCODE_PLACED,
    RETCODE_REQUOTE, RETCODE_REQUOTE2,
    RETCODE_REJECT, RETCODE_CANCEL,
    RETCODE_ERROR, RETCODE_TIMEOUT,
    RETCODE_INVALID, RETCODE_INVALID_VOLUME,
    RETCODE_INVALID_PRICE, RETCODE_INVALID_STOPS,
    RETCODE_TRADE_DISABLED, RETCODE_MARKET_CLOSED,
    RETCODE_NO_MONEY, RETCODE_PRICE_CHANGED,
    RETCODE_PRICE_OFF, RETCODE_INVALID_EXP,
    RETCODE_ORDER_CHANGED, RETCODE_TOO_MANY_REQ,
    RETCODE_NO_CHANGES, RETCODE_SERVER_DISCON,
    RETCODE_BROKER_BUSY, RETCODE_ORDER_LOCKED,
    RETCODE_LONG_ONLY, RETCODE_TOO_MANY_ORDERS,
    RETCODE_HEDGE_PROHIBITED, RETCODE_PROHIBITED_BY_FIFO,
)


class RetcodeCategory(Enum):
    SUCCESS   = "success"
    TRANSIENT = "transient"
    HARD      = "hard"
    FILL_ERROR = "fill_error"  # set by execution_engine when comment pattern matches
    UNKNOWN   = "unknown"


_MAP: Dict[int, Tuple[RetcodeCategory, str]] = {
    RETCODE_OK:               (RetcodeCategory.SUCCESS,   "Order accepted and processed"),
    RETCODE_PLACED:           (RetcodeCategory.SUCCESS,   "Order placed in queue"),
    RETCODE_REQUOTE:          (RetcodeCategory.TRANSIENT, "Requote — price changed"),
    RETCODE_REQUOTE2:         (RetcodeCategory.TRANSIENT, "Requote (server)"),
    RETCODE_REJECT:           (RetcodeCategory.HARD,      "Order rejected by server"),
    RETCODE_CANCEL:           (RetcodeCategory.HARD,      "Order cancelled"),
    RETCODE_ERROR:            (RetcodeCategory.HARD,      "Common error"),
    RETCODE_TIMEOUT:          (RetcodeCategory.TRANSIENT, "Request timed out"),
    RETCODE_INVALID:          (RetcodeCategory.HARD,      "Invalid request structure"),
    RETCODE_INVALID_VOLUME:   (RetcodeCategory.HARD,      "Invalid order volume"),
    RETCODE_INVALID_PRICE:    (RetcodeCategory.TRANSIENT, "Invalid price"),
    RETCODE_INVALID_STOPS:    (RetcodeCategory.HARD,      "Invalid stop levels"),
    RETCODE_TRADE_DISABLED:   (RetcodeCategory.HARD,      "Trading disabled for symbol"),
    RETCODE_MARKET_CLOSED:    (RetcodeCategory.HARD,      "Market is closed"),
    RETCODE_NO_MONEY:         (RetcodeCategory.HARD,      "Insufficient funds"),
    RETCODE_PRICE_CHANGED:    (RetcodeCategory.TRANSIENT, "Price changed"),
    RETCODE_PRICE_OFF:        (RetcodeCategory.TRANSIENT, "Off quotes"),
    RETCODE_INVALID_EXP:      (RetcodeCategory.HARD,      "Invalid order expiration"),
    RETCODE_ORDER_CHANGED:    (RetcodeCategory.HARD,      "Order state changed"),
    RETCODE_TOO_MANY_REQ:     (RetcodeCategory.TRANSIENT, "Too many requests — slow down"),
    RETCODE_NO_CHANGES:       (RetcodeCategory.HARD,      "No changes in modify request"),
    RETCODE_SERVER_DISCON:    (RetcodeCategory.TRANSIENT, "Server disconnected"),
    RETCODE_BROKER_BUSY:      (RetcodeCategory.TRANSIENT, "Broker is busy"),
    RETCODE_ORDER_LOCKED:     (RetcodeCategory.HARD,      "Order locked by broker"),
    RETCODE_LONG_ONLY:        (RetcodeCategory.HARD,      "Long-only restriction (short sales prohibited)"),
    RETCODE_TOO_MANY_ORDERS:  (RetcodeCategory.HARD,      "Too many pending orders"),
    RETCODE_HEDGE_PROHIBITED: (RetcodeCategory.HARD,      "Hedging not allowed on this account"),
    RETCODE_PROHIBITED_BY_FIFO: (RetcodeCategory.HARD,   "Prohibited by FIFO rules"),
    # NOTE: 10030 maps to RETCODE_LONG_ONLY above.
    # Fill-mode errors produce RETCODE_REJECT (10006) with a comment string.
    # See execution_engine._looks_like_fill_error() for detection logic.
}


def classify(retcode: int) -> Tuple[RetcodeCategory, str]:
    """Return (RetcodeCategory, human_readable_description)."""
    if retcode in _MAP:
        return _MAP[retcode]
    return RetcodeCategory.UNKNOWN, f"Unknown retcode {retcode}"


def is_success(retcode: int) -> bool:
    cat, _ = classify(retcode)
    return cat == RetcodeCategory.SUCCESS


def is_transient(retcode: int) -> bool:
    cat, _ = classify(retcode)
    return cat == RetcodeCategory.TRANSIENT


def is_hard(retcode: int) -> bool:
    """True for HARD, FILL_ERROR (set externally), or UNKNOWN."""
    cat, _ = classify(retcode)
    return cat in (RetcodeCategory.HARD, RetcodeCategory.FILL_ERROR, RetcodeCategory.UNKNOWN)


def describe(retcode: int) -> str:
    _, desc = classify(retcode)
    return desc
