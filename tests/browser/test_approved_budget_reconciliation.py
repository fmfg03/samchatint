"""Synthetic browser journey through actual reconciliation renderers.

POST authorization/transactions are covered by registered-route and PostgreSQL tests;
these intercepted responses exercise the browser's forms, escaping and review UI.
"""

import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import expect

import devnous.gastos  # noqa: F401
from devnous.gastos.routes.admin_budget_ui import (
    _render_budget_movement_details,
    render_budget_reconciliation_preview,
)

VERSION = "11111111-1111-4111-8111-111111111111"
TOURNAMENT = "22222222-2222-4222-8222-222222222222"
CONCEPT = "33333333-3333-4333-8333-333333333333"
LINE = "44444444-4444-4444-8444-444444444444"
KEY = "expense:55555555-5555-4555-8555-555555555555"
OUT = Path(
    os.environ.get(
        "SAMCHAT_BROWSER_ARTIFACT_DIR", "/tmp/approved-reconciliation-browser"
    )
)


def detail(*, writer=True):
    line = {
        "id": LINE,
        "budget_concept_id": CONCEPT,
        "concept_name": "Hospedaje",
        "phase": "Fase Nacional",
        "line_direction": "expense",
        "tournament_id": TOURNAMENT,
    }
    movement = {
        "movement_key": KEY,
        "concept_key": CONCEPT,
        "budget_concept_name": "Hospedaje",
        "effective_phase": "Fase Nacional",
        "kind": "pending_accounting",
        "amount": 116,
        "reconciliation_status": "unassigned",
        "budget_line_id": None,
        "document_reference": "S-26000450",
    }
    content = _render_budget_movement_details(
        [line],
        [movement],
        version_id=VERSION,
        tournament_key="LTTB",
        edition_year=2026,
        phase_filter="Fase Nacional",
        can_edit=False,
        can_reconcile=writer,
        reconciliation_lines=[line],
        active_concept_ids={CONCEPT},
        reconciliation_csrf_token="synthetic-csrf",
        tournament_id=TOURNAMENT,
    )
    return (
        '<html lang="es"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><body>'
        + content
        + "</body></html>"
    )


@pytest.mark.parametrize("width,height", [(1366, 900), (390, 844)])
def test_existing_target_review_confirm_and_cancel(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    reason = 'Revisión <script>window.injected=true</script> & "aprobada"'

    def serve(route):
        request = route.request
        if request.method == "GET":
            route.fulfill(content_type="text/html; charset=utf-8", body=detail())
            return
        values = {key: value[0] for key, value in parse_qs(request.post_data).items()}
        posts.append((request.url, values))
        if request.url.endswith("/preview"):
            intent = {
                "budget_version_id": VERSION,
                "edition_year": 2026,
                "reason": values["reason"],
                "assignments": [
                    {
                        "tournament_id": TOURNAMENT,
                        "movement_key": KEY,
                        "budget_line_id": LINE,
                    }
                ],
            }
            html = render_budget_reconciliation_preview(
                {
                    "plan_sha256": "a" * 64,
                    "assignments": [
                        {
                            "reference": "S-26000450",
                            "kind": "pending_accounting",
                            "concept_name": "Hospedaje",
                            "phase": "Fase Nacional",
                            "amount": 116,
                            "source_identity": {"private": "NOT FOR THE BROWSER"},
                        }
                    ],
                },
                intent=intent,
                return_context={
                    "tournament_key": "LTTB",
                    "phase_filter": "Fase Nacional",
                },
                csrf_token="synthetic-csrf",
                nonce="synthetic-nonce",
                cancel_url="http://reconciliation.test/detail",
            )
            route.fulfill(content_type="text/html; charset=utf-8", body=html)
        else:
            assert values["confirmed"] == "true"
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body="<p>Movimiento vinculado. Pendiente de contabilización.</p>",
            )

    page.route("http://reconciliation.test/**", serve)
    page.goto("http://reconciliation.test/detail")
    # Movement details are deliberately collapsed in the shared dashboard.
    for details in page.locator("details").all():
        details.evaluate("element => element.open = true")
    page.locator('[name="reason"]').fill(reason)
    page.get_by_role("button", name="Revisar conciliación").click()
    expect(page.get_by_role("heading", name="Revisar conciliación")).to_be_visible()
    expect(page.get_by_text("Pendiente de contabilización", exact=True)).to_be_visible()
    expect(page.get_by_text(reason, exact=True)).to_be_visible()
    assert page.evaluate("window.injected") is None
    assert "NOT FOR THE BROWSER" not in page.content()
    assert page.locator('[name="budget_amount"]').count() == 0
    page.get_by_role("button", name="Confirmar conciliación").click()
    assert len(posts) == 1  # Required explicit confirmation blocks submission.
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(OUT / f"reconciliation-review-{width}.png"), full_page=True
    )
    page.get_by_role("link", name="Cancelar y volver").click()
    expect(page.get_by_role("button", name="Revisar conciliación")).to_have_count(1)
    assert len(posts) == 1
    for details in page.locator("details").all():
        details.evaluate("element => element.open = true")
    page.locator('[name="reason"]').fill(reason)
    page.get_by_role("button", name="Revisar conciliación").click()
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Confirmar conciliación").click()
    expect(
        page.get_by_text("Movimiento vinculado. Pendiente de contabilización.")
    ).to_be_visible()
    assert posts[-1][1]["reason"] == reason
    assert posts[-1][1]["movement_key"] == KEY
    assert posts[-1][1]["expected_plan_sha256"] == "a" * 64


def test_readonly_movement_has_no_reconciliation_form(page):
    page.route(
        "http://reconciliation.test/**",
        lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body=detail(writer=False),
        ),
    )
    page.goto("http://reconciliation.test/detail")
    assert page.get_by_role("button", name="Revisar conciliación").count() == 0
    assert page.locator('[name="reason"]').count() == 0
    assert "Pendiente de vincular" in page.content()
