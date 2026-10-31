"""Presentation/native ceremony contracts, with intercepted synthetic API replies.

These tests do not claim SQL capture or operator accreditation. The real
workflow/PostgreSQL suites own those boundaries; no live request leaves here.
"""

import base64
from io import BytesIO
import json
import logging
from pathlib import Path
import secrets
from types import SimpleNamespace
from uuid import uuid4

from jinja2 import Environment, select_autoescape
from PIL import Image
from playwright.sync_api import expect
import pytest

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
                "presence_confirmed": True,
                **person,
            },
            {
                "slot": 2,
                "role": "auxiliar",
                "presence": "ABSENT",
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
        if path == "/static/js/ctt_staff_review.js":
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
                PublicKeyCredentialUserEntity,
                UserVerificationRequirement,
                AuthenticatorAttachment,
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
            from fido2.webauthn import RegistrationResponse
            from hashlib import sha256

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
