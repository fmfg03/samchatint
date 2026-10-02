"""Finance recovery races on a fresh Unix-socket-only PostgreSQL cluster.

No DSN, credentials, existing database, or Telegram transport is used. Only
message rendering is stubbed; eligibility reads, row locks and writes are real.
"""

import asyncio
import os
import pwd
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKeyConstraint, MetaData, select, text, update
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.schema_guard import SCHEMA_PATCHES
from devnous.gastos.services import documento_telegram
from devnous.gastos.services import telegram_outbox_service as service


@pytest.fixture(scope="module")
def isolated_postgres_url():
    candidates = sorted(
        Path("/usr/lib/postgresql").glob("*/bin"),
        key=lambda path: int(path.parent.name),
        reverse=True,
    )
    if shutil.which("initdb"):
        candidates.append(Path(shutil.which("initdb")).parent)
    binaries = next(
        (
            path
            for path in candidates
            if (path / "initdb").exists() and (path / "pg_ctl").exists()
        ),
        None,
    )
    if binaries is None:
        pytest.skip("PostgreSQL binaries required for isolated acceptance")
    with tempfile.TemporaryDirectory(prefix="samchat-finance-pg-") as directory:
        root = Path(directory)
        sockets = root / "socket"
        sockets.mkdir()
        prefix = []
        if os.geteuid() == 0:
            postgres = pwd.getpwnam("postgres")
            os.chown(root, postgres.pw_uid, postgres.pw_gid)
            os.chown(sockets, postgres.pw_uid, postgres.pw_gid)
            prefix = [shutil.which("runuser"), "-u", "postgres", "--"]

        def command(name, *arguments):
            return subprocess.run(
                [*prefix, str(binaries / name), *map(str, arguments)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )

        data = root / "data"
        command(
            "initdb",
            "-D",
            data,
            "-U",
            "fixture",
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-locale",
            "--encoding=UTF8",
        )
        started = False
        try:
            command(
                "pg_ctl",
                "-D",
                data,
                "-l",
                root / "server.log",
                "-o",
                f"-h '' -k {sockets} -p 55472",
                "-w",
                "start",
            )
            started = True
            yield URL.create(
                "postgresql+asyncpg",
                username="fixture",
                database="postgres",
                host=str(sockets),
                port=55472,
            )
        finally:
            if started:
                command("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")


@pytest_asyncio.fixture
async def pg(isolated_postgres_url, monkeypatch):
    schema = "finance_test_" + uuid4().hex
    admin = create_async_engine(isolated_postgres_url, hide_parameters=True)
    async with admin.begin() as connection:
        assert await connection.scalar(text("SHOW listen_addresses")) == ""
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        isolated_postgres_url,
        hide_parameters=True,
        connect_args={
            "server_settings": {"search_path": schema, "timezone": "Europe/Berlin"}
        },
    )
    metadata = MetaData()
    # Preserve actual outbox FKs; unrelated domain FKs need no business seeds.
    for table in models.Base.metadata.tables.values():
        clone = table.to_metadata(metadata)
        if table.name != "telegram_notification_outbox":
            for constraint in list(clone.constraints):
                if isinstance(constraint, ForeignKeyConstraint):
                    clone.constraints.remove(constraint)
                    for element in constraint.elements:
                        clone.foreign_keys.discard(element)
                        element.parent.foreign_keys.discard(element)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        index_sql = dict(SCHEMA_PATCHES)[
            "ux_telegram_notification_outbox_logical_recipient"
        ]
        await connection.execute(text(index_sql))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    real_rebuild = service.rebuild_outbox_message_text
    monkeypatch.setattr(
        service, "rebuild_outbox_message_text", AsyncMock(return_value="Fixture")
    )
    # Never schedule a sleeping production task during this test suite.
    monkeypatch.setattr(service, "schedule_outbox_retry", lambda entry_id: None)
    monkeypatch.setattr(
        documento_telegram, "get_notification_session_maker", lambda: factory
    )
    try:
        yield SimpleNamespace(factory=factory, real_rebuild=real_rebuild)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def _seed(pg, *, status="pending"):
    employee_id, document_id, entry_id = uuid4(), uuid4(), uuid4()
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with pg.factory.begin() as session:
        session.add(
            models.Empleado(
                id=employee_id,
                nombre="Synthetic Finance",
                rol="finanzas",
                activo=True,
                telegram_user_id=900001,
            )
        )
        await session.flush()
        session.add(
            models.Documento(
                id=document_id,
                empleado_id=employee_id,
                tipo="SOLICITUD",
                numero_referencia="TEST-FINANCE",
                estado="aprobado",
            )
        )
        await session.flush()
        session.add(
            models.TelegramNotificationOutbox(
                id=entry_id,
                documento_id=document_id,
                recipient_empleado_id=employee_id,
                telegram_chat_id=900001,
                notification_type="finance_pending_payment",
                status=status,
                header_text="Fixture",
                body_preview="Fixture",
                retry_count=0,
                created_at=old,
                updated_at=old,
            )
        )
    return SimpleNamespace(employee=employee_id, document=document_id, entry=entry_id)


