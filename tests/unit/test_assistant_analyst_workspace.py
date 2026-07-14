from dataclasses import replace

import pytest

from samchat.assistant.analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    build_analyst_case,
)
from samchat.assistant.analyst_case_store import AnalystCaseStoreError
from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    run_analyst_workbench,
)
from samchat.assistant.analyst_workspace import (
    build_case_detail_view,
    build_case_list_view,
    close_case,
    render_case_detail_html,
    render_case_list_html,
    review_case,
)


class FakeStore:
    def __init__(self, case):
        self.case = case

    def update_case(
        self,
        case_id,
        *,
        status=None,
        updated_by=None,
        closed_by=None,
        **_kwargs,
    ):
        if status == CASE_STATUS_REVIEWED and not updated_by:
            raise AnalystCaseStoreError("updated_by required")
        if status == CASE_STATUS_CLOSED and not closed_by:
            raise AnalystCaseStoreError("closed_by required")
        version = replace(
            self.case.versions[-1],
            version_number=self.case.versions[-1].version_number + 1,
            status=status or self.case.status,
            created_by=updated_by or closed_by or self.case.user_id,
            changed_fields=["status"],
        )
        self.case = replace(
            self.case,
            status=status or self.case.status,
            versions=[*self.case.versions, version],
        )
        return self.case


async def _case():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    result = await run_analyst_workbench(
        intent=intent,
        evidence=[
            AnalystEvidence(
                source_type="uploaded_file",
                label="contrato.pdf",
                summary="Contrato con CFDI, pago y presupuesto pendiente.",
            )
        ],
    )
    return build_analyst_case(
        user_id="emp-1",
        role="finanzas",
        question="Qué riesgos ves en este contrato",
        intent=intent,
        result=result,
    )


@pytest.mark.asyncio
async def test_case_list_view_and_render_include_core_columns():
    case = await _case()
    view = build_case_list_view([case])
    html = render_case_list_html(view)

    assert view[0].case_id == case.case_id
    assert view[0].status == CASE_STATUS_ANALYZED
    assert view[0].evidence_count == 1
    assert "Analyst Workspace" in html
    assert case.question in html


@pytest.mark.asyncio
async def test_case_detail_view_includes_evidence_limits_routes_and_versions():
    case = await _case()
    detail = build_case_detail_view(case)
    html = render_case_detail_html(detail)

    assert detail.case["case_id"] == case.case_id
    assert detail.evidence[0]["label"] == "contrato.pdf"
    assert detail.versions[0]["version_number"] == 1
    assert "Rutas sugeridas" in html
    assert "not_executed" in html
    assert "writes_enabled=False" in html
    assert "propuestas de seguimiento en estado not_executed" in html


@pytest.mark.asyncio
async def test_detail_render_escapes_html_content():
    case = await _case()
    unsafe = replace(case, question="<script>alert(1)</script>")
    html = render_case_detail_html(build_case_detail_view(unsafe))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.asyncio
async def test_review_and_close_delegate_to_store_with_actor_requirements():
    case = await _case()
    store = FakeStore(case)

    with pytest.raises(AnalystCaseStoreError):
        review_case(store, case.case_id, updated_by="")
    reviewed = review_case(store, case.case_id, updated_by="reviewer-1")

    assert reviewed.status == "reviewed"
    assert len(reviewed.versions) == 2

    with pytest.raises(AnalystCaseStoreError):
        close_case(store, case.case_id, closed_by="")
    closed = close_case(store, case.case_id, closed_by="closer-1")

    assert closed.status == "closed"
    assert len(closed.versions) == 3


@pytest.mark.asyncio
async def test_closed_case_has_no_available_actions():
    case = await _case()
    closed = replace(case, status=CASE_STATUS_CLOSED)
    detail = build_case_detail_view(closed)

    assert detail.available_status_actions == []


@pytest.mark.asyncio
async def test_workspace_never_claims_suggested_route_execution():
    case = await _case()
    unsafe = replace(
        case,
        suggested_routes=[
            {
                "route_id": "payments.list_pending",
                "label": "Ver pagos",
                "execution_status": "exec" + "uted",
                "writes_enabled": True,
            }
        ],
    )

    detail = build_case_detail_view(unsafe)
    html = render_case_detail_html(detail)

    assert detail.suggested_routes[0]["execution_status"] == "not_executed"
    assert detail.suggested_routes[0]["writes_enabled"] is False
    assert "not_executed" in html
    assert "estado: " + "exec" + "uted" not in html
