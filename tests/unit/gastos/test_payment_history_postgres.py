"""Acceptance queries run only against a disposable synthetic PostgreSQL cluster."""

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from devnous.gastos.routes.payment_history_ui import FILTER_DEFAULTS
from devnous.gastos.services.payment_history_service import list_payment_history


@pytest_asyncio.fixture
async def history_db():
    url = os.environ.get("PAYMENT_HISTORY_SYNTHETIC_URL")
    if not url:
        pytest.skip("Requires isolated synthetic PostgreSQL cluster")
    if "host=/tmp/samchat-history-" not in url:
        pytest.fail("Only disposable local history-test clusters are allowed")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        async with conn.begin():
            statements = [
                "CREATE TEMP TABLE empleados (id uuid PRIMARY KEY, nombre text)",
                "CREATE TEMP TABLE proveedores_clientes (id uuid PRIMARY KEY, nombre text)",
                "CREATE TEMP TABLE documentos (id uuid PRIMARY KEY, numero_referencia text, referencia_operaciones integer, estado text, fecha_pago date, aprobado_en timestamp, pagado_en timestamp, pago_urgente boolean, concepto_pago text, currency text, monto_total numeric, monto_solicitado numeric, empleado_id uuid, beneficiario_empleado_id uuid, proveedor_cliente_id uuid, tipo text, creado_en timestamp)",
                "CREATE TEMP TABLE payment_run_closures (id uuid PRIMARY KEY, closed_at timestamp, run_date date)",
                "CREATE TEMP TABLE payment_run_closure_items (documento_id uuid, closure_id uuid)",
                "INSERT INTO empleados VALUES (md5('Ana')::uuid, 'Ana'), (md5('Luis')::uuid, 'Luis')",
                "INSERT INTO proveedores_clientes VALUES (md5('Proveedor')::uuid, 'Proveedor sintético')",
                """INSERT INTO documentos
                    SELECT md5(n::text)::uuid, 'S-' || n, n,
                    CASE WHEN n % 3 = 0 THEN 'pagado' WHEN n % 3 = 1 THEN 'en_proceso_pago' ELSE 'aprobado' END,
                    '2026-10-20'::date, '2026-10-19'::timestamp,
                    CASE WHEN n % 3 = 0 THEN '2026-10-20 23:59:59'::timestamp END,
                    false, 'Prueba ' || n, CASE WHEN n % 2 = 0 THEN 'MXN' ELSE 'USD' END,
                    n + 0.01, NULL, md5('Ana')::uuid, md5('Luis')::uuid,
                    md5('Proveedor')::uuid, 'SOLICITUD', '2026-09-30'::timestamp
                    FROM generate_series(1, 603) n""",
            ]
            for statement in statements:
                await conn.execute(text(statement))
            async with AsyncSession(bind=conn) as session:
                yield session
    await engine.dispose()


def filters(**changes):
    return {**FILTER_DEFAULTS, **changes}


@pytest.mark.asyncio
async def test_global_sort_pagination_totals_beyond_500(history_db):
    result = await list_payment_history(
        history_db, filters(sort="monto", direction="desc")
    )
    assert result["total_count"] == 603
    assert len(result["rows"]) == 50
    assert result["rows"][0]["monto"] == "603.01"
    assert result["rows"][-1]["monto"] == "554.01"
    assert sum(t["count"] for t in result["totals"]) == 603
    assert {t["currency"] for t in result["totals"]} == {"MXN", "USD"}
    last = await list_payment_history(
        history_db, filters(page="999", sort="monto", direction="asc")
    )
    assert last["current_page"] == 13
    assert [r["monto"] for r in last["rows"]] == ["601.01", "602.01", "603.01"]
    assert sorted(last["totals"], key=lambda t: t["currency"]) == sorted(
        result["totals"], key=lambda t: t["currency"]
    )


@pytest.mark.asyncio
async def test_combined_filters_and_paid_end_of_day(history_db):
    result = await list_payment_history(
        history_db,
        filters(
            status="pagadas",
            month="2026-10",
            date_kind="pago",
            date_from="2026-10-20",
            date_to="2026-10-20",
            employee="Luis",
            requester="Ana",
            supplier="sintético",
            amount_min="600",
            amount_max="604",
            sort="monto",
            direction="asc",
        ),
    )
    assert [r["monto"] for r in result["rows"]] == ["600.01", "603.01"]
    assert all(r["status"] == "pagada" for r in result["rows"])
    result = await list_payment_history(history_db, filters(status="pendientes"))
    assert result["total_count"] == 402
    assert {r["status"] for r in result["rows"]} == {"programada", "en proceso de pago"}
    assert sum(t["paid_count"] for t in result["totals"]) == 0
    assert sum(t["pending_count"] for t in result["totals"]) == 402


