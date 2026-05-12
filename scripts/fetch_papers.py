#!/usr/bin/env python3
"""
fetch_papers.py
Fetch papers from the arXiv API for the configured categories and filter them
by keyword.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def build_query() -> str:
    cats = KEYWORDS["categories"]
    cat_query = " OR ".join(f"cat:{c}" for c in cats)
    return f"({cat_query})"


def fetch_arxiv(query: str, start: int, max_results: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": SETTINGS["arxiv"]["user_agent"]
    })
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return parse_atom(resp.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            wait = 2 ** attempt
            print(f"[fetch] arXiv error (attempt {attempt + 1}/4): {e}; retrying in {wait}s")
            time.sleep(wait)
    raise last_err  # type: ignore[misc]


def parse_atom(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    papers = []
    for entry in root.findall("atom:entry", NS):
        arxiv_id_raw = entry.findtext("atom:id", "", NS)
        arxiv_id = arxiv_id_raw.split("/abs/")[-1].strip()

        published = entry.findtext("atom:published", "", NS)
        try:
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            date_str = pub_dt.strftime("%b %d")  # e.g. "Apr 15"
        except Exception:
            date_str = ""

        authors = [
            a.findtext("atom:name", "", NS)
            for a in entry.findall("atom:author", NS)
        ]
        orgs = extract_orgs(entry)

        comment = (entry.findtext("arxiv:comment", "", NS) or "").strip().replace("\n", " ")
        journal_ref = (entry.findtext("arxiv:journal_ref", "", NS) or "").strip()

        papers.append({
            "id": arxiv_id,
            "title": (entry.findtext("atom:title", "", NS) or "").strip().replace("\n", " "),
            "abstract": (entry.findtext("atom:summary", "", NS) or "").strip().replace("\n", " "),
            "comment": comment or None,
            "journalRef": journal_ref or None,
            "date": date_str,
            "published_iso": published,
            "authors": authors[:5],
            "org": orgs,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "categories": [
                t.get("term", "")
                for t in entry.findall("atom:category", NS)
            ],
        })
    return papers


def extract_orgs(entry) -> str:
    """Extract the primary affiliation from an arXiv author entry (best-effort)."""
    affiliations = [
        a.findtext("arxiv:affiliation", "", NS)
        for a in entry.findall("atom:author", NS)
    ]
    affiliations = [a for a in affiliations if a]
    if affiliations:
        return affiliations[0][:60]
    # Fall back to author names when no affiliation is provided.
    authors = [a.findtext("atom:name", "", NS) for a in entry.findall("atom:author", NS)]
    if len(authors) == 1:
        return authors[0]
    elif len(authors) <= 3:
        return " / ".join(authors)
    else:
        return f"{authors[0]} et al."


def is_within_window(published_iso: str, lookback_days: int, ref_date: datetime) -> bool:
    try:
        pub_dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
        cutoff = ref_date - timedelta(days=lookback_days)
        return cutoff <= pub_dt <= ref_date
    except Exception:
        return True  # Include the paper if the date fails to parse.


def keyword_match(paper: dict, include: list[str], exclude: list[str]) -> bool:
    text = (paper["title"] + " " + paper["abstract"]).lower()
    if any(kw.lower() in text for kw in exclude):
        return False
    return any(kw.lower() in text for kw in include)


def assign_category(paper: dict, ui_categories: list[dict]) -> str:
    text = (paper["title"] + " " + paper["abstract"]).lower()
    for cat in ui_categories:
        if any(kw.lower() in text for kw in cat["keywords"]):
            return cat["id"]
    return "other"


def main(dry_run: bool = False, date_str: str | None = None):
    cfg = SETTINGS["arxiv"]
    max_papers = cfg["max_papers"]
    lookback_days = cfg["lookback_days"]
    interval = cfg["request_interval"]

    if date_str:
        ref_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    else:
        ref_date = datetime.now(timezone.utc)

    query = build_query()
    include_kws = KEYWORDS["include"]
    exclude_kws = KEYWORDS.get("exclude", [])
    ui_cats = KEYWORDS["ui_categories"]

    print(f"[fetch] Query: {query}")
    print(f"[fetch] Ref date: {ref_date.date()} / Lookback: {lookback_days} days / Max: {max_papers} papers")

    seen_ids: set[str] = set()
    collected: list[dict] = []
    batch = 100
    start = 0

    while len(collected) < max_papers:
        print(f"[fetch] Fetching batch start={start} ...")
        papers = fetch_arxiv(query, start, batch)
        if not papers:
            break

        for p in papers:
            if p["id"] in seen_ids:
                continue
            seen_ids.add(p["id"])

            if not is_within_window(p["published_iso"], lookback_days, ref_date):
                # Skip papers newer than ref_date; stop iterating once we go past the window.
                try:
                    pub_dt = datetime.fromisoformat(p["published_iso"].replace("Z", "+00:00"))
                    if pub_dt < ref_date - timedelta(days=lookback_days):
                        print(f"[fetch] Out of window, stopping.")
                        papers = []  # force outer break
                        break
                except Exception:
                    pass
                continue

            if keyword_match(p, include_kws, exclude_kws):
                p["category"] = assign_category(p, ui_cats)
                collected.append(p)
                if len(collected) >= max_papers:
                    break

        if not papers:
            break
        start += batch
        time.sleep(interval)

    print(f"[fetch] Collected {len(collected)} papers after filtering.")

    if dry_run:
        print("[fetch] --dry-run: skipping file output.")
        return

    out_path = ROOT / "data" / "raw_papers.json"
    out_path.write_text(json.dumps(collected, ensure_ascii=False, indent=2))
    print(f"[fetch] Saved → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show how many papers were fetched without writing the output file")
    parser.add_argument("--date", type=str, default=None, help="Base date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()
    main(dry_run=args.dry_run, date_str=args.date)
