#!/usr/bin/env python3
"""
backfill.py
Generate weekly data for every Friday from from_date through to_date.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_papers
import build_data as build_data_module
import fetch_papers

BACKFILL_SETTINGS = fetch_papers.SETTINGS["backfill"]


def fridays_between(from_date: datetime, to_date: datetime) -> list[datetime]:
    """Return every Friday from from_date through to_date."""
    dates = []
    # Find the first Friday on or after from_date.
    days_ahead = (BACKFILL_SETTINGS["weekday"] - from_date.weekday()) % BACKFILL_SETTINGS["interval_days"]
    first_friday = from_date + timedelta(days=days_ahead)
    d = first_friday
    while d <= to_date:
        dates.append(d)
        d += timedelta(days=BACKFILL_SETTINGS["interval_days"])
    return dates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", default="", help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    from_date = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
    to_date = (
        datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
        if args.to_date
        else datetime.now(timezone.utc)
    )

    dates = fridays_between(from_date, to_date)
    print(f"[backfill] Processing {len(dates)} weekly dates (Fridays):")
    for d in dates:
        print(f"  {d.strftime('%Y-%m-%d (%a)')}")

    for i, date in enumerate(dates, 1):
        date_str = date.strftime("%Y-%m-%d")
        date_key = date.strftime("%Y-%m%d")
        weekly_path = ROOT / "data" / "weekly" / f"{date_key}.json"

        print(f"\n[backfill] === ({i}/{len(dates)}) {date_str} ===")

        if weekly_path.exists():
            print(f"[backfill] {weekly_path.name} already exists; skipping.")
            continue

        # Retrieve papers.
        fetch_papers.main(date_str=date_str)

        raw_path = ROOT / "data" / "raw_papers.json"
        if not raw_path.exists():
            print("[backfill] No paper file was produced; skipping.")
            continue

        papers = json.loads(raw_path.read_text())
        if not papers:
            print("[backfill] No matching papers; skipping.")
            raw_path.unlink(missing_ok=True)
            continue

        # Analyze papers.
        analyze_papers.main()

        # Build weekly data.
        build_data_module.main(date_str=date_str)

        # Wait between weeks to respect rate limits.
        if i < len(dates):
            interval = BACKFILL_SETTINGS["week_interval"]
            print(f"[backfill] Waiting {interval:g} seconds before the next week...")
            time.sleep(interval)

    print("\n[backfill] Complete.")


if __name__ == "__main__":
    main()
