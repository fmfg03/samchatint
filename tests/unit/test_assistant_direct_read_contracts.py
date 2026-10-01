"""Inventory closure and actual Owner/vendor reader wiring; local doubles only."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from samchat.assistant.conversation_context import contextual_read_frame
from samchat.assistant.conversation_service import finalize_contextual_response
from samchat.assistant.direct_read_contracts import (
    PROPOSAL_READERS,
    READ_TOOL_CONTRACTS,
    REPORT_SCHEMAS,
    TECHNICAL_READERS,
    business_evidence_limit,
)
from samchat.assistant.read_evidence import validate_read_evidence


def test_inventory_covers_every_exposed_read_and_no_write():
    from samchat.assistant import router

    exposed = {item["function"]["name"] for item in router._tool_defs()}
    assert set(READ_TOOL_CONTRACTS) == router.READ_TOOLS
    assert router.READ_TOOLS <= exposed
    assert not (set(READ_TOOL_CONTRACTS) & router.WRITE_TOOLS)


def source():
    return SimpleNamespace(
        schema_version="local.v1",
        source_hash="sha256:test",
        domain_write_performed=False,
        project=SimpleNamespace(
            id="tor-1",
            name="Copa Local",
            active=True,
            categorias=["Sub 15"],
            etapas=["Inscripcion"],
        ),
        operations_link=SimpleNamespace(operations_tournament_slug="copa-local"),
        observed_operations=SimpleNamespace(
            available=True,
            scope_slug="copa-local",
            teams_count=3,
            players_count=42,
            categories=["Sub 15"],
            branches=["Varonil"],
            states=["CDMX"],
            municipalities=["Benito Juarez"],
        ),
        unavailable_components=["rich_tournament_dates", "communications"],
    )


OWNER_TOOLS = sorted(
    set(REPORT_SCHEMAS)
    - {"assistant_historical_accounting_precedent", "finance_closeout_diagnostics"}
)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", OWNER_TOOLS)
async def test_owner_real_router_report_positive_and_corruption(
    monkeypatch, tmp_path, tool
):
    from samchat.assistant import router

    # Keep all report construction and router wrappers real. Redirect workspace
    # reads to an empty fixture directory, and replace only tournament source IO.
    for name in (
        "build_owner_pack_live_snapshot_report",
        "build_owner_pack_readiness_from_scope",
        "build_owner_entity_folder_workspace_from_tournament_source",
    ):
        monkeypatch.setattr(
            router, name, partial(getattr(router, name), root_dir=tmp_path)
        )
    monkeypatch.setattr(
        router, "inspect_tournament_source", AsyncMock(return_value=source())
    )
    args = {}
    if tool in {
        "assistant_owner_pack_live_snapshot",
        "assistant_owner_pack_live_brief",
    }:
        args = {
            "surface_id": "entity_folder",
            "tournament_slug": "copa-local",
            "entity_name": "CDMX",
        }
    elif tool in {
        "assistant_owner_entity_dossier_live",
        "assistant_owner_entity_folder_workspace",
        "assistant_sports_operations_status",
    }:
        args = {"tournament_id": "tor-1", "entity_name": "CDMX"}
    elif tool == "assistant_owner_variable_query":
        args = {
            "question": "¿Cuántos equipos reales hay?",
            "tournament_id": "tor-1",
            "entity_name": "CDMX",
        }
    result = await router._run_read_tool(
        tool,
        args,
        gastos_session=AsyncMock(),
        tournament_key_default=None,
        current_role="admin",
    )
    assert validate_read_evidence(tool, result)[0], (tool, result)
    for corrupted in (
        {},
        {**result, "writes_attempted": 1},
        {**result, "side_effects_detected": 1},
        {**result, "execution_status": "executed"},
        {**result, "ok": False},
    ):
        assert not validate_read_evidence(tool, corrupted)[0]
    without_contract_field = dict(result)
    del without_contract_field[next(iter(REPORT_SCHEMAS[tool]))]
    assert not validate_read_evidence(tool, without_contract_field)[0]
    if tool == "assistant_owner_pack_readiness":
        # Exact reported regression: no live facts yet still valid readiness evidence.
        assert result["status"] == "schema_only_no_live_evidence"
        answer = "Faltantes del Owner Pack: seleccionar torneo y reunir evidencia de las secciones."
        safe, trace = finalize_contextual_response(
            answer,
            [{"tool": tool, "result": result}],
            work_frame=contextual_read_frame(
                "¿Qué falta para tener listo el Owner Pack?", []
            ),
            maybe_append_export_prompt=lambda text, trace: text,
        )
        assert answer in safe
        assert any(
            step.get("assistant_response_sufficiency_gate", {}).get("ok") is True
            for step in trace
        )
        safe, _ = finalize_contextual_response(
            "Se pagaron 42 MXN. Fuente: Owner Pack.",
            [{"tool": tool, "result": result}],
            work_frame=contextual_read_frame("¿Cuánto IVA se pagó?", []),
            maybe_append_export_prompt=lambda text, trace: text,
        )
        assert "Se pagaron 42" not in safe


@pytest.mark.asyncio
async def test_vendor_payments_real_reader_allows_only_bounded_estimate():
    from samchat.assistant import router

    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        all=lambda: [
            SimpleNamespace(
                id="doc-1",
                numero_referencia="SOL-1",
                estado="aprobado",
                fecha_pago=None,
                pagado_en=None,
                monto_total=42,
                monto_solicitado=42,
                proveedor="Proveedor",
                rfc="TEST",
            )
        ]
    )
    result = await router._run_read_tool(
        "finance_vendor_payments",
        {"vendor_name": "Proveedor"},
        gastos_session=session,
        tournament_key_default=None,
        current_role="admin",
    )
    assert result["total_pagado"] == 42
    assert "Estimado" in result["nota"]
    assert validate_read_evidence("finance_vendor_payments", result)[0]
    frame = contextual_read_frame(
        "¿Qué importes tienen los documentos del proveedor?", []
    )
    trace = [{"tool": "finance_vendor_payments", "result": result}]
    bounded = "Estimado de los documentos consultados: 42 MXN. Fuente: SOL-1; aprobación no acredita pago."
    safe, _ = finalize_contextual_response(
        bounded,
        trace,
        work_frame=frame,
        maybe_append_export_prompt=lambda text, trace: text,
    )
    assert bounded in safe
    for unsupported in (
        "Se pagaron 42 MXN. Fuente: SOL-1.",
        "IVA estimado de documentos consultados: 42 MXN. Fuente: SOL-1.",
    ):
        safe, _ = finalize_contextual_response(
            unsupported,
            trace,
            work_frame=frame,
            maybe_append_export_prompt=lambda text, trace: text,
        )
        assert unsupported not in safe
    for field in ("nota", "moneda", "documentos", "vendor_name_query"):
        missing = dict(result)
        del missing[field]
        assert not validate_read_evidence("finance_vendor_payments", missing)[0]


@pytest.mark.parametrize("tool", sorted(TECHNICAL_READERS | PROPOSAL_READERS))
@pytest.mark.parametrize("domain", ["finance", "owner", "mixed", "operations"])
def test_intrinsic_nonbusiness_capability_limits_are_explicit(tool, domain):
    expected = READ_TOOL_CONTRACTS[tool]
    assert business_evidence_limit(tool, "fuente", domain) == expected
    assert not validate_read_evidence(tool, {"ok": True, "payload": {"amount": 42}})[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("assistant_institutional_artifacts", {}),
        ("assistant_soul_data_coverage", {"include_live_soul": False}),
        ("assistant_tournament_soul_coverage", {"tournament_slug": "test"}),
    ],
)
async def test_other_real_coverage_wrappers(monkeypatch, tool, args):
    from samchat.assistant import router

    monkeypatch.setattr(
        router,
        "build_tournament_soul_snapshot",
        AsyncMock(return_value={"tournaments": []}),
    )
    result = await router._run_read_tool(
        tool,
        args,
        gastos_session=AsyncMock(),
        tournament_key_default=None,
        current_role="admin",
    )
    assert validate_read_evidence(tool, result)[0]
    assert not validate_read_evidence(tool, {**result, "read_only": False})[0]


@pytest.mark.asyncio
async def test_expense_search_actual_reader_preserves_bounded_rows():
    from samchat.assistant import router

    expense = SimpleNamespace(
        id="e1",
        fecha=None,
        proyecto="P",
        concepto="Hotel",
        gasto_cantidad=42,
        metodo_pago="transferencia",
        estado_reembolso="pendiente",
        numero_referencia="G-1",
        nombre_enviador="Test",
    )
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [expense])
    )
    result = await router._run_read_tool(
        "finance_expense_search",
        {"query": "Hotel"},
        gastos_session=session,
        tournament_key_default=None,
        current_role="admin",
    )
    assert validate_read_evidence("finance_expense_search", result)[0]
    assert result["gastos"][0]["numero_referencia"] == "G-1"
    assert (
        business_evidence_limit(
            "finance_expense_search", "estimado de registros consultados", "finance"
        )
        is None
    )
    assert (
        business_evidence_limit("finance_expense_search", "total de iva", "finance")
        == "reader_has_no_vat_breakdown"
    )
    assert (
        business_evidence_limit("finance_expense_search", "total de gastos", "finance")
        == "bounded_rows_not_complete_population"
    )
    assert not validate_read_evidence(
        "finance_expense_search", {**result, "total_registros": 2}
    )[0]


def test_closeout_real_report_contract():
    from samchat.assistant.closeout_diagnostics import (
        build_closeout_diagnostics_from_platform,
    )

    result = build_closeout_diagnostics_from_platform(
        {
            "period": {"year": 2026, "month": 8},
            "accounting_close_center": {
                "polizas_count": 3,
                "unbalanced_count": 0,
                "unbalanced_polizas": [],
                "pending_coi_expenses_count": 0,
                "pending_coi_expenses": [],
            },
            "tax_readiness": {"diot_blockers_count": 0, "blockers": []},
        }
    ).to_dict()
    assert validate_read_evidence("finance_closeout_diagnostics", result)[0]
    del result["source_summary"]
    assert not validate_read_evidence("finance_closeout_diagnostics", result)[0]


@pytest.mark.asyncio
async def test_historical_actual_report_contract():
    from samchat.assistant import router

    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(first=lambda: None)
    result = await router._run_read_tool(
        "assistant_historical_accounting_precedent",
        {"query": "hotel", "company_code": "01"},
        gastos_session=session,
        tournament_key_default=None,
        current_role="admin",
    )
    assert validate_read_evidence("assistant_historical_accounting_precedent", result)[
        0
    ]
    assert (
        result["candidates"] == []
    )  # Evidence of source absence, never an accounting assignment.
    assert not validate_read_evidence(
        "assistant_historical_accounting_precedent", {**result, "safety_summary": {}}
    )[0]


@pytest.mark.parametrize(
    "tool,result,required",
    [
        (
            "finance_ops_query",
            {
                "filters": {},
                "nota": "BD gastos",
                "expenses": {
                    "totals": {"registros": 0, "monto_total": 0, "moneda": "MXN"},
                    "items": [],
                },
                "documents": {
                    "totals": {"registros": 0, "monto_total": 0, "moneda": "MXN"},
                    "items": [],
                },
            },
            "nota",
        ),
        (
            "tournament_expediente_snapshot",
            {
                "ok": True,
                "tournament": {"id": "t1"},
                "summary": {"entities": 0},
                "entities": [],
                "sources": ["tournaments"],
            },
            "sources",
        ),
        (
            "tournament_registration_breakdown",
            {
                "tournament_key": "t1",
                "source": "teams",
                "nota": "DB",
                "total_equipos": 0,
                "total_jugadores": 0,
                "desglose_por_municipio": [],
            },
            "source",
        ),
        (
            "tournament_ops_query",
            {
                "source": "teams",
                "nota": "DB",
                "filters": {},
                "totals": {"equipos": 0, "jugadores": 0},
                "teams": [],
                "players": [],
            },
            "source",
        ),
    ],
)
def test_direct_source_schemas_reject_missing_metadata(tool, result, required):
    assert validate_read_evidence(tool, result)[0]
    missing = dict(result)
    del missing[required]
    assert not validate_read_evidence(tool, missing)[0]
    for error in ({}, {"ok": False}, {"coverage": "not_queried"}, {"status": "error"}):
        assert not validate_read_evidence(tool, error)[0]
