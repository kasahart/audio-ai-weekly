#!/usr/bin/env python3
"""Render validated feature JSON as escaped, crawlable static HTML pages."""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import math
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("data/features")
DEFAULT_OUTPUT = Path("web/public/features")
DEFAULT_SITE_URL = "https://kasahart.github.io/arxiv-weekly"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_ID_RE = re.compile(r"^S[1-9][0-9]*$")
JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
VALIDATION_MIN_CHARS = 3151
VALIDATION_MAX_CHARS = 5400
READING_CHARS_PER_MINUTE = 450
JAPANESE_BODY_MIN_RATIO = 0.5
REQUIRED_SECTIONS = {
    "primer": {
        "why-needed",
        "history",
        "approaches",
        "perspectives",
        "limits",
        "outlook",
    },
    "debate": {
        "current-signal",
        "positions",
        "evidence",
        "evaluation",
        "implications",
        "watch",
    },
}


class RenderError(RuntimeError):
    """Feature data is unsafe or incomplete and must not be rendered."""


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _inside(root: Path, path: Path) -> bool:
    root = root.resolve()
    path = path.resolve()
    return path == root or root in path.parents


def _require_text(data: dict, field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RenderError(f"{context}.{field} must be a non-empty string")
    return value.strip()


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


def _is_valid_primary_link(label: str, value: str) -> bool:
    if label not in ("Code", "Project") or not _is_public_https_url(value):
        return False
    parsed = urllib.parse.urlsplit(value)
    if label == "Code":
        path_parts = [part for part in parsed.path.split("/") if part]
        return (
            parsed.hostname.lower().rstrip(".") == "github.com" and len(path_parts) >= 2
        )
    return True


def _japanese_ratio(value: str) -> float:
    japanese = len(JAPANESE_CHAR_RE.findall(value))
    latin = len(LATIN_CHAR_RE.findall(value))
    return japanese / max(1, japanese + latin)


def _validate_feature_for_render(feature: Any, expected_slug: str) -> dict:
    """Check the rendering contract independently of generator dependencies."""
    if not isinstance(feature, dict):
        raise RenderError("Feature JSON must be an object")
    slug = _require_text(feature, "slug", "feature")
    if slug != expected_slug or not SLUG_RE.fullmatch(slug):
        raise RenderError("Feature slug is invalid or does not match its index entry")
    if feature.get("type") not in ("primer", "debate"):
        raise RenderError("Feature type must be primer or debate")
    if feature.get("verification", {}).get("status") != "passed":
        raise RenderError("Feature must be marked as verifier-passed")
    for field in ("date", "title", "titleEn", "dek", "dekEn", "summaryEn"):
        _require_text(feature, field, "feature")
    if (
        not isinstance(feature.get("readTimeMinutes"), int)
        or not 8 <= feature["readTimeMinutes"] <= 12
    ):
        raise RenderError("readTimeMinutes must be between 8 and 12")

    key_points = feature.get("keyPointsEn")
    if (
        not isinstance(key_points, list)
        or len(key_points) < 3
        or not all(isinstance(point, str) and point.strip() for point in key_points)
    ):
        raise RenderError("keyPointsEn must contain at least three strings")

    sources = feature.get("sources")
    if not isinstance(sources, list) or len(sources) < 8:
        raise RenderError("Feature must contain at least eight sources")
    source_ids: set[str] = set()
    primary_link_count = 0
    for source in sources:
        if not isinstance(source, dict):
            raise RenderError("Each source must be an object")
        source_id = _require_text(source, "sourceId", "source")
        if not SOURCE_ID_RE.fullmatch(source_id) or source_id in source_ids:
            raise RenderError("Source IDs must be unique S1, S2, ... values")
        source_ids.add(source_id)
        for field in ("arxivId", "title", "abstract", "url"):
            _require_text(source, field, f"source {source_id}")
        if source["url"] != f"https://arxiv.org/abs/{source['arxivId']}":
            raise RenderError(f"Source {source_id} must use an arXiv URL")
        primary_links = source.get("primaryLinks", [])
        if not isinstance(primary_links, list):
            raise RenderError(f"Source {source_id} primaryLinks must be an array")
        for link in primary_links:
            if not isinstance(link, dict):
                raise RenderError(f"Source {source_id} has an invalid primaryLink")
            label = link.get("label")
            url = link.get("url")
            if (
                not isinstance(label, str)
                or not isinstance(url, str)
                or not _is_valid_primary_link(label, url)
            ):
                raise RenderError(f"Source {source_id} has an invalid primaryLink")
            primary_link_count += 1
    if primary_link_count < 1:
        raise RenderError("Feature must include a validated metadata-linked resource")

    perspectives = feature.get("perspectives")
    if not isinstance(perspectives, list) or len(perspectives) != 3:
        raise RenderError("Feature must contain exactly three perspectives")
    for perspective in perspectives:
        if not isinstance(perspective, dict):
            raise RenderError("Each perspective must be an object")
        for field in ("id", "label", "description"):
            _require_text(perspective, field, "perspective")
        ids = perspective.get("sourceIds")
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(value, str) for value in ids)
            or not set(ids) <= source_ids
        ):
            raise RenderError("Perspective sourceIds must refer to known sources")

    sections = feature.get("sections")
    if not isinstance(sections, list) or len(sections) < 6:
        raise RenderError("Feature must contain at least six sections")
    cited_ids: set[str] = set()
    section_ids: set[str] = set()
    block_ids: set[str] = set()
    body_texts: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            raise RenderError("Each section must be an object")
        section_id = _require_text(section, "id", "section")
        if not SLUG_RE.fullmatch(section_id) or section_id in section_ids:
            raise RenderError("Section IDs must be unique lowercase kebab-case values")
        section_ids.add(section_id)
        _require_text(section, "heading", "section")
        blocks = section.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise RenderError("Every section must contain blocks")
        for block in blocks:
            if not isinstance(block, dict):
                raise RenderError("Each block must be an object")
            block_id = _require_text(block, "id", "block")
            if not SLUG_RE.fullmatch(block_id) or block_id in block_ids:
                raise RenderError(
                    "Block IDs must be unique lowercase kebab-case values"
                )
            block_ids.add(block_id)
            body_texts.append(_require_text(block, "text", "block"))
            ids = block.get("sourceIds")
            if (
                not isinstance(ids, list)
                or not ids
                or not all(isinstance(value, str) for value in ids)
                or not set(ids) <= source_ids
            ):
                raise RenderError("Every block must cite known source IDs")
            cited_ids.update(ids)
    missing_sections = REQUIRED_SECTIONS[feature["type"]] - section_ids
    if missing_sections:
        raise RenderError(
            "Feature is missing required sections: "
            + ", ".join(sorted(missing_sections))
        )
    if cited_ids != source_ids:
        raise RenderError("Every listed source must be cited in the article body")
    body_text = "".join(body_texts)
    character_count = len("".join(body_text.split()))
    if not VALIDATION_MIN_CHARS <= character_count <= VALIDATION_MAX_CHARS:
        raise RenderError(
            f"Japanese body must contain {VALIDATION_MIN_CHARS}-{VALIDATION_MAX_CHARS} "
            "non-whitespace characters"
        )
    if _japanese_ratio(body_text) < JAPANESE_BODY_MIN_RATIO:
        raise RenderError("Japanese body language ratio must be at least 50%")
    expected_read_time = math.ceil(character_count / READING_CHARS_PER_MINUTE)
    if feature["readTimeMinutes"] != expected_read_time:
        raise RenderError(f"readTimeMinutes must be {expected_read_time}")
    return feature


