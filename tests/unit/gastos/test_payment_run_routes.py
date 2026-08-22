from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes


@pytest.mark.asyncio
async def test_payment_run_page_rejects_non_finance_non_manager() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_run(
            request=SimpleNamespace(query_params={}),
            session=AsyncMock(),
            current_empleado=SimpleNamespace(id=uuid4(), rol="operaciones", departamento="Operaciones"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_payment_run_page_renders_fecha_pago_close_without_payment_proof_for_manager(
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
                    "can_upload_payment_proof": False,
                },
                {
                    "id": uuid4(),
                    "numero_referencia": "S-26000049",
                    "solicitante_nombre": "Jacquie",
                    "beneficiario_nombre": "Proveedor Pago",
                    "concepto_pago": "Hospedaje",
                    "fecha_pago": None,
                    "monto": Decimal("900.00"),
                    "currency": "MXN",
                    "status": "en proceso de pago",
                    "can_edit_fecha_pago": False,
                    "can_close": False,
                    "can_upload_payment_proof": True,
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
    assert "En Proceso de Pago" in html
    assert "Testigo de pago" in html
    assert "comprobante-pago" not in html
    assert "Subir testigo y pagar" not in html
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


@pytest.mark.asyncio
async def test_payment_run_page_renders_payment_proof_for_accounting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(
            return_value=[
                {
                    "id": uuid4(),
                    "numero_referencia": "S-26000100",
                    "solicitante_nombre": "Dani",
                    "beneficiario_nombre": "Proveedor Pago",
                    "concepto_pago": "Hospedaje",
                    "fecha_pago": None,
                    "monto": Decimal("900.00"),
                    "currency": "MXN",
                    "status": "en proceso de pago",
                    "can_edit_fecha_pago": False,
                    "can_close": False,
                    "can_upload_payment_proof": True,
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
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="cerradas",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "comprobante-pago" in html
    assert "Subir testigo y pagar" in html
