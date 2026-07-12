#!/usr/bin/env python3
"""
analyze_papers.py
Analyze each paper in Japanese from six perspectives using GitHub Models.
"""

import json
import os
import time
from pathlib import Path

import yaml
from openai import APIError, OpenAI

from model_utils import build_chat_kwargs

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())

SYSTEM_PROMPT = """You are a research-paper analyst specializing in speech and audio AI.
Analyze the supplied title and abstract and respond only with the JSON structure below.
Do not include a preamble, explanation, or Markdown code fences.

Write every descriptive field in natural Japanese. Apply these terminology rules strictly:
- Translate "Speech" as the Japanese term specifically meaning human speech.
- Translate "Sound" and "Acoustic" using Japanese terms for general sound or acoustics.
- Translate "Audio" as audio or an audio/acoustic signal, not as human speech.
- Translate "Voice" using the Japanese term for the biological voice or singing voice.
- Translate "Audio Source Separation" as source separation, including instruments and noise.
- Translate "Audio Signal Processing" as audio/acoustic signal processing.
- Translate "Acoustic Event" as an acoustic or sound event.
- Use these exact Japanese technical terms in the generated text:
  - Audio Source Separation: \u97f3\u6e90\u5206\u96e2
  - Audio Signal Processing: \u97f3\u97ff\u4fe1\u53f7\u51e6\u7406 or \u30aa\u30fc\u30c7\u30a3\u30aa\u4fe1\u53f7\u51e6\u7406
  - Acoustic Event: \u97f3\u97ff\u30a4\u30d9\u30f3\u30c8 or \u97f3\u30a4\u30d9\u30f3\u30c8
  - Speech Enhancement: \u97f3\u58f0\u5f37\u8abf
  - Acoustic Gain: \u97f3\u97ff\u5229\u5f97
  - Voice Activity Detection: \u97f3\u58f0\u533a\u9593\u691c\u51fa
  - Sound Localization: \u97f3\u6e90\u5b9a\u4f4d
  - Environmental Sound: \u74b0\u5883\u97f3
  - Audio Foundation Model: \u97f3\u97ff\u57fa\u76e4\u30e2\u30c7\u30eb
- Use the established Japanese technical terms for Speech Enhancement, Acoustic Gain,
  Voice Activity Detection, Sound Localization, Environmental Sound, and
  Audio Foundation Model. Reserve speech-specific Japanese terminology for
  contexts that actually concern human speech.

{
  "titleJa": "A natural Japanese translation of the paper title",
  "org": "Primary author affiliation, such as MIT / Google",
  "task": "One- or two-word task classification",
  "proposedMethod": "Named method or abbreviation, or null",
  "datasets": ["Dataset name 1", "Dataset name 2"],
  "what": "A one- or two-sentence overview",
  "novel": "A one- or two-sentence explanation of novelty and contributions",
  "method": "A one- or two-sentence explanation of the technical core",
  "validation": "A one- or two-sentence summary of datasets, metrics, and comparisons",
  "discussion": "A one- or two-sentence discussion of limitations and open issues",
  "abstractJa": "A complete, natural Japanese translation of the abstract",
  "nextReads": [
    {"label": "Related paper title (year)", "id": "arXiv ID or null"}
  ]
}

Return three or four nextReads entries. Use null when the arXiv ID is unknown.
List up to five datasets used for training or evaluation.
Write all descriptions in Japanese."""


def get_client() -> OpenAI:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN is not set")
    cfg = SETTINGS["github_models"]
    return OpenAI(base_url=cfg["endpoint"], api_key=token)


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
Authors: {", ".join(paper.get("authors", [])[:3])}
Categories: {", ".join(paper.get("categories", []))}
Published: {paper.get("date", "")}

Abstract:
{paper["abstract"]}"""
        )

    joined = "\n\n---\n\n".join(paper_blocks)
    return f"""Analyze the papers below.
Return one result per paper ID as a JSON object only. Use the paper ID as each
key and the following structure as its value:

