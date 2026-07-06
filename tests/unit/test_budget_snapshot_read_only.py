from __future__ import annotations

import pytest

import samchat.budgets.service as budget_service


class _ExecuteResult:
    def __init__(self, scalar_value=None) -> None:
        self._scalar_value = scalar_value

    def scalar_one(self):
        return self._scalar_value

    def mappings(self):
        raise AssertionError("read-only snapshot should not query budget tables")


class _ReadOnlySession:
    def __init__(self) -> None:
        self.statements = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "CREATE " in sql or "ALTER " in sql or "CREATE INDEX" in sql:
            raise AssertionError("read-only budget snapshot must not run DDL")
        if "to_regclass" in sql:
            return _ExecuteResult(False)
        raise AssertionError(f"unexpected read-only snapshot SQL: {sql}")

    async def commit(self):
        self.commits += 1
        raise AssertionError("read-only budget snapshot must not commit")


@pytest.mark.asyncio
async def test_build_budget_snapshot_does_not_ensure_schema_when_read_only(monkeypatch):
    monkeypatch.setattr(budget_service, "load_budget_artifact_rows", lambda: [])

    session = _ReadOnlySession()

    result = await budget_service.build_budget_snapshot(
        session,
        tournament_name="Liga Telmex Telcel",
        edition_year=2026,
    )

    assert result["summary"]["line_count"] == 0
    assert session.commits == 0
    assert any("to_regclass" in statement for statement in session.statements)
