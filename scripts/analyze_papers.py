#!/usr/bin/env python3
"""
analyze_papers.py
Analyze each paper in Japanese from six perspectives using the configured AI provider.
"""

import json
import time
from pathlib import Path

import yaml
from openai import APIError, OpenAI

from model_utils import build_chat_kwargs, create_client, get_ai_config

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())

SYSTEM_PROMPT = """You are a research-paper analyst specializing in speech and audio AI.
Analyze the supplied title and abstract and respond only with the JSON structure below.
Do not include a preamble, explanation, or Markdown code fences.

Write the base descriptive fields in natural Japanese and every field ending in En in natural English. Apply these terminology rules strictly to Japanese:
- Translate "Speech" as the Japanese term specifically meaning human speech.
- Translate "Sound" and "Acoustic" using Japanese terms for general sound or acoustics.
- Translate "Audio" as audio or an audio/acoustic signal, not as human speech.
- Translate "Voice" using the Japanese term for the biological voice or singing voice.
- Translate "Audio Source Separation" as source separation, including instruments and noise.
- Translate "Audio Signal Processing" as audio/acoustic signal processing.
- Translate "Acoustic Event" as an acoustic or sound event.
- Use these exact Japanese technical terms in the generated text:
  - Audio Source Separation: 音源分離
  - Audio Signal Processing: 音響信号処理 or オーディオ信号処理
  - Acoustic Event: 音響イベント or 音イベント
  - Speech Enhancement: 音声強調
  - Acoustic Gain: 音響利得
  - Voice Activity Detection: 音声区間検出
  - Sound Localization: 音源定位
  - Environmental Sound: 環境音
  - Audio Foundation Model: 音響基盤モデル
- Use the established Japanese technical terms for Speech Enhancement, Acoustic Gain,
  Voice Activity Detection, Sound Localization, Environmental Sound, and
  Audio Foundation Model. Reserve speech-specific Japanese terminology for
  contexts that actually concern human speech.

{
  "titleJa": "A natural Japanese translation of the paper title",
  "org": "Primary author affiliation, such as MIT / Google",
  "task": "One- or two-word task classification",
  "taskEn": "One- or two-word task classification in English",
  "proposedMethod": "Named method or abbreviation, or null",
  "datasets": ["Dataset name 1", "Dataset name 2"],
  "what": "A one- or two-sentence overview",
  "whatEn": "The same overview in English",
  "novel": "A one- or two-sentence explanation of novelty and contributions",
  "novelEn": "The same novelty explanation in English",
  "method": "A one- or two-sentence explanation of the technical core",
  "methodEn": "The same technical explanation in English",
  "validation": "A one- or two-sentence summary of datasets, metrics, and comparisons",
  "validationEn": "The same validation summary in English",
  "discussion": "A one- or two-sentence discussion of limitations and open issues",
  "discussionEn": "The same discussion in English",
  "abstractJa": "A complete, natural Japanese translation of the abstract",
  "nextReads": [
    {"label": "Related paper title (year)", "id": "arXiv ID or null"}
  ]
}

Return three or four nextReads entries. Keep nextReads labels as original English
paper titles (plus year), even though the surrounding base fields are Japanese.
Use null when the arXiv ID is unknown.
List up to five datasets used for training or evaluation.
Keep the Japanese and English descriptions equivalent in meaning."""


def get_client() -> OpenAI:
    return create_client(SETTINGS)


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
    "taskEn": "English task classification",
    "proposedMethod": "Named method or abbreviation, or null",
    "datasets": ["Dataset name 1", "Dataset name 2"],
    "what": "One- or two-sentence overview",
    "whatEn": "Equivalent English overview",
    "novel": "One- or two-sentence novelty summary",
    "novelEn": "Equivalent English novelty summary",
    "method": "One- or two-sentence technical summary",
    "methodEn": "Equivalent English technical summary",
    "validation": "One- or two-sentence validation summary",
    "validationEn": "Equivalent English validation summary",
    "discussion": "One- or two-sentence limitations summary",
    "discussionEn": "Equivalent English limitations summary",
    "abstractJa": "Complete natural Japanese abstract translation",
    "nextReads": [
      {{"label": "Related paper title (year)", "id": "arXiv ID or null"}}
    ]
  }}
}}

Return three or four nextReads entries per paper, keep their labels as original
English paper titles, and use null for unknown arXiv IDs.
List up to five training or evaluation datasets.
Write base fields in Japanese and fields ending in En in English.

{joined}"""


def fallback_result(paper: dict) -> dict:
    return {
        "titleJa": paper["title"],
        "org": paper.get("org", ""),
        "task": None,
        "taskEn": None,
        "proposedMethod": None,
        "datasets": [],
        "what": "Analysis failed.",
        "whatEn": "",
        "novel": "",
        "novelEn": "",
        "method": "",
        "methodEn": "",
        "validation": "",
        "validationEn": "",
        "discussion": "",
        "discussionEn": "",
        "abstractJa": "",
        "nextReads": [],
    }


def analyze_batch(
    client: OpenAI, papers: list[dict], last_request_at: float | None
) -> tuple[dict[str, dict], float | None]:
    _, cfg = get_ai_config(SETTINGS)
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
        f"AI analysis failed after {cfg['retry_max']} attempts; refusing to publish fallback data"
    )


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
    _, cfg = get_ai_config(SETTINGS)
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
                    "nextReads": build_next_reads(result.get("nextReads", [])),
                }
            )
            for field in ("taskEn", "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn"):
                if not analyzed[-1].get(field):
                    analyzed[-1].pop(field, None)

    out_path = ROOT / "data" / "analyzed_papers.json"
    out_path.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2))
    print(f"[analyze] Saved → {out_path}")


if __name__ == "__main__":
    main()
