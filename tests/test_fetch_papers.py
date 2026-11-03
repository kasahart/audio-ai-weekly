import sys
import ssl
import urllib.error

import pytest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_papers

from fetch_papers import (
    build_query,
    fetch_arxiv,
    fetch_arxiv_ids,
    get_retry_max,
    keyword_match,
    assign_category,
    is_within_window,
    load_excluded_ids,
    normalize_match_text,
    submission_cutoff,
    window_bounds,
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

    def test_hyphenated_text_matches_spaced_keyword(self):
        paper = {"title": "Probing Large Audio-Language Models", "abstract": ""}
        assert keyword_match(paper, ["audio language model"], []) is True

    def test_double_hyphen_and_dash_variants_match(self):
        for title in ("Audio--Language Models", "Audio\u2013Language Models", "Audio \u2014 Language Models"):
            paper = {"title": title, "abstract": ""}
            assert keyword_match(paper, ["audio language model"], []) is True

    def test_hyphenated_keyword_matches_spaced_text(self):
        paper = {"title": "", "abstract": "We study audio pre training at scale"}
        assert keyword_match(paper, ["audio pre-training"], []) is True

    def test_punctuation_is_treated_as_space(self):
        paper = {"title": "", "abstract": "Self-supervised learning (SSL) speech models"}
        assert keyword_match(paper, ["ssl speech"], []) is True

    def test_exclude_uses_same_normalization(self):
        paper = {"title": "Source Separation (Retracted)", "abstract": ""}
        assert keyword_match(paper, self.INCLUDE, ["retracted"]) is False


class TestNormalizeMatchText:
    def test_lowercases_and_collapses_separators(self):
        assert normalize_match_text("  Full-Duplex\tSpoken--Dialogue  ") == "full duplex spoken dialogue"


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

    def test_hyphenated_text_is_categorized(self):
        paper = {"title": "Probing Audio-Language Models", "abstract": ""}
        assert assign_category(paper, self.CATEGORIES) == "foundation"


# ── is_within_window ───────────────────────────────────────────────────

class TestIsWithinWindow:
    START = datetime(2026, 4, 16, 18, 0, tzinfo=timezone.utc)
    END = datetime(2026, 4, 23, 18, 0, tzinfo=timezone.utc)

    def test_within_window(self):
        assert is_within_window("2026-04-20T00:00:00Z", self.START, self.END) is True

    def test_start_is_inclusive(self):
        assert is_within_window("2026-04-16T18:00:00Z", self.START, self.END) is True

    def test_before_window(self):
        assert is_within_window("2026-04-16T17:59:59Z", self.START, self.END) is False

    def test_end_is_exclusive(self):
        # The next week's window starts here, so a boundary paper is counted once.
        assert is_within_window("2026-04-23T18:00:00Z", self.START, self.END) is False

    def test_invalid_iso_returns_true(self):
        # パース失敗時は含める
        assert is_within_window("not-a-date", self.START, self.END) is True


class TestSubmissionWindow:
    def test_friday_run_ends_at_thursday_deadline_in_daylight_time(self):
        # 14:00 EDT on Thursday 2026-10-08 is 18:00 UTC.
        ref = datetime(2026, 10, 9, tzinfo=timezone.utc)
        assert submission_cutoff(ref) == datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)

    def test_friday_run_ends_at_thursday_deadline_in_standard_time(self):
        # 14:00 EST on Thursday 2026-11-05 is 19:00 UTC.
        ref = datetime(2026, 11, 6, tzinfo=timezone.utc)
        assert submission_cutoff(ref) == datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)

    def test_late_thursday_paper_falls_into_next_week(self):
        # 2610.12693 was submitted Thursday 2026-10-08 20:19 UTC, after the deadline.
        published = "2026-10-08T20:19:46Z"
        assert not is_within_window(published, *window_bounds(datetime(2026, 10, 9, tzinfo=timezone.utc), 7))
        assert is_within_window(published, *window_bounds(datetime(2026, 10, 16, tzinfo=timezone.utc), 7))

    def test_consecutive_windows_are_contiguous_across_dst(self):
        first = window_bounds(datetime(2026, 10, 30, tzinfo=timezone.utc), 7)
        second = window_bounds(datetime(2026, 11, 6, tzinfo=timezone.utc), 7)
        assert first[1] == second[0]
        assert second == (
            datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc),
        )

    def test_catch_up_window_starts_at_previous_cutoff(self):
        start, end = window_bounds(datetime(2026, 10, 23, tzinfo=timezone.utc), 14)
        assert start == submission_cutoff(datetime(2026, 10, 9, tzinfo=timezone.utc))
        assert end == datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


