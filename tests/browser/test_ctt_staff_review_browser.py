"""Presentation/native ceremony contracts, with intercepted synthetic API replies.

These tests do not claim SQL capture or operator accreditation. The real
workflow/PostgreSQL suites own those boundaries; no live request leaves here.
"""

import base64
import json
import logging
import os
import secrets
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def panel(page):
    session, proposal, attempt, enrollment = (str(uuid4()) for _ in range(4))
    rectangle = {"page_index": 1, "x": 20, "y": 30, "width": 240, "height": 120}
    person = dict(
        first_name="Synthetic",
        last_name="Reviewer",
        birth_date="2000-01-01",
        curp="",
        source_page_index=1,
        boxes={"full_name": rectangle},
    )
    view = dict(
        session_id=session,
        proposal_id=proposal,
        state="ready",
        ready_to_approve=True,
        team={"name": "Synthetic Team"},
        players=[{"slot": 1, **person}],
        staff=[
            {
                "slot": 1,
                "role": "director_tecnico",
                "presence": "PRESENT",
                "presence_proposed": True,
                "presence_confirmed": True,
                **person,
            },
            {
                "slot": 2,
                "role": "auxiliar",
                "presence": "ABSENT",
                "presence_proposed": False,
                "presence_confirmed": True,
                **person,
            },
        ],
        pages=[{"page_index": 1, "pdf_page": 1, "width": 600, "height": 800}],
        blockers=[],
        passkey={"status": "active"},
    )
    state = SimpleNamespace(
        view=view,
        calls=[],
        stale=False,
        changes=None,
        server=None,
        credential=None,
        verified_assertions=0,
        fingerprint=None,
    )
    stream = BytesIO()
    Image.new("RGB", (600, 800), "#bcd8f0").save(stream, format="PNG")
    png = stream.getvalue()
    source = (ROOT / "templates/registration_review_detail.html").read_text()
    widget = source.split("<!-- protected-review-widget:start -->", 1)[1].split(
        "<!-- protected-review-widget:end -->", 1
    )[0]
    template = Environment(autoescape=select_autoescape(default=True)).from_string(
        widget
    )
    base = "/api/registration-review/" + session

    def reply(route, value, status=200):
        route.fulfill(
            status=status, content_type="application/json", body=json.dumps(value)
        )

    def handle(route):
        request = route.request
        if not request.url.startswith("https://sam.chat/"):
            route.abort()
            return
        path = request.url.split("https://sam.chat", 1)[1]
        if path.split("?", 1)[0] == "/static/js/ctt_staff_review.js":
            route.fulfill(
                status=200,
                content_type="text/javascript; charset=utf-8",
                body=(ROOT / "static/js/ctt_staff_review.js").read_text(),
            )
            return
        if path.startswith(base + "/staff-approval/source/"):
            route.fulfill(status=200, content_type="image/png", body=png)
            return
        if path == "/review":
            markup = template.render(
                protected_staff_review=True,
                session_committed=False,
                review_session=SimpleNamespace(id=session),
                staff_review_csrf="synthetic-csrf",
                staff_review_view=state.view,
            )
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body='<html><head><meta charset="utf-8"></head><body>'
                + markup
                + "</body></html>",
            )
            return
        body = request.post_data_json if request.method == "POST" else None
        state.calls.append((path, body))
        if request.method == "POST":
            assert request.headers.get("x-ctt-csrf") == "synthetic-csrf"
        if path == base + "/staff-approval":
            reply(
                route,
                {
                    "staff_review_view": state.view,
                    "staff_review_csrf": "synthetic-csrf",
                },
            )
        elif path == base + "/edit":
            state.changes = body
            reply(route, state.view)
        elif path == base + "/staff-approval/challenge":
            if state.stale:
                reply(
                    route,
                    {
                        "detail": {
                            "error": "STALE_PROPOSAL",
                            "message": "La revisión cambió. Recarga el plan.",
                        }
                    },
                    409,
                )
                return
            if state.server:
                from fido2.webauthn import UserVerificationRequirement

                offered, state.authentication = state.server.authenticate_begin(
                    [state.credential],
                    challenge=secrets.token_bytes(32),
                    user_verification=UserVerificationRequirement.REQUIRED,
                )
                public = dict(offered)
            else:
                public = {
                    "publicKey": {
                        "challenge": base64.urlsafe_b64encode(b"c" * 32)
                        .decode()
                        .rstrip("="),
                        "rpId": "sam.chat",
                        "userVerification": "required",
                    }
                }
            reply(route, {"attempt_id": attempt, "options": public})
        elif path == base + "/staff-approval/assertion":
            from fido2.webauthn import AuthenticationResponse

            if body["assertion"] is None:
                assert state.verified_assertions == 1
                reply(
                    route,
                    {
                        "session_id": session,
                        "attempt_id": attempt,
                        "state": "uncertain",
                    },
                )
                return
            state.server.authenticate_complete(
                state.authentication,
                [state.credential],
                AuthenticationResponse.from_dict(body["assertion"]),
            )
            state.verified_assertions += 1
            reply(
                route,
                {"session_id": session, "attempt_id": attempt, "state": "uncertain"},
            )
        elif path.startswith(base + "/staff-approval/status?"):
            reply(
                route,
                {"session_id": session, "attempt_id": attempt, "state": "uncertain"},
            )
        elif path == base + "/staff-passkey/begin":
            from fido2.webauthn import (
                AuthenticatorAttachment,
                PublicKeyCredentialUserEntity,
                UserVerificationRequirement,
            )

            offered, state.registration = state.server.register_begin(
                PublicKeyCredentialUserEntity(
                    id=b"synthetic-user",
                    name="synthetic",
                    display_name="Synthetic Reviewer",
                ),
                challenge=secrets.token_bytes(32),
                user_verification=UserVerificationRequirement.REQUIRED,
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            )
            reply(route, {"enrollment_id": enrollment, "options": dict(offered)})
        elif path == base + "/staff-passkey/complete":
            from hashlib import sha256

            from fido2.webauthn import RegistrationResponse

            observed = state.server.register_complete(
                state.registration, RegistrationResponse.from_dict(body["registration"])
            )
            state.credential = observed.credential_data
            state.fingerprint = "sha256:" + sha256(bytes(state.credential)).hexdigest()
            reply(
                route,
                {
                    "enrollment_id": enrollment,
                    "state": "pending_activation",
                    "fingerprint": state.fingerprint,
                },
            )
        else:
            route.abort()

    page.context.route("**/*", handle)
    yield SimpleNamespace(page=page, state=state, base=base)


