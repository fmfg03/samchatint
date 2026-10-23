"""Web decisions and optional Control rejection preserve canonical boundaries."""

import ast
import inspect
from datetime import datetime
from html.parser import HTMLParser
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import user_routes
from devnous.gastos.services.documento_workflow_service import (
    DocumentoWorkflowValidationError,
)


@pytest.fixture(autouse=True)
def no_provisional_policy_unless_requested(monkeypatch):
    monkeypatch.setattr(
        user_routes, "provisional_policy_is_active", AsyncMock(return_value=False)
    )


class ControlSession:
    def __init__(self, objects):
        self.objects = objects
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, key):
        return self.objects.get((model, key))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
@pytest.mark.parametrize("reason", [None, "", " \n\t ", "  <b>Corregir partida</b>  "])
async def test_control_rejection_reason_optional_and_audited(kind, reason):
    cuenta = SimpleNamespace(id=uuid4(), estado="cerrada", closed_at=datetime.now())
    document = SimpleNamespace(
        id=uuid4(),
        tipo=kind,
        estado="control_presupuestal",
        enviado_en=datetime.now(),
        cuenta_gastos_id=cuenta.id,
    )
    actor = SimpleNamespace(id=uuid4())
    session = ControlSession(
        {
            (user_routes.Documento, document.id): document,
            (user_routes.CuentaDeGastos, cuenta.id): cuenta,
        }
    )

    await user_routes._reject_control_presupuestal_document(
        session, documento_id=document.id, actor=actor, motivo_rechazo=reason
    )

    assert document.estado == ("borrador" if kind == "INFORME" else "rechazado")
    assert document.enviado_en is None
    assert cuenta.estado == ("abierta" if kind == "INFORME" else "cerrada")
    assert (cuenta.closed_at is None) is (kind == "INFORME")
    assert len(session.added) == 1
    audit = session.added[0]
    assert audit.aprobador_id == actor.id
    assert audit.entidad_id == document.id
    assert audit.accion == "rechazar_control_presupuestal"
    assert audit.comentario.startswith("Control Presupuestal regresó el documento")
    if reason and reason.strip():
        assert audit.comentario.endswith("Motivo: <b>Corregir partida</b>")
        latest = user_routes._latest_document_rejection([audit])
        guidance = user_routes.build_document_workflow_guidance(
            state="rechazado",
            document_type=kind,
            is_owner=True,
            can_approve_or_reject=False,
            rejection_reason=latest.comentario,
        )
        html = user_routes._render_document_workflow_guidance_html(guidance)
        assert "<b>Corregir partida</b>" not in html
        assert "&lt;b&gt;Corregir partida&lt;/b&gt;" in html
    else:
        assert "Motivo:" not in audit.comentario


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["enviado", "aprobado", "pagado", "borrador"])
async def test_control_reason_never_authorizes_a_document_in_another_state(state):
    document = SimpleNamespace(id=uuid4(), tipo="SOLICITUD", estado=state)
    session = ControlSession({(user_routes.Documento, document.id): document})
    with pytest.raises(DocumentoWorkflowValidationError, match="Control Presupuestal"):
        await user_routes._reject_control_presupuestal_document(
            session,
            documento_id=document.id,
            actor=SimpleNamespace(id=uuid4()),
            motivo_rechazo="Una explicación no concede autoridad",
        )
    assert document.estado == state
    assert session.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", "Corregir comprobantes"])
async def test_control_single_rejection_does_not_require_a_budget_concept(
    monkeypatch, reason
):
    document = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="control_presupuestal",
        enviado_en=datetime.now(),
        cuenta_gastos_id=None,
        budget_concept_id=None,
    )
    session = ControlSession({(user_routes.Documento, document.id): document})
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda _: True)
    response = await user_routes.asignar_control_presupuestal_lote(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        next=None,
        reject_documento_id=str(document.id),
        motivo_rechazo=reason,
    )
    assert response.status_code == 303
    assert "success_msg=" in response.headers["location"]
    assert document.estado == "rechazado"
    assert session.commits == 1 and session.rollbacks == 0
    assert len(session.added) == 1
    assert ("Motivo:" in session.added[0].comentario) is bool(reason)


