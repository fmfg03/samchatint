"""Public review boundary only; private authority is tested in PostgreSQL suites."""

import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.copa_telmex.staff_review_routes import (
    CSRF_HEADER,
    MAX_BODY_BYTES,
    StaffReviewRoutes,
    _proposal_changes,
    register_staff_review_routes,
)

ORIGIN = "https://sam.chat"


@pytest.fixture
def boundary():
    account, session, proposal, attempt, enrollment = (uuid4() for _ in range(5))
    state = SimpleNamespace(
        calls=[],
        refreshes=0,
        sessions=0,
        edition=True,
        active=True,
        actor_changed=False,
        failure=None,
        configured=True,
        foreign_view=False,
        png=b"\x89PNG\r\n\x1a\n" + b"synthetic",
    )

    class Denied(Exception):
        reason_code = "SCOPE_DENIED"

    class Client:
        def check(self, operation, actor, requested, *rest):
            assert state.sessions == 0, "app connection must close before private calls"
            assert actor == account
            state.calls.append((operation, actor, requested, rest))
            if state.failure:
                raise state.failure
            if requested != session or not state.edition:
                raise Denied()

        async def view(self, actor, requested):
            self.check("view", actor, requested)
            return {
                "session_id": str(uuid4() if state.foreign_view else session),
                "proposal_id": str(proposal),
                "state": "ready",
                "ready_to_approve": True,
                "team": {"name": "Synthetic"},
                "players": [],
                "staff": [],
                "pages": [],
                "blockers": [],
                "passkey": {"status": "active"},
            }

        async def prepare(self, actor, requested, expected, changes):
            self.check("prepare", actor, requested, expected, changes)
            return {
                "session_id": str(session),
                "proposal_id": str(proposal),
                "state": "ready",
            }

        async def challenge(self, actor, requested, selected, confirmed):
            self.check("challenge", actor, requested, selected, confirmed)
            assert selected == proposal and confirmed is True
            return {"attempt_id": str(attempt), "options": {"publicKey": {}}}

        async def assert_and_capture(self, actor, requested, selected, assertion):
            self.check("assertion", actor, requested, selected, assertion)
            assert selected == attempt and (
                assertion is None or type(assertion) is bytes
            )
            return {
                "session_id": str(session),
                "attempt_id": str(attempt),
                "state": "uncertain",
            }

        async def status(self, actor, requested, selected):
            self.check("status", actor, requested, selected)
            return {
                "session_id": str(session),
                "attempt_id": str(selected),
                "state": "committed",
            }

        async def source_page(self, actor, requested, selected, page):
            self.check("source", actor, requested, selected, page)
            return state.png

        async def enrollment_begin(self, actor, requested):
            self.check("enrollment_begin", actor, requested)
            return {"enrollment_id": str(enrollment), "options": {"publicKey": {}}}

        async def enrollment_complete(self, actor, requested, selected, registration):
            self.check("enrollment_complete", actor, requested, selected, registration)
            return {
                "enrollment_id": str(enrollment),
                "state": "pending_activation",
                "fingerprint": "sha256:" + "a" * 64,
            }

        async def enrollment_status(self, actor, requested, selected):
            self.check("enrollment_status", actor, requested, selected)
            return {"enrollment_id": str(enrollment), "state": "pending_activation"}

    class Session:
        async def __aenter__(self):
            state.sessions += 1
            return self

        async def __aexit__(self, *_):
            state.sessions -= 1

    def access(request):
        if not request.session.get("empleado_id"):
            raise HTTPException(status_code=401, detail="login required")
        if request.session.get("rol") != "coordinador":
            raise HTTPException(status_code=403, detail="role denied")

    async def refresh(_, actor):
        state.refreshes += 1
        if not state.active:
            raise HTTPException(status_code=409, detail="account inactive")
        return {"user_id": str(uuid4()) if state.actor_changed else actor["user_id"]}

    service = Client()
    dependencies = dict(
        ensure_access=access,
        actor_from_request=lambda request: {"user_id": request.session["empleado_id"]},
        refresh_actor=refresh,
        session_factory=Session,
        client_resolver=lambda _: service if state.configured else None,
        expected_origin=ORIGIN,
    )
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware, secret_key="isolated-public-boundary-test-key"
    )

    @app.get("/_test/login")
    def login(request: Request, role: str = "coordinador", impersonated: bool = False):
        request.session["empleado_id"] = str(account)
        request.session["rol"] = role
        if impersonated:
            request.session["impersonator_empleado_id"] = str(uuid4())
        return {"ok": True}

    controller = register_staff_review_routes(app, **dependencies)

    @app.post("/api/registration-review/{session_id}/edit")
    async def edit(request: Request, session_id: str):
        return await controller.prepare_request(request, session_id)

    client = TestClient(app, base_url=ORIGIN)
    base = "/api/registration-review/" + str(session)
    client.get("/_test/login")
    response = client.get(base + "/staff-approval")
    assert response.status_code == 200
    token = response.json()["staff_review_csrf"]
    state.calls.clear()
    state.refreshes = 0
    return SimpleNamespace(
        client=client,
        state=state,
        base=base,
        proposal=proposal,
        attempt=attempt,
        enrollment=enrollment,
        token=token,
        dependencies=dependencies,
    )


