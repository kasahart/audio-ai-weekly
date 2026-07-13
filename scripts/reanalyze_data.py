#!/usr/bin/env python3
"""
reanalyze_data.py
Overwrite AI fields in existing weekly JSON using the latest prompt.
"""
import json
import sys
import time
from pathlib import Path

import yaml
from openai import OpenAI

from model_utils import create_client, get_ai_config

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_papers import (
    SYSTEM_PROMPT,
    analyze_batch,
    build_next_reads,
    chunk_papers,
    fallback_result,
    wait_for_next_request,
)
from build_data import generate_trend

SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
WEEKLY_DIR = ROOT / "data" / "weekly"

AI_FIELDS = ("titleJa", "org", "task", "taskEn", "proposedMethod", "datasets",
             "what", "whatEn", "novel", "novelEn", "method", "methodEn", "validation", "validationEn", "discussion", "discussionEn",
             "abstractJa", "nextReads")


def get_client() -> OpenAI:
    return create_client(SETTINGS)


def reanalyze_file(path: Path, client: OpenAI, ai_results: dict) -> bool:
    data = json.loads(path.read_text())
    changed = False

    for cat in data.get("categories", []):
        for paper in cat.get("papers", []):
            arxiv_id = paper["id"].split("v")[0]
            if arxiv_id not in ai_results:
                continue

            result = ai_results[arxiv_id]
            for field in AI_FIELDS[:-1]:
                new_val = result.get(field)
                if new_val is not None and new_val != "":
                    if paper.get(field) != new_val:
                        paper[field] = new_val
                        changed = True

            new_reads = build_next_reads(result.get("nextReads", []))
            if new_reads and paper.get("nextReads") != new_reads:
                paper["nextReads"] = new_reads
                changed = True

    papers = [paper for cat in data.get("categories", []) for paper in cat.get("papers", [])]
    if papers:
        trend, trend_en = generate_trend(client, papers)
        if data.get("trend") != trend:
            data["trend"] = trend
            changed = True
        if data.get("trendEn") != trend_en:
            data["trendEn"] = trend_en
            changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[reanalyze] Saved -> {path.name}")
    else:
        print(f"[reanalyze] No changes -> {path.name}")

    return changed


def main():
    client = get_client()
    _, cfg = get_ai_config(SETTINGS)

    # Collect every paper from all weekly files.
    all_papers = []
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        for cat in data.get("categories", []):
            for paper in cat.get("papers", []):
                if paper.get("abstract"):
                    all_papers.append(paper)

    print(f"[reanalyze] Target papers: {len(all_papers)}")
    print(f"[reanalyze] Batch size: {cfg['batch_size']} → approximately {-(-len(all_papers) // cfg['batch_size'])} API calls")

    # Regenerate AI fields in batches.
    ai_results: dict[str, dict] = {}
    batches = chunk_papers(all_papers, cfg["batch_size"])
    last_request_at = None

    for i, batch in enumerate(batches, 1):
        ids = [p["id"].split("v")[0] for p in batch]
        print(f"[reanalyze] batch ({i}/{len(batches)}) ids={', '.join(ids)}")
        batch_results, last_request_at = analyze_batch(client, batch, last_request_at)

        for paper in batch:
            arxiv_id = paper["id"].split("v")[0]
            result = batch_results.get(paper["id"], fallback_result(paper))
            ai_results[arxiv_id] = result

    print(f"\n[reanalyze] AI analysis complete: {len(ai_results)} papers")

    # Update each file.
    last_trend_request_at = last_request_at
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        print(f"\n[reanalyze] --- {path.name} ---")
        wait_for_next_request(last_trend_request_at, cfg["min_request_interval"])
        reanalyze_file(path, client, ai_results)
        last_trend_request_at = time.monotonic()

    print("\n[reanalyze] Complete.")


if __name__ == "__main__":
    main()
