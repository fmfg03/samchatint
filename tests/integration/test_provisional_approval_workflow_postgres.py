"""Actual workflow/lock/event acceptance on disposable Unix-socket PostgreSQL.

Core workflow, state/authorization queries, row locks and approval writes are real.
Accounting posting results and outbound audit/Telegram adapters are isolated here;
their financial-source internals remain covered by their own acceptance suites.
"""

import asyncio
import importlib.util
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKeyConstraint, MetaData, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.services import authorization_profile_service as authorization
from devnous.gastos.services import documento_telegram
from devnous.gastos.services import documento_workflow_service as workflow
from devnous.gastos.services import project_authorization_service as policy

_fixture_path = Path(__file__).with_name("test_telegram_finance_recovery_postgres.py")
_fixture_spec = importlib.util.spec_from_file_location(
    "provisional_workflow_native_fixture", _fixture_path
)
_native = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_native)
isolated_postgres_url = _native.isolated_postgres_url


@pytest_asyncio.fixture
async def pg_workflow(isolated_postgres_url, monkeypatch):
    schema = "approval_workflow_" + uuid4().hex
    admin = create_async_engine(isolated_postgres_url, hide_parameters=True)
    async with admin.begin() as connection:
        assert await connection.scalar(text("SHOW listen_addresses")) == ""
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        isolated_postgres_url,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    copied = MetaData()
    # Full columns for actual ORM loads. Legacy foreign keys to unrelated domains
    # are omitted solely in this disposable fixture; the transition is not mocked.
    for model in (
        models.Empleado,
        models.Tournament,
        models.Documento,
        models.ExpenseReport,
        models.Aprobacion,
        models.Anticipo,
        models.Reembolso,
        models.CuentaDeGastos,
    ):
        clone = model.__table__.to_metadata(copied)
        for constraint in list(clone.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                clone.constraints.remove(constraint)
                for element in constraint.elements:
                    clone.foreign_keys.discard(element)
                    element.parent.foreign_keys.discard(element)
    async with engine.begin() as connection:
        await connection.run_sync(copied.create_all)
        # Exact serving constraints: technical audit must use an existing carrier.
        await connection.execute(
            text(
                "ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_accion_check CHECK ((accion)::text = ANY (ARRAY['enviar'::text, 'enviar_control_presupuestal'::text, 'rechazar_control_presupuestal'::text, 'asignar_partida_presupuestal'::text, 'asignar_partida_presupuestal_linea'::text, 'asignar_partidas_presupuestales'::text, 'reversar_a_control_presupuestal'::text, 'aprobar'::text, 'rechazar'::text, 'cancelar'::text, 'editar'::text, 'pagar'::text, 'aprobar_area'::text, 'rechazar_area'::text, 'aprobar_final'::text, 'rechazar_final'::text, 'retirar'::text, 'adjuntar_comprobante_no_deducible'::text, 'reemplazar_comprobante_no_deducible'::text, 'eliminar_comprobante_no_deducible'::text, 'confirmar_cfdi_compartido'::text, 'regularizar_aprobacion_reembolso'::text, 'liberar_cfdi_duplicado'::text, 'adjuntar_soporte'::text, 'adjuntar_factura'::text]))"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_tipo_entidad_check CHECK ((tipo_entidad)::text = ANY (ARRAY['documento'::text, 'gasto'::text, 'beneficiary_onboarding'::text, 'budget_cfdi_income_link'::text]))"
            )
        )
        for sql in (
            "CREATE TABLE authorization_positions("
            "position_key text PRIMARY KEY,active boolean)",
            "CREATE TABLE authorization_position_assignments("
            "position_key text,empleado_id uuid,active boolean)",
            "CREATE TABLE project_authorization_rules("
            "tournament_id uuid PRIMARY KEY,eligible_position_keys jsonb,"
            "requires_operations_reference boolean,active boolean)",
            "CREATE TABLE documento_authorization_routes("
            "documento_id uuid PRIMARY KEY,eligible_position_keys jsonb,"
            "eligible_empleado_ids jsonb,requires_operations_reference boolean,"
            "source varchar(100),resolved_at timestamptz DEFAULT now())",
        ):
            await connection.execute(text(sql))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gn, gv, operations, requester, beneficiary, project = [uuid4() for _ in range(6)]
    async with factory.begin() as session:
        session.add_all(
            models.Empleado(id=actor, nombre=label, rol="admin", activo=True)
            for actor, label in (
                (gn, "GN"),
                (gv, "GV"),
                (operations, "Operations"),
                (requester, "Requester"),
                (beneficiary, "Beneficiary"),
            )
        )
        session.add(models.Tournament(id=project, name="Homeless World Cup México"))
        for key, members in {
            policy.PROVISIONAL_GN: [gn],
            policy.PROVISIONAL_SUPERVISOR: [gv],
            policy.PROVISIONAL_EXCEPTION: [operations, gv],
            policy.DIRECTOR_OPERACIONES: [operations],
        }.items():
            await session.execute(
                text("INSERT INTO authorization_positions VALUES(:key,TRUE)"),
                {"key": key},
            )
            for actor in members:
                await session.execute(
                    text(
                        "INSERT INTO authorization_position_assignments "
                        "VALUES(:key,:actor,TRUE)"
                    ),
                    {"key": key, "actor": actor},
                )
        await session.execute(
            text(
                "INSERT INTO project_authorization_rules "
                "VALUES(:id,CAST(:keys AS jsonb),TRUE,TRUE)"
            ),
            {
                "id": project,
                "keys": json.dumps(
                    [policy.DIRECTOR_OPERACIONES, policy.PROVISIONAL_GN]
                ),
            },
        )
    monkeypatch.setenv("SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT", "true")
    monkeypatch.setattr(
        authorization,
        "build_authorization_route_soft_warning",
        AsyncMock(return_value=None),
    )
    for name in (
        "ensure_provider_approval_posting",
        "ensure_amex_report_approval_posting",
        "ensure_debtor_comprobacion_posting_for_informe",
    ):
        monkeypatch.setattr(
            workflow, name, AsyncMock(return_value=SimpleNamespace(status="skipped"))
        )
    monkeypatch.setattr(workflow, "record_customer_success_audit_event", AsyncMock())
    monkeypatch.setattr(
        documento_telegram,
        "schedule_document_workflow_telegram_notifications",
        lambda **kwargs: None,
    )
    try:
        yield SimpleNamespace(
            factory=factory,
            gn=gn,
            gv=gv,
            operations=operations,
            requester=requester,
            beneficiary=beneficiary,
            project=project,
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed_document(pg, *, kind="SOLICITUD", state="enviado", expenses=True):
    document_id = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.Documento(
                id=document_id,
                empleado_id=pg.requester,
                beneficiario_empleado_id=pg.beneficiary,
                torneo_id=pg.project,
                tipo=kind,
                estado=state,
                numero_referencia="ACCEPTANCE-" + document_id.hex,
                referencia_operaciones="363",
                monto_total=Decimal("250.00"),
            )
        )
        if kind == "INFORME" and expenses:
            session.add(
                models.ExpenseReport(
                    id=uuid4(),
                    empleado_id=pg.requester,
                    proyecto="Homeless World Cup México",
                    concepto="Synthetic expense",
                    gasto_cantidad=250,
                    informe_documento_id=document_id,
                    estado_gasto="activo",
                    pagado_con_amex_empresa=False,
                )
            )
    return document_id


async def decide(pg, document_id, actor, action="approve"):
    async with pg.factory() as session:
        return await workflow.transition_documento_workflow(
            session,
            documento_id=document_id,
            actor_id=actor,
            action=action,
            comentario="Acceptance decision",
        )


async def assert_final(pg, document_id, state, action, actor):
    async with pg.factory() as session:
        document = await session.get(models.Documento, document_id)
        events = (
            await session.scalars(
                select(models.Aprobacion).where(
                    models.Aprobacion.entidad_id == document_id
                )
            )
        ).all()
        assert document.estado == state
        assert document.monto_total == Decimal("250.00")
        assert document.referencia_operaciones == "363"
        assert len(events) == 1
        assert events[0].accion == action
        assert events[0].aprobador_id == actor


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
@pytest.mark.parametrize("first_actor_key", ["operations", "gn"])
async def test_first_of_two_approvers_serializes_real_workflow_once(
    pg_workflow, monkeypatch, kind, first_actor_key
):
    pg = pg_workflow
    document_id = await seed_document(pg, kind=kind)
    first_holds_row, release_first = asyncio.Event(), asyncio.Event()
    second_pid = asyncio.Future()

    async def hold_first_accounting_result(*args, **kwargs):
        first_holds_row.set()
        await release_first.wait()
        return SimpleNamespace(status="skipped")

    monkeypatch.setattr(
        workflow,
        (
            "ensure_provider_approval_posting"
            if kind == "SOLICITUD"
            else "ensure_amex_report_approval_posting"
        ),
        hold_first_accounting_result,
    )

    async def second_approval():
        async with pg.factory() as session:
            second_pid.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
            with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
                await workflow.transition_documento_workflow(
                    session,
                    documento_id=document_id,
                    actor_id=(
                        pg.gn if first_actor_key == "operations" else pg.operations
                    ),
                    action="approve",
                )
            assert exc.value.code == "invalid_estado"

    async def observe_wait(pid):
        async with pg.factory() as session:
            while not await session.scalar(
                text(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                ),
                {"pid": pid},
            ):
                await asyncio.sleep(0)

    winning_actor = getattr(pg, first_actor_key)
    first = asyncio.create_task(decide(pg, document_id, winning_actor))
    second = None
    try:
        await asyncio.wait_for(first_holds_row.wait(), 5)
        second = asyncio.create_task(second_approval())
        await asyncio.wait_for(observe_wait(await second_pid), 5)
        assert not second.done()
    finally:
        release_first.set()
        await asyncio.wait_for(first, 10)
        if second is not None:
            await asyncio.wait_for(second, 10)
    await assert_final(pg, document_id, "aprobado", "aprobar", winning_actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_configured_gv_decides_both_document_types_outside_assigned_route(
    pg_workflow, kind, action
):
    pg = pg_workflow
    document_id = await seed_document(pg, kind=kind)
    await decide(pg, document_id, pg.gv, action)
    await assert_final(
        pg,
        document_id,
        "aprobado" if action == "approve" else "rechazado",
        "aprobar" if action == "approve" else "rechazar",
        pg.gv,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["borrador", "aprobado", "pagado"])
async def test_gv_cannot_approve_document_in_nonpending_state(pg_workflow, state):
    pg = pg_workflow
    document_id = await seed_document(pg, state=state)
    with pytest.raises(workflow.DocumentoWorkflowValidationError):
        await decide(pg, document_id, pg.gv)
    async with pg.factory() as session:
        assert (await session.get(models.Documento, document_id)).estado == state
        assert (
            await session.scalar(select(func.count()).select_from(models.Aprobacion))
            == 0
        )


@pytest.mark.asyncio
async def test_gv_does_not_bypass_actual_active_expense_requirement(pg_workflow):
    pg = pg_workflow
    document_id = await seed_document(pg, kind="INFORME", expenses=False)
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await decide(pg, document_id, pg.gv)
    assert exc.value.code == "no_gastos"


@pytest.mark.asyncio
async def test_inactive_gv_has_no_supervisory_decision_authority(pg_workflow):
    pg = pg_workflow
    document_id = await seed_document(pg)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.Empleado)
            .where(models.Empleado.id == pg.gv)
            .values(activo=False)
        )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await decide(pg, document_id, pg.gv)
    assert exc.value.code == "not_route_approver"


