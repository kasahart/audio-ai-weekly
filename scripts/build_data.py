#!/usr/bin/env python3
"""
build_data.py
Format analyzed papers as weekly JSON and update the index.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml
from openai import OpenAI

from model_utils import build_chat_kwargs, create_client, get_ai_config

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())

TREND_PROMPT = """Based on the following paper titles and summaries, describe this
week's technical trends in speech and audio AI research in exactly three concise
Japanese lines and three equivalent English lines. Mention specific paper or method
names and do not prefix lines with numbers or symbols. Return JSON only in this form:
{"ja": ["...", "...", "..."], "en": ["...", "...", "..."]}."""


def generate_trend(client: OpenAI, papers: list[dict]) -> tuple[list[str], list[str]]:
    _, cfg = get_ai_config(SETTINGS)
    summaries = "\n".join(
        f"- {p['title']}: {p.get('whatEn') or p.get('what') or p.get('abstract', '')}"
        for p in papers[:20]
    )
    last_request_at = None
    for attempt in range(cfg["retry_max"]):
        if last_request_at is not None:
            elapsed = time.monotonic() - last_request_at
            remaining = cfg["min_request_interval"] - elapsed
            if remaining > 0:
                print(
                    f"  [build] waiting {remaining:.1f}s before retrying trend generation ..."
                )
                time.sleep(remaining)
        request_started_at = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": "Respond with JSON only."},
                    {"role": "user", "content": f"{TREND_PROMPT}\n\n{summaries}"},
                ],
                **build_chat_kwargs(cfg["model"], 400, temperature=0.4),
            )
            raw = (resp.choices[0].message.content or "").strip()
            raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            result = json.loads(raw)
            if isinstance(result, dict):
                ja, en = result.get("ja"), result.get("en")
                if (isinstance(ja, list) and isinstance(en, list)
                        and len(ja) == 3 and len(en) == 3
                        and all(isinstance(line, str) for line in ja + en)):
                    return ja, en
            # Accept the legacy response shape so transient model deviations remain usable.
            if (isinstance(result, list) and len(result) == 3
                    and all(isinstance(line, str) for line in result)):
                return result, []
            raise ValueError("trend response does not match the expected JSON shape")
        except Exception as e:
            last_request_at = request_started_at
            print(f"  [warn] trend generation error (attempt {attempt + 1}): {e}")
    return [], []


def group_by_category(papers: list[dict]) -> list[dict]:
    ui_cats = KEYWORDS["ui_categories"]
    cat_map = {
        c["id"]: {"id": c["id"], "label": c["label"], "labelEn": c["labelEn"], "color": c["color"], "papers": []}
        for c in ui_cats
    }
    cat_map["other"] = {
        "id": "other",
        "label": "その他",
        "labelEn": "Other",
        "color": "#94a3b8",
        "papers": [],
    }

    for p in papers:
        cat_id = p.get("category", "other")
        if cat_id not in cat_map:
            cat_id = "other"
        cat_map[cat_id]["papers"].append(p)

    return [v for v in cat_map.values() if v["papers"]]


def load_index() -> dict:
    index_path = ROOT / SETTINGS["data"]["index_file"]
    if index_path.exists():
        return json.loads(index_path.read_text())
    return {"weeks": [], "generated_at": ""}


def fetch_paper_meta(papers: list[dict]) -> dict[str, dict]:
    """Retrieve citation and repository metadata from Semantic Scholar and Hugging Face."""
    meta: dict[str, dict] = {}
    for p in papers:
        arxiv_id = p["id"].split("v")[0]
        citation_count = None
        github_repo = None

        # Semantic Scholar citation count.
        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}?fields=citationCount"
            req = urllib.request.Request(url, headers={"User-Agent": "arxiv-weekly/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                citation_count = data.get("citationCount")
        except Exception:
            pass

        # Hugging Face Papers repository, upvotes, and project page.
        upvotes = None
        project_page = None
        try:
            url = f"https://huggingface.co/api/papers/{arxiv_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "arxiv-weekly/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                github_repo = data.get("githubRepo") or None
                upvotes = data.get("upvotes")
                project_page = data.get("projectPage") or None
        except Exception:
            pass

        meta[arxiv_id] = {
            "citationCount": citation_count,
            "githubRepo": github_repo,
            "upvotes": upvotes,
            "projectPage": project_page,
        }
        time.sleep(0.5)  # Respect API rate limits.

    found_citations = sum(1 for v in meta.values() if v["citationCount"] is not None)
    found_repos = sum(1 for v in meta.values() if v["githubRepo"])
    found_hf = sum(1 for v in meta.values() if v["upvotes"] is not None)
    print(f"[build] Meta: citations={found_citations}/{len(papers)}, repos={found_repos}/{len(papers)}, hf={found_hf}/{len(papers)}")
    return meta


def save_index(index: dict):
    index_path = ROOT / SETTINGS["data"]["index_file"]
    index["generated_at"] = datetime.now(timezone.utc).isoformat()
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(f"[build] Index updated → {index_path}")


def main(date_str: str | None = None):
    analyzed_path = ROOT / "data" / "analyzed_papers.json"
    if not analyzed_path.exists():
        raise FileNotFoundError(
            f"{analyzed_path} was not found. Run analyze_papers.py first."
        )

    papers = json.loads(analyzed_path.read_text())
    if date_str:
        now = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    date_key = now.strftime("%Y-%m%d")  # Example: 2026-0425
    filename = f"{date_key}.json"
    weekly_path = ROOT / SETTINGS["data"]["weekly_dir"] / filename

    # Add citation and GitHub repository metadata to the papers.
    print("[build] Fetching citation counts and GitHub repos ...")
    meta = fetch_paper_meta(papers)
    for p in papers:
        arxiv_id = p["id"].split("v")[0]
        m = meta.get(arxiv_id, {})
        p["citationCount"] = m.get("citationCount")
        p["githubRepo"] = m.get("githubRepo")
        p["upvotes"] = m.get("upvotes")
        p["projectPage"] = m.get("projectPage")

    # Generate the trend summary with the configured AI provider.
    provider, cfg = get_ai_config(SETTINGS)
    try:
        client = create_client(SETTINGS)
        trend, trend_en = generate_trend(client, papers)
        if not trend:
            trend = ["① トレンド情報なし", "② トレンド情報なし", "③ トレンド情報なし"]
    except EnvironmentError:
        print(
            f"[build] {cfg['api_key_env']} is not set for provider {provider}; "
            "skipping trend generation."
        )
        trend = ["① トレンド情報なし", "② トレンド情報なし", "③ トレンド情報なし"]
        trend_en = []

    # Group papers by category.
    categories = group_by_category(papers)

    weekly_data = {
        "date": date_key,
        "generated_at": now.isoformat(),
        "total": len(papers),
        "categories": categories,
        "trend": trend,
    }
    if trend_en:
        weekly_data["trendEn"] = trend_en

    # Save the weekly file.
    weekly_path.parent.mkdir(parents=True, exist_ok=True)
    weekly_path.write_text(json.dumps(weekly_data, ensure_ascii=False, indent=2))
    print(f"[build] Saved weekly → {weekly_path}")

    # Update latest.json.
    latest_path = ROOT / SETTINGS["data"]["latest_file"]
    latest_path.write_text(json.dumps(weekly_data, ensure_ascii=False, indent=2))
    print(f"[build] Updated latest → {latest_path}")

    # Update index.json.
    index = load_index()
    index["weeks"] = [w for w in index["weeks"] if w["date"] != date_key]
    index["weeks"].insert(
        0,
        {
            "date": date_key,
            "file": f"weekly/{filename}",
            "count": len(papers),
            "generated_at": now.isoformat(),
        },
    )
    # Always write the latest category definitions from keywords.yaml.
    index["categories"] = [
        {"id": c["id"], "label": c["label"], "labelEn": c["labelEn"], "color": c["color"]}
        for c in KEYWORDS["ui_categories"]
    ]
    save_index(index)

    # Remove intermediate files.
    (ROOT / "data" / "raw_papers.json").unlink(missing_ok=True)
    (ROOT / "data" / "analyzed_papers.json").unlink(missing_ok=True)
    print("[build] Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="Reference date YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    main(date_str=args.date)
