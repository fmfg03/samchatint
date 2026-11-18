"""Native batch ceremony and full-page presentation with synthetic intercepted API.

No live sources, authority, capture rows or user credentials are used. Private
PostgreSQL and gate tests own actual materialization and provenance enforcement.
"""

import json
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
    run, block, attempt = [str(uuid4()) for _ in range(3)]
    members = [
        {
            "member_id": str(uuid4()),
            "document_id": f"{i + 1:064x}",
            "session_id": str(uuid4()),
            "team_name": f"Synthetic Team {i + 1}",
            "status": "ready",
            "manifest_sha256": "a" * 64,
            "issues": [],
            "source_pages": [{"page_index": 1}],
            "fields": [{"label": "Dato sintético", "value": "Fuente exacta"}],
        }
        for i in range(16)
    ]
    view = {
        "run_id": run,
        "version": 1,
        "status": "ready",
        "counts": {
            "identified": 16,
            "new_confirmed": 0,
            "already_captured": 0,
            "excluded": 0,
        },
        "metrics": {
            "autoaptos_al_corte": 16,
            "expedientes_pendientes_identificados_al_corte": 16,
            "paginas_unicas_de_autoaptos_al_corte": 32,
            "paginas_de_cedulas_pendientes_al_corte": 32,
            "cutoff": "2026-11-09T00:00:00Z",
        },
        "sample": [],
        "qualification": {"candidate_hash": "c" * 64, "status": "proposed"},
        "members": members,
        "total_members": 16,
        "next_block_limit": 5,
        "next_member_ids": [m["member_id"] for m in members[:5]],
        "blocks": [
            {
                "block_id": block,
                "version": 1,
                "status": "prepared",
                "member_count": 5,
                "root_hash": "d" * 64,
                "members": members[:5],
            }
        ],
    }
    state = SimpleNamespace(
        view=view,
        calls=[],
        server=None,
        credential=None,
        verified_assertions=0,
        view_reads=0,
        mode="complete",
        consented=False,
        catalog=None,
        correction_mode="stored",
        correction_version=3,
        member_fields=None,
    )
    buffer = BytesIO()
    Image.new("RGB", (612, 792), "#dfe8ef").save(buffer, format="PNG")
    template = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(("html",)),
    ).get_template("registration_review_batch.html")

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
        if path.startswith("/static/js/ctt_batch_review.js"):
            route.fulfill(
                status=200,
                content_type="text/javascript",
                body=(ROOT / "static/js/ctt_batch_review.js").read_text(),
            )
            return
        if path in {"/review", f"/registration-review-batches/{run}"}:
            route.fulfill(
                status=200,
                content_type="text/html",
                body=template.render(
                    request=SimpleNamespace(url=SimpleNamespace(path="/review")),
                    batch_review_csrf="synthetic-csrf",
                    batch_review_view=None if state.catalog else state.view,
                    batch_catalog=state.catalog,
                ),
            )
            return
        if "/sources/" in path:
            state.calls.append(("GET_SOURCE", path, None))
            route.fulfill(status=200, content_type="image/png", body=buffer.getvalue())
            return
        body = request.post_data_json if request.method == "POST" else None
        state.calls.append((request.method, path, body))
        if request.method == "POST":
            assert request.headers.get("x-ctt-batch-csrf") == "synthetic-csrf"
        base = "/api/registration-review-batches"
        if path == f"{base}/runs":
            state.catalog = None
            reply(route, {"run_id": run, "status": "ready"})
        elif path.startswith(f"{base}/runs/{run}/members/"):
            member_id = path.split("/members/", 1)[1].split("/", 1)[0]
            selected = next(
                item for item in state.view["members"] if item["member_id"] == member_id
            )
            if request.method == "GET":
                reply(
                    route,
                    {
                        "run_id": run,
                        "member_id": member_id,
                        "version": state.correction_version,
                        "status": selected["status"],
                        "document_id": selected["document_id"],
                        "team_name": selected["team_name"],
                        "fields": state.member_fields
                        or [
                            {
                                "field_id": "player:1:first_name",
                                "label": "Nombre según la fuente",
                                "value": (
                                    "Synthetic <img src=x "
                                    'onerror="window.correctionInjected=true">'
                                ),
                                "editable": selected["status"] == "exception",
                                "source_page_index": 1,
                            }
                        ],
                        "source_pages": [
                            {"page_index": 1, "width": 612, "height": 792}
                        ],
                    },
                )
            elif path.endswith("/corrections"):
                if state.correction_mode == "not_observed":
                    reply(
                        route,
                        {
                            "detail": {
                                "message": "No se recibió un resultado verificable."
                            }
                        },
                        503,
                    )
                    return
                state.correction_version += 1
                state.view["version"] += 1
                state.view["status"] = "analysis_pending"
                selected["status"] = "analysis_pending"
                state.view["qualification"]["status"] = "proposed"
                if state.correction_mode != "stale_prepared":
                    state.view["blocks"][0]["status"] = "revoked"
                if state.correction_mode == "stored_lost_response":
                    reply(
                        route,
                        {
                            "detail": {
                                "message": "Resultado perdido. Consulta la cédula."
                            }
                        },
                        503,
                    )
                    return
                reply(
                    route,
                    {
                        "run_id": run,
                        "member_id": member_id,
                        "version": state.correction_version,
                        "status": "analysis_pending",
                    },
                )
            elif path.endswith("/quality-report"):
                state.correction_version += 1
                state.view["version"] += 1
                state.view["status"] = "suspended"
                state.view["blocks"][0]["status"] = "revoked"
                reply(
                    route,
                    {
                        "run_id": run,
                        "member_id": member_id,
                        "version": state.correction_version,
                        "status": "suspended",
                    },
                )
            else:
                route.abort()
        elif path == f"{base}/runs/{run}/sample":
            state.view["version"] += 1
            if any(
                item["outcome"] == "critical_error" for item in body["observations"]
            ):
                state.view["qualification"]["status"] = "blocked"
                state.view["blocks"][0]["status"] = "blocked"
                state.view["next_member_ids"] = []
            reply(route, {"run_id": run, "version": state.view["version"]})
        elif path == f"{base}/blocks/{block}/challenge":
            from fido2.webauthn import UserVerificationRequirement

            offered, state.authentication = state.server.authenticate_begin(
                [state.credential],
                challenge=secrets.token_bytes(32),
                user_verification=UserVerificationRequirement.REQUIRED,
            )
            reply(
                route,
                {"block_id": block, "attempt_id": attempt, "options": dict(offered)},
            )
        elif path == f"{base}/blocks/{block}/assertion":
            from fido2.webauthn import AuthenticationResponse

            state.server.authenticate_complete(
                state.authentication,
                [state.credential],
                AuthenticationResponse.from_dict(body["assertion"]),
            )
            state.verified_assertions += 1
            state.consented = True
            if state.mode == "uncertain":
                reply(
                    route,
                    {
                        "detail": {
                            "message": "Respuesta perdida; consulta el mismo intento."
                        }
                    },
                    503,
                )
                return
            state.view["blocks"][0].update(
                status="active", version=2, attempt_id=attempt
            )
            reply(
                route,
                {
                    "block_id": block,
                    "consent_id": str(uuid4()),
                    "status": "active",
                    "expires_at": "2026-11-09T01:00:00Z",
                },
            )
        elif path.startswith(f"{base}/runs/{run}?"):
            if state.mode == "preparing":
                state.view_reads += 1
                if state.view_reads >= 2:
                    state.view["status"] = "ready"
                    state.view["version"] += 1
            if state.consented and state.mode == "complete":
                state.view_reads += 1
                if state.view_reads >= 2:
                    state.view["version"] = 3
                    state.view["blocks"][0].update(status="completed", version=3)
                    state.view["counts"]["new_confirmed"] = 5
                    for member in state.view["members"][:5]:
                        member["status"] = "committed"
                        member["result_id"] = str(uuid4())
            reply(
                route,
                {
                    "batch_review_view": state.view,
                    "batch_review_csrf": "synthetic-csrf",
                },
            )
        elif path == f"{base}/blocks/{block}/attempts/{attempt}":
            reply(
                route,
                {
                    "block_id": block,
                    "attempt_id": attempt,
                    "status": (
                        "uncertain"
                        if state.mode == "uncertain"
                        else state.view["blocks"][0]["status"]
                    ),
                },
            )
        elif path in [
            f"{base}/blocks/{block}/{action}"
            for action in ("pause", "resume", "revoke")
        ]:
            action = path.rsplit("/", 1)[1]
            state.view["blocks"][0]["status"] = {
                "pause": "paused",
                "resume": "active",
                "revoke": "revoked",
            }[action]
            state.view["blocks"][0]["version"] += 1
            state.view["version"] += 1
            reply(
                route, {"block_id": block, "status": state.view["blocks"][0]["status"]}
            )
        else:
            route.abort()

    page.context.route("**/*", handle)
    yield SimpleNamespace(page=page, state=state, run=run, block=block, attempt=attempt)


