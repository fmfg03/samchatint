"""Signed-session reconciliation review and transaction-boundary coverage."""

import base64
import json
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import devnous.gastos  # noqa: F401
from devnous.gastos.routes import admin_budget_routes as routes
from devnous.gastos.routes import admin_routes
from devnous.gastos.routes.admin_budget_ui import render_budget_reconciliation_preview

VERSION, TOURNAMENT, LINE, ACTOR = (str(uuid4()) for _ in range(4))
KEY = "expense:" + str(uuid4())
HASH = "a" * 64


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden":
            self.fields[values["name"]] = values.get("value", "")


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(
        actor=SimpleNamespace(id=ACTOR, rol="superadmin"),
        capabilities={"read", "line_update"},
        authenticated=True,
        clock=1000.0,
        trace=[],
        calls=[],
        receipt=None,
        error=None,
        fail_commit=False,
    )

    class CanonicalEngine:
        pass

    auth = SimpleNamespace(
        bind=CanonicalEngine(), commit=AsyncMock(), rollback=AsyncMock()
    )

    class Transaction:
        async def __aenter__(self):
            state.trace.append("begin")

        async def __aexit__(self, kind, value, traceback):
            state.trace.append("rollback" if kind else "commit")
            if not kind and state.fail_commit:
                state.fail_commit = False
                raise RuntimeError("commit acknowledgement interrupted")

    class Work:
        def __init__(self, **kwargs):
            assert kwargs == {"bind": auth.bind, "expire_on_commit": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            state.trace.append("closed")

        def begin(self):
            return Transaction()

        async def execute(self, statement):
            state.trace.append(str(statement))

    async def get_actor():
        if not state.authenticated:
            raise HTTPException(401, "login required")
        return state.actor

    async def get_session():
        return auth

    def guard(actor, capability):
        if capability not in state.capabilities:
            raise HTTPException(403, "denied")

    async def core(action, session, **kwargs):
        assert isinstance(session, Work)
        state.calls.append((action, kwargs))
        state.trace.append("core:" + action)
        if state.error:
            raise state.error
        if action == "preview":
            return {
                "plan_sha256": HASH,
                "assignments": [
                    {
                        **kwargs["assignments"][0],
                        "reference": "S-26000123",
                        "concept_name": "Hospedaje",
                        "kind": "pending_accounting",
                        "amount": "116.00",
                        "phase": "Nacional",
                        "source_identity": {"private_financial_row": "DO NOT EXPOSE"},
                    }
                ],
                "impact": {"changed": 1},
            }
        if action == "apply":
            state.receipt = {"status": "APPLIED", "audit_id": "audit-1"}
            return state.receipt
        return {**state.receipt, "status": "ALREADY_APPLIED"} if state.receipt else None

    monkeypatch.setattr(admin_routes, "get_db_session", get_session)
    monkeypatch.setattr(admin_routes, "get_current_empleado", get_actor)
    monkeypatch.setattr(admin_routes, "_require_budget_access", guard)
    monkeypatch.setattr(
        admin_routes,
        "_budget_access_map",
        lambda _actor: {key: True for key in state.capabilities},
    )
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *_a, **_k: "")
    monkeypatch.setattr(routes, "AsyncEngine", CanonicalEngine)
    monkeypatch.setattr(routes, "AsyncSession", Work)
    monkeypatch.setattr(routes, "_call_reconciliation_core", core)
    monkeypatch.setattr(routes, "time", SimpleNamespace(time=lambda: state.clock))
    monkeypatch.setattr(
        routes,
        "upsert_budget_line_for_concept",
        AsyncMock(side_effect=AssertionError("must not create or edit lines")),
    )
    router = APIRouter()
    routes.register_presupuestos_routes(router)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-key")
    app.include_router(router)

    @app.get("/test/seed")
    async def seed(request: Request):
        return {"token": routes._budget_reconciliation_csrf_token(request)}

    state.client = TestClient(app)
    state.token = state.client.get("/test/seed").json()["token"]
    state.auth = auth
    return state


def payload(h, **kwargs):
    return {
        "_csrf_token": h.token,
        "movement_key": KEY,
        "budget_line_id": LINE,
        "tournament_id": TOURNAMENT,
        "edition_year": "2026",
        "reason": "Vincular la partida existente",
        "tournament_key": "LTTB",
        "phase_filter": "Nacional",
        **kwargs,
    }


