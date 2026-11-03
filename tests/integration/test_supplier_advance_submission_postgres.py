"""Supplier invoice commits with the real audit CHECK on disposable PostgreSQL.

No production DSN or transport. Commit, CFDI parsing/reservation, attachments,
advance guards and independent readback are real. Authorization-route preparation
(unrelated policy fixture) and Telegram scheduling are stubbed; attachment-column
introspection is pinned to the fixture ORM columns, not the production schema.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from asyncpg.exceptions import RaiseError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import UploadFile
from test_telegram_finance_recovery_postgres import isolated_postgres_url  # noqa: F401

from devnous.gastos import models
from devnous.gastos.routes import supplier_advance_routes as routes
from devnous.gastos.schema_guard import SCHEMA_PATCHES
from devnous.gastos.services import documento_telegram, project_authorization_service
from devnous.gastos.services import supplier_advance_service as service
from devnous.gastos.services.documento_service import SolicitudTercerosAttachment
from devnous.gastos.utils import receipt_bytes

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261103_supplier_advance_audit_action.sql"
ACTION = "comprobar_anticipo_proveedor"
PAID = Decimal("36429.169999999998253770172595977783203125")


async def migration(connection, filename):
    """Execute owner SQL inside the fixture's existing real transaction."""
    sql = Path(filename).read_text()
    body = sql.split("BEGIN;", 1)[1].rsplit("COMMIT;", 1)[0]
    raw = await connection.get_raw_connection()
    await raw.driver_connection.execute(body)