def credential(panel):
    from fido2.server import Fido2Server
    from fido2.webauthn import (
        AuthenticatorAttachment,
        PublicKeyCredentialRpEntity,
        PublicKeyCredentialUserEntity,
        RegistrationResponse,
        UserVerificationRequirement,
    )

    server = Fido2Server(
        PublicKeyCredentialRpEntity(id="sam.chat", name="SamChat"),
        verify_origin=lambda origin: origin == "https://sam.chat",
    )
    panel.state.server = server
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
    offered, registration = server.register_begin(
        PublicKeyCredentialUserEntity(
            id=b"synthetic-batch-user", name="synthetic", display_name="Synthetic"
        ),
        challenge=secrets.token_bytes(32),
        user_verification=UserVerificationRequirement.REQUIRED,
        authenticator_attachment=AuthenticatorAttachment.PLATFORM,
    )
    registered = panel.page.evaluate(
        """async offered => {
      const decode = value => Uint8Array.from(atob(
        value.replace(/-/g,'+').replace(/_/g,'/')
        + '='.repeat((4-value.length%4)%4)), c=>c.charCodeAt(0));
      const encode = value => {let s='';
        for (const b of new Uint8Array(value)) s+=String.fromCharCode(b);
        return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');};
      const publicKey=offered.publicKey;
      publicKey.challenge=decode(publicKey.challenge);
      publicKey.user.id=decode(publicKey.user.id);
      const c=await navigator.credentials.create({publicKey});
      return {id:c.id,rawId:encode(c.rawId),type:c.type,response:{
        clientDataJSON:encode(c.response.clientDataJSON),
        attestationObject:encode(c.response.attestationObject)},
        clientExtensionResults:c.getClientExtensionResults()};
    }""",
        dict(offered),
    )
    panel.state.credential = server.register_complete(
        registration, RegistrationResponse.from_dict(registered)
    ).credential_data