@pytest.mark.asyncio
async def test_another_admin_cannot_decide_outside_project_route(pg_workflow):
    pg = pg_workflow
    document_id = await seed_document(pg)
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await decide(pg, document_id, pg.requester)
    assert exc.value.code == "not_route_approver"


@pytest.mark.asyncio
async def test_gv_report_rejection_reopens_real_expense_account(pg_workflow):
    pg = pg_workflow
    document_id = await seed_document(pg, kind="INFORME")
    account_id = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.CuentaDeGastos(
                id=account_id,
                empleado_id=pg.requester,
                referencia_base="SYNTHETIC-ACCOUNT",
                torneo_id=pg.project,
                estado="cerrada",
            )
        )
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id == document_id)
            .values(cuenta_gastos_id=account_id)
        )
    await decide(pg, document_id, pg.gv, "reject")
    await assert_final(pg, document_id, "borrador", "rechazar", pg.gv)
    async with pg.factory() as session:
        account = await session.get(models.CuentaDeGastos, account_id)
        assert account.estado == "abierta" and account.closed_at is None


@pytest.mark.asyncio
async def test_gv_cannot_approve_reimbursement_without_approved_report(pg_workflow):
    pg = pg_workflow
    document_id = await seed_document(pg)
    report_id = await seed_document(pg, kind="INFORME", state="borrador")
    account_id = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.CuentaDeGastos(
                id=account_id,
                empleado_id=pg.requester,
                referencia_base="SYNTHETIC-REIMBURSEMENT",
                torneo_id=pg.project,
                estado="abierta",
            )
        )
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id.in_([document_id, report_id]))
            .values(cuenta_gastos_id=account_id)
        )
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id == document_id)
            .values(concepto_pago=workflow.EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX)
        )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await decide(pg, document_id, pg.gv)
    assert exc.value.code == "informe_approval_required"


