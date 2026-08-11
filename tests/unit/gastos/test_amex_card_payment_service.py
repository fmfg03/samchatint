from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import Aprobacion, Documento
from devnous.gastos.services import amex_card_payment_service as svc


@pytest.mark.asyncio
async def test_create_amex_card_payment_request_creates_approved_solicitud(monkeypatch):
    actor_id = uuid4()
    card_id = uuid4()
    cuenta = SimpleNamespace(codigo="2120-002-062")
    card = SimpleNamespace(
        id=card_id,
        card_label="FGV AMEX",
        cardholder_key="FGV",
        cardholder_name="Federico González",
        last4="1105",
        liability_cuenta_contable=cuenta,
    )
    added = []
    session = SimpleNamespace(
        add=lambda item: added.append(item),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    async def fake_load_card(session_arg, card_account_id):
        assert session_arg is session
        assert card_account_id == card_id
        return card

    async def fake_generate_reference(session_arg, tipo, empleado_id):
        assert session_arg is session
        assert tipo == "SOLICITUD"
        assert empleado_id == actor_id
        return "S-26009999"

    monkeypatch.setattr(svc, "load_active_amex_card_account", fake_load_card)
    monkeypatch.setattr(
        svc, "generate_documento_reference_number", fake_generate_reference
    )

    result = await svc.create_amex_card_payment_request(
        session,
        actor=SimpleNamespace(id=actor_id, nombre="Benjamín", rol="finanzas"),
        request=svc.AmexCardPaymentRequest(
            card_account_id=card_id,
            amount=Decimal("1234.56"),
            fecha_pago=date(2026, 8, 14),
            urgent=True,
        ),
    )

    documento = next(item for item in added if isinstance(item, Documento))
    approval = next(item for item in added if isinstance(item, Aprobacion))
    assert result.documento is documento
    assert result.card_account is card
    assert documento.tipo == "SOLICITUD"
    assert documento.estado == "aprobado"
    assert documento.numero_referencia == "S-26009999"
    assert documento.fecha_pago == date(2026, 8, 14)
    assert documento.pago_urgente is True
    assert documento.metodo_pago == "AMEX"
    assert documento.monto_solicitado == 1234.56
    assert "Pago AMEX FGV AMEX" in documento.concepto_pago
    assert "2120-002-062" in documento.notas
    assert approval.tipo_entidad == "documento"
    assert approval.accion == "aprobar"
    assert approval.entidad_id == documento.id
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(documento)


@pytest.mark.asyncio
async def test_create_amex_card_payment_request_requires_finance_role(monkeypatch):
    session = SimpleNamespace(add=lambda item: None)

    async def fail_load_card(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("card lookup should not run")

    monkeypatch.setattr(svc, "load_active_amex_card_account", fail_load_card)

    with pytest.raises(svc.AmexCardPaymentError) as exc:
        await svc.create_amex_card_payment_request(
            session,
            actor=SimpleNamespace(id=uuid4(), nombre="Usuario", rol="empleado"),
            request=svc.AmexCardPaymentRequest(
                card_account_id=uuid4(),
                amount=Decimal("100.00"),
                fecha_pago=date(2026, 8, 14),
            ),
        )

    assert exc.value.code == "forbidden"


def test_parse_amex_payment_amount_is_strict_money():
    assert svc.parse_amex_payment_amount("1,234.567") == Decimal("1234.57")
    with pytest.raises(svc.AmexCardPaymentError):
        svc.parse_amex_payment_amount("0")
    with pytest.raises(svc.AmexCardPaymentError):
        svc.parse_amex_payment_amount("nope")
