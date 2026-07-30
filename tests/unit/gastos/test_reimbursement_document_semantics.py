from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from devnous.gastos.models import Documento
from devnous.gastos.services import documento_telegram as tg
from devnous.gastos.services.documento_semantics import (
    effective_account_beneficiary_id,
    is_employee_reimbursement,
)


def test_effective_account_beneficiary_prefers_selected_employee():
    requester_id = uuid4()
    beneficiary_id = uuid4()
    cuenta = SimpleNamespace(
        empleado_id=requester_id,
        beneficiario_empleado_id=beneficiary_id,
    )

    assert effective_account_beneficiary_id(cuenta) == beneficiary_id


def test_effective_account_beneficiary_falls_back_to_requester():
    requester_id = uuid4()
    cuenta = SimpleNamespace(
        empleado_id=requester_id,
        beneficiario_empleado_id=None,
    )

    assert effective_account_beneficiary_id(cuenta) == requester_id


def test_legacy_system_reimbursement_is_not_a_third_party_request():
    documento = SimpleNamespace(
        tipo="SOLICITUD",
        cuenta_gastos_id=uuid4(),
        beneficiario_empleado_id=uuid4(),
        proveedor_cliente_id=uuid4(),
        concepto_pago="Reembolso de saldo a favor — I-793655",
    )

    assert is_employee_reimbursement(documento) is True


def test_regular_supplier_request_is_not_an_employee_reimbursement():
    documento = SimpleNamespace(
        tipo="SOLICITUD",
        cuenta_gastos_id=None,
        beneficiario_empleado_id=None,
        proveedor_cliente_id=uuid4(),
        concepto_pago="Compra de uniformes",
    )

    assert is_employee_reimbursement(documento) is False


def test_reimbursement_telegram_omits_provider_and_separates_requester():
    provider = MagicMock()
    provider.nombre = "Cuenta adaptadora de Alicia"
    beneficiary = MagicMock()
    beneficiary.nombre = "Bibiana Roman"

    documento = MagicMock(spec=Documento)
    documento.numero_referencia = "S-26000052"
    documento.tipo = "SOLICITUD"
    documento.estado = "enviado"
    documento.cuenta_gastos_id = UUID("d324fc55-ca8f-44a9-bb44-8382eb6f8ff5")
    documento.beneficiario_empleado_id = UUID(
        "435825e1-0bd0-45c1-a7cc-c97cd18a2b15"
    )
    documento.beneficiario_empleado = beneficiary
    documento.proveedor_cliente = provider
    documento.concepto_pago = "Reembolso de saldo a favor — I-793655"
    documento.notas = None
    documento.referencia_operaciones = "9"
    documento.enviado_en = None
    documento.aprobado_en = None

    text = tg.format_documento_resumen_es(
        documento,
        context={
            "solicitante": "Alicia Zuñiga",
            "proyecto": "Operaciones",
            "etapa": "Artículos varios",
            "monto_line": "$628.00 MXN",
            "referencia_operaciones": "9",
        },
    )

    assert text.startswith("*Tipo de pago* *Reembolso a empleado*")
    assert "*Beneficiario del reembolso* *Bibiana Roman*" in text
    assert "*Solicitante* Alicia Zuñiga" in text
    assert "Proveedor" not in text
    assert "Cuenta adaptadora de Alicia" not in text


def test_telegram_solicitud_includes_project_phase_and_keeps_approval_keyboard() -> None:
    documento_id = uuid4()
    provider = MagicMock()
    provider.nombre = "HK DISENO SA DE CV"
    empleado = MagicMock()
    empleado.nombre = "Alicia Zuniga"

    documento = MagicMock(spec=Documento)
    documento.id = documento_id
    documento.numero_referencia = "S-26000051"
    documento.tipo = "SOLICITUD"
    documento.estado = "enviado"
    documento.empleado = empleado
    documento.proveedor_cliente = provider
    documento.beneficiario_empleado = None
    documento.beneficiario_empleado_id = None
    documento.cuenta_gastos_id = None
    documento.concepto_pago = "Estampado de playeras"
    documento.notas = "Fase Nacional"
    documento.referencia_operaciones = "10"
    documento.enviado_en = None
    documento.aprobado_en = None

    text = tg.format_documento_resumen_es(
        documento,
        context={
            "solicitante": "Alicia Zuniga",
            "proyecto": "Copa Telmex 2026",
            "etapa": "Fase Nacional",
            "monto_line": "$1,397.22 MXN",
            "referencia_operaciones": "10",
        },
        include_actions_hint=True,
    )
    keyboard = tg.approval_inline_keyboard(documento_id)

    assert "*Proyecto* Copa Telmex 2026" in text
    assert "*Etapa / subproyecto* Fase Nacional" in text
    assert "Usa los botones de abajo" in text
    assert keyboard["inline_keyboard"][0][0]["text"].endswith("Aprobar")
    assert keyboard["inline_keyboard"][0][1]["text"].endswith("Rechazar")
    assert keyboard["inline_keyboard"][0][0]["callback_data"] == f"{tg.CB_APPROVE}{documento_id}"
    assert keyboard["inline_keyboard"][0][1]["callback_data"] == f"{tg.CB_REJECT}{documento_id}"


def test_telegram_informe_includes_project_phase_from_context() -> None:
    beneficiary = MagicMock()
    beneficiary.nombre = "Bibiana Roman"
    empleado = MagicMock()
    empleado.nombre = "Bibiana Roman"

    documento = MagicMock(spec=Documento)
    documento.numero_referencia = "I-793655"
    documento.tipo = "INFORME"
    documento.estado = "abierta"
    documento.empleado = empleado
    documento.proveedor_cliente = None
    documento.beneficiario_empleado = beneficiary
    documento.beneficiario_empleado_id = uuid4()
    documento.cuenta_gastos_id = uuid4()
    documento.concepto_pago = None
    documento.notas = "Compra de articulos de papeleria"
    documento.enviado_en = None
    documento.aprobado_en = None

    text = tg.format_documento_resumen_es(
        documento,
        context={
            "solicitante": "Bibiana Roman",
            "proyecto": "Gastos Administrativos - Operaciones",
            "etapa": "Articulos varios",
            "monto_solicitado": "$0.00 MXN",
            "monto_gastado": "$500.00 MXN",
            "saldo_line": "$500.00 MXN - A favor del empleado - Reembolso pendiente",
        },
    )

    assert "*Proyecto* Gastos Administrativos - Operaciones" in text
    assert "*Etapa / subproyecto* Articulos varios" in text
    assert "*Monto gastado* $500.00 MXN" in text