def post(boundary, suffix, body, headers=None):
    return boundary.client.post(
        boundary.base + suffix,
        json=body,
        headers=(
            headers
            if headers is not None
            else {"Origin": ORIGIN, CSRF_HEADER: boundary.token}
        ),
    )


def test_public_reads_and_source_use_current_actor_and_private_object_scope(boundary):
    b = boundary
    for suffix in (
        "/staff-approval",
        "/staff-approval/status?attempt_id=" + str(b.attempt),
        f"/staff-approval/source/{b.proposal}/1",
    ):
        response = b.client.get(b.base + suffix)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert b.state.refreshes == 3
    foreign = b.client.get(
        "/api/registration-review/" + str(uuid4()) + "/staff-approval"
    )
    assert foreign.status_code == 403
    b.state.edition = False
    assert b.client.get(b.base + "/staff-approval").status_code == 403


@pytest.mark.parametrize(
    "failure", ["anonymous", "role", "impersonated", "inactive", "changed"]
)
def test_navigation_is_not_independent_review_authority(boundary, failure):
    b = boundary
    if failure == "anonymous":
        b.client.cookies.clear()
    elif failure == "role":
        b.client.get("/_test/login?role=empleado")
    elif failure == "impersonated":
        b.client.get("/_test/login?impersonated=true")
    elif failure == "inactive":
        b.state.active = False
    else:
        b.state.actor_changed = True
    response = b.client.get(b.base + "/staff-approval")
    assert response.status_code in {401, 403, 409}
    assert not b.state.calls
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": ORIGIN},
        {"Origin": "https://attacker.invalid", CSRF_HEADER: "anything"},
        {"Origin": "null", CSRF_HEADER: "anything"},
        {CSRF_HEADER: "anything"},
        {"Origin": ORIGIN, CSRF_HEADER: "incorrect"},
    ],
)
def test_csrf_and_origin_fail_before_database_or_private_mutation(boundary, headers):
    b = boundary
    response = post(
        b,
        "/staff-approval/challenge",
        {"proposal_id": str(b.proposal), "confirmed": True},
        headers,
    )
    assert response.status_code == 403
    assert b.state.refreshes == 0
    assert not b.state.calls


@pytest.mark.parametrize("name", ["Origin", CSRF_HEADER])
def test_duplicate_security_headers_fail_closed(boundary, name):
    b = boundary
    headers = [("Origin", ORIGIN), (CSRF_HEADER, b.token)]
    headers.append((name, ORIGIN if name == "Origin" else b.token))
    response = post(
        b,
        "/staff-approval/challenge",
        {"proposal_id": str(b.proposal), "confirmed": True},
        headers,
    )
    assert response.status_code == 403
    assert not b.state.calls


