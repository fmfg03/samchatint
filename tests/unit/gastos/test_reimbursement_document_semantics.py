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
