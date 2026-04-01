"""
Allocation Engine

When multiple strategy bots generate trade intents simultaneously,
the allocation engine resolves conflicts so the portfolio does not
inadvertently stack excessive exposure in one direction.

Conflict resolution policies (set in policy_config.yaml):
  first_wins     → first-arrived intent proceeds; others blocked
  highest_ranked → strategy with highest priority score proceeds
  block_all      → if >1 intent exists in same direction, all blocked

This engine is called by the orchestrator's intent aggregation loop,
NOT by individual bots. Each bot still sends its intent independently;
this layer arbitrates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.constants import SIDE_BUY, SIDE_SELL, EVT_ENTRY_BLOCKED
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.settings import settings
from strategies.base_strategy import TradeIntent

log = get_logger("allocation_engine")


@dataclass
class AllocationDecision:
    intent: TradeIntent
    approved: bool
    reason: str


class AllocationEngine:
    """
    Arbitrates between competing TradeIntents.

    Expects a list of pending intents and returns which are approved.
    Called once per orchestrator loop cycle after all bots have
    published their intents for the current bar.
    """

    def __init__(self) -> None:
        self._cfg = settings.policy
        self._priority: Dict[str, int] = self._cfg.get("strategy_priority", {})
        self._conflict_policy: str = settings.risk.get("portfolio", {}).get(
            "conflict_policy", "first_wins"
        )
        self._el = None

    @property
    def _event_log(self):
        if self._el is None:
            self._el = get_event_logger()
        return self._el

    def resolve(self, intents: List[TradeIntent]) -> List[AllocationDecision]:
        """
        Resolve a batch of simultaneous trade intents.

        Returns one AllocationDecision per intent — approved or blocked
        with a reason.

        Note: In the threaded architecture, bots execute independently
        and this function is typically used for post-hoc conflict
        analysis rather than blocking. For strict conflict control,
        portfolio_manager.check_can_open() is the live gate.
        """
        if not intents:
            return []

        # Group by direction
        longs  = [i for i in intents if i.side == SIDE_BUY]
        shorts = [i for i in intents if i.side == SIDE_SELL]

        decisions: List[AllocationDecision] = []

        decisions += self._resolve_group(longs, "long")
        decisions += self._resolve_group(shorts, "short")

        return decisions

    def _resolve_group(
        self, group: List[TradeIntent], direction: str
    ) -> List[AllocationDecision]:
        if len(group) <= 1:
            return [AllocationDecision(i, True, "only_intent") for i in group]

        policy = self._conflict_policy

        if policy == "block_all":
            reason = (
                f"block_all policy: {len(group)} competing {direction} intents"
            )
            log.info("Allocation: blocking all %d %s intents (%s)", len(group), direction, policy)
            return [AllocationDecision(i, False, reason) for i in group]

        elif policy == "highest_ranked":
            ranked = sorted(
                group,
                key=lambda i: self._priority.get(i.strategy, 0),
                reverse=True,
            )
            winner = ranked[0]
            results = []
            for intent in group:
                if intent is winner:
                    results.append(AllocationDecision(intent, True, "highest_priority"))
                else:
                    reason = (
                        f"outranked by {winner.strategy} "
                        f"(priority {self._priority.get(winner.strategy, 0)} "
                        f"vs {self._priority.get(intent.strategy, 0)})"
                    )
                    log.info(
                        "Allocation: blocking %s — %s", intent.strategy, reason
                    )
                    self._event_log.write({
                        "event":    EVT_ENTRY_BLOCKED,
                        "strategy": intent.strategy,
                        "reason":   reason,
                        "policy":   policy,
                    })
                    results.append(AllocationDecision(intent, False, reason))
            return results

        else:  # first_wins (default)
            # In the threaded model "first" is simply the first in the list
            results = []
            for idx, intent in enumerate(group):
                if idx == 0:
                    results.append(AllocationDecision(intent, True, "first_wins"))
                else:
                    reason = f"first_wins policy: blocked by {group[0].strategy}"
                    log.info(
                        "Allocation: blocking %s — %s", intent.strategy, reason
                    )
                    self._event_log.write({
                        "event":    EVT_ENTRY_BLOCKED,
                        "strategy": intent.strategy,
                        "reason":   reason,
                        "policy":   "first_wins",
                    })
                    results.append(AllocationDecision(intent, False, reason))
            return results


# Module-level singleton
allocation_engine = AllocationEngine()
