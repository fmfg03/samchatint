import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MUTABLE_FIELDS = (
    "ocr_raw",
    "extraction",
    "validation",
    "review_edits",
    "layout_regions",
    "overall_confidence",
    "needs_review",
    "draft_version",
)


def test_no_registration_draft_field_is_overwritten_outside_versioning_module():
    pattern = re.compile(
        rf"\b(?:draft|review_draft)\.({'|'.join(MUTABLE_FIELDS)})\s*="
    )
    violations = []
    for path in ROOT.rglob("*.py"):
        if path.name == "draft_versioning.py" or "tests" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{number}")
    assert violations == []


def test_database_migration_blocks_update_delete_and_discontinuous_insert():
    migration = (
        ROOT / "database/migrations/20260716_regs02_append_only_review_drafts.sql"
    ).read_text(encoding="utf-8")
    assert "IF TG_OP IN ('UPDATE', 'DELETE')" in migration
    assert "stale or discontinuous draft successor" in migration
    assert "BEFORE INSERT OR UPDATE OR DELETE" in migration
