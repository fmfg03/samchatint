from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_service
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


def test_documentos_todos_is_available_to_active_authenticated_users() -> None:
    active_user = SimpleNamespace(id=uuid4(), activo=True, rol="empleado")
    inactive_user = SimpleNamespace(id=uuid4(), activo=False, rol="empleado")
    anonymousish_user = SimpleNamespace(id=None, activo=True, rol="empleado")

    assert user_routes._can_view_documentos_todos(active_user) is True
    assert user_routes._can_view_documentos_todos(inactive_user) is False
    assert user_routes._can_view_documentos_todos(anonymousish_user) is False


def test_pending_approval_summary_shows_accumulated_amount() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def documentos_pendientes")
    end = source.index('@router.post("/documentos/pendientes/accion-lote")', start)
    block = source[start:end]

    assert "pending_amount_by_currency" in block
    assert "Monto acumulado" in block
    assert "pending_amount_display" in block


def test_historial_and_bulk_pending_actions_use_approver_visibility_gate() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    bulk_start = source.index("async def documentos_pendientes_accion_lote")
    bulk_end = source.index('@router.get("/documentos/historial-aprobador"', bulk_start)
    history_start = bulk_end
    history_end = source.index("# Build query based on role", history_start)

    assert "await _can_review_pending_approvals" in source[bulk_start:bulk_end]
    assert "await _can_review_pending_approvals" in source[history_start:history_end]


def test_solicitud_terceros_creation_does_not_render_budget_concept_selector() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def _render_solicitud_terceros_form")
    end = source.index('@router.post("/documentos/nueva-solicitud-terceros")', start)
    block = source[start:end]

    assert "budget_concept_row_terceros = """ in block
    assert "CONCEPTO:" not in block
    assert "budget_concept_id_terceros" not in block


def test_solicitud_terceros_create_and_edit_force_budget_control_assignment() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    create_start = source.index("async def crear_nueva_solicitud_terceros")
    edit_start = source.index("async def editar_solicitud_terceros_post")
    create_block = source[create_start:edit_start]
    edit_end = source.index('@router.post("/documentos/{documento_id}/adjuntos")', edit_start)
    edit_block = source[edit_start:edit_end]

    assert "budget_concept_id = None" in create_block
    assert "budget_concept_id = None" in edit_block


class _LazyAprobadorBomb:
    @property
    def aprobador(self):  # pragma: no cover - only exercised on regression
        raise AssertionError("aprobador relation must be resolved in batch")


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAprobadorSession:
    def __init__(self, employee):
        self._employee = employee
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _FakeResult([])
        return _FakeResult([self._employee])


async def test_fetch_documento_aprobador_display_batch_does_not_lazy_load_subject_approver():
    empleado_id = uuid4()
    documento = _doc(
        empleado_id=empleado_id,
        empleado=_LazyAprobadorBomb(),
        beneficiario_empleado_id=None,
        beneficiario_empleado=None,
    )
    session = _FakeAprobadorSession(
        SimpleNamespace(
            id=empleado_id,
            aprobador=SimpleNamespace(nombre="Odilon Aprobador"),
        )
    )

    display = await documento_service.fetch_documento_aprobador_display_batch(
        session, [documento]
    )

    assert display[documento.id] == "Odilon Aprobador"
    assert session.calls == 2
