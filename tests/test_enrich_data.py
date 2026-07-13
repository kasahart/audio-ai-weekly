import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import enrich_data
from enrich_data import AI_FIELDS, BATCH_PROMPT_TMPL


def test_enrichment_prompt_preserves_japanese_task_examples():
    assert "音源分離" in BATCH_PROMPT_TMPL
    assert "異音検知" in BATCH_PROMPT_TMPL
    assert "音楽生成" in BATCH_PROMPT_TMPL


def test_enrichment_requests_every_english_analysis_field():
    for field in ("taskEn", "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn"):
        assert field in AI_FIELDS
        assert field in BATCH_PROMPT_TMPL


def test_failed_ai_batch_does_not_mark_fields_complete(monkeypatch):
    monkeypatch.setattr(enrich_data, "SETTINGS", {
        "ai": {"provider": "gemini"},
        "gemini": {"model": "x", "retry_max": 0, "retry_interval": 0},
    })
    paper = {"id": "1234.5678", "title": "Title", "abstract": "Abstract"}
    assert enrich_data.fetch_ai_fields_batch(object(), [paper]) == {"1234.5678": {}}
