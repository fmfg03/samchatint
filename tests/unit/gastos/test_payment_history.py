from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes, user_routes
from devnous.gastos.routes.payment_history_ui import FILTER_DEFAULTS, render_history
from devnous.gastos.services.payment_history_service import (
    DATE_COLUMNS,
    SORT_COLUMNS,
    history_query,
    list_payment_history,
)
from devnous.gastos.services.payment_run_service import PaymentRunValidationError


def values(**changes):
    return {**FILTER_DEFAULTS, **changes}


def empty_payload(**changes):
    return {"rows": [], "totals": [], "total_count": 0, "current_page": 1, **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "invalid"},
        {"sort": "monto; DELETE FROM documentos"},
        {"direction": "desc NULLS FIRST;"},
        {"date_kind": "invalid"},
        {"page": "0"},
        {"page": "1000001"},
        {"page": "x"},
        {"month": "2026-13"},
        {"date_from": "31/01/2026"},
        {"date_from": "2026-10-20", "date_to": "2026-10-19"},
        {"month": "2026-10", "date_to": "2026-09-01"},
        {"amount_min": "NaN"},
        {"amount_max": "Infinity"},
        {"amount_min": "-1"},
        {"amount_max": "abc"},
        {"amount_min": "10", "amount_max": "5"},
    ],
)
def test_invalid_filters_fail_closed(changes):
    with pytest.raises(PaymentRunValidationError):
        history_query(values(**changes))


def test_combined_filters_are_bound_and_preserve_zero_amount():
    sql, params, page = history_query(
        values(
            status="pendientes",
            month="2024-02",
            date_from="2024-02-02",
            date_to="2024-03-04",
            date_kind="pago",
            employee="Ana",
            requester="Luis",
            beneficiary="Luz",
            supplier="x' OR 1=1 --",
            q="S-1",
            amount_min="0",
            amount_max="100.25",
            page="2",
        )
    )
    assert page == 2
    assert params["date_from"] == date(2024, 2, 2)
    assert params["date_to"] == date(2024, 2, 29)
    assert params["amount_min"] == Decimal("0")
    assert params["amount_max"] == Decimal("100.25")
    assert "x' OR" not in sql
    assert params["supplier"] == "%x' OR 1=1 --%"
    assert "pagado_en::date <= :date_to" in sql
    assert "NOT (estado = 'pagado' OR pagado_en IS NOT NULL)" in sql
    assert (
        "solicitante_nombre ILIKE :employee OR beneficiario_nombre ILIKE :employee"
        in sql
    )


@pytest.mark.parametrize(
    "status, expected",
    [
        ("pagadas", "(estado = 'pagado' OR pagado_en IS NOT NULL)"),
        ("cerradas", "(estado = 'en_proceso_pago' OR closure_id IS NOT NULL)"),
        ("programadas", "estado = 'aprobado' AND closure_id IS NULL"),
        ("todas", "estado IN ('aprobado', 'en_proceso_pago', 'pagado')"),
    ],
)
def test_status_filters(status, expected):
    sql, _, _ = history_query(values(status=status))
    assert expected in sql


@pytest.mark.parametrize("date_kind, column", DATE_COLUMNS.items())
def test_date_selector(date_kind, column):
    sql, _, _ = history_query(values(date_kind=date_kind, month="2026-10"))
    assert f"{column}::date >= :date_from" in sql
    assert f"{column}::date <= :date_to" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("sort, column", SORT_COLUMNS.items())
@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_global_order_and_read_only_single_statement(sort, column, direction):
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = empty_payload(
        rows=[
            {
                "estado": "aprobado",
                "fecha_pago": "2020-01-01",
                "closure_id": None,
            }
        ]
    )
    session.execute.return_value = result
    payload = await list_payment_history(
        session, values(sort=sort, direction=direction)
    )
    sql = str(session.execute.call_args.args[0])
    assert f"ORDER BY {column} {direction.upper()} NULLS LAST, id ASC" in sql
    assert sql.index("totals AS") < sql.index("LIMIT 50 OFFSET")
    assert "CREATE TABLE" not in sql
    assert "jsonb_build_object('monto', p.monto::text)" in sql
    assert session.execute.await_count == 1
    assert payload["rows"][0]["status"] == "vencida"


