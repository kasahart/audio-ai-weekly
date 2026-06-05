import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_papers

from fetch_papers import (
    build_query,
    fetch_arxiv,
    keyword_match,
    assign_category,
    is_within_window,
    extract_orgs,
    parse_atom,
)

# ── keyword_match ──────────────────────────────────────────────────────

class TestKeywordMatch:
    INCLUDE = ["source separation", "anomaly detection"]
    EXCLUDE = ["retracted"]

    def test_matches_title(self):
        paper = {"title": "Audio Source Separation using Transformers", "abstract": ""}
        assert keyword_match(paper, self.INCLUDE, self.EXCLUDE) is True

    def test_matches_abstract(self):
        paper = {"title": "A new method", "abstract": "We propose anomaly detection for machines"}
        assert keyword_match(paper, self.INCLUDE, self.EXCLUDE) is True

    def test_no_match(self):
        paper = {"title": "Image Classification", "abstract": "We classify images using CNNs"}
        assert keyword_match(paper, self.INCLUDE, self.EXCLUDE) is False

    def test_exclude_overrides_include(self):
        paper = {"title": "Retracted: Source Separation", "abstract": ""}
        assert keyword_match(paper, self.INCLUDE, self.EXCLUDE) is False

    def test_case_insensitive(self):
        paper = {"title": "ANOMALY DETECTION", "abstract": ""}
        assert keyword_match(paper, self.INCLUDE, self.EXCLUDE) is True

    def test_empty_include_no_match(self):
        paper = {"title": "Source Separation", "abstract": ""}
        assert keyword_match(paper, [], self.EXCLUDE) is False


# ── assign_category ────────────────────────────────────────────────────

class TestAssignCategory:
    CATEGORIES = [
        {"id": "foundation", "keywords": ["audio foundation model", "audio language model"]},
        {"id": "separation", "keywords": ["source separation", "music separation"]},
        {"id": "anomaly",    "keywords": ["anomaly detection", "anomalous sound"]},
    ]

    def test_assigns_separation(self):
        paper = {"title": "Audio Source Separation", "abstract": "We separate sources"}
        assert assign_category(paper, self.CATEGORIES) == "separation"

    def test_assigns_anomaly(self):
        paper = {"title": "Machine Anomaly Detection", "abstract": "detecting anomalous sound"}
        assert assign_category(paper, self.CATEGORIES) == "anomaly"

    def test_assigns_foundation(self):
        paper = {"title": "Audio Language Model", "abstract": "large audio foundation model"}
        assert assign_category(paper, self.CATEGORIES) == "foundation"

    def test_falls_back_to_other(self):
        paper = {"title": "Image Processing", "abstract": "visual features"}
        assert assign_category(paper, self.CATEGORIES) == "other"

    def test_first_match_wins(self):
        paper = {"title": "Audio Language Model for Source Separation", "abstract": ""}
        assert assign_category(paper, self.CATEGORIES) == "foundation"


# ── is_within_window ───────────────────────────────────────────────────

class TestIsWithinWindow:
    REF = datetime(2026, 4, 26, tzinfo=timezone.utc)

    def test_within_window(self):
        assert is_within_window("2026-04-20T00:00:00Z", 7, self.REF) is True

    def test_on_cutoff_boundary(self):
        assert is_within_window("2026-04-19T00:00:00Z", 7, self.REF) is True

    def test_before_window(self):
        assert is_within_window("2026-04-18T00:00:00Z", 7, self.REF) is False

    def test_on_ref_date(self):
        assert is_within_window("2026-04-26T00:00:00Z", 7, self.REF) is True

    def test_after_ref_date(self):
        assert is_within_window("2026-04-27T00:00:00Z", 7, self.REF) is False

    def test_invalid_iso_returns_true(self):
        # パース失敗時は含める
        assert is_within_window("not-a-date", 7, self.REF) is True


# ── extract_orgs ───────────────────────────────────────────────────────

