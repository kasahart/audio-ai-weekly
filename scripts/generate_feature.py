#!/usr/bin/env python3
"""Generate a grounded, twice-monthly long-form research feature."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from openai import APIConnectionError, APIStatusError, APITimeoutError

from model_utils import build_chat_kwargs, create_client, get_ai_config


ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())
FEATURE_SETTINGS = SETTINGS["features"]
PROMPT_DIR = ROOT / "config" / "prompts"
PROMPTS = {
    name: (PROMPT_DIR / f"feature_{name}.txt").read_text().strip()
    for name in (
        "select",
        "generate",
        "generate_en",
        "verify",
        "verify_en",
        "revise",
        "expand",
        "grounding_patch",
        "grounding_patch_en",
        "translate_ja_metadata",
        "translate_ja_blocks",
        "verify_translation",
    )
}

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
TOPIC_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEARCH_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+/_-]{1,79}$")
SOURCE_ID_RE = re.compile(r"^S[1-9][0-9]*$")
ARXIV_VERSION_RE = re.compile(r"v[0-9]+$")
JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


class FeatureError(RuntimeError):
    """Base error for a feature that must not be published."""


class FeatureValidationError(FeatureError):
    """A generated plan or article failed local validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class ModelResponseTruncated(ValueError):
    """The model exhausted its output budget before completing a JSON response."""


def feature_slot(value: date) -> str:
    """Return the feature type for a second/fourth Tuesday, otherwise ``none``."""
    if value.weekday() != 1:  # Monday=0, Tuesday=1.
        return "none"
    occurrence = (value.day - 1) // 7 + 1
    return {2: "primer", 4: "debate"}.get(occurrence, "none")


def canonical_arxiv_id(value: Any) -> str:
    """Normalize an arXiv URL or versioned identifier for de-duplication."""
    text = str(value or "").strip()
    if "/abs/" in text:
        text = text.split("/abs/", 1)[1]
    elif "/pdf/" in text:
        text = text.split("/pdf/", 1)[1]
    text = text.removesuffix(".pdf").strip("/")
    return ARXIV_VERSION_RE.sub("", text)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _inside(root: Path, path: Path) -> bool:
    root = root.resolve()
    path = path.resolve()
    return path == root or root in path.parents


def dedupe_weekly_papers(papers: list[dict]) -> list[dict]:
    """Keep the first (newest) complete occurrence of each canonical arXiv ID."""
    unique: dict[str, dict] = {}
    for paper in papers:
        arxiv_id = canonical_arxiv_id(paper.get("id"))
        if not arxiv_id or not _clean_text(paper.get("title")):
            continue
        if arxiv_id not in unique:
            item = dict(paper)
            item["id"] = arxiv_id
            item["url"] = f"https://arxiv.org/abs/{arxiv_id}"
            unique[arxiv_id] = item
            continue
        # Preserve the newest record while filling fields that were absent there.
        for key, value in paper.items():
            if key not in unique[arxiv_id] or unique[arxiv_id][key] in (None, "", []):
                unique[arxiv_id][key] = value
    return list(unique.values())


