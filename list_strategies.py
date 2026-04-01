"""
list_strategies.py — CLI tool for strategy discovery inspection.

Shows every strategy file found in strategies/ and its current status
(accepted, rejected, disabled, skipped), with full validation output.

Usage::

    python list_strategies.py           # discovery scan + table
    python list_strategies.py --config  # also show effective config per strategy
    python list_strategies.py --json    # machine-readable JSON output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List all strategy plugins and their discovery status."
    )
    parser.add_argument(
        "--config", action="store_true",
        help="Show effective config for each accepted strategy",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output registry as JSON (accepted strategies only)",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Run discovery and validation but do not connect to MT5",
    )
    args = parser.parse_args()

    # Initialise logging (quiet for CLI)
    from core.logger import init_logging
    from core.settings import settings
    init_logging(level="WARNING", console=True)

    print()
    print("═" * 70)
    print("  MT5 Multi-Bot — Strategy Plugin Scanner")
    print("═" * 70)

    # Run discovery (validation happens inside)
    from orchestration.plugin_loader import plugin_loader
    from orchestration.strategy_registry import strategy_registry

    discovered = plugin_loader.discover_all()

    if not discovered:
        print("\n  ✗ No strategies loaded.\n")
        print("  To add a strategy:")
        print("    1. cp strategies/template_strategy.py  strategies/my_name.py")
        print("    2. cp config/strategies/template.yaml  config/strategies/my_name.yaml")
        print("    3. Edit both files")
        print("    4. Run: python list_strategies.py\n")
        sys.exit(0)

    # Register for display
    for name, strategy in discovered.items():
        strategy_registry.register(strategy)

    # Output
    if args.json:
        print(strategy_registry.as_json())
        return

    # Rich table
    strategy_registry.print_table()

    # Config dump
    if args.config:
        for name in sorted(discovered.keys()):
            print(strategy_registry.config_summary(name))

    # Summary counts
    total_files = len(list(Path("strategies").glob("*.py")))
    loaded = len(discovered)
    print(f"  Scanned {total_files} .py files | {loaded} strategies loaded\n")

    # Point to docs
    if loaded > 0:
        print("  For developer docs:")
        print("    docs/HOW_TO_ADD_STRATEGY.md")
        print()


if __name__ == "__main__":
    main()
