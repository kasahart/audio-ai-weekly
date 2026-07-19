#!/usr/bin/env python3
"""
analyze_papers.py
Analyze each paper in Japanese from six perspectives using the configured AI provider.
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import yaml
from openai import APIError, OpenAI

from model_utils import build_chat_kwargs, create_client, get_ai_config
from fetch_papers import fetch_arxiv_ids

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
ANALYSIS_SETTINGS = SETTINGS["analysis"]
PROMPT_DIR = ROOT / "config" / "prompts"
SYSTEM_PROMPT = (PROMPT_DIR / "analyze_system.txt").read_text().strip()
BATCH_PROMPT_TEMPLATE = (PROMPT_DIR / "analyze_batch.txt").read_text().strip()


def get_client(provider: str | None = None) -> OpenAI:
    return create_client(SETTINGS, provider=provider)


def sanitize_json_text(raw: str) -> str:
    return raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()


def wait_for_next_request(last_request_at: float | None, min_interval: float):
    if last_request_at is None:
        return
    elapsed = time.monotonic() - last_request_at
    remaining = min_interval - elapsed
    if remaining > 0:
        print(f"[analyze] waiting {remaining:.1f}s to respect model rate limit ...")
        time.sleep(remaining)


def build_batch_prompt(papers: list[dict]) -> str:
    paper_blocks = []
    for paper in papers:
        paper_blocks.append(
            f"""ID: {paper["id"]}
Title: {paper["title"]}
Authors: {", ".join(paper.get("authors", [])[: ANALYSIS_SETTINGS["prompt_authors"]])}
Categories: {", ".join(paper.get("categories", []))}
Published: {paper.get("date", "")}