def test_render_keeps_filters_and_does_not_resort_page_or_mix_money():
    content = render_history(
        values(employee="<Ana>", sort="monto"),
        empty_payload(
            rows=[
                {
                    "id": str(uuid4()),
                    "estado": "pagado",
                    "status": "pagada",
                    "monto": None,
                    "numero_referencia": "<script>",
                    "fecha_pago": None,
                }
            ],
            total_count=102,
            current_page=2,
            totals=[
                {
                    "currency": "USD",
                    "paid": "10.10",
                    "pending": None,
                    "paid_count": 1,
                    "pending_count": 1,
                    "missing_amounts": 1,
                }
            ],
        ),
    )
    assert "&lt;script&gt;" in content and "<script>" not in content
    assert 'value="&lt;Ana&gt;"' in content
    assert "USD $10.10" in content and "No disponible" in content
    assert "Página 2 de 3 · 102 solicitudes" in content
    assert "Anterior" in content and "Siguiente" in content
    assert "employee=%3CAna%3E" in content
    assert "sort=monto" in content and "direction=asc" in content
    assert "data-sortable-table" not in content  # Avoid sorting only a page in JS.
    assert 'aria-sort="descending"' in content


def test_empty_and_zero_summary_and_invalid_filter_alert():
    content = render_history(
        values(direction="asc"),
        empty_payload(
            totals=[
                {
                    "currency": None,
                    "paid": None,
                    "pending": None,
                    "paid_count": 0,
                    "pending_count": 0,
                    "missing_amounts": 0,
                }
            ]
        ),
        "<bad>",
    )
    assert "Sin solicitudes" in content
    assert "Moneda no disponible $0.00" in content
    assert "&lt;bad&gt;" in content
    assert "Anterior" not in content and "Siguiente" not in content
    assert 'aria-sort="ascending"' in content


@pytest.mark.asyncio
async def test_route_denies_unprivileged_before_query(monkeypatch):
    query = AsyncMock()
    monkeypatch.setattr(admin_routes, "list_payment_history", query)
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_history(
            SimpleNamespace(query_params={}),
            session=AsyncMock(),
            current_empleado=SimpleNamespace(id=uuid4(), rol="empleado"),
        )
    assert exc.value.status_code == 403
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_accounting_auxiliary_can_consult_and_invalid_filters_do_not_fall_back(
    monkeypatch,
):
    query = AsyncMock(return_value=empty_payload())
    monkeypatch.setattr(admin_routes, "list_payment_history", query)
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *a, **kw: "")
    employee = SimpleNamespace(id=uuid4(), rol="usuario", departamento="Contabilidad")
    response = await admin_routes.admin_finance_payment_history(
        SimpleNamespace(query_params={"employee": "Ana", "page": "2"}),
        session=AsyncMock(),
        current_empleado=employee,
    )
    assert response.status_code == 200
    assert query.call_args.args[1]["employee"] == "Ana"
    assert b"Pagado y pendiente" in response.body
    assert b'method="POST"' not in response.body
    query.side_effect = PaymentRunValidationError("Revisa el monto")
    response = await admin_routes.admin_finance_payment_history(
        SimpleNamespace(query_params={"amount_min": "NaN"}),
        session=AsyncMock(),
        current_empleado=employee,
    )
    assert response.status_code == 400
    assert b"Revisa el monto" in response.body
    assert query.await_count == 2  # Never retry invalid filters as an unfiltered query.


def test_gastos_navigation_only_shows_lookup_to_existing_authorized_readers():
    employee = SimpleNamespace(
        id=uuid4(),
        rol="usuario",
        departamento="Contabilidad",
        visible_tool_keys={"admin.finanzas"},
    )
    assert "Pagado y pendiente" in user_routes._gastos_workspace_nav_html(
        employee, "pagos"
    )
    employee.rol = "empleado"
    employee.departamento = "Operaciones"
    assert "Pagado y pendiente" not in user_routes._gastos_workspace_nav_html(
        employee, "pagos"
    )
