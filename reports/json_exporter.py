"""
JSON Exporter

Exports metrics snapshots and registry state to JSON for
programmatic consumption by external analysis tools.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from core.logger import get_logger
from core.metrics_store import metrics_store
from core.utils import ensure_dir

log = get_logger("json_exporter")

_REPORT_DIR = Path("data/reports")


def export_metrics_snapshot(output_path: Path | None = None) -> Path:
    """Export current in-memory metrics to JSON."""
    ensure_dir(_REPORT_DIR)
    out = output_path or _REPORT_DIR / "metrics_snapshot.json"

    snapshot = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "strategies":  metrics_store.all_summaries(),
    }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)

    log.info("Metrics snapshot exported → %s", out)
    return out


def export_registry_summary(output_path: Path | None = None) -> Path:
    """Export strategy registry state to JSON."""
    ensure_dir(_REPORT_DIR)
    out = output_path or _REPORT_DIR / "registry_summary.json"

    try:
        from orchestration.strategy_registry import strategy_registry
        data: Dict[str, Any] = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "strategies":  strategy_registry.summary(),
        }
    except Exception as exc:
        data = {"error": str(exc), "strategies": []}

    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    log.info("Registry summary exported → %s", out)
    return out