@pytest.mark.asyncio
async def test_gv_does_not_bypass_pending_amex_posting_result(pg_workflow, monkeypatch):
    pg = pg_workflow
    document_id = await seed_document(pg, kind="INFORME")
    monkeypatch.setattr(
        workflow,
        "ensure_amex_report_approval_posting",
        AsyncMock(
            return_value=SimpleNamespace(status="pending", reason="test mapping")
        ),
    )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await decide(pg, document_id, pg.gv)
    assert exc.value.code == "accounting_posting_pending"
    async with pg.factory() as session:
        assert (await session.get(models.Documento, document_id)).estado == "enviado"
        assert (
            await session.scalar(select(func.count()).select_from(models.Aprobacion))
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("change", ["rule", "membership"])
async def test_changed_route_is_audited_by_real_decider_and_denied_actor_leaves_no_writes(
    pg_workflow, action, change
):
    pg = pg_workflow
    document_id = await seed_document(pg)
    async with pg.factory.begin() as session:
        doc = await session.get(models.Documento, document_id)
        await policy.resolve_and_snapshot_document_route(
            session, doc, audit_actor_id=pg.requester
        )
        original_source = (
            await session.execute(
                text(
                    "SELECT source FROM documento_authorization_routes WHERE documento_id=:id"
                ),
                {"id": document_id},
            )
        ).scalar_one()
        if change == "rule":
            await session.execute(
                text(
                    "UPDATE project_authorization_rules SET eligible_position_keys=CAST(:keys AS jsonb)"
                ),
                {"keys": json.dumps([policy.PROVISIONAL_GN])},
            )
        else:
            await session.execute(
                text(
                    "UPDATE authorization_position_assignments SET active=FALSE WHERE position_key=:key"
                ),
                {"key": policy.DIRECTOR_OPERACIONES},
            )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as error:
        await decide(pg, document_id, pg.operations, action)
    assert error.value.code == "not_route_approver"
    async with pg.factory() as session:
        assert (
            await session.execute(
                text(
                    "SELECT source FROM documento_authorization_routes WHERE documento_id=:id"
                ),
                {"id": document_id},
            )
        ).scalar_one() == original_source
        assert not (
            await session.scalars(
                select(models.Aprobacion).where(
                    models.Aprobacion.entidad_id == document_id
                )
            )
        ).all()
    await decide(pg, document_id, pg.gn, action)
    async with pg.factory() as session:
        events = (
            await session.scalars(
                select(models.Aprobacion).where(
                    models.Aprobacion.entidad_id == document_id
                )
            )
        ).all()
        assert sorted(event.accion for event in events) == sorted(
            [
                "editar",
                "aprobar" if action == "approve" else "rechazar",
            ]
        )
        audit = next(event for event in events if event.accion == "editar")
        assert audit.aprobador_id == pg.gn
        payload = json.loads(audit.comentario)
        assert payload["action"] == "actualizar_ruta_autorizacion"
        assert payload["before"]["source"] == original_source
        assert payload["after"]["eligible_position_keys"] == (
            [policy.PROVISIONAL_GN]
            if change == "rule"
            else [policy.DIRECTOR_OPERACIONES, policy.PROVISIONAL_GN]
        )
        assert payload["after"]["eligible_empleado_ids"] == [str(pg.gn)]
        current_source = (
            await session.execute(
                text(
                    "SELECT source FROM documento_authorization_routes WHERE documento_id=:id"
                ),
                {"id": document_id},
            )
        ).scalar_one()
        assert (
            current_source != original_source
            and payload["after"]["source"] == current_source
        )
        assert (
            await session.get(models.Documento, document_id)
        ).referencia_operaciones == "363"


@pytest.mark.asyncio
async def test_telegram_superadmin_prioritizes_assigned_before_thirty_no_folio_observers(
    pg_workflow,
):
    from datetime import datetime, timezone

    pg = pg_workflow
    assigned_id = await seed_document(pg)
    other_project = uuid4()
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.operations)
        actor.rol = "superadmin"
        session.add(models.Tournament(id=other_project, name="Synthetic GN-only"))
        await session.execute(
            text(
                "INSERT INTO project_authorization_rules VALUES(:id,CAST(:keys AS jsonb),FALSE,TRUE)"
            ),
            {"id": other_project, "keys": json.dumps([policy.PROVISIONAL_GN])},
        )
        assigned = await session.get(models.Documento, assigned_id)
        assigned.enviado_en = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(31):
            session.add(
                models.Documento(
                    id=uuid4(),
                    empleado_id=pg.requester,
                    torneo_id=other_project,
                    tipo="SOLICITUD",
                    estado="enviado",
                    numero_referencia=f"OBSERVER-{index}",
                    referencia_operaciones=None,
                    enviado_en=datetime(2026, 10, 1, tzinfo=timezone.utc),
                )
            )
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.operations)
        rows = await documento_telegram.query_pending_documentos_for_approver(
            session, actor
        )
        assert len(rows) == 30
        assert rows[0].id == assigned_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "excluded_name", ["Gestión de Patrocinios", "Gestión RRSS y Transmisiones"]
)
async def test_send_draft_changed_from_excluded_project_uses_creator_folio_policy(
    pg_workflow, monkeypatch, excluded_name
):
    from devnous.gastos.services import documento_service

    pg = pg_workflow
    doc_id = await seed_document(pg, state="borrador")
    excluded = uuid4()
    allocator = AsyncMock(return_value="364")
    monkeypatch.setattr(
        documento_service, "allocate_next_referencia_operaciones", allocator
    )
    async with pg.factory.begin() as session:
        requester = await session.get(models.Empleado, pg.requester)
        requester.departamento = "Operaciones"
        session.add(models.Tournament(id=excluded, name=excluded_name))
        await session.execute(
            text(
                "INSERT INTO project_authorization_rules VALUES(:id,CAST(:keys AS jsonb),FALSE,TRUE)"
            ),
            {"id": excluded, "keys": json.dumps([policy.PROVISIONAL_GN])},
        )
        await session.execute(
            text(
                "UPDATE project_authorization_rules SET requires_operations_reference=FALSE"
            )
        )
        doc = await session.get(models.Documento, doc_id)
        doc.torneo_id = excluded
        doc.referencia_operaciones = None
        await policy.prepare_document_authorization_route(
            session, doc, audit_actor_id=pg.requester
        )
        assert doc.referencia_operaciones is None
        allocator.assert_not_awaited()
        doc.torneo_id = pg.project
    async with pg.factory() as session:
        await workflow.transition_documento_workflow(
            session, documento_id=doc_id, actor_id=pg.requester, action="send"
        )
    allocator.assert_awaited_once()
    async with pg.factory() as session:
        doc = await session.get(models.Documento, doc_id)
        assert doc.estado == workflow.BUDGET_CONTROL_STATE
        assert doc.referencia_operaciones == "364"
        audits = (
            await session.scalars(
                select(models.Aprobacion).where(
                    models.Aprobacion.entidad_id == doc_id,
                    models.Aprobacion.accion == "editar",
                )
            )
        ).all()
        assert len(audits) == 1 and audits[0].aprobador_id == pg.requester


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [("accion", "actualizar_ruta_autorizacion"), ("tipo_entidad", "invented_entity")],
)
async def test_serving_audit_checks_reject_unapproved_enum_values(
    pg_workflow, field, value
):
    from sqlalchemy.exc import IntegrityError

    pg = pg_workflow
    row = dict(
        tipo_entidad="documento",
        entidad_id=uuid4(),
        aprobador_id=pg.gn,
        accion="editar",
    )
    row[field] = value
    with pytest.raises(IntegrityError) as error:
        async with pg.factory.begin() as session:
            session.add(models.Aprobacion(**row))
            await session.flush()
    assert (
        "aprobaciones_accion_check"
        if field == "accion"
        else "aprobaciones_tipo_entidad_check"
    ) in str(error.value)


