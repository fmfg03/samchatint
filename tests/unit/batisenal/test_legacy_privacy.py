"""Legacy owner regression: execute its real predicates against an isolated store."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite

from devnous.gastos.models import SupportTicket
from devnous.gastos.services import support_ticket_service as service


class Result:
    def __init__(self, rows, objects):
        self.rows, self.objects = rows, objects

    def scalars(self):
        return self

    def all(self):
        if self.objects:
            return [
                SimpleNamespace(
                    requester=None,
                    assignee=None,
                    comments=[],
                    **{
                        key: (
                            UUID(value)
                            if value
                            and key
                            in {
                                "id",
                                "requester_empleado_id",
                                "assigned_to_empleado_id",
                            }
                            else (
                                datetime.fromisoformat(value)
                                if value and key.endswith("_at")
                                else value
                            )
                        )
                        for key, value in row._mapping.items()
                    },
                )
                for row in self.rows
            ]
        return self.rows

    def scalar_one_or_none(self):
        rows = self.all()
        return rows[0] if rows else None


class Store:
    """Synchronous in-memory SQL adapter; no production DSN or ORM lazy loads."""

    def __init__(self):
        self.engine = create_engine("sqlite://")
        SupportTicket.__table__.create(self.engine)
        self.connection = self.engine.connect()
        self.writes = []

    async def execute(self, query):
        compiled = query.compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
        rows = self.connection.exec_driver_sql(str(compiled)).fetchall()
        objects = (
            len(query.column_descriptions) == 1
            and query.column_descriptions[0].get("entity") is SupportTicket
        )
        return Result(rows, objects)

    async def scalar(self, query):
        return (await self.execute(query)).rows[0][0]

    def add(self, item):
        self.writes.append(item)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, item):
        pass


@pytest.fixture
def store():
    value = Store()
    yield value
    value.connection.close()
    value.engine.dispose()


def insert(store, actor, marker, **extra):
    identifier = uuid4()
    store.connection.execute(
        SupportTicket.__table__.insert().values(
            id=identifier,
            requester_empleado_id=actor.id,
            asunto="Consulta",
            descripcion="Detalle",
            page_url=marker,
            **extra,
        )
    )
    return identifier


@pytest.mark.asyncio
async def test_lists_counts_search_and_status_exclude_private(store):
    actor = SimpleNamespace(id=uuid4(), rol="superadmin")
    insert(
        store,
        actor,
        "batisenal:private",
        estado="resuelto",
        resolved_at=datetime.now(timezone.utc),
    )
    insert(store, actor, None)
    insert(store, actor, "/panel")
    assert len(await service.list_my_tickets(store, empleado_id=actor.id)) == 2
    assert len(await service.list_admin_tickets(store, search="Consulta")) == 2
    assert await service.list_admin_tickets(store, estado="resuelto") == []
    summary = await service.summarize_admin_tickets(store)
    assert summary.total == summary.open_count == 2
    assert summary.resolved_last_7d == 0
    assert summary.by_estado == {"abierto": 2}
    assert summary.by_prioridad == {"normal": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["empleado", "admin", "superadmin"])
async def test_private_direct_detail_and_comment_are_not_found_even_for_owner_staff(
    store, role
):
    actor = SimpleNamespace(id=uuid4(), rol=role)
    private = insert(store, actor, "batisenal:private")
    for identifier in [private, uuid4()]:
        with pytest.raises(
            service.SupportTicketNotFoundError, match="Ticket no encontrado"
        ):
            await service.get_ticket_for_user(
                store, ticket_id=identifier, empleado=actor, include_staff=True
            )
        with pytest.raises(service.SupportTicketNotFoundError):
            await service.add_ticket_comment(
                store, ticket_id=identifier, empleado=actor, body="Respuesta"
            )
    assert store.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superadmin"])
async def test_private_update_never_writes(store, role):
    actor = SimpleNamespace(id=uuid4(), rol=role)
    identifier = insert(store, actor, "batisenal:private")
    with pytest.raises(service.SupportTicketNotFoundError):
        await service.update_ticket_admin_fields(
            store,
            ticket_id=identifier,
            actor=actor,
            estado="cerrado",
            prioridad="alta",
            categoria="bug",
        )
    assert store.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker", ["batisenal:private", " batisenal:private ", "batisenal:other"]
)
async def test_reserved_context_cannot_be_created_through_legacy(store, marker):
    with pytest.raises(
        service.SupportTicketValidationError, match="Contexto reservado"
    ):
        await service.create_ticket(
            store,
            requester_empleado_id=uuid4(),
            asunto="Hola",
            descripcion="Ayuda",
            page_url=marker,
        )
    assert store.writes == []


@pytest.mark.asyncio
async def test_ordinary_create_and_owner_detail_remain_available(store):
    actor = SimpleNamespace(id=uuid4(), rol="empleado")
    ticket = await service.create_ticket(
        store,
        requester_empleado_id=actor.id,
        asunto=" Hola ",
        descripcion=" Ayuda ",
        page_url="/panel",
    )
    assert (ticket.asunto, ticket.descripcion, ticket.page_url) == (
        "Hola",
        "Ayuda",
        "/panel",
    )
    identifier = insert(store, actor, None)
    result = await service.get_ticket_for_user(
        store, ticket_id=identifier, empleado=actor
    )
    assert result.asunto == "Consulta"


@pytest.mark.asyncio
async def test_http_private_detail_comment_update_and_missing_are_indistinguishable(
    store,
):
    import httpx
    from fastapi import FastAPI

    from devnous.gastos.routes import support_routes as routes

    actor = SimpleNamespace(id=uuid4(), rol="superadmin")
    private = insert(store, actor, "batisenal:private")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_db_session] = lambda: store
    app.dependency_overrides[routes.get_current_empleado] = lambda: actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for identifier in [private, uuid4()]:
            for path in [f"/soporte/{identifier}", f"/admin/soporte/{identifier}"]:
                response = await client.get(path)
                assert response.status_code == 404
                assert response.json() == {"detail": "Ticket no encontrado."}
            response = await client.post(
                f"/soporte/{identifier}/comentarios", data={"body": "Respuesta"}
            )
            assert response.status_code == 404
            response = await client.post(
                f"/admin/soporte/{identifier}/actualizar",
                data={"estado": "cerrado", "prioridad": "alta", "categoria": "bug"},
            )
            assert response.status_code == 404
        response = await client.post(
            "/soporte/nuevo",
            data={
                "asunto": "Hola",
                "descripcion": "Ayuda",
                "page_url": "batisenal:private",
            },
        )
        assert response.status_code == 303
        assert "Contexto" in response.headers["location"]
    assert store.writes == []


@pytest.mark.asyncio
async def test_ordinary_comments_triage_and_authorization_regression(store):
    requester = SimpleNamespace(id=uuid4(), rol="empleado")
    staff = SimpleNamespace(id=uuid4(), rol="admin", nombre="Soporte")
    identifier = insert(store, requester, "/panel")
    reply = await service.add_ticket_comment(
        store, ticket_id=identifier, empleado=requester, body=" Mi comentario "
    )
    assert reply.body == "Mi comentario" and reply.author_role == "requester"
    reply = await service.add_ticket_comment(
        store, ticket_id=identifier, empleado=staff, body="Respuesta"
    )
    assert reply.author_role == "staff"
    changed = await service.update_ticket_admin_fields(
        store,
        ticket_id=identifier,
        actor=staff,
        estado="cerrado",
        prioridad="alta",
        categoria="bug",
        assigned_to_empleado_id=staff.id,
        resolution_note=" Resuelto ",
    )
    assert changed.resolved_at and changed.resolution_note == "Resuelto"
    assert "Estado:" in store.writes[-1].body
    with pytest.raises(service.SupportTicketPermissionError):
        await service.update_ticket_admin_fields(
            store,
            ticket_id=identifier,
            actor=requester,
            estado="abierto",
            prioridad="normal",
            categoria="otro",
        )
    with pytest.raises(service.SupportTicketPermissionError):
        await service.get_ticket_for_user(
            store,
            ticket_id=identifier,
            empleado=SimpleNamespace(id=uuid4(), rol="empleado"),
        )
    missing = uuid4()
    with pytest.raises(service.SupportTicketNotFoundError):
        await service.update_ticket_admin_fields(
            store,
            ticket_id=missing,
            actor=staff,
            estado="abierto",
            prioridad="normal",
            categoria="otro",
        )
    for change in [
        {"estado": "invalid"},
        {"prioridad": "invalid"},
        {"categoria": "invalid"},
    ]:
        args = dict(estado="abierto", prioridad="normal", categoria="otro", **{})
        args.update(change)
        with pytest.raises(service.SupportTicketValidationError):
            await service.update_ticket_admin_fields(
                store, ticket_id=identifier, actor=staff, **args
            )


@pytest.mark.parametrize(
    "change",
    [
        {"asunto": ""},
        {"asunto": "x" * 201},
        {"descripcion": ""},
        {"descripcion": "x" * 8001},
        {"categoria": "invalid"},
        {"prioridad": "invalid"},
    ],
)
def test_ordinary_ticket_invalid_input(change):
    values = dict(
        asunto="Hello", descripcion="Detail", categoria="otro", prioridad="normal"
    )
    values.update(change)
    with pytest.raises(service.SupportTicketValidationError):
        service.validate_ticket_input(**values)


@pytest.mark.parametrize("body", ["", "x" * 8001])
def test_ordinary_comment_invalid_input(body):
    with pytest.raises(service.SupportTicketValidationError):
        service.validate_comment_body(body)


@pytest.mark.asyncio
async def test_http_ordinary_lists_forms_and_detail_preserve_privacy(
    store, monkeypatch
):
    import sys
    from types import ModuleType

    import httpx
    from fastapi import FastAPI

    from devnous.gastos.routes import support_routes as routes

    # Only financial page chrome is replaced. Actual support handlers/query owners run.
    chrome = ModuleType("devnous.gastos.routes.user_routes")
    chrome._render_workspace_hero = lambda *args, **kw: " ".join(
        str(v) for v in kw.values()
    )
    chrome._workspace_shell_styles = lambda *args: ""
    chrome.render_top_navigation = lambda *args: "<nav>Prueba</nav>"
    monkeypatch.setitem(sys.modules, chrome.__name__, chrome)
    actor = SimpleNamespace(
        id=uuid4(), rol="superadmin", nombre="Test", correo="test@example.invalid"
    )
    private = insert(store, actor, "batisenal:private")
    ordinary = insert(store, actor, "/panel")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_db_session] = lambda: store
    app.dependency_overrides[routes.get_current_empleado] = lambda: actor

    async def staff(*args):
        return [actor]

    monkeypatch.setattr(routes, "list_staff_empleados", staff)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in [
            "/soporte?creado=1",
            "/admin/soporte?estado=bad&prioridad=bad&categoria=bad",
            "/soporte/nuevo?error=Texto",
            f"/soporte/{ordinary}?ok=comentado",
            f"/admin/soporte/{ordinary}?ok=actualizado",
        ]:
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert str(private) not in response.text
        response = await client.post(
            f"/soporte/{ordinary}/comentarios", data={"body": "Respuesta"}
        )
        assert (
            response.status_code == 303
            and "ok=comentado" in response.headers["location"]
        )
        response = await client.post(
            f"/soporte/{ordinary}/comentarios", data={"body": " "}
        )
        assert response.status_code == 303 and "error=" in response.headers["location"]
        response = await client.post(
            f"/admin/soporte/{ordinary}/actualizar",
            data={
                "estado": "abierto",
                "categoria": "otro",
                "prioridad": "normal",
                "assigned_to_empleado_id": "invalid",
            },
        )
        assert response.status_code == 303 and "error=" in response.headers["location"]
        assert (await client.get("/soporte/invalid")).status_code == 400


@pytest.mark.asyncio
async def test_existing_status_reads_never_query_private_ticket_store(monkeypatch):
    from devnous.gastos.routes import support_routes as routes

    class Rows:
        def __init__(self, value):
            self.value = value

        def all(self):
            return self.value

        def scalars(self):
            return self

        def unique(self):
            return self

        def mappings(self):
            return self

        def scalar_one(self):
            return self.value

    class ReadOnly:
        def __init__(self, values):
            self.values = iter(values)
            self.queries = []

        async def execute(self, query, *args):
            sql = str(query)
            assert "support_tickets" not in sql
            assert "support_batisenal" not in sql
            assert not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            self.queries.append(sql)
            return Rows(next(self.values))

    pipeline = await routes._solicitud_pipeline_counts(ReadOnly([[("enviado", 2)]]))
    assert pipeline["enviado"] == 2 and pipeline["pagado"] == 0
    for helper in [routes._solicitud_approval_aging, routes._solicitud_payment_aging]:
        assert await helper(ReadOnly([0, []])) == (0, [])
    assert await routes._telegram_notification_failures(
        ReadOnly([[("workflow", 2)], []])
    ) == ({"workflow": 2}, [])
    blockers = await routes._flow_blockers(ReadOnly([2, 0, 1]))
    assert all(routes._blocker_is_active(b) for b in blockers)
    assert not routes._blocker_is_active(
        routes.FlowBlocker("BLK-FIN-001", "error", "Finance", 1, "")
    )
    doc = SimpleNamespace(id=uuid4())

    async def no_recipients(*args):
        return []

    monkeypatch.setattr(
        routes, "resolve_workflow_approval_notification_recipients", no_recipients
    )
    rows = await routes._workflow_telegram_incomplete_rows(ReadOnly([[doc]]))
    assert rows[0]["status"] == "sin destinatario esperado"
    recipient = SimpleNamespace(id=uuid4())

    async def recipients(*args):
        return [recipient]

    monkeypatch.setattr(
        routes, "resolve_workflow_approval_notification_recipients", recipients
    )
    for status, count in [("sent", 0), ("failed", 1)]:
        rows = await routes._workflow_telegram_incomplete_rows(
            ReadOnly(
                [[doc], [{"recipient_empleado_id": recipient.id, "status": status}]]
            )
        )
        assert len(rows) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("populated", [False, True])
async def test_status_http_is_readonly_and_escapes_synthetic_data(
    monkeypatch, populated
):
    import sys
    from datetime import timedelta
    from types import ModuleType

    import httpx
    from fastapi import FastAPI

    from devnous.gastos.routes import support_routes as routes

    chrome = ModuleType("devnous.gastos.routes.user_routes")
    chrome._render_workspace_hero = lambda *args, **kw: " ".join(
        str(v) for v in kw.values()
    )
    chrome._workspace_shell_styles = lambda *args: ""
    chrome.render_top_navigation = lambda *args: "<nav>Technical fixture</nav>"
    monkeypatch.setitem(sys.modules, chrome.__name__, chrome)
    actor = SimpleNamespace(id=uuid4(), rol="superadmin", nombre="<img src=x>")
    now = datetime.now(timezone.utc)
    doc = SimpleNamespace(
        id=uuid4(),
        empleado=actor,
        numero_referencia="S-SYNTHETIC",
        referencia_operaciones=None,
        enviado_en=now - timedelta(days=10),
        aprobado_en=now - timedelta(days=10),
    )
    notification = SimpleNamespace(
        documento=doc,
        recipient_empleado=actor,
        error_message="<script>bad</script>",
        notification_type="workflow",
        status="failed",
    )
    values = {
        "_solicitud_pipeline_counts": {"enviado": 1 if populated else 0},
        "_solicitud_approval_aging": (1, [doc]) if populated else (0, []),
        "_solicitud_payment_aging": (1, [doc]) if populated else (0, []),
        "_telegram_notification_failures": (
            ({"workflow": 1}, [notification]) if populated else ({}, [])
        ),
        "_workflow_telegram_incomplete_rows": (
            [{"documento": doc, "recipient": actor, "status": "missing"}]
            if populated
            else []
        ),
        "_flow_blockers": [
            routes.FlowBlocker("BLK-NOTIF-INFORME", "warning", "Known gap", 0, ""),
            routes.FlowBlocker(
                "BLK-FIN-001", "error", "Finance", 1, "/admin/empleados"
            ),
        ],
    }
    for name, value in values.items():

        async def fixed(*args, result=value):
            return result

        monkeypatch.setattr(routes, name, fixed)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_db_session] = lambda: None
    app.dependency_overrides[routes.get_current_empleado] = lambda: actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/admin/soporte/estado-sistema")
        assert result.status_code == 200
        assert (
            "<img src=x>" not in result.text
            and "<script>bad</script>" not in result.text
        )
        assert "batisenal:private" not in result.text
        assert ("S-SYNTHETIC" in result.text) == populated
        actor.rol = "empleado"
        assert (await client.get("/admin/soporte/estado-sistema")).status_code == 403
    assert routes._days_waiting(None) == 0
    assert routes._days_waiting(now + timedelta(days=1), now=now) == 0
    assert routes._is_approval_stale(doc.enviado_en, now=now)
    assert routes._is_payment_stale(doc.aprobado_en, now=now)
    assert not routes._is_approval_stale(None)
    assert not routes._is_payment_stale(None)
    assert routes._aware_dt(now.replace(tzinfo=None)).tzinfo == timezone.utc
    assert routes._route_visible_for_role(frozenset({"super_admin"}), "superadmin")