@pytest_asyncio.fixture(params=["text", "varchar(50)"])
async def pg(isolated_postgres_url, monkeypatch, request):  # noqa: F811
    schema = "supplier_submission_" + uuid4().hex
    admin = create_async_engine(isolated_postgres_url, hide_parameters=True)
    async with admin.begin() as connection:
        assert await connection.scalar(text("SHOW listen_addresses")) == ""
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        isolated_postgres_url,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
            # Both observed production text and ORM-created varchar are supported.
            await connection.execute(
                text(
                    f"ALTER TABLE aprobaciones ALTER COLUMN accion TYPE {request.param}"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE aprobaciones ADD CONSTRAINT "
                    "aprobaciones_accion_check CHECK (accion IN ('enviar','aprobar'))"
                )
            )
            for filename in (
                "20261010_late_support_audit_actions.sql",
                "20261008_supplier_advances.sql",
                "20261103_supplier_advance_cent_precision.sql",
            ):
                await migration(connection, ROOT / "database/migrations" / filename)
            await connection.execute(
                text(
                    "ALTER TABLE aprobaciones ADD CONSTRAINT "
                    "aprobaciones_tipo_entidad_check CHECK "
                    "(tipo_entidad IN ('documento','gasto','beneficiary_onboarding',"
                    "'budget_cfdi_income_link'))"
                )
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        actor_id, provider_id, advance_id = uuid4(), uuid4(), uuid4()
        async with factory.begin() as session:
            session.add(
                models.Empleado(id=actor_id, nombre="Synthetic owner", rol="empleado")
            )
            session.add(
                models.ProveedorCliente(
                    id=provider_id,
                    nombre="Synthetic provider",
                    rfc="AAA010101AAA",
                    tipo="proveedor",
                    activo=True,
                )
            )
            session.add(
                models.RFCConfig(
                    name="Synthetic company",
                    tax_id="BBB010101BBB",
                    taxpayer="Company",
                    active=True,
                )
            )
            await session.flush()
            session.add(
                models.Documento(
                    id=advance_id,
                    empleado_id=actor_id,
                    tipo="SOLICITUD",
                    numero_referencia="S-SYNTHETIC-ADVANCE",
                    estado="pagado",
                    pagado_en=datetime(2026, 10, 28),
                    proveedor_cliente_id=provider_id,
                    is_supplier_advance=True,
                    supplier_advance_due_date=date(2026, 11, 3),
                    monto_solicitado=PAID,
                    monto_total=PAID,
                    currency="MXN",
                    proyecto_otro="Synthetic project",
                    concepto_pago="Synthetic service",
                )
            )
        # Explicit fixture boundaries: no business approval policy or transport.
        monkeypatch.setattr(
            project_authorization_service,
            "prepare_document_authorization_route",
            AsyncMock(),
        )
        monkeypatch.setattr(
            receipt_bytes,
            "get_adjunto_columns",
            AsyncMock(return_value={c.name for c in models.Adjunto.__table__.columns}),
        )
        notification = Mock()
        monkeypatch.setattr(
            documento_telegram,
            "schedule_budget_control_telegram_notifications",
            notification,
        )
        yield SimpleNamespace(
            engine=engine,
            factory=factory,
            actor_id=actor_id,
            advance_id=advance_id,
            notification=notification,
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def invoice_xml():
    fiscal_id = str(uuid4()).upper()
    xml = f"""<?xml version="1.0"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0"
 Fecha="2026-11-03T12:00:00" SubTotal="36429.17" Total="36429.17"
 Moneda="MXN" TipoDeComprobante="I" MetodoPago="PUE">
 <cfdi:Emisor Rfc="AAA010101AAA" Nombre="Synthetic provider"/>
 <cfdi:Receptor Rfc="BBB010101BBB" Nombre="Synthetic company"/>
 <cfdi:Conceptos><cfdi:Concepto Descripcion="Synthetic service" Cantidad="1"
 ValorUnitario="36429.17" Importe="36429.17"/></cfdi:Conceptos>
 <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{fiscal_id}" Version="1.1"
 FechaTimbrado="2026-11-03T12:00:01"/></cfdi:Complemento>
</cfdi:Comprobante>"""
    return SolicitudTercerosAttachment(
        raw_bytes=xml.encode(),
        filename="synthetic.xml",
        mime_type="application/xml",
        categoria="cfdi_xml",
    )


async def submit(pg, session, submission_id=None, xml=None):
    return await service.submit_supplier_invoice(
        session,
        advance_id=pg.advance_id,
        actor=await session.get(models.Empleado, pg.actor_id),
        submission_id=submission_id or uuid4(),
        xml=xml or invoice_xml(),
    )


async def snapshot(pg):
    async with pg.factory() as session:
        result = {}
        for name, model in (
            ("documents", models.Documento),
            ("audits", models.Aprobacion),
            ("cfdis", models.CFDIReport),
            ("attachments", models.Adjunto),
        ):
            result[name] = await session.scalar(select(func.count()).select_from(model))
        return result


async def upgrade(pg):
    async with pg.engine.begin() as connection:
        await migration(connection, MIGRATION)


@pytest.mark.asyncio
async def test_submission_commits_document_and_audit(pg):
    await upgrade(pg)
    async with pg.factory() as session:
        child = await submit(pg, session)
        child_id = child.id
        await session.commit()
    async with pg.factory() as independent:
        child = await independent.get(models.Documento, child_id)
        audit = (
            await independent.execute(
                select(models.Aprobacion).where(
                    models.Aprobacion.entidad_id == child_id
                )
            )
        ).scalar_one()
        assert child.supplier_advance_id == pg.advance_id
        assert child.estado == "control_presupuestal"
        assert child.supplier_advance_applied == Decimal("36429.17")
        assert child.supplier_invoice_total == Decimal("36429.17")
        assert child.monto_total == 0
        assert child.cfdi_report_id is not None
        assert audit.accion == ACTION and audit.aprobador_id == pg.actor_id
        assert "S-SYNTHETIC-ADVANCE" in audit.comentario
        assert "36429.17" in audit.comentario


@pytest.mark.asyncio
async def test_old_check_rejects_real_service_commit_and_rolls_back(pg):
    before = await snapshot(pg)
    async with pg.factory() as session:
        await submit(pg, session)
        with pytest.raises(IntegrityError, match="aprobaciones_accion_check"):
            await session.commit()
        await session.rollback()
    assert await snapshot(pg) == before


@pytest.mark.asyncio
async def test_handler_commit_replay_and_independent_readback(pg):
    await upgrade(pg)
    key, xml = uuid4(), invoice_xml()
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor_id)
        first = await routes.supplier_invoice_submit(
            pg.advance_id,
            key,
            UploadFile(BytesIO(xml.raw_bytes), filename=xml.filename),
            None,
            session,
            actor,
        )
        assert first.status_code == 303
        replay = await routes.supplier_invoice_submit(
            pg.advance_id,
            key,
            UploadFile(BytesIO(xml.raw_bytes), filename=xml.filename),
            None,
            session,
            actor,
        )
        assert replay.headers["location"] == first.headers["location"]
    assert await snapshot(pg) == {
        "documents": 2,
        "audits": 1,
        "cfdis": 1,
        "attachments": 1,
    }
    pg.notification.assert_called_once()


@pytest.mark.asyncio
async def test_handler_audit_failure_rolls_back_document_and_fiscal_links(pg):
    await upgrade(pg)
    async with pg.engine.begin() as connection:
        # An additional independent audit integrity rule fails at actual commit.
        await connection.execute(
            text(
                "ALTER TABLE aprobaciones ADD CONSTRAINT test_audit_failure "
                "CHECK (accion <> 'comprobar_anticipo_proveedor')"
            )
        )
    before = await snapshot(pg)
    xml = invoice_xml()
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor_id)
        with pytest.raises(IntegrityError, match="test_audit_failure"):
            await routes.supplier_invoice_submit(
                pg.advance_id,
                uuid4(),
                UploadFile(BytesIO(xml.raw_bytes), filename=xml.filename),
                None,
                session,
                actor,
            )
    assert await snapshot(pg) == before
    pg.notification.assert_not_called()
    # The same fiscal evidence is reusable after rollback; no orphan reservation.
    async with pg.engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE aprobaciones DROP CONSTRAINT test_audit_failure")
        )
    async with pg.factory() as session:
        await submit(pg, session, xml=xml)
        await session.commit()
    assert (await snapshot(pg))["cfdis"] == 1


