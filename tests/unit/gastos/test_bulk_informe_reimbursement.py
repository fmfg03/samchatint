"""Exercise bulk approval through the canonical reimbursement routing service."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

from devnous.gastos.models import Documento
from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_workflow_service as workflow
from devnous.gastos.services import reimbursement_payment_run_service as routing


@pytest.fixture
def bulk(monkeypatch):
    actor = SimpleNamespace(id=uuid4())
    informe = Documento(
        id=uuid4(),
        tipo="INFORME",
        estado="enviado",
        numero_referencia="I-TEST",
        cuenta_gastos_id=uuid4(),
        budget_concept_id=uuid4(),
        empleado_id=actor.id,
    )
    solicitud = Documento(id=uuid4(), tipo="SOLICITUD", estado="borrador")
    cuenta = SimpleNamespace(
        id=informe.cuenta_gastos_id,
        empleado_id=actor.id,
        referencia_base="TEST",
    )
    account_result = MagicMock()
    account_result.scalar_one_or_none.return_value = cuenta
    session = SimpleNamespace(
        execute=AsyncMock(return_value=account_result),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        add=MagicMock(),
    )
    request = SimpleNamespace(
        form=AsyncMock(return_value=FormData([("documento_ids", str(informe.id))])),
    )

    async def transition(*args, **kwargs):
        informe.estado = "aprobado" if kwargs["action"] == "approve" else "rechazado"
        await session.commit()
        return SimpleNamespace(documento=informe)

    transition_mock = AsyncMock(side_effect=transition)
    monkeypatch.setattr(user_routes, "transition_documento_workflow", transition_mock)
    gate = AsyncMock(return_value=True)
    monkeypatch.setattr(user_routes, "_can_review_pending_approvals", gate)
    monkeypatch.setattr(user_routes, "audit_context_from_request", lambda _: None)
    monkeypatch.setattr(
        routing,
        "_compute_cuenta_saldo_context",
        AsyncMock(return_value={"saldo_raw": -250.75}),
    )
    find = AsyncMock(return_value=solicitud)
    monkeypatch.setattr(routing, "_find_existing_reimbursement_solicitud", find)
    monkeypatch.setattr(
        workflow,
        "_sync_reimbursement_budget_from_informe",
        AsyncMock(return_value=informe),
    )
    approval_actor = AsyncMock(return_value=actor.id)
    monkeypatch.setattr(workflow, "_linked_informe_approval_actor_id", approval_actor)
    create = AsyncMock(return_value=solicitud)
    monkeypatch.setattr(routing, "create_solicitud_personal_document", create)
    monkeypatch.setattr(
        routing,
        "_resolve_reimbursement_provider_id",
        AsyncMock(return_value=(uuid4(), None)),
    )
    notify = MagicMock()
    monkeypatch.setattr(routing, "_schedule_pending_payment_notification", notify)

    async def run(action="approve", comentario=None):
        return await user_routes.documentos_pendientes_accion_lote(
            request=request,
            session=session,
            current_empleado=actor,
            action=action,
            comentario=comentario,
            next="/documentos/pendientes",
        )

    return SimpleNamespace(
        run=run,
        informe=informe,
        solicitud=solicitud,
        session=session,
        actor=actor,
        request=request,
        transition=transition_mock,
        find=find,
        create=create,
        notify=notify,
        approval_actor=approval_actor,
        gate=gate,
    )


def feedback(response):
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)


def test_bulk_approval_promotes_existing_draft_with_inherited_approval(bulk):
    response = asyncio.run(bulk.run())

    assert bulk.informe.estado == "aprobado"
    assert bulk.solicitud.estado == "aprobado"
    assert bulk.solicitud.fecha_pago is not None
    approval = bulk.session.add.call_args.args[0]
    assert approval.entidad_id == bulk.solicitud.id
    assert approval.aprobador_id == bulk.actor.id
    assert approval.accion == "aprobar"
    bulk.create.assert_not_awaited()
    bulk.notify.assert_called_once_with(bulk.solicitud.id)
    assert bulk.session.commit.await_count == 2
    assert "error" not in feedback(response)


def test_bulk_approval_creates_missing_request_through_canonical_service(bulk):
    bulk.find.return_value = None
    response = asyncio.run(bulk.run())

    bulk.create.assert_awaited_once()
    payload = bulk.create.call_args.args[1]
    assert payload.allow_closed_cuenta is True
    assert payload.budget_concept_id == bulk.informe.budget_concept_id
    assert bulk.solicitud.estado == "aprobado"
    assert "error" not in feedback(response)


@pytest.mark.parametrize("block", ["budget", "audit"])
def test_bulk_approval_keeps_blocked_reimbursement_and_reports_warning(bulk, block):
    if block == "budget":
        bulk.informe.budget_concept_id = None
    else:
        bulk.approval_actor.return_value = None
    response = asyncio.run(bulk.run())

    assert bulk.informe.estado == "aprobado"
    assert bulk.solicitud.estado == "borrador"
    bulk.session.add.assert_not_called()
    bulk.notify.assert_not_called()
    bulk.session.rollback.assert_awaited_once()
    params = feedback(response)
    assert params["error"] == ["bulk_reimbursement_partial"]
    assert "1 documento(s) aprobado(s)" in params["success_msg"][0]
    assert "I-TEST" in params["error_msg"][0]


def test_bulk_rejection_does_not_route_reimbursement(bulk):
    response = asyncio.run(bulk.run(action="reject", comentario="No procede"))

    assert bulk.informe.estado == "rechazado"
    bulk.find.assert_not_awaited()
    assert bulk.solicitud.estado == "borrador"
    assert "error" not in feedback(response)


def test_bulk_approval_does_not_route_ordinary_solicitud(bulk):
    bulk.informe.tipo = "SOLICITUD"
    asyncio.run(bulk.run())
    bulk.find.assert_not_awaited()


def test_bulk_denied_access_never_approves_or_routes(bulk):
    bulk.gate.return_value = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk.run())
    assert exc.value.status_code == 403
    bulk.transition.assert_not_awaited()
    bulk.find.assert_not_awaited()


def test_bulk_failed_approval_never_routes_and_preserves_existing_error(bulk):
    bulk.transition.side_effect = workflow.DocumentoWorkflowPermissionError(
        "not_assigned_approver", "No autorizado"
    )
    response = asyncio.run(bulk.run())
    bulk.find.assert_not_awaited()
    assert feedback(response)["error"] == ["bulk_action_failed"]


def test_bulk_routing_failure_rolls_back_pending_work_and_continues(bulk, monkeypatch):
    second = Documento(id=uuid4(), tipo="INFORME", estado="enviado")
    documents = {bulk.informe.id: bulk.informe, second.id: second}
    bulk.request.form.return_value = FormData(
        [("documento_ids", str(key)) for key in documents]
    )
    events = []

    async def transition(*args, **kwargs):
        document = documents[kwargs["documento_id"]]
        document.estado = "aprobado"
        events.append("approve")
        return SimpleNamespace(documento=document)

    async def route(*args, **kwargs):
        events.append("route")
        if kwargs["informe_doc"] is bulk.informe:
            raise RuntimeError("database operation failed")
        return False, None

    async def rollback():
        events.append("rollback")

    bulk.transition.side_effect = transition
    bulk.session.rollback.side_effect = rollback
    helper = AsyncMock(side_effect=route)
    monkeypatch.setattr(
        user_routes, "_ensure_reembolso_solicitud_for_approved_informe", helper
    )
    response = asyncio.run(bulk.run())

    assert events == ["approve", "route", "rollback", "approve", "route"]
    assert all(doc.estado == "aprobado" for doc in documents.values())
    params = feedback(response)
    assert "2 documento(s) aprobado(s)" in params["success_msg"][0]
    assert params["error"] == ["bulk_reimbursement_partial"]
    assert "I-TEST" in params["error_msg"][0]


def test_bulk_partial_approval_and_reimbursement_warning_are_both_visible(
    bulk, monkeypatch
):
    failed_id = uuid4()
    bulk.request.form.return_value = FormData(
        [
            ("documento_ids", str(bulk.informe.id)),
            ("documento_ids", str(failed_id)),
        ]
    )
    bulk.transition.side_effect = [
        SimpleNamespace(documento=bulk.informe),
        workflow.DocumentoWorkflowValidationError("documento_not_found", "No existe"),
    ]
    bulk.informe.estado = "aprobado"
    monkeypatch.setattr(
        user_routes,
        "_ensure_reembolso_solicitud_for_approved_informe",
        AsyncMock(return_value=(False, "Falta cuenta vinculada")),
    )
    response = asyncio.run(bulk.run())

    params = feedback(response)
    assert params["error"] == ["bulk_action_partial"]
    assert "1 documento(s) aprobado(s)" in params["success_msg"][0]
    assert "1 documento(s) no se pudieron procesar" in params["error_msg"][0]
    assert "Falta cuenta vinculada" in params["error_msg"][0]


def test_repeated_bulk_routing_does_not_duplicate_request_or_approval(bulk):
    async def run_twice():
        await bulk.run()
        await bulk.run()

    asyncio.run(run_twice())
    assert bulk.solicitud.estado == "aprobado"
    bulk.session.add.assert_called_once()
    bulk.create.assert_not_awaited()
    bulk.notify.assert_called_once()


def test_bulk_unexpected_approval_failure_is_not_a_reimbursement_warning(bulk):
    bulk.transition.side_effect = RuntimeError("approval failed")
    response = asyncio.run(bulk.run())

    bulk.find.assert_not_awaited()
    bulk.session.rollback.assert_awaited_once()
    assert feedback(response)["error"] == ["bulk_action_failed"]


def test_bulk_warning_feedback_is_bounded_for_large_batches(bulk, monkeypatch):
    ids = [uuid4() for _ in range(5)]
    bulk.request.form.return_value = FormData(
        [("documento_ids", str(key)) for key in ids]
    )
    bulk.transition.side_effect = [
        SimpleNamespace(
            documento=Documento(
                id=key,
                tipo="INFORME",
                estado="aprobado",
                numero_referencia=f"I-{i}",
            )
        )
        for i, key in enumerate(ids)
    ]
    monkeypatch.setattr(
        user_routes,
        "_ensure_reembolso_solicitud_for_approved_informe",
        AsyncMock(return_value=(False, "Pendiente " * 100)),
    )

    params = feedback(asyncio.run(bulk.run()))
    assert "5 documento(s) aprobado(s)" in params["success_msg"][0]
    assert "5 informe(s) aprobado(s)" in params["error_msg"][0]
    assert "I-0" in params["error_msg"][0]
    assert "I-3" not in params["error_msg"][0]
    assert len(params["error_msg"][0]) < 850
