import asyncio

from scripts.consolidate_budget_concept_duplicates import (
    REFERENCE_TABLES,
    _apply,
    _catalog_rows,
)


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Session:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, _parameters):
        self.statements.append(str(statement))
        return _Result(1)


class _CatalogResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _CatalogSession:
    def __init__(self):
        self.statement = ""

    async def execute(self, statement):
        self.statement = str(statement)
        return _CatalogResult()


def test_catalog_reference_count_includes_movement_assignments():
    session = _CatalogSession()

    assert asyncio.run(_catalog_rows(session)) == []
    assert "FROM budget_movement_assignments" in session.statement


def test_apply_relinks_movement_assignments_before_deactivation():
    session = _Session()

    receipt = asyncio.run(
        _apply(
            session,
            {
                "operations": [
                    {
                        "canonical_id": "canonical",
                        "duplicate_ids": ["duplicate"],
                    }
                ]
            },
            "actor",
        )
    )

    assert "budget_movement_assignments" in REFERENCE_TABLES
    assert any(
        "UPDATE budget_movement_assignments" in statement
        for statement in session.statements
    )
    assert receipt == {
        "updated_references": len(REFERENCE_TABLES),
        "deactivated": 1,
    }
