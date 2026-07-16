from datetime import date

from samchat.assistant.analyst_evidence_adapters import (
    DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS,
    AnalystEvidenceQuery,
    BudgetEvidenceAdapter,
    CfdiDocumentEvidenceAdapter,
    DocumentEvidenceAdapter,
    ExpenseAccountEvidenceAdapter,
    ExpenseEvidenceAdapter,
    ProjectEvidenceAdapter,
    RegisteredPaymentEvidenceAdapter,
    VendorEvidenceAdapter,
    collect_read_only_analyst_evidence,
)
from samchat.assistant.analyst_intent import detect_analyst_intent


def _query():
    return AnalystEvidenceQuery(
        intent=detect_analyst_intent("Analiza desviaciones de gastos y CFDI"),
        question="Analiza desviaciones de gastos y CFDI",
        user_id="emp-1",
        role="finanzas",
        permissions=[
            "gastos:read",
            "cuentas_de_gastos:read",
            "cfdi:read",
            "presupuestos:read",
            "proyectos:read",
            "pagos:read",
            "proveedores:read",
            "documentos:read",
        ],
        reference_date=date(2026, 7, 14),
    )


def _row(source_id, label, summary, row_date="2026-07-01"):
    return {
        "id": source_id,
        "label": label,
        "summary": summary,
        "date": row_date,
        "reference": f"samchat://evidence/{source_id}",
        "coverage_level": "medium",
        "relevance": "high",
        "metadata": {"safe": True},
    }


def test_all_default_adapters_are_read_only():
    for adapter in DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS:
        assert adapter.supports_writes is False
        result = adapter.fetch(_query(), session={})
        assert result.read_only is True
        assert result.writes_supported is False
        assert result.provider_called is False
        assert result.actions_executed == []


def test_adapter_does_not_claim_permission_that_was_not_granted():
    query = AnalystEvidenceQuery(
        intent=detect_analyst_intent("Analiza gastos"),
        question="Analiza gastos",
        user_id="emp-1",
        role="empleado",
        permissions=[],
        reference_date=date(2026, 7, 14),
    )
    result = ExpenseEvidenceAdapter().fetch(
        query,
        session={"expenses": [_row("gasto-0", "Gasto", "Resumen")]},
    )

    assert result.permissions_applied == []
    assert result.evidence[0].permissions_applied == []


def test_expense_adapter_returns_normalized_evidence():
    result = ExpenseEvidenceAdapter().fetch(
        _query(),
        session={
            "expenses": [
                _row(
                    "gasto-1",
                    "Gasto hospedaje",
                    "Gasto de hospedaje contra presupuesto del proyecto.",
                )
            ]
        },
    )

    evidence = result.evidence[0]
    assert evidence.source == "gastos"
    assert evidence.source_type == "expense"
    assert evidence.source_id == "gasto-1"
    assert evidence.reference == "samchat://evidence/gasto-1"
    assert "gastos:read" in evidence.permissions_applied


def test_cfdi_document_adapter_preserves_id_reference_and_date():
    result = CfdiDocumentEvidenceAdapter().fetch(
        _query(),
        session={
            "cfdi_documents": [
                _row(
                    "cfdi-1",
                    "CFDI A",
                    "CFDI relacionado con pago pendiente.",
                    "2026-06-30",
                )
            ]
        },
    )

    evidence = result.evidence[0]
    assert evidence.source_type == "cfdi_document"
    assert evidence.date == "2026-06-30"
    assert evidence.reference == "samchat://evidence/cfdi-1"
    assert "cfdi:read" in evidence.permissions_applied


def test_budget_adapter_marks_freshness_and_coverage():
    result = BudgetEvidenceAdapter().fetch(
        _query(),
        session={
            "budgets": [
                _row(
                    "budget-1",
                    "Presupuesto julio",
                    "Presupuesto autorizado para operación.",
                    "2026-05-01",
                )
            ]
        },
    )

    assert result.freshness == "recent"
    assert result.evidence[0].coverage_level == "medium"
    assert result.coverage_level in {"low", "medium", "high"}


def test_registered_payment_adapter_does_not_perform_side_effects():
    result = RegisteredPaymentEvidenceAdapter().fetch(
        _query(),
        session={
            "registered_payments": [
                _row(
                    "pay-1",
                    "Pago registrado",
                    "Pago registrado contra proveedor.",
                )
            ]
        },
    )

    assert result.evidence[0].source_type == "registered_payment"
    assert result.read_only is True
    assert result.actions_executed == []
    assert result.writes_supported is False


def test_vendor_project_account_and_document_adapters_return_evidence():
    adapters = [
        (VendorEvidenceAdapter(), "vendors", "vendor"),
        (ProjectEvidenceAdapter(), "projects", "project"),
        (
            ExpenseAccountEvidenceAdapter(),
            "expense_accounts",
            "expense_account",
        ),
        (DocumentEvidenceAdapter(), "documents", "document_evidence"),
    ]

    for adapter, key, source_type in adapters:
        result = adapter.fetch(
            _query(),
            session={
                key: [
                    _row(
                        f"{key}-1",
                        f"{key} label",
                        f"{key} summary with operational context.",
                    )
                ]
            },
        )
        assert result.evidence[0].source_type == source_type
        assert result.provider_called is False
        assert result.actions_executed == []


def test_empty_adapter_result_returns_low_or_no_coverage_with_caveat():
    result = ExpenseEvidenceAdapter().fetch(_query(), session={})

    assert result.evidence == []
    assert result.coverage_level == "none"
    assert result.caveats == [
        "No hay gastos disponibles para sostener el análisis."
    ]
    assert result.freshness == "unknown"


def test_collector_dedupes_and_ranks_deterministically():
    query = _query()
    session = {
        "expenses": [
            _row(
                "shared-1",
                "CFDI gasto",
                "CFDI con gasto y presupuesto asociado.",
            )
        ],
        "cfdi_documents": [
            _row(
                "shared-1",
                "CFDI gasto",
                "CFDI con gasto y presupuesto asociado.",
            )
        ],
    }

    first = collect_read_only_analyst_evidence(
        query,
        adapters=[ExpenseEvidenceAdapter(), CfdiDocumentEvidenceAdapter()],
        session=session,
    )
    second = collect_read_only_analyst_evidence(
        query,
        adapters=[ExpenseEvidenceAdapter(), CfdiDocumentEvidenceAdapter()],
        session=session,
    )

    assert [item.to_dict() for item in first.evidence] == [
        item.to_dict() for item in second.evidence
    ]
    assert first.read_only is True
    assert first.writes_supported is False
    assert first.provider_called is False
    assert first.actions_executed == []


def test_session_object_read_only_rows_is_supported():
    class FakeSession:
        def read_only_rows(self, adapter_id):
            return {
                "expenses": [
                    _row(
                        "gasto-2",
                        "Gasto transporte",
                        "Gasto de transporte con comprobante.",
                    )
                ]
            }.get(adapter_id, [])

    result = ExpenseEvidenceAdapter().fetch(_query(), session=FakeSession())

    assert result.evidence[0].source_id == "gasto-2"
    assert result.read_only is True
