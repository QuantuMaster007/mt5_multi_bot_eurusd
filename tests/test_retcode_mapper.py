"""Tests for retcode classification."""
import sys, types
from pathlib import Path
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import retcode_mapper
from core.retcode_mapper import RetcodeCategory


def test_retcode_ok_is_success():
    cat, _ = retcode_mapper.classify(10009)
    assert cat == RetcodeCategory.SUCCESS


def test_requote_is_transient():
    cat, _ = retcode_mapper.classify(10004)
    assert cat == RetcodeCategory.TRANSIENT


def test_no_money_is_hard():
    cat, _ = retcode_mapper.classify(10019)
    assert cat == RetcodeCategory.HARD


def test_long_only_is_hard():
    # FIX K1: 10030 is TRADE_RETCODE_LONG_ONLY — a HARD broker restriction.
    # Fill-mode errors use RETCODE_REJECT (10006) + comment string detection.
    cat, _ = retcode_mapper.classify(10030)
    assert cat == RetcodeCategory.HARD


def test_reject_is_hard():
    # RETCODE_REJECT is the code MT5 uses for fill-mode rejection.
    # execution_engine._looks_like_fill_error() then inspects the comment.
    cat, _ = retcode_mapper.classify(10006)
    assert cat == RetcodeCategory.HARD


def test_unknown_retcode_is_unknown():
    cat, _ = retcode_mapper.classify(99999)
    assert cat == RetcodeCategory.UNKNOWN


def test_is_success_helper():
    assert retcode_mapper.is_success(10009)
    assert not retcode_mapper.is_success(10019)


def test_is_transient_helper():
    assert retcode_mapper.is_transient(10004)
    assert not retcode_mapper.is_transient(10009)


def test_describe_returns_string():
    desc = retcode_mapper.describe(10009)
    assert isinstance(desc, str) and len(desc) > 0
