"""
weekly_review.py

Run this at the end of each week to generate the performance report.

Usage::

    python weekly_review.py            # last 7 days
    python weekly_review.py --days 14  # last 14 days
"""
import argparse
import sys
from pathlib import Path

from core.logger import init_logging, get_logger
from core.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly performance report")
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of past days to include (default: 7)"
    )
    args = parser.parse_args()

    log_cfg = settings.logging
    init_logging(
        level=log_cfg.get("level", "INFO"),
        log_file=log_cfg.get("file_path", "data/logs/system.log"),
        console=True,
    )
    log = get_logger("weekly_review")
    log.info("Generating %d-day report...", args.days)

    from reports.weekly_report_builder import weekly_report_builder
    report = weekly_report_builder.build(days=args.days)

    # Print text summary to console
    md_path = Path("data/reports/weekly_summary.md")
    if md_path.exists():
        print("\n" + "=" * 60)
        print(md_path.read_text())
        print("=" * 60)

    print(f"\nReports written to: data/reports/")
    print("  weekly_summary.md")
    print("  weekly_summary.csv")
    print("  weekly_summary.json")


if __name__ == "__main__":
    main()
