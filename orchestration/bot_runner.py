"""
Bot Runner — per-strategy thread wrapper.

FIX BR1: _restart_count now resets after _SUCCESS_RESET_THRESHOLD
          consecutive successful cycles. A strategy that has two errors
          spread across thousands of good cycles will not accumulate
          toward the permanent-death threshold.
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Optional

from core.logger import get_logger
from core.settings import settings
from strategies.base_strategy import BaseStrategy

log = get_logger("bot_runner")

_SUCCESS_RESET_THRESHOLD = 50  # consecutive clean cycles before resetting error count


class BotRunner:
    """
    Wraps a BaseStrategy instance in a daemon thread.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        loop_interval: float = 5.0,
        max_restarts: int = 3,
    ) -> None:
        self._strategy        = strategy
        self._loop_interval   = loop_interval
        self._max_restarts    = max_restarts
        self._restart_count   = 0
        self._consec_success  = 0   # FIX BR1
        self._stop_event      = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._name            = strategy.metadata.name

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            log.warning("BotRunner for %s already running", self._name)
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"bot-{self._name}",
            daemon=True,
        )
        self._thread.start()
        log.info("BotRunner started: %s", self._name)

    def stop(self) -> None:
        self._stop_event.set()
        log.info("BotRunner stop requested: %s", self._name)

    def join(self, timeout: float = 15.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning(
                    "Thread %s did not stop within %.1fs", self._name, timeout
                )

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def strategy(self) -> BaseStrategy:
        return self._strategy

    # ─── Internal loop ────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        log.info("[%s] Loop starting (interval=%.1fs)", self._name, self._loop_interval)

        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                self._strategy.run_cycle()
                # FIX BR1: track consecutive successes and reset error count
                self._consec_success += 1
                if self._consec_success >= _SUCCESS_RESET_THRESHOLD and self._restart_count > 0:
                    log.info(
                        "[%s] %d consecutive clean cycles — resetting restart counter from %d",
                        self._name, self._consec_success, self._restart_count,
                    )
                    self._restart_count  = 0
                    self._consec_success = 0

            except Exception as exc:
                self._consec_success = 0   # reset on any exception
                self._restart_count += 1

                log.error(
                    "[%s] Exception in bot loop (restart %d/%d): %s\n%s",
                    self._name, self._restart_count, self._max_restarts,
                    exc, traceback.format_exc(),
                )

                if self._restart_count > self._max_restarts:
                    log.critical(
                        "[%s] Max restarts (%d) exceeded — bot permanently stopped",
                        self._name, self._max_restarts,
                    )
                    break

                cooldown = min(60, self._restart_count * 10)
                log.info("[%s] Restart cooldown: %ds", self._name, cooldown)
                # Use wait() so stop_event can interrupt the sleep
                self._stop_event.wait(timeout=cooldown)
                continue

            elapsed   = time.monotonic() - t0
            sleep_for = max(0.0, self._loop_interval - elapsed)
            if sleep_for > 0:
                self._stop_event.wait(timeout=sleep_for)

        log.info("[%s] Loop exited (restarts=%d)", self._name, self._restart_count)