import xml.etree.ElementTree as ET

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

def make_entry(authors: list[tuple[str, str]]) -> ET.Element:
    """(name, affiliation) のリストからAtom entryを生成"""
    entry = ET.Element("{http://www.w3.org/2005/Atom}entry")
    for name, affil in authors:
        author = ET.SubElement(entry, "{http://www.w3.org/2005/Atom}author")
        name_el = ET.SubElement(author, "{http://www.w3.org/2005/Atom}name")
        name_el.text = name
        if affil:
            affil_el = ET.SubElement(author, "{http://arxiv.org/schemas/atom}affiliation")
            affil_el.text = affil
    return entry

class TestExtractOrgs:
    def test_returns_first_affiliation(self):
        entry = make_entry([("Alice", "MIT"), ("Bob", "Google")])
        assert extract_orgs(entry) == "MIT"

    def test_single_author_no_affiliation(self):
        entry = make_entry([("Alice", "")])
        assert extract_orgs(entry) == "Alice"

    def test_two_authors_no_affiliation(self):
        entry = make_entry([("Alice", ""), ("Bob", "")])
        assert extract_orgs(entry) == "Alice / Bob"

    def test_three_authors_no_affiliation(self):
        entry = make_entry([("A", ""), ("B", ""), ("C", "")])
        assert extract_orgs(entry) == "A / B / C"

    def test_four_or_more_authors_et_al(self):
        entry = make_entry([("A", ""), ("B", ""), ("C", ""), ("D", "")])
        assert extract_orgs(entry) == "A et al."

    def test_affiliation_truncated_at_60_chars(self):
        long_affil = "X" * 80
        entry = make_entry([("Alice", long_affil)])
        result = extract_orgs(entry)
        assert len(result) <= 60


# ── parse_atom ─────────────────────────────────────────────────────────

SAMPLE_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2601.12345v1</id>
    <published>2026-01-15T00:00:00Z</published>
    <title>Test Paper Title</title>
    <summary>This is the abstract text.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <category term="cs.SD" scheme="http://arxiv.org/schemas/atom"/>
    <category term="eess.AS" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

class TestParseAtom:
    def test_returns_list(self):
        result = parse_atom(SAMPLE_ATOM)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_paper_fields(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert paper["id"] == "2601.12345v1"
        assert paper["title"] == "Test Paper Title"
        assert "abstract" in paper
        assert paper["abstract"] == "This is the abstract text."

    def test_authors_extracted(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert "Alice Smith" in paper["authors"]
        assert "Bob Jones" in paper["authors"]

    def test_categories_extracted(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert "cs.SD" in paper["categories"]
        assert "eess.AS" in paper["categories"]

    def test_url_format(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert paper["url"] == "https://arxiv.org/abs/2601.12345v1"

    def test_date_formatted(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert paper["date"] == "Jan 15"

    def test_empty_feed(self):
        empty = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
        result = parse_atom(empty)
        assert result == []


class TestBuildQuery:
    def test_includes_submitted_date_range(self):
        ref = datetime(2026, 4, 26, 12, 34, tzinfo=timezone.utc)

        query = build_query(ref, 7)

        assert "cat:cs.SD" in query
        assert "cat:eess.AS" in query
        assert "submittedDate:[202604191234+TO+202604261234]" in query


class DummyResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class TestFetchArxiv:
    def test_retries_retryable_http_error(self, monkeypatch):
        attempts = []
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_max", 2)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_interval", 0.0)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "request_timeout", 1)

        def fake_urlopen(req, timeout):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)
            return DummyResponse(SAMPLE_ATOM)

        monkeypatch.setattr(fetch_papers.urllib.request, "urlopen", fake_urlopen)

        result = fetch_arxiv("(cat:cs.SD)", 0, 1)

        assert len(attempts) == 2
        assert result[0]["id"] == "2601.12345v1"
