"""
Plugin Loader

Auto-discovers strategy plugins from the strategies/ directory.

HOW IT WORKS
============
1. Scans every .py file in strategies/ (skipping __init__.py,
   base_strategy.py, and files starting with _ or template)
2. Imports each module via importlib
3. Finds BaseStrategy subclasses using inspect
4. Runs the full PluginValidator pipeline (metadata → values → config → conflicts)
5. Instantiates the class and yields it for registration
6. Prints a formatted startup table summarising all outcomes

ADDING A NEW STRATEGY (3 steps)
================================
1. Copy strategies/template_strategy.py → strategies/my_strategy.py
2. Copy config/strategies/template.yaml → config/strategies/my_strategy.yaml
3. Restart the orchestrator

No other files need editing.

FAILURE HANDLING
================
- Malformed plugin → logged, skipped, remaining plugins load normally
- Duplicate name   → logged as CONFLICT, both plugins skipped
- Missing config   → WARNING only, plugin still loads (uses defaults)
- Import error     → logged with full traceback excerpt
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Type

from core.exceptions import PluginLoadError, PluginMetadataError
from core.logger import get_logger
from core.settings import settings
from orchestration.plugin_validator import plugin_validator
from strategies.base_strategy import BaseStrategy

log = get_logger("plugin_loader")

_STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"

_EXCLUDED_FILES = {
    "__init__.py",
    "base_strategy.py",
}
# Also skip anything starting with _ or named template*
_SKIP_PREFIXES = ("_", "template")


# ─── Discovery result dataclass ───────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    """Outcome of attempting to load one plugin file."""
    filename:  str
    status:    str   = "unknown"   # accepted | rejected | disabled | skipped | warning
    name:      str   = ""
    version:   str   = ""
    symbol:    str   = ""
    timeframe: str   = ""
    magic:     int   = 0
    reason:    str   = ""
    warnings:  List[str] = field(default_factory=list)


# ─── Plugin Loader ────────────────────────────────────────────────────────────

class PluginLoader:
    """
    Scans strategies/ and returns a dict of {strategy_name: instance}.
    Emits a formatted startup table to the log.
    """

    def __init__(self, strategies_dir: Path = _STRATEGIES_DIR) -> None:
        self._dir = strategies_dir

    def discover_all(self) -> Dict[str, BaseStrategy]:
        """
        Scan, validate, and instantiate all valid strategy plugins.

        Returns:
            Dict mapping strategy_name → BaseStrategy instance.
        """
        plugin_validator.reset()
        results: List[DiscoveryResult] = []
        discovered: Dict[str, BaseStrategy] = {}

        py_files = sorted(self._dir.glob("*.py"))

        for py_file in py_files:
            result = self._process_file(py_file, discovered)
            results.append(result)

        # Print startup table
        self._print_startup_table(results)

        accepted = {name: inst for name, inst in discovered.items()}
        return accepted

    # ─── Per-file processing ──────────────────────────────────────────────

    def _process_file(
        self, py_file: Path, discovered: Dict[str, BaseStrategy]
    ) -> DiscoveryResult:
        result = DiscoveryResult(filename=py_file.name)

        # Skip reserved files
        if py_file.name in _EXCLUDED_FILES:
            result.status = "skipped"
            result.reason = "framework file"
            return result

        if any(py_file.name.startswith(p) for p in _SKIP_PREFIXES):
            result.status = "skipped"
            result.reason = "template or private file"
            return result

        # Import
        try:
            cls = self._import_strategy_class(py_file)
        except PluginLoadError as exc:
            result.status = "rejected"
            result.reason = str(exc)
            log.error("✗ REJECTED [%s]\n  %s", py_file.name, exc)
            return result
        except Exception as exc:
            result.status = "rejected"
            result.reason = f"Unexpected import error: {exc}"
            log.error(
                "✗ REJECTED [%s] unexpected error:\n%s",
                py_file.name,
                traceback.format_exc(limit=5),
            )
            return result

        if cls is None:
            result.status = "skipped"
            result.reason = "no BaseStrategy subclass found"
            log.warning("⚠  SKIPPED [%s] — no BaseStrategy subclass found", py_file.name)
            return result

        # Run validation pipeline
        try:
            warnings = plugin_validator.validate(cls, py_file.name)
        except PluginLoadError as exc:
            result.status = "rejected"
            result.name   = getattr(getattr(cls, "metadata", None), "name", cls.__name__)
            result.reason = str(exc)
            log.error("✗ REJECTED [%s]\n  %s", py_file.name, exc)
            return result
        except Exception as exc:
            result.status = "rejected"
            result.reason = f"Validation error: {exc}"
            log.error("✗ REJECTED [%s] validation crashed: %s", py_file.name, exc)
            return result

        meta = cls.metadata

        # Check enabled flag in config
        cfg = settings.strategy_config(meta.name)
        if not cfg.get("enabled", True):
            result.status = "disabled"
            result.name   = meta.name
            result.version = meta.version
            result.reason  = "disabled in config (set 'enabled: true' to activate)"
            log.info("○ DISABLED [%s] — %s", py_file.name, result.reason)
            return result

        # Instantiate
        try:
            instance = cls()
        except Exception as exc:
            result.status = "rejected"
            result.name   = meta.name
            result.reason = f"Instantiation failed: {exc}"
            log.error(
                "✗ REJECTED [%s] instantiation error:\n%s",
                py_file.name, traceback.format_exc(limit=5),
            )
            return result

        # All good
        magic = (
            settings.execution.get("magic_base", 200000)
            + cfg.get("magic_offset", meta.magic_offset)
        )
        result.status    = "accepted" if not warnings else "warning"
        result.name      = meta.name
        result.version   = meta.version
        result.symbol    = cfg.get("symbol", meta.symbols[0] if meta.symbols else "?")
        result.timeframe = cfg.get("timeframe", meta.timeframes[0] if meta.timeframes else "?")
        result.magic     = magic
        result.warnings  = warnings

        for w in warnings:
            log.warning("⚠  [%s] %s", meta.name, w)

        discovered[meta.name] = instance
        return result

    # ─── Import helper ────────────────────────────────────────────────────

    def _import_strategy_class(self, py_file: Path) -> Optional[Type[BaseStrategy]]:
        """
        Import a .py file and find the first valid BaseStrategy subclass.
        Returns None if no subclass is found.
        Raises PluginLoadError on import failure.
        """
        module_name = f"strategies.{py_file.stem}"

        try:
            if module_name in sys.modules:
                module = importlib.reload(sys.modules[module_name])
            else:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    raise PluginLoadError(f"Cannot create module spec for {py_file.name}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
        except PluginLoadError:
            raise
        except Exception as exc:
            # Extract the most useful part of the traceback
            tb_lines = traceback.format_exc().splitlines()
            short_tb = "\n".join(tb_lines[-6:])
            raise PluginLoadError(
                f"Import error in {py_file.name}:\n{short_tb}"
            ) from exc

        candidates = [
            obj
            for _name, obj in inspect.getmembers(module, inspect.isclass)
            if (
                obj is not BaseStrategy
                and issubclass(obj, BaseStrategy)
                and obj.__module__ == module_name
            )
        ]

        if not candidates:
            return None

        if len(candidates) > 1:
            names = [c.__name__ for c in candidates]
            log.warning(
                "[%s] Multiple BaseStrategy subclasses found: %s. "
                "Using first: %s. Put each strategy in its own file.",
                py_file.name, names, candidates[0].__name__,
            )

        return candidates[0]

    # ─── Startup table ────────────────────────────────────────────────────

    def _print_startup_table(self, results: List[DiscoveryResult]) -> None:
        """
        Emit a formatted summary table to the log at startup.
        Shows every file scanned and its outcome.
        """
        accepted = [r for r in results if r.status in ("accepted", "warning")]
        rejected = [r for r in results if r.status == "rejected"]
        disabled = [r for r in results if r.status == "disabled"]
        skipped  = [r for r in results if r.status == "skipped"]

        # ── Header ──────────────────────────────────────────────────────
        log.info("=" * 70)
        log.info("  STRATEGY PLUGIN DISCOVERY — %d scanned", len(results))
        log.info("  ✓ Accepted: %-3d  ✗ Rejected: %-3d  ○ Disabled: %-3d",
                 len(accepted), len(rejected), len(disabled))
        log.info("=" * 70)

        if not accepted:
            log.warning("  No strategies loaded — no bots will run!")
        else:
            # ── Table ────────────────────────────────────────────────────
            col_w = [22, 7, 8, 5, 8, 10]
            header = (
                f"{'Strategy':<{col_w[0]}} "
                f"{'Ver':<{col_w[1]}} "
                f"{'Symbol':<{col_w[2]}} "
                f"{'TF':<{col_w[3]}} "
                f"{'Magic':<{col_w[4]}} "
                f"{'Status':<{col_w[5]}}"
            )
            divider = "-" * sum(col_w) + "-" * (len(col_w) - 1)
            log.info("  %s", header)
            log.info("  %s", divider)
            for r in sorted(accepted, key=lambda x: x.name):
                status_str = "✓ ok" if r.status == "accepted" else "⚠  warn"
                log.info(
                    "  %-*s %-*s %-*s %-*s %-*d %-*s",
                    col_w[0], r.name,
                    col_w[1], r.version,
                    col_w[2], r.symbol,
                    col_w[3], r.timeframe,
                    col_w[4], r.magic,
                    col_w[5], status_str,
                )
            log.info("  %s", divider)

        # ── Rejected details ─────────────────────────────────────────────
        for r in rejected:
            log.error("  ✗ REJECTED  %-20s  %s", r.filename, r.reason.split("\n")[0])

        # ── Disabled notice ──────────────────────────────────────────────
        for r in disabled:
            log.info("  ○ DISABLED  %-20s  (enabled: false in config)", r.name or r.filename)

        # ── Warnings inline ──────────────────────────────────────────────
        for r in accepted:
            for w in r.warnings:
                log.warning("  ⚠  [%s] %s", r.name, w)

        log.info("=" * 70)

        # ── Quick copy-paste hint if no strategies loaded ────────────────
        if not accepted:
            log.info(
                "  HINT: To add a new strategy:\n"
                "    1. cp strategies/template_strategy.py strategies/my_name.py\n"
                "    2. cp config/strategies/template.yaml config/strategies/my_name.yaml\n"
                "    3. Edit both files, then restart the orchestrator."
            )


# Module-level singleton
plugin_loader = PluginLoader()
