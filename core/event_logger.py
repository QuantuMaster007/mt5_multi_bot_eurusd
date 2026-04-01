"""
Event Logger

Typed helpers that wrap JsonLineLogger for clean, consistent event
emission from any module without having to remember field names.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.constants import (
    EVT_SIGNAL, EVT_ENTRY_INTENT, EVT_ENTRY_APPROVED, EVT_ENTRY_BLOCKED,
    EVT_POLICY_DECISION, EVT_RISK_BLOCK, EVT_SPREAD_SPIKE,
    EVT_RECONNECT, EVT_REGIME_CHANGE, EVT_COOLDOWN_START, EVT_COOLDOWN_END,
)
from core.json_logger import get_event_logger
from core.utils import ts_now


def emit_signal(
    strategy: str,
    symbol: str,
    side: str,
    reason_code: str,
    regimes: List[str],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    rec = {
        "event":    EVT_SIGNAL,
        "strategy": strategy,
        "symbol":   symbol,
        "side":     side,
        "reason":   reason_code,
        "regimes":  regimes,
    }
    if extra:
        rec.update(extra)
    get_event_logger().write(rec)


def emit_entry_blocked(
    strategy: str,
    symbol: str,
    reason: str,
    reason_code: str,
    layer: str = "policy",
) -> None:
    get_event_logger().write({
        "event":       EVT_ENTRY_BLOCKED,
        "strategy":    strategy,
        "symbol":      symbol,
        "reason":      reason,
        "reason_code": reason_code,
        "layer":       layer,
    })


def emit_spread_spike(
    symbol: str,
    spread_pips: float,
    threshold: float,
) -> None:
    get_event_logger().write({
        "event":     EVT_SPREAD_SPIKE,
        "symbol":    symbol,
        "spread":    round(spread_pips, 2),
        "threshold": threshold,
    })


def emit_reconnect(server: str, attempt: int) -> None:
    get_event_logger().write({
        "event":   EVT_RECONNECT,
        "server":  server,
        "attempt": attempt,
    })


def emit_regime_change(
    symbol: str,
    old_regime: str,
    new_regime: str,
) -> None:
    get_event_logger().write({
        "event":      EVT_REGIME_CHANGE,
        "symbol":     symbol,
        "old_regime": old_regime,
        "new_regime": new_regime,
    })


def emit_cooldown(key: str, seconds: float, reason: str, started: bool = True) -> None:
    get_event_logger().write({
        "event":   EVT_COOLDOWN_START if started else EVT_COOLDOWN_END,
        "key":     key,
        "seconds": seconds,
        "reason":  reason,
    })
