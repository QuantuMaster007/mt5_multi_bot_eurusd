"""Tests for RiskManager sizing and guard logic."""
import sys, types
from pathlib import Path
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.risk_manager import RiskManager
from core.broker_profile import SymbolProfile
from core.exceptions import RiskBlockError


def _profile() -> SymbolProfile:
    return SymbolProfile(
        symbol="EURUSD",
        pip_value=0.0001,
        point=0.00001,
        trade_contract_size=100_000,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )


def _manager() -> RiskManager:
    rm = RiskManager()
    rm._cfg_risk = {
        "default_risk_per_trade": 0.01,   # 1%
        "max_daily_loss_fraction": 0.05,
        "max_drawdown_fraction": 0.10,
        "max_consecutive_losses": 5,
        "max_trades_per_hour": 10,
        "spread_block_pips": 4.0,
        "consecutive_loss_cooldown_seconds": 60,
    }
    return rm


def test_lot_size_basic():
    rm = _manager()
    # 10000 balance, 1% risk = 100 risk amount
    # 20 pip SL × 10 pip_value_per_lot = 200 per lot
    # lots = 100 / 200 = 0.5 → snapped to 0.5 (step 0.01)
    lots = rm.compute_lot_size(10000, 20, _profile(), risk_fraction=0.01)
    assert lots == pytest.approx(0.5, abs=0.01)


def test_lot_size_zero_sl():
    rm = _manager()
    lots = rm.compute_lot_size(10000, 0, _profile())
    assert lots == 0.0


def test_lot_size_respects_max():
    rm = _manager()
    # Huge balance with tiny SL → would compute enormous lots
    lots = rm.compute_lot_size(10_000_000, 1, _profile())
    assert lots <= 100.0


def test_spread_block_raises():
    rm = _manager()
    rm._equity_hwm = 10000
    with pytest.raises(RiskBlockError):
        rm.check_can_trade(
            symbol="EURUSD",
            spread_pips=5.0,   # > limit of 4.0
            strategy_name="test",
            account_balance=10000,
            account_equity=10000,
        )


def test_daily_loss_block():
    rm = _manager()
    rm._equity_hwm = 10000
    rm._daily_loss = 600  # 6% of 10000 > 5% limit
    with pytest.raises(RiskBlockError):
        rm.check_can_trade(
            symbol="EURUSD",
            spread_pips=1.0,
            strategy_name="test",
            account_balance=10000,
            account_equity=9400,
        )


def test_record_trade_resets_consecutive_on_win():
    rm = _manager()
    rm._consecutive_losses = 3
    rm.record_trade_close(pnl=50.0)
    assert rm._consecutive_losses == 0
