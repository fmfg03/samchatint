"""Provisional routing semantics and preservation of financial guard owners."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.services import authorization_profile_service as profiles
from devnous.gastos.services import documento_service as documents
from devnous.gastos.services import project_authorization_service as policy


@pytest.mark.parametrize("side", ["requester", "beneficiary", "both"])
def test_person_exception_checks_both_sides_and_routes_gn_exclusively(side):
    project = policy.ProjectAuthorizationRoute(
        (policy.DIRECTOR_OPERACIONES,), True, "project"
    )
    route = policy.resolve_project_authorization_route(
        beneficiary_position_keys=(
            (policy.PROVISIONAL_EXCEPTION,) if side in {"beneficiary", "both"} else ()
        ),
        requester_position_keys=(
            (policy.PROVISIONAL_EXCEPTION,) if side in {"requester", "both"} else ()
        ),
        provisional=True,
        project_rule=project,
    )
    assert route.eligible_position_keys == (policy.PROVISIONAL_GN,)
    # Changing approver does not invent a new general folio exemption.
    assert route.requires_operations_reference is True


def test_finance_pair_does_not_make_juan_pablo_a_protected_requester():
    project = policy.ProjectAuthorizationRoute(
        ("aprobadores_finanzas_provisional",), False, "project"
    )
    assert (
        policy.resolve_project_authorization_route(
            beneficiary_position_keys=("aprobadores_finanzas_provisional",),
            requester_position_keys=(),
            provisional=True,
            project_rule=project,
        )
        == project
    )


@pytest.mark.parametrize(
    "field",
    ["tournament_id", "requester_id", "beneficiary_id", "eligible_employee_ids"],
)
def test_source_fingerprint_changes_with_authority_context(field):
    args = dict(
        route=policy.ProjectAuthorizationRoute(
            (policy.PROVISIONAL_GN,), False, "project"
        ),
        tournament_id=str(uuid4()),
        requester_id=str(uuid4()),
        beneficiary_id=None,
        eligible_employee_ids=[uuid4()],
    )
    before = policy.build_route_context_source(**args)
    args[field] = [uuid4()] if field == "eligible_employee_ids" else str(uuid4())
    after = policy.build_route_context_source(**args)
    assert before != after
    assert before.startswith("p20261023:project:") and len(before) <= 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_name", ["Gestión de Patrocinios", "Gestión RRSS y Transmisiones"]
)
async def test_excluded_project_never_calls_allocator_for_operations_creator(
    monkeypatch, project_name
):
    result = MagicMock()
    result.scalar_one_or_none.return_value = project_name
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    allocate = AsyncMock(return_value="364")
    monkeypatch.setattr(documents, "allocate_next_referencia_operaciones", allocate)
    value = await documents.allocate_referencia_operaciones_for_empleado(
        session, SimpleNamespace(departamento="Operaciones"), torneo_id=uuid4()
    )
    assert value is None
    allocate.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_context_respects_explicit_provider_and_project(monkeypatch):
    account = dict(
        torneo_id=uuid4(),
        empleado_id=uuid4(),
        beneficiario_empleado_id=uuid4(),
        beneficiario_proveedor_cliente_id=None,
    )
    result = MagicMock()
    result.mappings.return_value.first.return_value = account
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    doc = SimpleNamespace(
        empleado_id=uuid4(),
        torneo_id=None,
        cuenta_gastos_id=uuid4(),
        beneficiario_empleado_id=None,
        beneficiario_proveedor_cliente_id=uuid4(),
    )
    context = await policy.resolve_document_route_context(session, doc)
    assert context["tournament_id"] == str(account["torneo_id"])
    assert context["requester_id"] == str(doc.empleado_id)
    assert context["beneficiary_id"] is None


@pytest.mark.asyncio
async def test_historical_folio_cannot_replace_versioned_gn_authority(monkeypatch):
    route = policy.ProjectAuthorizationRoute(
        (policy.PROVISIONAL_GN,), False, "p20261023:project:" + "a" * 64
    )
    monkeypatch.setattr(
        policy, "resolve_and_snapshot_document_route", AsyncMock(return_value=route)
    )
    session = SimpleNamespace(execute=AsyncMock())
    doc = SimpleNamespace(
        id=uuid4(), tipo="SOLICITUD", estado="enviado", referencia_operaciones="363"
    )
    assert await policy.prepare_document_authorization_route(session, doc) == route
    assert doc.referencia_operaciones == "363"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active,authorized,blocked",
    [(True, True, False), (True, False, True), (False, True, True)],
)
async def test_legacy_profile_identity_defers_only_to_verified_active_policy(
    monkeypatch, active, authorized, blocked
):
    monkeypatch.setenv("SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT", "true")
    monkeypatch.setattr(
        policy, "provisional_policy_is_active", AsyncMock(return_value=active)
    )
    authority = AsyncMock(return_value=authorized)
    monkeypatch.setattr(policy, "actor_is_route_approver", authority)
    monkeypatch.setattr(
        profiles,
        "build_document_authorization_evidence",
        AsyncMock(
            return_value={
                "required_role_keys": ["legacy_required"],
                "matching_profiles": [
                    {"role_key": "legacy_required", "employee_matcher": "Unrelated"}
                ],
            }
        ),
    )
    actor = SimpleNamespace(id=uuid4(), nombre="Configured supervisor", rol="admin")
    result = await profiles.build_authorization_route_hard_block(
        None, SimpleNamespace(id=uuid4(), tipo="INFORME"), actor
    )
    assert (result is not None) is blocked
    assert authority.await_count == int(active)


@pytest.mark.asyncio
async def test_context_refresh_records_real_actor_without_approval(monkeypatch):
    doc = SimpleNamespace(
        id=uuid4(), tipo="SOLICITUD", estado="enviado", referencia_operaciones="363"
    )
    actor = uuid4()
    previous = dict(
        eligible_position_keys=["director_operaciones"],
        eligible_empleado_ids=[str(uuid4())],
        requires_operations_reference=True,
        source="operations_reference",
    )
    result = MagicMock()
    result.mappings.return_value.first.return_value = previous
    session = SimpleNamespace(execute=AsyncMock(return_value=result), add=MagicMock())
    route = policy.ProjectAuthorizationRoute((policy.PROVISIONAL_GN,), False, "project")
    preview = dict(
        route=route,
        source="p20261023:project:" + "a" * 64,
        eligible_employee_ids=[str(uuid4())],
        provisional=True,
        context={
            "tournament_id": str(uuid4()),
            "requester_id": str(uuid4()),
            "beneficiary_id": None,
        },
    )
    monkeypatch.setattr(
        policy, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        policy, "preview_document_authorization_route", AsyncMock(return_value=preview)
    )
    actual = await policy.resolve_and_snapshot_document_route(
        session, doc, audit_actor_id=actor, audit_context={"plan_sha256": "b" * 64}
    )
    event = session.add.call_args.args[0]
    assert event.accion == "actualizar_ruta_autorizacion"
    assert event.aprobador_id == actor and event.entidad_id == doc.id
    assert "operations_reference" in event.comentario
    assert "plan_sha256" in event.comentario and "b" * 64 in event.comentario
    assert session.add.call_count == 1
    assert doc.estado == "enviado" and doc.referencia_operaciones == "363"
    assert actual.source == preview["source"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["aprobado", "pagado", "cerrado"])
async def test_direct_resolver_does_not_create_or_rewrite_final_snapshot(
    monkeypatch, status
):
    doc = SimpleNamespace(id=uuid4(), estado=status)
    result = MagicMock()
    result.mappings.return_value.first.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result), add=MagicMock())
    route = policy.ProjectAuthorizationRoute((policy.PROVISIONAL_GN,), False, "project")
    monkeypatch.setattr(
        policy, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        policy,
        "preview_document_authorization_route",
        AsyncMock(
            return_value={
                "route": route,
                "source": "p20261023:project:" + "a" * 64,
                "provisional": True,
                "eligible_employee_ids": [],
                "context": {},
            }
        ),
    )
    await policy.resolve_and_snapshot_document_route(
        session, doc, audit_actor_id=uuid4()
    )
    assert session.execute.await_count == 1
    session.add.assert_not_called()


def test_legacy_person_folio_exemption_is_not_changed_by_new_approval_route():
    route = policy.resolve_project_authorization_route(
        beneficiary_position_keys=(
            policy.PROVISIONAL_EXCEPTION,
            policy.DIRECTOR_OPERACIONES,
        ),
        requester_position_keys=(),
        provisional=True,
        project_rule=policy.ProjectAuthorizationRoute(
            (policy.DIRECTOR_OPERACIONES,), True, "project"
        ),
    )
    assert route.eligible_position_keys == (policy.PROVISIONAL_GN,)
    assert route.requires_operations_reference is False


@pytest.mark.asyncio
async def test_unattributed_context_refresh_is_read_only(monkeypatch):
    doc = SimpleNamespace(id=uuid4(), tipo="SOLICITUD", estado="enviado")
    result = MagicMock()
    result.mappings.return_value.first.return_value = {
        "source": "old",
        "eligible_position_keys": ["director_operaciones"],
        "eligible_empleado_ids": [],
        "requires_operations_reference": True,
    }
    session = SimpleNamespace(execute=AsyncMock(return_value=result), add=MagicMock())
    route = policy.ProjectAuthorizationRoute((policy.PROVISIONAL_GN,), False, "person")
    monkeypatch.setattr(
        policy, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        policy,
        "preview_document_authorization_route",
        AsyncMock(
            return_value={
                "route": route,
                "provisional": True,
                "source": "new",
                "context": {},
                "eligible_employee_ids": [],
            }
        ),
    )
    actual = await policy.resolve_and_snapshot_document_route(session, doc)
    assert actual.source == "new"
    assert session.execute.await_count == 1
    session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_rejection", [False, True])
@pytest.mark.parametrize("decided_state", ["aprobado", "rechazado"])
async def test_pending_label_uses_canonical_gn_and_preserves_decision_actor(
    monkeypatch,
    old_rejection,
    decided_state,
):
    pending = SimpleNamespace(id=uuid4(), tipo="SOLICITUD", estado="enviado")
    historical = SimpleNamespace(id=uuid4(), tipo="INFORME", estado=decided_state)
    decision_result = MagicMock()
    decision_result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            entidad_id=historical.id,
            aprobador=SimpleNamespace(nombre="Historical actor"),
        )
    ]
    if old_rejection:
        decision_result.scalars.return_value.all.return_value.append(
            SimpleNamespace(
                entidad_id=pending.id,
                aprobador=SimpleNamespace(nombre="Old Odilon rejector"),
            )
        )
    route_result = MagicMock()
    route_result.mappings.return_value.all.return_value = [
        {"documento_id": pending.id, "nombre": "Federico GN"}
    ]
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[decision_result, route_result])
    )
    monkeypatch.setattr(
        policy, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    actual = await documents.fetch_documento_aprobador_display_batch(
        session, [pending, historical]
    )
    assert actual == {pending.id: "Federico GN", historical.id: "Historical actor"}
    query = str(session.execute.await_args_list[1].args[0])
    assert "supervision_aprobaciones_federico_gv" not in query
    assert "aprobaciones_escalan_gn" in query
    assert session.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_name", ["Gestión de Patrocinios", "Gestión RRSS y Transmisiones"]
)
async def test_assistant_receipt_project_exclusion_precedes_operations_allocator(
    monkeypatch, project_name
):
    from devnous.gastos.services import receipt_workflow_service as receipts

    tournament_id = uuid4()
    employee = SimpleNamespace(id=uuid4(), departamento="Operaciones")
    monkeypatch.setattr(
        receipts,
        "_employee_and_tournament",
        AsyncMock(
            return_value=(
                employee,
                SimpleNamespace(id=tournament_id, name=project_name),
            )
        ),
    )
    monkeypatch.setattr(receipts, "_unique_reference", AsyncMock(return_value="123456"))
    monkeypatch.setattr(receipts, "normalize_categories", lambda values, tournament: [])
    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project_name
    session = SimpleNamespace(
        execute=AsyncMock(return_value=project_result),
        add=MagicMock(),
        flush=AsyncMock(),
        rollback=AsyncMock(),
    )
    allocator = AsyncMock()
    monkeypatch.setattr(documents, "allocate_next_referencia_operaciones", allocator)
    monkeypatch.setattr(
        receipts,
        "create_expense_from_data",
        AsyncMock(side_effect=ValueError("stop after allocation")),
    )
    with pytest.raises(ValueError, match="stop after allocation"):
        await receipts.create_personal_receipt_workflow(
            session,
            employee_id=employee.id,
            payload={
                "amount": 10,
                "concept": "Test",
                "date": "2026-10-23",
                "payment_method": "efectivo",
                "tournament_id": str(tournament_id),
                "account_type": "local",
                "file_b64": "eA==",
            },
        )
    allocator.assert_not_awaited()
    report = session.add.call_args_list[1].args[0]
    assert report.referencia_operaciones is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,reference", [("borrador", None), ("enviado", None), ("borrador", "363")]
)
@pytest.mark.parametrize(
    "project_name",
    ["Normal project", "Gestión de Patrocinios", "Gestión RRSS y Transmisiones"],
)
async def test_draft_project_change_reuses_creator_emitter_independent_rule_flag(
    monkeypatch, state, reference, project_name
):
    requester, project = uuid4(), uuid4()
    document = SimpleNamespace(
        id=uuid4(),
        empleado_id=requester,
        estado=state,
        referencia_operaciones=reference,
    )
    route = policy.ProjectAuthorizationRoute(
        (policy.PROVISIONAL_GN,), False, "p20261023:project:" + "a" * 64
    )
    monkeypatch.setattr(
        policy, "resolve_and_snapshot_document_route", AsyncMock(return_value=route)
    )
    monkeypatch.setattr(
        policy,
        "resolve_document_route_context",
        AsyncMock(
            return_value={"requester_id": str(requester), "tournament_id": str(project)}
        ),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = project_name
    session = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        get=AsyncMock(return_value=SimpleNamespace(departamento="Operaciones")),
    )
    allocator = AsyncMock(return_value="364")
    monkeypatch.setattr(documents, "allocate_next_referencia_operaciones", allocator)
    await policy.prepare_document_authorization_route(session, document)
    expected_allocation = (
        state == "borrador" and reference is None and project_name == "Normal project"
    )
    assert allocator.await_count == int(expected_allocation)
    assert document.referencia_operaciones == (
        "364" if expected_allocation else reference
    )