def load_features(input_dir: Path) -> tuple[dict, list[dict]]:
    index_path = input_dir / "index.json"
    if not index_path.exists():
        return {"features": [], "generatedAt": ""}, []
    try:
        index = json.loads(index_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RenderError(f"Cannot load feature index {index_path}: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("features"), list):
        raise RenderError("Feature index must contain a features array")

    features: list[dict] = []
    for entry in index["features"]:
        if not isinstance(entry, dict):
            raise RenderError("Feature index entries must be objects")
        slug = _require_text(entry, "slug", "feature index entry")
        if not SLUG_RE.fullmatch(slug):
            raise RenderError(f"Unsafe feature slug: {slug!r}")
        relative_file = entry.get("file", f"{slug}.json")
        if not isinstance(relative_file, str):
            raise RenderError(f"Invalid file for feature {slug}")
        path = (input_dir / relative_file).resolve()
        if not _inside(input_dir, path):
            raise RenderError(f"Unsafe feature file path: {relative_file}")
        try:
            feature = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise RenderError(f"Cannot load feature {path}: {exc}") from exc
        features.append(_validate_feature_for_render(feature, slug))
    return index, features


STYLE = """
:root{color-scheme:dark;--bg:#0f1117;--deep:#0a0d14;--panel:#131720;--line:#1e293b;--text:#e7edf7;--muted:#94a3b8;--accent:#22d3ee;--feature:#f472b6}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:"IBM Plex Mono","Space Mono",ui-monospace,SFMono-Regular,Consolas,monospace;line-height:1.8}
a{color:var(--accent)}.shell{width:min(960px,calc(100% - 32px));margin:auto}.nav{display:flex;justify-content:space-between;gap:16px;padding:24px 0;color:var(--muted)}.nav a{text-decoration:none;display:inline-flex;align-items:center;min-height:28px}.hero{padding:72px 0 44px;border-bottom:1px solid var(--line)}
.badge{display:inline-block;padding:4px 9px;border:1px solid var(--feature);border-radius:3px;color:var(--feature);font-size:.76rem;letter-spacing:.1em;text-transform:uppercase}.badge.debate{border-color:var(--accent);color:var(--accent)}h1{font-size:clamp(2.1rem,6vw,4.2rem);line-height:1.14;margin:.5em 0 .3em;letter-spacing:-.04em}h2{font-size:clamp(1.4rem,3vw,1.9rem);line-height:1.4;margin-top:2.3em}.dek{font-size:1.08rem;color:#cbd5e1;max-width:52rem}.meta{display:flex;flex-wrap:wrap;gap:10px 24px;color:var(--muted);font-size:.88rem}
main{padding:30px 0 90px}.perspectives{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:40px 0}.card,.summary,.source{background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:20px}.card h3{margin-top:0;color:var(--feature)}.article-section{max-width:780px;margin:0 auto}.article-section p{font-size:1rem}.citations{white-space:nowrap;font-size:.75rem;margin-left:.35rem}.citations a{display:inline-flex;align-items:center;justify-content:center;min-width:24px;min-height:24px;text-decoration:none;margin-right:.2rem}.summary{margin:64px 0;border-color:var(--accent);background:var(--deep)}.summary h2{margin-top:0}.sources{margin-top:64px}.source{margin:10px 0}.source-title{font-weight:700}.source-meta,.primary-links{color:var(--muted);font-size:.82rem}.primary-links a{display:inline-flex;align-items:center;min-height:28px;margin-right:1rem}.archive-grid{display:grid;gap:12px;padding:40px 0 90px}.archive-card{display:block;text-decoration:none;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:24px}.archive-card:hover{border-color:var(--feature)}.archive-card h2{margin:.35em 0 .1em}.archive-card p{color:#cbd5e1}.archive-title-en{color:var(--muted)!important;font-size:.86rem;margin:0 0 1rem}.footer{border-top:1px solid var(--line);padding:30px 0 60px;color:var(--muted)}
@media(max-width:720px){.perspectives{grid-template-columns:1fr}.hero{padding-top:40px}.shell{width:min(100% - 22px,960px)}}
"""


def _json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _document_head(
    *, title: str, description: str, canonical_url: str, json_ld: dict
) -> str:
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)}</title><meta name="description" content="{_escape(description)}">
<link rel="canonical" href="{_escape(canonical_url)}">
<meta property="og:type" content="article"><meta property="og:title" content="{_escape(title)}">
<meta property="og:description" content="{_escape(description)}"><meta property="og:url" content="{_escape(canonical_url)}">
<meta property="og:site_name" content="Audio Research Weekly"><meta name="twitter:card" content="summary">
<script type="application/ld+json">{_json_for_script(json_ld)}</script><style>{STYLE}</style></head>"""


def _source_links(source_ids: list[str]) -> str:
    links = "".join(
        f'<a href="#source-{_escape(source_id)}" aria-label="Source {_escape(source_id)}">[{_escape(source_id)}]</a>'
        for source_id in source_ids
    )
    return f'<sup class="citations">{links}</sup>'


def _primary_links(source: dict) -> str:
    links = source.get("primaryLinks", [])
    if not links:
        return ""
    rendered = "".join(
        f'<a href="{_escape(link["url"])}" rel="noopener noreferrer">{_escape(link["label"])}</a>'
        for link in links
    )
    return f'<div class="primary-links">Metadata-linked resources: {rendered}</div>'


def render_feature_page(feature: dict, site_url: str = DEFAULT_SITE_URL) -> str:
    slug = feature["slug"]
    canonical_url = f"{site_url.rstrip('/')}/features/{slug}/"
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": feature["title"],
        "alternativeHeadline": feature["titleEn"],
        "description": feature["dek"],
        "datePublished": feature["date"],
        "dateModified": feature.get("generatedAt", feature["date"]),
        "inLanguage": ["ja", "en"],
        "mainEntityOfPage": canonical_url,
        "publisher": {"@type": "Organization", "name": "Audio Research Weekly"},
        "citation": [source["url"] for source in feature["sources"]],
    }
    head = _document_head(
        title=f"{feature['title']} | Audio Research Weekly",
        description=feature["dek"],
        canonical_url=canonical_url,
        json_ld=json_ld,
    )
    kind = "分野を解く" if feature["type"] == "primer" else "論点を読む"
    perspectives = "".join(
        f"""<article class="card"><h3>{_escape(item['label'])}</h3>
