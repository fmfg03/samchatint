"""Late evidence races on CI's isolated PostgreSQL, never a production DSN."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKeyConstraint, MetaData, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.schema_guard import SCHEMA_PATCHES
from devnous.gastos.services.cfdi_ingestion_service import (
    CFDIDuplicateLinkError,
    ingest_cfdi_from_upload,
)
from devnous.gastos.services.documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
    add_solicitud_documento_adjuntos,
)
from devnous.gastos.utils.receipt_bytes import invalidate_adjunto_columns_cache


@pytest_asyncio.fixture
async def pg():
    raw = os.environ.get("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("CI isolated PostgreSQL DSN required")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 55471
        or url.database != "devnous_test"
        or url.username != "test_user"
    ):
        pytest.fail("Evidence tests require CI's isolated localhost:55471 database")
    schema = "late_evidence_test_" + uuid4().hex
    invalidate_adjunto_columns_cache()
    admin = create_async_engine(url)
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        copied = MetaData()
        for model in (
            models.Empleado,
            models.ProveedorCliente,
            models.RFCConfig,
            models.Documento,
            models.Adjunto,
            models.Aprobacion,
            models.CFDIReport,
            models.ExpenseReport,
            models.AccountingPoliza,
        ):
            model.__table__.to_metadata(copied)
        for clone in copied.tables.values():
            # Only unrelated legacy FK seeds are omitted; unique/check constraints
            # and the actual row/advisory locks and transactions remain real.
            for constraint in list(clone.constraints):
                if isinstance(constraint, ForeignKeyConstraint) and any(
                    element.target_fullname.rsplit(".", 1)[0] not in copied.tables
                    for element in constraint.elements
                ):
                    clone.constraints.remove(constraint)
                    for element in constraint.elements:
                        clone.foreign_keys.discard(element)
                        element.parent.foreign_keys.discard(element)
        async with engine.begin() as connection:
            await connection.run_sync(copied.create_all)
            # Reproduce the deployed legacy CHECK, rather than relying only on
            # ORM metadata (which does not contain this owner-managed constraint).
            await connection.execute(
                text(
                    "ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_accion_check "
                    "CHECK (accion IN ('enviar', 'aprobar', 'confirmar_cfdi_compartido'))"
                )
            )
            audit_patch = dict(SCHEMA_PATCHES)[
                "aprobaciones_accion_check_workflow_actions"
            ]
            await connection.execute(text(audit_patch))
            await connection.execute(text(audit_patch))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        actor, provider, first, second = [uuid4() for _ in range(4)]
        async with factory.begin() as session:
            session.add(models.Empleado(id=actor, nombre="Test owner", rol="empleado"))
            session.add(
                models.ProveedorCliente(
                    id=provider,
                    nombre="Test provider",
                    tipo="proveedor",
                    rfc="AAA010101AAA",
                    activo=True,
                )
            )
            session.add(
                models.RFCConfig(
                    name="Test company",
                    taxpayer="Test company",
                    tax_id="BBB010101BBB",
                    active=True,
                )
            )
            for identifier in (first, second):
                session.add(
                    models.Documento(
                        id=identifier,
                        empleado_id=actor,
                        tipo="SOLICITUD",
                        estado="pagado",
                        proveedor_cliente_id=provider,
                        numero_referencia="S-TEST-" + identifier.hex,
                        monto_solicitado=1000,
                        monto_total=1000,
                        currency="MXN",
                    )
                )
        yield SimpleNamespace(factory=factory, actor=actor, first=first, second=second)
    finally:
        invalidate_adjunto_columns_cache()
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


def invoice():
    fiscal_uuid = str(uuid4()).upper()
    xml = f"""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
      xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0"
      Fecha="2026-10-08T12:00:00" SubTotal="1000" Total="1000" Moneda="MXN"
      TipoDeComprobante="I" MetodoPago="PUE">
      <cfdi:Emisor Rfc="AAA010101AAA" Nombre="Test provider"/>
      <cfdi:Receptor Rfc="BBB010101BBB" Nombre="Test company"/>
      <cfdi:Conceptos><cfdi:Concepto Descripcion="Servicio" Cantidad="1"
      ValorUnitario="1000" Importe="1000"/></cfdi:Conceptos>
      <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{fiscal_uuid}" Version="1.1"
      FechaTimbrado="2026-10-08T12:00:01"/></cfdi:Complemento></cfdi:Comprobante>"""
    return SolicitudTercerosAttachment(
        xml.encode(), "factura.xml", "application/xml", "cfdi_xml"
    )


async def locked_document(session, identifier):
    return (
        await session.execute(
            select(models.Documento)
            .where(models.Documento.id == identifier)
            .with_for_update()
        )
    ).scalar_one()


@pytest.mark.parametrize("mode", ["repeat", "other_evidence", "canonical"])
async def test_concurrent_invoice_is_idempotent_or_reserved(pg, mode):
    attachment = invoice()
    started = asyncio.Event()
    worker = {}

    async def second_upload():
        async with pg.factory() as session:
            worker["pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            document = await locked_document(
                session, pg.first if mode == "repeat" else pg.second
            )
            try:
                if mode == "canonical":
                    await ingest_cfdi_from_upload(
                        session,
                        xml_bytes=attachment.raw_bytes,
                        source="user_upload",
                        entity=document,
                        require_shared_confirmation=True,
                    )
                    pytest.fail("Concurrent canonical ingestion reused late evidence")
                return await add_solicitud_documento_adjuntos(
                    session,
                    documento=document,
                    attachments=[attachment],
                    actor_id=pg.actor,
                )
            except SolicitudValidationError as exc:
                await session.rollback()
                return exc.code
            except CFDIDuplicateLinkError:
                await session.rollback()
                return "duplicate_cfdi"

    async with pg.factory() as session:
        document = await locked_document(session, pg.first)
        assert (
            await add_solicitud_documento_adjuntos(
                session,
                documento=document,
                attachments=[attachment],
                actor_id=pg.actor,
                commit=False,
            )
            == 1
        )
        await session.flush()
        pending = asyncio.create_task(second_upload())
        try:
            await asyncio.wait_for(started.wait(), 5)

            async def wait_for_contention():
                async with pg.factory() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE pid = :pid AND locktype = :kind AND NOT granted)"
                        ),
                        {
                            "pid": worker["pid"],
                            "kind": (
                                "transactionid" if mode == "repeat" else "advisory"
                            ),
                        },
                    ):
                        if pending.done():
                            pytest.fail(
                                "Second upload finished without contending on its lock"
                            )
                        await asyncio.sleep(0.01)

            # Observe a real PostgreSQL waiter before releasing the first writer;
            # connection/scheduler latency alone must not make this test pass.
            await asyncio.wait_for(wait_for_contention(), 10)
            await session.commit()
            assert await asyncio.wait_for(pending, 10) == (
                0 if mode == "repeat" else "duplicate_cfdi"
            )
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await session.rollback()
    async with pg.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(models.Adjunto)) == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(models.Aprobacion)
                .where(models.Aprobacion.accion == "adjuntar_factura")
            )
            == 1
        )
        for model in (models.CFDIReport, models.ExpenseReport, models.AccountingPoliza):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_owner_migration_matches_guard_and_rejects_unknown_actions(pg):
    from pathlib import Path

    patch = dict(SCHEMA_PATCHES)["aprobaciones_accion_check_workflow_actions"]
    migration = (
        Path(__file__).resolve().parents[2]
        / "database/migrations/20261010_late_support_audit_actions.sql"
    ).read_text(encoding="utf8")
    assert patch.strip() in migration
    async with pg.factory() as session:
        definition = await session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='aprobaciones'::regclass AND conname='aprobaciones_accion_check'"
            )
        )
        assert "adjuntar_soporte" in definition and "adjuntar_factura" in definition
        with pytest.raises(IntegrityError) as error:
            async with session.begin_nested():
                session.add(
                    models.Aprobacion(
                        tipo_entidad="documento",
                        entidad_id=pg.first,
                        aprobador_id=pg.actor,
                        accion="unknown_action",
                    )
                )
                await session.flush()
        assert "aprobaciones_accion_check" in str(error.value)
