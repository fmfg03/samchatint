import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_live_evidence import (
    LiveEvidenceContext,
    _money,
    _requested_live_evidence_sources,
    _safe_scalar,
    acquire_live_analyst_evidence,
    build_isolated_sqlalchemy_live_evidence_rows_provider,
    build_sqlalchemy_live_evidence_rows_provider,
    configured_live_evidence_sources,
)
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    build_analyst_evidence_pack,
    rank_analyst_evidence,
    run_analyst_workbench,
)


def _context(
    *permissions,
    role="empleado",
    department=None,
    question=(
        "Explica gastos, CFDI, presupuestos, proyectos, pagos, proveedores "
        "y documentos financieros"
    ),
):
    return LiveEvidenceContext(
        employee_id="emp-1",
        role=role,
        permissions=set(permissions),
        question=question,
        department=department,
        reference_date=date(2026, 7, 14),
        limit_per_source=3,
    )


def _intent():
    return detect_analyst_intent("Explica este caso")


def _live_row(source_id="row-1"):
    return {
        "id": source_id,
        "label": "Gasto de hospedaje",
        "summary": "Gasto reciente ligado al proyecto nacional.",
        "date": "2026-07-10",
        "reference": f"samchat://gastos/{source_id}",
        "metadata": {
            "amount": 2500,
            "currency": "MXN",
            "secret_bank_account": "must-not-pass",
        },
    }


def test_money_preserves_decimal_cent_precision():
    amount = Decimal("9007199254740992.01")
    assert _money(
        amount,
        "MXN",
    ) == "9,007,199,254,740,992.01 MXN"
    assert _safe_scalar(amount) == "9007199254740992.01"


