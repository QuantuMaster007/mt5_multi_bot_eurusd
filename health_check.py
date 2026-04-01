"""
health_check.py

Print the current heartbeat status of all running bots.
Run while the framework is active.

Usage::

    python health_check.py
"""
import json
from pathlib import Path
from datetime import datetime, timezone

from core.heartbeat import read_all_heartbeats


def main() -> None:
    hbs = read_all_heartbeats()
    if not hbs:
        print("No heartbeat files found. Is the framework running?")
        return

    now = datetime.now(timezone.utc).timestamp()
    print(f"\n{'Strategy':<25} {'Status':<12} {'Last Beat':<25} {'Age(s)':<8} {'Positions':<10} {'Loops'}")
    print("-" * 95)

    for name, hb in sorted(hbs.items()):
        ts_str = hb.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = int(now - ts.timestamp())
        except Exception:
            age = -1
            ts_str = "unknown"

        status     = hb.get("status", "?")
        positions  = hb.get("open_positions", 0)
        loop_count = hb.get("loop_count", 0)
        stale_flag = " ⚠ STALE" if age > 60 else ""

        print(
            f"{name:<25} {status:<12} {str(ts_str)[:24]:<25} "
            f"{age:<8} {positions:<10} {loop_count}{stale_flag}"
        )

    print()

    # Also show metrics snapshot if available
    metrics_path = Path("data/metrics/metrics_snapshot.json")
    if metrics_path.exists():
        print("Strategy Metrics Snapshot:")
        print("-" * 60)
        with open(metrics_path) as f:
            metrics = json.load(f)
        for name, m in metrics.items():
            trades = m.get("total_trades", 0)
            wr     = m.get("win_rate", 0)
            pnl    = m.get("gross_pnl", 0)
            ef     = m.get("exec_failure_rate", 0)
            print(
                f"  {name:<25} trades={trades:<5} "
                f"win%={wr*100:.1f}  pnl={pnl:+.2f}  "
                f"exec_fail%={ef*100:.1f}"
            )
        print()


if __name__ == "__main__":
    main()
