import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from published_ids import published_ids_from_dir, week_date


def write_week(directory: Path, name: str, ids: list[str]) -> None:
    data = {"categories": [{"id": "foundation", "papers": [{"id": i} for i in ids]}]}
    (directory / name).write_text(json.dumps(data))


def test_week_date_parses_weekly_file_names():
    assert week_date("2026-1030.json") == date(2026, 10, 30)
    assert week_date("index.json") is None


def test_only_weeks_before_reference_date_are_listed(tmp_path):
    write_week(tmp_path, "2026-1023.json", ["2610.11111v1"])
    write_week(tmp_path, "2026-1030.json", ["2610.40883v2"])
    write_week(tmp_path, "2026-1106.json", ["2611.00001v1"])

    assert published_ids_from_dir(tmp_path, date(2026, 11, 6)) == {"2610.11111", "2610.40883"}


def test_regenerating_a_week_does_not_exclude_its_own_papers(tmp_path):
    write_week(tmp_path, "2026-1009.json", ["2610.12693v1"])

    assert published_ids_from_dir(tmp_path, date(2026, 10, 9)) == set()