@pytest.mark.asyncio
async def test_flag_off_is_exact_noop(monkeypatch):
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        raising=False,
    )
    calls = []

    async def provider(_context, sources):
        calls.append(sources)
        return {"expenses": [_live_row()]}

    result = await acquire_live_analyst_evidence(
        context=_context("gastos:read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.enabled is False
    assert result.collection.evidence == []
    assert result.collection.provider_called is False
    assert calls == []


def test_default_sources_include_expense_accounts(monkeypatch):
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        raising=False,
    )

    assert "expense_accounts" in configured_live_evidence_sources()


def test_specific_source_phrases_suppress_broader_sources():
    assert _requested_live_evidence_sources(
        "Explícame esta cuenta de gastos"
    ) == {"expense_accounts"}
    assert _requested_live_evidence_sources(
        "Explícame esta solicitud de pago"
    ) == {"documents"}


@pytest.mark.asyncio
async def test_only_authorized_sources_are_queried(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "budgets,vendors",
    )
    calls = []

    async def provider(_context, sources):
        calls.append(sources)
        return {"budgets": [_live_row()]}

    result = await acquire_live_analyst_evidence(
        context=_context("budgets.read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert calls == [{"budgets"}]
    assert result.allowed_sources == ["budgets"]
    assert result.denied_sources == ["vendors"]
    assert result.collection.provider_called is True
    assert result.collection.evidence[0].permissions_applied == ["presupuestos:read"]
    assert result.collection.evidence[0].metadata == {
        "amount": 2500,
        "currency": "MXN",
    }
    assert any(
        "no están autorizadas" in caveat
        for caveat in result.collection.caveats
    )


@pytest.mark.asyncio
async def test_provider_is_not_called_when_all_sources_are_denied(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "budgets,vendors",
    )

    async def provider(_context, _sources):  # pragma: no cover
        raise AssertionError("unauthorized provider call")

    result = await acquire_live_analyst_evidence(
        context=_context(),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.allowed_sources == []
    assert result.denied_sources == ["budgets", "vendors"]
    assert result.collection.provider_called is False
    assert any(
        "permisos" in caveat for caveat in result.collection.caveats
    )


@pytest.mark.asyncio
async def test_ambiguous_follow_up_does_not_query_unrelated_live_sources(
    monkeypatch,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,cfdi_documents,budgets,registered_payments,documents",
    )

    async def provider(_context, _sources):  # pragma: no cover
        raise AssertionError("ambiguous follow-up must use conversation history")

    result = await acquire_live_analyst_evidence(
        context=_context(
            "gastos:read",
            "cfdi:read",
            "presupuestos:read",
            "pagos:read",
            "documentos:read",
            question="¿Qué implica?",
        ),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.attempted_sources == []
    assert result.allowed_sources == []
    assert result.denied_sources == []
    assert result.collection.provider_called is False
    assert result.collection.evidence == []


@pytest.mark.asyncio
async def test_effective_profile_tokens_map_to_canonical_sources(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "registered_payments,budgets",
    )
    calls = []

    async def provider(_context, sources):
        calls.append(sources)
        source = next(iter(sources))
        return {source: [_live_row(source)]}

    result = await acquire_live_analyst_evidence(
        context=_context("finance.payments.*", "budgets.read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert calls == [{"registered_payments"}, {"budgets"}]
    assert result.allowed_sources == ["registered_payments", "budgets"]
    assert result.denied_sources == []


@pytest.mark.asyncio
async def test_middle_wildcard_profile_token_is_supported(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "projects",
    )

    async def provider(_context, sources):
        return {"projects": [_live_row(next(iter(sources)))]}

    result = await acquire_live_analyst_evidence(
        context=_context("operations.*.read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.allowed_sources == ["projects"]
    assert len(result.collection.evidence) == 1


@pytest.mark.asyncio
async def test_employee_can_read_only_ownership_scoped_sources(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,documents,budgets",
    )
    calls = []

    async def provider(_context, sources):
        calls.append(sources)
        source = next(iter(sources))
        return {source: [_live_row(source)]}

    result = await acquire_live_analyst_evidence(
        context=_context(),
        intent=_intent(),
        rows_provider=provider,
    )

    assert calls == [{"expenses"}, {"documents"}]
    assert result.allowed_sources == ["expenses", "documents"]
    assert result.denied_sources == ["budgets"]


@pytest.mark.asyncio
async def test_checklist_permissions_do_not_authorize_financial_documents(
    monkeypatch,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "documents",
    )

    async def provider(_context, _sources):  # pragma: no cover
        raise AssertionError("financial documents must remain unauthorized")

    result = await acquire_live_analyst_evidence(
        context=_context(
            "documents.checklist.read",
            "documents.players.read",
            role="coordinador",
        ),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.allowed_sources == []
    assert result.denied_sources == ["documents"]
    assert result.collection.provider_called is False


@pytest.mark.asyncio
async def test_missing_provider_fails_closed_with_diagnostic(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses",
    )

    result = await acquire_live_analyst_evidence(
        context=_context("gastos:read"),
        intent=_intent(),
        rows_provider=None,
    )

    assert result.failed_sources == ["expenses"]
    assert result.source_counts == {"expenses": 0}
    assert result.collection.provider_called is False
    assert result.collection.evidence == []
    assert "no está disponible" in result.collection.caveats[0]


@pytest.mark.asyncio
async def test_one_source_failure_keeps_other_evidence(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,documents",
    )

    async def provider(_context, sources):
        source = next(iter(sources))
        if source == "documents":
            raise RuntimeError("database view unavailable")
        return {"expenses": [_live_row()]}

    result = await acquire_live_analyst_evidence(
        context=_context("gastos:read", "documentos:read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.failed_sources == ["documents"]
    assert result.source_counts == {"expenses": 1, "documents": 0}
    assert len(result.collection.evidence) == 1
    assert any("parcial" in caveat for caveat in result.collection.caveats)


@pytest.mark.asyncio
async def test_successful_empty_source_keeps_its_caveat(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,budgets",
    )

    async def provider(_context, sources):
        source = next(iter(sources))
        if source == "budgets":
            return {"budgets": []}
        return {"expenses": [_live_row()]}

    result = await acquire_live_analyst_evidence(
        context=_context(
            "gastos:read",
            "presupuestos:read",
            question="Compara gastos y presupuestos",
        ),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.source_counts == {"expenses": 1, "budgets": 0}
    assert any(
        "No hay presupuestos disponibles" in caveat
        for caveat in result.collection.caveats
    )


@pytest.mark.asyncio
async def test_authorized_sources_are_read_concurrently(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,documents",
    )
    active = 0
    max_active = 0

    async def provider(_context, sources):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        source = next(iter(sources))
        return {source: [_live_row(source)]}

    result = await acquire_live_analyst_evidence(
        context=_context("gastos:read", "documentos:read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert max_active == 2
    assert result.source_counts == {"expenses": 1, "documents": 1}


@pytest.mark.asyncio
async def test_source_timeout_is_safe_and_compact(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_TIMEOUT_MS",
        "50",
    )

    async def provider(_context, _sources):
        await asyncio.sleep(0.2)
        return {"expenses": [_live_row()]}

    result = await acquire_live_analyst_evidence(
        context=_context("gastos:read"),
        intent=_intent(),
        rows_provider=provider,
    )

    assert result.timed_out_sources == ["expenses"]
    assert result.collection.evidence == []
    assert result.trace()["source_counts"] == {"expenses": 0}
    assert "row-1" not in str(result.trace())


class _FakeMappings:
    def all(self):
        return [
            {
                "id": "expense-1",
                "concept": "Hospedaje",
                "project": "Nacional",
                "amount": 1200,
                "currency": "MXN",
                "date": date(2026, 7, 10),
                "status": "activo",
                "reference_number": "G-1",
            }
        ]


class _FakeResult:
    def mappings(self):
        return _FakeMappings()


class _FakeAsyncSession:
    def __init__(self):
        self.statements = []
        self.no_autoflush = nullcontext()

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult()


class _FakeSessionContext(_FakeAsyncSession):
    def __init__(self):
        super().__init__()
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.exited = True


@pytest.mark.asyncio
async def test_sqlalchemy_provider_uses_select_and_owner_scope():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    result = await provider(_context("gastos:read"), {"expenses"})

    sql = str(session.statements[0])
    assert sql.lstrip().startswith("SELECT")
    assert "expense_reports.empleado_id =" in sql
    assert "expense_reports.estado_gasto !=" in sql
    assert "UPDATE " not in sql
    assert "DELETE " not in sql
    params = session.statements[0].compile().params.values()
    assert "emp-1" in params
    assert "cancelado" in params
    assert result["expenses"][0]["metadata"]["amount"] == 1200


@pytest.mark.asyncio
async def test_requested_expense_account_is_filtered_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    result = await provider(
        _context(
            "cuentas_de_gastos:read",
            question="Explica la cuenta de gastos CTA-OLD",
        ),
        {"expense_accounts"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    assert "requested_match" in sql
    assert sql.index("case when") < sql.index(
        "cuentas_de_gastos.created_at desc"
    )
    assert sql.count("lower(cast(cuentas_de_gastos.referencia_base") >= 3
    assert result["expense_accounts"] == []


@pytest.mark.asyncio
async def test_admin_operations_is_scoped_to_own_department():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "gastos:read",
            role="admin",
            department="Operaciones",
        ),
        {"expenses"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    params = statement.compile().params.values()
    assert "join empleados" in sql
    assert "lower(trim(empleados.departamento))" in sql
    assert "Operaciones".casefold() in params
    assert "expense_reports.empleado_id =" not in sql.split("join empleados")[0]


@pytest.mark.asyncio
async def test_missing_employee_identity_fails_closed_before_query():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)
    context = LiveEvidenceContext(
        employee_id=None,
        role="empleado",
        permissions={"gastos:read"},
        question="Explica mis gastos",
    )

    with pytest.raises(PermissionError):
        await provider(context, {"expenses"})

    assert session.statements == []


@pytest.mark.asyncio
async def test_requested_expense_reference_is_prioritized_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)
    context = LiveEvidenceContext(
        employee_id="emp-1",
        role="empleado",
        permissions={"gastos:read"},
        question="Explica el gasto G-123",
        limit_per_source=2,
    )

    result = await provider(context, {"expenses"})

    sql = str(session.statements[0]).lower()
    assert "lower(expense_reports.numero_referencia)" in sql
    assert "case when" in sql
    assert "requested_match" in sql
    assert sql.index("case when") < sql.index("expense_reports.fecha desc")
    assert sql.count("lower(expense_reports.numero_referencia)") >= 3
    assert result["expenses"] == []


@pytest.mark.asyncio
async def test_requested_expense_year_filters_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "gastos:read",
            question="Explícame los gastos de 2025",
        ),
        {"expenses"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    assert "extract(year from expense_reports.fecha)" in sql
    assert 2025 in statement.compile().params.values()


def test_requested_identifier_match_survives_cross_source_ranking():
    intent = _intent()
    evidence = [
        AnalystEvidence(
            source_type="cfdi_document",
            label=f"CFDI {index}",
            summary="CFDI reciente con evidencia suficiente.",
        )
        for index in range(6)
    ]
    evidence.append(
        AnalystEvidence(
            source_type="expense",
            label="Gasto G-123",
            summary="Gasto solicitado explícitamente por referencia.",
            metadata={"requested_match": True},
        )
    )

    ranked = rank_analyst_evidence(intent, evidence)

    assert ranked[0].label == "Gasto G-123"
    assert "requested_identifier_match" in ranked[0].rank_reasons


@pytest.mark.asyncio
async def test_historical_financial_evidence_does_not_claim_live_acquisition():
    result = await run_analyst_workbench(
        intent=_intent(),
        evidence=[
            AnalystEvidence(
                source_type="expense",
                label="Gasto histórico",
                summary="Gasto conservado desde el contexto de conversación.",
            )
        ],
    )

    assert any("No revisé datos vivos" in caveat for caveat in result.caveats)
    assert "evidencia en vivo autorizada" not in result.answer


def test_requested_source_survives_cross_source_pack_ranking():
    intent = replace(_intent(), raw_text="Explica el presupuesto 2026")
    evidence = [
        AnalystEvidence(
            source_type="cfdi_document",
            label=f"CFDI {index}",
            summary="CFDI reciente con evidencia suficiente.",
        )
        for index in range(6)
    ]
    evidence.append(
        AnalystEvidence(
            source_type="budget",
            label="Presupuesto 2026",
            summary="Version presupuestal solicitada para la edicion 2026.",
            metadata={"requested_match": True},
        )
    )

    packed = build_analyst_evidence_pack(
        live_evidence=evidence,
        inline_evidence=[],
        history_evidence=[],
        intent=intent,
        limit=6,
    )

    assert packed[0].label == "Presupuesto 2026"
    assert "requested_identifier_match" in packed[0].rank_reasons
    assert any(item.source_type == "budget" for item in packed)


@pytest.mark.asyncio
async def test_cfdi_owner_scope_includes_direct_document_links():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(_context("cfdi:read"), {"cfdi_documents"})

    sql = str(session.statements[0]).lower()
    assert "from documentos" in sql
    assert "documentos.cfdi_report_id = cfdi_reports.id" in sql
    assert "documentos.empleado_id =" in sql
    assert "emp-1" in session.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_budget_provider_uses_existing_tables_without_schema_writes():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(_context("presupuestos:read"), {"budgets"})

    sql = str(session.statements[0]).upper()
    assert sql.lstrip().startswith("WITH SELECTED_VERSION AS")
    assert "FROM BUDGET_LINES" in sql
    assert "FROM BUDGET_VERSIONS" in sql
    assert "CREATE " not in sql
    assert "ALTER " not in sql
    assert "UPDATE " not in sql
    assert "DELETE " not in sql


@pytest.mark.asyncio
async def test_budget_provider_prefers_newest_edition_before_status():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "presupuestos:read",
            question="Explica el presupuesto",
        ),
        {"budgets"},
    )

    sql = str(session.statements[0]).upper()
    selected_version_order = sql.split(
        "ORDER BY",
        maxsplit=1,
    )[1].split("LIMIT", maxsplit=1)[0]
    assert selected_version_order.index("EDITION_YEAR DESC") < (
        selected_version_order.index("CASE STATUS")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modifier",
    ("actual", "anual", "disponible", "general", "global", "total", "vigente"),
)
async def test_budget_descriptive_modifier_is_not_a_project_filter(modifier):
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "presupuestos:read",
            question=f"Explica el presupuesto {modifier}",
        ),
        {"budgets"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    assert "budget_project_0" not in statement.compile().params
    assert "where lower(coalesce(l.tournament_name" not in sql


@pytest.mark.asyncio
async def test_budget_provider_filters_requested_edition_year():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "presupuestos:read",
            question="Explica el presupuesto 2026 de Nacional",
        ),
        {"budgets"},
    )

    statement = session.statements[0]
    sql = str(statement).upper()
    assert "AND EDITION_YEAR = :EDITION_YEAR" in sql
    assert statement.compile().params["edition_year"] == 2026
    assert "AS REQUESTED_MATCH" in sql
    assert sql.index("CASE WHEN") < sql.index("L.BUDGET_AMOUNT DESC")
    assert sql.count("LOWER(COALESCE(L.TOURNAMENT_NAME") >= 3
    assert "%nacional%" in statement.compile().params.values()


@pytest.mark.asyncio
async def test_project_reader_applies_form_department_visibility():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(
        _context(
            "proyectos:read",
            role="admin",
            department="Operaciones",
            question="Explica el proyecto Nacional",
        ),
        {"projects"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    params = statement.compile().params.values()
    assert "tournaments.form_visibility_areas" in sql
    assert any(value == ["Operaciones"] for value in params)


@pytest.mark.asyncio
async def test_registered_payment_prefers_final_amount():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    await provider(_context("pagos:read"), {"registered_payments"})

    statement = session.statements[0]
    sql = str(statement).lower()
    assert sql.index("documentos.monto_total") < sql.index(
        "documentos.monto_solicitado"
    )
    params = list(statement.compile().params.values())
    assert "SOLICITUD" in params
    assert any(
        set(value) == {"pagado", "cerrado"}
        for value in params
        if isinstance(value, (list, tuple))
    )
    assert "aprobado" in params
    assert "documentos.pagado_en is not null" in sql


@pytest.mark.asyncio
async def test_requested_registered_payment_is_prioritized_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    result = await provider(
        _context(
            "pagos:read",
            question="Explica el pago REF-OLD",
        ),
        {"registered_payments"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    assert "requested_match" in sql
    assert sql.index("case when") < sql.index(
        "documentos.pagado_en desc"
    )
    params = {
        str(value).lower()
        for value in statement.compile().params.values()
    }
    assert any("ref-old" in value for value in params)
    assert sql.count("lower(documentos.numero_referencia)") >= 3
    assert result["registered_payments"] == []


@pytest.mark.asyncio
async def test_vendor_entity_is_prioritized_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    result = await provider(
        _context(
            "proveedores:read",
            role="superadmin",
            question="Explica el proveedor Acme con detalle",
        ),
        {"vendors"},
    )

    sql = str(session.statements[0]).lower()
    assert "lower(cast(proveedores_clientes.nombre" in sql
    assert "requested_match" in sql
    assert sql.index("case when") < sql.index(
        "proveedores_clientes.nombre asc"
    )
    params = {
        str(value).lower()
        for value in session.statements[0].compile().params.values()
    }
    assert any("acme" in value for value in params)
    assert not any("detalle" in value or value == "con" for value in params)
    assert sql.count("lower(cast(proveedores_clientes.nombre") >= 3
    assert result["vendors"] == []


@pytest.mark.asyncio
async def test_requested_document_is_filtered_before_limit():
    session = _FakeAsyncSession()
    provider = build_sqlalchemy_live_evidence_rows_provider(session)

    result = await provider(
        _context(
            "documentos:read",
            question="Explica el documento DOC-OLD",
        ),
        {"documents"},
    )

    statement = session.statements[0]
    sql = str(statement).lower()
    assert "requested_match" in sql
    assert sql.index("case when") < sql.index("documentos.creado_en desc")
    assert sql.count("lower(cast(documentos.numero_referencia") >= 3
    assert result["documents"] == []


@pytest.mark.asyncio
async def test_isolated_provider_owns_and_closes_its_read_session():
    sessions = []

    def session_maker():
        session = _FakeSessionContext()
        sessions.append(session)
        return session

    provider = build_isolated_sqlalchemy_live_evidence_rows_provider(session_maker)
    result = await provider(_context("gastos:read"), {"expenses"})

    assert len(sessions) == 1
    assert sessions[0].entered is True
    assert sessions[0].exited is True
    assert result["expenses"][0]["id"] == "expense-1"