@pytest.mark.asyncio
async def test_missing_data_and_ineligible_documents(history_db):
    await history_db.execute(
        text(
            "UPDATE documentos SET monto_total=NULL, monto_solicitado=NULL, fecha_pago=NULL, currency=NULL WHERE referencia_operaciones=603"
        )
    )
    await history_db.execute(
        text(
            "UPDATE documentos SET estado='rechazado' WHERE referencia_operaciones=602"
        )
    )
    await history_db.execute(
        text("UPDATE documentos SET tipo='INFORME' WHERE referencia_operaciones=601")
    )
    result = await list_payment_history(
        history_db, filters(sort="monto", direction="desc", page="99")
    )
    assert result["total_count"] == 601
    assert result["rows"][-1]["monto"] is None
    missing = next(t for t in result["totals"] if t["currency"] is None)
    assert missing["paid"] is None and missing["missing_amounts"] == 1
    assert result["rows"][-1]["status"] == "pagada"
    filtered = await list_payment_history(history_db, filters(month="2026-10"))
    assert filtered["total_count"] == 600
    empty = await list_payment_history(history_db, filters(supplier="no existe"))
    assert empty == {"rows": [], "totals": [], "total_count": 0, "current_page": 1}


@pytest.mark.asyncio
async def test_shared_source_preserves_existing_payment_run_behavior(
    history_db, monkeypatch
):
    from unittest.mock import AsyncMock

    from devnous.gastos.services import payment_run_service

    monkeypatch.setattr(payment_run_service, "ensure_payment_run_schema", AsyncMock())
    rows = await payment_run_service.list_payment_run_items(
        history_db, status_filter="pagadas", limit=10
    )
    assert len(rows) == 10
    assert all(row["status"] == "pagada" for row in rows)
    await history_db.execute(
        text(
            "UPDATE documentos SET monto_total=NULL, monto_solicitado=NULL WHERE referencia_operaciones=603"
        )
    )
    rows = await payment_run_service.list_payment_run_items(
        history_db, query="Prueba 603", status_filter="todas"
    )
    assert str(rows[0]["monto"]) == "0.00"
    history = await list_payment_history(history_db, filters(q="Prueba 603"))
    assert history["rows"][0]["monto"] is None


@pytest.mark.asyncio
async def test_full_totals_are_exact_and_cutoffs_are_not_paid(history_db):
    from decimal import Decimal

    result = await list_payment_history(history_db, filters())
    for total in result["totals"]:
        candidates = [
            n
            for n in range(1, 604)
            if ("MXN" if n % 2 == 0 else "USD") == total["currency"]
        ]
        assert Decimal(total["paid"]) == sum(
            Decimal(n) + Decimal("0.01") for n in candidates if n % 3 == 0
        )
        assert Decimal(total["pending"]) == sum(
            Decimal(n) + Decimal("0.01") for n in candidates if n % 3 != 0
        )
    await history_db.execute(
        text(
            "INSERT INTO payment_run_closures VALUES (md5('cut')::uuid, '2026-10-20', '2026-10-20')"
        )
    )
    await history_db.execute(
        text(
            "INSERT INTO payment_run_closure_items SELECT id, md5('cut')::uuid FROM documentos WHERE referencia_operaciones IN (2,4)"
        )
    )
    await history_db.execute(
        text("UPDATE documentos SET estado='rechazado' WHERE referencia_operaciones=4")
    )
    pending = await list_payment_history(
        history_db,
        filters(status="cerradas", q="Prueba 2", sort="monto", direction="asc"),
    )
    assert pending["rows"][0]["referencia_operaciones"] == 2
    assert pending["rows"][0]["status"] == "en proceso de pago"
    rejected = await list_payment_history(
        history_db, filters(q="S-4", status="pendientes")
    )
    assert all(row["referencia_operaciones"] != 4 for row in rejected["rows"])


@pytest.mark.asyncio
async def test_state_sort_matches_displayed_labels(history_db):
    await history_db.execute(
        text(
            "UPDATE documentos SET fecha_pago='2000-01-01' WHERE referencia_operaciones=2"
        )
    )
    for direction in ["asc", "desc"]:
        result = await list_payment_history(
            history_db, filters(amount_max="10", sort="estado", direction=direction)
        )
        labels = [row["status"] for row in result["rows"]]
        assert set(labels) == {"pagada", "programada", "vencida", "en proceso de pago"}
        assert labels == sorted(labels, reverse=direction == "desc")
