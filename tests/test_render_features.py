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
                "heading": f"節 {index + 1}",
                "headingEn": f"Section {index + 1}",
                "blocks": [
                    {
                        "id": f"block-{index + 1}",
                        "text": ("本文" * 260)
                        + " <script>alert(1)</script> & analysis",
                        "textEn": " ".join(["evidence"] * 200),
                        "sourceIds": source_ids,
                    }
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "sourceLanguage": "en",
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
        "readTimeMinutesEn": 6,
        "perspectives": [
            {
                "id": "metrics",
                "label": "指標",
                "description": "指標の視点",
                "labelEn": "Metrics",
                "descriptionEn": "The metrics perspective.",
                "sourceIds": ["S1"],
            },
            {
                "id": "data",
                "label": "データ",
                "description": "データの視点",
                "labelEn": "Data",
                "descriptionEn": "The data perspective.",
                "sourceIds": ["S2"],
            },
            {
                "id": "users",
                "label": "利用",
                "description": "利用の視点",
                "labelEn": "Use",
                "descriptionEn": "The practitioner perspective.",
                "sourceIds": ["S3"],
            },
        ],
        "sections": sections,
        "sources": sources,
        "translation": {
            "targetLanguage": "ja",
            "status": "passed",
            "revisionCount": 0,
        },
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
    assert written == [output / "index.html", output / "en" / "index.html"]
    japanese = (output / "index.html").read_text()
    english = (output / "en" / "index.html").read_text()
    assert "公開済みの特集はまだありません" in japanese
    assert "No features have been published yet" in english
    assert "radial-gradient" not in japanese


def test_renderer_keeps_legacy_english_summary_compatible(tmp_path):
    input_dir = tmp_path / "data" / "features"
    output_dir = tmp_path / "public" / "features"
    feature = make_feature()
    feature.pop("sourceLanguage")
    feature.pop("translation")
    feature.pop("readTimeMinutesEn")
    for perspective in feature["perspectives"]:
        perspective.pop("labelEn")
        perspective.pop("descriptionEn")
    for section in feature["sections"]:
        section.pop("headingEn")
        for block in section["blocks"]:
            block.pop("textEn")
    write_feature_data(input_dir, feature)

    render_features.render_all(input_dir, output_dir)

    english = (output_dir / feature["slug"] / "en" / "index.html").read_text()
    assert "English summary" in english
    assert feature["summaryEn"] in english
    assert "This summary is grounded" in english


def test_render_all_writes_escaped_article_archive_seo_and_primary_links(tmp_path):
    input_dir = tmp_path / "data" / "features"
    output_dir = tmp_path / "public" / "features"
    feature = make_feature()
    write_feature_data(input_dir, feature)

    written = render_features.render_all(
        input_dir, output_dir, "https://example.test/site"
    )

    japanese_path = output_dir / feature["slug"] / "index.html"
    english_path = output_dir / feature["slug"] / "en" / "index.html"
    assert japanese_path in written
    assert english_path in written
    japanese = japanese_path.read_text()
    english = english_path.read_text()
    assert "&lt;script&gt;" in japanese
    assert "&lt;script&gt;" in english
    assert "<script>alert(1)</script>" not in japanese
    assert "<script>alert(1)</script>" not in english
    assert 'rel="canonical" href="https://example.test/site/features/' in japanese
    assert f'rel="canonical" href="https://example.test/site/features/{feature["slug"]}/en/"' in english
    assert 'hreflang="ja"' in japanese
    assert 'hreflang="en"' in japanese
    assert 'type="application/ld+json"' in japanese
    assert "\\u003cscript\\u003e" in japanese
    assert "\\u003cscript\\u003e" in english
    assert "分野を解く" in japanese
    assert "AI生成・自動検証" in japanese
    assert "関連リソース:" in japanese
    assert "一次資料（原題）" in japanese
    assert feature["titleEn"] not in japanese
    assert feature["dekEn"] not in japanese
    assert feature["summaryEn"] not in japanese
    assert "English summary" not in japanese
    assert feature["title"] not in english
    assert feature["dek"] not in english
    assert feature["sections"][0]["blocks"][0]["text"] not in english
    assert feature["perspectives"][0]["labelEn"] in english
    assert feature["sections"][0]["headingEn"] in english
    assert feature["sections"][0]["blocks"][0]["textEn"] in english
    assert "Source Separation &lt;script&gt;" in english
    assert feature["summaryEn"] not in english
    assert "Metadata-linked resources:" in english
    assert ">コード</a>" in japanese
    assert ">Code</a>" in english
    assert "x=1&amp;y=2" in japanese
    assert "IBM Plex Mono" in japanese
    assert "radial-gradient" not in japanese

    japanese_archive = (output_dir / "index.html").read_text()
    english_archive = (output_dir / "en" / "index.html").read_text()
    assert f'./{feature["slug"]}/' in japanese_archive
    assert f'../{feature["slug"]}/en/' in english_archive
    assert "音源分離 &lt;script&gt;" in japanese_archive
    assert "Source Separation &lt;script&gt;" not in japanese_archive
    assert "Source Separation &lt;script&gt;" in english_archive
    assert "音源分離 &lt;script&gt;" not in english_archive
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
    with pytest.raises(render_features.RenderError, match="predominantly Japanese"):
        render_features.load_features(input_dir)

    feature = make_feature()
    feature["sections"][0]["blocks"][0]["textEn"] = "日本語だけの本文" * 100
    input_dir = tmp_path / "japanese-english-edition"
    write_feature_data(input_dir, feature)
    with pytest.raises(render_features.RenderError, match="textEn.*English"):
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