@pytest.mark.asyncio
async def test_control_bulk_rejection_records_reason_once_per_informe(monkeypatch):
    document = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        estado="control_presupuestal",
        enviado_en=datetime.now(),
        cuenta_gastos_id=None,
    )
    expenses = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]
    objects = {(user_routes.Documento, document.id): document}
    objects.update(
        {(user_routes.ExpenseReport, expense.id): expense for expense in expenses}
    )
    session = ControlSession(objects)
    selected = [f"expense:{expense.id}" for expense in expenses]
    selected.append(f"doc:{document.id}")
    form = SimpleNamespace(
        getlist=lambda key: selected if key == "budget_item_ids" else []
    )
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda _: True)
    monkeypatch.setattr(
        user_routes, "_informe_documento_for_expense", AsyncMock(return_value=document)
    )
    response = await user_routes.asignar_control_presupuestal_lote(
        request=SimpleNamespace(form=AsyncMock(return_value=form)),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        next=None,
        single_item_id=None,
        single_documento_id=None,
        reject_documento_id=None,
        bulk_action="reject_selected",
        motivo_rechazo="  Falta aclaración  ",
    )
    assert response.status_code == 303
    assert document.estado == "borrador"
    assert len(session.added) == 1
    assert session.added[0].comentario.endswith("Motivo: Falta aclaración")
    assert session.commits == 1


@pytest.mark.asyncio
async def test_control_rejection_reason_does_not_bypass_actor_permission(monkeypatch):
    session = ControlSession({})
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda _: False)
    with pytest.raises(HTTPException) as exc:
        await user_routes.asignar_control_presupuestal_lote(
            request=SimpleNamespace(),
            session=session,
            current_empleado=SimpleNamespace(id=uuid4()),
            reject_documento_id=str(uuid4()),
            motivo_rechazo="Motivo informado",
        )
    assert exc.value.status_code == 403
    assert session.added == [] and session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
@pytest.mark.parametrize("reference", [None, "363"])
async def test_configured_supervisor_has_detail_actions_outside_assigned_route(
    monkeypatch, kind, reference
):
    supervisor = AsyncMock(return_value=True)
    monkeypatch.setattr(user_routes, "actor_is_approval_supervisor", supervisor)
    session = SimpleNamespace(execute=AsyncMock())
    actor = SimpleNamespace(id=uuid4(), rol="admin")
    document = SimpleNamespace(
        id=uuid4(), tipo=kind, estado="enviado", referencia_operaciones=reference
    )
    assert await user_routes._can_decide_document_approval(
        session, actor, document, None
    )
    supervisor.assert_awaited_once_with(session, actor_id=actor.id)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
@pytest.mark.parametrize(
    "state", ["control_presupuestal", "aprobado", "pagado", "rechazado"]
)
async def test_global_supervision_does_not_render_actions_for_ineligible_state(
    monkeypatch, kind, state
):
    supervisor = AsyncMock(return_value=True)
    monkeypatch.setattr(user_routes, "actor_is_approval_supervisor", supervisor)
    document = SimpleNamespace(id=uuid4(), tipo=kind, estado=state)
    assert not await user_routes._can_decide_document_approval(
        SimpleNamespace(), SimpleNamespace(id=uuid4(), rol="admin"), document, None
    )
    supervisor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("supervisor", [False, True])
