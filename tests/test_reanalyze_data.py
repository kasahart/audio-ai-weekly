import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import reanalyze_data


def test_reanalyze_file_writes_bilingual_trends(tmp_path, monkeypatch):
    path = tmp_path / "week.json"
    path.write_text(json.dumps({
        "trend": ["old"],
        "categories": [{"papers": [{"id": "1234.5678", "title": "Title", "what": "概要"}]}],
    }))
    monkeypatch.setattr(reanalyze_data, "generate_trend", lambda client, papers: (["日1", "日2", "日3"], ["E1", "E2", "E3"]))

    assert reanalyze_data.reanalyze_file(path, object(), {}) is True
    data = json.loads(path.read_text())
    assert data["trend"] == ["日1", "日2", "日3"]
    assert data["trendEn"] == ["E1", "E2", "E3"]
