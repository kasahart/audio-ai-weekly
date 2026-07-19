import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import analyze_papers
import pytest
from analyze_papers import (
    BATCH_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    chunk_papers,
    get_analysis_providers,
    sanitize_json_text,
    normalize_arxiv_id,
    verify_related_papers,
    trusted_affiliation,
)


def test_analyze_batch_uses_selected_provider_model(monkeypatch):
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = json.dumps({"1234.5678": {}})
            message = type("Message", (), {"content": content})()
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
            "min_request_interval": 0,
            "batch_max_tokens": 1000,
        },
    }
    monkeypatch.setattr(analyze_papers, "SETTINGS", settings)
    paper = {"id": "1234.5678", "title": "Title", "abstract": "Abstract"}

    analyze_papers.analyze_batch(client, [paper], None)

    assert calls[0]["model"] == "gemini-3.5-flash"
    assert calls[0]["max_tokens"] == 1000
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_analyze_batch_fails_closed_after_empty_response(monkeypatch, capsys):
    class Completions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": ""})()
            choice = type(
                "Choice", (), {"message": message, "finish_reason": "length"}
            )()
            details = type("Details", (), {"reasoning_tokens": 1000})()
            usage = type(
                "Usage",
                (),
                {"completion_tokens": 1000, "completion_tokens_details": details},
            )()
            return type("Response", (), {"choices": [choice], "usage": usage})()

    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": Completions()})()}
    )()
    monkeypatch.setattr(analyze_papers, "SETTINGS", {
        "ai": {"provider": "github_models"},
        "github_models": {
            "model": "openai/gpt-5",
            "retry_max": 1,
            "retry_interval": 0,
            "min_request_interval": 0,
            "batch_max_tokens": 1000,
        },
    })
    paper = {"id": "1234.5678", "title": "Title", "abstract": "Abstract"}

    with pytest.raises(RuntimeError, match="github_models failed after 1 attempts"):
        analyze_papers.analyze_batch(client, [paper], None)

    output = capsys.readouterr().out
    assert "finish_reason=length" in output
    assert "reasoning_tokens=1000" in output


def test_get_analysis_providers_deduplicates_primary(monkeypatch):
    monkeypatch.setattr(analyze_papers, "SETTINGS", {
        "ai": {"provider": "gemini"},
        "analysis": {"fallback_providers": ["gemini", "github_models"]},
        "gemini": {},
        "github_models": {},
    })

    assert get_analysis_providers() == ["gemini", "github_models"]


def test_analyze_batch_uses_explicit_fallback_provider(monkeypatch):
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = type(
                "Message", (), {"content": json.dumps({"1234.5678": {}})}
            )()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": Completions()})()}
    )()
    monkeypatch.setattr(analyze_papers, "SETTINGS", {
        "ai": {"provider": "gemini"},
        "gemini": {},
        "github_models": {
            "model": "openai/gpt-4.1",
            "retry_max": 1,
            "retry_interval": 0,
            "min_request_interval": 0,
            "batch_max_tokens": 1000,
        },
    })

    analyze_papers.analyze_batch(
        client,
        [{"id": "1234.5678", "title": "Title", "abstract": "Abstract"}],
        None,
        "github_models",
    )

    assert calls[0]["model"] == "openai/gpt-4.1"


def test_system_prompt_preserves_exact_japanese_terminology():
    assert "音源分離" in SYSTEM_PROMPT
    assert "音響信号処理" in SYSTEM_PROMPT
    assert "音響イベント" in SYSTEM_PROMPT


def test_prompt_requests_bilingual_analysis_fields():
    for field in ("taskEn", "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn"):
        assert field in BATCH_PROMPT_TEMPLATE


def test_prompt_forbids_affiliation_generation_and_overclaiming():
    assert '"org"' not in BATCH_PROMPT_TEMPLATE
    assert "Never generate or infer author affiliations" in SYSTEM_PROMPT
    assert "authors claim" in SYSTEM_PROMPT
    assert "paper body, tables, figures" in SYSTEM_PROMPT


