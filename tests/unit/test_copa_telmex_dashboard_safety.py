from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

os.environ.setdefault(
    "SESSION_SECRET_KEY",
    "test-only-session-secret-key-0123456789abcdef",
)

import copa_telmex_dashboard as dashboard  # noqa: E402


def test_default_cors_origins_are_https_only(monkeypatch) -> None:
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("ALLOWED_APP_ORIGINS", raising=False)

    origins = dashboard._build_allowed_origins()

    assert origins == ["https://sam.chat", "https://www.sam.chat"]


def test_explicit_cors_origin_can_enable_local_development(monkeypatch) -> None:
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setenv("ALLOWED_APP_ORIGINS", "http://localhost:5173")

    assert "http://localhost:5173" in dashboard._build_allowed_origins()


@pytest.mark.asyncio
async def test_html_middleware_preserves_body_and_repeated_cookies() -> None:
    async def _chunks():
        yield b"<html><head></head><body>ok</body></html>"

    async def _call_next(_request):
        response = StreamingResponse(_chunks(), media_type="text/html")
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        return response

    response = await dashboard.modernize_html_middleware(None, _call_next)
    body = response.body.decode("utf-8")
    cookie_headers = [
        value for name, value in response.raw_headers if name.lower() == b"set-cookie"
    ]

    assert "<body>ok</body>" in body
    assert "samchat-modern-theme" in body
    assert len(cookie_headers) == 2


def test_player_page_map_is_remapped_after_deduplication() -> None:
    def _page(page_index, players):
        return {
            "extraction": {
                "team": {"name": "Equipo Prueba", "confidence": 0.9},
                "manager": None,
                "players": players,
                "overall_confidence": 0.9,
                "notes": "",
            },
            "raw": {},
            "asset": {"page_index": page_index, "width": 1000, "height": 1000},
            "provider": "test",
        }

    merged, _raw, _provider, layout = dashboard._merge_review_extractions(
        [
            _page(
                1,
                [
                    {"name": "Ana", "birth_date": "01/01/2001", "confidence": 0.4},
                    {"name": "Bea", "birth_date": "02/02/2002", "confidence": 0.8},
                ],
            ),
            _page(
                2,
                [
                    {"name": "Ana", "birth_date": "01/01/2001", "confidence": 0.9},
                    {"name": "Carla", "birth_date": "03/03/2003", "confidence": 0.7},
                ],
            ),
        ]
    )

    assert [player["name"] for player in merged["players"]] == ["Ana", "Bea", "Carla"]
    assert layout["player_page_map"] == {"1": 2, "2": 1, "3": 2}


def test_internal_errors_do_not_echo_exception_details() -> None:
    with pytest.raises(HTTPException) as exc_info:
        try:
            raise RuntimeError("private database value")
        except RuntimeError:
            dashboard._raise_dashboard_internal_error("test operation")

    assert exc_info.value.status_code == 500
    assert "private database value" not in str(exc_info.value.detail)


def test_review_template_guards_reprocess_and_does_not_force_new_tabs() -> None:
    template = (
        Path(dashboard.__file__).parent
        / "templates"
        / "registration_review_detail.html"
    ).read_text(encoding="utf-8")

    assert 'onsubmit="return confirm(' in template
    assert "if (event.ctrlKey || event.metaKey)" in template
    assert "window.open(link.href" not in template


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


def test_rejected_review_session_cannot_commit_until_reopened() -> None:
    review_session = SimpleNamespace(status="rejected")

    with pytest.raises(HTTPException) as exc_info:
        dashboard._ensure_review_session_not_rejected(review_session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "session_rejected"


class _DecisionResult:
    def __init__(self, review_session) -> None:
        self.review_session = review_session

    def scalar_one_or_none(self):
        return self.review_session


class _DecisionSession:
    def __init__(self, review_session) -> None:
        self.review_session = review_session
        self.statements = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return _DecisionResult(self.review_session)

    async def commit(self) -> None:
        self.commits += 1


class _DecisionRequest:
    async def form(self):
        return {"reason": "Documento incompleto"}


def _decision_review_session(status: str):
    committed = status == "committed"
    return SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        status=status,
        committed_at=datetime(2026, 1, 1) if committed else None,
        committed_team_id="team-id" if committed else None,
        draft=SimpleNamespace(validation={}, needs_review=False),
        assets=[],
    )


def _configure_decision_route(monkeypatch, sessions) -> None:
    pending_sessions = list(sessions)
    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {"user_id": "operator-id", "role": "admin"},
    )
    monkeypatch.setattr(
        dashboard, "async_session_maker", lambda: pending_sessions.pop(0)
    )
    monkeypatch.setattr(
        dashboard, "_log_registration_review_event", lambda *_args, **_kwargs: None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "status", "expected_error"),
    [
        (
            dashboard.reject_registration_review_session,
            "committed",
            "session_already_committed",
        ),
        (
            dashboard.commit_registration_review_session,
            "committed",
            "session_already_committed",
        ),
        (dashboard.commit_registration_review_session, "rejected", "session_rejected"),
    ],
)
async def test_decision_routes_lock_before_rejecting_terminal_states(
    monkeypatch, endpoint, status, expected_error
) -> None:
    decision_session = _DecisionSession(_decision_review_session(status))
    _configure_decision_route(monkeypatch, [decision_session])

    with pytest.raises(HTTPException) as exc_info:
        await endpoint(str(decision_session.review_session.id), _DecisionRequest())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == expected_error
    assert decision_session.commits == 0
    assert decision_session.statements[0]._for_update_arg is not None


@pytest.mark.asyncio
async def test_reject_then_commit_conflict_preserves_single_terminal_decision(
    monkeypatch,
) -> None:
    review_session = _decision_review_session("ready")
    reject_session = _DecisionSession(review_session)
    commit_session = _DecisionSession(review_session)
    _configure_decision_route(monkeypatch, [reject_session, commit_session])

    response = await dashboard.reject_registration_review_session(
        str(review_session.id), _DecisionRequest()
    )

    assert response.status_code == 303
    assert review_session.status == "rejected"
    assert (
        review_session.draft.validation["audit"]["rejection_events"][0]["reason"]
        == "Documento incompleto"
    )
    assert reject_session.commits == 1
    assert reject_session.statements[0]._for_update_arg is not None

    with pytest.raises(HTTPException) as exc_info:
        await dashboard.commit_registration_review_session(
            str(review_session.id), _DecisionRequest()
        )

    assert exc_info.value.detail["error"] == "session_rejected"
    assert commit_session.commits == 0
    assert commit_session.statements[0]._for_update_arg is not None


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