@pytest.mark.asyncio
async def test_migration_preserves_actions_history_and_survives_schema_guard(pg):
    async with pg.engine.begin() as connection:
        prior = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='aprobaciones'::regclass "
                "AND conname='aprobaciones_accion_check'"
            )
        )
    actions = re.findall(r"'([^']+)'::text", prior)
    assert len(actions) == 25
    async with pg.factory.begin() as session:
        session.add_all(
            [
                models.Aprobacion(
                    tipo_entidad="documento",
                    entidad_id=pg.advance_id,
                    aprobador_id=pg.actor_id,
                    accion=action,
                )
                for action in actions
            ]
        )
    before = await snapshot(pg)
    await upgrade(pg)
    async with pg.engine.begin() as connection:
        oid_before = await connection.scalar(
            text(
                "SELECT oid FROM pg_constraint WHERE conrelid='aprobaciones'::regclass "
                "AND conname='aprobaciones_accion_check'"
            )
        )
        await migration(connection, MIGRATION)
        await connection.execute(
            text(dict(SCHEMA_PATCHES)["aprobaciones_accion_check_workflow_actions"])
        )
        oid_after = await connection.scalar(
            text(
                "SELECT oid FROM pg_constraint WHERE conrelid='aprobaciones'::regclass "
                "AND conname='aprobaciones_accion_check'"
            )
        )
        assert oid_after == oid_before  # Rerun/normal bootstrap did not replace it.
    assert await snapshot(pg) == before
    async with pg.factory.begin() as session:
        session.add_all(
            [
                models.Aprobacion(
                    tipo_entidad="documento",
                    entidad_id=pg.advance_id,
                    aprobador_id=pg.actor_id,
                    accion=action,
                )
                for action in [*actions, ACTION]
            ]
        )
    async with pg.factory() as session:
        session.add(
            models.Aprobacion(
                tipo_entidad="documento",
                entidad_id=pg.advance_id,
                aprobador_id=pg.actor_id,
                accion="unknown_action",
            )
        )
        with pytest.raises(IntegrityError, match="aprobaciones_accion_check"):
            await session.commit()
        await session.rollback()
    assert (await snapshot(pg))["audits"] == 51


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["absent", "unknown_action", "permissive", "extra_rule", "unvalidated"]
)
async def test_migration_rejects_drift_without_replacing_constraint(pg, drift):
    async with pg.engine.begin() as connection:
        baseline_expression = await connection.scalar(
            text(
                "SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint "
                "WHERE conrelid='aprobaciones'::regclass "
                "AND conname='aprobaciones_accion_check'"
            )
        )
        await connection.execute(
            text("ALTER TABLE aprobaciones DROP CONSTRAINT aprobaciones_accion_check")
        )
        if drift != "absent":
            expression = (
                "accion IN ('enviar','unknown_action')"
                if drift == "unknown_action"
                else (
                    "accion IS NOT NULL"
                    if drift == "permissive"
                    else "accion IN ('enviar','aprobar')"
                )
            )
            if drift == "extra_rule":
                expression = "(" + baseline_expression + ") AND accion <> 'enviar'"
            suffix = " NOT VALID" if drift == "unvalidated" else ""
            await connection.execute(
                text(
                    "ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_accion_check "
                    "CHECK (" + expression + ")" + suffix
                )
            )

    async def definition():
        async with pg.engine.connect() as connection:
            return (
                await connection.execute(
                    text(
                        "SELECT oid,pg_get_constraintdef(oid),convalidated "
                        "FROM pg_constraint "
                        "WHERE conrelid='aprobaciones'::regclass "
                        "AND conname='aprobaciones_accion_check'"
                    )
                )
            ).all()

    before = await definition()
    with pytest.raises(RaiseError, match="action CHECK|CHECK; owner review"):
        await upgrade(pg)
    assert await definition() == before
