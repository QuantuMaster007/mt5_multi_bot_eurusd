"""
Policy Engine — explainable, rule-based strategy gating.

FIX PE1: Consecutive-loss pause timer is only set if the strategy is
          NOT already paused. Previously it was reset on every call,
          making the pause effectively permanent. Now the timer is
          set once and checked by _check_active_pause() on subsequent
          calls, which allows it to expire naturally.
FIX PE2: Pause state is persisted to StateStore so it survives a
          process restart.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.constants import (
    STATE_ENABLED, STATE_PAUSED, STATE_BLOCKED,
    EVT_POLICY_DECISION, EVT_STRATEGY_STATE,
)
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.metrics_store import metrics_store
from core.settings import settings
from core.state_store import StateStore

log = get_logger("policy_engine")

_PAUSE_UNTIL_KEY = "policy_pause_until"   # StateStore key


@dataclass
class PolicyDecision:
    strategy:    str
    state:       str
    reason:      str
    reason_code: str


class PolicyEngine:
    """
    Evaluates all registered strategies against gating rules.
    Every decision is logged with a reason_code for weekly review.
    """

    def __init__(self) -> None:
        self._cfg = settings.policy
        self._state_stores: Dict[str, StateStore] = {}
        self._el = None

    @property
    def _event_log(self):
        if self._el is None:
            self._el = get_event_logger()
        return self._el

    def _store(self, strategy: str) -> StateStore:
        if strategy not in self._state_stores:
            self._state_stores[strategy] = StateStore(f"policy_{strategy}")
        return self._state_stores[strategy]

    # ─── Main evaluation ─────────────────────────────────────────────────

    def evaluate(
        self,
        strategy_name: str,
        regimes: List[str],
        spread_pips: float,
    ) -> PolicyDecision:
        """Return a PolicyDecision. Logs and records all non-pass decisions."""

        decision = (
            self._check_active_pause(strategy_name)
            or self._check_regime(strategy_name, regimes)
            or self._check_spread(strategy_name, spread_pips)
            or self._check_consecutive_losses(strategy_name)
            or self._check_exec_failure_rate(strategy_name)
            or PolicyDecision(
                strategy=strategy_name,
                state=STATE_ENABLED,
                reason="All policy checks passed",
                reason_code="policy_pass",
            )
        )

        if decision.state != STATE_ENABLED:
            log.info(
                "POLICY [%s] → %s | %s",
                strategy_name, decision.state.upper(), decision.reason,
            )
            self._event_log.write({
                "event":       EVT_POLICY_DECISION,
                "strategy":    strategy_name,
                "state":       decision.state,
                "reason_code": decision.reason_code,
                "reason":      decision.reason,
                "regimes":     regimes,
                "spread_pips": round(spread_pips, 2),
            })
            metrics_store.record_policy_block(strategy_name)

        return decision

    def is_allowed(self, strategy_name: str, regimes: List[str], spread_pips: float) -> bool:
        return self.evaluate(strategy_name, regimes, spread_pips).state == STATE_ENABLED

    # ─── Checks — return None if check passes ────────────────────────────

    def _check_active_pause(self, strategy: str) -> Optional[PolicyDecision]:
        """FIX PE1/PE2: Read pause_until from durable StateStore."""
        pause_until = float(self._store(strategy).get(_PAUSE_UNTIL_KEY, 0))
        now = time.monotonic()
        if now < pause_until:
            remaining = pause_until - now
            return PolicyDecision(
                strategy=strategy,
                state=STATE_PAUSED,
                reason=f"Pause active for {remaining:.0f}s more (consecutive loss cooldown)",
                reason_code="policy_pause_active",
            )
        # Pause has expired — clear it from state store
        if self._store(strategy).get(_PAUSE_UNTIL_KEY):
            self._store(strategy).delete(_PAUSE_UNTIL_KEY)
        return None

    def _check_regime(self, strategy: str, regimes: List[str]) -> Optional[PolicyDecision]:
        regime_rules: Dict = self._cfg.get("regime_gating", {})
        blocked_regimes: List[str] = regime_rules.get(strategy, {}).get("blocked_regimes", [])
        for regime in regimes:
            if regime in blocked_regimes:
                return PolicyDecision(
                    strategy=strategy,
                    state=STATE_BLOCKED,
                    reason=f"Regime '{regime}' incompatible with {strategy}",
                    reason_code=f"policy_regime_{regime}",
                )
        return None

    def _check_spread(self, strategy: str, spread_pips: float) -> Optional[PolicyDecision]:
        limit = float(self._cfg.get("spread_block", {}).get(strategy, 999.0))
        if spread_pips > limit:
            return PolicyDecision(
                strategy=strategy,
                state=STATE_BLOCKED,
                reason=f"Spread {spread_pips:.2f} > strategy limit {limit:.2f} pips",
                reason_code="policy_spread_block",
            )
        return None

    def _check_consecutive_losses(self, strategy: str) -> Optional[PolicyDecision]:
        """
        FIX PE1: Only set pause timer if not already paused.
        Prevents the timer being reset to 'now + X' on every call,
        which made pauses perpetually renew as long as losses persisted.
        """
        threshold = int(self._cfg.get("consecutive_loss_disable_threshold", 4))
        m = metrics_store.get(strategy)

        if m.consecutive_losses < threshold:
            return None

        # Already paused? _check_active_pause handled it above.
        # If we reach here, no active pause exists — set it for the first time.
        pause_seconds = self._pause_duration_seconds()
        pause_until   = time.monotonic() + pause_seconds

        # FIX PE2: Persist to StateStore for restart survival
        self._store(strategy).set(_PAUSE_UNTIL_KEY, pause_until)

        log.warning(
            "Policy: pausing %s for %.0fs after %d consecutive losses",
            strategy, pause_seconds, m.consecutive_losses,
        )
        self._event_log.write({
            "event":    EVT_STRATEGY_STATE,
            "strategy": strategy,
            "state":    STATE_PAUSED,
            "reason":   f"{m.consecutive_losses} consecutive losses",
            "pause_seconds": pause_seconds,
        })
        return PolicyDecision(
            strategy=strategy,
            state=STATE_PAUSED,
            reason=(
                f"{m.consecutive_losses} consecutive losses ≥ "
                f"threshold {threshold} — pausing {pause_seconds:.0f}s"
            ),
            reason_code="policy_consec_loss_pause",
        )

    def _check_exec_failure_rate(self, strategy: str) -> Optional[PolicyDecision]:
        window    = int(self._cfg.get("exec_failure_rate_window", 20))
        threshold = float(self._cfg.get("exec_failure_rate_threshold", 0.4))
        m = metrics_store.get(strategy)
        if m.exec_attempts >= window and m.exec_failure_rate >= threshold:
            return PolicyDecision(
                strategy=strategy,
                state=STATE_BLOCKED,
                reason=(
                    f"Exec failure rate {m.exec_failure_rate:.1%} "
                    f"≥ {threshold:.1%} over {m.exec_attempts} attempts"
                ),
                reason_code="policy_exec_failure_rate",
            )
        return None

    # ─── External control ────────────────────────────────────────────────

    def force_pause(self, strategy: str, seconds: float, reason: str) -> None:
        pause_until = time.monotonic() + seconds
        self._store(strategy).set(_PAUSE_UNTIL_KEY, pause_until)
        log.warning("Force-paused %s for %.0fs: %s", strategy, seconds, reason)

    def resume(self, strategy: str) -> None:
        self._store(strategy).delete(_PAUSE_UNTIL_KEY)
        log.info("Strategy %s manually resumed", strategy)

    def _pause_duration_seconds(self) -> float:
        threshold = int(self._cfg.get("consecutive_loss_disable_threshold", 4))
        return float(threshold) * 900   # 15 min × threshold


# Module-level singleton
policy_engine = PolicyEngine()
