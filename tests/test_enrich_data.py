import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from enrich_data import BATCH_PROMPT_TMPL


def test_enrichment_prompt_preserves_japanese_task_examples():
    assert "音源分離" in BATCH_PROMPT_TMPL
    assert "異音検知" in BATCH_PROMPT_TMPL
    assert "音楽生成" in BATCH_PROMPT_TMPL
