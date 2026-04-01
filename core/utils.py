"""
Shared utility functions. No MT5 dependency here.

FIX U1: round_to_step now uses Decimal arithmetic to avoid IEEE 754
         rounding errors for step values like 0.001, 0.0001.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ts_now() -> str:
    """ISO-8601 UTC timestamp string."""
    return utc_now().isoformat()


def round_to_step(value: float, step: float) -> float:
    """
    Round *value* DOWN to the nearest multiple of *step*.
    Used for lot-size alignment.

    Uses Decimal arithmetic to avoid IEEE 754 rounding errors that
    occur with float division for steps like 0.001 or 0.0001.

    Examples::
        round_to_step(0.123, 0.01)   → 0.12
        round_to_step(0.025, 0.01)   → 0.02
        round_to_step(0.0034, 0.001) → 0.003
    """
    if step <= 0:
        return value
    try:
        d_val  = Decimal(str(value))
        d_step = Decimal(str(step))
        result = (d_val // d_step) * d_step
        return float(result)
    except Exception:
        # Arithmetic fallback — should never happen with valid inputs
        factor = 1.0 / step
        return math.floor(value * factor) / factor


def pips_to_price(pips: float, pip_value: float) -> float:
    """Convert pip count to price distance (e.g. 10 pips * 0.0001 = 0.001)."""
    return pips * pip_value


def price_to_pips(price_distance: float, pip_value: float) -> float:
    """Convert price distance to pip count."""
    if pip_value == 0:
        return 0.0
    return price_distance / pip_value


def ensure_dir(path: str | Path) -> Path:
    """Create directory and all parents if they don't exist. Returns Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns *default* instead of raising ZeroDivisionError."""
    if denominator == 0:
        return default
    return numerator / denominator


def load_env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def elapsed_seconds(since: float) -> float:
    """Seconds elapsed since *since* (time.monotonic() value)."""
    return time.monotonic() - since


def format_pnl(value: float) -> str:
    """Format a P&L value for display."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}"
