"""
main.py — Framework entry point

FIX PP3: In paper mode, the paper_engine.update_positions() call is
          injected into the orchestrator's supervision loop by subclassing
          Orchestrator so SL/TP checking happens every supervision tick.
"""
import os
import sys

from core.logger import init_logging
from core.settings import settings


def main() -> None:
    log_cfg = settings.logging
    init_logging(
        level=os.environ.get("LOG_LEVEL", log_cfg.get("level", "INFO")),
        log_file=log_cfg.get("file_path", "data/logs/system.log"),
        console=log_cfg.get("console", True),
    )

    from core.logger import get_logger
    log = get_logger("main")

    mode = settings.run_mode
    log.info("Starting framework in mode: %s", mode.upper())

    if mode not in ("paper", "demo", "live"):
        log.critical("Unknown run_mode '%s'. Use: paper | demo | live", mode)
        sys.exit(1)

    if mode == "paper":
        _run_paper()
    else:
        _run_real()


def _run_paper() -> None:
    """
    Paper mode: live MT5 data feed, locally simulated fills.
    Monkeypatches execution_engine to use PaperExecutionEngine.
    Subclasses Orchestrator to call update_positions() each tick.
    """
    import core.execution_engine as exe_mod
    from paper.paper_execution import paper_engine
    from core.execution_engine import ExecutionResult

    class _PaperWrapper:
        """Adapts PaperExecutionEngine to match ExecutionEngine interface."""
        def send_market_order(self, intent):
            r = paper_engine.send_market_order(intent, strategy=intent.comment)
            return ExecutionResult(
                success=r.success,
                retcode=0,
                order_id=r.ticket,
                volume_filled=r.volume_filled,
                fill_price=r.fill_price,
                comment=r.comment,
                error_description=r.error_description,
                category=r.category or "paper",
            )

    exe_mod.execution_engine = _PaperWrapper()

    from orchestrator import Orchestrator
    import time

    class PaperOrchestrator(Orchestrator):
        """
        FIX PP3: Overrides the supervision loop to call
        paper_engine.update_positions() on every tick, simulating
        the SL/TP checking that MT5 would perform on live orders.
        """
        def run(self) -> None:
            self._register_signals()

            from core.logger import get_logger
            _log = get_logger("paper_orchestrator")
            _log.info("Paper mode: SL/TP simulation active")

            self._connect_mt5()

            from orchestration.plugin_loader import plugin_loader
            from orchestration.strategy_registry import strategy_registry
            from core.metrics_store import metrics_store
            from core.mt5_connector import connector

            strategies = plugin_loader.discover_all()
            if not strategies:
                import sys as _sys
                _log.critical("No strategies loaded")
                _sys.exit(1)

            for name, strategy in strategies.items():
                strategy_registry.register(strategy)
                self._launch_bot(name, strategy)

            _log.info("Paper bots launched: %s", list(self._runners.keys()))

            last_health  = time.monotonic()
            last_mt5     = time.monotonic()
            last_flush   = time.monotonic()

            try:
                while not self._shutdown_requested:
                    now = time.monotonic()

                    # FIX PP3: Check SL/TP for all open paper positions
                    try:
                        closed = paper_engine.update_positions()
                        if closed:
                            _log.debug("Paper closed %d position(s)", len(closed))
                    except Exception as exc:
                        _log.warning("paper update_positions error: %s", exc)

                    if now - last_mt5 >= 15:
                        self._check_mt5_connection()
                        last_mt5 = now

                    if now - last_health >= 30:
                        self._check_bot_health()
                        last_health = now

                    if now - last_flush >= 300:
                        metrics_store.flush()
                        last_flush = now

                    time.sleep(1)

            except KeyboardInterrupt:
                _log.info("Interrupted")

            self._shutdown()

    PaperOrchestrator().run()


def _run_real() -> None:
    """Demo or live mode: real MT5 orders submitted."""
    from orchestrator import Orchestrator
    Orchestrator().run()


if __name__ == "__main__":
    main()