@pytest.fixture
def native_server(panel):
    module = pytest.importorskip(
        "fido2.server", reason="isolated approved FIDO2 test dependency required"
    )
    from fido2.webauthn import PublicKeyCredentialRpEntity

    logger = logging.getLogger("fido2.server")
    previous = logger.level
    logger.setLevel(logging.WARNING)
    panel.state.server = module.Fido2Server(
        PublicKeyCredentialRpEntity(id="sam.chat", name="SamChat"),
        verify_origin=lambda origin: origin == "https://sam.chat",
    )
    cdp = panel.page.context.new_cdp_session(panel.page)
    cdp.send("WebAuthn.enable")
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    try:
        yield panel.state.server
    finally:
        logger.setLevel(previous)


def open_panel(panel):
    panel.page.goto("https://sam.chat/review")
    expect(panel.page.locator("[data-review-team]")).to_contain_text("Equipo")


def test_frozen_plan_requires_explicit_confirmation_and_never_edits_original_form(
    panel,
):
    open_panel(panel)
    approve = panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    expect(approve).to_be_disabled()
    expect(panel.page.get_by_label("Equipo", exact=True)).to_be_disabled()
    panel.page.locator("[data-review-confirm]").check()
    expect(approve).to_be_enabled()


def test_cancelled_passkey_never_submits_assertion_or_capture(panel):
    panel.page.add_init_script(
        "Object.defineProperty(navigator.credentials,'get',"
        "{value:async()=>{throw new DOMException('cancelled','NotAllowedError')}})"
    )
    open_panel(panel)
    panel.page.locator("[data-review-confirm]").check()
    panel.page.get_by_role("button", name="Aprobar y capturar con passkey").click()
    expect(panel.page.locator("[data-review-status]")).to_contain_text("se canceló")
    assert not any(path.endswith("/assertion") for path, _ in panel.state.calls)


def test_missing_browser_factor_stays_closed(panel):
    panel.page.add_init_script(
        "Object.defineProperty(window,'PublicKeyCredential',{value:undefined})"
    )
    open_panel(panel)
    panel.page.locator("[data-review-confirm]").check()
    expect(
        panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    ).to_be_disabled()
    expect(panel.page.locator("[data-review-error]")).to_contain_text(
        "soporte de passkeys"
    )


def test_stale_plan_is_not_replaced_or_captured_implicitly(panel):
    panel.state.stale = True
    open_panel(panel)
    panel.page.locator("[data-review-confirm]").check()
    panel.page.get_by_role("button", name="Aprobar y capturar con passkey").click()
    expect(panel.page.locator("[data-review-error]")).to_contain_text("Recarga el plan")
    assert not any(path.endswith("/assertion") for path, _ in panel.state.calls)


