import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_data
from build_data import group_by_category


def test_generate_trend_uses_selected_provider_model(monkeypatch):
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = type("Message", (), {"content": json.dumps(["a", "b", "c"])})()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": Completions()})()}
    )()
    settings = {
        "ai": {"provider": "gemini"},
        "gemini": {
            "model": "gemini-3.5-flash",
            "retry_max": 1,
            "retry_interval": 0,
        },
    }
    monkeypatch.setattr(build_data, "SETTINGS", settings)

    assert build_data.generate_trend(client, [{"title": "T", "what": "W"}]) == (["a", "b", "c"], [])
    assert calls[0]["model"] == "gemini-3.5-flash"
    assert calls[0]["max_tokens"] == 400


def test_generate_trend_rejects_non_array_language_values(monkeypatch):
    class Completions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": json.dumps({"ja": "abc", "en": "xyz"})})()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    monkeypatch.setattr(build_data, "SETTINGS", {
        "ai": {"provider": "gemini"},
        "gemini": {"model": "x", "retry_max": 1, "retry_interval": 0},
    })
    ja, en = build_data.generate_trend(client, [{"title": "T", "what": "W"}])
    assert isinstance(ja, list)
    assert isinstance(en, list)
    assert ja == []
    assert en == []


def test_generate_trend_waits_for_provider_interval_before_retry(monkeypatch):
    attempts = 0

    class Completions:
        def create(self, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("rate limited")
            message = type(
                "Message",
                (),
                {"content": json.dumps({"ja": ["日1", "日2", "日3"], "en": ["E1", "E2", "E3"]})},
            )()
            return type(
                "Response", (), {"choices": [type("Choice", (), {"message": message})()]}
            )()

    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": Completions()})()}
    )()
    monkeypatch.setattr(build_data, "SETTINGS", {
        "ai": {"provider": "github_models"},
        "github_models": {
            "model": "openai/gpt-5",
            "retry_max": 2,
            "min_request_interval": 60.0,
        },
    })
    monotonic_values = iter([100.0, 100.0, 160.0])
    monkeypatch.setattr(build_data.time, "monotonic", lambda: next(monotonic_values))
    sleeps = []
    monkeypatch.setattr(build_data.time, "sleep", sleeps.append)

    result = build_data.generate_trend(client, [{"title": "T", "what": "W"}])

    assert result == (["日1", "日2", "日3"], ["E1", "E2", "E3"])
    assert sleeps == [60.0]


def test_main_omits_failed_english_trend_for_later_enrichment(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "analyzed_papers.json").write_text("[]")
    monkeypatch.setattr(build_data, "ROOT", tmp_path)
    monkeypatch.setattr(build_data, "SETTINGS", {
        "ai": {"provider": "gemini"},
        "gemini": {"api_key_env": "GEMINI_API_KEY"},
        "data": {
            "weekly_dir": "data/weekly",
            "index_file": "data/index.json",
            "latest_file": "data/latest.json",
        },
    })
    monkeypatch.setattr(build_data, "KEYWORDS", {"ui_categories": []})
    monkeypatch.setattr(build_data, "fetch_paper_meta", lambda papers: {})
    monkeypatch.setattr(build_data, "create_client", lambda settings: object())
    monkeypatch.setattr(build_data, "generate_trend", lambda client, papers: ([], []))

    build_data.main(date_str="2026-07-10")

    weekly = json.loads((data_dir / "latest.json").read_text())
    assert weekly["trend"]
    assert "trendEn" not in weekly

# group_by_category depends on KEYWORDS["ui_categories"], so these tests use
# the real definitions from keywords.yaml.

class TestGroupByCategory:
    def test_groups_papers_by_category(self):
        papers = [
            {"id": "1", "category": "foundation"},
            {"id": "2", "category": "separation"},
            {"id": "3", "category": "foundation"},
        ]
        result = group_by_category(papers)
        ids_by_cat = {c["id"]: len(c["papers"]) for c in result}
        assert ids_by_cat.get("foundation") == 2
        assert ids_by_cat.get("separation") == 1

    def test_unknown_category_goes_to_other(self):
        papers = [{"id": "1", "category": "unknown_cat"}]
        result = group_by_category(papers)
        other = next((c for c in result if c["id"] == "other"), None)
        assert other is not None
        assert len(other["papers"]) == 1

    def test_missing_category_goes_to_other(self):
        papers = [{"id": "1"}]  # No category key.
        result = group_by_category(papers)
        other = next((c for c in result if c["id"] == "other"), None)
        assert other is not None

    def test_empty_categories_excluded(self):
        papers = [{"id": "1", "category": "foundation"}]
        result = group_by_category(papers)
        cat_ids = [c["id"] for c in result]
        # Categories without papers are omitted.
        assert "separation" not in cat_ids or any(
            c["id"] == "separation" and len(c["papers"]) == 0 for c in result
        ) is False

    def test_result_has_required_fields(self):
        papers = [{"id": "1", "category": "anomaly"}]
        result = group_by_category(papers)
        cat = next(c for c in result if c["id"] == "anomaly")
        assert "id" in cat
        assert "label" in cat
        assert "color" in cat
        assert "papers" in cat
        assert "labelEn" in cat
