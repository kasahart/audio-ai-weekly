import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_data import group_by_category

# group_by_category depends on KEYWORDS["ui_categories"], so these tests use
# the real definitions from keywords.yaml.

class TestGroupByCategory:
    def test_groups_papers_by_category(self):
        papers = [
            {"id": "1", "category": "foundation"},
            {"id": "2", "category": "separation"},
            {"id": "3", "category": "foundation"},
        ]
        result = group_by_category(papers)
        ids_by_cat = {c["id"]: len(c["papers"]) for c in result}
        assert ids_by_cat.get("foundation") == 2
        assert ids_by_cat.get("separation") == 1

    def test_unknown_category_goes_to_other(self):
        papers = [{"id": "1", "category": "unknown_cat"}]
        result = group_by_category(papers)
        other = next((c for c in result if c["id"] == "other"), None)
        assert other is not None
        assert len(other["papers"]) == 1

    def test_missing_category_goes_to_other(self):
        papers = [{"id": "1"}]  # No category key.
        result = group_by_category(papers)
        other = next((c for c in result if c["id"] == "other"), None)
        assert other is not None

    def test_empty_categories_excluded(self):
        papers = [{"id": "1", "category": "foundation"}]
        result = group_by_category(papers)
        cat_ids = [c["id"] for c in result]
        # Categories without papers are omitted.
        assert "separation" not in cat_ids or any(
            c["id"] == "separation" and len(c["papers"]) == 0 for c in result
        ) is False

    def test_result_has_required_fields(self):
        papers = [{"id": "1", "category": "anomaly"}]
        result = group_by_category(papers)
        cat = next(c for c in result if c["id"] == "anomaly")
        assert "id" in cat
        assert "label" in cat
        assert "color" in cat
        assert "papers" in cat
