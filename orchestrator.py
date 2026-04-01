"""
Orchestrator — central controller.

FIX O1: Supervision loop now calls connector.is_connection_alive()
         and triggers connector.reconnect() when MT5 drops.
FIX O2: A dedicated _check_mt5_connection() method checks the actual
         terminal response, not just the file-based heartbeats.
"""
from __future__ import annotations

import signal
import sys
import time
from typing import Dict, Optional

from core.logger import get_logger, init_logging
from core.metrics_store import metrics_store
from core.mt5_connector import connector
from core.settings import settings
from orchestration.bot_runner import BotRunner
from orchestration.health_monitor import health_monitor
from orchestration.plugin_loader import plugin_loader
from orchestration.strategy_registry import strategy_registry
from strategies.base_strategy import BaseStrategy

log = get_logger("orchestrator")

_HEALTH_CHECK_INTERVAL   = 30    # seconds between heartbeat checks
_MT5_HEALTH_INTERVAL     = 15    # seconds between MT5 liveness checks
_METRICS_FLUSH_INTERVAL  = 300   # seconds between metrics flush to disk
_RECONNECT_BACKOFF_MAX   = 120   # max seconds to wait before retry


class Orchestrator:
    """
    Manages the full lifecycle of all strategy bot threads.
    """

    def __init__(self) -> None:
        self._runners: Dict[str, BotRunner]  = {}
        self._shutdown_requested             = False
        self._loop_interval                  = float(
            settings.general.get("loop_interval_seconds", 5)
        )
        self._reconnect_failures             = 0

    # ─── Main entry point ─────────────────────────────────────────────────

    def run(self) -> None:
        """Start and block until shutdown is requested."""
        self._register_signals()

        log.info("=" * 60)
        log.info("MT5 Multi-Bot Framework starting")
        log.info("Run mode: %s", settings.run_mode.upper())
        log.info("=" * 60)

        self._connect_mt5()

        strategies = plugin_loader.discover_all()
        if not strategies:
            log.critical("No strategies loaded — exiting")
            sys.exit(1)

        for name, strategy in strategies.items():
            strategy_registry.register(strategy)
            self._launch_bot(name, strategy)

        log.info("All bots launched: %s", list(self._runners.keys()))

        last_health_check  = time.monotonic()
        last_mt5_check     = time.monotonic()
        last_metrics_flush = time.monotonic()

        try:
            while not self._shutdown_requested:
                now = time.monotonic()

                # FIX O1/O2: Check MT5 connection health
                if now - last_mt5_check >= _MT5_HEALTH_INTERVAL:
                    self._check_mt5_connection()
                    last_mt5_check = now

                if now - last_health_check >= _HEALTH_CHECK_INTERVAL:
                    self._check_bot_health()
                    last_health_check = now

                if now - last_metrics_flush >= _METRICS_FLUSH_INTERVAL:
                    metrics_store.flush()
                    last_metrics_flush = now

                time.sleep(1)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received")

        self._shutdown()

    # ─── Bot management ──────────────────────────────────────────────────

    def _launch_bot(self, name: str, strategy: BaseStrategy) -> None:
        runner = BotRunner(
            strategy=strategy,
            loop_interval=self._loop_interval,
            max_restarts=int(settings.general.get("max_restart_attempts", 3)),
        )
        runner.start()
        self._runners[name] = runner
        strategy_registry.set_runner_alive(name, True)
        log.info("Bot launched: %s", name)

    def _check_bot_health(self) -> None:
        status = health_monitor.check()
        for name, st in status.items():
            if "stale" in st or "missing" in st:
                log.warning("HEALTH ALERT: %s → %s", name, st)
                runner = self._runners.get(name)
                if runner and not runner.is_alive():
                    log.warning("Restarting dead bot thread: %s", name)
                    runner.start()
                    strategy_registry.set_runner_alive(name, True)

    # ─── MT5 Connection ───────────────────────────────────────────────────

    def _connect_mt5(self) -> None:
        cfg = settings.mt5
        connector.configure(
            login=int(cfg.get("login", 0)),
            password=cfg.get("password", ""),
            server=cfg.get("server", ""),
            path=cfg.get("path", ""),
            max_retries=int(cfg.get("max_retries", 3)),
            retry_delay=float(cfg.get("retry_delay_seconds", 5)),
        )
        if settings.run_mode == "paper":
            log.info("Paper mode: connecting MT5 for data feed only")
        connector.connect()
        acct = connector.account_info()
        if acct:
            log.info(
                "Account | login=%s server=%s balance=%.2f %s",
                acct.login, acct.server, acct.balance, acct.currency,
            )
        else:
            log.warning("Could not retrieve account info (stub mode or connection issue)")

    def _check_mt5_connection(self) -> None:
        """
        FIX O1/O2: Test that MT5 terminal is actually responding.
        Calls account_info() as a lightweight liveness probe.
        Triggers reconnect with backoff on failure.
        """
        if not connector.is_connection_alive():
            self._reconnect_failures += 1
            wait = min(_RECONNECT_BACKOFF_MAX, self._reconnect_failures * 10)
            log.warning(
                "MT5 connection lost (attempt %d) — reconnecting in %ds",
                self._reconnect_failures, wait,
            )
            time.sleep(wait)
            success = connector.reconnect()
            if success:
                log.info("MT5 reconnected successfully")
                self._reconnect_failures = 0
            else:
                log.error(
                    "MT5 reconnect failed (attempt %d) — will retry",
                    self._reconnect_failures,
                )
        else:
            self._reconnect_failures = 0  # reset on healthy check

    # ─── Shutdown ─────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        log.info("Initiating graceful shutdown...")
        for name, runner in self._runners.items():
            log.info("Stopping bot: %s", name)
            runner.stop()
        for name, runner in self._runners.items():
            runner.join(timeout=15)
            if runner.is_alive():
                log.warning("Bot %s did not stop cleanly", name)
        metrics_store.flush()
        connector.disconnect()
        log.info("Shutdown complete.")

    # ─── Signal handling ──────────────────────────────────────────────────

    def _register_signals(self) -> None:
        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        log.info("Signal %d received — requesting shutdown", signum)
        self._shutdown_requested = True