{{
  "<paper_id>": {{
    "titleJa": "Natural Japanese title translation",
    "org": "Primary author affiliation",
    "task": "One- or two-word task classification",
    "proposedMethod": "Named method or abbreviation, or null",
    "datasets": ["Dataset name 1", "Dataset name 2"],
    "what": "One- or two-sentence overview",
    "novel": "One- or two-sentence novelty summary",
    "method": "One- or two-sentence technical summary",
    "validation": "One- or two-sentence validation summary",
    "discussion": "One- or two-sentence limitations summary",
    "abstractJa": "Complete natural Japanese abstract translation",
    "nextReads": [
      {{"label": "Related paper title (year)", "id": "arXiv ID or null"}}
    ]
  }}
}}

Return three or four nextReads entries per paper and use null for unknown arXiv IDs.
List up to five training or evaluation datasets.
Write all descriptions in Japanese.

{joined}"""


def fallback_result(paper: dict) -> dict:
    return {
        "titleJa": paper["title"],
        "org": paper.get("org", ""),
        "task": None,
        "proposedMethod": None,
        "datasets": [],
        "what": "\u89e3\u6790\u306b\u5931\u6557\u3057\u307e\u3057\u305f\u3002",
        "novel": "",
        "method": "",
        "validation": "",
        "discussion": "",
        "abstractJa": "",
        "nextReads": [],
    }


def analyze_batch(
    client: OpenAI, papers: list[dict], last_request_at: float | None
) -> tuple[dict[str, dict], float | None]:
    cfg = SETTINGS["github_models"]
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
                **build_chat_kwargs(
                    cfg["model"], cfg["batch_max_tokens"], temperature=0.3
                ),
            )
            last_request_at = time.monotonic()
            raw = sanitize_json_text(resp.choices[0].message.content or "")
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

    return {paper["id"]: fallback_result(paper) for paper in papers}, last_request_at


def chunk_papers(papers: list[dict], batch_size: int) -> list[list[dict]]:
    return [
        papers[index : index + batch_size]
        for index in range(0, len(papers), batch_size)
    ]


def build_next_reads(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        arxiv_id = item.get("id")
        url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None
        result.append({"label": item.get("label", ""), "url": url})
    return result


def main():
    raw_path = ROOT / "data" / "raw_papers.json"
    if not raw_path.exists():
        raise FileNotFoundError(
            f"{raw_path} was not found. Run fetch_papers.py first."
        )

    papers = json.loads(raw_path.read_text())
    print(f"[analyze] Analyzing {len(papers)} papers ...")

    client = get_client()
    cfg = SETTINGS["github_models"]
    analyzed = []
    batches = chunk_papers(papers, cfg["batch_size"])
    last_request_at = None

    for batch_index, batch in enumerate(batches, 1):
        batch_ids = ", ".join(paper["id"] for paper in batch)
        print(
            f"[analyze] batch ({batch_index}/{len(batches)}) size={len(batch)} ids={batch_ids}"
        )
        batch_results, last_request_at = analyze_batch(client, batch, last_request_at)

        for paper in batch:
            result = batch_results.get(paper["id"], fallback_result(paper))
            analyzed.append(
                {
                    "id": paper["id"],
                    "date": paper["date"],
                    "title": paper["title"],
                    "titleJa": result.get("titleJa", paper["title"]),
                    "authors": paper.get("authors", []),
                    "org": result.get("org") or paper.get("org", ""),
                    "abstract": paper.get("abstract", ""),
                    "comment": paper.get("comment"),
                    "journalRef": paper.get("journalRef"),
                    "categories": paper.get("categories", []),
                    "url": paper["url"],
                    "category": paper.get("category", "other"),
                    "task": result.get("task"),
                    "proposedMethod": result.get("proposedMethod"),
                    "datasets": result.get("datasets", []),
                    "what": result.get("what", ""),
                    "novel": result.get("novel", ""),
                    "method": result.get("method", ""),
                    "validation": result.get("validation", ""),
                    "discussion": result.get("discussion", ""),
                    "abstractJa": result.get("abstractJa", ""),
                    "nextReads": build_next_reads(result.get("nextReads", [])),
                }
            )

    out_path = ROOT / "data" / "analyzed_papers.json"
    out_path.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2))
    print(f"[analyze] Saved → {out_path}")


if __name__ == "__main__":
    main()
