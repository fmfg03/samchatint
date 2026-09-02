from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.employee_debtor_accounting_service import (
    DEBTOR_ACCOUNT_PREFIXES,
    SANTANDER_BANK_ACCOUNT_CODE,
    _create_poliza,
    _event_poliza_number,
    _naive_utc_datetime,
    _preview_expense_lines,
)


ROOT = Path(__file__).resolve().parents[3]


class FakeAccountingSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def _account(code: str):
    return SimpleNamespace(id=uuid4(), codigo=code, nombre=code, activo=True)


def _preview_account(account):
    return {
        "codigo": account.codigo,
        "cuenta_contable_id": str(account.id),
    }


def test_laminar_event_identity_binds_event_and_complete_uuid():
    entity_id = uuid4()
    assert _event_poliza_number("PROV-APR", entity_id) == (
        f"LAM-PROV-APR-{entity_id}"
    )
    assert _event_poliza_number("PROV-PAY", entity_id) != _event_poliza_number(
        "PROV-APR", entity_id
    )


def test_poliza_date_normalization_accepts_aware_datetimes():
    aware = datetime(2026, 8, 26, 22, 33, 3, tzinfo=timezone.utc)

    normalized = _naive_utc_datetime(aware)

    assert normalized == datetime(2026, 8, 26, 22, 33, 3)
    assert normalized.tzinfo is None


async def test_create_poliza_keeps_lines_attached_in_memory():
    session = FakeAccountingSession()

    poliza = await _create_poliza(
        session,
        origen="proveedor_aprobacion",
        numero_poliza="LAM-PROV-APR-test",
        fecha=datetime(2026, 8, 26, 22, 33, 3, tzinfo=timezone.utc),
        beneficiario_nombre="Proveedor",
        concepto="Proveedor aprobado",
        lines=[
            {
                "cuenta_codigo": "5300-001-001",
                "cuenta_contable_id": uuid4(),
                "concepto": "Proveedor aprobado",
                "debe": Decimal("100.00"),
                "haber": 0,
                "raw_row_json": {"movement": "debe_gasto"},
            },
            {
                "cuenta_codigo": "2120-001-001",
                "cuenta_contable_id": uuid4(),
                "concepto": "Proveedor aprobado",
                "debe": 0,
                "haber": Decimal("100.00"),
                "raw_row_json": {"movement": "haber_pasivo"},
            },
        ],
    )

    assert poliza.fecha_poliza.tzinfo is None
    assert len(poliza.lines) == 2
    assert all(line.poliza is poliza for line in poliza.lines)


def test_laminar_accounts_are_exact_and_reject_1700_aliases():
    assert SANTANDER_BANK_ACCOUNT_CODE == "1120-001-001"
    assert DEBTOR_ACCOUNT_PREFIXES == ("1170-001-", "1170-002-")
    assert all(not prefix.startswith("1700-") for prefix in DEBTOR_ACCOUNT_PREFIXES)


def test_full_fiscal_preview_translates_to_balanced_bound_lines():
    expense_account = _account("5300-001-001")
    iva_account = _account("1200-001-001")
    local_account = _account("5300-010-002")
    nd_account = _account("5500-001-001")
    retention_account = _account("2130-001-001")
    counterpart = _account("2120-001-001")
    expense = SimpleNamespace(
        concepto="Hospedaje",
        cuenta_contable=expense_account,
    )
    preview = {
        "taxes": {
            "base_gasto": Decimal("100.00"),
            "iva_trasladado": Decimal("16.00"),
            "iva_account": _preview_account(iva_account),
            "impuestos_locales": [
                {
                    "label": "ISH",
                    "importe": Decimal("4.00"),
                    "account": _preview_account(local_account),
                }
            ],
            "gastos_no_deducibles": [
                {
                    "label": "Propina",
                    "importe": Decimal("5.00"),
                    "account": _preview_account(nd_account),
                }
            ],
            "retenciones": [
                {
                    "label": "ISR",
                    "importe": Decimal("10.00"),
                    "account": _preview_account(retention_account),
                }
            ],
            "neto_contrapartida": Decimal("115.00"),
        }
    }

    lines, error = _preview_expense_lines(
        preview=preview,
        expense=expense,
        counterpart=counterpart,
        meta={"source": "test"},
    )

    assert error is None
    assert {line["cuenta_codigo"] for line in lines} == {
        expense_account.codigo,
        iva_account.codigo,
        local_account.codigo,
        nd_account.codigo,
        retention_account.codigo,
        counterpart.codigo,
    }
    assert sum(Decimal(str(line["debe"])) for line in lines) == Decimal("125.00")
    assert sum(Decimal(str(line["haber"])) for line in lines) == Decimal("125.00")


def test_full_fiscal_preview_fails_closed_without_exact_tax_binding():
    expense = SimpleNamespace(
        concepto="Servicio",
        cuenta_contable=_account("5300-001-001"),
    )
    lines, error = _preview_expense_lines(
        preview={
            "taxes": {
                "base_gasto": Decimal("100.00"),
                "iva_trasladado": Decimal("16.00"),
                "iva_account": None,
                "neto_contrapartida": Decimal("116.00"),
            }
        },
        expense=expense,
        counterpart=_account("2120-001-001"),
        meta={},
    )
    assert lines == []
    assert error == "missing_iva_account"


def test_real_workflow_and_payment_services_wire_laminar_hooks():
    workflow = (ROOT / "src/devnous/gastos/services/documento_workflow_service.py").read_text(
        encoding="utf-8"
    )
    payment = (ROOT / "src/devnous/gastos/services/documento_payment_service.py").read_text(
        encoding="utf-8"
    )
    assert "ensure_provider_approval_posting" in workflow
    assert "ensure_debtor_comprobacion_posting_for_informe" in workflow
    assert "ensure_amex_report_approval_posting" in workflow
    assert 'posting.status == "pending"' in workflow
    assert "ensure_provider_payment_posting" in payment
    assert "ensure_debtor_payment_posting_for_document" in payment
    assert "ensure_amex_payment_posting" in payment
    assert payment.index("parse_amex_payment_card_id") < payment.index("has_proveedor")
    assert 'posting.status == "pending"' in payment


def test_amex_reconciliation_validation_wires_accounting_before_notification():
    routes = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")
    start = routes.index("async def amex_conciliacion_validate_notify")
    end = routes.index('@router.get("/admin/gastos/amex/conciliacion"', start)
    body = routes[start:end]

    assert "ensure_amex_reconciliation_posting" in body
    assert "notify_amex_reconciliation_validated" in body
    assert body.index("ensure_amex_reconciliation_posting") < body.index("notify_amex_reconciliation_validated")
    assert 'posting.status == "pending"' in body


def test_cuentas_por_cobrar_has_accounting_breadcrumb_context():
    routes = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")
    start = routes.index("async def contabilidad_cuentas_por_cobrar_view")
    end = routes.index(
        '@router.post("/admin/contabilidad/cuentas-por-cobrar/cfdi-ingresos/assign")',
        start,
    )
    body = routes[start:end]

    assert 'render_top_navigation(current_empleado, "contabilidad")' in body
    assert '_contabilidad_subnav("cxc")' in body
    assert "_gastos_breadcrumb_html([" in body
    assert '("Contabilidad", "/admin/contabilidad/estado")' in body
    assert '("Cuentas por Cobrar", None)' in body