async def test_another_admin_does_not_gain_supervisor_operations_actions(
    monkeypatch, supervisor
):
    monkeypatch.setattr(
        user_routes, "actor_is_approval_supervisor", AsyncMock(return_value=supervisor)
    )
    route_authority = AsyncMock(return_value=False)
    monkeypatch.setattr(user_routes, "actor_is_route_approver", route_authority)
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1))
    )
    document = SimpleNamespace(
        id=uuid4(), estado="enviado", referencia_operaciones="363"
    )
    result = await user_routes._can_decide_document_approval(
        session, SimpleNamespace(id=uuid4(), rol="admin"), document, None
    )
    assert result is supervisor
    assert route_authority.await_count == int(not supervisor)


@pytest.mark.asyncio
async def test_supervisor_membership_opens_queue_without_changing_role(monkeypatch):
    monkeypatch.setattr(
        user_routes, "actor_is_approval_supervisor", AsyncMock(return_value=True)
    )
    session = SimpleNamespace(execute=AsyncMock())
    assert await user_routes._can_review_pending_approvals(
        session, SimpleNamespace(id=uuid4(), rol="empleado")
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superadmin"])
@pytest.mark.parametrize("route_exists", [False, True])
async def test_active_matrix_denies_unassigned_detail_actor_even_without_snapshot(
    monkeypatch, role, route_exists
):
    monkeypatch.setattr(
        user_routes, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        user_routes, "actor_is_approval_supervisor", AsyncMock(return_value=False)
    )
    authority = AsyncMock(return_value=False)
    monkeypatch.setattr(user_routes, "actor_is_route_approver", authority)
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: 1 if route_exists else None
            )
        )
    )
    document = SimpleNamespace(
        id=uuid4(), estado="enviado", referencia_operaciones=None
    )
    actor = SimpleNamespace(id=uuid4(), rol=role)
    assert not await user_routes._can_decide_document_approval(
        session, actor, document, SimpleNamespace(aprobador_id=None)
    )
    authority.assert_awaited_once_with(
        session, actor_id=actor.id, documento_id=document.id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superadmin"])
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
async def test_active_matrix_queue_keeps_actor_predicate_and_route_action_controls(
    monkeypatch, role, kind
):
    document = SimpleNamespace(
        id=uuid4(),
        tipo=kind,
        estado="enviado",
        referencia_operaciones=None,
        monto_total=10,
        monto_solicitado=10,
        enviado_en=None,
        creado_en=None,
    )
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            unique=lambda: SimpleNamespace(all=lambda: [document])
        )
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    actor = SimpleNamespace(id=uuid4(), rol=role)
    monkeypatch.setattr(
        user_routes, "provisional_policy_is_active", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", AsyncMock(return_value=True)
    )
    authority = AsyncMock(return_value=set())
    monkeypatch.setattr(user_routes, "actor_route_approver_document_ids", authority)
    monkeypatch.setattr(
        user_routes,
        "fetch_documento_aprobador_display_batch",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(user_routes, "currency_for", lambda _: "MXN")
    monkeypatch.setattr(user_routes, "documento_project_name", lambda *_: "Proyecto")
    monkeypatch.setattr(
        user_routes,
        "_documentos_todos_reporting_row_values",
        lambda *_a, **_k: {
            "numero_referencia": str(document.id),
            "enviado": "-",
            "creado": "-",
            "concepto": "Prueba",
            "beneficiario": "Beneficiario",
            "proveedor": "-",
            "solicitante": "Solicitante",
            "tipo_documento": kind,
            "estado": "En revisión",
            "monto_total": "10",
        },
    )
    for name in (
        "render_top_navigation",
        "_gastos_workspace_nav_html",
        "_gastos_breadcrumb_html",
    ):
        monkeypatch.setattr(user_routes, name, lambda *_: "")
    monkeypatch.setattr(
        user_routes, "_render_workspace_hero", lambda **kwargs: kwargs["side_html"]
    )
    html = await user_routes.documentos_pendientes(
        SimpleNamespace(query_params={}), session, actor
    )
    assert f"/documentos/{document.id}?next=" in html
    if role == "superadmin":
        assert f"/documentos/{document.id}/aprobar" not in html
        assert f'name="documento_ids" value="{document.id}"' not in html
        authority.assert_awaited_once_with(
            session, actor_id=actor.id, documento_ids={document.id}
        )
    else:
        # Non-superadmin visibility is resolved by the canonical SQL predicate,
        # which admits GV as supervisor but excludes unassigned ordinary admins.
        query = session.execute.await_args.args[0]
        sql = str(query)
        assert user_routes.document_route_approver_sql() in sql
        assert f"NOT ({user_routes.provisional_policy_active_sql()})" in sql
        assert query.compile().params["route_employee_id"] == str(actor.id)


@pytest.mark.asyncio
async def test_control_queue_renders_optional_reason_in_the_decision_form(monkeypatch):
    from html.parser import HTMLParser

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.form_action = None
            self.reason = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.form_action = attrs.get("action")
            if tag == "textarea" and attrs.get("name") == "motivo_rechazo":
                self.reason.append((self.form_action, attrs))

        def handle_endtag(self, tag):
            if tag == "form":
                self.form_action = None

    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda _: True)
    for name in ("render_top_navigation", "_gastos_breadcrumb_html"):
        monkeypatch.setattr(user_routes, name, lambda *_: "")
    monkeypatch.setattr(user_routes, "_render_workspace_hero", lambda **_: "")
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(unique=lambda: SimpleNamespace(all=lambda: []))
    )
    html = await user_routes.documentos_control_presupuestal(
        request=SimpleNamespace(query_params={}),
        session=SimpleNamespace(execute=AsyncMock(return_value=result)),
        current_empleado=SimpleNamespace(id=uuid4(), rol="admin"),
    )
    fields = Fields()
    fields.feed(html)
    assert len(fields.reason) == 1
    action, attrs = fields.reason[0]
    assert action == "/documentos/control-presupuestal/asignar-lote"
    assert "required" not in attrs
    assert "Motivo de rechazo (opcional)" in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,assigned,has_subject,expected",
    [
        ("empleado", True, True, True),
        ("empleado", False, True, False),
        ("admin", False, True, True),
        ("empleado", False, False, False),
        ("admin", False, False, True),
    ],
)
async def test_legacy_detail_fallback_retained_only_when_policy_inactive(
    monkeypatch, role, assigned, has_subject, expected
):
    actor = SimpleNamespace(id=uuid4(), rol=role)
    monkeypatch.setattr(
        user_routes, "actor_is_approval_supervisor", AsyncMock(return_value=False)
    )
    subject = SimpleNamespace(aprobador_id=actor.id if assigned else uuid4())
    monkeypatch.setattr(
        user_routes,
        "approval_subject_empleado",
        lambda _: subject if has_subject else None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    )
    document = SimpleNamespace(
        id=uuid4(), estado="enviado", referencia_operaciones=None
    )
    assert (
        await user_routes._can_decide_document_approval(session, actor, document, None)
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", [False, True])
async def test_anticipo_form_explains_project_folio_exclusion(monkeypatch, excluded):
    from starlette.datastructures import QueryParams

    actor = SimpleNamespace(
        id=uuid4(),
        rol="empleado",
        nombre="Operaciones",
        departamento="Operaciones",
        activo=True,
    )
    tournament = SimpleNamespace(id=uuid4(), name="Proyecto", etapas=[], categorias=[])
    monkeypatch.setattr(
        user_routes,
        "fetch_active_tournaments_for_empleado",
        AsyncMock(return_value=[tournament]),
    )
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _: {})
    monkeypatch.setattr(
        user_routes,
        "_resolve_active_beneficiary_empleado",
        AsyncMock(return_value=actor),
    )
    monkeypatch.setattr(
        user_routes, "_active_beneficiary_empleados", AsyncMock(return_value=[actor])
    )
    monkeypatch.setattr(user_routes, "_beneficiary_selection_allowed", lambda *_: True)
    monkeypatch.setattr(user_routes, "_can_request_for_other_employee", lambda _: False)
    monkeypatch.setattr(
        user_routes, "_html_empleado_bank_account_options", AsyncMock(return_value="")
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_: "")
    exclusion = AsyncMock(return_value=excluded)
    monkeypatch.setattr(user_routes, "project_excludes_operations_reference", exclusion)
    session = SimpleNamespace(execute=AsyncMock())
    html = await user_routes.solicitar_anticipo_form(
        SimpleNamespace(query_params=QueryParams({"torneo_id": str(tournament.id)})),
        session,
        actor,
    )
    exclusion.assert_awaited_once_with(session, tournament_id=tournament.id)
    if excluded:
        assert "Este proyecto no genera folios de Operaciones." in html
        assert 'value="No aplica"' in html
    else:
        assert "Se asignará al crear si corresponde al proyecto seleccionado" in html


