"""
Process Manager

Owns the collection of BotRunner instances and provides lifecycle
operations: start_all, stop_all, restart_one, status_all.

The name "process_manager" is kept for architectural clarity even
though we use threads (not processes) — it manages the concurrent
execution units regardless of their OS-level implementation.

If the architecture ever migrates to true multiprocessing, this is
the only module that needs to change its runner implementation.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from core.logger import get_logger
from core.settings import settings
from orchestration.bot_runner import BotRunner
from orchestration.strategy_registry import strategy_registry
from strategies.base_strategy import BaseStrategy

log = get_logger("process_manager")


class ProcessManager:
    """
    Lifecycle manager for all BotRunner threads.
    """

    def __init__(self) -> None:
        self._runners: Dict[str, BotRunner] = {}
        self._loop_interval = float(
            settings.general.get("loop_interval_seconds", 5)
        )
        self._max_restarts = int(
            settings.general.get("max_restart_attempts", 3)
        )

    def start_all(self, strategies: Dict[str, BaseStrategy]) -> None:
        """Start a BotRunner for each strategy. Registers in strategy_registry."""
        for name, strategy in strategies.items():
            self.start_one(strategy)

    def start_one(self, strategy: BaseStrategy) -> BotRunner:
        name = strategy.metadata.name
        if name in self._runners and self._runners[name].is_alive():
            log.warning("Runner for %s already alive — skipping start", name)
            return self._runners[name]

        runner = BotRunner(
            strategy=strategy,
            loop_interval=self._loop_interval,
            max_restarts=self._max_restarts,
        )
        runner.start()
        self._runners[name] = runner
        strategy_registry.set_runner_alive(name, True)
        log.info("Started runner: %s", name)
        return runner

    def stop_all(self, join_timeout: float = 15.0) -> None:
        """Signal all runners to stop, then join them."""
        log.info("Stopping all %d bot runners...", len(self._runners))
        for name, runner in self._runners.items():
            runner.stop()

        for name, runner in self._runners.items():
            runner.join(timeout=join_timeout)
            alive = runner.is_alive()
            strategy_registry.set_runner_alive(name, not alive)
            if alive:
                log.warning("Runner %s did not stop within %.1fs", name, join_timeout)
            else:
                log.info("Runner %s stopped cleanly", name)

    def stop_one(self, name: str, join_timeout: float = 10.0) -> None:
        runner = self._runners.get(name)
        if runner:
            runner.stop()
            runner.join(timeout=join_timeout)
            strategy_registry.set_runner_alive(name, runner.is_alive())

    def restart_one(self, name: str) -> Optional[BotRunner]:
        """Stop and restart the runner for *name*."""
        runner = self._runners.get(name)
        if runner is None:
            log.error("Cannot restart %s — not found", name)
            return None

        log.info("Restarting bot: %s", name)
        runner.stop()
        runner.join(timeout=10)
        time.sleep(2)  # brief gap before restart

        new_runner = BotRunner(
            strategy=runner.strategy,
            loop_interval=self._loop_interval,
            max_restarts=self._max_restarts,
        )
        new_runner.start()
        self._runners[name] = new_runner
        strategy_registry.set_runner_alive(name, True)
        log.info("Bot %s restarted", name)
        return new_runner

    def status(self) -> Dict[str, bool]:
        """Return {strategy_name: is_alive} for all runners."""
        result = {}
        for name, runner in self._runners.items():
            alive = runner.is_alive()
            result[name] = alive
            strategy_registry.set_runner_alive(name, alive)
        return result

    def all_alive(self) -> bool:
        return all(r.is_alive() for r in self._runners.values())

    def get_runner(self, name: str) -> Optional[BotRunner]:
        return self._runners.get(name)

    def runner_names(self) -> List[str]:
        return list(self._runners.keys())


# Module-level singleton
process_manager = ProcessManager()