Abstract:
{paper["abstract"]}"""
        )

    return BATCH_PROMPT_TEMPLATE.replace("{{papers}}", "\n\n---\n\n".join(paper_blocks))


def analyze_batch(
    client: OpenAI,
    papers: list[dict],
    last_request_at: float | None,
    provider: str | None = None,
) -> tuple[dict[str, dict], float | None]:
    provider, cfg = get_ai_config(SETTINGS, provider)
    prompt = build_batch_prompt(papers)
    paper_ids = {paper["id"] for paper in papers}

    for attempt in range(cfg["retry_max"]):
        request_started_at = None
        try:
            wait_for_next_request(last_request_at, cfg["min_request_interval"])
            request_started_at = time.monotonic()
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                **build_chat_kwargs(
                    cfg["model"], cfg["batch_max_tokens"],
                    temperature=ANALYSIS_SETTINGS["temperature"],
                ),
            )
            last_request_at = time.monotonic()
            choice = resp.choices[0]
            raw = sanitize_json_text(choice.message.content or "")
            if not raw:
                usage = getattr(resp, "usage", None)
                details = getattr(usage, "completion_tokens_details", None)
                diagnostics = (
                    f"finish_reason={getattr(choice, 'finish_reason', None)}, "
                    f"completion_tokens={getattr(usage, 'completion_tokens', None)}, "
                    f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)}"
                )
                raise json.JSONDecodeError(
                    f"Empty model response ({diagnostics})", raw, 0
                )
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise json.JSONDecodeError(
                    "Batch response is not a JSON object", raw, 0
                )
            missing_ids = sorted(paper_ids - set(result.keys()))
            if missing_ids:
                raise json.JSONDecodeError(
                    f"Missing paper ids: {', '.join(missing_ids)}", raw, 0
                )
            return result, last_request_at
        except json.JSONDecodeError as e:
            if request_started_at is not None:
                last_request_at = request_started_at
            print(f"  [warn] JSON parse error (attempt {attempt + 1}): {e}")
        except APIError as e:
            if request_started_at is not None:
                last_request_at = request_started_at
            print(f"  [warn] API error (attempt {attempt + 1}): {e}")
        time.sleep(cfg["retry_interval"] * (2**attempt))

    raise RuntimeError(
        f"AI analysis with {provider} failed after {cfg['retry_max']} attempts"
    )


def get_analysis_providers() -> list[str]:
    primary, _ = get_ai_config(SETTINGS)
    configured = SETTINGS.get("analysis", {}).get("fallback_providers", [])
    return list(dict.fromkeys([primary, *configured]))


def chunk_papers(papers: list[dict], batch_size: int) -> list[list[dict]]:
    return [
        papers[index : index + batch_size]
        for index in range(0, len(papers), batch_size)
    ]


ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?$", re.IGNORECASE
)


def normalize_arxiv_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not ARXIV_ID_RE.fullmatch(candidate):
        return None
    return re.sub(r"v\d+$", "", candidate, flags=re.IGNORECASE)


def normalize_title(value) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s*\(\d{4}\)\s*$", "", value.strip())
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def verify_related_papers(
    candidates_by_paper: dict[str, list[dict]], fetcher=fetch_arxiv_ids
) -> dict[str, list[dict]]:
    """Fail closed unless candidate IDs and titles match official arXiv metadata."""
    valid_ids = sorted(
        {
            arxiv_id
            for items in candidates_by_paper.values()
            for item in items
            if isinstance(item, dict)
            for arxiv_id in [normalize_arxiv_id(item.get("id"))]
            if arxiv_id
        }
    )
    verified = {paper_id: [] for paper_id in candidates_by_paper}
    if not valid_ids:
        return verified
    try:
        official_papers = fetcher(valid_ids)
    except Exception as exc:
        print(
            "  [warn] Official arXiv related-paper verification unavailable; "
            f"publishing no related-paper links ({type(exc).__name__}: {exc})"
        )
        return verified
    official_by_id = {
        normalized: paper
        for paper in official_papers
        for normalized in [normalize_arxiv_id(paper.get("id"))]
        if normalized
    }
    for paper_id, items in candidates_by_paper.items():
        for item in items:
            if not isinstance(item, dict):
                continue
            arxiv_id = normalize_arxiv_id(item.get("id"))
            official = official_by_id.get(arxiv_id)
            if not arxiv_id or not official:
                continue
            if normalize_title(item.get("label")) != normalize_title(
                official.get("title")
            ):
                continue
            year = str(official.get("published_iso", ""))[:4]
            title = official["title"].strip()
            verified[paper_id].append(
                {
                    "label": f"{title} ({year})" if year.isdigit() else title,
                    "arxivId": arxiv_id,
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "verified": True,
                    "source": "arxiv_api",
                }
            )
    return verified


def trusted_affiliation(paper: dict) -> tuple[str, str | None]:
    """Return only affiliations explicitly sourced from arXiv metadata."""
    if paper.get("orgSource") == "arxiv_affiliation" and paper.get("org"):
        return str(paper["org"]), "arxiv_affiliation"
    return "", None


def main():
    raw_path = ROOT / "data" / "raw_papers.json"
    if not raw_path.exists():
        raise FileNotFoundError(
            f"{raw_path} was not found. Run fetch_papers.py first."
        )

    papers = json.loads(raw_path.read_text())
    print(f"[analyze] Analyzing {len(papers)} papers ...")

    providers = get_analysis_providers()
    clients = {}
    _, cfg = get_ai_config(SETTINGS, providers[0])
    analyzed = []
    next_read_candidates = {}
    batches = chunk_papers(papers, cfg["batch_size"])
    last_request_at = {provider: None for provider in providers}
    active_provider_index = 0

    for batch_index, batch in enumerate(batches, 1):
        batch_ids = ", ".join(paper["id"] for paper in batch)
        print(
            f"[analyze] batch ({batch_index}/{len(batches)}) size={len(batch)} ids={batch_ids}"
        )
        errors = []
        for provider_index in range(active_provider_index, len(providers)):
            provider = providers[provider_index]
            try:
                if provider not in clients:
                    clients[provider] = get_client(provider)
                batch_results, last_request_at[provider] = analyze_batch(
                    clients[provider], batch, last_request_at[provider], provider
                )
            except (EnvironmentError, RuntimeError, ValueError) as exc:
                errors.append(str(exc))
                if provider_index + 1 < len(providers):
                    print(
                        f"  [warn] {exc}; falling back to "
                        f"{providers[provider_index + 1]}"
                    )
                continue
            active_provider_index = provider_index
            break
        else:
            raise RuntimeError(
                "AI analysis failed closed across all configured providers: "
                + "; ".join(errors)
            )

        for paper in batch:
            result = batch_results[paper["id"]]
            raw_candidates = result.get("nextReads", [])
            next_read_candidates[paper["id"]] = (
                raw_candidates if isinstance(raw_candidates, list) else []
            )
            org, org_source = trusted_affiliation(paper)
            analyzed.append(
                {
                    "id": paper["id"],
                    "date": paper["date"],
                    "title": paper["title"],
                    "titleJa": result.get("titleJa", paper["title"]),
                    "authors": paper.get("authors", []),
                    "org": org,
                    "orgSource": org_source,
                    "abstract": paper.get("abstract", ""),
                    "comment": paper.get("comment"),
                    "journalRef": paper.get("journalRef"),
                    "categories": paper.get("categories", []),
                    "url": paper["url"],
                    "category": paper.get("category", "other"),
                    "task": result.get("task"),
                    "taskEn": result.get("taskEn") or None,
                    "proposedMethod": result.get("proposedMethod"),
                    "datasets": result.get("datasets", []),
                    "what": result.get("what", ""),
                    "whatEn": result.get("whatEn") or "",
                    "novel": result.get("novel", ""),
                    "novelEn": result.get("novelEn") or "",
                    "method": result.get("method", ""),
                    "methodEn": result.get("methodEn") or "",
                    "validation": result.get("validation", ""),
                    "validationEn": result.get("validationEn") or "",
                    "discussion": result.get("discussion", ""),
                    "discussionEn": result.get("discussionEn") or "",
                    "abstractJa": result.get("abstractJa", ""),
                    "nextReads": [],
                }
            )
            for field in ("taskEn", "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn"):
                if not analyzed[-1].get(field):
                    analyzed[-1].pop(field, None)

    verified_next_reads = verify_related_papers(next_read_candidates)
    for paper in analyzed:
        paper["nextReads"] = verified_next_reads.get(paper["id"], [])

    out_path = ROOT / "data" / "analyzed_papers.json"
    out_path.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2))
    print(f"[analyze] Saved → {out_path}")


if __name__ == "__main__":
    main()
