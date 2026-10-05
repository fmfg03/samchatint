from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.services import documento_telegram as telegram
from devnous.gastos.services import documento_workflow_service as workflow
from devnous.gastos.services import project_authorization_service as routing


@pytest.mark.parametrize(
    "reference,expected", [(None, False), (" ", False), ("190", True), (" 1 ", True)]
)
def test_reference_policy(reference, expected):
    assert (
        routing.has_operations_reference(
            SimpleNamespace(referencia_operaciones=reference)
        )
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["aprobado", "pagado", "cancelado", "en_proceso_pago"]
)
async def test_prepare_never_rewrites_terminal_documents(state):
    session = SimpleNamespace(execute=AsyncMock())
    assert (
        await routing.prepare_document_authorization_route(
            session, SimpleNamespace(estado=state)
        )
        is None
    )
    session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference,route_positions,requires,expected_writes",
    [
        ("177", ("director_operaciones",), True, 0),
        ("177", ("direccion_general",), False, 1),
        ("177", (), False, 1),
        (None, ("direccion_general",), False, 0),
    ],
)
async def test_prepare_reference_overrides_personal_exception_without_reallocating(
    monkeypatch, reference, route_positions, requires, expected_writes
):
    doc = SimpleNamespace(
        id=uuid4(), estado="enviado", referencia_operaciones=reference
    )
    route = (
        routing.ProjectAuthorizationRoute(route_positions, requires, "snapshot")
        if route_positions
        else None
    )
    monkeypatch.setattr(
        routing, "resolve_and_snapshot_document_route", AsyncMock(return_value=route)
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [uuid4()]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    actual = await routing.prepare_document_authorization_route(session, doc)
    assert session.execute.call_count == expected_writes * 2
    if expected_writes:
        assert actual.eligible_position_keys == ("director_operaciones",)
        assert "ON CONFLICT" in str(session.execute.call_args.args[0])
    else:
        assert actual == route
    assert doc.referencia_operaciones == reference


@pytest.mark.asyncio
async def test_prepare_allocates_once_for_configured_operations_route(monkeypatch):
    from devnous.gastos.services import documento_service

    doc = SimpleNamespace(id=uuid4(), estado="borrador", referencia_operaciones=None)
    route = routing.ProjectAuthorizationRoute(
        ("director_operaciones",), True, "project"
    )
    monkeypatch.setattr(
        routing, "resolve_and_snapshot_document_route", AsyncMock(return_value=route)
    )
    allocate = AsyncMock(return_value="300")
    monkeypatch.setattr(
        documento_service, "allocate_next_referencia_operaciones", allocate
    )
    session = SimpleNamespace(execute=AsyncMock())
    await routing.prepare_document_authorization_route(session, doc)
    await routing.prepare_document_authorization_route(session, doc)
    assert doc.referencia_operaciones == "300"
    allocate.assert_awaited_once_with(session)
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "alias,param", [("documentos;DROP", "actor"), ("documentos", "actor;bad")]
)
def test_queue_policy_rejects_untrusted_identifiers(alias, param):
    with pytest.raises(ValueError):
        routing.document_route_approver_sql(alias, param)


def test_queue_sql_has_closed_operations_branch_and_active_fallback():
    sql = routing.document_route_approver_sql()
    assert "NULLIF(BTRIM(documentos.referencia_operaciones), '') IS NOT NULL" in sql
    assert "eligible_position_keys = '[\"director_operaciones\"]'::jsonb" in sql
    assert "route.requires_operations_reference = TRUE" in sql
    assert "p.active = TRUE" in sql and "operations_holder.activo = TRUE" in sql
    assert "active_actor.activo = TRUE" in sql
    assert "aprobador_id" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("role", ["empleado", "finanzas", "admin", "superadmin"])