def load_recent_weekly_papers(
    data_root: Path, as_of: date, recent_days: int
) -> list[dict]:
    """Load recent weekly JSON through its index and de-duplicate paper versions."""
    index_path = data_root / "index.json"
    try:
        index = json.loads(index_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise FeatureError(f"Cannot load weekly index {index_path}: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("weeks"), list):
        raise FeatureError("Weekly index must contain a weeks array")

    cutoff = as_of - timedelta(days=recent_days)
    weeks: list[tuple[date, str]] = []
    for entry in index["weeks"]:
        if not isinstance(entry, dict):
            continue
        try:
            week_date = datetime.strptime(entry["date"], "%Y-%m%d").date()
            relative_file = entry["file"]
        except (KeyError, TypeError, ValueError):
            continue
        if cutoff <= week_date <= as_of and isinstance(relative_file, str):
            weeks.append((week_date, relative_file))

    papers: list[dict] = []
    for week_date, relative_file in sorted(weeks, reverse=True):
        path = (data_root / relative_file).resolve()
        if not _inside(data_root, path):
            raise FeatureError(f"Weekly index contains an unsafe path: {relative_file}")
        try:
            weekly = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise FeatureError(
                f"Cannot load indexed weekly file {path}: {exc}"
            ) from exc
        if not isinstance(weekly, dict) or not isinstance(
            weekly.get("categories"), list
        ):
            raise FeatureError(f"Weekly file has an invalid schema: {path}")
        for category in weekly["categories"]:
            if not isinstance(category, dict) or not isinstance(
                category.get("papers"), list
            ):
                continue
            for raw_paper in category["papers"]:
                if not isinstance(raw_paper, dict):
                    continue
                paper = dict(raw_paper)
                paper["archiveDate"] = week_date.isoformat()
                paper.setdefault("category", category.get("id", "other"))
                papers.append(paper)
    return dedupe_weekly_papers(papers)


def load_feature_index(output_dir: Path) -> dict:
    path = output_dir / "index.json"
    if not path.exists():
        return {"features": [], "generatedAt": ""}
    try:
        index = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FeatureError(f"Cannot parse feature index {path}: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("features"), list):
        raise FeatureError("Feature index must contain a features array")
    if not all(isinstance(entry, dict) for entry in index["features"]):
        raise FeatureError("Feature index entries must be JSON objects")
    return index


def load_existing_feature_for_slot(
    output_dir: Path, feature_index: dict, as_of: date, article_type: str
) -> dict | None:
    """Return a verified published feature for an idempotent scheduled rerun."""
    for entry in feature_index.get("features", []):
        if entry.get("date") != as_of.isoformat() or entry.get("type") != article_type:
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not TOPIC_KEY_RE.fullmatch(slug):
            raise FeatureError("Existing feature index has an invalid slug")
        relative_file = entry.get("file", f"{slug}.json")
        if not isinstance(relative_file, str):
            raise FeatureError("Existing feature index has an invalid file path")
        path = (output_dir / relative_file).resolve()
        if not _inside(output_dir, path):
            raise FeatureError("Existing feature index has an unsafe file path")
        try:
            feature = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise FeatureError(f"Indexed feature cannot be loaded: {path}") from exc
        validate_feature(feature)
        if feature.get("verification", {}).get("status") != "passed":
            raise FeatureError("Indexed feature is not marked as verifier-passed")
        if (
            feature.get("date") != as_of.isoformat()
            or feature.get("type") != article_type
        ):
            raise FeatureError("Indexed feature does not match its scheduled slot")
        return feature
    return None


def _has_valid_primary_link(paper: Mapping[str, Any]) -> bool:
    if "hasPrimaryLink" in paper:
        return paper.get("hasPrimaryLink") is True
    return any(
        isinstance(paper.get(field), str) and is_valid_primary_link(label, paper[field])
        for label, field in (("Code", "githubRepo"), ("Project", "projectPage"))
    )


def _candidate_payload(papers: list[dict], limit: int) -> list[dict]:
    result = []
    for paper in papers[:limit]:
        result.append(
            {
                "id": canonical_arxiv_id(paper.get("id")),
                "title": _clean_text(paper.get("title")),
                "abstract": _clean_text(paper.get("abstract"))[:2400],
                "task": paper.get("task"),
                "proposedMethod": paper.get("proposedMethod"),
                "datasets": paper.get("datasets", []),
                "category": paper.get("category", "other"),
                "archiveDate": paper.get("archiveDate"),
                "hasPrimaryLink": _has_valid_primary_link(paper),
            }
        )
    return result


def _normalized_term(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").split())


def _is_duplicate_topic(plan: dict, feature_index: dict, history_limit: int) -> bool:
    plan_key = plan["topicKey"]
    plan_terms = {_normalized_term(term) for term in plan["searchTerms"]}
    for feature in feature_index.get("features", [])[:history_limit]:
        if feature.get("topicKey") == plan_key:
            return True
        old_terms = {
            _normalized_term(term)
            for term in feature.get("searchTerms", [])
            if isinstance(term, str)
        }
        union = plan_terms | old_terms
        if old_terms and union and len(plan_terms & old_terms) / len(union) >= 0.6:
            return True
    return False


def validate_topic_plan(
    plan: Any,
    candidates: list[dict],
    feature_index: dict,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    """Validate and normalize the model-selected topic before doing any retrieval."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        raise FeatureValidationError(["Topic plan must be a JSON object"])

    normalized = dict(plan)
    topic_key = plan.get("topicKey")
    if not isinstance(topic_key, str) or not TOPIC_KEY_RE.fullmatch(topic_key):
        errors.append("topicKey must be lowercase kebab-case")

    for field in ("title", "titleEn", "angle"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            errors.append(f"{field} must be a non-empty string")

    raw_ids = plan.get("archivePaperIds")
    archive_ids = (
        [canonical_arxiv_id(value) for value in raw_ids]
        if isinstance(raw_ids, list)
        else []
    )
    candidate_by_id = {canonical_arxiv_id(p.get("id")): p for p in candidates}
    if len(archive_ids) != len(set(archive_ids)):
        errors.append("archivePaperIds must be unique")
    if not cfg["archive_source_min"] <= len(archive_ids) <= cfg["archive_source_max"]:
        errors.append(
            f"archivePaperIds must contain {cfg['archive_source_min']}-"
            f"{cfg['archive_source_max']} papers"
        )
    unknown = sorted(set(archive_ids) - set(candidate_by_id))
    if unknown:
        errors.append(f"archivePaperIds contains unknown IDs: {', '.join(unknown)}")
    linked_candidate_ids = {
        paper_id
        for paper_id, paper in candidate_by_id.items()
        if _has_valid_primary_link(paper)
    }
    required_primary_links = int(cfg.get("primary_link_min", 0))
    selected_linked_count = len(set(archive_ids) & linked_candidate_ids)
    if required_primary_links and len(linked_candidate_ids) < required_primary_links:
        errors.append(
            "Candidate set does not contain enough validated metadata-linked resources"
        )
    elif selected_linked_count < required_primary_links:
        errors.append(
            f"archivePaperIds must include at least {required_primary_links} paper(s) "
            "with hasPrimaryLink=true"
        )
    normalized["archivePaperIds"] = archive_ids

    terms = plan.get("searchTerms")
    if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
        terms = []
    terms = [term.strip() for term in terms]
    if not cfg["search_term_min"] <= len(terms) <= cfg["search_term_max"]:
        errors.append(
            f"searchTerms must contain {cfg['search_term_min']}-"
            f"{cfg['search_term_max']} terms"
        )
    if len({_normalized_term(term) for term in terms}) != len(terms):
        errors.append("searchTerms must be unique")
    for term in terms:
        if not SEARCH_TERM_RE.fullmatch(term):
            errors.append(f"Unsafe search term: {term!r}")
        if re.search(r"\b(?:AND|OR|NOT)\b", term, re.IGNORECASE):
            errors.append(f"Search terms cannot contain boolean operators: {term!r}")
    selected_texts = [
        _normalized_term(
            f"{candidate_by_id[paper_id].get('title', '')} "
            f"{candidate_by_id[paper_id].get('abstract', '')}"
        )
        for paper_id in archive_ids
        if paper_id in candidate_by_id
    ]
    for term in terms:
        if not any(_normalized_term(term) in text for text in selected_texts):
            errors.append(
                f"Search term is not grounded in selected archive papers: {term!r}"
            )
    normalized["searchTerms"] = terms

    perspectives = plan.get("perspectives")
    if (
        not isinstance(perspectives, list)
        or len(perspectives) != cfg["perspective_count"]
    ):
        errors.append(
            f"Topic plan must contain exactly {cfg['perspective_count']} perspectives"
        )
    else:
        perspective_ids = []
        for perspective in perspectives:
            if not isinstance(perspective, dict):
                errors.append("Each perspective must be an object")
                continue
            perspective_id = perspective.get("id")
            perspective_ids.append(perspective_id)
            if not isinstance(perspective_id, str) or not IDENTIFIER_RE.fullmatch(
                perspective_id
            ):
                errors.append("Perspective IDs must be lowercase kebab-case")
            for field in ("label", "description"):
                if (
                    not isinstance(perspective.get(field), str)
                    or not perspective[field].strip()
                ):
                    errors.append(f"Perspective {field} must be a non-empty string")
        if not _unique_strings(perspective_ids):
            errors.append("Perspective IDs must be unique")

    if not errors and _is_duplicate_topic(
        normalized, feature_index, cfg["prior_topic_limit"]
    ):
        errors.append("Topic duplicates an already-published feature")
    if errors:
        raise FeatureValidationError(errors)
    return normalized


def parse_arxiv_atom(xml_bytes: bytes) -> list[dict]:
    """Parse primary-source metadata returned by the official arXiv Atom API."""
    root = ET.fromstring(xml_bytes)
    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        arxiv_id = canonical_arxiv_id(entry.findtext("atom:id", "", ATOM_NS))
        title = _clean_text(entry.findtext("atom:title", "", ATOM_NS))
        abstract = _clean_text(entry.findtext("atom:summary", "", ATOM_NS))
        if not arxiv_id or not title or not abstract:
            continue
        papers.append(
            {
                "arxivId": arxiv_id,
                "title": title,
                "abstract": abstract,
                "authors": [
                    _clean_text(author.findtext("atom:name", "", ATOM_NS))
                    for author in entry.findall("atom:author", ATOM_NS)
                    if _clean_text(author.findtext("atom:name", "", ATOM_NS))
                ],
                "publishedAt": entry.findtext("atom:published", "", ATOM_NS),
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "origin": "external",
            }
        )
    return papers


def build_arxiv_query(search_terms: list[str], categories: list[str]) -> str:
    """Build query syntax only after search terms have passed strict validation."""
    term_query = " OR ".join(f'all:"{term}"' for term in search_terms)
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    return f"({category_query}) AND ({term_query})"


def _retryable_url_error(exc: Exception, retryable_statuses: list[int]) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in retryable_statuses
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionError))
    return False


def fetch_additional_arxiv_sources(
    search_terms: list[str],
    exclude_ids: set[str],
    *,
    categories: list[str] | None = None,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Search official arXiv Atom metadata for sources outside the weekly archive."""
    categories = categories or KEYWORDS["categories"]
    query = build_arxiv_query(search_terms, categories)
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": cfg["external_candidate_limit"],
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    user_agent = SETTINGS["arxiv"]["user_agent"]
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    retry_max = max(
        1, int(cfg.get("arxiv_retry_max", cfg.get("retry_max", 3)))
    )
    retry_interval = max(
        0.0,
        float(cfg.get("arxiv_retry_interval", cfg.get("retry_interval", 5.0))),
    )
    retry_max_interval = max(
        retry_interval,
        float(cfg.get("arxiv_retry_max_interval", retry_interval)),
    )
    retryable_statuses = list(cfg.get("retryable_http_statuses", []))
    for attempt in range(retry_max):
        try:
            with opener(request, timeout=cfg["request_timeout"]) as response:
                parsed = parse_arxiv_atom(response.read())
            seen = {canonical_arxiv_id(value) for value in exclude_ids}
            result = []
            for source in parsed:
                arxiv_id = canonical_arxiv_id(source["arxivId"])
                if arxiv_id in seen:
                    continue
                seen.add(arxiv_id)
                result.append(source)
            return result
        except (
            ET.ParseError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ) as exc:
            retryable = _retryable_url_error(exc, retryable_statuses)
            if (
                isinstance(exc, ET.ParseError)
                or not retryable
                or attempt == retry_max - 1
            ):
                raise FeatureError(
                    f"arXiv primary-source retrieval failed: {exc}"
                ) from exc
            retry_delay = min(
                retry_max_interval,
                retry_interval * (2**attempt),
            )
            if isinstance(exc, urllib.error.HTTPError):
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    retry_delay = min(
                        retry_max_interval,
                        max(retry_delay, float(retry_after)),
                    )
                except (TypeError, ValueError):
                    pass
                error_label = f"HTTP {exc.code}"
            else:
                error_label = type(exc).__name__
            print(
                "  [warn] arXiv primary-source retrieval "
                f"attempt {attempt + 1}/{retry_max} failed ({error_label}); "
                f"retrying in {retry_delay:g}s"
            )
            sleep(retry_delay)
    raise FeatureError("arXiv primary-source retry loop exited unexpectedly")


def select_archived_fallback_sources(
    search_terms: list[str],
    candidates: list[dict],
    exclude_ids: set[str],
    *,
    limit: int,
) -> list[dict]:
    """Rank unused weekly-archive papers when the live arXiv API is unavailable."""
    excluded = {canonical_arxiv_id(value) for value in exclude_ids}
    query_phrases = [_normalized_term(term) for term in search_terms]
    query_tokens = {
        token
        for phrase in query_phrases
        for token in re.findall(r"[a-z0-9]+", phrase)
        if len(token) >= 2
    }
    eligible: list[tuple[dict, str, set[str]]] = []
    for paper in candidates:
        arxiv_id = canonical_arxiv_id(paper.get("id"))
        title = _clean_text(paper.get("title"))
        abstract = _clean_text(paper.get("abstract"))
        if not arxiv_id or arxiv_id in excluded or not title or not abstract:
            continue
        searchable = _normalized_term(
            " ".join(
                str(paper.get(field) or "")
                for field in (
                    "title",
                    "abstract",
                    "task",
                    "proposedMethod",
                    "category",
                )
            )
        )
        tokens = set(re.findall(r"[a-z0-9]+", searchable))
        eligible.append((paper, searchable, tokens))

    document_frequency = {
        token: sum(token in tokens for _paper, _text, tokens in eligible)
        for token in query_tokens
    }
    ranked: list[tuple[float, str, str, dict]] = []
    document_count = len(eligible)
    for paper, searchable, tokens in eligible:
        matched_tokens = query_tokens & tokens
        if not matched_tokens:
            continue
        token_score = sum(
            math.log((document_count + 1) / (document_frequency[token] + 1)) + 1
            for token in matched_tokens
        )
        phrase_score = 6 * sum(
            bool(phrase and phrase in searchable) for phrase in query_phrases
        )
        arxiv_id = canonical_arxiv_id(paper.get("id"))
        ranked.append(
            (
                token_score + phrase_score,
                str(paper.get("archiveDate") or ""),
                arxiv_id,
                paper,
            )
        )

    result: list[dict] = []
    for _score, _archive_date, arxiv_id, paper in sorted(
        ranked, key=lambda item: item[:3], reverse=True
    )[: max(0, limit)]:
        result.append(
            {
                "arxivId": arxiv_id,
                "title": _clean_text(paper.get("title")),
                "abstract": _clean_text(paper.get("abstract")),
                "authors": [str(author) for author in paper.get("authors", [])],
                "publishedAt": paper.get("published_iso")
                or paper.get("archiveDate", ""),
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "origin": "historical",
            }
        )
    return result


def build_source_packet(
    plan: dict,
    candidates: list[dict],
    external_sources: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> list[dict]:
    candidate_by_id = {canonical_arxiv_id(p.get("id")): p for p in candidates}
    sources: list[dict] = []
    for arxiv_id in plan["archivePaperIds"]:
        paper = candidate_by_id[arxiv_id]
        primary_links = []
        for label, field in (("Code", "githubRepo"), ("Project", "projectPage")):
            url = paper.get(field)
            if isinstance(url, str) and is_valid_primary_link(label, url):
                primary_links.append({"label": label, "url": url})
        sources.append(
            {
                "arxivId": arxiv_id,
                "title": _clean_text(paper.get("title")),
                "abstract": _clean_text(paper.get("abstract")),
                "authors": [str(author) for author in paper.get("authors", [])],
                "publishedAt": paper.get("published_iso")
                or paper.get("archiveDate", ""),
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "origin": "archive",
                "archiveDate": paper.get("archiveDate"),
                "primaryLinks": primary_links,
            }
        )

    required_external = max(
        cfg["external_source_min"], cfg["source_target"] - len(sources)
    )
    if len(external_sources) < required_external:
        raise FeatureValidationError(
            [
                f"Need {required_external} external arXiv sources but retrieved "
                f"only {len(external_sources)}"
            ]
        )
    for source in external_sources[:required_external]:
        item = dict(source)
        item["primaryLinks"] = []
        sources.append(item)
    for index, source in enumerate(sources, 1):
        source["sourceId"] = f"S{index}"
    return sources


def _is_public_https_url(value: str) -> bool:
    if value != value.strip() or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return (
            "." in hostname
            and hostname != "localhost"
            and not hostname.endswith((".localhost", ".local"))
        )


def is_valid_primary_link(label: str, value: str) -> bool:
    """Accept only bounded, metadata-derived HTTPS resources safe to display."""
    if label not in ("Code", "Project") or not _is_public_https_url(value):
        return False
    parsed = urllib.parse.urlsplit(value)
    if label == "Code":
        path_parts = [part for part in parsed.path.split("/") if part]
        return (
            parsed.hostname.lower().rstrip(".") == "github.com" and len(path_parts) >= 2
        )
    return True


def grounding_source_payload(sources: list[dict]) -> list[dict]:
    """Remove discovery-only links before asking the model to make factual claims."""
    return [
        {key: value for key, value in source.items() if key != "primaryLinks"}
        for source in sources
    ]


def render_prompt(template: str, **values: Any) -> str:
    rendered = template
    for key, value in values.items():
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        rendered = rendered.replace("{{" + key + "}}", value)
    unresolved = re.findall(r"{{[a-zA-Z0-9_]+}}", rendered)
    if unresolved:
        raise FeatureError(f"Unresolved prompt placeholders: {', '.join(unresolved)}")
    return rendered


def _retryable_model_error(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 409, 425, 429) or exc.status_code >= 500
    return isinstance(
        exc,
        (
            json.JSONDecodeError,
            ValueError,
            TypeError,
            AttributeError,
            IndexError,
            KeyError,
        ),
    )


def _provider_fallback_error(exc: Exception) -> bool:
    """Return whether another configured provider may handle this failure."""
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and (
        status_code in (408, 409, 425, 429) or status_code >= 500
    )


def _compact_provider_payload(payload: Any, provider_cfg: Mapping[str, Any]) -> Any:
    """Bound large feature inputs for providers with smaller free-tier contexts."""
    raw_candidate_limit = provider_cfg.get("feature_topic_candidate_limit")
    raw_linked_candidate_min = provider_cfg.get("feature_linked_candidate_min")
    raw_abstract_limit = provider_cfg.get("feature_abstract_max_chars")
    if (
        raw_candidate_limit is None
        and raw_linked_candidate_min is None
        and raw_abstract_limit is None
    ):
        return payload

    candidate_limit = (
        max(1, int(raw_candidate_limit))
        if raw_candidate_limit is not None
        else None
    )
    abstract_limit = (
        max(1, int(raw_abstract_limit))
        if raw_abstract_limit is not None
        else None
    )
    linked_candidate_min = (
        max(0, int(raw_linked_candidate_min))
        if raw_linked_candidate_min is not None
        else 0
    )

    def compact(value: Any, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {
                item_key: compact(item, item_key)
                for item_key, item in value.items()
            }
        if isinstance(value, list):
            items = value
            if key == "archiveCandidates" and candidate_limit is not None:
                items = list(items[:candidate_limit])
                linked_count = sum(
                    isinstance(item, Mapping)
                    and item.get("hasPrimaryLink") is True
                    for item in items
                )
                if linked_count < linked_candidate_min:
                    missing_linked = [
                        item
                        for item in value[candidate_limit:]
                        if isinstance(item, Mapping)
                        and item.get("hasPrimaryLink") is True
                    ][: linked_candidate_min - linked_count]
                    for linked_item in missing_linked:
                        replace_at = next(
                            (
                                index
                                for index in range(len(items) - 1, -1, -1)
                                if not (
                                    isinstance(items[index], Mapping)
                                    and items[index].get("hasPrimaryLink") is True
                                )
                            ),
                            None,
                        )
                        if replace_at is None:
                            break
                        items[replace_at] = linked_item
            return [compact(item) for item in items]
        if key == "abstract" and abstract_limit is not None and isinstance(value, str):
            return value[:abstract_limit]
        return value

    return compact(payload)


class JsonModel:
    """Rate-limited JSON-only wrapper around the configured OpenAI-compatible model."""

    def __init__(
        self,
        settings: Mapping[str, Any] = SETTINGS,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.feature_cfg = settings.get("features", FEATURE_SETTINGS)
        self.client = create_client(settings).with_options(
            timeout=float(self.feature_cfg.get("model_timeout", 180)), max_retries=0
        )
        self.sleep = sleep
        self.monotonic = monotonic
        self.last_request_at: float | None = None
        self._fallback_model: JsonModel | None = None

    def complete(
        self, instructions: str, payload: Any, max_tokens: int, purpose: str
    ) -> dict:
        if self._fallback_model is not None:
            return self._fallback_model.complete(
                instructions, payload, max_tokens, purpose
            )
        provider_name, provider_cfg = get_ai_config(self.settings)
        retry_max = max(
            1,
            int(
                self.feature_cfg.get(
                    "model_retry_max", provider_cfg.get("retry_max", 3)
                )
            ),
        )
        retry_interval = max(
            0.0,
            float(
                self.feature_cfg.get(
                    "model_retry_interval", provider_cfg.get("retry_interval", 5.0)
                )
            ),
        )
        retry_max_interval = max(
            retry_interval,
            float(self.feature_cfg.get("model_retry_max_interval", 240.0)),
        )
        configured_max_tokens = max(
            1, int(self.feature_cfg.get("model_max_tokens", max_tokens))
        )
        provider_max_tokens = max(
            1,
            int(provider_cfg.get("feature_max_tokens", configured_max_tokens)),
        )
        model_max_tokens = min(configured_max_tokens, provider_max_tokens)
        reasoning_effort = self.feature_cfg.get(
            f"{purpose.replace(' ', '_')}_reasoning_effort"
        )
        request_max_tokens = min(max(1, max_tokens), model_max_tokens)
        request_payload = _compact_provider_payload(payload, provider_cfg)
        for attempt in range(retry_max):
            if self.last_request_at is not None:
                remaining = float(provider_cfg.get("min_request_interval", 0)) - (
                    self.monotonic() - self.last_request_at
                )
                if remaining > 0:
                    self.sleep(remaining)
            request_started_at = self.monotonic()
            try:
                response = self.client.chat.completions.create(
                    model=provider_cfg["model"],
                    messages=[
                        {"role": "system", "content": instructions},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"untrustedData": request_payload},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                    **build_chat_kwargs(
                        provider_cfg["model"],
                        request_max_tokens,
                        temperature=self.feature_cfg.get("temperature", 0.2),
                        reasoning_effort=reasoning_effort,
                    ),
                )
                self.last_request_at = self.monotonic()
                choice = response.choices[0]
                raw = (choice.message.content or "").strip()
                finish_reason = str(getattr(choice, "finish_reason", "") or "").lower()
                if finish_reason in TRUNCATED_FINISH_REASONS:
                    usage = getattr(response, "usage", None)
                    details = getattr(usage, "completion_tokens_details", None)
                    diagnostics = (
                        f"finish_reason={finish_reason}, "
                        f"requested_max_tokens={request_max_tokens}, "
                        f"completion_tokens={getattr(usage, 'completion_tokens', None)}, "
                        f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)}"
                    )
                    raise ModelResponseTruncated(
                        f"response truncated before valid JSON ({diagnostics})"
                    )
                raw = (
                    raw.removeprefix("```json")
                    .removeprefix("```")
                    .removesuffix("```")
                    .strip()
                )
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("response is not a JSON object")
                return result
            except Exception as exc:
                self.last_request_at = request_started_at
                fallback_providers = [
                    name
                    for name in self.feature_cfg.get("model_fallback_providers", [])
                    if isinstance(name, str)
                    and name != provider_name
                    and isinstance(self.settings.get(name), Mapping)
                ]
                fallback_after = max(
                    1, int(self.feature_cfg.get("model_fallback_after", 2))
                )
                status_code = getattr(exc, "status_code", None)
                if (
                    fallback_providers
                    and _provider_fallback_error(exc)
                    and (status_code == 429 or attempt + 1 >= fallback_after)
                ):
                    fallback_provider = fallback_providers[0]
                    status = (
                        f", status_code={status_code}"
                        if status_code is not None
                        else ""
                    )
                    print(
                        f"  [warn] AI {purpose} provider {provider_name} exhausted "
                        f"({type(exc).__name__}{status}); falling back to "
                        f"{fallback_provider}"
                    )
                    fallback_settings = dict(self.settings)
                    fallback_features = dict(self.feature_cfg)
                    fallback_features["model_fallback_providers"] = (
                        fallback_providers[1:]
                    )
                    fallback_settings["ai"] = {"provider": fallback_provider}
                    fallback_settings["features"] = fallback_features
                    try:
                        self._fallback_model = JsonModel(
                            fallback_settings,
                            sleep=self.sleep,
                            monotonic=self.monotonic,
                        )
                    except Exception as fallback_exc:
                        raise FeatureError(
                            f"AI {purpose} failed closed: fallback provider "
                            f"{fallback_provider} is unavailable "
                            f"({type(fallback_exc).__name__})"
                        ) from exc
                    return self._fallback_model.complete(
                        instructions, payload, max_tokens, purpose
                    )
                if not _retryable_model_error(exc) or attempt == retry_max - 1:
                    raise FeatureError(f"AI {purpose} failed closed: {exc}") from exc
                retry_delay = min(retry_interval * (2**attempt), retry_max_interval)
                if isinstance(exc, ModelResponseTruncated):
                    next_max_tokens = min(request_max_tokens * 2, model_max_tokens)
                    print(
                        f"  [warn] AI {purpose} {exc}; retrying with "
                        f"max_tokens={next_max_tokens} in {retry_delay:g}s"
                    )
                    request_max_tokens = next_max_tokens
                else:
                    status = (
                        f", status_code={status_code}"
                        if status_code is not None
                        else ""
                    )
                    print(
                        f"  [warn] AI {purpose} attempt {attempt + 1}/{retry_max} "
                        f"failed ({type(exc).__name__}{status}); retrying in "
                        f"{retry_delay:g}s"
                    )
                self.sleep(retry_delay)
        raise FeatureError(f"AI {purpose} retry loop exited unexpectedly")


def choose_topic(
    model: Any,
    article_type: str,
    candidates: list[dict],
    feature_index: dict,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    candidate_payload = _candidate_payload(candidates, cfg["topic_candidate_limit"])
    prior_topics = [
        {
            "topicKey": entry.get("topicKey"),
            "searchTerms": entry.get("searchTerms", []),
            "titleEn": entry.get("titleEn"),
        }
        for entry in feature_index.get("features", [])[: cfg["prior_topic_limit"]]
    ]
    instructions = render_prompt(
        PROMPTS["select"],
        article_type=article_type,
    )
    validation_retry_max = max(1, int(cfg.get("selection_validation_retry_max", 1)))
    validation_errors: list[str] | None = None
    for attempt in range(validation_retry_max):
        payload: dict[str, Any] = {
            "priorTopics": prior_topics,
            "archiveCandidates": candidate_payload,
        }
        if validation_errors is not None:
            payload["validationFeedback"] = {
                "errors": validation_errors,
                "remainingAttempts": validation_retry_max - attempt,
            }
        raw_plan = model.complete(
            instructions,
            payload,
            cfg["selection_max_tokens"],
            "topic selection",
        )
        try:
            return validate_topic_plan(raw_plan, candidate_payload, feature_index, cfg)
        except FeatureValidationError as exc:
            if attempt == validation_retry_max - 1:
                raise
            validation_errors = list(exc.errors)
            print(
                "  [warn] AI topic selection failed local validation "
                f"(attempt {attempt + 1}/{validation_retry_max}, "
                f"errors={len(validation_errors)}); requesting a corrected plan"
            )
    raise FeatureError("AI topic selection validation loop exited unexpectedly")


def _body_texts(feature: Mapping[str, Any]) -> list[str]:
    sections = feature.get("sections", [])
    if not isinstance(sections, list):
        return []
    texts: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                texts.append(block["text"])
    return texts


def article_character_count(feature: Mapping[str, Any]) -> int:
    text = "".join(_body_texts(feature))
    return len("".join(text.split()))


def _japanese_ratio(value: str) -> float:
    japanese = len(JAPANESE_CHAR_RE.findall(value))
    latin = len(LATIN_CHAR_RE.findall(value))
    return japanese / max(1, japanese + latin)


def _valid_source_ids(value: Any, known_ids: set[str]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if not all(isinstance(item, str) and item in known_ids for item in value):
        return False
    return len(value) == len(set(value))


def _unique_strings(values: list[Any]) -> bool:
    return all(isinstance(value, str) for value in values) and len(values) == len(
        set(values)
    )


def english_article_word_count(body: Mapping[str, Any]) -> int:
    return sum(len(ENGLISH_WORD_RE.findall(text)) for text in _body_texts(body))


def _predominantly_english(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and bool(LATIN_CHAR_RE.search(value))
        and _japanese_ratio(value) <= 0.1
    )


def _predominantly_japanese(value: Any, cfg: Mapping[str, Any]) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and bool(JAPANESE_CHAR_RE.search(value))
        and _japanese_ratio(value) >= cfg["japanese_metadata_min_ratio"]
    )


def validate_english_body(
    body: Any,
    sources: list[dict],
    article_type: str,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> None:
    """Validate the canonical English draft before any translation is attempted."""
    errors: list[str] = []
    if not isinstance(body, dict):
        raise FeatureValidationError(["English draft must be a JSON object"])
    if article_type not in ("primer", "debate"):
        raise FeatureValidationError(["English draft type must be primer or debate"])

    for field in ("title", "dek", "summary"):
        if not _predominantly_english(body.get(field)):
            errors.append(f"English draft {field} must be predominantly English")
    summary = body.get("summary")
    if isinstance(summary, str):
        summary_words = len(ENGLISH_WORD_RE.findall(summary))
        if not (
            cfg["english_summary_min_words"]
            <= summary_words
            <= cfg["english_summary_max_words"]
        ):
            errors.append(
                "English draft summary must contain "
                f"{cfg['english_summary_min_words']}-"
                f"{cfg['english_summary_max_words']} words"
            )
    key_points = body.get("keyPoints")
    if (
        not isinstance(key_points, list)
        or len(key_points) < 3
        or not all(_predominantly_english(point) for point in key_points)
    ):
        errors.append("English draft keyPoints must contain at least three items")

    known_source_ids = {
        source.get("sourceId")
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("sourceId"), str)
    }
    perspectives = body.get("perspectives")
    perspective_ids: list[Any] = []
    if (
        not isinstance(perspectives, list)
        or len(perspectives) != cfg["perspective_count"]
    ):
        errors.append(
            "English draft must contain exactly "
            f"{cfg['perspective_count']} perspectives"
        )
    else:
        for perspective in perspectives:
            if not isinstance(perspective, dict):
                errors.append("Every English perspective must be an object")
                continue
            perspective_id = perspective.get("id")
            perspective_ids.append(perspective_id)
            if not isinstance(perspective_id, str) or not IDENTIFIER_RE.fullmatch(
                perspective_id
            ):
                errors.append("English perspective IDs must be lowercase kebab-case")
            for field in ("label", "description"):
                if not _predominantly_english(perspective.get(field)):
                    errors.append(
                        f"English perspective {field} must be predominantly English"
                    )
            if not _valid_source_ids(
                perspective.get("sourceIds"), known_source_ids
            ):
                errors.append(
                    f"English perspective {perspective_id!r} has invalid sourceIds"
                )
        if not _unique_strings(perspective_ids):
            errors.append("English perspective IDs must be unique")

    sections = body.get("sections")
    sections = sections if isinstance(sections, list) else []
    if len(sections) < cfg["section_min"]:
        errors.append(
            f"English draft must contain at least {cfg['section_min']} sections"
        )
    section_ids: list[Any] = []
    block_ids: list[Any] = []
    cited_source_ids: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            errors.append("Every English section must be an object")
            continue
        section_id = section.get("id")
        section_ids.append(section_id)
        if not isinstance(section_id, str) or not IDENTIFIER_RE.fullmatch(section_id):
            errors.append("English section IDs must be lowercase kebab-case")
        if not _predominantly_english(section.get("heading")):
            errors.append(
                f"English section {section_id!r} heading must be predominantly English"
            )
        blocks = section.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            errors.append(f"English section {section_id!r} needs at least one block")
            continue
        for block in blocks:
            if not isinstance(block, dict):
                errors.append("Every English block must be an object")
                continue
            block_id = block.get("id")
            block_ids.append(block_id)
            if not isinstance(block_id, str) or not IDENTIFIER_RE.fullmatch(block_id):
                errors.append("English block IDs must be lowercase kebab-case")
            if not _predominantly_english(block.get("text")):
                errors.append(
                    f"English block {block_id!r} text must be predominantly English"
                )
            if not _valid_source_ids(block.get("sourceIds"), known_source_ids):
                errors.append(
                    f"English block {block_id!r} has invalid or empty sourceIds"
                )
            else:
                cited_source_ids.update(block["sourceIds"])
    required_sections = set(cfg[f"{article_type}_sections"])
    missing_sections = sorted(
        required_sections - {value for value in section_ids if isinstance(value, str)}
    )
    if missing_sections:
        errors.append(
            "English draft is missing required sections: "
            + ", ".join(missing_sections)
        )
    if not _unique_strings(section_ids):
        errors.append("English section IDs must be unique")
    if not _unique_strings(block_ids):
        errors.append("English block IDs must be unique")
    unused_sources = sorted(known_source_ids - cited_source_ids)
    if unused_sources:
        errors.append(
            "Every source must be cited in English; unused: "
            + ", ".join(unused_sources)
        )

    word_count = english_article_word_count(body)
    if not (
        cfg["english_body_validation_min_words"]
        <= word_count
        <= cfg["english_body_validation_max_words"]
    ):
        errors.append(
            f"English body has {word_count} words; allowed range is "
            f"{cfg['english_body_validation_min_words']}-"
            f"{cfg['english_body_validation_max_words']}"
        )
    if errors:
        raise FeatureValidationError(errors)


def validate_feature(feature: Any, cfg: Mapping[str, Any] = FEATURE_SETTINGS) -> None:
    """Apply deterministic publication gates to a complete feature payload."""
    errors: list[str] = []
    if not isinstance(feature, dict):
        raise FeatureValidationError(["Feature must be a JSON object"])

    if feature.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if feature.get("sourceLanguage") != "en":
        errors.append("sourceLanguage must be en")
    translation = feature.get("translation")
    if (
        not isinstance(translation, dict)
        or translation.get("targetLanguage") != "ja"
        or translation.get("status") != "passed"
    ):
        errors.append("translation must be verifier-passed English-to-Japanese")
    if feature.get("type") not in ("primer", "debate"):
        errors.append("type must be primer or debate")
    try:
        date.fromisoformat(feature.get("date", ""))
    except (TypeError, ValueError):
        errors.append("date must be ISO YYYY-MM-DD")
    slug = feature.get("slug")
    if not isinstance(slug, str) or not TOPIC_KEY_RE.fullmatch(slug):
        errors.append("slug must be lowercase kebab-case")
    for field in ("topicKey", "title", "titleEn", "dek", "dekEn", "summaryEn"):
        if not isinstance(feature.get(field), str) or not feature[field].strip():
            errors.append(f"{field} must be a non-empty string")
    for field in ("title", "dek"):
        if not _predominantly_japanese(feature.get(field), cfg):
            errors.append(f"{field} must be predominantly Japanese")
    for field in ("titleEn", "dekEn"):
        if not _predominantly_english(feature.get(field)):
            errors.append(f"{field} must be predominantly English")
    summary_en = feature.get("summaryEn")
    if isinstance(summary_en, str):
        summary_word_count = len(ENGLISH_WORD_RE.findall(summary_en))
        if (
            not cfg["english_summary_min_words"]
            <= summary_word_count
            <= cfg["english_summary_max_words"]
        ):
            errors.append(
                f"summaryEn must contain {cfg['english_summary_min_words']}-"
                f"{cfg['english_summary_max_words']} English words"
            )
        if not LATIN_CHAR_RE.search(summary_en) or _japanese_ratio(summary_en) > 0.1:
            errors.append("summaryEn must be predominantly English")
    key_points = feature.get("keyPointsEn")
    if (
        not isinstance(key_points, list)
        or len(key_points) < 3
        or not all(isinstance(point, str) and point.strip() for point in key_points)
    ):
        errors.append("keyPointsEn must contain at least three non-empty strings")
    else:
        for point in key_points:
            if not LATIN_CHAR_RE.search(point) or _japanese_ratio(point) > 0.1:
                errors.append("Every keyPointsEn item must be predominantly English")

    sources = feature.get("sources")
    sources = sources if isinstance(sources, list) else []
    if len(sources) < cfg["source_target"]:
        errors.append(f"Feature must contain at least {cfg['source_target']} sources")
    source_ids: list[Any] = []
    arxiv_ids: list[str] = []
    archive_count = 0
    external_count = 0
    valid_primary_link_count = 0
    for source in sources:
        if not isinstance(source, dict):
            errors.append("Each source must be an object")
            continue
        source_id = source.get("sourceId")
        arxiv_id = canonical_arxiv_id(source.get("arxivId"))
        source_ids.append(source_id)
        arxiv_ids.append(arxiv_id)
        if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id):
            errors.append("Source IDs must use S1, S2, ... format")
        if (
            not arxiv_id
            or not _clean_text(source.get("title"))
            or not _clean_text(source.get("abstract"))
        ):
            errors.append(f"Source {source_id!r} lacks canonical arXiv metadata")
        expected_url = f"https://arxiv.org/abs/{arxiv_id}"
        if source.get("url") != expected_url:
            errors.append(f"Source {source_id!r} must use its canonical arXiv URL")
        if source.get("origin") == "archive":
            archive_count += 1
        elif source.get("origin") in ("external", "historical"):
            external_count += 1
        else:
            errors.append(f"Source {source_id!r} has an invalid origin")
        primary_links = source.get("primaryLinks", [])
        if not isinstance(primary_links, list):
            errors.append(f"Source {source_id!r} primaryLinks must be an array")
        else:
            for link in primary_links:
                valid_link = (
                    isinstance(link, dict)
                    and isinstance(link.get("label"), str)
                    and isinstance(link.get("url"), str)
                    and is_valid_primary_link(link["label"], link["url"])
                )
                if not valid_link:
                    errors.append(f"Source {source_id!r} has an invalid primaryLink")
                else:
                    valid_primary_link_count += 1
    if not _unique_strings(source_ids):
        errors.append("Source IDs must be unique")
    if len(arxiv_ids) != len(set(arxiv_ids)):
        errors.append("Source arXiv IDs must be unique")
    if archive_count < cfg["archive_source_min"]:
        errors.append(
            f"Feature needs at least {cfg['archive_source_min']} archive sources"
        )
    if external_count < cfg["external_source_min"]:
        errors.append(
            f"Feature needs at least {cfg['external_source_min']} external sources"
        )
    if valid_primary_link_count < cfg.get("primary_link_min", 0):
        errors.append(
            f"Feature needs at least {cfg['primary_link_min']} validated "
            "metadata-linked resource"
        )
    known_source_ids = {value for value in source_ids if isinstance(value, str)}

    perspectives = feature.get("perspectives")
    if (
        not isinstance(perspectives, list)
        or len(perspectives) != cfg["perspective_count"]
    ):
        errors.append(
            f"Feature must contain exactly {cfg['perspective_count']} perspectives"
        )
    else:
        perspective_ids = []
        for perspective in perspectives:
            if not isinstance(perspective, dict):
                errors.append("Each perspective must be an object")
                continue
            perspective_ids.append(perspective.get("id"))
            for field in ("id", "label", "description"):
                if (
                    not isinstance(perspective.get(field), str)
                    or not perspective[field].strip()
                ):
                    errors.append(f"Perspective {field} must be a non-empty string")
            for field in ("label", "description"):
                value = perspective.get(field)
                if not _predominantly_japanese(value, cfg):
                    errors.append(
                        f"Perspective {field} must be predominantly Japanese"
                    )
            for field in ("labelEn", "descriptionEn"):
                if not _predominantly_english(perspective.get(field)):
                    errors.append(
                        f"Perspective {field} must be predominantly English"
                    )
            if not _valid_source_ids(perspective.get("sourceIds"), known_source_ids):
                errors.append(
                    f"Perspective {perspective.get('id')!r} has invalid sourceIds"
                )
        if not _unique_strings(perspective_ids):
            errors.append("Perspective IDs must be unique")

    sections = feature.get("sections")
    sections = sections if isinstance(sections, list) else []
    if len(sections) < cfg["section_min"]:
        errors.append(f"Feature must contain at least {cfg['section_min']} sections")
    section_ids: list[Any] = []
    block_ids: list[Any] = []
    cited_source_ids: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            errors.append("Each section must be an object")
            continue
        section_ids.append(section.get("id"))
        if not isinstance(section.get("id"), str) or not IDENTIFIER_RE.fullmatch(
            section["id"]
        ):
            errors.append("Section IDs must be lowercase kebab-case")
        if (
            not isinstance(section.get("heading"), str)
            or not section["heading"].strip()
        ):
            errors.append(f"Section {section.get('id')!r} needs a heading")
        elif not _predominantly_japanese(section["heading"], cfg):
            errors.append(
                f"Section {section.get('id')!r} heading must be predominantly Japanese"
            )
        if not _predominantly_english(section.get("headingEn")):
            errors.append(
                f"Section {section.get('id')!r} headingEn must be predominantly English"
            )
        blocks = section.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            errors.append(f"Section {section.get('id')!r} needs at least one block")
            continue
        for block in blocks:
            if not isinstance(block, dict):
                errors.append("Each block must be an object")
                continue
            block_ids.append(block.get("id"))
            if not isinstance(block.get("id"), str) or not IDENTIFIER_RE.fullmatch(
                block["id"]
            ):
                errors.append("Block IDs must be lowercase kebab-case")
            if not isinstance(block.get("text"), str) or not block["text"].strip():
                errors.append(f"Block {block.get('id')!r} needs Japanese text")
            elif _japanese_ratio(block["text"]) < cfg["japanese_body_min_ratio"]:
                errors.append(
                    f"Block {block.get('id')!r} must be predominantly Japanese"
                )
            if not _predominantly_english(block.get("textEn")):
                errors.append(
                    f"Block {block.get('id')!r} textEn must be predominantly English"
                )
            if not _valid_source_ids(block.get("sourceIds"), known_source_ids):
                errors.append(
                    f"Block {block.get('id')!r} has invalid or empty sourceIds"
                )
            else:
                cited_source_ids.update(block["sourceIds"])
    required_section_ids = (
        set(cfg[f"{feature.get('type')}_sections"])
        if feature.get("type") in ("primer", "debate")
        else set()
    )
    present_section_ids = {value for value in section_ids if isinstance(value, str)}
    missing_section_ids = sorted(required_section_ids - present_section_ids)
    if missing_section_ids:
        errors.append(f"Missing required sections: {', '.join(missing_section_ids)}")
    if not _unique_strings(section_ids):
        errors.append("Section IDs must be unique")
    if not _unique_strings(block_ids):
        errors.append("Block IDs must be unique")
    unused_sources = sorted(known_source_ids - cited_source_ids)
    if unused_sources:
        errors.append(
            f"Every source must be cited; unused: {', '.join(unused_sources)}"
        )

    char_count = article_character_count(feature)
    if not cfg["validation_min_chars"] <= char_count <= cfg["validation_max_chars"]:
        errors.append(
            f"Japanese body has {char_count} characters; allowed tolerance is "
            f"{cfg['validation_min_chars']}-{cfg['validation_max_chars']}"
        )
    body_text = "".join(_body_texts(feature))
    if _japanese_ratio(body_text) < cfg["japanese_body_min_ratio"]:
        errors.append(
            "Japanese body language ratio is below "
            f"{cfg['japanese_body_min_ratio']:.0%}"
        )
    expected_read_time = max(1, math.ceil(char_count / cfg["reading_chars_per_minute"]))
    if feature.get("readTimeMinutes") != expected_read_time:
        errors.append(f"readTimeMinutes must be {expected_read_time}")
    if (
        not cfg["reading_minutes_min"]
        <= expected_read_time
        <= cfg["reading_minutes_max"]
    ):
        errors.append(
            f"Estimated reading time must be {cfg['reading_minutes_min']}-"
            f"{cfg['reading_minutes_max']} minutes"
        )
    english_body = {
        "title": feature.get("titleEn"),
        "dek": feature.get("dekEn"),
        "summary": feature.get("summaryEn"),
        "keyPoints": feature.get("keyPointsEn"),
        "perspectives": [
            {
                "id": perspective.get("id"),
                "label": perspective.get("labelEn"),
                "description": perspective.get("descriptionEn"),
                "sourceIds": perspective.get("sourceIds"),
            }
            for perspective in (
                perspectives if isinstance(perspectives, list) else []
            )
            if isinstance(perspective, dict)
        ],
        "sections": [
            {
                "id": section.get("id"),
                "heading": section.get("headingEn"),
                "blocks": [
                    {
                        "id": block.get("id"),
                        "text": block.get("textEn"),
                        "sourceIds": block.get("sourceIds"),
                    }
                    for block in (
                        section.get("blocks")
                        if isinstance(section.get("blocks"), list)
                        else []
                    )
                    if isinstance(block, dict)
                ],
            }
            for section in sections
            if isinstance(section, dict)
        ],
    }
    try:
        validate_english_body(english_body, sources, feature.get("type"), cfg)
    except FeatureValidationError as exc:
        errors.extend(exc.errors)
    english_read_time = max(
        1,
        math.ceil(
            english_article_word_count(english_body)
            / cfg["english_reading_words_per_minute"]
        ),
    )
    if feature.get("readTimeMinutesEn") != english_read_time:
        errors.append(f"readTimeMinutesEn must be {english_read_time}")
    if errors:
        raise FeatureValidationError(errors)


BODY_FIELDS = (
    "title",
    "titleEn",
    "dek",
    "dekEn",
    "summaryEn",
    "keyPointsEn",
    "perspectives",
    "sections",
    "translation",
)


def assemble_feature(
    body: dict,
    *,
    plan: dict,
    sources: list[dict],
    article_type: str,
    as_of: date,
    generated_at: datetime,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    topic_key = plan["topicKey"]
    slug = f"{as_of.isoformat()}-{article_type}-{topic_key}"
    feature = {field: body.get(field) for field in BODY_FIELDS}
    feature.update(
        {
            "schemaVersion": 1,
            "sourceLanguage": "en",
            "slug": slug,
            "type": article_type,
            "date": as_of.isoformat(),
            "generatedAt": generated_at.astimezone(timezone.utc).isoformat(),
            "topicKey": topic_key,
            "searchTerms": list(plan["searchTerms"]),
            "sources": sources,
        }
    )
    char_count = article_character_count(feature)
    feature["readTimeMinutes"] = max(
        1, math.ceil(char_count / cfg["reading_chars_per_minute"])
    )
    feature["readTimeMinutesEn"] = max(
        1,
        math.ceil(
            sum(
                len(ENGLISH_WORD_RE.findall(block.get("textEn", "")))
                for section in feature.get("sections", [])
                if isinstance(section, dict)
                for block in section.get("blocks", [])
                if isinstance(block, dict)
            )
            / cfg["english_reading_words_per_minute"]
        ),
    )
    return feature


def _body_from_feature(feature: dict) -> dict:
    return {field: feature.get(field) for field in BODY_FIELDS}


def _issue_payload(errors: list[str]) -> list[dict]:
    return [{"blockId": "_article", "reason": error} for error in errors]


def validate_verdict(verdict: Any, feature: dict) -> dict:
    if not isinstance(verdict, dict) or verdict.get("status") not in ("pass", "revise"):
        raise FeatureValidationError(["Verifier must return status pass or revise"])
    issues = verdict.get("issues")
    if not isinstance(issues, list):
        raise FeatureValidationError(["Verifier issues must be an array"])
    block_ids: set[str] = set()
    sections = feature.get("sections", [])
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            blocks = section.get("blocks", [])
            if not isinstance(blocks, list):
                continue
            block_ids.update(
                block["id"]
                for block in blocks
                if isinstance(block, dict) and isinstance(block.get("id"), str)
            )
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or issue.get("blockId") not in block_ids | {"_article"}
            or not isinstance(issue.get("reason"), str)
            or not issue["reason"].strip()
        ):
            raise FeatureValidationError(["Verifier returned an invalid issue"])
    if verdict["status"] == "pass" and issues:
        raise FeatureValidationError(
            ["A passing verifier result cannot contain issues"]
        )
    if verdict["status"] == "revise" and not issues:
        raise FeatureValidationError(["A revise verifier result must contain issues"])
    return verdict


def _verifier_issue_summary(issues: list[dict], limit: int = 5) -> str:
    summarized = [
        f"{issue['blockId']}: {_clean_text(issue['reason'])[:160]}"
        for issue in issues[:limit]
    ]
    if len(issues) > limit:
        summarized.append(f"and {len(issues) - limit} more")
    return "; ".join(summarized)


def generate_body(
    model: Any,
    plan: dict,
    sources: list[dict],
    article_type: str,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    instructions = render_prompt(
        PROMPTS["generate"],
        target_min_chars=str(cfg["target_min_chars"]),
        target_max_chars=str(cfg["target_max_chars"]),
        section_min=str(cfg["section_min"]),
        perspective_count=str(cfg["perspective_count"]),
        english_summary_min_words=str(cfg["english_summary_min_words"]),
        english_summary_max_words=str(cfg["english_summary_max_words"]),
        required_sections_json=cfg[f"{article_type}_sections"],
    )
    return model.complete(
        instructions,
        {
            "featurePlan": {**plan, "articleType": article_type},
            "primarySources": grounding_source_payload(sources),
        },
        cfg["generation_max_tokens"],
        "feature generation",
    )


def generate_english_body(
    model: Any,
    plan: dict,
    sources: list[dict],
    article_type: str,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    """Generate and locally validate the canonical English edition."""
    instructions = render_prompt(
        PROMPTS["generate_en"],
        english_body_target_min_words=str(cfg["english_body_target_min_words"]),
        english_body_target_max_words=str(cfg["english_body_target_max_words"]),
        english_summary_min_words=str(cfg["english_summary_min_words"]),
        english_summary_max_words=str(cfg["english_summary_max_words"]),
        section_min=str(cfg["section_min"]),
        perspective_count=str(cfg["perspective_count"]),
        required_sections_json=cfg[f"{article_type}_sections"],
    )
    retry_max = max(
        1, int(cfg.get("english_generation_validation_retry_max", 1))
    )
    validation_errors: list[str] | None = None
    for attempt in range(retry_max):
        payload: dict[str, Any] = {
            "featurePlan": {**plan, "articleType": article_type},
            "primarySources": grounding_source_payload(sources),
        }
        if validation_errors is not None:
            payload["validationFeedback"] = {
                "errors": validation_errors,
                "remainingAttempts": retry_max - attempt,
            }
        body = model.complete(
            instructions,
            payload,
            cfg["generation_max_tokens"],
            "feature generation",
        )
        try:
            validate_english_body(body, sources, article_type, cfg)
            return body
        except FeatureValidationError as exc:
            if attempt == retry_max - 1:
                raise
            validation_errors = list(exc.errors)
            print(
                "  [warn] AI English feature generation failed local validation "
                f"(attempt {attempt + 1}/{retry_max}, "
                f"errors={len(validation_errors)}); requesting a corrected draft"
            )
    raise FeatureError("AI English feature generation loop exited unexpectedly")


def verify_english_body(
    model: Any,
    body: dict,
    plan: dict,
    sources: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    verdict = model.complete(
        render_prompt(PROMPTS["verify_en"]),
        {
            "featurePlan": plan,
            "primarySources": grounding_source_payload(sources),
            "draft": body,
        },
        cfg["verification_max_tokens"],
        "grounding verification",
    )
    return validate_verdict(verdict, body)


def _validate_translation_metadata(
    raw: Any,
    english_body: dict,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise FeatureValidationError(["Japanese metadata translation must be an object"])
    for field in ("title", "dek"):
        value = raw.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or not _predominantly_japanese(value, cfg)
        ):
            errors.append(
                f"Japanese translation {field} must be predominantly Japanese"
            )

    expected_perspectives = english_body["perspectives"]
    translated_perspectives = raw.get("perspectives")
    if not isinstance(translated_perspectives, list):
        errors.append("Japanese translation perspectives must be an array")
        translated_perspectives = []
    expected_perspective_ids = [item["id"] for item in expected_perspectives]
    translated_perspective_ids = [
        item.get("id") for item in translated_perspectives if isinstance(item, dict)
    ]
    if translated_perspective_ids != expected_perspective_ids:
        errors.append(
            "Japanese translation must preserve perspective IDs and order"
        )
    for item in translated_perspectives:
        if not isinstance(item, dict):
            errors.append("Every translated perspective must be an object")
            continue
        for field in ("label", "description"):
            value = item.get(field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or not _predominantly_japanese(value, cfg)
            ):
                errors.append(
                    f"Translated perspective {field} must be predominantly Japanese"
                )

    expected_sections = english_body["sections"]
    translated_sections = raw.get("sections")
    if not isinstance(translated_sections, list):
        errors.append("Japanese translation sections must be an array")
        translated_sections = []
    expected_section_ids = [item["id"] for item in expected_sections]
    translated_section_ids = [
        item.get("id") for item in translated_sections if isinstance(item, dict)
    ]
    if translated_section_ids != expected_section_ids:
        errors.append("Japanese translation must preserve section IDs and order")
    for item in translated_sections:
        if not isinstance(item, dict):
            errors.append("Every translated section heading must be an object")
            continue
        heading = item.get("heading")
        if (
            not isinstance(heading, str)
            or not heading.strip()
            or not _predominantly_japanese(heading, cfg)
        ):
            errors.append(
                "Translated section heading must be predominantly Japanese"
            )
    if errors:
        raise FeatureValidationError(errors)
    return {
        "title": raw["title"].strip(),
        "dek": raw["dek"].strip(),
        "perspectives": translated_perspectives,
        "sections": translated_sections,
    }


def _translation_block_targets(
    english_body: dict, cfg: Mapping[str, Any]
) -> dict[str, int]:
    blocks = [
        block
        for section in english_body["sections"]
        for block in section["blocks"]
    ]
    weights = {
        block["id"]: max(1, len(ENGLISH_WORD_RE.findall(block["text"])))
        for block in blocks
    }
    total_weight = sum(weights.values())
    target_total = (cfg["target_min_chars"] + cfg["target_max_chars"]) // 2
    targets = {
        block_id: max(100, round(target_total * weight / total_weight))
        for block_id, weight in weights.items()
    }
    difference = target_total - sum(targets.values())
    if targets:
        last_id = blocks[-1]["id"]
        targets[last_id] = max(100, targets[last_id] + difference)
    return targets


def _translate_japanese_blocks(
    model: Any,
    english_body: dict,
    cfg: Mapping[str, Any],
    validation_feedback: list[str] | None,
) -> dict[str, str]:
    instructions = render_prompt(
        PROMPTS["translate_ja_blocks"],
        validation_min_chars=str(cfg["validation_min_chars"]),
        validation_max_chars=str(cfg["validation_max_chars"]),
    )
    targets = _translation_block_targets(english_body, cfg)
    blocks = [
        {
            "id": block["id"],
            "sectionId": section["id"],
            "sectionHeading": section["heading"],
            "text": block["text"],
            "targetCharacters": targets[block["id"]],
        }
        for section in english_body["sections"]
        for block in section["blocks"]
    ]
    batch_max = max(1, int(cfg.get("translation_block_batch_max", 3)))
    translated: dict[str, str] = {}
    for start in range(0, len(blocks), batch_max):
        batch = blocks[start : start + batch_max]
        payload: dict[str, Any] = {"blocks": batch}
        if validation_feedback is not None:
            payload["validationFeedback"] = validation_feedback
        raw = model.complete(
            instructions,
            payload,
            cfg["translation_max_tokens"],
            "feature translation",
        )
        items = raw.get("blockTranslations") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise FeatureValidationError(
                ["Japanese block translation must return blockTranslations"]
            )
        expected_ids = [block["id"] for block in batch]
        returned_ids = [item.get("id") for item in items if isinstance(item, dict)]
        errors: list[str] = []
        if returned_ids != expected_ids:
            errors.append("Japanese block translation must preserve IDs and order")
        for item in items:
            if not isinstance(item, dict):
                errors.append("Every Japanese block translation must be an object")
                continue
            block_id = item.get("id")
            text = item.get("text")
            if (
                block_id not in expected_ids
                or not isinstance(text, str)
                or not text.strip()
                or _japanese_ratio(text) < cfg["japanese_body_min_ratio"]
            ):
                errors.append(
                    f"Japanese block translation {block_id!r} is invalid"
                )
                continue
            translated[block_id] = text.strip()
        if errors:
            raise FeatureValidationError(errors)
    if set(translated) != {block["id"] for block in blocks}:
        raise FeatureValidationError(
            ["Japanese translation must include every English block"]
        )
    return translated


def _merge_bilingual_body(
    english_body: dict,
    metadata: dict,
    block_texts: Mapping[str, str],
    translation_revision_count: int,
) -> dict:
    metadata_perspectives = {
        item["id"]: item for item in metadata["perspectives"]
    }
    metadata_sections = {item["id"]: item for item in metadata["sections"]}
    perspectives = [
        {
            "id": item["id"],
            "label": metadata_perspectives[item["id"]]["label"],
            "description": metadata_perspectives[item["id"]]["description"],
            "labelEn": item["label"],
            "descriptionEn": item["description"],
            "sourceIds": list(item["sourceIds"]),
        }
        for item in english_body["perspectives"]
    ]
    sections = []
    for section in english_body["sections"]:
        sections.append(
            {
                "id": section["id"],
                "heading": metadata_sections[section["id"]]["heading"],
                "headingEn": section["heading"],
                "blocks": [
                    {
                        "id": block["id"],
                        "text": block_texts[block["id"]],
                        "textEn": block["text"],
                        "sourceIds": list(block["sourceIds"]),
                    }
                    for block in section["blocks"]
                ],
            }
        )
    return {
        "title": metadata["title"],
        "titleEn": english_body["title"],
        "dek": metadata["dek"],
        "dekEn": english_body["dek"],
        "summaryEn": english_body["summary"],
        "keyPointsEn": list(english_body["keyPoints"]),
        "perspectives": perspectives,
        "sections": sections,
        "translation": {
            "targetLanguage": "ja",
            "status": "pending",
            "revisionCount": translation_revision_count,
        },
    }


def _translation_verification_payload(
    english_body: dict, bilingual_body: dict
) -> dict:
    return {
        "canonicalEnglish": {
            "title": english_body["title"],
            "dek": english_body["dek"],
            "perspectives": english_body["perspectives"],
            "sections": english_body["sections"],
        },
        "japaneseTranslation": {
            "title": bilingual_body["title"],
            "dek": bilingual_body["dek"],
            "perspectives": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "description": item["description"],
                }
                for item in bilingual_body["perspectives"]
            ],
            "sections": [
                {
                    "id": section["id"],
                    "heading": section["heading"],
                    "blocks": [
                        {"id": block["id"], "text": block["text"]}
                        for block in section["blocks"]
                    ],
                }
                for section in bilingual_body["sections"]
            ],
        },
    }


def translate_english_body(
    model: Any,
    english_body: dict,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> tuple[dict, int]:
    """Translate a verified English edition and require bilingual fidelity."""
    metadata_instructions = render_prompt(PROMPTS["translate_ja_metadata"])
    verification_instructions = render_prompt(PROMPTS["verify_translation"])
    retry_max = max(1, int(cfg.get("translation_retry_max", 1)))
    feedback: list[str] | None = None
    for attempt in range(retry_max):
        metadata_payload: dict[str, Any] = {
            "title": english_body["title"],
            "dek": english_body["dek"],
            "perspectives": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "description": item["description"],
                }
                for item in english_body["perspectives"]
            ],
            "sections": [
                {"id": section["id"], "heading": section["heading"]}
                for section in english_body["sections"]
            ],
        }
        if feedback is not None:
            metadata_payload["validationFeedback"] = feedback
        try:
            raw_metadata = model.complete(
                metadata_instructions,
                metadata_payload,
                cfg["translation_max_tokens"],
                "feature translation",
            )
            metadata = _validate_translation_metadata(
                raw_metadata, english_body, cfg
            )
            block_texts = _translate_japanese_blocks(
                model, english_body, cfg, feedback
            )
            bilingual_body = _merge_bilingual_body(
                english_body, metadata, block_texts, attempt
            )
            japanese_chars = article_character_count(bilingual_body)
            if not (
                cfg["validation_min_chars"]
                <= japanese_chars
                <= cfg["validation_max_chars"]
            ):
                raise FeatureValidationError(
                    [
                        f"Japanese translation has {japanese_chars} characters; "
                        f"required range is {cfg['validation_min_chars']}-"
                        f"{cfg['validation_max_chars']}"
                    ]
                )
            verdict = model.complete(
                verification_instructions,
                _translation_verification_payload(english_body, bilingual_body),
                cfg["translation_verification_max_tokens"],
                "translation verification",
            )
            verdict = validate_verdict(verdict, bilingual_body)
            if verdict["status"] == "pass":
                bilingual_body["translation"]["status"] = "passed"
                return bilingual_body, attempt
            feedback = [
                f"{issue['blockId']}: {issue['reason']}"
                for issue in verdict["issues"]
            ]
        except FeatureValidationError as exc:
            feedback = list(exc.errors)
        if attempt == retry_max - 1:
            raise FeatureValidationError(
                [
                    "English-to-Japanese translation failed after "
                    f"{retry_max} attempt(s): " + "; ".join(feedback or [])
                ]
            )
        print(
            "  [warn] AI English-to-Japanese translation requested a revision "
            f"({attempt + 1}/{retry_max}, errors={len(feedback or [])})"
        )
    raise FeatureError("AI English-to-Japanese translation loop exited unexpectedly")


def revise_body(
    model: Any,
    feature: dict,
    plan: dict,
    sources: list[dict],
    issues: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    instructions = render_prompt(
        PROMPTS["revise"],
        section_min=str(cfg["section_min"]),
        required_sections_json=cfg[f"{feature['type']}_sections"],
        target_min_chars=str(cfg["target_min_chars"]),
        target_max_chars=str(cfg["target_max_chars"]),
        english_summary_min_words=str(cfg["english_summary_min_words"]),
        english_summary_max_words=str(cfg["english_summary_max_words"]),
    )
    return model.complete(
        instructions,
        {
            "featurePlan": plan,
            "primarySources": grounding_source_payload(sources),
            "currentDraft": _body_from_feature(feature),
            "issues": issues,
        },
        cfg["revision_max_tokens"],
        "single revision",
    )


def _only_short_body_expansion_errors(errors: list[str]) -> bool:
    prefixes = (
        "Every source must be cited; unused: ",
        "Japanese body has ",
        "Estimated reading time must be ",
    )
    return bool(errors) and all(error.startswith(prefixes) for error in errors)


def _non_whitespace_character_count(value: str) -> int:
    return len("".join(value.split()))


def _trim_text_to_character_limit(value: str, limit: int) -> str:
    """Return a bounded prefix, preferring a nearby complete sentence."""
    if _non_whitespace_character_count(value) <= limit:
        return value.strip()
    seen = 0
    end = 0
    for index, character in enumerate(value):
        if not character.isspace():
            seen += 1
        if seen >= limit:
            end = index + 1
            break
    prefix = value[:end].rstrip()
    sentence_end = max(prefix.rfind(mark) for mark in "。！？!?")
    if sentence_end >= 0:
        sentence_prefix = prefix[: sentence_end + 1].rstrip()
        if _non_whitespace_character_count(sentence_prefix) >= int(limit * 0.85):
            return sentence_prefix
    if prefix and prefix[-1] not in "。！？!?":
        while prefix and _non_whitespace_character_count(prefix) >= limit:
            prefix = prefix[:-1].rstrip()
        prefix += "。"
    return prefix


def _trim_additions_to_character_limit(
    additions: Mapping[str, str], limit: int
) -> dict[str, str]:
    """Trim additions proportionally while retaining prose for every block."""
    counts = {
        block_id: _non_whitespace_character_count(text)
        for block_id, text in additions.items()
    }
    total = sum(counts.values())
    if total <= limit:
        return dict(additions)

    result: dict[str, str] = {}
    remaining = limit
    items = list(additions.items())
    for index, (block_id, text) in enumerate(items):
        remaining_blocks = len(items) - index - 1
        if not remaining_blocks:
            quota = remaining
        else:
            proportional = round(limit * counts[block_id] / total)
            quota = max(1, min(proportional, remaining - remaining_blocks))
        trimmed = _trim_text_to_character_limit(text, quota)
        result[block_id] = trimmed
        remaining -= _non_whitespace_character_count(trimmed)
    return result


def expand_short_body(
    model: Any,
    feature: dict,
    sources: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    """Append grounded prose without spending the response budget on full regeneration."""
    blocks: list[dict] = []
    for section in feature.get("sections", []):
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks", []):
            if not isinstance(block, dict):
                continue
            blocks.append(
                {
                    "id": block.get("id"),
                    "text": block.get("text"),
                    "sourceIds": block.get("sourceIds"),
                }
            )
    expected_ids = [block["id"] for block in blocks]
    if not expected_ids or not _unique_strings(expected_ids):
        raise FeatureValidationError(
            ["Short-body expansion requires unique existing block IDs"]
        )
    known_source_ids = {
        source.get("sourceId")
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("sourceId"), str)
    }
    existing_source_ids = {block["id"]: block["sourceIds"] for block in blocks}
    if not known_source_ids or not all(
        _valid_source_ids(source_ids, known_source_ids)
        for source_ids in existing_source_ids.values()
    ):
        raise FeatureValidationError(
            ["Short-body expansion requires valid existing source IDs"]
        )
    cited_source_ids = {
        source_id
        for source_ids in existing_source_ids.values()
        for source_id in source_ids
    }
    required_source_ids = sorted(known_source_ids - cited_source_ids)

    current_chars = article_character_count(feature)
    absolute_max = min(cfg["target_max_chars"], cfg["validation_max_chars"])
    expanded_min = min(absolute_max, cfg["validation_min_chars"])
    expanded_max = absolute_max
    addition_min = max(1, expanded_min - current_chars)
    addition_max = max(addition_min, expanded_max - current_chars)
    instructions = render_prompt(
        PROMPTS["expand"],
        addition_min_chars=str(addition_min),
        addition_max_chars=str(addition_max),
        block_count=str(len(blocks)),
        required_source_ids_json=required_source_ids,
    )
    retry_max = max(1, int(cfg.get("short_body_expansion_retry_max", 1)))
    validation_feedback: list[str] | None = None
    addition_by_id: dict[str, str] | None = None
    source_ids_by_id: dict[str, list[str]] | None = None
    for attempt in range(retry_max):
        payload: dict[str, Any] = {
            "primarySources": grounding_source_payload(sources),
            "blocks": blocks,
        }
        if validation_feedback is not None:
            payload["validationFeedback"] = {
                "errors": validation_feedback,
                "remainingAttempts": retry_max - attempt,
            }
        raw = model.complete(
            instructions,
            payload,
            cfg["revision_max_tokens"],
            "short body expansion",
        )
        errors: list[str] = []
        additions = raw.get("blockAdditions") if isinstance(raw, dict) else None
        if not isinstance(additions, list):
            errors.append("Short-body expansion must return a blockAdditions array")
            additions = []

        candidate_additions: dict[str, str] = {}
        candidate_source_ids: dict[str, list[str]] = {}
        for addition in additions:
            if not isinstance(addition, dict):
                errors.append("Every short-body addition must be an object")
                continue
            block_id = addition.get("id")
            text = addition.get("text")
            source_ids = addition.get("sourceIds")
            if (
                not isinstance(block_id, str)
                or block_id not in expected_ids
                or block_id in candidate_additions
                or not isinstance(text, str)
                or not text.strip()
                or not JAPANESE_CHAR_RE.search(text)
                or not _valid_source_ids(source_ids, known_source_ids)
                or not set(existing_source_ids.get(block_id, []))
                <= set(source_ids if isinstance(source_ids, list) else [])
            ):
                errors.append("Short-body expansion returned an invalid block addition")
                continue
            candidate_additions[block_id] = text.strip()
            candidate_source_ids[block_id] = source_ids
        if set(candidate_additions) != set(expected_ids):
            errors.append(
                "Short-body expansion must add prose to every existing block"
            )
        cited_after_expansion = {
            source_id
            for source_ids in candidate_source_ids.values()
            for source_id in source_ids
        }
        missing_required = sorted(set(required_source_ids) - cited_after_expansion)
        if missing_required:
            errors.append(
                "Short-body expansion must cite required source IDs: "
                + ", ".join(missing_required)
            )
        addition_text = "".join(candidate_additions.values())
        addition_chars = _non_whitespace_character_count(addition_text)
        if not errors and addition_chars > addition_max:
            print(
                "  [warn] AI short body expansion exceeded the local character "
                f"limit ({addition_chars}>{addition_max}); trimming complete "
                "additions proportionally"
            )
            candidate_additions = _trim_additions_to_character_limit(
                candidate_additions, addition_max
            )
            addition_text = "".join(candidate_additions.values())
            addition_chars = _non_whitespace_character_count(addition_text)
        if not addition_min <= addition_chars <= addition_max:
            errors.append(
                f"Short-body additions have {addition_chars} characters; required "
                f"range is {addition_min}-{addition_max}"
            )
        if _japanese_ratio(addition_text) < cfg["japanese_body_min_ratio"]:
            errors.append("Short-body additions must be predominantly Japanese")
        if not errors:
            addition_by_id = candidate_additions
            source_ids_by_id = candidate_source_ids
            break
        if attempt == retry_max - 1:
            raise FeatureValidationError(errors)
        validation_feedback = errors
        print(
            "  [warn] AI short body expansion failed local validation "
            f"(attempt {attempt + 1}/{retry_max}, errors={len(errors)}); "
            "requesting corrected additions"
        )
    if addition_by_id is None or source_ids_by_id is None:
        raise FeatureError("AI short body expansion validation loop exited unexpectedly")

    body = dict(_body_from_feature(feature))
    expanded_sections: list[dict] = []
    for section in feature["sections"]:
        expanded_section = dict(section)
        expanded_blocks: list[dict] = []
        for block in section["blocks"]:
            expanded_block = dict(block)
            expanded_block["text"] = (
                f"{block['text'].rstrip()}\n\n{addition_by_id[block['id']]}"
            )
            expanded_block["sourceIds"] = source_ids_by_id[block["id"]]
            expanded_blocks.append(expanded_block)
        expanded_section["blocks"] = expanded_blocks
        expanded_sections.append(expanded_section)
    body["sections"] = expanded_sections
    return body


def _revise_grounding_block_batch(
    model: Any,
    feature: dict,
    plan: dict,
    sources: list[dict],
    issues: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
    *,
    language: str = "ja",
    article_type: str | None = None,
) -> dict:
    """Apply compact, verifier-directed block replacements to a complete draft."""
    if language not in ("ja", "en"):
        raise ValueError("language must be ja or en")
    blocks: list[dict] = []
    for section in feature.get("sections", []):
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks", []):
            if not isinstance(block, dict):
                continue
            blocks.append(
                {
                    "id": block.get("id"),
                    "sectionId": section.get("id"),
                    "sectionHeading": section.get("heading"),
                    "text": block.get("text"),
                    "sourceIds": block.get("sourceIds"),
                }
            )
    expected_ids = [block["id"] for block in blocks]
    if not expected_ids or not _unique_strings(expected_ids):
        raise FeatureValidationError(
            ["Grounding patch requires unique existing block IDs"]
        )
    block_by_id = {block["id"]: block for block in blocks}
    known_source_ids = {
        source.get("sourceId")
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("sourceId"), str)
    }
    if not known_source_ids or not all(
        _valid_source_ids(block.get("sourceIds"), known_source_ids)
        for block in blocks
    ):
        raise FeatureValidationError(
            ["Grounding patch requires valid existing source IDs"]
        )

    required_block_ids = {
        issue.get("blockId")
        for issue in issues
        if isinstance(issue, dict) and issue.get("blockId") != "_article"
    }
    if not required_block_ids <= set(expected_ids):
        raise FeatureValidationError(
            ["Grounding patch issues reference an unknown block ID"]
        )
    requested_blocks = (
        [block for block in blocks if block["id"] in required_block_ids]
        if required_block_ids
        else blocks
    )
    required_block_order = [
        block["id"] for block in blocks if block["id"] in required_block_ids
    ]
    block_max = max(1, int(cfg.get("grounding_patch_block_max", 3)))
    if language == "ja":
        instructions = render_prompt(
            PROMPTS["grounding_patch"],
            patch_block_max=str(block_max),
            validation_min_chars=str(cfg["validation_min_chars"]),
            validation_max_chars=str(cfg["validation_max_chars"]),
        )
    else:
        instructions = render_prompt(
            PROMPTS["grounding_patch_en"],
            patch_block_max=str(block_max),
            english_body_validation_min_words=str(
                cfg["english_body_validation_min_words"]
            ),
            english_body_validation_max_words=str(
                cfg["english_body_validation_max_words"]
            ),
        )
    retry_max = max(1, int(cfg.get("grounding_patch_retry_max", 1)))
    validation_feedback: list[str] | None = None
    for attempt in range(retry_max):
        payload: dict[str, Any] = {
            "featurePlan": plan,
            "primarySources": grounding_source_payload(sources),
            "blocks": requested_blocks,
            "issues": issues,
            "requiredBlockIds": required_block_order,
        }
        if validation_feedback is not None:
            payload["validationFeedback"] = {
                "errors": validation_feedback,
                "remainingAttempts": retry_max - attempt,
            }
        raw = model.complete(
            instructions,
            payload,
            cfg["grounding_patch_max_tokens"],
            "grounding patch",
        )
        errors: list[str] = []
        replacements = raw.get("blockReplacements") if isinstance(raw, dict) else None
        if not isinstance(replacements, list):
            errors.append("Grounding patch must return a blockReplacements array")
            replacements = []
        if not 1 <= len(replacements) <= block_max:
            errors.append(
                f"Grounding patch must replace between 1 and {block_max} blocks"
            )

        replacement_by_id: dict[str, dict] = {}
        for replacement in replacements:
            if not isinstance(replacement, dict):
                errors.append("Every grounding replacement must be an object")
                continue
            block_id = replacement.get("id")
            text = replacement.get("text")
            source_ids = replacement.get("sourceIds")
            if (
                not isinstance(block_id, str)
                or block_id not in block_by_id
                or block_id in replacement_by_id
                or not isinstance(text, str)
                or not text.strip()
                or (
                    language == "ja"
                    and _japanese_ratio(text) < cfg["japanese_body_min_ratio"]
                )
                or (language == "en" and not _predominantly_english(text))
                or not _valid_source_ids(source_ids, known_source_ids)
            ):
                errors.append("Grounding patch returned an invalid block replacement")
                continue
            current = block_by_id[block_id]
            if text.strip() == current["text"].strip() and source_ids == current["sourceIds"]:
                errors.append("Grounding patch must change every replacement block")
                continue
            replacement_by_id[block_id] = {
                "text": text.strip(),
                "sourceIds": source_ids,
            }
        if not required_block_ids <= set(replacement_by_id):
            errors.append(
                "Grounding patch must replace every specifically affected block"
            )

        body = (
            dict(_body_from_feature(feature))
            if language == "ja"
            else dict(feature)
        )
        patched_sections: list[dict] = []
        for section in feature["sections"]:
            patched_section = dict(section)
            patched_blocks: list[dict] = []
            for block in section["blocks"]:
                patched_block = dict(block)
                replacement = replacement_by_id.get(block["id"])
                if replacement is not None:
                    patched_block.update(replacement)
                patched_blocks.append(patched_block)
            patched_section["blocks"] = patched_blocks
            patched_sections.append(patched_section)
        body["sections"] = patched_sections

        if not errors:
            patched_feature = dict(feature)
            patched_feature.update(body)
            try:
                if language == "ja":
                    patched_feature["readTimeMinutes"] = max(
                        1,
                        math.ceil(
                            article_character_count(patched_feature)
                            / cfg["reading_chars_per_minute"]
                        ),
                    )
                    validate_feature(patched_feature, cfg)
                else:
                    validate_english_body(
                        patched_feature,
                        sources,
                        article_type or str(plan.get("articleType", "")),
                        cfg,
                    )
            except FeatureValidationError as exc:
                errors.extend(
                    f"Patched feature failed local validation: {error}"
                    for error in exc.errors
                )
        if not errors:
            return body
        if attempt == retry_max - 1:
            raise FeatureValidationError(errors)
        validation_feedback = errors
        print(
            "  [warn] AI grounding patch failed local validation "
            f"(attempt {attempt + 1}/{retry_max}, errors={len(errors)}); "
            "requesting corrected replacements"
        )
    raise FeatureError("AI grounding patch validation loop exited unexpectedly")


def revise_grounding_blocks(
    model: Any,
    feature: dict,
    plan: dict,
    sources: list[dict],
    issues: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
    *,
    language: str = "ja",
    article_type: str | None = None,
) -> dict:
    """Apply all verifier issues in output-bounded batches of block patches."""
    block_max = max(1, int(cfg.get("grounding_patch_block_max", 3)))
    issue_groups: dict[str, list[dict]] = {}
    block_order: list[str] = []
    article_issues: list[dict] = []
    for issue in issues:
        block_id = issue.get("blockId") if isinstance(issue, dict) else None
        if block_id == "_article":
            article_issues.append(issue)
            continue
        if block_id not in issue_groups:
            issue_groups[block_id] = []
            block_order.append(block_id)
        issue_groups[block_id].append(issue)

    batches: list[list[dict]] = []
    for start in range(0, len(block_order), block_max):
        batch: list[dict] = []
        for block_id in block_order[start : start + block_max]:
            batch.extend(issue_groups[block_id])
        batches.append(batch)
    if article_issues:
        batches.append(article_issues)
    if not batches:
        raise FeatureValidationError(
            ["Grounding patch requires at least one verifier issue"]
        )
    if len(batches) > 1:
        print(
            "  [warn] AI grounding patch split "
            f"{len(block_order)} affected blocks into {len(batches)} bounded batches"
        )

    patched_feature = dict(feature)
    for batch in batches:
        body = _revise_grounding_block_batch(
            model,
            patched_feature,
            plan,
            sources,
            batch,
            cfg,
            language=language,
            article_type=article_type,
        )
        patched_feature.update(body)
        if language == "ja":
            patched_feature["readTimeMinutes"] = max(
                1,
                math.ceil(
                    article_character_count(patched_feature)
                    / cfg["reading_chars_per_minute"]
                ),
            )
    return (
        _body_from_feature(patched_feature)
        if language == "ja"
        else patched_feature
    )


def verify_feature(
    model: Any,
    feature: dict,
    plan: dict,
    sources: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    instructions = render_prompt(PROMPTS["verify"])
    verdict = model.complete(
        instructions,
        {
            "featurePlan": plan,
            "primarySources": grounding_source_payload(sources),
            "draft": _body_from_feature(feature),
        },
        cfg["verification_max_tokens"],
        "grounding verification",
    )
    return validate_verdict(verdict, feature)


def _atomic_json_temp(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def feature_index_entry(feature: dict) -> dict:
    return {
        "slug": feature["slug"],
        "type": feature["type"],
        "date": feature["date"],
        "readTimeMinutes": feature["readTimeMinutes"],
        "readTimeMinutesEn": feature["readTimeMinutesEn"],
        "sourceCount": len(feature["sources"]),
        "title": feature["title"],
        "titleEn": feature["titleEn"],
        "dek": feature["dek"],
        "dekEn": feature["dekEn"],
        "file": f"{feature['slug']}.json",
        "topicKey": feature["topicKey"],
        "searchTerms": feature["searchTerms"],
        "sourcePaperIds": [source["arxivId"] for source in feature["sources"]],
        "generatedAt": feature["generatedAt"],
    }


def publish_feature(
    feature: dict, output_dir: Path, *, replace_existing: bool = False
) -> tuple[Path, Path]:
    """Publish one feature per date/type slot without exposing a broken index."""
    validate_feature(feature)
    if feature.get("verification", {}).get("status") != "passed":
        raise FeatureValidationError(
            ["Feature must pass AI verification before publication"]
        )
    article_path = output_dir / f"{feature['slug']}.json"
    index_path = output_dir / "index.json"
    index = load_feature_index(output_dir)
    slot_entries = [
        entry
        for entry in index["features"]
        if entry.get("date") == feature["date"]
        and entry.get("type") == feature["type"]
    ]
    slug_entries = [
        entry
        for entry in index["features"]
        if entry.get("slug") == feature["slug"]
    ]
    replaced_entries = list(
        {id(entry): entry for entry in [*slot_entries, *slug_entries]}.values()
    )

    if not replace_existing and article_path.exists():
        raise FeatureError(f"Refusing to overwrite existing feature: {article_path}")
    if not replace_existing and slug_entries:
        raise FeatureError(f"Feature slug is already indexed: {feature['slug']}")
    if not replace_existing and slot_entries:
        raise FeatureError(
            f"Feature slot is already indexed: {feature['date']} {feature['type']}"
        )

    replaced_paths: set[Path] = set()
    for entry in replaced_entries:
        entry_slug = entry.get("slug")
        if not isinstance(entry_slug, str) or not TOPIC_KEY_RE.fullmatch(
            entry_slug
        ):
            raise FeatureError("Cannot replace a feature with an invalid slug")
        expected_file = f"{entry_slug}.json"
        relative_file = entry.get("file", expected_file)
        if relative_file != expected_file:
            raise FeatureError("Cannot replace a feature with an invalid file path")
        indexed_path = output_dir / relative_file
        if indexed_path.is_symlink():
            raise FeatureError("Cannot replace a feature through a symbolic link")
        replaced_path = indexed_path.resolve()
        if not _inside(output_dir, replaced_path):
            raise FeatureError("Cannot replace a feature with an unsafe file path")
        if not replaced_path.is_file():
            raise FeatureError(
                "Cannot replace an inconsistent feature: indexed article is missing"
            )
        replaced_paths.add(replaced_path)

    resolved_article_path = article_path.resolve()
    if (
        replace_existing
        and article_path.exists()
        and resolved_article_path not in replaced_paths
    ):
        raise FeatureError(
            "Cannot replace an inconsistent feature: article and index disagree"
        )
    if replace_existing:
        index["features"] = [
            entry
            for entry in index["features"]
            if not (
                entry.get("slug") == feature["slug"]
                or (
                    entry.get("date") == feature["date"]
                    and entry.get("type") == feature["type"]
                )
            )
        ]
    index["features"].append(feature_index_entry(feature))
    index["features"].sort(
        key=lambda entry: (entry.get("date", ""), entry.get("slug", "")), reverse=True
    )
    index["generatedAt"] = feature["generatedAt"]

    article_temp = _atomic_json_temp(article_path, feature)
    index_temp = _atomic_json_temp(index_path, index)
    try:
        os.replace(article_temp, article_path)
        os.replace(index_temp, index_path)
    finally:
        article_temp.unlink(missing_ok=True)
        index_temp.unlink(missing_ok=True)
    for replaced_path in replaced_paths:
        if replaced_path != resolved_article_path:
            replaced_path.unlink(missing_ok=True)
    return article_path, index_path


def run_feature_pipeline(
    *,
    as_of: date,
    article_type: str = "auto",
    dry_run: bool = False,
    replace_existing: bool = False,
    data_root: Path = ROOT / "data",
    output_dir: Path | None = None,
    model: Any | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: datetime | None = None,
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict | None:
    resolved_type = feature_slot(as_of) if article_type == "auto" else article_type
    if resolved_type == "none":
        return None
    if resolved_type not in ("primer", "debate"):
        raise ValueError("article_type must be auto, primer, or debate")

    output_dir = output_dir or ROOT / cfg["output_dir"]
    feature_index = load_feature_index(output_dir)
    if not dry_run and not replace_existing:
        existing = load_existing_feature_for_slot(
            output_dir, feature_index, as_of, resolved_type
        )
        if existing is not None:
            return existing
    topic_history = feature_index
    if replace_existing:
        topic_history = {
            **feature_index,
            "features": [
                entry
                for entry in feature_index["features"]
                if not (
                    entry.get("date") == as_of.isoformat()
                    and entry.get("type") == resolved_type
                )
            ],
        }
    candidates = load_recent_weekly_papers(data_root, as_of, cfg["recent_days"])
    if len(candidates) < cfg["archive_source_min"]:
        raise FeatureValidationError(
            [
                f"Need at least {cfg['archive_source_min']} recent archive papers; "
                f"found {len(candidates)}"
            ]
        )
    model = model or JsonModel(SETTINGS)
    plan = choose_topic(model, resolved_type, candidates, topic_history, cfg)
    excluded_ids = {canonical_arxiv_id(paper.get("id")) for paper in candidates}
    required_external = max(
        cfg["external_source_min"],
        cfg["source_target"] - len(plan["archivePaperIds"]),
    )
    retrieval_error: FeatureError | None = None
    try:
        external = fetch_additional_arxiv_sources(
            plan["searchTerms"],
            excluded_ids,
            cfg=cfg,
            opener=opener,
        )
    except FeatureError as exc:
        retrieval_error = exc
        external = []
    if len(external) < required_external:
        fallback = select_archived_fallback_sources(
            plan["searchTerms"],
            candidates,
            set(plan["archivePaperIds"])
            | {canonical_arxiv_id(source.get("arxivId")) for source in external},
            limit=cfg["external_candidate_limit"],
        )
        missing = required_external - len(external)
        external.extend(fallback[:missing])
        if fallback:
            reason = (
                "live API failed"
                if retrieval_error is not None
                else "live API returned too few results"
            )
            print(
                "  [warn] arXiv primary-source retrieval fallback "
                f"used {min(len(fallback), missing)} archived source(s) "
                f"because the {reason}"
            )
    if retrieval_error is not None and len(external) < required_external:
        raise FeatureError(
            f"{retrieval_error}; archived fallback supplied only "
            f"{len(external)} of {required_external} required sources"
        ) from retrieval_error
    sources = build_source_packet(plan, candidates, external, cfg)
    generated_at = now or datetime.now(timezone.utc)

    english_body = generate_english_body(model, plan, sources, resolved_type, cfg)
    verifier_revision_max = max(0, int(cfg.get("verification_revision_max", 1)))
    verifier_revision_count = 0
    verdict = verify_english_body(model, english_body, plan, sources, cfg)
    while (
        verdict["status"] == "revise"
        and verifier_revision_count < verifier_revision_max
    ):
        print(
            "  [warn] AI grounding verification requested a revision "
            f"({verifier_revision_count + 1}/{verifier_revision_max}); "
            f"issues={len(verdict['issues'])}; "
            "blocks="
            + ",".join(
                dict.fromkeys(issue["blockId"] for issue in verdict["issues"])
            )
        )
        english_body = revise_grounding_blocks(
            model,
            english_body,
            plan,
            sources,
            verdict["issues"],
            cfg,
            language="en",
            article_type=resolved_type,
        )
        verifier_revision_count += 1
        validate_english_body(english_body, sources, resolved_type, cfg)
        verdict = verify_english_body(model, english_body, plan, sources, cfg)
    if verdict["status"] != "pass":
        raise FeatureValidationError(
            [
                "Feature failed grounding verification after "
                f"{verifier_revision_count} verifier revision(s); remaining: "
                f"{_verifier_issue_summary(verdict['issues'])}"
            ]
        )

    body, translation_revision_count = translate_english_body(
        model, english_body, cfg
    )
    body["translation"]["verifiedAt"] = generated_at.astimezone(
        timezone.utc
    ).isoformat()
    feature = assemble_feature(
        body,
        plan=plan,
        sources=sources,
        article_type=resolved_type,
        as_of=as_of,
        generated_at=generated_at,
        cfg=cfg,
    )
    feature["verification"] = {
        "status": "passed",
        "revisionCount": verifier_revision_count,
        "verifiedAt": generated_at.astimezone(timezone.utc).isoformat(),
        "canonicalLanguage": "en",
        "translationRevisionCount": translation_revision_count,
    }
    validate_feature(feature, cfg)
    if not dry_run:
        publish_feature(feature, output_dir, replace_existing=replace_existing)
    return feature


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date", default=None, help="Reference date YYYY-MM-DD (default: today UTC)"
    )
    parser.add_argument(
        "--article-type",
        choices=("auto", "primer", "debate"),
        default="auto",
        help="Use the scheduled type by default, or force a type for a manual run",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate and validate without writing"
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Atomically replace an existing feature in the same scheduled slot",
    )
    parser.add_argument(
        "--print-slot",
        action="store_true",
        help="Print primer, debate, or none for the date and exit",
    )
    args = parser.parse_args(argv)
    try:
        as_of = (
            date.fromisoformat(args.date)
            if args.date
            else datetime.now(timezone.utc).date()
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.print_slot:
        print(feature_slot(as_of))
        return 0

    feature = run_feature_pipeline(
        as_of=as_of,
        article_type=args.article_type,
        dry_run=args.dry_run,
        replace_existing=args.replace_existing,
    )
    if feature is None:
        print(
            f"[feature] {as_of.isoformat()} is not a scheduled feature slot; nothing to do"
        )
        return 0
    mode = "validated (dry-run)" if args.dry_run else "published"
    print(f"[feature] {mode}: {feature['slug']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
