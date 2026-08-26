from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.routes import user_routes
from devnous.gastos.services.documento_semantics import (
    EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX,
)


def _doc(**overrides):
    values = {
        "id": uuid4(),
        "tipo": "SOLICITUD",
        "numero_referencia": "S-26000123",
        "estado": "aprobado",
        "empleado": SimpleNamespace(nombre="Odilon Reportero"),
        "beneficiario_empleado": None,
        "beneficiario_empleado_id": None,
        "proveedor_cliente": None,
        "proveedor_cliente_id": None,
        "concepto_pago": "Hospedaje regional",
        "referencia_pago": "RP-001",
        "referencia_operaciones": "456",
        "monto_solicitado": Decimal("1000.00"),
        "monto_total": Decimal("1160.00"),
        "currency": "MXN",
        "creado_en": datetime(2026, 7, 31, 12, 0, 0),
        "enviado_en": None,
        "aprobado_en": None,
        "pagado_en": None,
        "cuenta_gastos_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_documentos_todos_reporting_values_for_provider_solicitud():
    documento = _doc(
        proveedor_cliente_id=uuid4(),
        proveedor_cliente=SimpleNamespace(nombre="Federal Express"),
    )

    row = user_routes._documentos_todos_reporting_row_values(
        documento, aprobador_nombre="Finanzas"
    )

    assert row["tipo_documento"] == "SOLICITUD"
    assert row["tipo_solicitud"] == "Terceros"
    assert row["solicitante"] == "Odilon Reportero"
    assert row["beneficiario"] == "Federal Express"
    assert row["proveedor"] == "Federal Express"
    assert row["concepto"] == "Hospedaje regional"
    assert row["referencia_pago"] == "RP-001"
    assert row["referencia_operaciones"] == "456"
    assert row["monto_solicitado"] == "$1,000.00"
    assert row["monto_total"] == "$1,160.00"
    assert row["currency"] == "MXN"
    assert row["situacion"] == "Abierta"
    assert row["aprobador"] == "Finanzas"


def test_documentos_todos_reporting_values_for_employee_beneficiary():
    documento = _doc(
        beneficiario_empleado_id=uuid4(),
        beneficiario_empleado=SimpleNamespace(nombre="Alicia Beneficiaria"),
        proveedor_cliente_id=uuid4(),
        proveedor_cliente=SimpleNamespace(nombre="Cuenta bancaria Alicia"),
    )

    row = user_routes._documentos_todos_reporting_row_values(documento)

    assert row["tipo_solicitud"] == "Personal / empleado"
    assert row["beneficiario"] == "Alicia Beneficiaria"
    assert row["proveedor"] == "Cuenta bancaria Alicia"


def test_documentos_todos_reporting_values_for_employee_reimbursement():
    documento = _doc(
        cuenta_gastos_id=uuid4(),
        beneficiario_empleado_id=uuid4(),
        beneficiario_empleado=SimpleNamespace(nombre="Alicia Beneficiaria"),
        concepto_pago=f"{EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX} I-26000001",
    )

    assert user_routes._documentos_todos_reporting_type(documento) == (
        "Reembolso empleado"
    )


def test_documentos_todos_reporting_values_for_informe():
    documento = _doc(tipo="INFORME", numero_referencia="I-26000001")

    row = user_routes._documentos_todos_reporting_row_values(documento)

    assert row["tipo_solicitud"] == "Informe de gastos"


def test_documentos_todos_reporting_situation_marks_closed_states():
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="pagado"))
        == "Cerrada"
    )
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="rechazado"))
        == "Cerrada"
    )
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="enviado"))
        == "Abierta"
    )


def test_documentos_todos_filter_form_preserves_q_field():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()

    assert 'name="q"' in text
    assert 'value="{escape(q_value)}"' in text
    assert 'name="situacion"' in text
    assert "Abiertas" in text
    assert "Cerradas" in text
    assert "Referencia, proveedor, beneficiario, concepto" in text


def test_document_detail_expenses_table_exposes_edit_actions():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()
    start = text.index("# Build expenses table rows")
    end = text.index("# Build aprobaciones rows", start)
    expenses_table_flow = text[start:end]

    assert "is_finance_admin_for_expense_actions" in expenses_table_flow
    assert "can_edit_expense_from_document" in expenses_table_flow
    assert 'href="/gastos/{expense.id}/editar"' in expenses_table_flow
    assert "<th>Acciones</th>" in text
    assert 'colspan="9"' in text



def test_budget_control_is_named_operator_only_not_role_or_department():
    allowed = SimpleNamespace(
        id="e3d13040-2360-420f-98a1-516440ef63c3",
        nombre="Juan Pablo López Romero",
        correo="jlopez@plataformasports.com",
        rol="empleado",
        departamento="operaciones",
    )
    luis = SimpleNamespace(
        id=uuid4(),
        nombre="Luis Angel Orozco",
        correo="luis@example.com",
        rol="empleado",
        departamento="direccion",
    )
    denied_finance_role = SimpleNamespace(
        id=uuid4(),
        nombre="Odilon Trujillo Macedo",
        correo="odilon@example.com",
        rol="finanzas",
        departamento="contabilidad",
    )

    assert user_routes._is_budget_control_user(allowed) is True
    assert user_routes._is_budget_control_user(luis) is True
    assert user_routes._is_budget_control_user(denied_finance_role) is False


def test_control_presupuestal_selects_are_not_globally_required():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()
    start = text.index("async def documentos_control_presupuestal")
    end = text.index("async def _apply_control_presupuestal_assignment", start)
    control_view = text[start:end]

    assert "required=False" in control_view
    assert "Todos los documentos" in text[text.index("operaciones_section"):text.index("aprobaciones_section")]
    assert "Descripción" in control_view