@pytest.mark.parametrize("width", [1280, 390])
def test_one_native_uv_authorizes_five_results_with_visible_final_confirmation(
    panel, width
):
    panel.page.set_viewport_size({"width": width, "height": 844})
    panel.page.goto("https://sam.chat/review")
    credential(panel)
    approve = panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    expect(approve).to_be_disabled()
    panel.page.locator("[data-batch-confirm]").check()
    approve.click()
    notice = panel.page.locator("[data-batch-complete]")
    expect(notice).to_be_visible(timeout=10000)
    expect(notice).to_contain_text("5 equipos confirmados")
    assert panel.state.verified_assertions == 1
    posts = [path for method, path, _ in panel.state.calls if method == "POST"]
    assert posts == [
        f"/api/registration-review-batches/blocks/{panel.block}/challenge",
        f"/api/registration-review-batches/blocks/{panel.block}/assertion",
    ]
    assert panel.page.evaluate(
        "document.activeElement.matches('[data-batch-complete]')"
    )
    assert panel.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert notice.evaluate(
        "el => {const r=el.getBoundingClientRect();"
        "return r.top>=0 && r.bottom<=innerHeight;}"
    )
    expect(approve).to_be_disabled()
    artifact = os.environ.get("SAMCHAT_BATCH_BROWSER_ARTIFACT_DIR")
    if artifact:
        target = Path(artifact)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        panel.page.screenshot(path=str(target / f"batch-completed-{width}.png"))
    panel.page.get_by_role("button", name="Consultar estado").click()
    assert panel.state.verified_assertions == 1
    assert len([1 for method, _, _ in panel.state.calls if method == "POST"]) == 2