<p>{_escape(item['description'])}{_source_links(item['sourceIds'])}</p></article>"""
        for item in feature["perspectives"]
    )
    sections = "".join(
        f"""<section class="article-section" id="{_escape(section['id'])}"><h2>{_escape(section['heading'])}</h2>
{''.join(f'<p>{_escape(block["text"])}{_source_links(block["sourceIds"])}</p>' for block in section['blocks'])}</section>"""
        for section in feature["sections"]
    )
    english_points = "".join(
        f"<li>{_escape(point)}</li>" for point in feature["keyPointsEn"]
    )
    sources = "".join(
        f"""<article class="source" id="source-{_escape(source['sourceId'])}">
<div class="source-meta">{_escape(source['sourceId'])} · {_escape(source['origin'])} · {_escape(source.get('publishedAt', ''))}</div>
<div class="source-title"><a href="{_escape(source['url'])}" rel="noopener noreferrer">{_escape(source['title'])}</a></div>
<div class="source-meta">{_escape(', '.join(source.get('authors', [])[:5]))} · arXiv:{_escape(source['arxivId'])}</div>
{_primary_links(source)}</article>"""
        for source in feature["sources"]
    )
    return f"""{head}<body><div class="shell"><nav class="nav"><a href="../../">← Audio Research Weekly</a><a href="../">特集一覧</a></nav></div>
