"""
Weekly Report Builder

FIX R1: Trade close event filter now matches both "trade_close" (paper
         and live) and "trade_close_detected" (order_manager reconcile).
         Previously only "paper_trade_close" was matched, producing
         empty strategy summaries for demo/live mode.
FIX R2: Adds hold time calculation from open_time/close_time fields
         which are now consistently logged in both modes.
FIX R3: Consecutive loss calculation re-plays in correct chronological
         order by sorting on the 'ts' field before processing.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from core.utils import safe_divide, ensure_dir

log = get_logger("weekly_report_builder")

_EVENTS_FILE = Path("data/events/events.jsonl")
_TRADES_FILE = Path("data/trades/trades.jsonl")
_REPORT_DIR  = Path("data/reports")

# FIX R1: All event names that represent a completed round-trip trade close
_CLOSE_EVENTS = {"trade_close", "trade_close_detected", "paper_trade_close"}


def _load_jsonl(path: Path, since: datetime) -> List[Dict]:
    records = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts", "")
                if not ts_str:
                    continue
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= since:
                    records.append(rec)
            except Exception:
                pass
    return records


class WeeklyReportBuilder:

    def build(self, days: int = 7) -> Dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        ensure_dir(_REPORT_DIR)

        log.info("Building %d-day report since %s", days, since.date())

        events = _load_jsonl(_EVENTS_FILE, since)
        trades = _load_jsonl(_TRADES_FILE, since)

        report = {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "period_days":        days,
            "since":              since.isoformat(),
            "strategy_summaries": self._strategy_summaries(trades),
            "execution_diagnostics": self._execution_diagnostics(events),
            "policy_summary":     self._policy_summary(events),
            "risk_summary":       self._risk_summary(events),
            "regime_summary":     self._regime_summary(events),
            "spread_summary":     self._spread_summary(events),
            "session_summary":    self._session_summary(events),
            "suggestions":        self._generate_suggestions(trades, events),
        }

        self._write_json(report)
        self._write_csv(report)
        self._write_markdown(report)

        log.info("Report written to %s", _REPORT_DIR)
        return report

    # ─── Strategy summaries ───────────────────────────────────────────────

    def _strategy_summaries(self, trades: List[Dict]) -> Dict[str, Dict]:
        # FIX R1: Only process known close events
        closes = [t for t in trades if t.get("event") in _CLOSE_EVENTS]

        # FIX R3: Sort by timestamp for correct consecutive loss sequencing
        closes.sort(key=lambda t: t.get("ts", ""))

        by_strategy: Dict[str, Dict] = defaultdict(lambda: {
            "trades": 0, "wins": 0, "losses": 0,
            "gross_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
            "pnl_pips": 0.0, "max_drawdown": 0.0, "peak_pnl": 0.0,
            "consec_losses": 0, "max_consec_losses": 0,
            "total_hold_minutes": 0.0,
        })

        for t in closes:
            strategy = t.get("strategy", "unknown")
            pnl      = float(t.get("pnl", 0.0))
            pips     = float(t.get("pnl_pips", 0.0))
            s        = by_strategy[strategy]

            s["trades"] += 1
            s["gross_pnl"] += pnl
            s["pnl_pips"]  += pips

            # Hold time
            try:
                open_t   = datetime.fromisoformat(t.get("open_time", ""))
                close_t  = datetime.fromisoformat(t.get("close_time", ""))
                s["total_hold_minutes"] += (close_t - open_t).total_seconds() / 60
            except Exception:
                pass

            if pnl >= 0:
                s["wins"]          += 1
                s["gross_win"]     += pnl
                s["consec_losses"]  = 0
            else:
                s["losses"]        += 1
                s["gross_loss"]    += abs(pnl)
                s["consec_losses"] += 1
                s["max_consec_losses"] = max(
                    s["max_consec_losses"], s["consec_losses"]
                )

            if s["gross_pnl"] > s["peak_pnl"]:
                s["peak_pnl"] = s["gross_pnl"]
            dd = s["peak_pnl"] - s["gross_pnl"]
            if dd > s["max_drawdown"]:
                s["max_drawdown"] = dd

        result = {}
        for name, s in by_strategy.items():
            t_count   = s["trades"]
            wins      = s["wins"]
            losses    = s["losses"]
            gross_win = s["gross_win"]
            gross_loss= s["gross_loss"]
            win_rate  = safe_divide(wins, t_count)
            avg_win   = safe_divide(gross_win, wins)
            avg_loss  = safe_divide(gross_loss, losses)
            expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

            result[name] = {
                "trades":           t_count,
                "wins":             wins,
                "losses":           losses,
                "win_rate_pct":     round(win_rate * 100, 1),
                "gross_pnl":        round(s["gross_pnl"], 2),
                "pnl_pips":         round(s["pnl_pips"], 1),
                "avg_win":          round(avg_win, 2),
                "avg_loss":         round(avg_loss, 2),
                "expectancy":       round(expectancy, 3),
                "profit_factor":    round(safe_divide(gross_win, gross_loss), 2),
                "max_drawdown":     round(s["max_drawdown"], 2),
                "max_consec_losses":s["max_consec_losses"],
                "avg_hold_minutes": round(
                    safe_divide(s["total_hold_minutes"], t_count), 1
                ),
            }
        return result

    # ─── Execution diagnostics ────────────────────────────────────────────

    def _execution_diagnostics(self, events: List[Dict]) -> Dict:
        attempts  = [e for e in events if e.get("event") == "order_sent"]
        filled    = [e for e in events if e.get("event") == "order_filled"]
        rejected  = [e for e in events if e.get("event") == "order_rejected"]

        by_category: Dict[str, int] = defaultdict(int)
        for r in rejected:
            by_category[r.get("category", "unknown")] += 1

        return {
            "total_attempts":      len(attempts),
            "total_filled":        len(filled),
            "total_rejected":      len(rejected),
            "fill_rate_pct":       round(
                safe_divide(len(filled), len(attempts)) * 100, 1
            ),
            "rejection_by_category": dict(sorted(
                by_category.items(), key=lambda x: -x[1]
            )),
        }

    def _policy_summary(self, events: List[Dict]) -> Dict:
        policy_events = [e for e in events if e.get("event") == "policy_decision"]
        by_reason: Dict[str, int]   = defaultdict(int)
        by_strategy: Dict[str, int] = defaultdict(int)
        for e in policy_events:
            by_reason[e.get("reason_code", "unknown")] += 1
            by_strategy[e.get("strategy", "unknown")]  += 1
        return {
            "total_policy_blocks": len(policy_events),
            "by_reason":           dict(sorted(by_reason.items(), key=lambda x: -x[1])),
            "by_strategy":         dict(by_strategy),
        }

    def _risk_summary(self, events: List[Dict]) -> Dict:
        risk_events = [e for e in events if e.get("event") == "risk_block"]
        by_strategy: Dict[str, int] = defaultdict(int)
        by_reason:   Dict[str, int] = defaultdict(int)
        for e in risk_events:
            by_strategy[e.get("strategy", "unknown")] += 1
            by_reason[str(e.get("reason", ""))[:80]]  += 1
        return {
            "total_risk_blocks": len(risk_events),
            "by_strategy":       dict(by_strategy),
            "top_reasons":       dict(sorted(
                by_reason.items(), key=lambda x: -x[1]
            )[:5]),
        }

    def _regime_summary(self, events: List[Dict]) -> Dict:
        signals = [e for e in events if e.get("event") == "signal"]
        regime_counts: Dict[str, int] = defaultdict(int)
        for s in signals:
            for r in (s.get("regimes") or []):
                regime_counts[r] += 1
        return {
            "signal_count_by_regime": dict(
                sorted(regime_counts.items(), key=lambda x: -x[1])
            )
        }

    def _spread_summary(self, events: List[Dict]) -> Dict:
        blocks = [
            e for e in events
            if e.get("event") in ("risk_block", "policy_decision")
            and "spread" in str(e.get("reason", "")).lower()
        ]
        return {
            "spread_blocks": len(blocks),
            "stale_ticks":   sum(
                1 for e in events if e.get("event") == "stale_tick"
            ),
        }

    def _session_summary(self, events: List[Dict]) -> Dict:
        sess = [e for e in events if e.get("event") == "session_filter"]
        by_strategy: Dict[str, int] = defaultdict(int)
        for e in sess:
            by_strategy[e.get("strategy", "unknown")] += 1
        return {
            "total_session_blocks": len(sess),
            "by_strategy":          dict(by_strategy),
        }

    # ─── Suggestions ──────────────────────────────────────────────────────

    def _generate_suggestions(
        self, trades: List[Dict], events: List[Dict]
    ) -> List[str]:
        suggestions: List[str] = []
        summaries = self._strategy_summaries(trades)
        exec_diag = self._execution_diagnostics(events)

        for name, s in summaries.items():
            if s["trades"] == 0:
                suggestions.append(
                    f"{name}: Zero trades recorded this week. "
                    f"Check that the strategy is enabled, sessions overlap with "
                    f"market open, and policy/risk gates are not permanently blocking it."
                )
                continue

            if s["win_rate_pct"] < 35 and s["trades"] >= 10:
                suggestions.append(
                    f"{name}: Win rate {s['win_rate_pct']}% over {s['trades']} trades "
                    f"is below 35%. Consider reviewing entry conditions or tightening "
                    f"the regime compatibility filter."
                )

            if s["profit_factor"] < 0.8 and s["trades"] >= 5:
                suggestions.append(
                    f"{name}: Profit factor {s['profit_factor']:.2f} < 0.8. "
                    f"Spread and execution costs may be eroding all gains. "
                    f"Review TP/SL ratio and spread gate settings."
                )

            if s["max_consec_losses"] >= 5:
                suggestions.append(
                    f"{name}: {s['max_consec_losses']} consecutive losses in this period. "
                    f"Review market regime compatibility and consider raising "
                    f"policy consecutive_loss_disable_threshold."
                )

            if s["avg_hold_minutes"] < 5 and name == "scalping":
                suggestions.append(
                    f"{name}: Average hold time {s['avg_hold_minutes']:.1f} min — "
                    f"very short. Confirm min_bars_between_trades is enforced and "
                    f"review spread cost vs TP size."
                )

        reject_rate = 100.0 - exec_diag.get("fill_rate_pct", 100.0)
        if reject_rate > 20:
            suggestions.append(
                f"Execution: {reject_rate:.1f}% order rejection rate. "
                f"Review broker profile fill modes, spread limits, and stop levels. "
                f"Check rejection_by_category in this report for root cause."
            )

        stale = sum(
            1 for e in events if e.get("event") == "stale_tick"
        )
        if stale > 10:
            suggestions.append(
                f"Data: {stale} stale tick events this week. "
                f"MT5 data feed may be unreliable. "
                f"Check internet connection, broker server status, and session filter settings."
            )

        reconnects = sum(1 for e in events if e.get("event") == "mt5_reconnect")
        if reconnects > 3:
            suggestions.append(
                f"Connectivity: {reconnects} MT5 reconnect events this week. "
                f"Unstable connection will degrade execution quality. "
                f"Consider using a VPS closer to the broker's server."
            )

        if not suggestions:
            suggestions.append(
                "No automatic issues detected this week. "
                "Manual review of the trade journal is still recommended."
            )

        return suggestions

    # ─── Writers ──────────────────────────────────────────────────────────

    def _write_json(self, report: Dict) -> None:
        path = _REPORT_DIR / "weekly_summary.json"
        with open(path, "w") as f:
            json.dump(report, f, indent=2, default=str)

    def _write_csv(self, report: Dict) -> None:
        path = _REPORT_DIR / "weekly_summary.csv"
        rows = []
        for name, s in report["strategy_summaries"].items():
            row = {"strategy": name}
            row.update(s)
            rows.append(row)
        if not rows:
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _write_markdown(self, report: Dict) -> None:
        path = _REPORT_DIR / "weekly_summary.md"
        lines = [
            "# Weekly Performance Report",
            f"**Generated:** {report['generated_at']}",
            f"**Period:** Last {report['period_days']} days\n",
            "---",
            "## Strategy Performance\n",
        ]

        summaries = report["strategy_summaries"]
        if summaries:
            headers = [
                "Strategy", "Trades", "Win%", "PnL $", "PnL Pips",
                "Expectancy", "PF", "Max DD", "Max CL", "Avg Hold(m)"
            ]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")
            for name, s in sorted(
                summaries.items(),
                key=lambda x: x[1].get("gross_pnl", 0),
                reverse=True,
            ):
                lines.append(
                    f"| {name} | {s['trades']} | {s['win_rate_pct']}% "
                    f"| {s['gross_pnl']:+.2f} | {s['pnl_pips']:+.1f} "
                    f"| {s['expectancy']:.3f} | {s['profit_factor']:.2f} "
                    f"| {s['max_drawdown']:.2f} | {s['max_consec_losses']} "
                    f"| {s['avg_hold_minutes']:.1f} |"
                )
        else:
            lines.append(
                "_No trade close records found for this period. "
                "If running in demo mode, ensure order_manager.reconcile() "
                "is being called regularly._"
            )

        ed = report["execution_diagnostics"]
        lines += [
            "\n---", "## Execution Diagnostics\n",
            f"Attempts: **{ed['total_attempts']}** | "
            f"Filled: **{ed['total_filled']}** | "
            f"Rejected: **{ed['total_rejected']}** | "
            f"Fill Rate: **{ed['fill_rate_pct']}%**",
        ]
        for cat, count in ed.get("rejection_by_category", {}).items():
            lines.append(f"- `{cat}`: {count}")

        pd_ = report["policy_summary"]
        lines += ["\n---", "## Policy Blocks\n",
                  f"Total: **{pd_['total_policy_blocks']}**"]
        for r, c in pd_.get("by_reason", {}).items():
            lines.append(f"- `{r}`: {c}")

        rd = report["risk_summary"]
        lines += ["\n---", "## Risk Blocks\n",
                  f"Total: **{rd['total_risk_blocks']}**"]

        spread = report["spread_summary"]
        lines += ["\n---", "## Data Quality\n",
                  f"Spread blocks: **{spread['spread_blocks']}** | "
                  f"Stale ticks: **{spread['stale_ticks']}**"]

        reconnects = sum(
            1 for e in _load_jsonl(_EVENTS_FILE, datetime.now(timezone.utc) - timedelta(days=7))
            if e.get("event") == "mt5_reconnect"
        ) if _EVENTS_FILE.exists() else 0
        lines.append(f"MT5 reconnects: **{reconnects}**")

        lines += ["\n---", "## Suggestions\n"]
        for s in report.get("suggestions", []):
            lines.append(f"- {s}")

        lines += [
            "\n---",
            "> ⚠️ Rule-based analysis only. Past results do not predict future performance.",
            "> All trading involves risk of loss.",
        ]

        with open(path, "w") as f:
            f.write("\n".join(lines))


# Module-level singleton
weekly_report_builder = WeeklyReportBuilder()
