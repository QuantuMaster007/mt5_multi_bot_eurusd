"""
CSV Exporter

Converts JSONL trade and event logs into flat CSV files suitable for
analysis in Excel, pandas, or any spreadsheet tool.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from core.utils import ensure_dir

log = get_logger("csv_exporter")

_REPORT_DIR = Path("data/reports")
_TRADES_FILE = Path("data/trades/trades.jsonl")
_EVENTS_FILE = Path("data/events/events.jsonl")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    if not path.exists():
        log.warning("File not found: %s", path)
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _write_csv(records: List[Dict], path: Path) -> None:
    if not records:
        log.info("No records to write for %s", path.name)
        return

    # Collect all keys across all records for consistent columns
    fieldnames: List[str] = []
    seen = set()
    for rec in records:
        for k in rec.keys():
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)

    ensure_dir(path.parent)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    log.info("Wrote %d records → %s", len(records), path)


def export_trades(output_path: Optional[Path] = None) -> Path:
    """Export all trade records to CSV."""
    out = output_path or _REPORT_DIR / "trades_export.csv"
    records = _load_jsonl(_TRADES_FILE)
    _write_csv(records, out)
    return out


def export_events(
    event_types: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Export event records to CSV, optionally filtered by event type.

    Args:
        event_types:  If provided, only records whose 'event' field
                      matches one of these strings are included.
        output_path:  Override default output path.
    """
    out = output_path or _REPORT_DIR / "events_export.csv"
    records = _load_jsonl(_EVENTS_FILE)

    if event_types:
        records = [r for r in records if r.get("event") in event_types]

    _write_csv(records, out)
    return out


def export_policy_decisions(output_path: Optional[Path] = None) -> Path:
    return export_events(
        event_types=["policy_decision"],
        output_path=output_path or _REPORT_DIR / "policy_decisions.csv",
    )


def export_order_events(output_path: Optional[Path] = None) -> Path:
    return export_events(
        event_types=["order_sent", "order_filled", "order_rejected"],
        output_path=output_path or _REPORT_DIR / "order_events.csv",
    )