def test_uncertain_assertion_and_stale_prepared_view_never_offer_second_approval(panel):
    panel.state.mode = "uncertain"
    panel.page.goto("https://sam.chat/review")
    credential(panel)
    panel.page.locator("[data-batch-confirm]").check()
    panel.page.get_by_role("button", name="Autorizar bloque con passkey").click()
    expect(panel.page.locator("[data-batch-error]")).to_contain_text(
        "Respuesta perdida"
    )
    panel.page.get_by_role("button", name="Consultar estado").click()
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    assert panel.state.verified_assertions == 1
    assert len([1 for method, _, _ in panel.state.calls if method == "POST"]) == 2
    assert any(
        "/attempts/" in path and method == "GET"
        for method, path, _ in panel.state.calls
    )


@pytest.mark.parametrize("state", ["expired", "revoked", "completed"])
def test_terminal_reload_has_closed_approval_and_no_capture_post(panel, state):
    panel.state.view["blocks"][0]["status"] = state
    panel.page.goto("https://sam.chat/review")
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    expect(
        panel.page.get_by_role("button", name="Reanudar", exact=True)
    ).to_be_disabled()
    panel.page.get_by_role("button", name="Consultar estado").click()
    assert not any(method == "POST" for method, _, _ in panel.state.calls)


def test_pause_resume_and_revoke_use_separate_bound_operations(panel):
    panel.state.view["blocks"][0]["status"] = "active"
    panel.page.goto("https://sam.chat/review")
    panel.page.get_by_role("button", name="Pausar", exact=True).click()
    expect(
        panel.page.get_by_role("button", name="Reanudar", exact=True)
    ).to_be_enabled()
    panel.page.get_by_role("button", name="Reanudar", exact=True).click()
    expect(panel.page.get_by_role("button", name="Pausar", exact=True)).to_be_enabled()
    panel.page.get_by_role("button", name="Revocar autorización").click()
    expect(
        panel.page.get_by_role("button", name="Reanudar", exact=True)
    ).to_be_disabled()
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    assert [
        path.rsplit("/", 1)[1]
        for method, path, _ in panel.state.calls
        if method == "POST"
    ] == ["pause", "resume", "revoke"]


def test_sample_source_view_and_contradiction_stay_in_same_panel(panel):
    member = deepcopy(panel.state.view["members"][0])
    panel.state.view["sample"] = [member]
    panel.page.goto("https://sam.chat/review")
    panel.page.locator("[data-batch-sample]").get_by_role(
        "button", name="Ver fuente 1"
    ).click()
    expect(panel.page.locator("[data-batch-source-panel]")).to_be_visible()
    expect(panel.page.locator("[data-batch-source-image]")).to_have_attribute(
        "src",
        f"/api/registration-review-batches/runs/{panel.run}"
        f"/sources/{member['document_id']}/pages/1",
    )
    expect(panel.page.locator("[data-batch-source-fields]")).to_contain_text(
        "Fuente exacta"
    )
    panel.page.locator("[data-sample-document]").select_option("critical_error")
    expect(
        panel.page.get_by_role("button", name="Guardar contraste propuesto")
    ).to_be_enabled()
    assert not any(method == "POST" for method, _, _ in panel.state.calls)


def test_catalog_creation_uses_selected_sources_without_manual_identifiers(panel):
    edition = str(uuid4())
    panel.state.catalog = {
        "source_manifests": [
            {
                "manifest_sha256": "a" * 64,
                "edition_id": edition,
                "label": "Consolidados de prueba",
                "pdf_count": 2,
                "document_count": 5,
            }
        ],
        "runs": [],
    }
    panel.page.goto("https://sam.chat/review")
    expect(
        panel.page.get_by_role("button", name="Preparar fuentes seleccionadas")
    ).to_be_disabled()
    panel.page.get_by_label("Consolidados de prueba").check()
    panel.page.get_by_role("button", name="Preparar fuentes seleccionadas").click()
    expect(panel.page).to_have_url(
        f"https://sam.chat/registration-review-batches/{panel.run}"
    )
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1
    assert posts[0][1]["edition_id"] == edition
    assert posts[0][1]["source_manifest_hashes"] == ["a" * 64]
    assert "approved" not in posts[0][1]


