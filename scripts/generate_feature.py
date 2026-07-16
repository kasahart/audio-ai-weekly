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
        "verify",
        "revise",
        "expand",
        "grounding_patch",
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
    retry_max = max(1, int(cfg.get("retry_max", 3)))
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
            sleep(float(cfg["retry_interval"]) * (2**attempt))
    raise FeatureError("arXiv primary-source retry loop exited unexpectedly")


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


def validate_feature(feature: Any, cfg: Mapping[str, Any] = FEATURE_SETTINGS) -> None:
    """Apply deterministic publication gates to a complete feature payload."""
    errors: list[str] = []
    if not isinstance(feature, dict):
        raise FeatureValidationError(["Feature must be a JSON object"])

    if feature.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
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
        value = feature.get(field)
        if isinstance(value, str) and not JAPANESE_CHAR_RE.search(value):
            errors.append(f"{field} must contain Japanese text")
    for field in ("titleEn", "dekEn"):
        value = feature.get(field)
        if isinstance(value, str) and not LATIN_CHAR_RE.search(value):
            errors.append(f"{field} must contain English text")
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
                if isinstance(value, str) and not JAPANESE_CHAR_RE.search(value):
                    errors.append(f"Perspective {field} must contain Japanese text")
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
        elif not JAPANESE_CHAR_RE.search(section["heading"]):
            errors.append(f"Section {section.get('id')!r} heading must be Japanese")
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
            elif not JAPANESE_CHAR_RE.search(block["text"]):
                errors.append(f"Block {block.get('id')!r} must contain Japanese text")
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
    expanded_min = min(
        absolute_max,
        max(
            cfg["target_min_chars"] + 500,
            cfg["validation_min_chars"] + 500,
        ),
    )
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


def revise_grounding_blocks(
    model: Any,
    feature: dict,
    plan: dict,
    sources: list[dict],
    issues: list[dict],
    cfg: Mapping[str, Any] = FEATURE_SETTINGS,
) -> dict:
    """Apply compact, verifier-directed block replacements to a complete draft."""
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
    block_max = max(1, int(cfg.get("grounding_patch_block_max", 3)))
    if len(required_block_ids) > block_max:
        raise FeatureValidationError(
            [
                "Grounding patch has more specifically affected blocks than its "
                f"limit ({len(required_block_ids)}>{block_max})"
            ]
        )

    instructions = render_prompt(
        PROMPTS["grounding_patch"],
        patch_block_max=str(block_max),
        validation_min_chars=str(cfg["validation_min_chars"]),
        validation_max_chars=str(cfg["validation_max_chars"]),
    )
    retry_max = max(1, int(cfg.get("grounding_patch_retry_max", 1)))
    validation_feedback: list[str] | None = None
    for attempt in range(retry_max):
        payload: dict[str, Any] = {
            "featurePlan": plan,
            "primarySources": grounding_source_payload(sources),
            "blocks": blocks,
            "issues": issues,
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
                or _japanese_ratio(text) < cfg["japanese_body_min_ratio"]
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

        body = dict(_body_from_feature(feature))
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
            patched_feature["readTimeMinutes"] = max(
                1,
                math.ceil(
                    article_character_count(patched_feature)
                    / cfg["reading_chars_per_minute"]
                ),
            )
            try:
                validate_feature(patched_feature, cfg)
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


def publish_feature(feature: dict, output_dir: Path) -> tuple[Path, Path]:
    """Atomically publish the article first, then an index that references it."""
    validate_feature(feature)
    if feature.get("verification", {}).get("status") != "passed":
        raise FeatureValidationError(
            ["Feature must pass AI verification before publication"]
        )
    article_path = output_dir / f"{feature['slug']}.json"
    index_path = output_dir / "index.json"
    if article_path.exists():
        raise FeatureError(f"Refusing to overwrite existing feature: {article_path}")
    index = load_feature_index(output_dir)
    if any(entry.get("slug") == feature["slug"] for entry in index["features"]):
        raise FeatureError(f"Feature slug is already indexed: {feature['slug']}")
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
    return article_path, index_path


def run_feature_pipeline(
    *,
    as_of: date,
    article_type: str = "auto",
    dry_run: bool = False,
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
    if not dry_run:
        existing = load_existing_feature_for_slot(
            output_dir, feature_index, as_of, resolved_type
        )
        if existing is not None:
            return existing
    candidates = load_recent_weekly_papers(data_root, as_of, cfg["recent_days"])
    if len(candidates) < cfg["archive_source_min"]:
        raise FeatureValidationError(
            [
                f"Need at least {cfg['archive_source_min']} recent archive papers; "
                f"found {len(candidates)}"
            ]
        )
    model = model or JsonModel(SETTINGS)
    plan = choose_topic(model, resolved_type, candidates, feature_index, cfg)
    excluded_ids = {canonical_arxiv_id(paper.get("id")) for paper in candidates}
    external = fetch_additional_arxiv_sources(
        plan["searchTerms"],
        excluded_ids,
        cfg=cfg,
        opener=opener,
    )
    sources = build_source_packet(plan, candidates, external, cfg)
    generated_at = now or datetime.now(timezone.utc)

    body = generate_body(model, plan, sources, resolved_type, cfg)
    feature = assemble_feature(
        body,
        plan=plan,
        sources=sources,
        article_type=resolved_type,
        as_of=as_of,
        generated_at=generated_at,
        cfg=cfg,
    )
    revision_count = 0
    try:
        validate_feature(feature, cfg)
    except FeatureValidationError as exc:
        if (
            article_character_count(feature) < cfg["validation_min_chars"]
            and _only_short_body_expansion_errors(exc.errors)
        ):
            body = expand_short_body(model, feature, sources, cfg)
        else:
            body = revise_body(
                model, feature, plan, sources, _issue_payload(exc.errors), cfg
            )
        revision_count = 1
        feature = assemble_feature(
            body,
            plan=plan,
            sources=sources,
            article_type=resolved_type,
            as_of=as_of,
            generated_at=generated_at,
            cfg=cfg,
        )
        validate_feature(feature, cfg)

    verifier_revision_max = max(0, int(cfg.get("verification_revision_max", 1)))
    verifier_revision_count = 0
    verdict = verify_feature(model, feature, plan, sources, cfg)
    while (
        verdict["status"] == "revise"
        and verifier_revision_count < verifier_revision_max
    ):
        print(
            "  [warn] AI grounding verification requested a revision "
            f"({verifier_revision_count + 1}/{verifier_revision_max})"
        )
        body = revise_grounding_blocks(
            model, feature, plan, sources, verdict["issues"], cfg
        )
        revision_count += 1
        verifier_revision_count += 1
        feature = assemble_feature(
            body,
            plan=plan,
            sources=sources,
            article_type=resolved_type,
            as_of=as_of,
            generated_at=generated_at,
            cfg=cfg,
        )
        validate_feature(feature, cfg)
        verdict = verify_feature(model, feature, plan, sources, cfg)
    if verdict["status"] != "pass":
        raise FeatureValidationError(
            [
                "Feature failed grounding verification after "
                f"{verifier_revision_count} verifier revision(s)"
            ]
        )

    feature["verification"] = {
        "status": "passed",
        "revisionCount": revision_count,
        "verifiedAt": generated_at.astimezone(timezone.utc).isoformat(),
    }
    validate_feature(feature, cfg)
    if not dry_run:
        publish_feature(feature, output_dir)
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
