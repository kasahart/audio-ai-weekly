#!/usr/bin/env python3
"""List arXiv IDs already published in weekly files dated before a reference date.

fetch_papers.py excludes these IDs so that overlapping retrieval windows (for
example at the switch to deadline-based windows, or after a catch-up) do not
publish the same paper twice. Weeks on or after the reference date are ignored,
so regenerating an existing week still collects its own papers.
"""

import argparse
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

WEEKLY_NAME_RE = re.compile(r"^(\d{4})-(\d{2})(\d{2})\.json$")


def week_date(name: str) -> date | None:
    match = WEEKLY_NAME_RE.match(Path(name).name)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def ids_from_weekly(data: dict) -> set[str]:
    return {
        re.sub(r"v\d+$", "", paper["id"])
        for category in data.get("categories", [])
        for paper in category.get("papers", [])
        if paper.get("id")
    }


def published_ids_from_dir(weekly_dir: Path, before: date) -> set[str]:
    ids: set[str] = set()
    for path in sorted(weekly_dir.glob("*.json")):
        week = week_date(path.name)
        if week is not None and week < before:
            ids |= ids_from_weekly(json.loads(path.read_text()))
    return ids


def published_ids_from_git(git_ref: str, before: date, prefix: str = "data/weekly/") -> set[str]:
    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", git_ref, "--", prefix],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    ids: set[str] = set()
    for name in names:
        week = week_date(name)
        if week is None or week >= before:
            continue
        content = subprocess.run(
            ["git", "show", f"{git_ref}:{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
        ids |= ids_from_weekly(json.loads(content))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--weekly-dir", type=Path, help="Directory of weekly JSON files")
    source.add_argument("--git-ref", help="Git ref holding data/weekly/*.json (e.g. origin/gh-pages)")
    parser.add_argument("--before", required=True, help="Reference date YYYY-MM-DD")
    args = parser.parse_args()

    before = datetime.strptime(args.before, "%Y-%m-%d").date()
    if args.weekly_dir:
        ids = published_ids_from_dir(args.weekly_dir, before)
    else:
        ids = published_ids_from_git(args.git_ref, before)
    for arxiv_id in sorted(ids):
        print(arxiv_id)


if __name__ == "__main__":
    main()
