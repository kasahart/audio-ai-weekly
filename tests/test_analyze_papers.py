import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from analyze_papers import (
    SYSTEM_PROMPT,
    build_next_reads,
    chunk_papers,
    fallback_result,
    sanitize_json_text,
)


def test_system_prompt_preserves_exact_japanese_terminology():
    assert "音源分離" in SYSTEM_PROMPT
    assert "音響信号処理" in SYSTEM_PROMPT
    assert "音響イベント" in SYSTEM_PROMPT


def test_fallback_copy_is_english():
    paper = {"title": "Paper", "org": "Example University"}

    assert fallback_result(paper)["what"] == "Analysis failed."


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