@pytest.mark.asyncio
async def test_typed_edit_route_audit_does_not_approve_or_notify(
    pg_workflow, monkeypatch
):
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        pg = pg_workflow
        document_id = await seed_document(pg)
        notify = AsyncMock()
        monkeypatch.setattr(
            documento_telegram,
            "schedule_document_workflow_telegram_notifications",
            notify,
        )
        async with pg.factory.begin() as session:
            database_before = await session.scalar(text("SELECT clock_timestamp()"))
            doc = await session.get(models.Documento, document_id)
            await policy.resolve_and_snapshot_document_route(
                session, doc, audit_actor_id=pg.requester
            )
            await session.execute(
                text(
                    "UPDATE project_authorization_rules SET eligible_position_keys=CAST(:keys AS jsonb)"
                ),
                {"keys": json.dumps([policy.PROVISIONAL_GN])},
            )
            await policy.resolve_and_snapshot_document_route(
                session,
                doc,
                audit_actor_id=pg.gn,
                audit_context={"plan_sha256": "a" * 64},
            )
        async with pg.factory() as session:
            doc = await session.get(models.Documento, document_id)
            rows = (
                await session.scalars(
                    select(models.Aprobacion).where(
                        models.Aprobacion.entidad_id == document_id
                    )
                )
            ).all()
            assert len(rows) == 1 and rows[0].accion == "editar"
            assert rows[0].aprobador_id == pg.gn
            database_after = await session.scalar(text("SELECT clock_timestamp()"))
            assert database_before <= rows[0].fecha <= database_after
            payload = json.loads(rows[0].comentario)
            assert payload["action"] == "actualizar_ruta_autorizacion"
            assert payload["audit_context"]["plan_sha256"] == "a" * 64
            assert doc.estado == "enviado" and doc.aprobado_en is None
            assert doc.referencia_operaciones == "363"
            assert not await workflow.documento_has_approval(session, doc)
        notify.assert_not_called()
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()
