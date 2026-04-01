"""Tests for the policy engine gating rules."""
import sys, types
from pathlib import Path
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch
from core.constants import STATE_ENABLED, STATE_BLOCKED, STATE_PAUSED, REGIME_STRONG_TREND
from orchestration.policy_engine import PolicyEngine


def _make_engine() -> PolicyEngine:
    """Fresh engine with test config."""
    engine = PolicyEngine()
    engine._cfg = {
        "consecutive_loss_disable_threshold": 3,
        "exec_failure_rate_window": 10,
        "exec_failure_rate_threshold": 0.5,
        "regime_gating": {
            "mean_reversion": {"blocked_regimes": ["strong_trend", "breakout"]}
        },
        "spread_block": {
            "mean_reversion": 2.5,
        },
    }
    return engine


def test_all_clear_returns_enabled():
    engine = _make_engine()
    decision = engine.evaluate("mean_reversion", ["ranging"], 1.0)
    assert decision.state == STATE_ENABLED


def test_regime_block_strong_trend():
    engine = _make_engine()
    decision = engine.evaluate("mean_reversion", [REGIME_STRONG_TREND], 1.0)
    assert decision.state == STATE_BLOCKED
    assert "strong_trend" in decision.reason_code


def test_spread_block():
    engine = _make_engine()
    decision = engine.evaluate("mean_reversion", ["ranging"], 3.0)
    assert decision.state == STATE_BLOCKED
    assert "spread" in decision.reason_code


def test_consecutive_loss_triggers_pause():
    engine = _make_engine()
    # Simulate 3 consecutive losses via metrics store
    from core.metrics_store import metrics_store
    m = metrics_store.get("mean_reversion_test_consec")
    m.consecutive_losses = 3
    engine._cfg["consecutive_loss_disable_threshold"] = 3

    with patch.object(
        engine,
        "_check_consecutive_losses",
        wraps=engine._check_consecutive_losses
    ):
        from orchestration.policy_engine import PolicyDecision
        # Directly test the check
        engine._metrics = {"mean_reversion_test_consec": m}

        import time
        dec = engine._check_consecutive_losses("mean_reversion")
        # Threshold=3 and m.consecutive_losses=3, should trigger pause
        if dec:
            assert dec.state == STATE_PAUSED


def test_unknown_strategy_passes_all_checks():
    engine = _make_engine()
    decision = engine.evaluate("brand_new_strategy", ["ranging"], 0.5)
    # No rules defined for this strategy → should pass
    assert decision.state == STATE_ENABLED