def test_dragged_source_uses_original_pixels_and_only_submits_a_new_proposal(panel):
    open_panel(panel)
    panel.page.get_by_role("button", name="Corregir propuesta").click()
    canvas = panel.page.locator("[data-source-canvas]")
    expect(canvas).to_be_visible()
    bounds = canvas.bounding_box()
    panel.page.mouse.move(
        bounds["x"] + bounds["width"] * 0.1, bounds["y"] + bounds["height"] * 0.1
    )
    panel.page.mouse.down()
    panel.page.mouse.move(
        bounds["x"] + bounds["width"] * 0.4, bounds["y"] + bounds["height"] * 0.25
    )
    panel.page.mouse.up()
    panel.page.get_by_role("button", name="Preparar revisión", exact=True).click()
    panel.page.wait_for_function(
        "document.querySelector('[data-review-prepare]').disabled"
    )
    assert panel.state.changes is not None
    rect = panel.state.changes["changes"]["players"][0]["boxes"]["full_name"]
    assert rect == {"page_index": 1, "x": 60, "y": 80, "width": 180, "height": 120}
    assert "role" not in panel.state.changes["changes"]["staff"][0]
    assert not any(path.endswith("/assertion") for path, _ in panel.state.calls)


def test_native_registration_and_assertion_keep_pending_activation_and_uncertain_result(
    panel, native_server
):
    panel.state.view["passkey"]["status"] = "not_enrolled"
    open_panel(panel)
    panel.page.get_by_role("button", name="Registrar passkey del dispositivo").click()
    expect(panel.page.locator("[data-enrollment-fingerprint]")).to_contain_text(
        "sha256:"
    )
    assert panel.state.credential is not None
    expect(
        panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    ).to_be_disabled()
    # Explicit synthetic fixture activation; this never changes a real faculty.
    panel.state.view["passkey"]["status"] = "active"
    panel.page.get_by_role("button", name="Consultar revisión", exact=True).click()
    panel.page.locator("[data-review-confirm]").check()
    panel.page.get_by_role("button", name="Aprobar y capturar con passkey").click()
    expect(panel.page.locator("[data-review-status]")).to_contain_text("mismo intento")
    expect(panel.page.locator("[data-review-status]")).to_contain_text(
        "resultado aún no está confirmado"
    )
    assert any("/staff-approval/status?" in path for path, _ in panel.state.calls)
    assert panel.state.verified_assertions == 1
    assert sum(path.endswith("/assertion") for path, _ in panel.state.calls) == 1
    expect(
        panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    ).to_be_disabled()
    panel.page.get_by_role("button", name="Reintentar el mismo intento").click()
    assert sum(path.endswith("/challenge") for path, _ in panel.state.calls) == 1
    assertions = [
        body for path, body in panel.state.calls if path.endswith("/assertion")
    ]
    assert len(assertions) == 2
    assert assertions[0]["attempt_id"] == assertions[1]["attempt_id"]
    assert assertions[1]["assertion"] is None
    assert panel.state.verified_assertions == 1
    assert panel.page.evaluate("Object.keys(localStorage)") == []
    assert panel.page.evaluate("location.origin") == "https://sam.chat"


@pytest.mark.parametrize(
    "status,message",
    [
        ("PENDING_OPERATOR_REVIEW", "activación"),
        ("ACTIVE", "acreditada"),
        ("EXPIRED", "venció"),
        ("REVOKED", "revocada"),
    ],
)
def test_repeat_enrollment_without_challenge_shows_current_state(
    panel, status, message
):
    from devnous.copa_telmex.staff_review_workflow import ReviewWorkflowService

    response = ReviewWorkflowService._enrollment_view(
        dict(
            enrollment_id=str(uuid4()),
            status=status,
            grants_faculty=False,
            fingerprint="sha256:synthetic-existing-candidate",
        )
    )
    panel.page.route(
        "**/staff-passkey/begin",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(response)
        ),
    )
    panel.page.add_init_script(
        "window.registrationCalls=0;"
        "Object.defineProperty(navigator.credentials,'create',"
        "{value:async()=>{window.registrationCalls++;"
        "throw Error('unexpected ceremony')}})"
    )
    open_panel(panel)
    panel.page.locator("[data-enrollment-fingerprint]").evaluate(
        "node => {node.textContent = 'sha256:previous-candidate'}"
    )
    panel.page.get_by_role("button", name="Registrar passkey del dispositivo").click()
    expect(panel.page.locator("[data-review-status]")).to_contain_text(message)
    expect(panel.page.locator("[data-review-error]")).to_be_hidden()
    assert panel.page.evaluate("window.registrationCalls") == 0
    assert not any(
        path.endswith("/staff-passkey/complete") for path, _ in panel.state.calls
    )
    if status == "PENDING_OPERATOR_REVIEW":
        expect(panel.page.locator("[data-enrollment-fingerprint]")).to_contain_text(
            "sha256:synthetic-existing-candidate"
        )
    if status in ("EXPIRED", "REVOKED"):
        expect(panel.page.locator("[data-enrollment-status]")).to_contain_text(message)
        expect(panel.page.locator("[data-enrollment-fingerprint]")).to_have_text("")


