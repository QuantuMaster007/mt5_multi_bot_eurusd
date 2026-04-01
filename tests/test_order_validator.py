"""
Tests for OrderValidator — no MT5 required.
"""
import sys, types
from pathlib import Path
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.order_validator import OrderValidator, OrderIntent
from core.broker_profile import SymbolProfile
from core.constants import SIDE_BUY, SIDE_SELL, FILL_IOC, FILL_FOK
from core.exceptions import VolumeError, StopLevelError, InvalidFillModeError


def _profile(**kwargs) -> SymbolProfile:
    defaults = dict(
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
        supported_fill_modes=[FILL_IOC, FILL_FOK],
        preferred_fill_mode=FILL_IOC,
    )
    defaults.update(kwargs)
    return SymbolProfile(**defaults)


def _intent(**kwargs) -> OrderIntent:
    defaults = dict(
        symbol="EURUSD",
        side=SIDE_BUY,
        volume=0.01,
        entry_price=1.08000,
        sl=1.07800,
        tp=1.08400,
        comment="test",
        magic=200100,
    )
    defaults.update(kwargs)
    return OrderIntent(**defaults)


validator = OrderValidator()


def test_valid_intent_passes():
    intent = _intent()
    profile = _profile()
    result = validator.validate(intent, profile, 1.08010)
    assert result.volume == 0.01


def test_volume_below_min_raises():
    intent = _intent(volume=0.001)
    with pytest.raises(VolumeError):
        validator.validate(intent, _profile(), 1.08010)


def test_volume_above_max_raises():
    intent = _intent(volume=999.0)
    with pytest.raises(VolumeError):
        validator.validate(intent, _profile(), 1.08010)


def test_volume_snapped_to_step():
    intent = _intent(volume=0.0123)
    profile = _profile()
    result = validator.validate(intent, profile, 1.08010)
    assert result.volume == 0.01  # snapped down


def test_sl_too_close_raises():
    # stops_level=10 points = 0.00010; sl is 0.00005 away → too close
    intent = _intent(sl=1.07995)  # 0.00005 from 1.08000
    profile = _profile(stops_level=10)
    with pytest.raises(StopLevelError):
        validator.validate(intent, profile, 1.08000)


def test_unsupported_fill_mode_raises():
    from core.constants import FILL_RETURN
    intent = _intent(fill_mode=FILL_RETURN)
    profile = _profile(supported_fill_modes=[FILL_IOC, FILL_FOK])
    with pytest.raises(InvalidFillModeError):
        validator.validate(intent, profile, 1.08010)


def test_no_sl_tp_skips_stop_validation():
    intent = _intent(sl=0.0, tp=0.0)
    profile = _profile(stops_level=100)  # very tight — would fail with stops
    result = validator.validate(intent, profile, 1.08010)
    assert result.volume == 0.01
