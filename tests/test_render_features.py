import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import render_features


def make_feature():
    sources = []
    for index in range(1, 9):
        sources.append(
            {
                "sourceId": f"S{index}",
                "arxivId": f"2501.0000{index}",
                "title": f"Paper {index}",
                "abstract": f"Abstract {index}",
                "authors": ["Alice", "Bob"],
                "publishedAt": "2025-01-01T00:00:00Z",
                "url": f"https://arxiv.org/abs/2501.0000{index}",
                "origin": "archive" if index <= 4 else "external",
                "primaryLinks": (
                    [
                        {
                            "label": "Code",
                            "url": "https://github.com/example/repo?x=1&y=2",
                        }
                    ]
                    if index == 1
                    else []
                ),
            }
        )
    section_ids = [
        "why-needed",
        "history",
        "approaches",
        "perspectives",
        "limits",
        "outlook",
    ]
    sections = []
    for index, section_id in enumerate(section_ids):
        source_ids = [f"S{index + 1}"]
        if index == 0:
            source_ids.extend(["S7", "S8"])
        sections.append(
            {
                "id": section_id,
                "heading": f"Section {index + 1}",
                "blocks": [
                    {
                        "id": f"block-{index + 1}",
                        "text": ("本文" * 260)
                        + " <script>alert(1)</script> & analysis",
                        "sourceIds": source_ids,
                    }
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "slug": "2026-07-14-primer-source-separation",
        "type": "primer",
        "date": "2026-07-14",
        "generatedAt": "2026-07-14T00:00:00+00:00",
        "topicKey": "source-separation",
        "searchTerms": ["source separation"],
        "title": "音源分離 <script>",
        "titleEn": "Source Separation <script>",
        "dek": "一次資料から読む & 考える。",
        "dekEn": "A primary-source feature.",
        "summaryEn": "A concise English summary.",
        "keyPointsEn": ["One", "Two", "Three"],
        "readTimeMinutes": 8,
        "perspectives": [
            {
                "id": "metrics",
                "label": "指標",
                "description": "指標の視点",
                "sourceIds": ["S1"],
            },
            {
                "id": "data",
                "label": "データ",
                "description": "データの視点",
                "sourceIds": ["S2"],
            },
            {
                "id": "users",
                "label": "利用",
                "description": "利用の視点",
                "sourceIds": ["S3"],
            },
        ],
        "sections": sections,
        "sources": sources,
        "verification": {"status": "passed", "revisionCount": 0},
    }


def write_feature_data(input_dir, feature):
    input_dir.mkdir(parents=True)
    (input_dir / f"{feature['slug']}.json").write_text(
        json.dumps(feature, ensure_ascii=False)
    )
    (input_dir / "index.json").write_text(
        json.dumps(
            {
                "features": [
                    {
                        "slug": feature["slug"],
                        "file": f"{feature['slug']}.json",
                    }
                ],
                "generatedAt": feature["generatedAt"],
            }
        )
    )


def test_missing_index_renders_empty_archive_placeholder(tmp_path):
    output = tmp_path / "public" / "features"
    written = render_features.render_all(tmp_path / "missing-features", output)
    assert written == [output / "index.html"]
    html = (output / "index.html").read_text()
    assert "公開済みの特集はまだありません" in html
    assert "radial-gradient" not in html


def test_render_all_writes_escaped_article_archive_seo_and_primary_links(tmp_path):
    input_dir = tmp_path / "data" / "features"
    output_dir = tmp_path / "public" / "features"
    feature = make_feature()
    write_feature_data(input_dir, feature)

    written = render_features.render_all(
        input_dir, output_dir, "https://example.test/site"
    )

    article_path = output_dir / feature["slug"] / "index.html"
    assert article_path in written
    article = article_path.read_text()
    assert "&lt;script&gt;" in article
    assert "<script>alert(1)</script>" not in article
    assert 'rel="canonical" href="https://example.test/site/features/' in article
    assert 'type="application/ld+json"' in article
    assert "\\u003cscript\\u003e" in article
    assert "分野を解く" in article
    assert "AI生成・自動検証" in article
    assert "Metadata-linked resources:" in article
    assert ">Code</a>" in article
    assert "x=1&amp;y=2" in article
    assert "IBM Plex Mono" in article
    assert "radial-gradient" not in article

    archive = (output_dir / "index.html").read_text()
    assert f'./{feature["slug"]}/' in archive
    assert "音源分離 &lt;script&gt;" in archive
    assert "Source Separation &lt;script&gt;" in archive
    assert not list(output_dir.rglob(".*.html.*"))


def test_renderer_refuses_unverified_or_tampered_features(tmp_path):
    feature = make_feature()
    feature["verification"]["status"] = "revise"
    input_dir = tmp_path / "unverified"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="verifier-passed"):
        render_features.load_features(input_dir)

    feature = make_feature()
    feature["sections"][0]["blocks"][0]["sourceIds"].remove("S8")
    input_dir = tmp_path / "uncited"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="Every listed source"):
        render_features.load_features(input_dir)

    feature = make_feature()
    feature["sections"][0]["id"] = "replacement"
    input_dir = tmp_path / "missing-required-section"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="why-needed"):
        render_features.load_features(input_dir)


def test_renderer_rejects_missing_or_deceptive_metadata_links(tmp_path):
    feature = make_feature()
    for source in feature["sources"]:
        source["primaryLinks"] = []
    input_dir = tmp_path / "missing-link"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="metadata-linked resource"):
        render_features.load_features(input_dir)

    feature = make_feature()
    feature["sources"][0]["primaryLinks"][0][
        "url"
    ] = "https://github.com@evil.example/owner/repo"
    input_dir = tmp_path / "deceptive-link"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="invalid primaryLink"):
        render_features.load_features(input_dir)


def test_renderer_recomputes_body_length_language_and_read_time(tmp_path):
    feature = make_feature()
    for section in feature["sections"]:
        section["blocks"][0]["text"] = "短い本文"
    input_dir = tmp_path / "short-body"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="Japanese body must contain"):
        render_features.load_features(input_dir)

    feature = make_feature()
    for section in feature["sections"]:
        section["blocks"][0]["text"] = ("あ" * 100) + ("a" * 450)
    input_dir = tmp_path / "english-dominant"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="language ratio"):
        render_features.load_features(input_dir)

    feature = make_feature()
    feature["readTimeMinutes"] = 9
    input_dir = tmp_path / "wrong-read-time"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="readTimeMinutes must be 8"):
        render_features.load_features(input_dir)


def test_renderer_rejects_index_path_traversal(tmp_path):
    input_dir = tmp_path / "features"
    input_dir.mkdir()
    (input_dir / "index.json").write_text(
        json.dumps(
            {
                "features": [
                    {
                        "slug": "2026-07-14-primer-safe",
                        "file": "../outside.json",
                    }
                ]
            }
        )
    )
    with pytest.raises(render_features.RenderError, match="Unsafe feature file path"):
        render_features.load_features(input_dir)