async def _monitor(pg):
    async with pg.factory() as session:
        return await service.recover_stale_finance_pending_notifications(session)


async def _deliver(pg, seed, *, force=False):
    async with pg.factory() as session:
        return await service.deliver_telegram_notification(
            session,
            notification_type="finance_pending_payment",
            header_text="Fixture",
            text="Fixture",
            chat_id=900001,
            documento_id=seed.document,
            recipient_empleado_id=seed.employee,
            force_resend=force,
        )


@pytest.mark.asyncio
async def test_recovery_rebuilds_full_message_from_current_database(pg, monkeypatch):
    seed = await _seed(pg)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id == seed.document)
            .values(
                monto_solicitado=5232,
                beneficiario_empleado_id=seed.employee,
                concepto_pago="Synthetic reimbursement",
            )
        )
    monkeypatch.setattr(service, "rebuild_outbox_message_text", pg.real_rebuild)
    transport = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    result = await _monitor(pg)
    assert result["recovered"] == 1
    transport.assert_awaited_once()
    message = transport.await_args.args[1]
    assert "TEST-FINANCE" in message
    assert "$5,232.00" in message
    assert "Synthetic Finance" in message
    assert "Synthetic reimbursement" in message
    async with pg.factory() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        assert entry.status == "sent" and entry.sent_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_finance_timestamps_are_utc_in_non_utc_database(
    pg, monkeypatch, confirmed
):
    seed = await _seed(pg)
    async with pg.factory.begin() as session:
        await session.delete(
            await session.get(models.TelegramNotificationOutbox, seed.entry)
        )
    entered, release = asyncio.Event(), asyncio.Event()

    async def transport(chat_id, message, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return confirmed

    monkeypatch.setattr(service, "send_telegram_message", transport)
    task = asyncio.create_task(_deliver(pg, seed))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        async with pg.factory() as session:
            assert await session.scalar(text("SHOW timezone")) == "Europe/Berlin"
            entry = await session.scalar(select(models.TelegramNotificationOutbox))
            now = datetime.now(timezone.utc)
            assert abs((entry.created_at - now).total_seconds()) < 5
            assert abs((entry.updated_at - now).total_seconds()) < 5
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=10)
    async with pg.factory() as session:
        entry = await session.scalar(select(models.TelegramNotificationOutbox))
        now = datetime.now(timezone.utc)
        assert abs((entry.updated_at - now).total_seconds()) < 5
        if confirmed:
            assert entry.status == "sent"
            assert abs((entry.sent_at - now).total_seconds()) < 5
        else:
            assert entry.status == "failed"
            retry_delay = (entry.next_retry_at - now).total_seconds()
            assert 7195 < retry_delay <= 7200


@pytest.mark.asyncio
@pytest.mark.parametrize("contender", ["monitor", "initial", "console", "retry"])
async def test_monitor_race_preserves_one_send_and_same_row(pg, monkeypatch, contender):
    seed = await _seed(pg)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def transport(chat_id, message, **kwargs):
        calls.append(chat_id)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return True

    monkeypatch.setattr(service, "send_telegram_message", transport)
    first = asyncio.create_task(_monitor(pg))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        if contender == "monitor":
            second = _monitor(pg)
        elif contender == "retry":
            monkeypatch.setattr(service, "OUTBOX_RETRY_DELAY_SECONDS", 0)
            second = service._execute_outbox_retry(seed.entry)
        else:
            second = _deliver(pg, seed, force=contender == "console")
        # A sender must omit the locked row, without waiting for the HTTP call.
        await asyncio.wait_for(second, timeout=3)
        assert calls == [900001]
    finally:
        release.set()
        result = await asyncio.wait_for(first, timeout=10)
    assert result["recovered"] == 1
    assert calls == [900001]
    async with pg.factory() as session:
        rows = (await session.scalars(select(models.TelegramNotificationOutbox))).all()
        assert len(rows) == 1
        assert rows[0].id == seed.entry
        assert rows[0].status == "sent" and rows[0].sent_at is not None


