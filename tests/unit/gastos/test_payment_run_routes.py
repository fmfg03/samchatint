from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes


def test_payment_run_amount_issue_is_visible_and_not_selectable() -> None:
    document_id = uuid4()
    html = admin_routes._render_payment_run_items(
        [
            {
                "id": document_id,
                "numero_referencia": "S-260005",
                "concepto_pago": "Reembolso de saldo a favor",
                "monto": Decimal("0.00"),
                "currency": "MXN",
                "status": "programada",
                "can_close": False,
                "amount_issue": "Reembolso sin monto_total; requiere conciliacion.",
            }
        ]
    )

    assert "requiere conciliacion" in html
    assert f'name="document_ids" value="{document_id}"' not in html


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
            side_effect=[
                [
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
                ],
                [
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
                    },
                ],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
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

    assert "Solicitudes aprobadas para corte" in html
    assert "Comprobantes pendientes - En Proceso de Pago" in html
    assert "/admin/finanzas/payment-run/documentos/" in html
    assert 'name="fecha_pago"' in html
    assert 'form="payment-run-close-form"' in html
    assert "/admin/finanzas/payment-run/pay" not in html
    assert "En Proceso de Pago" in html
    assert "Testigo de pago" in html
    assert "comprobante-pago" not in html
    assert "Subir testigo y pagar" not in html
    assert "sin registrar pago" not in html


@pytest.mark.asyncio
async def test_payment_run_page_queries_approved_and_in_process_sections(
    monkeypatch,
) -> None:
    list_mock = AsyncMock(side_effect=[[], []])
    monkeypatch.setattr(admin_routes, "list_payment_run_items", list_mock)
    loan_list_mock = AsyncMock(side_effect=[[], []])
    monkeypatch.setattr(admin_routes, "list_prestamo_payment_run_items", loan_list_mock)
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )

    await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q="S-26000146",
    )

    assert list_mock.await_args_list[0].kwargs["status_filter"] == "pendientes"
    assert list_mock.await_args_list[1].kwargs["status_filter"] == "cerradas"
    assert list_mock.await_args_list[0].kwargs["query"] == "S-26000146"
    assert list_mock.await_args_list[1].kwargs["query"] == "S-26000146"
    assert loan_list_mock.await_args_list[0].kwargs["status_filter"] == "pendientes"
    assert loan_list_mock.await_args_list[1].kwargs["status_filter"] == "cerradas"
    assert loan_list_mock.await_args_list[0].kwargs["query"] == "S-26000146"
    assert loan_list_mock.await_args_list[1].kwargs["query"] == "S-26000146"


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
            side_effect=[
                [],
                [
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
                    },
                ],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
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

    assert "Comprobantes pendientes - En Proceso de Pago" in html
    assert "comprobante-pago" in html
    assert "Subir testigo y pagar" in html


@pytest.mark.asyncio
async def test_payment_run_page_renders_approved_and_in_process_loans(
    monkeypatch,
) -> None:
    approved_id = uuid4()
    proof_id = uuid4()
    monkeypatch.setattr(admin_routes, "list_payment_run_items", AsyncMock(side_effect=[[], []]))
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": approved_id,
                        "entity_type": "prestamo",
                        "numero_referencia": "PRE-26000010",
                        "solicitante_nombre": "Sebas",
                        "beneficiario_nombre": "Sebas",
                        "concepto_pago": "Prestamo viaje",
                        "fecha_pago": None,
                        "monto": Decimal("2000.00"),
                        "currency": "MXN",
                        "status": "programada",
                        "can_edit_fecha_pago": False,
                        "can_close": True,
                        "can_upload_payment_proof": False,
                    },
                ],
                [
                    {
                        "id": proof_id,
                        "entity_type": "prestamo",
                        "numero_referencia": "PRE-26000011",
                        "solicitante_nombre": "Dani",
                        "beneficiario_nombre": "Dani",
                        "concepto_pago": "Prestamo apoyo",
                        "fecha_pago": None,
                        "monto": Decimal("900.00"),
                        "currency": "MXN",
                        "status": "en proceso de pago",
                        "can_edit_fecha_pago": False,
                        "can_close": False,
                        "can_upload_payment_proof": True,
                    },
                ],
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
            rol="superadmin",
            departamento="Contabilidad",
            nombre="Superadmin",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "PRE-26000010" in html
    assert "PRE-26000011" in html
    assert f'action="/prestamos/{approved_id}/programar-pago"' in html
    assert f'action="/prestamos/{proof_id}/comprobante-pago"' in html
    assert "Préstamo" in html


@pytest.mark.asyncio
async def test_payment_run_legacy_pay_endpoint_is_blocked() -> None:
    response = await admin_routes.admin_finance_payment_run_pay(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        document_ids=[str(uuid4())],
        year=None,
        month=None,
    )

    assert response.status_code == 303
    assert "/admin/finanzas/payment-run?error_msg=" in response.headers["location"]
    assert "comprobante" in response.headers["location"]


def test_payment_run_upload_payment_proof_is_atomic() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    start = source.index("async def admin_finance_payment_run_upload_payment_proof")
    end = source.index("@router.get(\"/admin/finanzas/payment-run/closures", start)
    block = source[start:end]

    assert "commit=False" in block
    assert "await register_document_payment(" in block
    assert "actor=current_empleado" in block


def test_accounting_profile_can_create_employee_beneficiary_requests() -> None:
    preset = admin_routes._PROFILE_PRESETS["contabilidad"]

    assert "finance.employee_beneficiary.request" in preset["permissions"]
    assert "contabilidad.pagos.marcar_pagado" in preset["permissions"]


def test_finance_dashboard_no_longer_renders_mass_mark_paid_form() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    start = source.index("async def admin_finance_platform")
    end = source.index("@router.get(\"/admin/finanzas/payment-run\"", start)
    block = source[start:end]

    assert "/admin/finanzas/payment-run/pay" not in block
    assert "Registrar seleccionados como pagados" not in block


def test_payment_run_tables_are_sortable_by_operational_reference() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    helper = source[
        source.index("def _admin_sortable_table_assets"):
        source.index("def _payment_run_badge")
    ]
    payment_run = source[
        source.index("@router.get(\"/admin/finanzas/payment-run\""):
        source.index("def _render_payment_history_rows")
    ]
    history_start = source.index("@router.get(\"/admin/finanzas/payment-history\"")
    payment_history = source[
        history_start:
        source.index("@router.post(\"/admin/finanzas/payment-run/documentos", history_start)
    ]

    assert "table[data-sortable-table]" in helper
    assert "header.cellIndex" in helper
    assert "_payment_run_sort_key" in source
    assert "_payment_run_ref_number" in source
    assert "for row in sorted(rows, key=_payment_run_sort_key)" in source
    for block in (payment_run, payment_history):
        assert "data-sortable-table" in block
        assert 'data-default-sort-dir="desc"' in block
        assert 'data-sort-key="referencia_operaciones"' in block
        assert 'data-sort-type="number"' in block
        assert "_admin_sortable_table_assets()" in block
