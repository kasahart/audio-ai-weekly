#!/usr/bin/env python3
"""
enrich_data.py
Add missing fields to existing weekly JSON files.
"""
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from openai import OpenAI

from model_utils import build_chat_kwargs, create_client, get_ai_config
from analyze_papers import wait_for_next_request
from build_data import generate_trend

ROOT = Path(__file__).parent.parent
WEEKLY_DIR = ROOT / "data" / "weekly"
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

AI_FIELDS = ("abstractJa", "task", "taskEn", "proposedMethod", "datasets",
             "whatEn", "novelEn", "methodEn", "validationEn", "discussionEn")


def fetch_arxiv_meta(arxiv_id: str) -> dict:
    clean_id = arxiv_id.split("v")[0]
    url = f"https://export.arxiv.org/api/query?id_list={clean_id}&max_results=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arxiv-weekly/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            tree = ET.fromstring(r.read())
        entry = tree.find("atom:entry", NS)
        if entry is None:
            return {}
        return {
            "abstract": (entry.findtext("atom:summary", "", NS) or "").strip().replace("\n", " "),
            "comment": (entry.findtext("arxiv:comment", "", NS) or "").strip().replace("\n", " ") or None,
            "journalRef": (entry.findtext("arxiv:journal_ref", "", NS) or "").strip() or None,
            "categories": [t.get("term", "") for t in entry.findall("atom:category", NS)],
        }
    except Exception as e:
        print(f"  [warn] arXiv error {arxiv_id}: {e}")
        return {}


def fetch_hf_meta(arxiv_id: str) -> dict:
    clean_id = arxiv_id.split("v")[0]
    try:
        url = f"https://huggingface.co/api/papers/{clean_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "arxiv-weekly/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return {
            "githubRepo": data.get("githubRepo") or None,
            "upvotes": data.get("upvotes"),
            "projectPage": data.get("projectPage") or None,
        }
    except Exception:
        return {}


def fetch_citation_count(arxiv_id: str) -> int | None:
    clean_id = arxiv_id.split("v")[0]
    try:
        url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{clean_id}?fields=citationCount"
        req = urllib.request.Request(url, headers={"User-Agent": "arxiv-weekly/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("citationCount")
    except Exception:
        return None


BATCH_PROMPT_TMPL = """Analyze the papers below and respond with a JSON object only,
without Markdown code fences. Use the paper ID as each key and this structure as
its value:

{{
  "<paper_id>": {{
    "abstractJa": "Complete natural Japanese abstract translation",
    "task": "One- or two-word Japanese task classification, such as TTS, ASR, 音源分離, 異音検知, or 音楽生成",
    "taskEn": "Equivalent one- or two-word English task classification",
    "proposedMethod": "Named method or abbreviation, or null",
    "datasets": ["Dataset name 1", "Dataset name 2"],
    "whatEn": "One- or two-sentence English overview",
    "novelEn": "One- or two-sentence English novelty summary",
    "methodEn": "One- or two-sentence English technical summary",
    "validationEn": "One- or two-sentence English validation summary",
    "discussionEn": "One- or two-sentence English limitations summary"
  }}
}}

List up to five datasets. Write task in Japanese and every field ending in En in English.

{papers}
"""


def build_batch_prompt(papers: list[dict]) -> str:
    blocks = []
    for p in papers:
        blocks.append(f"ID: {p['id'].split('v')[0]}\nTitle: {p['title']}\nAbstract: {p.get('abstract', '')}")
    return BATCH_PROMPT_TMPL.format(papers="\n\n---\n\n".join(blocks))


def fetch_ai_fields_batch(client: OpenAI, papers: list[dict]) -> dict[str, dict]:
    _, cfg = get_ai_config(SETTINGS)
    prompt = build_batch_prompt(papers)
    paper_ids = [p["id"].split("v")[0] for p in papers]
    fallback = {pid: {} for pid in paper_ids}

    for attempt in range(cfg["retry_max"]):
        try:
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": "Respond with JSON only."},
                    {"role": "user", "content": prompt},
                ],
                **build_chat_kwargs(
                    cfg["model"], 800 * len(papers), temperature=0.3
                ),
            )
            raw = (resp.choices[0].message.content or "").strip()
            raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            result = json.loads(raw)
            if isinstance(result, dict):
                return result
        except Exception as e:
            print(f"  [warn] AI error (attempt {attempt + 1}): {e}")
            time.sleep(cfg["retry_interval"] * (2 ** attempt))
    return fallback