@pytest.mark.asyncio
async def test_stale_session_cannot_resend_after_other_session_commits(pg, monkeypatch):
    seed = await _seed(pg)
    transport = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    async with pg.factory() as stale_session:
        stale = await stale_session.get(models.TelegramNotificationOutbox, seed.entry)
        assert stale.status == "pending"
        result = await _monitor(pg)
        assert result["recovered"] == 1
        await service._send_outbox_entry(stale_session, stale)
    assert transport.await_count == 1
    async with pg.factory() as session:
        assert (
            await session.get(models.TelegramNotificationOutbox, seed.entry)
        ).status == "sent"


@pytest.mark.asyncio
async def test_simultaneous_initial_creation_uses_real_unique_index(pg, monkeypatch):
    seed = await _seed(pg)
    async with pg.factory.begin() as session:
        await session.delete(
            await session.get(models.TelegramNotificationOutbox, seed.entry)
        )
    original_create = service.create_outbox_entry
    both_entered = asyncio.Event()
    arrivals = 0

    async def simultaneous_create(*args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(service, "create_outbox_entry", simultaneous_create)
    transport = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    await asyncio.wait_for(
        asyncio.gather(_deliver(pg, seed), _deliver(pg, seed)), timeout=10
    )
    transport.assert_awaited_once()
    async with pg.factory() as session:
        rows = (await session.scalars(select(models.TelegramNotificationOutbox))).all()
        assert len(rows) == 1 and rows[0].status == "sent"


@pytest.mark.asyncio
async def test_shared_chat_concurrent_recipient_rows_do_not_duplicate(pg, monkeypatch):
    seed = await _seed(pg)
    other_employee, other_entry = uuid4(), uuid4()
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with pg.factory.begin() as session:
        session.add(
            models.Empleado(
                id=other_employee,
                nombre="Synthetic Shared Chat",
                rol="finanzas",
                activo=True,
                telegram_user_id=900001,
            )
        )
        await session.flush()
        session.add(
            models.TelegramNotificationOutbox(
                id=other_entry,
                documento_id=seed.document,
                recipient_empleado_id=other_employee,
                telegram_chat_id=900001,
                notification_type="finance_pending_payment",
                status="pending",
                header_text="Fixture",
                body_preview="Fixture",
                retry_count=0,
                created_at=old,
                updated_at=old,
            )
        )
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def transport(chat_id, message, **kwargs):
        calls.append(chat_id)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return True

    async def send(entry_id):
        async with pg.factory() as session:
            return await service._send_finance_outbox_entry(session, entry_id)

    monkeypatch.setattr(service, "send_telegram_message", transport)
    first = asyncio.create_task(send(seed.entry))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert await asyncio.wait_for(send(other_entry), timeout=3) == "busy"
        assert calls == [900001]
    finally:
        release.set()
        assert await asyncio.wait_for(first, timeout=10) == "sent"
    assert await send(other_entry) == "skipped"
    assert calls == [900001]
    async with pg.factory() as session:
        other = await session.get(models.TelegramNotificationOutbox, other_entry)
        assert other.status == "skipped" and other.error_message


@pytest.mark.asyncio
async def test_deferred_retry_race_consumes_exactly_one_retry(pg, monkeypatch):
    seed = await _seed(pg, status="failed")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def transport(chat_id, message, **kwargs):
        calls.append(chat_id)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=10)
        return True

    monkeypatch.setattr(service, "send_telegram_message", transport)
    monkeypatch.setattr(service, "OUTBOX_RETRY_DELAY_SECONDS", 0)
    first = asyncio.create_task(service._execute_outbox_retry(seed.entry))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        await asyncio.wait_for(service._execute_outbox_retry(seed.entry), timeout=3)
        assert calls == [900001]
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=10)
    async with pg.factory() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        assert entry.status == "sent" and entry.retry_count == 1
        assert entry.next_retry_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "paid",
        "effective_paid",
        "rejected",
        "payment_in_progress",
        "proof",
        "inactive",
        "role",
        "unlinked",
        "chat",
    ],
)
async def test_recovery_reads_current_eligibility_and_chat(pg, monkeypatch, change):
    seed = await _seed(pg)
    transport = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    async with pg.factory.begin() as session:
        if change == "paid":
            stmt = (
                update(models.Documento)
                .where(models.Documento.id == seed.document)
                .values(pagado_en=datetime.now(timezone.utc))
            )
        elif change == "effective_paid":
            stmt = (
                update(models.Documento)
                .where(models.Documento.id == seed.document)
                .values(fecha_pago_efectiva=date.today())
            )
        elif change in {"rejected", "payment_in_progress"}:
            stmt = (
                update(models.Documento)
                .where(models.Documento.id == seed.document)
                .values(
                    estado="rechazado" if change == "rejected" else "en_proceso_pago"
                )
            )
        elif change == "proof":
            session.add(
                models.Adjunto(
                    documento_id=seed.document,
                    categoria="comprobante_pago",
                    ruta_archivo="synthetic-proof.pdf",
                )
            )
            stmt = None
        else:
            values = {
                "inactive": {"activo": False},
                "role": {"rol": "empleado"},
                "unlinked": {"telegram_user_id": None},
                "chat": {"telegram_user_id": 900002},
            }[change]
            stmt = (
                update(models.Empleado)
                .where(models.Empleado.id == seed.employee)
                .values(**values)
            )
        if stmt is not None:
            await session.execute(stmt)
    result = await _monitor(pg)
    async with pg.factory() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        if change == "chat":
            assert result["recovered"] == 1 and entry.status == "sent"
            assert transport.await_args.args[0] == 900002
            assert entry.telegram_chat_id == 900002
        else:
            assert result["skipped"] == 1 and entry.status == "skipped"
            assert entry.error_message
            transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_deferred_retry_rechecks_after_document_lock_releases(
    pg, monkeypatch
):
    seed = await _seed(pg, status="failed")
    transport = AsyncMock(return_value=True)
    scheduled = []
    monkeypatch.setattr(service, "send_telegram_message", transport)
    monkeypatch.setattr(service, "schedule_outbox_retry", scheduled.append)
    monkeypatch.setattr(service, "OUTBOX_RETRY_DELAY_SECONDS", 0)
    async with pg.factory() as lock_session:
        await lock_session.execute(
            select(models.Documento.id)
            .where(models.Documento.id == seed.document)
            .with_for_update()
        )
        await asyncio.wait_for(service._execute_outbox_retry(seed.entry), timeout=3)
        transport.assert_not_awaited()
        assert scheduled == [seed.entry]
        async with pg.factory() as read_session:
            entry = await read_session.get(
                models.TelegramNotificationOutbox, seed.entry
            )
            assert entry.status == "failed" and entry.retry_count == 0
            assert entry.next_retry_at is not None
        await lock_session.rollback()
    await service._execute_outbox_retry(scheduled.pop())
    transport.assert_awaited_once()
    async with pg.factory() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        assert entry.status == "sent" and entry.retry_count == 1
        assert entry.next_retry_at is None


