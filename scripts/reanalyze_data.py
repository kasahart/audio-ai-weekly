#!/usr/bin/env python3
"""
reanalyze_data.py
Overwrite AI fields in existing weekly JSON using the latest prompt.
"""
import json
import os
import sys
import time
from pathlib import Path

import yaml
from openai import OpenAI

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_papers import (
    SYSTEM_PROMPT,
    analyze_batch,
    build_next_reads,
    chunk_papers,
    fallback_result,
)

SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
WEEKLY_DIR = ROOT / "data" / "weekly"

AI_FIELDS = ("titleJa", "org", "task", "proposedMethod", "datasets",
             "what", "novel", "method", "validation", "discussion",
             "abstractJa", "nextReads")


def get_client() -> OpenAI:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN is not set")
    cfg = SETTINGS["github_models"]
    return OpenAI(base_url=cfg["endpoint"], api_key=token)


def reanalyze_file(path: Path, client: OpenAI, ai_results: dict) -> bool:
    data = json.loads(path.read_text())
    changed = False

    for cat in data.get("categories", []):
        for paper in cat.get("papers", []):
            arxiv_id = paper["id"].split("v")[0]
            if arxiv_id not in ai_results:
                continue

            result = ai_results[arxiv_id]
            for field in ("titleJa", "org", "task", "proposedMethod",
                          "datasets", "what", "novel", "method",
                          "validation", "discussion", "abstractJa"):
                new_val = result.get(field)
                if new_val is not None and new_val != "":
                    if paper.get(field) != new_val:
                        paper[field] = new_val
                        changed = True

            new_reads = build_next_reads(result.get("nextReads", []))
            if new_reads and paper.get("nextReads") != new_reads:
                paper["nextReads"] = new_reads
                changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[reanalyze] Saved -> {path.name}")
    else:
        print(f"[reanalyze] No changes -> {path.name}")

    return changed


def main():
    client = get_client()
    cfg = SETTINGS["github_models"]

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
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        print(f"\n[reanalyze] --- {path.name} ---")
        reanalyze_file(path, client, ai_results)

    print("\n[reanalyze] Complete.")


if __name__ == "__main__":
    main()
