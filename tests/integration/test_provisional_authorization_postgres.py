"""Real PostgreSQL parity for provisional identity/project/folio authority.

The imported native fixture creates a fresh Unix-socket-only cluster; no existing
DSN or business database is consulted. Only synthetic narrow policy tables exist.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos.services import project_authorization_service as policy

_path = Path(__file__).with_name("test_telegram_finance_recovery_postgres.py")
_spec = importlib.util.spec_from_file_location("provisional_native_fixture", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
isolated_postgres_url = _fixture.isolated_postgres_url

GN, GV, OPS, LA, JP, OLOF, REQUESTER, BENEFICIARY = [uuid4() for _ in range(8)]
PROJECT_KEYS = [
    ("Liga", (policy.DIRECTOR_OPERACIONES,)),
    ("Copa", (policy.DIRECTOR_OPERACIONES,)),
    ("Calle", (policy.DIRECTOR_OPERACIONES,)),
    ("Bimbo", (policy.DIRECTOR_OPERACIONES,)),
    ("Merced", (policy.DIRECTOR_OPERACIONES,)),
    ("Becarios", (policy.DIRECTOR_OPERACIONES,)),
    ("AdminOps", (policy.DIRECTOR_OPERACIONES,)),
    ("Otros", (policy.DIRECTOR_OPERACIONES, policy.PROVISIONAL_GN)),
    ("Homeless", (policy.DIRECTOR_OPERACIONES, policy.PROVISIONAL_GN)),
    ("AdminDG", (policy.PROVISIONAL_GN,)),
    ("Patrocinios", (policy.PROVISIONAL_GN,)),
    ("RRSS", (policy.PROVISIONAL_GN,)),
    ("Promocion", (policy.PROVISIONAL_GN,)),
    ("AdminFin", ("aprobadores_finanzas_provisional",)),
    ("Ingresos", ("aprobadores_finanzas_provisional",)),
]


@pytest_asyncio.fixture
async def pg_policy(isolated_postgres_url):
    schema = "approval_" + uuid4().hex
    admin = create_async_engine(isolated_postgres_url, hide_parameters=True)
    async with admin.begin() as c:
        assert await c.scalar(text("SHOW listen_addresses")) == ""
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        isolated_postgres_url,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        for sql in [
            "CREATE TABLE empleados(id uuid PRIMARY KEY,activo boolean NOT NULL)",
            "CREATE TABLE tournaments(id uuid PRIMARY KEY,name text)",
            "CREATE TABLE authorization_positions(position_key text PRIMARY KEY,active boolean)",
            "CREATE TABLE authorization_position_assignments(position_key text,empleado_id uuid,active boolean)",
            "CREATE TABLE project_authorization_rules(tournament_id uuid PRIMARY KEY,eligible_position_keys jsonb,requires_operations_reference boolean,active boolean)",
            "CREATE TABLE cuentas_de_gastos(id uuid PRIMARY KEY,torneo_id uuid,empleado_id uuid,beneficiario_empleado_id uuid,beneficiario_proveedor_cliente_id uuid)",
            "CREATE TABLE documentos(id uuid PRIMARY KEY,tipo text,estado text,empleado_id uuid,beneficiario_empleado_id uuid,beneficiario_proveedor_cliente_id uuid,torneo_id uuid,cuenta_gastos_id uuid,referencia_operaciones text)",
            "CREATE TABLE documento_authorization_routes(documento_id uuid PRIMARY KEY,eligible_position_keys jsonb,eligible_empleado_ids jsonb,requires_operations_reference boolean,source varchar(100),resolved_at timestamptz DEFAULT now())",
        ]:
            await c.execute(text(sql))
        for actor in (GN, GV, OPS, LA, JP, OLOF, REQUESTER, BENEFICIARY):
            await c.execute(
                text("INSERT INTO empleados VALUES(:id,TRUE)"), {"id": actor}
            )
        memberships = {
            policy.PROVISIONAL_GN: [GN],
            policy.PROVISIONAL_SUPERVISOR: [GV],
            policy.PROVISIONAL_EXCEPTION: [OPS, OLOF, LA, GV],
            policy.DIRECTOR_OPERACIONES: [OPS],
            "aprobadores_finanzas_provisional": [LA, JP],
        }
        for key, actors in memberships.items():
            await c.execute(
                text("INSERT INTO authorization_positions VALUES(:key,TRUE)"),
                {"key": key},
            )
            for actor in actors:
                await c.execute(
                    text(
                        "INSERT INTO authorization_position_assignments VALUES(:key,:actor,TRUE)"
                    ),
                    {"key": key, "actor": actor},
                )
        projects = {}
        for name, keys in PROJECT_KEYS:
            projects[name] = uuid4()
            await c.execute(
                text("INSERT INTO tournaments VALUES(:id,:name)"),
                {
                    "id": projects[name],
                    "name": {
                        "RRSS": "Gestión RRSS y Transmisiones",
                        "Patrocinios": "Gestión de Patrocinios",
                    }.get(name, name),
                },
            )
            await c.execute(
                text(
                    "INSERT INTO project_authorization_rules VALUES(:id,CAST(:keys AS jsonb),:ro,TRUE)"
                ),
                {
                    "id": projects[name],
                    "keys": json.dumps(keys),
                    "ro": name not in {"RRSS", "Patrocinios"},
                },
            )
    try:
        yield SimpleNamespace(factory=factory, projects=projects)
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def document(
    pg,
    *,
    name="RRSS",
    requester=REQUESTER,
    beneficiary=BENEFICIARY,
    reference="363",
    kind="SOLICITUD",
):
    doc = SimpleNamespace(
        id=uuid4(),
        tipo=kind,
        estado="enviado",
        empleado_id=requester,
        beneficiario_empleado_id=beneficiary,
        beneficiario_proveedor_cliente_id=None,
        torneo_id=pg.projects[name],
        cuenta_gastos_id=None,
        referencia_operaciones=reference,
    )
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO documentos VALUES(:id,:tipo,:estado,:empleado_id,:beneficiario_empleado_id,NULL,:torneo_id,NULL,:referencia_operaciones)"
            ),
            vars(doc),
        )
    return doc


async def authorized(pg, doc, *, supervision=True):
    async with pg.factory() as session:
        return {
            actor
            for actor in (GN, GV, OPS, LA, JP, OLOF, REQUESTER, BENEFICIARY)
            if await policy.actor_is_route_approver(
                session,
                actor_id=actor,
                documento_id=doc.id,
                include_supervisor=supervision,
            )
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("name,keys", PROJECT_KEYS)
@pytest.mark.parametrize("kind", ["SOLICITUD", "INFORME"])
async def test_all_projects_actor_alternatives_and_historical_folio(
    pg_policy, name, keys, kind
):
    pg = pg_policy
    doc = await document(pg, name=name, kind=kind)
    owners = {
        actor
        for key in keys
        for actor in {
            policy.DIRECTOR_OPERACIONES: [OPS],
            policy.PROVISIONAL_GN: [GN],
            "aprobadores_finanzas_provisional": [LA, JP],
        }[key]
    }
    assert await authorized(pg, doc) == owners | {GV}
    assert await authorized(pg, doc, supervision=False) == owners


@pytest.mark.asyncio
@pytest.mark.parametrize("person", [OPS, OLOF, LA, GV])
@pytest.mark.parametrize("side", ["requester", "beneficiary"])
async def test_person_override_ignores_old_ops_snapshot_and_folio(
    pg_policy, person, side
):
    pg = pg_policy
    doc = await document(pg, name="Liga", **{side: person})
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO documento_authorization_routes VALUES(:id,'[\"director_operaciones\"]',CAST(:ids AS jsonb),TRUE,'operations_reference',now())"
            ),
            {"id": doc.id, "ids": json.dumps([str(OPS)])},
        )
        preview = await policy.preview_document_authorization_route(session, doc)
        assert preview["route"].eligible_position_keys == (policy.PROVISIONAL_GN,)
        assert preview["eligible_employee_ids"] == [str(GN)]
    assert await authorized(pg, doc) == {GN, GV}
    async with pg.factory.begin() as session:
        route = await policy.resolve_and_snapshot_document_route(session, doc)
        assert route.source.startswith("p20261023:person:")
        assert doc.referencia_operaciones == "363"


@pytest.mark.asyncio
async def test_context_change_recomputes_snapshot_and_inactive_supervisor_is_denied(
    pg_policy,
):
    pg = pg_policy
    doc = await document(pg, name="Liga", reference=None)
    async with pg.factory.begin() as session:
        before = await policy.resolve_and_snapshot_document_route(session, doc)
    doc.beneficiario_empleado_id = OLOF
    async with pg.factory.begin() as session:
        await session.execute(
            text("UPDATE documentos SET beneficiario_empleado_id=:party WHERE id=:id"),
            {"party": OLOF, "id": doc.id},
        )
        after = await policy.resolve_and_snapshot_document_route(session, doc)
        assert before.source != after.source
        assert after.eligible_position_keys == (policy.PROVISIONAL_GN,)
        await session.execute(
            text(
                "UPDATE authorization_position_assignments SET active=FALSE WHERE position_key='supervision_aprobaciones_federico_gv'"
            )
        )
    assert await authorized(pg, doc) == {GN}


@pytest.mark.asyncio
async def test_legacy_folio_authority_remains_until_explicit_activation(pg_policy):
    pg = pg_policy
    doc = await document(pg, name="RRSS")
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "UPDATE authorization_positions SET active=FALSE WHERE position_key='aprobaciones_escalan_gn'"
            )
        )
    assert await authorized(pg, doc) == {OPS}


@pytest.mark.asyncio
async def test_incomplete_gn_assignment_never_falls_back_to_ops(pg_policy):
    pg = pg_policy
    doc = await document(pg, name="RRSS", requester=OPS)
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "UPDATE authorization_position_assignments SET active=FALSE WHERE position_key='aprobador_federico_gn'"
            )
        )
    assert await authorized(pg, doc, supervision=False) == set()


@pytest.mark.asyncio
async def test_employee_inactive_and_other_document_types_do_not_gain_supervision(
    pg_policy,
):
    pg = pg_policy
    doc = await document(pg, name="RRSS", kind="OTRO")
    assert await authorized(pg, doc) == {
        OPS
    }  # legacy operational record, no GVextension
    doc = await document(pg, name="RRSS")
    async with pg.factory.begin() as session:
        await session.execute(
            text("UPDATE empleados SET activo=FALSE WHERE id=:id"), {"id": GV}
        )
    assert await authorized(pg, doc) == {GN}


@pytest.mark.asyncio
async def test_account_beneficiary_and_project_have_same_python_and_sql_authority(
    pg_policy,
):
    pg = pg_policy
    doc = await document(pg, name="Liga", reference=None)
    doc.torneo_id = None
    doc.beneficiario_empleado_id = None
    doc.cuenta_gastos_id = uuid4()
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO cuentas_de_gastos VALUES(:id,:project,:requester,:beneficiary,NULL)"
            ),
            {
                "id": doc.cuenta_gastos_id,
                "project": pg.projects["Liga"],
                "requester": REQUESTER,
                "beneficiary": OLOF,
            },
        )
        await session.execute(
            text(
                "UPDATE documentos SET torneo_id=NULL,beneficiario_empleado_id=NULL,cuenta_gastos_id=:account WHERE id=:id"
            ),
            {"account": doc.cuenta_gastos_id, "id": doc.id},
        )
        preview = await policy.preview_document_authorization_route(session, doc)
        assert preview["route"].eligible_position_keys == (policy.PROVISIONAL_GN,)
        assert preview["context"]["beneficiary_id"] == str(OLOF)
    assert await authorized(pg, doc) == {GN, GV}


@pytest.mark.asyncio
async def test_provider_beneficiary_does_not_escalate_account_employee(pg_policy):
    pg = pg_policy
    doc = await document(pg, name="Liga", reference=None)
    doc.torneo_id = None
    doc.beneficiario_empleado_id = None
    doc.cuenta_gastos_id = uuid4()
    async with pg.factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO cuentas_de_gastos VALUES(:id,:project,:requester,:beneficiary,:provider)"
            ),
            {
                "id": doc.cuenta_gastos_id,
                "project": pg.projects["Liga"],
                "requester": OLOF,
                "beneficiary": OLOF,
                "provider": uuid4(),
            },
        )
        await session.execute(
            text(
                "UPDATE documentos SET torneo_id=NULL,beneficiario_empleado_id=NULL,cuenta_gastos_id=:account WHERE id=:id"
            ),
            {"account": doc.cuenta_gastos_id, "id": doc.id},
        )
        preview = await policy.preview_document_authorization_route(session, doc)
        assert preview["route"].eligible_position_keys == (policy.DIRECTOR_OPERACIONES,)
        assert preview["context"]["beneficiary_id"] is None
    assert await authorized(pg, doc) == {OPS, GV}