def preview(h, **kwargs):
    return h.client.post(
        f"/admin/presupuestos/versiones/{VERSION}/conciliacion/preview",
        data=payload(h, **kwargs),
        follow_redirects=False,
    )


def confirmation(h, **kwargs):
    response = preview(h)
    assert response.status_code == 200
    return {**Inputs(response.text).fields, "confirmed": "true", **kwargs}


def confirm(h, data):
    return h.client.post(
        f"/admin/presupuestos/versiones/{VERSION}/conciliacion/confirm",
        data=data,
        follow_redirects=False,
    )


def cookie_state(h):
    raw = h.client.cookies.get("session").split(".", 1)[0]
    return json.loads(base64.b64decode(raw))


def test_preview_is_readonly_fresh_same_engine_and_hides_source_rows(harness):
    h = harness
    response = preview(h)
    assert response.status_code == 200
    assert [x[0] for x in h.calls] == ["preview"]
    assert h.trace[:2] == [
        "begin",
        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY",
    ]
    assert h.trace[-2:] == ["commit", "closed"]
    h.auth.commit.assert_not_awaited()
    h.auth.rollback.assert_not_awaited()
    assert "DO NOT EXPOSE" not in response.text
    assert "Pendiente de contabilización" in response.text
    cookie = json.dumps(cookie_state(h))
    assert "116.00" not in cookie and "Hospedaje" not in cookie
    assert "Vincular la partida" not in cookie and "private_financial_row" not in cookie
    assert h.calls[0][1]["actor_empleado_id"] == ACTOR
    assert h.calls[0][1]["assignments"] == [
        {
            "tournament_id": TOURNAMENT,
            "movement_key": KEY,
            "budget_line_id": LINE,
        }
    ]


def test_confirm_keeps_context_and_records_success_without_auth_session_commit(harness):
    h = harness
    data = confirmation(h)
    response = confirm(h, data)
    assert response.status_code == 303
    assert "phase_filter=Nacional" in response.headers["location"]
    assert "version_id=" + VERSION in response.headers["location"]
    assert "estados%20de%20pago" in response.headers["location"]
    assert h.calls[-1][0] == "apply"
    assert h.calls[-1][1]["expected_plan_sha256"] == HASH
    assert "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE" in h.trace
    h.auth.commit.assert_not_awaited()
    h.auth.rollback.assert_not_awaited()


@pytest.mark.parametrize("csrf", ["", "wrong", "x" * 300, "á"])
def test_bad_csrf_rejected_before_core(harness, csrf):
    response = preview(harness, _csrf_token=csrf)
    assert response.status_code == 403
    assert not harness.calls and not harness.trace


@pytest.mark.parametrize(
    "capabilities,authenticated,status",
    [
        ({"read"}, True, 403),
        ({"line_update"}, False, 401),
    ],
)
def test_existing_role_and_authentication_are_required(
    harness, capabilities, authenticated, status
):
    h = harness
    h.capabilities = capabilities
    h.authenticated = authenticated
    assert preview(h).status_code == status
    assert not h.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("movement_key", "expense:" + str(uuid4())),
        ("budget_line_id", str(uuid4())),
        ("tournament_id", str(uuid4())),
        ("edition_year", "2025"),
        ("reason", "Otro motivo"),
        ("expected_plan_sha256", "b" * 64),
        ("confirmation_nonce", "another-nonce"),
        ("confirmation_nonce", "é"),
        ("confirmation_nonce", "x" * 300),
        ("tournament_key", "other-project"),
        ("phase_filter", "Otra fase"),
        ("confirmed", "false"),
    ],
)
def test_modified_confirmation_cannot_reach_writer(harness, field, value):
    h = harness
    data = confirmation(h)
    data[field] = value
    assert confirm(h, data).status_code == 409
    assert [x[0] for x in h.calls] == ["preview"]


def test_another_actor_and_another_session_cannot_confirm(harness):
    h = harness
    data = confirmation(h)
    h.actor = SimpleNamespace(id=str(uuid4()))
    assert confirm(h, data).status_code == 409
    h.actor = SimpleNamespace(id=ACTOR)
    h.client.cookies.clear()
    assert confirm(h, data).status_code == 403
    assert [x[0] for x in h.calls] == ["preview"]


def test_new_preview_invalidates_previous_nonce(harness):
    h = harness
    old = confirmation(h)
    confirmation(h)
    assert confirm(h, old).status_code == 409
    assert [x[0] for x in h.calls] == ["preview", "preview"]