@pytest.mark.asyncio
async def test_recovery_refreshes_cached_document_and_related_people(pg, monkeypatch):
    seed = await _seed(pg)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id == seed.document)
            .values(
                monto_solicitado=100,
                concepto_pago="Previous concept",
                beneficiario_empleado_id=seed.employee,
            )
        )
    monkeypatch.setattr(service, "rebuild_outbox_message_text", pg.real_rebuild)
    transport = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    async with pg.factory() as recovery_session:
        cached = await service._load_documento_for_outbox(
            recovery_session, seed.document
        )
        assert cached.monto_solicitado == 100
        assert cached.empleado.nombre == "Synthetic Finance"
        await recovery_session.commit()
        new_beneficiary = uuid4()
        async with pg.factory.begin() as other_session:
            other_session.add(
                models.Empleado(
                    id=new_beneficiary,
                    nombre="Current beneficiary",
                    rol="empleado",
                )
            )
            await other_session.flush()
            await other_session.execute(
                update(models.Empleado)
                .where(models.Empleado.id == seed.employee)
                .values(nombre="Current requester")
            )
            await other_session.execute(
                update(models.Documento)
                .where(models.Documento.id == seed.document)
                .values(
                    monto_solicitado=5232,
                    concepto_pago="Current concept",
                    beneficiario_empleado_id=new_beneficiary,
                )
            )
        assert cached.monto_solicitado == 100
        result = await service.recover_stale_finance_pending_notifications(
            recovery_session
        )
        assert result["recovered"] == 1
    transport.assert_awaited_once()
    message = transport.await_args.args[1]
    assert "$5,232.00" in message and "Current concept" in message
    assert "Current beneficiary" in message and "Current requester" in message
    assert "Previous concept" not in message


