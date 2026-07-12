import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest

from fetch_window import CatchUpWindowExceeded, resolve_fetch_window


def resolve(ref_date, index):
    return resolve_fetch_window(ref_date, index, 7, 50, 28, 200)


def test_normal_week_uses_defaults():
    index = {"weeks": [{"date": "2026-0710"}]}

    assert resolve(date(2026, 7, 17), index) == (7, 50)


def test_missing_week_catches_up_with_safety_limit():
    index = {"weeks": [{"date": "2026-0529"}]}

    assert resolve(date(2026, 6, 12), index) == (14, 200)


def test_catchup_at_maximum_days_is_allowed():
    index = {"weeks": [{"date": "2026-0619"}]}

    assert resolve(date(2026, 7, 17), index) == (28, 200)


def test_catchup_beyond_maximum_days_stops():
    index = {"weeks": [{"date": "2026-0612"}]}

    with pytest.raises(CatchUpWindowExceeded, match="35 days"):
        resolve(date(2026, 7, 17), index)


def test_future_and_invalid_entries_are_ignored():
    index = {
        "weeks": [
            {"date": "2026-0718"},
            {"date": "invalid"},
            {},
            {"date": "2026-0710"},
        ]
    }

    assert resolve(date(2026, 7, 17), index) == (7, 50)


def test_empty_history_uses_defaults():
    assert resolve(date(2026, 7, 17), {}) == (7, 50)
