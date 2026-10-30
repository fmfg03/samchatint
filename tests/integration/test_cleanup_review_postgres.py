"""Real audit constraints, timestamps and competing transactions on isolated PG.

Uses the existing Unix-socket-only temporary cluster fixture; no production DSN.
"""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.services import cleanup_review_service as reviews
from scripts.request_merced_cleanup_review import validate_audit_storage
from test_telegram_finance_recovery_postgres import isolated_postgres_url  # noqa: F401

# Exact allowed action set observed in the deployed aprobaciones_accion_check.
ALLOWED_ACTIONS = (
    "enviar",
    "enviar_control_presupuestal",
    "rechazar_control_presupuestal",
    "asignar_partida_presupuestal",
    "asignar_partida_presupuestal_linea",
    "asignar_partidas_presupuestales",
    "reversar_a_control_presupuestal",
    "aprobar",
    "rechazar",
    "cancelar",
    "editar",
    "pagar",
    "aprobar_area",
    "rechazar_area",
    "aprobar_final",
    "rechazar_final",
    "retirar",
    "adjuntar_comprobante_no_deducible",
    "reemplazar_comprobante_no_deducible",
    "eliminar_comprobante_no_deducible",
    "confirmar_cfdi_compartido",
    "regularizar_aprobacion_reembolso",
    "liberar_cfdi_duplicado",
    "adjuntar_soporte",
    "adjuntar_factura",
)


@pytest_asyncio.fixture
async def pg(isolated_postgres_url):  # noqa: F811 - imported pytest fixture
    engine = create_async_engine(isolated_postgres_url, hide_parameters=True)
    metadata = MetaData()
    for owner in (models.Empleado, models.ExpenseReport, models.Aprobacion):
        clone = owner.__table__.to_metadata(metadata)
        for constraint in list(clone.constraints):
            if isinstance(constraint, ForeignKeyConstraint) and not (
                clone.name == "aprobaciones"
                and all(
                    fk.target_fullname == "empleados.id" for fk in constraint.elements
                )
            ):
                clone.constraints.remove(constraint)
                for element in constraint.elements:
                    clone.foreign_keys.discard(element)
                    element.parent.foreign_keys.discard(element)
    allowed = ",".join(f"'{value}'::text" for value in ALLOWED_ACTIONS)
    metadata.tables["aprobaciones"].append_constraint(
        CheckConstraint(
            f"accion = ANY (ARRAY[{allowed}])", name="aprobaciones_accion_check"
        )
    )
    metadata.tables["aprobaciones"].append_constraint(
        CheckConstraint(
            "tipo_entidad IN ('documento','gasto','beneficiary_onboarding',"
            "'budget_cfdi_income_link')",
            name="aprobaciones_tipo_entidad_check",
        )
    )
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor, expense_id = uuid4(), uuid4()
    async with factory.begin() as session:
        session.add(
            models.Empleado(
                id=actor, nombre="Synthetic Finance", rol="finanzas", activo=True
            )
        )
        await session.flush()
        session.add(
            models.ExpenseReport(
                id=expense_id,
                proyecto="La Merced",
                concepto="Synthetic weekly payment",
                gasto_cantidad=2289.83,
                fecha=datetime(2026, 10, 26),
                estado_gasto="activo",
                numero_referencia="SYNTHETIC-MERCED",
            )
        )
    try:
        yield SimpleNamespace(factory=factory, actor=actor, expense_id=expense_id)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


async def financial_snapshot(session, expense_id):
    return dict(
        (
            await session.execute(
                select(models.ExpenseReport.__table__).where(
                    models.ExpenseReport.id == expense_id
                )
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_real_constraint_and_lifecycle_preserve_every_expense_field(pg):
    async with pg.factory.begin() as session:
        await validate_audit_storage(session)
        before = await financial_snapshot(session, pg.expense_id)
        # Reproduce the rejected deployment encoding without keeping any row.
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    models.Aprobacion(
                        tipo_entidad="gasto",
                        entidad_id=pg.expense_id,
                        aprobador_id=pg.actor,
                        accion=reviews.REQUESTED,
                        comentario="unsupported action",
                        fecha=datetime.now(UTC),
                    )
                )
                await session.flush()
        assert await reviews.request_review(session, pg.expense_id, pg.actor)

    async with pg.factory.begin() as session:
        state = (await reviews.load_review_states(session, [pg.expense_id]))[
            pg.expense_id
        ]
        assert state.pending and state.fecha.tzinfo is not None
        assert await reviews.confirm_review(session, pg.expense_id, pg.actor)

    async with pg.factory.begin() as session:
        assert not await reviews.request_review(session, pg.expense_id, pg.actor)
        assert not await reviews.confirm_review(session, pg.expense_id, pg.actor)
        assert not (await reviews.load_review_states(session, [pg.expense_id]))[
            pg.expense_id
        ].pending
        assert await financial_snapshot(session, pg.expense_id) == before
        events = (
            (
                await session.execute(
                    select(models.Aprobacion).order_by(models.Aprobacion.fecha)
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2 and all(row.accion == "editar" for row in events)
        assert [json.loads(row.comentario)["event"] for row in events] == [
            reviews.REQUESTED,
            reviews.CONFIRMED,
        ]
        assert events[1].fecha > events[0].fecha


@pytest.mark.asyncio
async def test_competing_transactions_request_and_confirm_only_once(pg):
    async def operate(action):
        async with pg.factory.begin() as session:
            return await action(session, pg.expense_id, pg.actor)

    assert sorted(
        await asyncio.gather(
            operate(reviews.request_review), operate(reviews.request_review)
        )
    ) == [False, True]
    assert sorted(
        await asyncio.gather(
            operate(reviews.confirm_review), operate(reviews.confirm_review)
        )
    ) == [False, True]
    async with pg.factory() as session:
        events = (await session.execute(select(models.Aprobacion))).scalars().all()
        assert len(events) == 2


@pytest.mark.asyncio
async def test_plain_text_edit_history_does_not_create_a_review(pg):
    async with pg.factory.begin() as session:
        session.add(
            models.Aprobacion(
                tipo_entidad="gasto",
                entidad_id=pg.expense_id,
                aprobador_id=pg.actor,
                accion="editar",
                comentario="Clasificación COI desde Finanzas.",
            )
        )
    async with pg.factory() as session:
        assert await reviews.load_review_states(session, [pg.expense_id]) == {}
