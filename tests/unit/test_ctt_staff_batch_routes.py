"""Public batch boundary uses current sessions; no private authority is faked."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.copa_telmex.staff_batch_routes import (
    CSRF_HEADER,
    MAX_BODY_BYTES,
    register_staff_batch_routes,
)

ORIGIN = "https://sam.chat"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def boundary():
    account, run, block, attempt, edition, member = [uuid4() for _ in range(6)]
    state = SimpleNamespace(
        calls=[],
        open_sessions=0,
        configured=True,
        active=True,
        actor_changed=False,
        foreign=False,
        failure=None,
        response_extra={},
        member_status="exception",
    )

    class Denied(Exception):
        reason_code = "SCOPE_DENIED"

    class Client:
        def record(self, method, actor, *arguments):
            assert state.open_sessions == 0
            assert actor == account
            state.calls.append((method, arguments))
            if state.failure:
                raise state.failure
            if state.foreign:
                raise Denied()

        async def catalog(self, actor):
            self.record("catalog", actor)
            return {
                "source_manifests": [
                    {
                        "manifest_sha256": "a" * 64,
                        "edition_id": str(edition),
                        "label": "Fuentes sintéticas",
                        "pdf_count": 1,
                        "document_count": 5,
                    }
                ],
                "runs": [],
            }

        async def create_run(self, actor, payload):
            self.record("create_run", actor, payload)
            return {"run_id": str(run), "status": "preparing"}

        async def view(self, actor, requested, offset=0, limit=50):
            self.record("view", actor, requested, offset, limit)
            if requested != run:
                raise Denied()
            return {
                "run_id": str(run),
                "version": 1,
                "status": "ready",
                "members": [],
                "blocks": [],
                "counts": {},
                **state.response_extra,
            }

        async def source_page(self, actor, requested, document, page):
            self.record("source_page", actor, requested, document, page)
            return b"\x89PNG\r\n\x1a\nsynthetic"

        async def member(self, actor, requested_run, requested_member):
            self.record("member", actor, requested_run, requested_member)
            return {
                "run_id": str(requested_run),
                "member_id": str(requested_member),
                "version": 3,
                "status": state.member_status,
                "document_id": "b" * 64,
                "team_name": "Synthetic Team",
                "fields": [
                    {
                        "field_id": "players.1.first_name",
                        "label": "Nombre",
                        "value": "Synthetic",
                        "editable": state.member_status == "exception",
                        "source_page_index": 1,
                    }
                ],
                "source_pages": [{"page_index": 1}],
                **state.response_extra,
            }

        async def propose_corrections(
            self, actor, requested_run, requested_member, payload
        ):
            self.record(
                "propose_corrections", actor, requested_run, requested_member, payload
            )
            if state.member_status != "exception":

                class FalseAdmission(Exception):
                    reason_code = "QUALITY_FALSE_ADMISSION_REPORT_REQUIRED"

                raise FalseAdmission()
            return {
                "run_id": str(requested_run),
                "member_id": str(requested_member),
                "version": 4,
                "status": "analysis_pending",
                **state.response_extra,
            }

        async def report_quality_issue(
            self, actor, requested_run, requested_member, payload
        ):
            self.record(
                "report_quality_issue", actor, requested_run, requested_member, payload
            )
            return {
                "run_id": str(requested_run),
                "member_id": str(requested_member),
                "version": 4,
                "status": "suspended",
                **state.response_extra,
            }

        async def attempt(self, actor, selected, selected_attempt):
            self.record("attempt", actor, selected, selected_attempt)
            return {
                "block_id": str(selected),
                "attempt_id": str(selected_attempt),
                "status": "uncertain",
                **state.response_extra,
            }

        def __getattr__(self, method):
            async def execute(actor, requested, payload):
                self.record(method, actor, requested, payload)
                return {
                    "run_id": str(run),
                    "block_id": str(block),
                    "version": 2,
                    "status": "prepared",
                    **state.response_extra,
                }

            return execute

    class Session:
        async def __aenter__(self):
            state.open_sessions += 1
            return self

        async def __aexit__(self, *_):
            state.open_sessions -= 1

    def access(request):
        if not request.session.get("empleado_id"):
            raise HTTPException(status_code=401)
        if request.session.get("rol") != "coordinador":
            raise HTTPException(status_code=403)

    async def refresh(_, actor):
        if not state.active:
            raise HTTPException(status_code=403)
        return {"user_id": str(uuid4()) if state.actor_changed else actor["user_id"]}

    service = Client()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated-batch-test-key")

    @app.get("/_test/login")
    def login(request: Request, role: str = "coordinador", impersonated: bool = False):
        request.session["empleado_id"] = str(account)
        request.session["rol"] = role
        if impersonated:
            request.session["impersonator_empleado_id"] = str(uuid4())
        return {"ok": True}

    dependencies = dict(
        ensure_access=access,
        actor_from_request=lambda request: {"user_id": request.session["empleado_id"]},
        refresh_actor=refresh,
        session_factory=Session,
        client_resolver=lambda _: service if state.configured else None,
        expected_origin=ORIGIN,
    )
    register_staff_batch_routes(
        app,
        templates=Jinja2Templates(directory=str(ROOT / "templates")),
        **dependencies,
    )
    client = TestClient(app, base_url=ORIGIN)
    client.get("/_test/login")
    response = client.get("/api/registration-review-batches/catalog")
    assert response.status_code == 200
    token = response.json()["batch_review_csrf"]
    state.calls.clear()
    return SimpleNamespace(
        client=client,
        state=state,
        token=token,
        run=str(run),
        block=str(block),
        attempt=str(attempt),
        edition=str(edition),
        member=str(member),
        dependencies=dependencies,
    )


def post(b, suffix, payload, **kwargs):
    return b.client.post(
        "/api/registration-review-batches" + suffix,
        json=payload,
        headers={"Origin": ORIGIN, CSRF_HEADER: b.token},
        **kwargs,
    )


def cases(b):
    return [
        (
            "/runs",
            {
                "edition_id": b.edition,
                "source_manifest_hashes": ["a" * 64],
                "idempotency_key": str(uuid4()),
            },
        ),
        (
            f"/runs/{b.run}/sample",
            {
                "expected_run_version": 1,
                "observations": [
                    {
                        "document_id": "b" * 64,
                        "outcome": "matches",
                        "notes": "Contraste sintético",
                    }
                ],
            },
        ),
        (
            f"/runs/{b.run}/prepare",
            {
                "expected_run_version": 1,
                "member_ids": [b.member],
                "idempotency_key": str(uuid4()),
            },
        ),
        (
            f"/blocks/{b.block}/challenge",
            {"expected_block_version": 1, "confirmed": True},
        ),
        (
            f"/blocks/{b.block}/assertion",
            {"attempt_id": b.attempt, "assertion": {"id": "synthetic", "response": {}}},
        ),
        *[
            (
                f"/blocks/{b.block}/{action}",
                {"expected_version": 1, "idempotency_key": str(uuid4())},
            )
            for action in ("pause", "resume", "revoke")
        ],
    ]


def test_catalog_and_run_reads_refresh_actor_without_any_write(boundary):
    b = boundary
    assert (
        b.client.get(
            f"/api/registration-review-batches/runs/{b.run}?offset=50&limit=25"
        ).status_code
        == 200
    )
    assert b.state.calls == [("view", (UUID(b.run), 50, 25))]
    b.state.calls.clear()
    response = b.client.get(
        f"/api/registration-review-batches/blocks/{b.block}/attempts/{b.attempt}"
    )
    assert response.status_code == 200 and b.state.calls[0][0] == "attempt"
    assert response.headers["cache-control"] == "no-store"


def test_every_mutation_forwards_only_proposed_values_and_current_account(boundary):
    for suffix, payload in cases(boundary):
        assert post(boundary, suffix, payload).status_code == 200
    assert [method for method, _ in boundary.state.calls] == [
        "create_run",
        "sample",
        "prepare",
        "challenge",
        "assertion",
        "pause",
        "resume",
        "revoke",
    ]


@pytest.mark.parametrize(
    "extra",
    ["approved", "verified_by_human", "principal_id", "policy_hash", "child_proof"],
)
def test_browser_cannot_supply_private_approval_or_identity_flags(boundary, extra):
    for suffix, payload in cases(boundary):
        assert post(boundary, suffix, {**payload, extra: True}).status_code == 400
    assert boundary.state.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://other.invalid"},
        {"Origin": "https://sam.chat.evil.invalid"},
        {"Origin": ORIGIN, "X-CTT-CSRF": "legacy-token"},
    ],
)
def test_origin_and_batch_csrf_required_before_private_calls(boundary, headers):
    suffix, payload = cases(boundary)[0]
    response = boundary.client.post(
        "/api/registration-review-batches" + suffix, json=payload, headers=headers
    )
    assert response.status_code == 403
    assert boundary.state.calls == []


@pytest.mark.parametrize(
    "mode",
    [
        "anonymous",
        "wrong_role",
        "impersonated",
        "inactive",
        "actor_changed",
        "unconfigured",
    ],
)
def test_navigation_and_current_account_guard_all_surfaces(boundary, mode):
    b = boundary
    if mode == "anonymous":
        b.client.cookies.clear()
    elif mode == "wrong_role":
        b.client.get("/_test/login?role=admin")
    elif mode == "impersonated":
        b.client.get("/_test/login?impersonated=true")
    elif mode == "inactive":
        b.state.active = False
    elif mode == "actor_changed":
        b.state.actor_changed = True
    else:
        b.state.configured = False
    for path in (
        "/api/registration-review-batches/catalog",
        f"/api/registration-review-batches/runs/{b.run}",
        "/registration-review-batches/new",
        f"/registration-review-batches/{b.run}",
    ):
        assert b.client.get(path).status_code in {401, 403, 503}
    assert b.state.calls == []


def test_private_scope_denial_and_errors_never_leak_personal_details(boundary):
    boundary.state.foreign = True
    response = boundary.client.get(f"/api/registration-review-batches/runs/{uuid4()}")
    assert response.status_code == 403
    boundary.state.foreign = False
    boundary.state.failure = RuntimeError("SECRET_PII_FROM_BACKEND")
    response = boundary.client.get(
        f"/api/registration-review-batches/runs/{boundary.run}"
    )
    assert response.status_code == 503 and "SECRET_PII" not in response.text


@pytest.mark.parametrize(
    "extra",
    [
        {"run_id": str(uuid4())},
        {"proof": "private-proof"},
        {"consent_proof": "retained-private-consent"},
        {"nested": {"child_proof": "private"}},
        *[
            {"nested": {key: "must-never-render"}}
            for key in (
                "parent_proof_bytes",
                "verification_proof_bytes",
                "root_bytes",
                "scope_bytes",
                "gate_envelope",
                "preauth_bytes",
                "proof_base64",
                "Parent_Proof_Bytes",
                "signed_review",
                "signing_seed",
            )
        ],
        {"nan": float("nan")},
    ],
)
def test_private_response_must_match_object_and_contain_no_internal_proof(
    boundary, extra
):
    boundary.state.response_extra = extra
    assert (
        boundary.client.get(
            f"/api/registration-review-batches/runs/{boundary.run}"
        ).status_code
        == 503
    )


def test_source_preview_is_scoped_private_png(boundary):
    response = boundary.client.get(
        f"/api/registration-review-batches/runs/{boundary.run}"
        f"/sources/{'b' * 64}/pages/1"
    )
    assert (
        response.status_code == 200 and response.headers["content-type"] == "image/png"
    )
    assert response.headers["cache-control"] == "no-store"
    assert boundary.state.calls[0][0] == "source_page"
    assert (
        boundary.client.get(
            f"/api/registration-review-batches/runs/{boundary.run}"
            f"/sources/{'b' * 64}/pages/5"
        ).status_code
        == 400
    )


def test_source_preview_refuses_non_png(boundary):
    async def bad(*_):
        return b"secret-text"

    client = boundary.dependencies["client_resolver"](None)
    client.source_page = bad
    response = boundary.client.get(
        f"/api/registration-review-batches/runs/{boundary.run}"
        f"/sources/{'b' * 64}/pages/1"
    )
    assert response.status_code == 503 and "secret-text" not in response.text


@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_versions_are_exact_nonnegative_integers(boundary, value):
    assert (
        post(
            boundary,
            f"/blocks/{boundary.block}/challenge",
            {"expected_block_version": value, "confirmed": True},
        ).status_code
        == 400
    )
    assert boundary.state.calls == []


def test_sample_outcome_requires_allowed_string(boundary):
    payload = {
        "expected_run_version": 1,
        "observations": [{"document_id": "b" * 64, "outcome": {}, "notes": ""}],
    }
    assert post(boundary, f"/runs/{boundary.run}/sample", payload).status_code == 400


@pytest.mark.parametrize(
    "raw",
    [
        '{"expected_block_version":1,"confirmed":true,"confirmed":false}',
        '{"expected_block_version":NaN,"confirmed":true}',
        "[" * 18 + "0" + "]" * 18,
    ],
)
def test_malformed_duplicate_or_nested_json_rejected(boundary, raw):
    response = boundary.client.post(
        f"/api/registration-review-batches/blocks/{boundary.block}/challenge",
        content=raw,
        headers={
            "Origin": ORIGIN,
            CSRF_HEADER: boundary.token,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400 and boundary.state.calls == []


def test_oversized_requests_and_pagination_fail_closed(boundary):
    response = boundary.client.post(
        "/api/registration-review-batches/runs",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={
            "Origin": ORIGIN,
            CSRF_HEADER: boundary.token,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413
    for query in ("offset=-1", "limit=101", "limit=0"):
        assert (
            boundary.client.get(
                f"/api/registration-review-batches/runs/{boundary.run}?{query}"
            ).status_code
            == 400
        )
    assert boundary.state.calls == []


def test_html_creation_and_run_pages_use_private_initial_view_and_scripts(boundary):
    for path in (
        "/registration-review-batches/new",
        f"/registration-review-batches/{boundary.run}",
    ):
        response = boundary.client.get(path)
        assert response.status_code == 200
        assert (
            "data-batch-review" in response.text
            and "ctt_batch_review.js?v=" in response.text
        )
        assert response.headers["cache-control"] == "no-store"


def test_member_read_and_corrections_are_scoped_proposals_without_authority(boundary):
    b = boundary
    base = f"/runs/{b.run}/members/{b.member}"
    response = b.client.get("/api/registration-review-batches" + base)
    assert response.status_code == 200 and response.json()["member_id"] == b.member
    payload = {
        "expected_version": 3,
        "edits": [{"field_id": "players.1.first_name", "value": "Source value"}],
    }
    response = post(b, base + "/corrections", payload)
    assert (
        response.status_code == 200 and response.json()["status"] == "analysis_pending"
    )
    assert [method for method, _ in b.state.calls] == ["member", "propose_corrections"]
    forwarded = b.state.calls[-1][1][-1]
    assert forwarded == payload
    assert "verified_by_human" not in str(forwarded) and "approved" not in forwarded


@pytest.mark.parametrize(
    "extra",
    [
        {"geometry": {}},
        {"verified_by_human": True},
        {"approved": True},
        {"strata": ["caller-chosen"]},
    ],
)
def test_correction_cannot_supply_geometry_or_human_review_claim(boundary, extra):
    payload = {
        "expected_version": 3,
        "edits": [{"field_id": "players.1.first_name", "value": "Source value"}],
        **extra,
    }
    assert (
        post(
            boundary,
            f"/runs/{boundary.run}/members/{boundary.member}/corrections",
            payload,
        ).status_code
        == 400
    )
    assert boundary.state.calls == []


@pytest.mark.parametrize(
    "edits",
    [
        [{"field_id": "x", "value": "a", "geometry": {}}],
        [{"field_id": "x", "value": "a"}, {"field_id": "x", "value": "b"}],
        [{"field_id": "x", "value": True}],
        [{"field_id": "../source", "value": "a"}],
        [{"field_id": "x", "value": "a\x00b"}],
    ],
)
def test_invalid_correction_values_fail_before_private_service(boundary, edits):
    assert (
        post(
            boundary,
            f"/runs/{boundary.run}/members/{boundary.member}/corrections",
            {"expected_version": 3, "edits": edits},
        ).status_code
        == 400
    )
    assert boundary.state.calls == []


def test_auto_ready_member_requires_quality_report_instead_of_autofix(boundary):
    boundary.state.member_status = "ready"
    response = post(
        boundary,
        f"/runs/{boundary.run}/members/{boundary.member}/corrections",
        {
            "expected_version": 3,
            "edits": [{"field_id": "players.1.first_name", "value": "Other"}],
        },
    )
    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"] == "QUALITY_FALSE_ADMISSION_REPORT_REQUIRED"
    )
    assert "Informa" in response.json()["detail"]["message"]


def test_quality_report_only_transmits_scoped_reason_and_never_correction_or_approval(
    boundary,
):
    payload = {"expected_version": 3, "reason": "Discrepancia crítica en la fuente"}
    response = post(
        boundary,
        f"/runs/{boundary.run}/members/{boundary.member}/quality-report",
        payload,
    )
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    assert boundary.state.calls == [
        ("report_quality_issue", (UUID(boundary.run), UUID(boundary.member), payload))
    ]
    assert (
        post(
            boundary,
            f"/runs/{boundary.run}/members/{boundary.member}/quality-report",
            {**payload, "stratum": "untrusted"},
        ).status_code
        == 400
    )


def test_member_read_correction_and_report_refuse_foreign_response_scope(boundary):
    boundary.state.response_extra = {"member_id": str(uuid4())}
    prefix = f"/runs/{boundary.run}/members/{boundary.member}"
    assert (
        boundary.client.get("/api/registration-review-batches" + prefix).status_code
        == 503
    )
    assert (
        post(
            boundary,
            prefix + "/corrections",
            {
                "expected_version": 3,
                "edits": [{"field_id": "players.1.first_name", "value": None}],
            },
        ).status_code
        == 503
    )
    assert (
        post(
            boundary,
            prefix + "/quality-report",
            {"expected_version": 3, "reason": "Source mismatch"},
        ).status_code
        == 503
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "players.1.first_name",
            {
                "kind": "source_field",
                "value": "Original",
                "rectangle": {
                    "page_index": 1,
                    "x": 10,
                    "y": 20,
                    "width": 100,
                    "height": 30,
                },
            },
        ),
        (
            "players.1.photo",
            {
                "kind": "photo",
                "presence": "PRESENT",
                "rectangle": {
                    "page_index": 1,
                    "x": 40,
                    "y": 80,
                    "width": 90,
                    "height": 120,
                },
            },
        ),
        ("staff.2.presence", {"kind": "presence", "presence": "ABSENT"}),
    ],
)
def test_typed_manual_proposals_forward_only_actor_bound_values(boundary, field, value):
    payload = {"expected_version": 3, "edits": [{"field_id": field, "value": value}]}
    response = post(
        boundary, f"/runs/{boundary.run}/members/{boundary.member}/corrections", payload
    )
    assert response.status_code == 200
    forwarded = boundary.state.calls[-1][1]
    assert str(forwarded[0]) == boundary.run
    assert forwarded[-1] == payload
    assert "approved" not in forwarded[-1]


@pytest.mark.parametrize(
    "value",
    [
        {
            "kind": "photo",
            "presence": "ABSENT",
            "rectangle": {"page_index": 1, "x": 1, "y": 2, "width": 3, "height": 4},
            "sha256": "a" * 64,
        },
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": {"page_index": 1, "x": 1.1, "y": 2, "width": 3, "height": 4},
        },
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": {
                "page_index": 1,
                "x": 1,
                "y": 2,
                "width": 3,
                "height": 4,
                "approved": True,
            },
        },
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": {"page_index": 5, "x": 1, "y": 2, "width": 3, "height": 4},
        },
        {"kind": "photo", "presence": "PRESENT", "photo_path": "/private/example"},
    ],
)
def test_typed_photo_proposals_reject_hashes_paths_authority_and_noninteger_geometry(
    boundary, value
):
    response = post(
        boundary,
        f"/runs/{boundary.run}/members/{boundary.member}/corrections",
        {
            "expected_version": 3,
            "edits": [{"field_id": "players.1.photo", "value": value}],
        },
    )
    assert response.status_code == 400
    assert boundary.state.calls == []