def test_only_arxiv_sourced_affiliation_is_trusted():
    assert trusted_affiliation({"org": "Model guess"}) == ("", None)
    assert trusted_affiliation(
        {"org": "Official Lab", "orgSource": "arxiv_affiliation"}
    ) == ("Official Lab", "arxiv_affiliation")


class TestChunkPapers:
    PAPERS = [{"id": str(i)} for i in range(7)]

    def test_splits_evenly(self):
        chunks = chunk_papers([{"id": str(i)} for i in range(6)], 3)
        assert len(chunks) == 2
        assert len(chunks[0]) == 3
        assert len(chunks[1]) == 3

    def test_last_chunk_smaller(self):
        chunks = chunk_papers(self.PAPERS, 3)
        assert len(chunks) == 3
        assert len(chunks[2]) == 1

    def test_batch_size_larger_than_list(self):
        chunks = chunk_papers(self.PAPERS, 20)
        assert len(chunks) == 1
        assert len(chunks[0]) == 7

    def test_empty_list(self):
        assert chunk_papers([], 5) == []

    def test_batch_size_one(self):
        chunks = chunk_papers(self.PAPERS, 1)
        assert len(chunks) == 7


class TestSanitizeJsonText:
    def test_strips_code_fence_json(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = sanitize_json_text(raw)
        assert result == '{"key": "value"}'

    def test_strips_code_fence_plain(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = sanitize_json_text(raw)
        assert result == '{"key": "value"}'

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert sanitize_json_text(raw) == raw

    def test_strips_whitespace(self):
        raw = "  {\"key\": \"value\"}  "
        assert sanitize_json_text(raw) == '{"key": "value"}'

    def test_empty_string(self):
        assert sanitize_json_text("") == ""


class TestVerifyRelatedPapers:
    OFFICIAL = [{
        "id": "2401.12345v2",
        "title": "A Real Related Paper",
        "published_iso": "2024-01-20T00:00:00Z",
    }]

    def test_matching_id_and_title_uses_official_metadata(self):
        result = verify_related_papers(
            {"source": [{"label": "A Real Related Paper (2024)", "id": "2401.12345"}]},
            fetcher=lambda ids: self.OFFICIAL,
        )["source"]
        assert result == [{
            "label": "A Real Related Paper (2024)",
            "arxivId": "2401.12345",
            "url": "https://arxiv.org/abs/2401.12345",
            "verified": True,
            "source": "arxiv_api",
        }]

    def test_version_suffix_is_normalized(self):
        assert normalize_arxiv_id("2401.12345v3") == "2401.12345"

    @pytest.mark.parametrize("bad_id", ["bad", "2401.1", None])
    def test_invalid_or_null_id_is_not_fetched(self, bad_id):
        calls = []
        result = verify_related_papers(
            {"source": [{"label": "A Real Related Paper", "id": bad_id}]},
            fetcher=lambda ids: calls.append(ids) or self.OFFICIAL,
        )
        assert result["source"] == []
        assert calls == []

    def test_nonexistent_id_is_not_published(self):
        result = verify_related_papers(
            {"source": [{"label": "Missing", "id": "2401.00001"}]},
            fetcher=lambda ids: [],
        )
        assert result["source"] == []

    def test_title_mismatch_is_not_published(self):
        result = verify_related_papers(
            {"source": [{"label": "Different Paper (2024)", "id": "2401.12345"}]},
            fetcher=lambda ids: self.OFFICIAL,
        )
        assert result["source"] == []

    def test_api_failure_fails_closed_with_warning(self, capsys):
        def fail(_ids):
            raise TimeoutError("temporary")

        result = verify_related_papers(
            {"source": [{"label": "A Real Related Paper", "id": "2401.12345"}]},
            fetcher=fail,
        )
        assert result["source"] == []
        assert "publishing no related-paper links" in capsys.readouterr().out
