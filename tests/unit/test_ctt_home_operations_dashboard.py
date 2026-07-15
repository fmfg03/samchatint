from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

import pytest
from sqlalchemy.dialects import postgresql

os.environ.setdefault(
    "SESSION_SECRET_KEY",
    "test-only-session-secret-key-0123456789abcdef",
)

import copa_telmex_dashboard as dashboard  # noqa: E402


class _ReviewResult:
    def __init__(self, review_records: Iterable[Any]) -> None:
        self.review_records = list(review_records)

    def mappings(self) -> "_ReviewResult":
        return self

    def all(self) -> List[Any]:
        return self.review_records


class _HomeSession:
    def __init__(self, review_records: Iterable[Any]) -> None:
        self.review_records = list(review_records)
        self.statements: List[Any] = []

    async def __aenter__(self) -> "_HomeSession":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any) -> _ReviewResult:
        self.statements.append(statement)
        return _ReviewResult(self.review_records)


class _HomeDB:
    def __init__(self, _session: Any) -> None:
        pass

    async def get_registration_stats(self) -> SimpleNamespace:
        return SimpleNamespace(total_teams=1)

    async def get_registrations_needing_review(self) -> List[str]:
        return ["legacy-review"]


@pytest.mark.asyncio
async def test_home_loads_review_operations_snapshot(monkeypatch) -> None:
    review_records = [{"id": "review-session"}]
    session = _HomeSession(review_records)
    captured: Dict[str, Any] = {}

    monkeypatch.setattr(
        dashboard,
        "_ensure_legacy_copa_dashboard_access",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: session)
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _HomeDB)

    def _snapshot_builder(received: Iterable[Any]) -> Dict[str, Any]:
        assert list(received) == review_records
        return {"pending_count": 1, "recent": []}

    def _template_response(name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        captured["name"] = name
        captured["context"] = context
        return context

    monkeypatch.setattr(dashboard, "build_home_operations_snapshot", _snapshot_builder)
    monkeypatch.setattr(dashboard.templates, "TemplateResponse", _template_response)

    response = await dashboard.home(SimpleNamespace())

    assert response["pending_reviews"] == 1
    assert response["review_operations"]["pending_count"] == 1
    assert captured["name"] == "home.html"
    assert session.statements
    selected_keys = {column.key for column in session.statements[0].selected_columns}
    assert selected_keys == {
        "id",
        "status",
        "tournament_slug",
        "started_at",
        "updated_at",
        "draft_updated_at",
        "ready_to_commit",
        "blocking_issue_count",
        "issue_count",
        "player_count",
        "intake_folio",
    }
    compiled_query = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "canonical_shadow" not in compiled_query
    assert "review_edits" not in compiled_query
    assert "intake_folio" in compiled_query