def enrich_file(path: Path, ai_client: OpenAI | None, ai_results: dict) -> bool:
    data = json.loads(path.read_text())
    changed = False

    for cat in data.get("categories", []):
        for paper in cat.get("papers", []):
            arxiv_id = paper["id"].split("v")[0]
            paper_changed = False

            # arXiv metadata.
            if "abstract" not in paper or "categories" not in paper:
                meta = fetch_arxiv_meta(arxiv_id)
                for k, v in meta.items():
                    if k not in paper:
                        paper[k] = v
                        paper_changed = True
                time.sleep(0.5)

            # Hugging Face metadata.
            if "upvotes" not in paper or "projectPage" not in paper:
                meta = fetch_hf_meta(arxiv_id)
                for k, v in meta.items():
                    if k not in paper:
                        paper[k] = v
                        paper_changed = True
                time.sleep(0.3)

            # Citation count.
            if "citationCount" not in paper:
                paper["citationCount"] = fetch_citation_count(arxiv_id)
                paper_changed = True
                time.sleep(0.3)

            # AI fields from the completed batch.
            if arxiv_id in ai_results:
                result = ai_results[arxiv_id]
                for k, v in result.items():
                    if k not in paper and v is not None:
                        paper[k] = v
                        paper_changed = True

            if paper_changed:
                changed = True
                print(f"  [enrich] {arxiv_id} updated")

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[enrich] Saved -> {path.name}")
    else:
        print(f"[enrich] No changes -> {path.name}")

    return changed


def main():
    weekly_files = sorted(WEEKLY_DIR.glob("*.json"))
    print(f"[enrich] Processing {len(weekly_files)} weekly files")

    # Refresh public category definitions even when the source index predates
    # the bilingual schema.
    index_path = ROOT / SETTINGS["data"]["index_file"]
    index = json.loads(index_path.read_text()) if index_path.exists() else {"weeks": []}
    index["categories"] = [
        {"id": c["id"], "label": c["label"], "labelEn": c["labelEn"], "color": c["color"]}
        for c in KEYWORDS["ui_categories"]
    ]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))

    ai_client = None
    provider, cfg = get_ai_config(SETTINGS)
    batch_size = cfg["batch_size"]
    try:
        ai_client = create_client(SETTINGS)
        print(f"[enrich] Enabling batched AI field enrichment with {provider}")
    except EnvironmentError:
        print(
            f"[enrich] {cfg['api_key_env']} is not set for provider {provider}; "
            "skipping AI fields"
        )

    # Collect papers with missing AI fields from every weekly file.
    ai_results: dict[str, dict] = {}
    last_ai_request_at = None
    if ai_client:
        papers_needing_ai = []
        for path in weekly_files:
            data = json.loads(path.read_text())
            for cat in data.get("categories", []):
                for paper in cat.get("papers", []):
                    if any(f not in paper for f in AI_FIELDS) and paper.get("abstract"):
                        papers_needing_ai.append(paper)

        print(f"[enrich] Papers requiring AI enrichment: {len(papers_needing_ai)}")

        # Process in batches.
        for i in range(0, len(papers_needing_ai), batch_size):
            batch = papers_needing_ai[i:i + batch_size]
            ids = [p["id"].split("v")[0] for p in batch]
            print(f"[enrich] AI batch ({i // batch_size + 1}/{(len(papers_needing_ai) + batch_size - 1) // batch_size}) ids={', '.join(ids)}")
            wait_for_next_request(last_ai_request_at, cfg["min_request_interval"])
            result = fetch_ai_fields_batch(ai_client, batch)
            last_ai_request_at = time.monotonic()
            ai_results.update(result)

    # Write metadata back to each file.
    last_trend_request_at = last_ai_request_at
    for path in weekly_files:
        print(f"\n[enrich] --- {path.name} ---")
        enrich_file(path, ai_client, ai_results)
        if ai_client:
            data = json.loads(path.read_text())
            papers = [paper for cat in data.get("categories", []) for paper in cat.get("papers", [])]
            if papers and not data.get("trendEn"):
                wait_for_next_request(last_trend_request_at, cfg["min_request_interval"])
                trend, trend_en = generate_trend(ai_client, papers)
                last_trend_request_at = time.monotonic()
                if trend and not data.get("trend"):
                    data["trend"] = trend
                if trend_en:
                    data["trendEn"] = trend_en
                if trend or trend_en:
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    print(f"[enrich] Added bilingual trends -> {path.name}")

    if weekly_files:
        latest_path = ROOT / SETTINGS["data"]["latest_file"]
        latest_path.write_text(max(weekly_files).read_text())
        print(f"[enrich] Refreshed latest -> {latest_path}")

    print("\n[enrich] Complete.")


if __name__ == "__main__":
    main()