async def test_telegram_folio_requires_operations_authority_even_admin(
    monkeypatch, allowed, role
):
    checker = AsyncMock(return_value=allowed)
    monkeypatch.setattr(routing, "actor_is_route_approver", checker)
    actor = SimpleNamespace(id=uuid4(), rol=role)
    doc = SimpleNamespace(id=uuid4(), estado="enviado", referencia_operaciones="177")
    assert (
        await telegram.approver_can_see_document_in_queue_live(None, actor, doc)
        is allowed
    )
    checker.assert_awaited_once()
    assert not telegram.approver_can_see_document_in_queue(actor, doc)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("role", ["empleado", "finanzas", "admin", "superadmin"])
async def test_folio_action_cannot_fall_through_when_legacy_snapshot_missing(
    monkeypatch, action, role
):
    actor = SimpleNamespace(id=uuid4(), rol=role)
    doc = SimpleNamespace(
        id=uuid4(),
        estado="enviado",
        referencia_operaciones="177",
        tipo="SOLICITUD",
        cuenta_gastos_id=None,
    )
    for name, value in [
        ("_load_documento", doc),
        ("_load_actor", actor),
        ("documento_financial_terminal_reason", None),
        ("documento_has_approval", False),
        ("_document_has_recorded_approval", False),
        ("validate_informe_surplus_before_submission", None),
    ]:
        monkeypatch.setattr(workflow, name, AsyncMock(return_value=value))
    checker = AsyncMock(return_value=False)
    monkeypatch.setattr(workflow, "actor_is_route_approver", checker)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(
        execute=AsyncMock(return_value=result), commit=AsyncMock()
    )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as error:
        await workflow.transition_documento_workflow(
            session, documento_id=doc.id, actor_id=actor.id, action=action
        )
    assert error.value.code == "not_route_approver"
    assert doc.estado == "enviado"
    session.commit.assert_not_called()
    checker.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_reimbursement_inherits_only_operations_authorized_report_actor(
    monkeypatch, allowed
):
    doc = SimpleNamespace(id=uuid4(), estado="enviado", referencia_operaciones="190")
    informe = SimpleNamespace(estado="aprobado", budget_concept_id=uuid4())
    approver = uuid4()
    monkeypatch.setattr(
        workflow,
        "_sync_reimbursement_budget_from_informe",
        AsyncMock(return_value=informe),
    )
    monkeypatch.setattr(
        workflow, "_linked_informe_approval_actor_id", AsyncMock(return_value=approver)
    )
    monkeypatch.setattr(workflow, "prepare_document_authorization_route", AsyncMock())
    monkeypatch.setattr(
        workflow, "actor_is_route_approver", AsyncMock(return_value=allowed)
    )
    approved = object()
    auto = MagicMock(return_value=approved)
    monkeypatch.setattr(workflow, "_auto_approve_solicitud_with_approved_informe", auto)
    session = SimpleNamespace(add=MagicMock())
    result = await workflow.approve_reimbursement_solicitud_for_approved_informe(
        session, doc
    )
    assert result is (approved if allowed else None)
    assert auto.call_count == int(allowed)
    assert session.add.call_count == int(allowed)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["pagado_en", "estado_pago"])