def test_recovery_passes_same_attempt_and_no_new_assertion(boundary):
    b = boundary
    result = post(
        b,
        "/staff-approval/assertion",
        {"attempt_id": str(b.attempt), "assertion": None},
    )
    assert result.status_code == 200
    assert b.state.calls[-1][0] == "assertion"
    assert b.state.calls[-1][-1] == (b.attempt, None)
    assert result.headers["Cache-Control"] == "no-store"


def test_challenge_assertion_and_enrollment_only_pass_server_derived_account(boundary):
    b = boundary
    actions = [
        (
            "/staff-approval/challenge",
            {"proposal_id": str(b.proposal), "confirmed": True},
        ),
        (
            "/staff-approval/assertion",
            {"attempt_id": str(b.attempt), "assertion": {"type": "public-key"}},
        ),
        ("/staff-passkey/begin", {}),
        (
            "/staff-passkey/complete",
            {
                "enrollment_id": str(b.enrollment),
                "registration": {"type": "public-key"},
            },
        ),
        ("/staff-passkey/status", {"enrollment_id": str(b.enrollment)}),
    ]
    for suffix, body in actions:
        response = post(b, suffix, body)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
    assert [call[0] for call in b.state.calls] == [
        "challenge",
        "assertion",
        "enrollment_begin",
        "enrollment_complete",
        "enrollment_status",
    ]


@pytest.mark.parametrize(
    "extra",
    [
        "principal_id",
        "account_id",
        "tenant_id",
        "edition_id",
        "role_current",
        "verified",
    ],
)
def test_browser_cannot_supply_identity_or_authority_fields(boundary, extra):
    b = boundary
    response = post(
        b,
        "/staff-approval/challenge",
        {"proposal_id": str(b.proposal), "confirmed": True, extra: "forged"},
    )
    assert response.status_code == 400
    assert not b.state.calls


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_explicit_intention_is_required_but_not_an_authority_flag(boundary, confirmed):
    b = boundary
    assert (
        post(
            b,
            "/staff-approval/challenge",
            {"proposal_id": str(b.proposal), "confirmed": confirmed},
        ).status_code
        == 400
    )
    assert not b.state.calls


def proposal():
    rectangle = {"page_index": 1, "x": 10, "y": 20, "width": 200, "height": 100}
    return {
        "team": {"name": "Synthetic"},
        "players": [
            {
                "slot": 1,
                "first_name": "Synthetic",
                "last_name": "Player",
                "birth_date": "2000-01-01",
                "curp": "",
                "source_page_index": 1,
                "boxes": {"full_name": rectangle},
            }
        ],
        "staff": [
            {
                "slot": 1,
                "first_name": "Synthetic",
                "last_name": "Coach",
                "birth_date": "1980-01-01",
                "curp": "",
                "presence": "PRESENT",
                "presence_confirmed": True,
                "source_page_index": 1,
                "boxes": {"full_name": rectangle},
                "photo_box": rectangle,
            }
        ],
    }


def test_preparing_values_and_rectangles_does_not_accept_database_rows(boundary):
    b = boundary
    response = post(
        b, "/edit", {"expected_proposal_id": str(b.proposal), "changes": proposal()}
    )
    assert response.status_code == 200
    assert b.state.calls[0][0] == "prepare"
    assert b.state.calls[0][3][0] == b.proposal


@pytest.mark.parametrize("confirmed", [True, False])
def test_team_presence_confirmation_reaches_private_prepare(boundary, confirmed):
    changes = proposal()
    changes["confirm_present_staff"] = confirmed
    response = post(
        boundary,
        "/edit",
        {"expected_proposal_id": str(boundary.proposal), "changes": changes},
    )
    assert response.status_code == 200
    assert boundary.state.calls[0][0] == "prepare"
    assert boundary.state.calls[0][3][1] == changes


@pytest.mark.parametrize("invalid", [1, 0, "true", None, [], {}])
def test_team_presence_confirmation_requires_an_explicit_boolean(invalid):
    changes = proposal()
    changes["confirm_present_staff"] = invalid
    with pytest.raises(HTTPException) as error:
        _proposal_changes(changes)
    assert error.value.status_code == 400


