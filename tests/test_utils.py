"""Tests for core utility functions."""
import sys, types
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.utils import (
    round_to_step, pips_to_price, price_to_pips,
    clamp, safe_divide, format_pnl,
)


def test_round_to_step_normal():
    assert round_to_step(0.123, 0.01) == pytest.approx(0.12)


def test_round_to_step_exact():
    assert round_to_step(0.10, 0.01) == pytest.approx(0.10)


def test_round_to_step_zero_step_returns_value():
    assert round_to_step(0.12345, 0) == 0.12345


def test_pips_to_price():
    assert pips_to_price(10, 0.0001) == pytest.approx(0.001)


def test_price_to_pips():
    assert price_to_pips(0.001, 0.0001) == pytest.approx(10)


def test_price_to_pips_zero_pip_value():
    assert price_to_pips(0.001, 0) == 0.0


def test_clamp_within():
    assert clamp(5, 0, 10) == 5


def test_clamp_below():
    assert clamp(-1, 0, 10) == 0


def test_clamp_above():
    assert clamp(15, 0, 10) == 10


def test_safe_divide_normal():
    assert safe_divide(10, 4) == pytest.approx(2.5)


def test_safe_divide_by_zero():
    assert safe_divide(10, 0) == 0.0
    assert safe_divide(10, 0, default=99.9) == 99.9


def test_format_pnl_positive():
    assert format_pnl(42.5) == "+42.50"


def test_format_pnl_negative():
    assert format_pnl(-10.0) == "-10.00"
