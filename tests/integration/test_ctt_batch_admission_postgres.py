"""PostgreSQL contracts for governed Copa Telmex batch admission."""

import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_batch_admission_migration_constraints_and_guarded_rollback():
    socket = os.environ.get("SAMCHAT_TEST_PG_SOCKET")
    if not socket:
        pytest.skip("Explicit ephemeral PostgreSQL socket required")
    assert socket.startswith("/tmp/samchat-ctt-pg-"), "Only isolated temporary sockets"

    import asyncpg

    port = int(os.environ.get("SAMCHAT_TEST_PG_PORT", "55439"))
    schema = "test_batch_admission_" + uuid4().hex
    root = Path(__file__).resolve().parents[2]
    edition_up = (
        root / "database/migrations/20261005_copa_telmex_tournament_editions.sql"
    ).read_text()
    admission_up = (
        root / "database/migrations/20261006_copa_telmex_batch_admission.sql"
    ).read_text()
    admission_down = (
        root / "database/migrations/20261006_copa_telmex_batch_admission.rollback.sql"
    ).read_text()
    admin = await asyncpg.connect(
        host=socket, port=port, user="postgres", database="postgres"
    )
    try:
        await admin.execute("CREATE ROLE devnous_user NOLOGIN")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute(
            """
            CREATE TABLE tournaments(id UUID PRIMARY KEY, name TEXT, active BOOLEAN);
            CREATE TABLE copa_telmex_teams(id UUID PRIMARY KEY);
            CREATE TABLE copa_telmex_registration_review_sessions(id UUID PRIMARY KEY);
            """
        )
        await admin.execute(edition_up)
        await admin.execute(admission_up)
        await admin.execute(admission_up)
        assert not await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', "
            "'copa_telmex_registration_batches', 'UPDATE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', "
            "'copa_telmex_registration_batch_documents', 'DELETE')"
        )

        tournament_id, edition_id = uuid4(), uuid4()
        team_id, session_id, batch_id = uuid4(), uuid4(), uuid4()
        await admin.execute(
            "INSERT INTO tournaments VALUES ($1, 'Copa', TRUE)", tournament_id
        )
        await admin.execute(
            """INSERT INTO copa_telmex_tournament_editions
               (id, tournament_id, edition_year, roster_slug)
               VALUES ($1, $2, 2026, 'copa-telmex-2026')""",
            edition_id,
            tournament_id,
        )
        await admin.execute("INSERT INTO copa_telmex_teams VALUES ($1)", team_id)
        await admin.execute(
            "INSERT INTO copa_telmex_registration_review_sessions VALUES ($1)",
            session_id,
        )
        second_edition_id, second_session_id = uuid4(), uuid4()
        await admin.execute(
            """INSERT INTO copa_telmex_tournament_editions
               (id, tournament_id, edition_year, roster_slug)
               VALUES ($1, $2, 2025, 'copa-telmex-2025')""",
            second_edition_id,
            tournament_id,
        )
        await admin.execute(
            "INSERT INTO copa_telmex_registration_review_sessions VALUES ($1)",
            second_session_id,
        )
        await admin.execute(
            """INSERT INTO copa_telmex_registration_batches
               (id, tournament_edition_id, manifest_sha256, document_count,
                admitted_by_user_id)
               VALUES ($1, $2, $3, 1, 'operator-1')""",
            batch_id,
            edition_id,
            "a" * 64,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await admin.execute(
                """INSERT INTO copa_telmex_registration_batch_documents
                   (id, batch_id, tournament_edition_id, document_id,
                    source_filename, pdf_sha256, source_pages,
                    source_pages_sha256, payload_sha256, review_session_id,
                    admission_receipt)
                   VALUES ($1, $2, $3, $4, 'wrong-edition.pdf', $5,
                           '[1]'::jsonb, $6, $7, $8, '{}'::jsonb)""",
                uuid4(),
                batch_id,
                second_edition_id,
                "f" * 64,
                "1" * 64,
                "2" * 64,
                "3" * 64,
                second_session_id,
            )
        await admin.execute(
            """INSERT INTO copa_telmex_registration_batch_documents
               (id, batch_id, tournament_edition_id, document_id, source_filename,
                pdf_sha256, source_pages, source_pages_sha256, payload_sha256,
                review_session_id, admission_receipt)
               VALUES ($1, $2, $3, $4, 'source.pdf', $5, '[1,2]'::jsonb,
                       $6, $7, $8, '{}'::jsonb)""",
            uuid4(),
            batch_id,
            edition_id,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            session_id,
        )
        for slot, role in ((1, "director_tecnico"), (2, "auxiliar")):
            await admin.execute(
                """INSERT INTO copa_telmex_team_staff
                   (id, team_id, staff_slot, role, evidence, governance_state,
                    governance_draft_id, governance_draft_version,
                    governance_decision_id, roster_draft_binding,
                    preauthorization_receipt_id)
                   VALUES ($1, $2, $3, $4, '{}'::jsonb, 'PENDING_FINALITY',
                           'draft', 1, 'decision', 'binding', 'preauth')""",
                uuid4(),
                team_id,
                slot,
                role,
            )

        with pytest.raises(asyncpg.UniqueViolationError):
            await admin.execute(
                """INSERT INTO copa_telmex_registration_batches
                   (id, tournament_edition_id, manifest_sha256, document_count,
                    admitted_by_user_id)
                   VALUES ($1, $2, $3, 1, 'operator-2')""",
                uuid4(),
                edition_id,
                "a" * 64,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            bad_role_team = uuid4()
            await admin.execute(
                "INSERT INTO copa_telmex_teams VALUES ($1)", bad_role_team
            )
            await admin.execute(
                """INSERT INTO copa_telmex_team_staff
                   (id, team_id, staff_slot, role, evidence, governance_state,
                    governance_draft_id, governance_draft_version,
                    governance_decision_id, roster_draft_binding,
                    preauthorization_receipt_id)
                   VALUES ($1, $2, 3, 'auxiliar', '{}'::jsonb, 'PENDING_FINALITY',
                           'draft', 1, 'decision', 'binding', 'preauth')""",
                uuid4(),
                team_id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(
                """INSERT INTO copa_telmex_team_staff
                   (id, team_id, staff_slot, role, evidence, governance_state,
                    governance_draft_id, governance_draft_version,
                    governance_decision_id, roster_draft_binding,
                    preauthorization_receipt_id)
                   VALUES ($1, $2, 1, 'auxiliar', '{}'::jsonb, 'PENDING_FINALITY',
                           'draft', 1, 'decision', 'binding', 'preauth')""",
                uuid4(),
                bad_role_team,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            active_without_finality_team = uuid4()
            await admin.execute(
                "INSERT INTO copa_telmex_teams VALUES ($1)",
                active_without_finality_team,
            )
            await admin.execute(
                """INSERT INTO copa_telmex_team_staff
                   (id, team_id, staff_slot, role, evidence, governance_state,
                    governance_draft_id, governance_draft_version,
                    governance_decision_id, roster_draft_binding,
                    preauthorization_receipt_id)
                   VALUES ($1, $2, 1, 'director_tecnico', '{}'::jsonb, 'ACTIVE',
                           'draft', 1, 'decision', 'binding', 'preauth')""",
                uuid4(),
                active_without_finality_team,
            )

        with pytest.raises(asyncpg.RaiseError, match="owner review required"):
            await admin.execute(admission_down)
        await admin.execute("ROLLBACK")
        assert await admin.fetchval(
            "SELECT to_regclass('copa_telmex_registration_batch_documents') IS NOT NULL"
        )

        await admin.execute("DELETE FROM copa_telmex_registration_batch_documents")
        await admin.execute("DELETE FROM copa_telmex_team_staff")
        await admin.execute(admission_down)
        assert not await admin.fetchval(
            "SELECT to_regclass('copa_telmex_registration_batches') IS NOT NULL"
        )
    finally:
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute("DROP ROLE IF EXISTS devnous_user")
        await admin.close()