def unresolved_staff_proposal():
    changes = proposal()
    changes["staff"][0].update(
        source_page_index=0,
        presence="UNCERTAIN",
        presence_confirmed=False,
        boxes={},
        photo_box=None,
        absence_box=None,
    )
    return changes


def test_unresolved_staff_can_prepare_observations_without_claiming_evidence(boundary):
    changes = unresolved_staff_proposal()
    response = post(
        boundary,
        "/edit",
        {"expected_proposal_id": None, "changes": changes},
    )
    assert response.status_code == 200
    assert boundary.state.calls[0][0] == "prepare"
    assert boundary.state.calls[0][3] == (None, changes)


@pytest.mark.parametrize(
    "update",
    [
        {"presence": "PRESENT"},
        {"presence": "ABSENT"},
        {"presence_confirmed": True},
        {"boxes": {"full_name": None}},
        {"photo_box": {"page_index": 1, "x": 0, "y": 0, "width": 1, "height": 1}},
        {"absence_box": {"page_index": 1, "x": 0, "y": 0, "width": 1, "height": 1}},
    ],
)
def test_unselected_staff_page_cannot_claim_presence_or_geometry(update):
    changes = unresolved_staff_proposal()
    changes["staff"][0].update(update)
    with pytest.raises(HTTPException) as error:
        _proposal_changes(changes)
    assert error.value.status_code == 400


def test_player_source_page_cannot_be_unselected():
    changes = proposal()
    changes["players"][0].update(source_page_index=0, boxes={})
    with pytest.raises(HTTPException) as error:
        _proposal_changes(changes)
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "metadata",
    [
        {"manual_fields": []},
        {"source_card": {"status": "verified"}},
        {"photo_status": "photo"},
        {"presence_proposed": True},
    ],
)
def test_browser_cannot_submit_server_derived_evidence_metadata(boundary, metadata):
    changes = proposal()
    changes["staff"][0].update(metadata)
    response = post(
        boundary,
        "/edit",
        {"expected_proposal_id": str(boundary.proposal), "changes": changes},
    )
    assert response.status_code == 400
    assert not boundary.state.calls


@pytest.mark.parametrize(
    "attack",
    [
        "extra",
        "team_flag",
        "row_id",
        "role",
        "receipt",
        "slot_bool",
        "duplicate",
        "box_float",
        "box_page",
        "photo_page",
        "presence_flag",
        "bad_presence",
        "bad_string",
        "bad_boxes",
        "bad_member",
        "bad_team",
    ],
)
def test_proposal_surface_only_accepts_explicit_business_values_and_source_boxes(
    attack,
):
    changes = copy.deepcopy(proposal())
    if attack == "extra":
        changes["authority"] = True
    elif attack == "team_flag":
        changes["team"]["eligible"] = True
    elif attack == "row_id":
        changes["players"][0]["id"] = str(uuid4())
    elif attack == "role":
        changes["staff"][0]["role"] = "director_tecnico"
    elif attack == "receipt":
        changes["staff"][0]["finality_receipt_id"] = "forged"
    elif attack == "slot_bool":
        changes["players"][0]["slot"] = True
    elif attack == "duplicate":
        changes["players"] *= 2
    elif attack == "box_float":
        changes["players"][0]["boxes"]["full_name"]["x"] = 1.5
    elif attack == "box_page":
        changes["players"][0]["source_page_index"] = 2
    elif attack == "photo_page":
        changes["staff"][0]["photo_box"]["page_index"] = 2
    elif attack == "presence_flag":
        changes["staff"][0]["presence_confirmed"] = "true"
    elif attack == "bad_presence":
        changes["staff"][0]["presence"] = "VERIFIED"
    elif attack == "bad_string":
        changes["players"][0]["first_name"] = 1
    elif attack == "bad_boxes":
        changes["players"][0]["boxes"] = []
    elif attack == "bad_member":
        changes["players"] = [None]
    else:
        changes["team"] = []
    with pytest.raises(HTTPException):
        _proposal_changes(changes)


