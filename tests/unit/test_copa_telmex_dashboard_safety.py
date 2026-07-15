from __future__ import annotations

import inspect
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "SESSION_SECRET_KEY",
    "test-only-session-secret-key-0123456789abcdef",
)

import copa_telmex_dashboard as dashboard  # noqa: E402


def test_custom_404_escapes_the_reflected_request_path() -> None:
    request = SimpleNamespace(
        url=SimpleNamespace(path='<img src=x onerror="alert(1)">')
    )

    response = dashboard._render_not_found_page(request)
    html = response.body.decode("utf-8")

    assert '<img src=x onerror="alert(1)">' not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


@pytest.mark.parametrize(
    "review_session",
    [
        SimpleNamespace(status="committed", committed_at=None, committed_team_id=None),
        SimpleNamespace(
            status="ready", committed_at=datetime(2026, 1, 1), committed_team_id=None
        ),
        SimpleNamespace(status="ready", committed_at=None, committed_team_id="team-id"),
    ],
)
def test_committed_review_sessions_are_immutable(review_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        dashboard._ensure_review_session_mutable(review_session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "session_already_committed"


def test_all_review_mutation_endpoints_apply_the_immutability_guard() -> None:
    endpoints = (
        dashboard.edit_registration_review_session,
        dashboard.reprocess_registration_review_session,
        dashboard.append_assets_to_registration_review_session,
    )

    for endpoint in endpoints:
        assert "_ensure_review_session_mutable" in inspect.getsource(endpoint)


@pytest.mark.asyncio
async def test_team_list_keeps_web_teams_without_a_telegram_chat_id(
    monkeypatch,
) -> None:
    web_team = SimpleNamespace(
        id="team-id",
        name="Equipo Web",
        category="Libre",
        gender="Femenil",
        state="Jalisco",
        telegram_chat_id=None,
        created_at=datetime(2026, 1, 1, 12, 0),
    )

    class _ScalarResult:
        def scalars(self):
            return self

        def all(self):
            return [web_team]

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return _ScalarResult()

    class _FakeDB:
        def __init__(self, _session):
            pass

        async def get_players_by_team(self, _team_id):
            return []

    captured = {}

    def _template_response(name, context):
        captured["name"] = name
        captured["context"] = context
        return context

    monkeypatch.setattr(
        dashboard,
        "_ensure_legacy_copa_dashboard_access",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: _FakeSession())
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _FakeDB)
    monkeypatch.setattr(dashboard.templates, "TemplateResponse", _template_response)

    await dashboard.list_teams(SimpleNamespace())

    assert captured["name"] == "teams.html"
    assert captured["context"]["teams"][0]["name"] == "Equipo Web"