def test_expired_unapplied_plan_only_attempts_readonly_receipt_recovery(harness):
    h = harness
    data = confirmation(h)
    h.clock += 901
    response = confirm(h, data)
    assert response.status_code == 303
    assert "error_msg=" in response.headers["location"]
    assert [x[0] for x in h.calls] == ["preview", "recover"]
    assert h.trace.count("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY") == 2
    assert h.receipt is None


def test_expired_lost_response_recovers_prior_receipt_without_renewing_nonce(harness):
    h = harness
    data = confirmation(h)
    before = cookie_state(h)[routes._RECONCILIATION_PENDING_KEY]
    h.receipt = {"status": "APPLIED", "audit_id": "already-committed"}
    h.clock += 901
    assert confirm(h, data).status_code == 303
    after = cookie_state(h)[routes._RECONCILIATION_PENDING_KEY]
    assert after["issued_at"] == before["issued_at"]
    assert after["nonce"] == before["nonce"]
    assert after["completed"] is True
    assert [x[0] for x in h.calls] == ["preview", "recover"]


def test_successful_repeat_is_receipt_read_without_new_apply(harness):
    h = harness
    data = confirmation(h)
    assert confirm(h, data).status_code == 303
    assert confirm(h, data).status_code == 303
    assert [x[0] for x in h.calls] == ["preview", "apply", "recover"]


@pytest.mark.parametrize(
    "error,status",
    [
        (ValueError("Cambió la fuente"), 303),
        (PermissionError("actor revoked"), 403),
        (RuntimeError("sensitive-row-value"), 303),
    ],
)
def test_failure_rolls_back_fresh_work_and_never_logs_or_commits_auth(
    harness, error, status, caplog
):
    h = harness
    data = confirmation(h)
    h.error = error
    response = confirm(h, data)
    assert response.status_code == status
    assert h.trace[-2:] == ["rollback", "closed"]
    assert "sensitive-row-value" not in caplog.text
    h.auth.commit.assert_not_awaited()
    h.auth.rollback.assert_not_awaited()
    assert cookie_state(h)[routes._RECONCILIATION_PENDING_KEY]["recover_only"]


def test_commit_acknowledgement_failure_allows_only_receipt_recovery(harness):
    h = harness
    data = confirmation(h)
    h.fail_commit = True
    assert confirm(h, data).status_code == 303
    assert cookie_state(h)[routes._RECONCILIATION_PENDING_KEY]["recover_only"]
    assert confirm(h, data).status_code == 303
    assert [x[0] for x in h.calls] == ["preview", "apply", "recover"]


def test_invalid_bind_fails_closed_without_touching_auth_transaction(harness):
    h = harness
    h.auth.bind = object()
    assert preview(h).status_code == 303
    assert not h.calls and not h.trace
    h.auth.commit.assert_not_awaited()
    h.auth.rollback.assert_not_awaited()


def test_preview_domain_error_and_private_failure_are_safe(harness, caplog):
    h = harness
    h.error = ValueError("No existe línea elegible")
    assert preview(h).status_code == 303
    h.error = RuntimeError("private_financial_row")
    assert preview(h).status_code == 303
    assert "private_financial_row" not in caplog.text


def test_preview_escapes_all_display_and_hidden_fields():
    intent = {
        "budget_version_id": VERSION,
        "edition_year": 2026,
        "assignments": [
            {"tournament_id": TOURNAMENT, "movement_key": KEY, "budget_line_id": LINE}
        ],
        "reason": '"><script>alert(1)</script>',
    }
    plan = {
        "plan_sha256": HASH,
        "assignments": [
            {
                "reference": '<img src=x onerror="alert(1)">',
                "concept_name": "<script>bad</script>",
                "phase": "<b>Phase</b>",
                "amount": None,
                "kind": "pending_accounting",
                "source_identity": {"secret": "NOT-HTML"},
            }
        ],
    }
    html = render_budget_reconciliation_preview(
        plan,
        intent=intent,
        return_context={"tournament_key": "LTTB", "phase_filter": ""},
        csrf_token="token",
        nonce="nonce",
        cancel_url='/admin/presupuestos?x="bad"',
    )
    assert "<script>" not in html and "<img " not in html
    assert "NOT-HTML" not in html
    assert "&lt;script&gt;" in html and "Pendiente de evidencia" in html
    assert Inputs(html).fields["reason"] == intent["reason"]