@pytest.mark.asyncio
async def test_sql_batch_limit_boundary_recent_sent_and_created_fallback(
    pg, monkeypatch
):
    now = datetime.now(timezone.utc)
    seeds = [await _seed(pg) for _ in range(6)]
    ages = [20, 10, 6, 5, 1, 20]
    async with pg.factory.begin() as session:
        for index, (seed, age) in enumerate(zip(seeds, ages)):
            activity = now - timedelta(minutes=age)
            await session.execute(
                update(models.TelegramNotificationOutbox)
                .where(models.TelegramNotificationOutbox.id == seed.entry)
                .values(
                    created_at=activity,
                    updated_at=None if index == 0 else activity,
                    status="sent" if index == 5 else "pending",
                )
            )
    calls = []

    async def render(session, entry):
        return str(entry.id)

    async def transport(chat_id, message, **kwargs):
        calls.append(message)
        return True

    monkeypatch.setattr(service, "rebuild_outbox_message_text", render)
    monkeypatch.setattr(service, "send_telegram_message", transport)
    async with pg.factory() as session:
        first = await service.recover_stale_finance_pending_notifications(
            session, limit=2, now=now
        )
        second = await service.recover_stale_finance_pending_notifications(
            session, limit=500, now=now
        )
    assert first["reviewed"] == first["recovered"] == 2
    assert second["reviewed"] == second["recovered"] == 1
    assert calls == [str(seed.entry) for seed in seeds[:3]]
    async with pg.factory() as session:
        for seed in seeds[3:5]:
            assert (
                await session.get(models.TelegramNotificationOutbox, seed.entry)
            ).status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_confirmed", [True, False])
async def test_monitor_restart_recovers_due_failure_once_without_sleep_task(
    pg, monkeypatch, retry_confirmed
):
    seed = await _seed(pg)
    future = await _seed(pg, status="failed")
    now = datetime.now(timezone.utc)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.TelegramNotificationOutbox)
            .where(models.TelegramNotificationOutbox.id == future.entry)
            .values(next_retry_at=now + timedelta(hours=3))
        )
    transport = AsyncMock(side_effect=[False, retry_confirmed])
    monkeypatch.setattr(service, "send_telegram_message", transport)
    # Fixture suppresses the sleeping callback, like a one-shot process exit.
    first = await _monitor(pg)
    assert first["failed"] == 1
    assert transport.await_count == 1
    async with pg.factory.begin() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        assert entry.status == "failed" and entry.retry_count == 0
        assert 7190 < (entry.next_retry_at - now).total_seconds() < 7210
        entry.next_retry_at = now - timedelta(minutes=1)
    second = await _monitor(pg)
    assert second["recovered" if retry_confirmed else "failed"] == 1
    assert transport.await_count == 2
    async with pg.factory() as session:
        entry = await session.get(models.TelegramNotificationOutbox, seed.entry)
        assert entry.status == ("sent" if retry_confirmed else "failed")
        assert entry.retry_count == 1 and entry.next_retry_at is None
        future_entry = await session.get(
            models.TelegramNotificationOutbox, future.entry
        )
        assert future_entry.status == "failed" and future_entry.retry_count == 0
        assert future_entry.next_retry_at == now + timedelta(hours=3)
    third = await _monitor(pg)
    assert third["reviewed"] == 0
    assert transport.await_count == 2