@pytest.mark.parametrize(
    "phase,optional", [("NACIONAL", True), ("ESTATAL", False), (None, False)]
)
def test_curp_label_only_marks_trusted_national_profile_optional(
    panel, phase, optional
):
    panel.state.view["identity_policy"] = {
        "phase": phase,
        "optional_curp_kinds": ["PLAYER", "STAFF"],
    }
    open_panel(panel)
    label = "CURP (opcional en fase nacional)" if optional else "CURP"
    expect(panel.page.get_by_label(label, exact=True)).to_have_count(3)
    if not optional:
        expect(
            panel.page.get_by_label("CURP (opcional en fase nacional)", exact=True)
        ).to_have_count(0)


def test_explicit_team_confirmation_only_affirms_proposed_present_slots(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["staff"][0]["presence_confirmed"] = False
    panel.state.view["staff"][1]["presence"] = "UNCERTAIN"
    panel.state.view["staff"][1]["presence_confirmed"] = False
    open_panel(panel)
    team = panel.page.locator("[data-review-team-confirm]")
    present = panel.page.locator('[data-presence-confirmed="0"]')
    uncertain = panel.page.locator('[data-presence-confirmed="1"]')
    expect(team).not_to_be_checked()
    expect(present).not_to_be_checked()
    team.check()
    expect(present).to_be_checked()
    expect(uncertain).not_to_be_checked()
    with panel.page.expect_response(lambda response: response.url.endswith("/edit")):
        panel.page.get_by_role("button", name="Preparar revisión", exact=True).click()
    assert panel.state.changes["changes"]["confirm_present_staff"] is True
    assert panel.state.changes["changes"]["staff"][0]["presence_confirmed"] is True
    assert panel.state.changes["changes"]["staff"][1]["presence_confirmed"] is False
    assert panel.state.changes["changes"]["staff"][1]["presence"] == "UNCERTAIN"
    assert (
        panel.state.changes["changes"]["players"][0]["boxes"]
        == panel.state.view["players"][0]["boxes"]
    )
    assert not any(path.endswith("/assertion") for path, _ in panel.state.calls)


def test_any_edit_invalidates_team_confirmation_and_old_approval(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["staff"][0]["presence_confirmed"] = False
    open_panel(panel)
    panel.page.locator("[data-review-team-confirm]").check()
    expect(panel.page.locator('[data-presence-confirmed="0"]')).to_be_checked()
    panel.page.get_by_label("Equipo", exact=True).fill("Equipo corregido")
    expect(panel.page.locator("[data-review-team-confirm]")).not_to_be_checked()
    expect(panel.page.locator('[data-presence-confirmed="0"]')).not_to_be_checked()
    expect(
        panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    ).to_be_disabled()


def test_exception_navigates_to_person_field_source(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["blockers"] = [
        {
            "code": "FIELD_EVIDENCE_REQUIRED",
            "kind": "PLAYER",
            "slot": 1,
            "field": "birth_date",
            "navigation_target": {
                "kind": "PLAYER",
                "slot": 1,
                "field": "birth_date",
                "source_page_index": 1,
            },
            "message": "Falta la fuente.",
        }
    ]
    open_panel(panel)
    panel.page.get_by_role(
        "button", name="Jugador 1 · Fecha de nacimiento: Falta la fuente."
    ).click()
    expect(panel.page.locator("[data-source-target] option:checked")).to_have_text(
        "Jugador 1 · Fecha de nacimiento"
    )
    expect(panel.page.locator("[data-source-canvas]")).to_be_visible()


def test_individual_staff_confirmation_is_invalidated_by_staff_edit(panel):
    panel.state.view["ready_to_approve"] = False
    open_panel(panel)
    confirmation = panel.page.locator('[data-presence-confirmed="0"]')
    expect(confirmation).to_be_checked()
    panel.page.locator(
        '[data-kind="staff"][data-index="0"][data-value="first_name"]'
    ).fill("Cambio")
    expect(confirmation).not_to_be_checked()


def test_photo_status_distinguishes_missing_uncertain_and_technical_error(panel):
    panel.state.view["players"] = [
        {**panel.state.view["players"][0], "slot": i, "photo_status": status}
        for i, status in enumerate(("missing", "uncertain", "error"), 1)
    ]
    open_panel(panel)
    members = panel.page.locator("[data-review-members]")
    expect(members).to_contain_text("No se detectó una fotografía")
    expect(members).to_contain_text("La fotografía necesita revisión")
    expect(members).to_contain_text("No se pudo preparar la fotografía")


def national_photos_pending(panel):
    """A trusted review response with eight absent photos and eight real previews."""
    person = panel.state.view["players"][0]
    players = []
    for slot in range(1, 17):
        player = {**person, "slot": slot, "photo_status": "pending"}
        if slot % 2 == 0:
            stream = BytesIO()
            Image.new("RGB", (8, 8), (slot, 80, 160)).save(stream, format="PNG")
            player.update(
                photo_status="photo",
                photo_preview="data:image/png;base64,"
                + base64.b64encode(stream.getvalue()).decode(),
            )
        players.append(player)
    panel.state.view["players"] = players
    panel.state.view["identity_policy"] = {
        "phase": "NACIONAL",
        "optional_curp_kinds": ["PLAYER", "STAFF"],
    }
    for member in panel.state.view["staff"]:
        member.update(
            presence="PRESENT", presence_proposed=True, presence_confirmed=True
        )


def test_national_pending_photos_are_informative_and_keep_plan_confirmation(panel):
    national_photos_pending(panel)
    open_panel(panel)
    members = panel.page.locator("[data-review-members]")
    expect(
        members.get_by_text("Sin fotografía — opcional en fase nacional", exact=True)
    ).to_have_count(8)
    expect(members.locator("img.ctt-review-photo")).to_have_count(8)
    expect(members.locator('input[type="checkbox"]')).to_have_count(2)
    expect(panel.page.locator("[data-review-blockers] li")).to_have_count(0)
    for index, player in enumerate(panel.state.view["players"]):
        if player["photo_status"] == "photo":
            expect(
                members.locator(".ctt-review-member").nth(index).locator("img")
            ).to_have_attribute("src", player["photo_preview"])
    approve = panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    expect(panel.page.locator("[data-review-confirm]")).not_to_be_checked()
    expect(approve).to_be_disabled()
    panel.page.locator("[data-review-confirm]").check()
    expect(approve).to_be_enabled()
    assert not any(
        path.endswith(("/challenge", "/assertion")) for path, _ in panel.state.calls
    )


def test_preparing_pending_photos_keeps_values_and_omits_server_policy_metadata(panel):
    national_photos_pending(panel)
    panel.state.view["ready_to_approve"] = False
    open_panel(panel)
    panel.page.locator("[data-review-team-confirm]").check()
    with panel.page.expect_response(lambda response: response.url.endswith("/edit")):
        panel.page.get_by_role("button", name="Preparar revisión", exact=True).click()
    submitted = panel.state.changes
    assert submitted["expected_proposal_id"] == panel.state.view["proposal_id"]
    changes = submitted["changes"]
    assert changes["confirm_present_staff"] is True
    assert len(changes["players"]) == 16
    assert all(member["presence_confirmed"] for member in changes["staff"])
    for original, update in zip(panel.state.view["players"], changes["players"]):
        for field in ("slot", "first_name", "last_name", "birth_date", "curp", "boxes"):
            assert update[field] == original[field]
        assert not set(update) & {
            "photo_status",
            "photo_preview",
            "source_card",
            "manual_fields",
            "policy_hash",
            "required_photo_kinds",
            "identity_policy",
        }
    assert not set(changes) & {
        "identity_policy",
        "policy_hash",
        "required_photo_kinds",
        "phase",
    }
    expect(panel.page.locator("[data-review-confirm]")).not_to_be_checked()
    expect(
        panel.page.get_by_role("button", name="Aprobar y capturar con passkey")
    ).to_be_disabled()
    assert not any(
        path.endswith(("/challenge", "/assertion")) for path, _ in panel.state.calls
    )


def test_global_confirmation_cannot_affirm_newly_selected_presence(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["staff"][1]["presence"] = "UNCERTAIN"
    panel.state.view["staff"][1]["presence_confirmed"] = False
    open_panel(panel)
    panel.page.locator('[data-presence="1"]').select_option("PRESENT")
    panel.page.locator("[data-review-team-confirm]").click()
    expect(panel.page.locator("[data-review-team-confirm]")).not_to_be_checked()
    expect(panel.page.locator('[data-presence-confirmed="1"]')).not_to_be_checked()
    expect(panel.page.locator("[data-review-status]")).to_contain_text(
        "Confirma individualmente"
    )


def test_prepared_staff_confirmation_survives_refresh_from_server(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["staff"][0]["presence_confirmed"] = False

    def save(route):
        changes = route.request.post_data_json["changes"]
        panel.state.view["staff"][0]["presence_confirmed"] = changes["staff"][0][
            "presence_confirmed"
        ]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(panel.state.view),
        )

    panel.page.route("**/edit", save)
    open_panel(panel)
    panel.page.locator("[data-review-team-confirm]").check()
    with panel.page.expect_response(lambda response: response.url.endswith("/edit")):
        panel.page.get_by_role("button", name="Preparar revisión", exact=True).click()
    panel.page.get_by_role("button", name="Consultar revisión", exact=True).click()
    expect(panel.page.locator('[data-presence-confirmed="0"]')).to_be_checked()
    expect(panel.page.locator("[data-review-team-confirm]")).not_to_be_checked()


def test_global_confirmation_requires_source_backed_presence_proposal(panel):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["staff"][0]["presence_proposed"] = False
    panel.state.view["staff"][0]["presence_confirmed"] = False
    open_panel(panel)
    panel.page.locator("[data-review-team-confirm]").check()
    expect(panel.page.locator('[data-presence-confirmed="0"]')).not_to_be_checked()


@pytest.mark.parametrize("field", ["source_page_index", None])
def test_person_exception_without_specific_field_opens_person_source(panel, field):
    panel.state.view["ready_to_approve"] = False
    panel.state.view["blockers"] = [
        {
            "code": "STAFF_REQUIRED",
            "kind": "STAFF",
            "slot": 1,
            "field": field,
            "message": "Revisa el puesto.",
            "navigation_target": {
                "kind": "STAFF",
                "slot": 1,
                "field": field,
                "source_page_index": 1,
            },
        }
    ]
    open_panel(panel)
    panel.page.get_by_role(
        "button", name="Director técnico · Nombre completo: Revisa el puesto."
    ).click()
    expect(panel.page.locator("[data-source-target] option:checked")).to_have_text(
        "Director técnico · Nombre completo"
    )


@pytest.fixture
def completion_page(panel):
    """Whole served page, sixteen synthetic players and intercepted capture replies."""
    page, state = panel.page, panel.state
    state.view["players"] = [
        {**deepcopy(state.view["players"][0]), "slot": slot} for slot in range(1, 17)
    ]
    state.attempt_id, state.result_id = str(uuid4()), str(uuid4())
    state.committed = False
    state.document_gets = []
    state.assertion_state = "committed"
    state.statuses = []
    state.result_patch = {}
    state.initial_session_status = "ready"
    state.view_committed = False
    state.legacy_blockers = [
        {
            "code": "STAFF_GOVERNANCE_CONTRACT_REQUIRED",
            "message": "Synthetic legacy player-only contract.",
        }
    ]
    path = "/registration-review/" + state.view["session_id"]
    template = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(("html",)),
    ).get_template("registration_review_detail.html")

    def result(value):
        reply = {
            "session_id": state.view["session_id"],
            "attempt_id": state.attempt_id,
            "state": value,
        }
        if value == "committed":
            reply["result_id"] = state.result_id
            reply.update(state.result_patch)
            if not state.result_patch:
                state.committed = True
        return reply

    def handle(route):
        request = route.request
        url_path = request.url.split("https://sam.chat", 1)[-1]
        if url_path == path:
            assert request.method == "GET"
            state.document_gets.append(request.url)
            status = "committed" if state.committed else state.initial_session_status
            markup = template.render(
                request=SimpleNamespace(url=SimpleNamespace(path=path)),
                review_session=SimpleNamespace(
                    id=state.view["session_id"],
                    status=status,
                    provider="reviewed_manifest",
                    tournament_slug="copa-telmex-2026",
                ),
                assets=[],
                team=state.view["team"],
                manager={},
                staff=state.view["staff"],
                players=[],
                notes="",
                validation={
                    "blockers": state.legacy_blockers,
                    "issues": [],
                    "ready_to_commit": False,
                },
                layout_regions={},
                overall_confidence="100%",
                canonical_review=None,
                tournament_options=[],
                protected_staff_review=True,
                staff_review_csrf="synthetic-csrf",
                staff_review_view=state.view,
            )
            route.fulfill(status=200, content_type="text/html", body=markup)
            return
        if url_path.startswith(panel.base) and "/source/" not in url_path:
            body = request.post_data_json if request.method == "POST" else None
            state.calls.append((url_path, body))
            if request.method == "POST":
                assert request.headers.get("x-ctt-csrf") == "synthetic-csrf"
            if url_path == panel.base + "/staff-approval/challenge":
                reply = {
                    "attempt_id": state.attempt_id,
                    "options": {
                        "publicKey": {
                            "challenge": base64.urlsafe_b64encode(b"c" * 32)
                            .decode()
                            .rstrip("="),
                            "rpId": "sam.chat",
                            "userVerification": "required",
                        }
                    },
                }
            elif url_path == panel.base + "/staff-approval/assertion":
                reply = result(state.assertion_state)
            elif url_path.startswith(panel.base + "/staff-approval/status?"):
                status = state.statuses.pop(0) if state.statuses else "uncertain"
                if isinstance(status, int):
                    route.fulfill(
                        status=status,
                        content_type="application/json",
                        body=json.dumps(
                            {"detail": {"message": "Synthetic status outage"}}
                        ),
                    )
                    return
                reply = result(status)
            elif url_path == panel.base + "/staff-approval":
                view = deepcopy(state.view)
                if state.view_committed:
                    view.update(result("committed"))
                reply = {
                    "staff_review_view": view,
                    "staff_review_csrf": "synthetic-csrf",
                }
            else:
                route.abort()
                return
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(reply)
            )
            return
        route.fallback()

    page.route("**/*", handle)
    page.add_init_script(
        """Object.defineProperty(navigator.credentials,'get',{value:async()=>({
        id:'synthetic',type:'public-key',rawId:new Uint8Array([1]).buffer,
        response:{clientDataJSON:new Uint8Array([1]).buffer,
          authenticatorData:new Uint8Array([1]).buffer,
          signature:new Uint8Array([1]).buffer,userHandle:null},
        getClientExtensionResults:()=>({})})});"""
    )
    return SimpleNamespace(
        page=page, state=state, path=path, base=panel.base, result=result
    )


def _open_completion_page(completion):
    completion.page.goto("https://sam.chat" + completion.path)
    expect(completion.page.locator("[data-review-team]")).to_contain_text("Equipo")


def _approve_completion(completion):
    completion.page.locator("[data-review-confirm]").check()
    completion.page.locator("[data-review-approve]").click()


def _assert_completed_page(completion, *, expected_gets=2, anchored=True):
    page = completion.page
    banner = page.locator("#ctt-capture-complete")
    expect(banner).to_contain_text("Captura completada")
    expect(banner).to_be_in_viewport()
    expect(banner).to_be_focused()
    assert banner.get_attribute("role") == "status"
    assert "rejected-banner" not in (banner.get_attribute("class") or "")
    expect(page.locator(".status-pill[data-status='committed']")).to_contain_text(
        "Capturado"
    )
    assert len(completion.state.document_gets) == expected_gets
    if anchored:
        assert page.url.endswith("#ctt-capture-complete")
    expect(banner.locator("a[href='/registration-review']")).to_be_visible()
    expect(page.locator("[data-action='modify']")).to_be_disabled()
    expect(page.locator("[data-action='reject']")).to_be_disabled()
    expect(page.get_by_role("button", name="Reprocesar", exact=True)).to_be_disabled()
    expect(
        page.get_by_role("button", name="Subir y anexar", exact=True)
    ).to_be_disabled()
    expect(page.locator("input[type='file']")).to_be_disabled()
    for button in page.locator("[data-commit-button]").all():
        expect(button).to_be_disabled()
    expect(page.locator("[data-review-approve]")).to_have_count(0)
    expect(
        page.get_by_text("La captura anterior admite jugadores", exact=False)
    ).to_have_count(0)
    expect(page.locator("[data-review-error]:visible")).to_have_count(0)
    expect(page.get_by_text("Borrador editable", exact=True)).to_have_count(0)
    expect(
        page.get_by_text("Para corregir y capturar utiliza", exact=False)
    ).to_have_count(0)
    expect(
        page.get_by_text("Su captura permanece bloqueada", exact=False)
    ).to_have_count(0)
    for control in page.locator(
        "#review-edit-form input:not([type='hidden']), "
        "#review-edit-form textarea, #review-edit-form select"
    ).all():
        expect(control).to_be_disabled()


def _assert_single_capture(completion):
    paths = [path for path, _ in completion.state.calls]
    assert paths.count(completion.base + "/staff-approval/challenge") == 1
    assert paths.count(completion.base + "/staff-approval/assertion") == 1
    assert not any(path.endswith("/edit") for path in paths)


@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1280, "height": 800},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_committed_capture_refreshes_full_page_with_visible_completion(completion_page):
    completion = completion_page
    _open_completion_page(completion)
    _approve_completion(completion)
    _assert_completed_page(completion)
    _assert_single_capture(completion)
    artifacts = Path(
        os.environ.get("SAMCHAT_BROWSER_ARTIFACT_DIR", ROOT / "artifacts" / "browser")
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    width = completion.page.viewport_size["width"]
    completion.page.screenshot(path=str(artifacts / f"committed-success-{width}.png"))
    completion.page.reload()
    _assert_completed_page(completion, expected_gets=3)
    _assert_single_capture(completion)


@pytest.mark.parametrize("transient_error", [False, True])
@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1280, "height": 800},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_committed_status_reconciles_uncertain_capture_without_resubmission(
    completion_page, transient_error
):
    completion = completion_page
    completion.state.assertion_state = "uncertain"
    completion.state.statuses = [503 if transient_error else "uncertain", "committed"]
    _open_completion_page(completion)
    _approve_completion(completion)
    expect(completion.page.locator("#ctt-capture-complete")).to_be_hidden()
    if transient_error:
        expect(completion.page.locator("[data-review-error]")).to_be_visible()
    _assert_completed_page(completion)
    _assert_single_capture(completion)
    statuses = [p for p, _ in completion.state.calls if "/staff-approval/status?" in p]
    assert len(statuses) == 2
    assert all("attempt_id=" + completion.state.attempt_id in p for p in statuses)


@pytest.mark.parametrize(
    "patch",
    [
        {"session_id": str(uuid4())},
        {"session_id": None},
        {"attempt_id": str(uuid4())},
        {"attempt_id": None},
        {"result_id": None},
        {"result_id": "invalid"},
        {"result_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_malformed_committed_reply_never_announces_success(completion_page, patch):
    completion = completion_page
    completion.state.result_patch = patch
    _open_completion_page(completion)
    _approve_completion(completion)
    completion.page.wait_for_timeout(100)
    expect(completion.page.locator("#ctt-capture-complete")).to_be_hidden()
    assert len(completion.state.document_gets) == 1
    _assert_single_capture(completion)


def test_committed_refresh_uses_same_completion_transition(completion_page):
    completion = completion_page
    completion.state.assertion_state = "uncertain"
    _open_completion_page(completion)
    _approve_completion(completion)
    completion.state.view_committed = True
    completion.page.locator("[data-review-refresh]").click()
    _assert_completed_page(completion)
    _assert_single_capture(completion)


def test_repeated_committed_status_causes_one_full_page_get(completion_page):
    completion = completion_page
    completion.state.assertion_state = "uncertain"
    _open_completion_page(completion)
    _approve_completion(completion)
    completion.state.committed = True
    reply = completion.result("committed")
    completion.page.evaluate(
        """reply => {
          const original=window.fetch;let release;
          const replace=window.history.replaceState.bind(window.history);
          window.history.replaceState=(...args)=>{
            sessionStorage.setItem('completion-sync-count',String(Number(sessionStorage.getItem('completion-sync-count')||0)+1));
            return replace(...args);
          };
          const pending=new Promise(resolve=>{release=resolve;});
          window.fetch=(input,...args)=>String(input).includes('/staff-approval/status?')
            ? pending.then(()=>new Response(JSON.stringify(reply),{status:200,headers:{'content-type':'application/json'}}))
            : original(input,...args);
          const button=document.querySelector('[data-review-result]');
          button.click();button.click();release();
        }""",
        reply,
    )
    _assert_completed_page(completion)
    _assert_single_capture(completion)
    assert (
        completion.page.evaluate("sessionStorage.getItem('completion-sync-count')")
        == "1"
    )


def test_committed_direct_entry_is_visible_without_mounting_review_panel(
    completion_page,
):
    completion = completion_page
    completion.state.committed = True
    completion.page.goto("https://sam.chat" + completion.path)
    _assert_completed_page(completion, expected_gets=1, anchored=False)
    assert completion.state.calls == []


@pytest.mark.parametrize("capture_state", ["uncertain", "expired", "stale"])
def test_noncommitted_capture_never_shows_completion(completion_page, capture_state):
    completion = completion_page
    completion.state.assertion_state = capture_state
    _open_completion_page(completion)
    _approve_completion(completion)
    completion.page.wait_for_timeout(100)
    expect(completion.page.locator("#ctt-capture-complete")).to_be_hidden()
    assert len(completion.state.document_gets) == 1
    _assert_single_capture(completion)
    if capture_state in ("expired", "stale"):
        expect(completion.page.locator("[data-review-confirm]")).not_to_be_checked()


@pytest.mark.parametrize("view_state", ["ready", "blocked", "unavailable", "rejected"])
def test_uncommitted_page_keeps_actual_blockers_and_never_reports_success(
    completion_page, view_state
):
    completion = completion_page
    if view_state != "ready":
        completion.state.view.update(
            state="unavailable" if view_state == "rejected" else view_state,
            ready_to_approve=False,
            blockers=[
                {"code": "SOURCE_MISSING", "message": "Synthetic pending source."}
            ],
        )
    if view_state == "rejected":
        completion.state.initial_session_status = "rejected"
    _open_completion_page(completion)
    expect(completion.page.locator("#ctt-capture-complete")).to_be_hidden()
    expect(
        completion.page.get_by_text("La captura anterior admite jugadores", exact=False)
    ).to_be_visible()
    expect(completion.page.locator("[data-review-approve]")).to_be_disabled()
    if view_state != "ready":
        expect(completion.page.locator("[data-review-blockers]")).to_contain_text(
            "Synthetic pending source."
        )
    assert len(completion.state.document_gets) == 1
    assert completion.state.calls == []
