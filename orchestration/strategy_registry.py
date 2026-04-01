"""
Strategy Registry

The canonical, in-memory catalogue of all loaded strategy plugins.

Extended with:
  - print_table()   rich console listing for CLI tools
  - log_table()     same output via logger (shown at startup)
  - config_summary() shows effective config for each strategy
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.constants import STATE_ENABLED
from core.logger import get_logger
from strategies.base_strategy import BaseStrategy, StrategyMetadata

log = get_logger("strategy_registry")


@dataclass
class RegistryEntry:
    strategy:     BaseStrategy
    metadata:     StrategyMetadata
    config:       Dict[str, Any] = field(default_factory=dict)
    state:        str = STATE_ENABLED
    state_reason: str = ""
    runner_alive: bool = False
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StrategyRegistry:
    """
    Thread-safe registry of all loaded strategy plugins.

    Supports rich introspection:
        strategy_registry.print_table()   → console table
        strategy_registry.log_table()     → same via logger
        strategy_registry.as_json()       → JSON string for APIs
    """

    def __init__(self) -> None:
        self._entries: Dict[str, RegistryEntry] = {}

    # ─── Mutation ─────────────────────────────────────────────────────────

    def register(self, strategy: BaseStrategy, config: Optional[Dict] = None) -> None:
        from core.settings import settings
        name = strategy.metadata.name
        if name in self._entries:
            log.warning("Strategy %s already registered — overwriting", name)

        cfg = config or settings.strategy_config(name)
        self._entries[name] = RegistryEntry(
            strategy=strategy,
            metadata=strategy.metadata,
            config=cfg,
        )
        log.info(
            "Registered | %-22s v%-8s magic=%-6d  tf=%-5s  symbol=%s",
            name, strategy.metadata.version, strategy.magic,
            cfg.get("timeframe", "?"), cfg.get("symbol", "?"),
        )

    def unregister(self, name: str) -> None:
        self._entries.pop(name, None)
        log.info("Unregistered strategy: %s", name)

    # ─── Query ────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[RegistryEntry]:
        return self._entries.get(name)

    def all_names(self) -> List[str]:
        return list(self._entries.keys())

    def all_strategies(self) -> List[BaseStrategy]:
        return [e.strategy for e in self._entries.values()]

    def count(self) -> int:
        return len(self._entries)

    # ─── State updates ────────────────────────────────────────────────────

    def update_state(self, name: str, state: str, reason: str = "") -> None:
        entry = self._entries.get(name)
        if entry:
            entry.state        = state
            entry.state_reason = reason
            log.info("State update | %s → %s  (%s)", name, state, reason)

    def set_runner_alive(self, name: str, alive: bool) -> None:
        entry = self._entries.get(name)
        if entry:
            entry.runner_alive = alive

    # ─── Rich output ──────────────────────────────────────────────────────

    def log_table(self) -> None:
        """Emit the full registry table via logger (INFO level)."""
        lines = self._build_table_lines()
        for line in lines:
            log.info(line)

    def print_table(self) -> None:
        """Print the registry table to stdout (for CLI tools)."""
        for line in self._build_table_lines():
            print(line)

    def _build_table_lines(self) -> List[str]:
        col = [22, 7, 8, 5, 8, 10, 14]
        header = (
            f"{'Strategy':<{col[0]}} "
            f"{'Ver':<{col[1]}} "
            f"{'Symbol':<{col[2]}} "
            f"{'TF':<{col[3]}} "
            f"{'Magic':<{col[4]}} "
            f"{'State':<{col[5]}} "
            f"{'Runner':<{col[6]}}"
        )
        divider = "─" * (sum(col) + len(col) - 1)
        lines = [
            "",
            f"  STRATEGY REGISTRY  ({self.count()} loaded)",
            f"  {divider}",
            f"  {header}",
            f"  {divider}",
        ]
        for name, entry in sorted(self._entries.items()):
            cfg = entry.config
            runner_str = "● running" if entry.runner_alive else "○ stopped"
            lines.append(
                f"  "
                f"{name:<{col[0]}} "
                f"{entry.metadata.version:<{col[1]}} "
                f"{cfg.get('symbol', '?'):<{col[2]}} "
                f"{cfg.get('timeframe', '?'):<{col[3]}} "
                f"{entry.strategy.magic:<{col[4]}} "
                f"{entry.state:<{col[5]}} "
                f"{runner_str:<{col[6]}}"
            )
            if entry.state_reason:
                lines.append(f"  {'':>{col[0]+1}}reason: {entry.state_reason}")
        lines.append(f"  {divider}")
        lines.append("")
        return lines

    def config_summary(self, name: str) -> str:
        """Return a human-readable config dump for one strategy."""
        entry = self._entries.get(name)
        if not entry:
            return f"Strategy '{name}' not found in registry."
        lines = [f"\nConfig for strategy: {name}"]
        lines.append("-" * 40)
        for k, v in sorted(entry.config.items()):
            lines.append(f"  {k:<25} = {v!r}")
        lines.append("")
        return "\n".join(lines)

    def summary(self) -> List[Dict]:
        """Machine-readable summary for JSON export and health_check.py."""
        return [
            {
                "name":          e.metadata.name,
                "version":       e.metadata.version,
                "description":   e.metadata.description,
                "state":         e.state,
                "state_reason":  e.state_reason,
                "runner_alive":  e.runner_alive,
                "symbols":       e.metadata.symbols,
                "timeframes":    e.metadata.timeframes,
                "regime_tags":   e.metadata.regime_tags,
                "risk_profile":  e.metadata.risk_profile,
                "magic":         e.strategy.magic,
                "registered_at": e.registered_at,
            }
            for e in self._entries.values()
        ]

    def as_json(self, indent: int = 2) -> str:
        return json.dumps(self.summary(), indent=indent, default=str)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries


# Module-level singleton
strategy_registry = StrategyRegistry()