@pytest.mark.asyncio
async def test_informe_creation_passes_effective_project_to_folio_owner(monkeypatch):
    actor = SimpleNamespace(id=uuid4())
    tournament_id = uuid4()

    class CreationSession(ControlSession):
        async def execute(self, _):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        async def flush(self):
            for row in self.added:
                if row.id is None:
                    row.id = uuid4()

    session = CreationSession({})
    allocator = AsyncMock(return_value=None)
    monkeypatch.setattr(
        user_routes, "allocate_referencia_operaciones_for_empleado", allocator
    )
    cuenta, error = await user_routes._create_cuenta_de_gastos_with_informe(
        session,
        empleado=actor,
        beneficiario=actor,
        tipo_cuenta="local",
        torneo_id=tournament_id,
        fase="Fase Nacional",
        categorias=[],
        edicion=2026,
        currency="MXN",
    )
    assert error is None and cuenta is not None
    allocator.assert_awaited_once_with(session, actor, torneo_id=tournament_id)
    documents = [row for row in session.added if isinstance(row, user_routes.Documento)]
    assert len(documents) == 1
    assert documents[0].referencia_operaciones is None
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "comment",
    [
        None,
        "Motivo: Corregir la partida presupuestal",
        'Motivo: <img src=x onerror="alert(1)"> & revisar',
    ],
)
async def test_document_detail_real_approval_history_escapes_stored_comment(comment):
    """Execute the actual detail history loop, including its real HTML template."""
    tree = ast.parse(dedent(inspect.getsource(user_routes.ver_documento)))
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef))
    history_loop = next(
        node
        for node in route.body
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "aprobacion"
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "aprobaciones"
    )
    fragment = ast.parse(
        "async def render_history(session, aprobaciones):\n"
        "    aprobaciones_rows = ''\n"
        "    return aprobaciones_rows\n"
    )
    fragment.body[0].body.insert(1, history_loop)
    ast.fix_missing_locations(fragment)
    namespace = dict(user_routes.__dict__)
    exec(compile(fragment, "<actual-document-detail-history>", "exec"), namespace)
    actor = SimpleNamespace(nombre="Aprobador QA")
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: actor)
        )
    )
    approval = SimpleNamespace(
        aprobador_id=uuid4(),
        fecha=datetime(2026, 10, 23),
        accion="rechazar_control_presupuestal",
        comentario=comment,
    )

    rendered = await namespace["render_history"](session, [approval])

    class HistoryParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []
            self.text = []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)

        def handle_data(self, data):
            self.text.append(data)

    parsed = HistoryParser()
    parsed.feed(rendered)
    assert parsed.tags == ["tr", "td", "td", "td", "td"]
    assert (comment if comment is not None else "-") in "".join(parsed.text)
    assert "Aprobador QA" in rendered
    if comment and "<img" in comment:
        assert "<img" not in rendered
        assert "&lt;img" in rendered
        assert "&amp; revisar" in rendered
