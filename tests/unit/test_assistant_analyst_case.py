from datetime import datetime, timezone

import pytest

from samchat.assistant.analyst_case import build_analyst_case
from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    run_analyst_workbench,
)


CREATED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _policy_blocks_operational_writes(case):
    assert case.writes_policy == {
        "product_case_writes_allowed": True,
        "operational_writes_allowed": False,
        "route_execution_allowed": False,
        "provider_activation_allowed": False,
    }


@pytest.mark.asyncio
async def test_builds_waiting_context_case_without_runtime_writes():
    intent = detect_analyst_intent("Explícame esta balanza")
    result = await run_analyst_workbench(intent=intent, evidence=[])

    case = build_analyst_case(
        user_id="emp-1",
        role="finanzas",
        question="Explícame esta balanza",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )

    assert case.case_id.startswith("analyst_case_")
    assert case.status == "waiting_context"
    assert case.user_id == "emp-1"
    assert case.role == "finanzas"
    assert case.question == "Explícame esta balanza"
    assert case.current_answer == result.answer
    assert case.next_questions == result.next_questions
    assert case.evidence == []
    assert case.suggested_routes[0]["route_id"] == "evidence.collect_context"
    assert case.suggested_routes[0]["execution_status"] == "not_executed"
    assert case.suggested_routes[0]["writes_enabled"] is False
    _policy_blocks_operational_writes(case)


@pytest.mark.asyncio
async def test_builds_analyzed_case_with_initial_version():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary="Contrato con penalizacion y responsable faltante.",
        )
    ]
    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    case = build_analyst_case(
        user_id="emp-2",
        role="direccion",
        question="Qué riesgos ves en este contrato",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )

    assert case.status == "analyzed"
    assert case.analyst_intent["analyst_intent"] == "risk_review"
    assert case.evidence == result.evidence
    assert case.caveats == result.caveats
    assert len(case.versions) == 1
    version = case.versions[0]
    assert version.version_id.startswith("analyst_case_version_")
    assert version.created_at == "2026-07-14T12:00:00+00:00"
    assert version.created_by == "emp-2"
    assert version.status == "analyzed"
    assert version.answer == result.answer
    assert version.answer_contract == result.answer_contract
    _policy_blocks_operational_writes(case)


@pytest.mark.asyncio
async def test_routed_operational_case_stays_open_and_inert():
    intent = detect_analyst_intent("Qué CFDIs están pendientes")
    result = await run_analyst_workbench(intent=intent, evidence=[])

    case = build_analyst_case(
        user_id="emp-3",
        role="operaciones",
        question="Qué CFDIs están pendientes",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )

    assert case.status == "open"
    assert case.suggested_routes[0]["route_id"] == "cfdi.list_pending"
    assert case.suggested_routes[0]["execution_status"] == "not_executed"
    assert case.suggested_routes[0]["writes_enabled"] is False
    assert "writes" in case.suggested_routes[0]["blocked_capabilities"]
    assert "route_execution" in case.suggested_routes[0][
        "blocked_capabilities"
    ]
    _policy_blocks_operational_writes(case)


@pytest.mark.asyncio
async def test_case_ids_are_deterministic_for_same_inputs():
    intent = detect_analyst_intent("Explícame esta balanza")
    result = await run_analyst_workbench(intent=intent, evidence=[])
    kwargs = {
        "user_id": "emp-1",
        "role": "finanzas",
        "question": "Explícame esta balanza",
        "intent": intent,
        "result": result,
        "created_at": CREATED_AT,
    }

    first = build_analyst_case(**kwargs)
    second = build_analyst_case(**kwargs)

    assert first.case_id == second.case_id
    assert first.versions[0].version_id == second.versions[0].version_id
    assert first.to_dict() == second.to_dict()


@pytest.mark.asyncio
async def test_provider_unavailable_case_waits_for_context_without_provider():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary="Contrato con penalizacion y responsable faltante.",
        )
    ]

    async def provider_raises(_intent, _evidence):
        raise RuntimeError("provider unavailable")

    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        provider_allowed=True,
        provider_fn=provider_raises,
    )
    case = build_analyst_case(
        user_id="emp-4",
        role="finanzas",
        question="Qué riesgos ves en este contrato",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )

    assert case.status == "waiting_context"
    assert case.writes_policy["provider_activation_allowed"] is False
    assert case.versions[0].status == "waiting_context"