def test_saving_critical_sample_is_proposal_and_does_not_authorize_capture(panel):
    panel.state.view["sample"] = [deepcopy(panel.state.view["members"][0])]
    panel.page.goto("https://sam.chat/review")
    panel.page.locator("[data-sample-document]").select_option("critical_error")
    panel.page.get_by_role("button", name="Guardar contraste propuesto").click()
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    expect(
        panel.page.get_by_role("button", name="Preparar bloque", exact=True)
    ).to_be_disabled()
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1 and posts[0][0].endswith("/sample")
    assert posts[0][1]["observations"][0]["outcome"] == "critical_error"
    assert "approved" not in posts[0][1]


def test_source_and_sample_labels_are_rendered_as_text_not_markup(panel):
    panel.state.view["members"][0][
        "team_name"
    ] = '<img src=x onerror="window.batchInjected=true">'
    panel.page.goto("https://sam.chat/review")
    expect(panel.page.locator("[data-batch-members]")).to_contain_text("<img src=x")
    assert panel.page.evaluate("window.batchInjected === undefined")


def test_frozen_snapshot_metrics_are_distinct_from_current_progress(panel):
    panel.state.view["metrics"] = {
        "teams": {"numerator": 80, "denominator": 100},
        "pages": {"numerator": 160, "denominator": 200},
        "cutoff": "2026-11-09T00:00:00Z",
    }
    panel.state.view["counts"]["new_confirmed"] = 5
    panel.page.goto("https://sam.chat/review")
    expect(panel.page.locator("[data-batch-quality]")).to_contain_text("80/100 (80.0%)")
    expect(panel.page.locator("[data-batch-quality]")).to_contain_text(
        "160/200 (80.0%)"
    )
    expect(panel.page.locator("[data-batch-counts]")).to_contain_text("5")


def test_analysis_pending_polls_only_reads_and_never_offers_capture(panel):
    panel.state.mode = "preparing"
    panel.state.view.update(status="analysis_pending", blocks=[], next_member_ids=[])
    for member in panel.state.view["members"]:
        member["status"] = "analysis_pending"
    panel.page.goto("https://sam.chat/review")
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    expect(panel.page.locator("[data-batch-status]")).to_contain_text(
        "Listo para preparar", timeout=10000
    )
    assert panel.state.view_reads == 2
    assert not any(method == "POST" for method, _, _ in panel.state.calls)


def exception_panel(panel):
    selected = panel.state.view["members"][-1]
    selected["status"] = "exception"
    panel.page.goto("https://sam.chat/review")
    panel.page.get_by_role("button", name="Revisar esta excepción", exact=True).click()
    expect(panel.page.locator("[data-batch-corrections]")).to_be_visible()
    return selected


def test_exception_correction_is_field_proposal_and_invalidates_old_root(panel):
    panel.state.correction_mode = "stale_prepared"
    selected = exception_panel(panel)
    field = panel.page.locator('[data-correction-field="player:1:first_name"]')
    expect(field).to_be_enabled()
    assert panel.page.evaluate("window.correctionInjected === undefined")
    field.fill("Corrected source value")
    expect(panel.page.locator("[data-batch-confirm]")).to_be_disabled()
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-status]")).to_contain_text(
        "Corrección propuesta guardada"
    )
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1 and posts[0][0].endswith(
        f"/members/{selected['member_id']}/corrections"
    )
    assert posts[0][1] == {
        "expected_version": 3,
        "edits": [
            {"field_id": "player:1:first_name", "value": "Corrected source value"}
        ],
    }
    assert panel.state.verified_assertions == 0


@pytest.mark.parametrize("mode", ["not_observed", "stored_lost_response"])
def test_uncertain_correction_recovers_with_get_without_resubmit_or_old_approval(
    panel, mode
):
    panel.state.correction_mode = mode
    selected = exception_panel(panel)
    panel.page.locator("[data-correction-field]").fill("Proposed correction")
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-error]")).to_be_visible()
    panel.page.get_by_role("button", name="Consultar estado").click()
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    assert len([1 for method, _, _ in panel.state.calls if method == "POST"]) == 1
    assert (
        sum(
            method == "GET" and path.endswith(f"/members/{selected['member_id']}")
            for method, path, _ in panel.state.calls
        )
        >= 2
    )


