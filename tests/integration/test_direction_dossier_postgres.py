"""Integration contracts for scoped dossier capture and guarded migration rollback."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive.dossier_records import (
    DossierRecordError,
    capture_permissions,
    known_editions,
    read_domains,
    read_records,
    save_record,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "database" / "migrations"


@pytest.mark.asyncio
async def test_direction_dossier_migration_scope_audit_and_rollback(dossier_postgres):
    sqlalchemy_dsn, postgres_connect = dossier_postgres
    admin = await asyncpg.connect(**postgres_connect)
    schema = f"dossier_test_{uuid4().hex}"
    project, another_project, employee, proof, next_year = (uuid4() for _ in range(5))
    up = (MIGRATIONS / "20261118_direction_living_dossier.sql").read_text()
    down = (MIGRATIONS / "20261118_direction_living_dossier.rollback.sql").read_text()
    engine = create_async_engine(
        sqlalchemy_dsn,
        connect_args={**postgres_connect, "server_settings": {"search_path": schema}},
    )
    try:
        await admin.execute(
            "DO $$ BEGIN CREATE ROLE devnous_user NOLOGIN; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO devnous_user')
        await admin.execute("""
            CREATE TABLE tournaments (id uuid PRIMARY KEY, active boolean NOT NULL);
            CREATE TABLE empleados (id uuid PRIMARY KEY);
            CREATE TABLE documentos (
                id uuid PRIMARY KEY, torneo_id uuid REFERENCES tournaments(id),
                edicion int, fase text, tipo text, fecha_pago_efectiva date, categorias jsonb
            );
            GRANT SELECT, UPDATE ON documentos, tournaments TO devnous_user;
            """)
        await admin.execute(
            (MIGRATIONS / "20261005_copa_telmex_tournament_editions.sql").read_text()
        )
        await admin.execute(
            "GRANT SELECT ON copa_telmex_tournament_editions TO devnous_user"
        )
        await admin.executemany(
            "INSERT INTO tournaments VALUES ($1, TRUE)",
            [(project,), (another_project,)],
        )
        await admin.execute("INSERT INTO empleados VALUES ($1)", employee)
        await admin.executemany(
            "INSERT INTO copa_telmex_tournament_editions "
            "(id, tournament_id, edition_year, roster_slug) "
            "VALUES ($1, $2, $3, $4)",
            [
                (uuid4(), project, 2026, "ctt-2026"),
                (uuid4(), project, 2027, "ctt-2027"),
            ],
        )
        await admin.execute(
            """
            INSERT INTO documentos VALUES
                ($1, $3, 2026, 'Nacional', 'SOLICITUD', DATE '2027-01-15', '["Juvenil"]'::jsonb),
                ($2, $3, 2027, 'Nacional', 'SOLICITUD', NULL, NULL)
            """,
            proof,
            next_year,
            project,
        )
        session_factory = async_sessionmaker(engine)
        async with session_factory() as session:
            uncaptured = await read_records(
                session,
                tournament_id=str(project),
                edition_year=2026,
                allowed_domains={"finance"},
            )
            assert uncaptured == {"status": "unavailable", "rows": []}
        await admin.execute(up)
        await admin.execute(up)
        await admin.execute(down)
        await admin.execute(down)
        await admin.execute(up)
        assert not await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', 'direction_dossier_records', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', 'direction_dossier_revisions', 'UPDATE')"
        )
        assert not await admin.fetchval(
            "SELECT has_column_privilege('devnous_user', 'direction_dossier_records', "
            "'edition_year', 'UPDATE')"
        )
        request_id = str(uuid4())
        payload = dict(
            tournament_id=str(project),
            edition_year=2026,
            employee_id=str(employee),
            scope={"phase": "nacional", "entity": "", "category": "Juvenil"},
            kind="financial_link",
            fields={"document_id": str(proof), "concept": "hotel_anticipo"},
            evidence_reference="Referencia de Finanzas aprobada",
            request_id=request_id,
        )
        async with session_factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            assert await known_editions(session, str(project)) == [2027, 2026]
            with pytest.raises(DossierRecordError, match="categoría"):
                await save_record(
                    session,
                    **{**payload, "scope": {**payload["scope"], "category": "Varonil"}},
                )
            original = await save_record(session, **payload)
            assert await save_record(session, **payload) == original
            assert original["id"] == request_id and original["revision"] == 1
            with pytest.raises(DossierRecordError, match="no coincide"):
                await save_record(
                    session,
                    **{
                        **payload,
                        "fields": {
                            **payload["fields"],
                            "document_id": str(next_year),
                        },
                    },
                )
            await session.commit()
        async with session_factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            assert (
                await read_records(
                    session,
                    tournament_id=str(project),
                    edition_year=2026,
                    allowed_domains={"operations"},
                )
            )["rows"] == []
            assert (
                await read_records(
                    session,
                    tournament_id=str(project),
                    edition_year=2027,
                    allowed_domains={"finance"},
                )
            )["rows"] == []
            assert (
                await read_records(
                    session,
                    tournament_id=str(another_project),
                    edition_year=2026,
                    allowed_domains={"finance"},
                )
            )["rows"] == []
            visible = await read_records(
                session,
                tournament_id=str(project),
                edition_year=2026,
                allowed_domains={"finance"},
            )
            assert [row["id"] for row in visible["rows"]] == [request_id]
            changed_payload = {
                **payload,
                "record_id": request_id,
                "expected_revision": 1,
                "evidence_reference": "Referencia corregida",
                "fields": {**payload["fields"], "concept": "hotel_liquidacion"},
            }
            edited = await save_record(session, **changed_payload)
            assert edited["revision"] == 2
            with pytest.raises(DossierRecordError, match="Recarga"):
                await save_record(session, **changed_payload)
            await session.commit()
        assert (
            await admin.fetchval("SELECT COUNT(*) FROM direction_dossier_revisions")
            == 2
        )
        assert (
            await admin.fetchval("SELECT edicion FROM documentos WHERE id=$1", proof)
            == 2026
        )
        assert (
            await admin.fetchval(
                "SELECT fecha_pago_efectiva::text FROM documentos WHERE id=$1", proof
            )
            == "2027-01-15"
        )
        with pytest.raises(asyncpg.RaiseError, match="Dossier records exist"):
            await admin.execute(down)
        await admin.execute("ROLLBACK")
        assert (
            await admin.fetchval("SELECT COUNT(*) FROM direction_dossier_records") == 1
        )
        refs = await admin.fetch(
            "SELECT evidence_reference FROM direction_dossier_revisions ORDER BY revision"
        )
        assert [row["evidence_reference"] for row in refs] == [
            "Referencia de Finanzas aprobada",
            "Referencia corregida",
        ]
        snapshots = await admin.fetch(
            "SELECT snapshot FROM direction_dossier_revisions ORDER BY revision"
        )
        import json

        prior, current = [json.loads(row["snapshot"]) for row in snapshots]
        assert prior["fields"]["concept"] == "hotel_anticipo"
        assert current["fields"]["concept"] == "hotel_liquidacion"
        assert prior["document_id"] == current["document_id"] == str(proof)
        assert prior["category"] == current["category"] == "Juvenil"
        assert prior["revision"] == 1 and current["revision"] == 2
        await admin.execute("DELETE FROM documentos WHERE id = $1", proof)

        async with session_factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            binding = {
                "tournament_id": str(project),
                "edition_year": 2026,
                "employee_id": str(employee),
                "scope": {"phase": "nacional", "entity": "", "category": ""},
                "kind": "operations_edition",
                "fields": {"operations_tournament_id": str(project)},
                "evidence_reference": "Calendario y folio de la edición verificados",
            }
            with pytest.raises(DossierRecordError, match="acredita"):
                await save_record(
                    session,
                    **{
                        **binding,
                        "fields": {"operations_tournament_id": str(another_project)},
                    },
                )
            await save_record(session, **binding)
            await session.commit()
        async with session_factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            assert (
                await known_editions(
                    session,
                    str(project),
                    finance_allowed=False,
                    operations_allowed=False,
                )
                == []
            )
            with pytest.raises(DossierRecordError, match="vínculo"):
                await save_record(session, **{**binding, "edition_year": 2027})
            await session.rollback()
        assert (
            await admin.fetchval("SELECT COUNT(*) FROM direction_dossier_records") == 2
        )
    finally:
        await engine.dispose()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_dossier_permissions_require_explicit_rule(monkeypatch):
    from devnous.gastos.services.access_control_service import AccessControlLookupError
    from samchat.client_executive import dossier_records

    calls = []

    async def explicit(_session, _employee, key, action):
        calls.append((key, action))
        return {
            "direccion.expediente_contactos": True,
            "direccion.expediente_fotografias": False,
        }.get(key)

    monkeypatch.setattr(dossier_records, "explicit_tool_decision", explicit)
    assert await capture_permissions(None, object(), action="ver") == {"contacts"}
    assert {action for _, action in calls} == {"ver"}
    assert await read_domains(None, object()) == {
        "operations",
        "finance",
        "marketing",
        "contacts",
    }

    async def unavailable(*_args):
        raise AccessControlLookupError("detached")

    monkeypatch.setattr(dossier_records, "explicit_tool_decision", unavailable)
    assert await capture_permissions(None, object()) == set()
    assert await read_domains(None, object()) == set()