@pytest.mark.parametrize(
    "body",
    [
        b'{"proposal_id":"x","proposal_id":"y","confirmed":true}',
        b'{"proposal_id":NaN,"confirmed":true}',
        b"[]",
        b"{" + b'"x":' + b"[" * 25 + b"0" + b"]" * 25 + b"}",
    ],
)
def test_malformed_or_ambiguous_json_is_rejected_before_private_calls(boundary, body):
    b = boundary
    response = b.client.post(
        b.base + "/staff-approval/challenge",
        content=body,
        headers={
            "Origin": ORIGIN,
            CSRF_HEADER: b.token,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    assert not b.state.calls


def test_request_limit_and_content_type_are_enforced(boundary):
    b = boundary
    response = b.client.post(
        b.base + "/staff-approval/challenge",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={
            "Origin": ORIGIN,
            CSRF_HEADER: b.token,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413
    response = b.client.post(
        b.base + "/staff-approval/challenge",
        data={},
        headers={"Origin": ORIGIN, CSRF_HEADER: b.token},
    )
    assert response.status_code == 415
    assert not b.state.calls


def test_unconfigured_backend_or_foreign_view_never_returns_a_plan(boundary):
    b = boundary
    b.state.configured = False
    assert b.client.get(b.base + "/staff-approval").status_code == 503
    b.state.configured = True
    b.state.foreign_view = True
    assert b.client.get(b.base + "/staff-approval").status_code == 503


def test_backend_diagnostics_are_not_exposed_and_known_errors_are_actionable(boundary):
    b = boundary
    b.state.failure = RuntimeError("PRIVATE_BACKEND_DIAGNOSTIC")
    response = b.client.get(b.base + "/staff-approval")
    assert response.status_code == 503
    assert "PRIVATE_BACKEND_DIAGNOSTIC" not in response.text
    b.state.failure = SimpleNamespace()

    class Stale(Exception):
        reason_code = "STALE_PROPOSAL"

    b.state.failure = Stale()
    response = b.client.get(b.base + "/staff-approval")
    assert response.status_code == 409
    assert "Recarga" in response.json()["detail"]["message"]


def test_private_source_must_be_bounded_png(boundary):
    b = boundary
    b.state.png = b"PRIVATE_JSON_NOT_IMAGE"
    response = b.client.get(b.base + f"/staff-approval/source/{b.proposal}/1")
    assert response.status_code == 503
    assert "PRIVATE_JSON_NOT_IMAGE" not in response.text


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.invalid",
        "http://sam.chat",
        "https://sam.chat/",
        "https://sam.chat?q=1",
        "https://user@sam.chat",
    ],
)
def test_origin_configuration_cannot_be_derived_from_request_host(boundary, origin):
    dependencies = {**boundary.dependencies, "expected_origin": origin}
    with pytest.raises(ValueError):
        StaffReviewRoutes(**dependencies)


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_contract_notice_distinguishes_legacy_capture_without_removing_incidents(
    protected,
    committed,
):
    from jinja2 import Environment, select_autoescape

    source = (
        Path(__file__).resolve().parents[2]
        / "templates/registration_review_detail.html"
    ).read_text()
    condition = "{% if validation.blockers and not session_committed %}"
    section = source.split(condition, 1)[1].split("{% if validation.issues %}", 1)[0]
    template = Environment(autoescape=select_autoescape(default=True)).from_string(
        condition + section
    )
    validation = {
        "blockers": [
            {
                "code": "STAFF_GOVERNANCE_CONTRACT_REQUIRED",
                "message": "El contrato anterior solo autoriza jugadores.",
            },
            {"code": "CURP_REVIEW_PENDING", "message": "CURP sin revisar."},
        ],
        "ready_to_commit": False,
    }
    before = copy.deepcopy(validation)
    rendered = template.render(
        validation=validation,
        protected_staff_review=protected,
        session_committed=committed,
    )
    assert ("CURP sin revisar." in rendered) is (not committed)
    assert ("usa la revisión protegida con passkey" in rendered) is (
        protected and not committed
    )
    assert ("El contrato anterior solo autoriza jugadores." in rendered) is (
        not protected and not committed
    )
    assert validation == before