def test_auto_ready_source_is_readonly_and_can_report_critical_discrepancy(panel):
    panel.page.goto("https://sam.chat/review")
    panel.page.get_by_role(
        "button", name="Contrastar datos de esta cédula", exact=True
    ).first.click()
    expect(panel.page.locator("[data-correction-field]")).to_be_disabled()
    expect(
        panel.page.get_by_role("button", name="Guardar corrección propuesta")
    ).to_be_disabled()
    panel.page.locator("[data-batch-quality-reason]").fill(
        "La fecha no coincide con el original"
    )
    panel.page.get_by_role("button", name="Informar discrepancia crítica").click()
    expect(panel.page.locator("[data-batch-status]")).to_contain_text(
        "grupo queda detenido"
    )
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1 and posts[0][0].endswith("/quality-report")
    assert set(posts[0][1]) == {"expected_version", "reason"}
    assert panel.state.verified_assertions == 0


def test_prepared_block_can_be_revoked_without_creating_capture_authority(panel):
    panel.page.goto("https://sam.chat/review")
    panel.page.get_by_role("button", name="Revocar autorización").click()
    expect(
        panel.page.get_by_role("button", name="Autorizar bloque con passkey")
    ).to_be_disabled()
    assert panel.state.verified_assertions == 0
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1 and posts[0][0].endswith("/revoke")


def typed_exception_fields():
    card = dict(page_index=1, x=50, y=50, width=400, height=400)
    return [
        dict(
            field_id="players.1.first_name",
            label="Nombre con recorte",
            value="Nombre correcto",
            editable=True,
            kind="source_field",
            rectangle=dict(page_index=1, x=100, y=100, width=90, height=30),
            card_bounds=card,
            allow_absent=True,
        ),
        dict(
            field_id="players.1.photo",
            label="Fotografía por contrastar",
            value="PRESENT",
            editable=True,
            kind="photo",
            rectangle=dict(page_index=1, x=200, y=200, width=100, height=100),
            card_bounds=card,
            allow_absent=False,
        ),
        dict(
            field_id="players.1.presence",
            label="Casilla admitida uno",
            value="UNCERTAIN",
            editable=True,
            kind="presence",
            rectangle=None,
            card_bounds=card,
            allow_absent=True,
        ),
    ]


@pytest.mark.parametrize("width", [1280, 390])
def test_typed_geometry_photo_presence_use_original_pixels_without_new_authority(
    panel, width
):
    panel.page.set_viewport_size({"width": width, "height": 900})
    panel.state.member_fields = typed_exception_fields()
    selected = exception_panel(panel)
    # An explicitly changed crop is a proposal even when the value is correct.
    panel.page.locator('[data-correction-geometry="players.1.first_name"]').check()
    panel.page.locator(
        '[data-correction-owner="players.1.first_name"][data-correction-coordinate="x"]'
    ).fill("101")
    panel.page.locator(
        '[data-correction-owner="players.1.photo"][data-correction-coordinate="width"]'
    ).fill("99")
    panel.page.locator('[data-correction-field="players.1.presence"]').select_option(
        "PRESENT"
    )
    panel.page.locator('[data-correction-preview-button="players.1.photo"]').click()
    preview = panel.page.locator('[data-correction-preview="players.1.photo"]')
    expect(preview).to_be_visible()
    assert preview.evaluate(
        "canvas => [...canvas.getContext('2d').getImageData(0,0,1,1).data]"
    ) == [223, 232, 239, 255]
    assert panel.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert (
        "[object Object]" not in panel.page.locator("[data-batch-review]").inner_text()
    )
    artifact = os.environ.get("SAMCHAT_BATCH_BROWSER_ARTIFACT_DIR")
    if artifact:
        Path(artifact).mkdir(parents=True, exist_ok=True)
        panel.page.screenshot(
            path=str(Path(artifact) / f"batch-manual-{width}.png"), full_page=True
        )
        panel.page.locator("[data-batch-corrections]").screenshot(
            path=str(Path(artifact) / f"batch-manual-panel-{width}.png")
        )
    expect(panel.page.locator("[data-batch-approve]")).to_be_disabled()
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-status]")).to_contain_text(
        "Corrección propuesta guardada"
    )
    posts = [
        (path, body) for method, path, body in panel.state.calls if method == "POST"
    ]
    assert len(posts) == 1
    assert posts[0][0].endswith(f"/members/{selected['member_id']}/corrections")
    assert posts[0][1] == {
        "expected_version": 3,
        "edits": [
            {
                "field_id": "players.1.first_name",
                "value": {
                    "kind": "source_field",
                    "value": "Nombre correcto",
                    "rectangle": dict(page_index=1, x=101, y=100, width=90, height=30),
                },
            },
            {
                "field_id": "players.1.photo",
                "value": {
                    "kind": "photo",
                    "presence": "PRESENT",
                    "rectangle": dict(page_index=1, x=200, y=200, width=99, height=100),
                },
            },
            {
                "field_id": "players.1.presence",
                "value": {"kind": "presence", "presence": "PRESENT"},
            },
        ],
    }
    assert panel.state.verified_assertions == 0
    assert (
        "todavía no se ha capturado"
        in panel.page.locator("[data-batch-status]").inner_text()
    )


