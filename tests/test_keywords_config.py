import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from fetch_papers import KEYWORDS, assign_category, keyword_match


def paper(title: str, abstract: str = "") -> dict:
    return {"title": title, "abstract": abstract}


def test_include_matches_ui_category_keywords():
    ui_keywords = [kw for cat in KEYWORDS["ui_categories"] for kw in cat["keywords"]]
    assert sorted(KEYWORDS["include"]) == sorted(ui_keywords)


def test_variant_spellings_are_included():
    cases = {
        "Probing Large Audio-Language Models for Compositional Understanding": "foundation",
        "Why Full-Duplex Speech Models Collapse in Long Conversations": "foundation",
        "Developing a Speech Language Model for Basque": "foundation",
        "NECTAR: Negative Clue Estimation for Target Speaker Extraction": "separation",
        "Soloist: A Generative Model for Blind Voice Separation in the Wild": "separation",
        "Unsupervised Maneuver-Aware Acoustic Fault Detection for Autonomous Drones": "anomaly",
        "PG-SELD: Physics-Guided Sound Event Localization and Detection": "anomaly",
    }
    for title, category in cases.items():
        p = paper(title)
        assert keyword_match(p, KEYWORDS["include"], KEYWORDS["exclude"]), title
        assert assign_category(p, KEYWORDS["ui_categories"]) == category, title


def test_unrelated_paper_is_not_included():
    p = paper("Timestamped Hindi speech transcription using Whisper")
    assert not keyword_match(p, KEYWORDS["include"], KEYWORDS["exclude"])
