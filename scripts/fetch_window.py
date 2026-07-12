#!/usr/bin/env python3
"""Determine the arXiv retrieval window and limit from the last published date."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

import yaml


class CatchUpWindowExceeded(ValueError):
    """The required catch-up window exceeds the safe automatic limit."""


def parse_week_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m%d").date()


def resolve_fetch_window(
    ref_date: date,
    index: dict,
    default_lookback_days: int,
    default_max_papers: int,
    catchup_max_days: int,
    catchup_max_papers: int,
) -> tuple[int, int]:
    """Extend the retrieval window to the last published week after a missed run.

    Long gaps are delegated to the weekly backfill instead of being processed
    in a single run.
    """
    published_dates = []
    for week in index.get("weeks", []):
        try:
            published_date = parse_week_date(week["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if published_date < ref_date:
            published_dates.append(published_date)

    if not published_dates:
        return default_lookback_days, default_max_papers

    days_since_last_publish = (ref_date - max(published_dates)).days
    lookback_days = max(default_lookback_days, days_since_last_publish)
    if lookback_days > catchup_max_days:
        raise CatchUpWindowExceeded(
            f"Catch-up requires {lookback_days} days, exceeding the "
            f"{catchup_max_days}-day safety limit. Run the weekly backfill workflow."
        )

    max_papers = (
        catchup_max_papers
        if lookback_days > default_lookback_days
        else default_max_papers
    )
    return lookback_days, max_papers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--date", required=True, help="Reference date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        index = json.loads(args.index.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        index = {"weeks": []}

    settings = yaml.safe_load(args.settings.read_text())["arxiv"]
    ref_date = date.fromisoformat(args.date)
    lookback_days, max_papers = resolve_fetch_window(
        ref_date,
        index,
        settings["lookback_days"],
        settings["max_papers"],
        settings["catchup_max_days"],
        settings["catchup_max_papers"],
    )
    print(f"lookback_days={lookback_days}")
    print(f"max_papers={max_papers}")


if __name__ == "__main__":
    main()
