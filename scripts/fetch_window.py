#!/usr/bin/env python3
"""前回公開日から arXiv 取得期間と件数上限を決定する。"""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

import yaml


class CatchUpWindowExceeded(ValueError):
    """自動遡及で安全に処理できる期間を超えた。"""


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
    """欠損週があれば、最後に公開した週まで取得期間を広げる。

    長期間の欠損は一度の実行で処理せず、週単位バックフィルに委ねる。
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
    parser.add_argument("--date", required=True, help="基準日 YYYY-MM-DD")
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