@pytest.mark.parametrize(
    "status,source,write,governed,expected",
    [
        ("approved", "manual", True, False, True),
        ("approved", "manual", False, False, False),
        ("frozen", "manual", True, False, False),
        ("approved", "distribution_revision", True, False, False),
        ("approved", "manual", True, True, False),
    ],
)
def test_real_tournament_get_separates_reconciliation_from_editing(
    harness,
    monkeypatch,
    status,
    source,
    write,
    governed,
    expected,
):
    from devnous.gastos.services import tournament_authority_service as authority
    from samchat.budgets import tournament_weekly_overlay

    h = harness
    if not write:
        h.capabilities = {"read"}
    cid = str(uuid4())
    lines = [
        {
            "id": LINE,
            "budget_concept_id": cid,
            "concept_name": "Hospedaje",
            "phase": "Nacional",
            "line_direction": "expense",
            "budget_amount": 116,
            "tournament_id": TOURNAMENT,
        },
        {
            "id": str(uuid4()),
            "budget_concept_id": cid,
            "concept_name": "Hospedaje",
            "phase": "Estatal",
            "line_direction": "expense",
            "budget_amount": 50,
            "tournament_id": TOURNAMENT,
        },
    ]
    result = SimpleNamespace(
        scalar_one_or_none=lambda: None,
        scalars=lambda: SimpleNamespace(all=lambda: []),
    )
    h.auth.execute = AsyncMock(return_value=result)
    version = {
        "id": VERSION,
        "status": status,
        "source": source,
        "edition_year": 2026,
        "version_name": "Presupuesto autorizado",
    }
    monkeypatch.setattr(routes, "ensure_budget_schema", AsyncMock())
    monkeypatch.setattr(
        routes,
        "resolve_budget_tournament_context",
        AsyncMock(
            return_value={
                "tournament_id": TOURNAMENT,
                "tournament_code": "LTTB",
                "tournament_name": "Liga",
            }
        ),
    )
    monkeypatch.setattr(
        routes, "list_budget_versions", AsyncMock(return_value=[version])
    )

    async def list_lines(_session, **kwargs):
        return lines if kwargs["line_direction"] == "expense" else []

    async def attach(_session, values):
        return values

    monkeypatch.setattr(routes, "list_budget_lines", list_lines)
    monkeypatch.setattr(routes, "attach_cuenta_contable_to_budget_lines", attach)
    monkeypatch.setattr(
        routes, "list_monthly_plan_for_lines", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tournament_weekly_overlay,
        "effective_plans_for_lines",
        AsyncMock(return_value=({}, set())),
    )
    monkeypatch.setattr(
        routes,
        "build_budget_actuals_snapshot",
        AsyncMock(return_value={"monthly": {}, "movements": []}),
    )
    monkeypatch.setattr(
        routes, "build_budget_monthly_plan_rollups", AsyncMock(return_value={})
    )
    concepts = AsyncMock(return_value=[{"id": cid}])
    monkeypatch.setattr(routes, "list_budget_concepts", concepts)
    guard = AsyncMock(
        side_effect=(
            authority.GovernedGastosProjectError("synthetic", 1, "a" * 64)
            if governed
            else None
        )
    )
    monkeypatch.setattr(authority, "require_ungoverned_gastos_project", guard)
    captured = []

    def matrix(values, **kwargs):
        captured.append((values, kwargs))
        return "<div>Matrix</div>"

    monkeypatch.setattr(routes, "render_budget_partida_matrix", matrix)
    response = h.client.get(
        "/admin/presupuestos/torneo/LTTB?edition_year=2026"
        f"&version_id={VERSION}&phase_filter=Nacional"
    )
    assert response.status_code == 200
    expenses, income = captured
    assert expenses[1]["can_reconcile"] is expected
    assert expenses[1]["can_edit"] is False
    assert len(expenses[0]) == 1
    assert expenses[1]["reconciliation_lines"] == lines
    assert bool(expenses[1]["reconciliation_csrf_token"]) is expected
    assert not income[1].get("can_reconcile", False)
    if expected:
        assert expenses[1]["active_concept_ids"] == {cid}
        guard.assert_awaited_once_with(h.auth, TOURNAMENT)
    if status == "frozen" or source == "distribution_revision":
        concepts.assert_not_awaited()
    h.auth.commit.assert_not_awaited()
    h.auth.rollback.assert_not_awaited()
