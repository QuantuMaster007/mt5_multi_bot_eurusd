"""
Analytics

Lightweight in-process helpers for summarising trade and event data
that the orchestrator and weekly_review can call during operation.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from core.logger import get_logger
from core.utils import safe_divide

log = get_logger("analytics")


def load_trade_records(path: str = "data/trades/trades.jsonl") -> List[Dict]:
    """Load all trade records from the JSONL trade log."""
    records = []
    p = Path(path)
    if not p.exists():
        return records
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def strategy_pnl_summary(records: List[Dict]) -> Dict[str, Dict]:
    """
    Summarise realised P&L per strategy from close records.
    Returns dict keyed by strategy name.
    """
    by_strat: Dict[str, Dict] = defaultdict(lambda: {
        "trades": 0, "pnl": 0.0, "wins": 0, "losses": 0
    })

    for r in records:
        event = r.get("event", "")
        if "close" not in event:
            continue
        strategy = r.get("strategy", "unknown")
        pnl      = float(r.get("pnl", 0))
        s        = by_strat[strategy]
        s["trades"] += 1
        s["pnl"]    += pnl
        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1

    for name, s in by_strat.items():
        s["win_rate"] = round(safe_divide(s["wins"], s["trades"]), 3)
        s["pnl"]      = round(s["pnl"], 2)

    return dict(by_strat)


def top_rejection_reasons(
    event_path: str = "data/events/events.jsonl", top_n: int = 10
) -> List[Tuple[str, int]]:
    """
    Return the most common order rejection reasons.
    """
    counts: Dict[str, int] = defaultdict(int)
    p = Path(event_path)
    if not p.exists():
        return []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("event") == "order_rejected":
                    key = rec.get("category", "unknown")
                    counts[key] += 1
            except json.JSONDecodeError:
                pass

    return sorted(counts.items(), key=lambda x: -x[1])[:top_n]