@pytest.mark.parametrize(
    "key,value", [("x", "49"), ("x", "500"), ("width", "0"), ("height", "3.5")]
)
def test_typed_rectangles_cannot_leave_fixed_card_or_use_fractional_pixels(
    panel, key, value
):
    panel.state.member_fields = typed_exception_fields()[:1]
    exception_panel(panel)
    panel.page.locator('[data-correction-geometry="players.1.first_name"]').check()
    panel.page.locator(f'[data-correction-coordinate="{key}"]').fill(value)
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-error]")).to_contain_text("píxeles enteros")
    assert not any(method == "POST" for method, _, _ in panel.state.calls)
    assert panel.page.locator('[data-correction-coordinate="page_index"]').count() == 0


def test_known_supplied_photo_and_occupied_person_cannot_be_proposed_absent(panel):
    fields = typed_exception_fields()[1:]
    fields[1]["value"] = "PRESENT"
    fields[1]["allow_absent"] = False
    panel.state.member_fields = fields
    exception_panel(panel)
    for field in ("players.1.photo", "players.1.presence"):
        selector = panel.page.locator(f'[data-correction-field="{field}"]')
        expect(selector.locator('option[value="ABSENT"]')).to_have_attribute(
            "disabled", ""
        )
        # Even DOM tampering cannot make the UI send this forbidden proposal.
        selector.evaluate(
            "input => {input.value='ABSENT'; "
            "input.dispatchEvent(new Event('change', {bubbles:true}));}"
        )
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-error]")).to_contain_text(
        "no puede marcarse ausente"
    )
    assert not any(method == "POST" for method, _, _ in panel.state.calls)
    assert panel.page.get_by_role("button", name="Agregar casilla").count() == 0


def test_typed_geometry_only_change_and_lost_response_recovers_without_resubmit(panel):
    panel.state.member_fields = typed_exception_fields()[:1]
    panel.state.correction_mode = "stored_lost_response"
    selected = exception_panel(panel)
    panel.page.locator('[data-correction-geometry="players.1.first_name"]').check()
    # Explicitly re-propose the same geometry and value; do not erase the intent.
    panel.page.get_by_role("button", name="Guardar corrección propuesta").click()
    expect(panel.page.locator("[data-batch-error]")).to_be_visible()
    panel.page.get_by_role("button", name="Consultar estado").click()
    expect(panel.page.locator("[data-batch-approve]")).to_be_disabled()
    posts = [body for method, _, body in panel.state.calls if method == "POST"]
    assert len(posts) == 1
    assert posts[0]["edits"][0]["value"]["value"] == "Nombre correcto"
    assert posts[0]["edits"][0]["value"]["rectangle"]["x"] == 100
    assert (
        sum(
            method == "GET" and path.endswith(f"/members/{selected['member_id']}")
            for method, path, _ in panel.state.calls
        )
        >= 2
    )