async def test_prepare_preserves_financially_paid_nonterminal_legacy_document(marker):
    doc = SimpleNamespace(estado="enviado", **{marker: "pagado"})
    session = SimpleNamespace(execute=AsyncMock())
    assert await routing.prepare_document_authorization_route(session, doc) is None
    session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_operations_holder_can_act_on_missing_legacy_route(monkeypatch, action):
    from devnous.gastos.services import authorization_profile_service as profiles

    actor = SimpleNamespace(id=uuid4(), rol="empleado", nombre="Configured Ops holder")
    doc = SimpleNamespace(
        id=uuid4(),
        empleado_id=uuid4(),
        numero_referencia="S-test",
        estado="enviado",
        referencia_operaciones="177",
        tipo="SOLICITUD",
        cuenta_gastos_id=None,
    )
    for name, value in [
        ("_load_documento", doc),
        ("_load_actor", actor),
        ("documento_financial_terminal_reason", None),
        ("documento_has_approval", False),
        ("_document_has_recorded_approval", False),
        ("validate_informe_surplus_before_submission", None),
        ("_reopen_informe_de_gastos_on_reject", None),
        ("record_customer_success_audit_event", None),
        ("ensure_provider_approval_posting", SimpleNamespace(status="not_applicable")),
    ]:
        monkeypatch.setattr(workflow, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        workflow, "actor_is_route_approver", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        workflow, "assign_fecha_pago_on_solicitud_approval", MagicMock()
    )
    monkeypatch.setattr(
        profiles, "build_authorization_route_hard_block", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        profiles, "build_authorization_route_soft_warning", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        telegram, "schedule_document_workflow_telegram_notifications", MagicMock()
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        add=MagicMock(),
    )
    actual = await workflow.transition_documento_workflow(
        session, documento_id=doc.id, actor_id=actor.id, action=action
    )
    assert actual.documento.estado == (
        "aprobado" if action == "approve" else "rechazado"
    )
    assert actual.aprobacion.aprobador_id == actor.id
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_operations_recipients_never_notify_personal_approver(
    monkeypatch,
):
    monkeypatch.setattr(
        routing, "route_approvers_for_document", AsyncMock(return_value=[])
    )
    doc = SimpleNamespace(id=uuid4(), referencia_operaciones="177")
    session = SimpleNamespace(get=AsyncMock())
    assert (
        await telegram.resolve_workflow_approval_notification_recipients(session, doc)
        == []
    )
    session.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("classified", [False, True])
@pytest.mark.parametrize("existing_reference", [None, "190"])
async def test_explicit_send_prepares_folio_before_budget_lane_without_reallocating(
    monkeypatch, classified, existing_reference
):
    from devnous.gastos.services import authorization_profile_service as profiles

    actor = SimpleNamespace(id=uuid4(), rol="empleado", nombre="Tournament requester")
    doc = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor.id,
        numero_referencia="S-test",
        estado="borrador",
        referencia_operaciones=existing_reference,
        tipo="SOLICITUD",
        cuenta_gastos_id=None,
        budget_concept_id=uuid4() if classified else None,
    )
    for name, value in [
        ("_load_documento", doc),
        ("_load_actor", actor),
        ("documento_financial_terminal_reason", None),
        ("documento_has_approval", False),
        ("_sync_reimbursement_budget_or_raise", None),
        ("reserve_documento_cfdis_or_raise", None),
        ("_linked_informe_approval_actor_id", None),
        ("record_customer_success_audit_event", None),
    ]:
        monkeypatch.setattr(workflow, name, AsyncMock(return_value=value))

    async def prepare(session, documento):
        assert documento.estado == "borrador"
        if not documento.referencia_operaciones:
            documento.referencia_operaciones = "300"

    prepared = AsyncMock(side_effect=prepare)
    invalidate = AsyncMock()
    monkeypatch.setattr(workflow, "prepare_document_authorization_route", prepared)
    monkeypatch.setattr(workflow, "invalidate_document_route", invalidate)
    monkeypatch.setattr(
        profiles, "build_document_authorization_evidence", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        telegram, "schedule_document_workflow_telegram_notifications", MagicMock()
    )
    monkeypatch.setattr(
        telegram, "schedule_budget_control_telegram_notifications", MagicMock()
    )
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), add=MagicMock())
    await workflow.transition_documento_workflow(
        session, documento_id=doc.id, actor_id=actor.id, action="send"
    )
    assert doc.estado == ("enviado" if classified else "control_presupuestal")
    assert doc.referencia_operaciones == (existing_reference or "300")
    assert invalidate.call_count == int(existing_reference is None)
    prepared.assert_awaited_once_with(session, doc)
    session.commit.assert_awaited_once()


def test_recipient_sql_uses_distinct_operations_holder_alias():
    sql = routing.document_route_approver_sql().replace(
        ":route_employee_id", "e.id::text"
    )
    assert "JOIN empleados operations_holder" in sql
    assert "a.empleado_id::text = e.id::text" in sql
    assert "JOIN empleados e " not in sql
