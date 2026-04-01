"""
Tests for ExecutionEngine — uses mocks so no MT5 terminal is required.

The key change from the previous version: _mock_tick() now sets t.time
to the current wall-clock time so the freshness check inside
_get_fresh_tick() (time.time() - tick.time <= MAX_TICK_AGE_SECONDS)
passes correctly.

Previously the test patched core.execution_engine.connector but
relied on market_data.is_tick_fresh() for the freshness check.
market_data imports its own connector reference, so patching
core.execution_engine.connector did not cover the market_data path,
causing false stale-tick rejections in every test.

The fix in execution_engine.py (_get_fresh_tick) uses
connector.symbol_info_tick() directly — the same connector reference
that the tests patch — making mock isolation reliable.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Stub MetaTrader5 before any project import
mt5_stub = types.ModuleType("MetaTrader5")
for attr in ("initialize", "shutdown", "login", "version", "last_error",
             "symbol_info", "symbol_info_tick", "order_check", "order_send",
             "positions_get", "account_info", "copy_rates_from_pos",
             "TIMEFRAME_M15", "TIMEFRAME_H1",
             "ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN"):
    setattr(mt5_stub, attr, MagicMock(return_value=None))
sys.modules.setdefault("MetaTrader5", mt5_stub)
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.execution_engine import ExecutionEngine
from core.broker_profile import SymbolProfile
from core.order_validator import OrderIntent
from core.constants import SIDE_BUY, FILL_IOC, RETCODE_OK, RETCODE_REQUOTE


def _mock_profile() -> SymbolProfile:
    return SymbolProfile(
        symbol="EURUSD",
        digits=5,
        pip_value=0.0001,
        point=0.00001,
        trade_contract_size=100_000,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        spread=2,
        supported_fill_modes=[FILL_IOC],
        preferred_fill_mode=FILL_IOC,
    )


def _mock_order_result(retcode: int, order: int = 12345) -> MagicMock:
    r = MagicMock()
    r.retcode = retcode
    r.order   = order
    r.price   = 1.08000
    r.volume  = 0.01
    return r


def _mock_tick(bid: float = 1.07990, ask: float = 1.08010) -> MagicMock:
    """
    Return a mock tick with realistic bid, ask, and time attributes.

    The time field is set to the current wall-clock time so the
    freshness check in _get_fresh_tick() passes (age ≈ 0s).
    """
    t = MagicMock()
    t.bid  = bid
    t.ask  = ask
    t.time = time.time()   # fresh tick — age ≈ 0 seconds
    return t


def _make_engine() -> ExecutionEngine:
    engine = ExecutionEngine()
    engine._cfg = {
        "pre_check_enabled":             False,
        "transient_retry_count":         1,
        "transient_retry_delay_seconds": 0,
        "hard_reject_cooldown_seconds":  5,
        "deviation_points":              20,
        "magic_base":                    200000,
    }
    return engine


def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="EURUSD",
        side=SIDE_BUY,
        volume=0.01,
        entry_price=1.08010,
        sl=1.07800,
        tp=1.08400,
        comment="test",
        magic=200100,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_successful_fill():
    engine = _make_engine()

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = _mock_tick()
        mock_conn.order_send.return_value       = _mock_order_result(RETCODE_OK)
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is True
    assert result.order_id == 12345
    assert result.fill_price == 1.08000


def test_transient_rejection_triggers_retry():
    engine = _make_engine()
    call_count = {"n": 0}

    def side_effect(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_order_result(RETCODE_REQUOTE)
        return _mock_order_result(RETCODE_OK)

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = _mock_tick()
        mock_conn.order_send.side_effect        = side_effect
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is True
    assert call_count["n"] == 2  # retried once


def test_hard_rejection_applies_cooldown():
    from core.constants import RETCODE_NO_MONEY
    engine = _make_engine()
    engine._cfg["hard_reject_cooldown_seconds"] = 60

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = _mock_tick()
        mock_conn.order_send.return_value       = _mock_order_result(RETCODE_NO_MONEY)
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is False

    # Second attempt should hit cooldown — connector not called again
    with patch("core.execution_engine.broker_profile") as mock_bp2, \
         patch("core.execution_engine.connector") as mock_conn2, \
         patch("core.execution_engine.order_validator") as mock_val2:

        mock_bp2.get_symbol_profile.return_value = _mock_profile()
        mock_conn2.symbol_info_tick.return_value = _mock_tick()
        mock_val2.validate.side_effect           = lambda intent, *a, **k: intent

        result2 = engine.send_market_order(_intent())

    assert result2.success is False
    assert result2.category == "cooldown"


def test_order_none_response_handled_gracefully():
    engine = _make_engine()
    engine._cfg["transient_retry_count"] = 0

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = _mock_tick()
        mock_conn.order_send.return_value       = None
        mock_conn.last_error.return_value       = (-1, "stub error")
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is False


def test_stale_tick_returns_rejection():
    """
    When connector.symbol_info_tick() returns a tick older than
    MAX_TICK_AGE_SECONDS, send_market_order() must return a clean
    stale_tick rejection without reaching order_send().
    """
    engine = _make_engine()

    stale = _mock_tick()
    stale.time = time.time() - 300   # 5 minutes old — clearly stale

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = stale
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is False
    assert result.category == "stale_tick"
    # order_send must NOT have been called
    mock_conn.order_send.assert_not_called()


def test_missing_tick_returns_data_error():
    """
    When connector.symbol_info_tick() returns None,
    send_market_order() must return a data_error rejection.
    """
    engine = _make_engine()

    with patch("core.execution_engine.broker_profile") as mock_bp, \
         patch("core.execution_engine.connector") as mock_conn, \
         patch("core.execution_engine.order_validator") as mock_val:

        mock_bp.get_symbol_profile.return_value = _mock_profile()
        mock_conn.symbol_info_tick.return_value = None
        mock_val.validate.side_effect           = lambda intent, *a, **k: intent

        result = engine.send_market_order(_intent())

    assert result.success is False
    assert result.category == "data_error"