<header class="hero"><div class="shell"><span class="badge {_escape(feature['type'])}">{_escape(kind)}</span>
<h1>{_escape(feature['title'])}</h1><p class="dek">{_escape(feature['dek'])}</p>
<div class="meta"><span>{_escape(feature['date'])}</span><span>約 {_escape(feature['readTimeMinutes'])} 分</span><span>{len(feature['sources'])} primary sources</span><span>AI生成・自動検証</span><a href="#english-summary">English summary</a></div></div></header>
<main class="shell"><aside class="perspectives" aria-label="3つの視点">{perspectives}</aside>{sections}
<section class="summary" id="english-summary" lang="en"><span class="badge">English summary</span><h2>{_escape(feature['titleEn'])}</h2>
<p>{_escape(feature['dekEn'])}</p><p>{_escape(feature['summaryEn'])}</p><ul>{english_points}</ul></section>
<section class="sources"><h2>一次資料</h2><p class="dek">本文は以下の arXiv 論文のタイトルとアブストラクトに基づき、出典 ID を段落ごとに付与しています。</p>{sources}</section></main>
<footer class="footer"><div class="shell"><a href="../">特集一覧へ</a> · <a href="../../">週報へ戻る</a></div></footer></body></html>"""


def render_archive_page(features: list[dict], site_url: str = DEFAULT_SITE_URL) -> str:
    canonical_url = f"{site_url.rstrip('/')}/features/"
    description = "音声・音響AI研究を一次資料から読み解く、月2回の特集記事。"
    json_ld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Audio Research Weekly 特集",
        "description": description,
        "url": canonical_url,
        "hasPart": [
            {
                "@type": "Article",
                "headline": feature["title"],
                "url": f"{canonical_url}{feature['slug']}/",
            }
            for feature in features
        ],
    }
    head = _document_head(
        title="特集 | Audio Research Weekly",
        description=description,
        canonical_url=canonical_url,
        json_ld=json_ld,
    )
    cards = "".join(
        f"""<a class="archive-card" href="./{_escape(feature['slug'])}/"><span class="badge {_escape(feature['type'])}">{'分野を解く' if feature['type'] == 'primer' else '論点を読む'}</span>
<h2>{_escape(feature['title'])}</h2><p class="archive-title-en" lang="en">{_escape(feature['titleEn'])}</p><p>{_escape(feature['dek'])}</p>
<div class="meta"><span>{_escape(feature['date'])}</span><span>約 {_escape(feature['readTimeMinutes'])} 分</span><span>{len(feature['sources'])} sources</span></div></a>"""
        for feature in features
    )
    if not cards:
        cards = '<p class="dek">公開済みの特集はまだありません。</p>'
    return f"""{head}<body><div class="shell"><nav class="nav"><a href="../">← Audio Research Weekly</a></nav>
<header class="hero"><span class="badge">Features</span><h1>研究の現在地を、一次資料から。</h1><p class="dek">{_escape(description)}</p></header>
<main class="archive-grid">{cards}</main><footer class="footer"><a href="../">週報へ戻る</a></footer></div></body></html>"""


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def render_all(
    input_dir: Path = DEFAULT_INPUT,
    output_dir: Path = DEFAULT_OUTPUT,
    site_url: str = DEFAULT_SITE_URL,
) -> list[Path]:
    _, features = load_features(input_dir)
    written: list[Path] = []
    for feature in features:
        path = output_dir / feature["slug"] / "index.html"
        _atomic_write_text(path, render_feature_page(feature, site_url))
        written.append(path)
    archive_path = output_dir / "index.html"
    _atomic_write_text(archive_path, render_archive_page(features, site_url))
    written.append(archive_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    args = parser.parse_args(argv)
    written = render_all(args.input, args.output, args.site_url)
    print(
        f"[features] Rendered {len(written) - 1} article page(s) and archive -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
