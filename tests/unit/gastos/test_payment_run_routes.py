from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes


@pytest.mark.asyncio
async def test_payment_run_page_requires_named_manager_or_superadmin() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_run(
            request=SimpleNamespace(query_params={}),
            session=AsyncMock(),
            current_empleado=SimpleNamespace(id=uuid4(), rol="finanzas"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_payment_run_page_renders_fecha_pago_edit_and_close_without_pay(
    monkeypatch,
) -> None:
    empleado_id = uuid4()
    documento_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(
            return_value=[
                {
                    "id": documento_id,
                    "numero_referencia": "S-26000048",
                    "solicitante_nombre": "Benjamin",
                    "beneficiario_nombre": "Proveedor Demo",
                    "concepto_pago": "Uniformes",
                    "fecha_pago": None,
                    "monto": Decimal("1200.00"),
                    "currency": "MXN",
                    "status": "programada",
                    "can_edit_fecha_pago": True,
                    "can_close": True,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )

    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=empleado_id,
            rol="finanzas",
            nombre="Benjamin",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "/admin/finanzas/payment-run/documentos/" in html
    assert 'name="fecha_pago"' in html
    assert 'form="payment-run-close-form"' in html
    assert "/admin/finanzas/payment-run/pay" not in html
    assert "sin registrar pago" in html


@pytest.mark.asyncio
async def test_payment_run_close_uses_close_service(monkeypatch) -> None:
    empleado_id = uuid4()
    close_mock = AsyncMock(
        return_value=SimpleNamespace(
            item_count=2,
            total_amount=Decimal("1500.00"),
        )
    )
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )
    monkeypatch.setattr(admin_routes, "close_payment_run", close_mock)

    response = await admin_routes.admin_finance_payment_run_close(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=empleado_id, rol="finanzas"),
        document_ids=["doc-1", "doc-2"],
        notes="corte semanal",
        run_date="2026-07-31",
    )

    assert response.status_code == 303
    close_mock.assert_awaited_once()
    assert close_mock.await_args.kwargs["document_ids"] == [
        "doc-1",
        "doc-2",
    ]
