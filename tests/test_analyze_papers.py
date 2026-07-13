import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import analyze_papers
import pytest
from analyze_papers import (
    SYSTEM_PROMPT,
    build_next_reads,
    chunk_papers,
    fallback_result,
    sanitize_json_text,
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

    with pytest.raises(RuntimeError, match="refusing to publish fallback data"):
        analyze_papers.analyze_batch(client, [paper], None)

    output = capsys.readouterr().out
    assert "finish_reason=length" in output
    assert "reasoning_tokens=1000" in output


def test_system_prompt_preserves_exact_japanese_terminology():
    assert "音源分離" in SYSTEM_PROMPT
    assert "音響信号処理" in SYSTEM_PROMPT
    assert "音響イベント" in SYSTEM_PROMPT


def test_fallback_keeps_failed_english_overview_retryable():
    paper = {"title": "Paper", "org": "Example University", "abstract": "Original abstract"}

    assert fallback_result(paper)["what"] == "Analysis failed."
    assert fallback_result(paper)["whatEn"] == ""
    assert "taskEn" in fallback_result(paper)


def test_prompt_requests_bilingual_analysis_fields():
    for field in ("taskEn", "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn"):
        assert field in SYSTEM_PROMPT


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


class TestBuildNextReads:
    def test_with_arxiv_id(self):
        items = [{"label": "Paper A (2024)", "id": "2401.12345"}]
        result = build_next_reads(items)
        assert result[0]["label"] == "Paper A (2024)"
        assert result[0]["url"] == "https://arxiv.org/abs/2401.12345"

    def test_with_null_id(self):
        items = [{"label": "Paper B (2023)", "id": None}]
        result = build_next_reads(items)
        assert result[0]["url"] is None

    def test_multiple_items(self):
        items = [
            {"label": "A", "id": "2401.00001"},
            {"label": "B", "id": None},
        ]
        result = build_next_reads(items)
        assert len(result) == 2
        assert result[0]["url"] == "https://arxiv.org/abs/2401.00001"
        assert result[1]["url"] is None

    def test_empty_list(self):
        assert build_next_reads([]) == []

    def test_missing_label_defaults_empty(self):
        items = [{"id": "2401.00001"}]
        result = build_next_reads(items)
        assert result[0]["label"] == ""