class TestExcludedIds:
    def test_load_strips_versions_and_blank_lines(self, tmp_path):
        path = tmp_path / "ids.txt"
        path.write_text("2610.12693v2\n\n2610.40883\n")
        assert load_excluded_ids(path) == {"2610.12693", "2610.40883"}

    def test_missing_path_means_no_exclusions(self):
        assert load_excluded_ids(None) == set()


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
        assert extract_orgs(entry) == ""

    def test_two_authors_no_affiliation(self):
        entry = make_entry([("Alice", ""), ("Bob", "")])
        assert extract_orgs(entry) == ""

    def test_three_authors_no_affiliation(self):
        entry = make_entry([("A", ""), ("B", ""), ("C", "")])
        assert extract_orgs(entry) == ""

    def test_four_or_more_authors_et_al(self):
        entry = make_entry([("A", ""), ("B", ""), ("C", ""), ("D", "")])
        assert extract_orgs(entry) == ""

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

    def test_missing_affiliation_has_no_org_provenance(self):
        paper = parse_atom(SAMPLE_ATOM)[0]
        assert paper["org"] == ""
        assert paper["orgSource"] is None

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
        ref = datetime(2026, 4, 24, tzinfo=timezone.utc)

        query = build_query(ref, 7)

        assert "cat:cs.SD" in query
        assert "cat:eess.AS" in query
        # Thursday 14:00 EDT deadlines on 2026-04-16 and 2026-04-23.
        assert "submittedDate:[202604161800 TO 202604231800]" in query


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
    def test_coerces_invalid_retry_max_to_default(self):
        assert get_retry_max({"retry_max": 0}) == 1
        assert get_retry_max({"retry_max": "bad"}) == 3

    def test_retries_retryable_http_error(self, monkeypatch):
        attempts = []
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_max", 2)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_interval", 0.0)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "request_timeout", 1)

        def fake_urlopen(req, timeout, *, context):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)
            return DummyResponse(SAMPLE_ATOM)

        monkeypatch.setattr(fetch_papers.urllib.request, "urlopen", fake_urlopen)

        result = fetch_arxiv("(cat:cs.SD)", 0, 1)

        assert len(attempts) == 2
        assert result[0]["id"] == "2601.12345v1"

    def test_fetches_related_ids_in_one_request(self, monkeypatch):
        urls = []
        monkeypatch.setattr(
            fetch_papers.urllib.request,
            "urlopen",
            lambda req, timeout, *, context: (urls.append(req.full_url) or DummyResponse(SAMPLE_ATOM)),
        )

        result = fetch_arxiv_ids(["2601.12345", "2401.00001"])

        assert len(urls) == 1
        assert "id_list=2601.12345%2C2401.00001" in urls[0]
        assert result[0]["title"] == "Test Paper Title"

    def test_retries_urlerror_wrapped_connection_error(self, monkeypatch):
        attempts = []
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_max", 2)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "retry_interval", 0.0)
        monkeypatch.setitem(fetch_papers.SETTINGS["arxiv"], "request_timeout", 1)

        def fake_urlopen(_req, timeout, *, context):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise urllib.error.URLError(ConnectionResetError("connection reset"))
            return DummyResponse(SAMPLE_ATOM)

        monkeypatch.setattr(fetch_papers.urllib.request, "urlopen", fake_urlopen)

        result = fetch_arxiv("(cat:cs.SD)", 0, 1)

        assert len(attempts) == 2
        assert result[0]["id"] == "2601.12345v1"


@pytest.mark.parametrize("fetch_by_id", [False, True])
@pytest.mark.parametrize(
    "override,contact,expected",
    [
        (None, None, "audio-ai-weekly/1.0"),
        ("  ", "  ", "audio-ai-weekly/1.0"),
        ("test-client/2.0", None, "test-client/2.0"),
        (None, "https://example.org/contact", "audio-ai-weekly/1.0 (https://example.org/contact)"),
        ("test-client/2.0", "operator@example.org", "test-client/2.0 (operator@example.org)"),
    ],
)
def test_request_headers(monkeypatch, capsys, fetch_by_id, override, contact, expected):
    for name, value in [("ARXIV_USER_AGENT", override), ("ARXIV_CONTACT", contact)]:
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    requests = []

    def fake_urlopen(req, timeout, *, context):
        requests.append(req)
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.post_handshake_auth is False
        assert req.get_header("User-agent") == expected
        assert req.get_header("Accept") == (
            "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"
        )
        return DummyResponse(SAMPLE_ATOM)

    monkeypatch.setattr(fetch_papers.urllib.request, "urlopen", fake_urlopen)
    if fetch_by_id:
        result = fetch_arxiv_ids(["2601.12345"])
    else:
        result = fetch_arxiv("cat:cs.SD", 0, 1)
    assert len(requests) == 1
    assert result[0]["id"] == "2601.12345v1"
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("fetch_by_id", [False, True])
def test_does_not_retry_http_406(monkeypatch, fetch_by_id):
    requests = []

    def fake_urlopen(req, timeout, *, context):
        requests.append(req)
        raise urllib.error.HTTPError(req.full_url, 406, "Not Acceptable", None, None)

    monkeypatch.setattr(fetch_papers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError) as error:
        if fetch_by_id:
            fetch_arxiv_ids(["2601.12345"])
        else:
            fetch_arxiv("cat:cs.SD", 0, 1)
    assert error.value.code == 406
    assert len(requests) == 1
