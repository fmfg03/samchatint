"""Recovery owner and direct-SQL negative proofs on isolated owner PostgreSQL."""

import hashlib
import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from devnous.gastos import models
from devnous.gastos.services.amex_direct_common import item_hash
from devnous.gastos.services.amex_direct_intake_service import parse_direct_csv
from devnous.gastos.services.amex_direct_recovery_service import (
    _json_source,
    confirm_direct_period_recovery,
    preview_direct_period_recovery,
)

# isort: split
import test_amex_sat_budget_direct_postgres as direct_fixture

direct_pg = direct_fixture.direct_pg


async def seed_previous_period_omission(
    pg,
    content=None,
    *,
    start=date(2026, 10, 1),
    end=date(2026, 10, 31),
    profile="debit_negative",
):
    """Install valid historical owner data without weakening any SQL guard.

    This explicit test fixture models the previous uploader's period defect;
    the current uploader must reject creating such data from a new upload.
    """
    from devnous.gastos.services.amex_direct_context_service import set_direct_context
    from devnous.gastos.services.expense_service import create_expense_from_data

    content = content or (
        b"Date,Description,Amount\n2026-10-02,Existing,-116.00\n"
        + b"".join(f"2026-09-30,Omitted {n},-10.00\n".encode() for n in range(5))
    )
    rows = parse_direct_csv(content, profile, "MXN")
    file_hash = hashlib.sha256(content).hexdigest()
    async with pg.factory.begin() as session:
        original_rows = []
        for row in rows:
            original = dict(row)
            if not start <= row["date"].date() <= end:
                original.update(
                    status="skipped",
                    movement_kind="skipped",
                    reason="row_outside_declared_period",
                    amount=None,
                )
            original_rows.append(original)
        counts = {
            kind: sum(row["movement_kind"] == kind for row in original_rows)
            for kind in ("debit", "credit", "unknown", "skipped")
        }
        statement = models.AmexStatementImport(
            id=uuid4(),
            workflow_mode="direct",
            card_account_id=pg.card.id,
            employee_id=pg.owner.id,
            period_start=start,
            period_end=end,
            file_sha256=file_hash,
            filename="historical.csv",
            parser_version="amex-direct-csv-v1",
            actor_id=pg.actor.id,
            total_rows=len(rows),
            imported_count=counts["debit"],
            credit_count=counts["credit"],
            unknown_count=counts["unknown"],
            skipped_count=counts["skipped"],
            summary_json={"profile": profile, "currency": "MXN"},
        )
        session.add(statement)
        await session.flush()
        state = models.AmexStatementDirectState(
            import_id=statement.id, source_version=1
        )
        session.add(state)
        session.add(
            models.AmexStatementSourceFile(
                import_id=statement.id,
                content=content,
                file_sha256=file_hash,
                byte_size=len(content),
                filename="historical.csv",
                mime_type="text/csv",
                actor_id=pg.actor.id,
            )
        )
        # Canonical import -> global -> expense/folio order, including fixtures.
        from devnous.gastos.services.amex_recognition_service import lock_recognition

        await lock_recognition(session)
        for row in original_rows:
            expense = None
            if row["movement_kind"] == "debit":
                expense = await create_expense_from_data(
                    session=session,
                    concepto=row["description"],
                    gasto_cantidad=float(row["amount"]),
                    fecha=row["date"],
                    empleado_id=pg.owner.id,
                    nombre_enviador=pg.owner.nombre,
                    tipo_gasto="manual",
                    metodo_pago="TARJETA CREDITO AMEX",
                    ultimos_4_digitos=pg.card.last4,
                    origen="amex_batch",
                )
                expense.pagado_con_amex_empresa = True
                expense.currency = "MXN"
                await session.flush()
            source = _json_source(row, file_hash)
            receipt = models.AmexStatementImportItem(
                id=uuid4(),
                import_id=statement.id,
                workflow_mode="direct",
                row_number=row["row_number"],
                imported_expense_id=expense.id if expense else None,
                status=row["status"],
                reason_code=row["reason"],
                normalized_json=source,
            )
            session.add(receipt)
            await session.flush()
            item = models.AmexDirectItem(
                id=uuid4(),
                import_id=statement.id,
                import_item_id=receipt.id,
                imported_expense_id=expense.id if expense else None,
                movement_kind=row["movement_kind"],
                raw_signed_amount=row["raw_signed_amount"],
                raw_amount=row["raw_amount"],
                amount_column=row["amount_column"],
                profile=profile,
                source_json=source,
                source_version=1,
                profile_version="amex-direct-csv-v1",
                match_status="pending",
                budget_status="unclassified",
                accounting_status="pending",
                credit_excluded=False,
                source_hash="",
            )
            item.source_hash = item_hash(item)
            session.add(item)
        await session.flush()
        result = await set_direct_context(
            session,
            actor_id=pg.actor.id,
            import_id=statement.id,
            company_code="TEST",
            receiver_rfc="BBB010101BBB",
            currency="MXN",
            profile=profile,
            expected_source_version=1,
            reason="",
            confirmed=True,
        )
        assert result.status != "pending", result.reason
        return statement.id, state.source_version


async def recovery_preview(pg, import_id, version):
    async with pg.factory() as session:
        return await preview_direct_period_recovery(
            session,
            actor_id=pg.actor.id,
            import_id=import_id,
            period_start=date(2026, 9, 30),
            period_end=date(2026, 10, 31),
            reason="Correct declared coverage against retained original",
            expected_source_version=version,
        )


async def recovery_confirm(pg, import_id, version, preview_hash, **overrides):
    args = dict(
        actor_id=pg.actor.id,
        import_id=import_id,
        period_start=date(2026, 9, 30),
        period_end=date(2026, 10, 31),
        reason="Correct declared coverage against retained original",
        expected_source_version=version,
        preview_hash=preview_hash,
        idempotency_key="recovery-proof",
        confirmed=True,
    )
    args.update(overrides)
    async with pg.factory.begin() as session:
        return await confirm_direct_period_recovery(session, **args)


@pytest.mark.asyncio
async def test_recovery_exact_sources_parity_and_original_anchors_unchanged(direct_pg):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory() as session:
        before = (
            await session.execute(
                text(
                    (
                        "SELECT row_to_json(h)::text FROM amex_statement_im"
                        "ports h WHERE id=:id"
                    )
                ),
                {"id": import_id},
            )
        ).scalar_one()
        originals = (
            await session.execute(
                text(
                    (
                        "SELECT id,imported_expense_id,source_json FROM ame"
                        "x_direct_items WHERE import_id=:id ORDER BY id"
                    )
                ),
                {"id": import_id},
            )
        ).all()
    preview = await recovery_preview(pg, import_id, version)
    assert preview.status == "preview", preview.reason
    assert preview.progress["expected_count"] == 5
    result = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"]
    )
    assert result.status == "recovered"
    from devnous.gastos.services.amex_direct_effective_source_service import (
        load_effective_sources,
    )

    async with pg.factory() as session:
        after = (
            await session.execute(
                text(
                    (
                        "SELECT row_to_json(h)::text FROM amex_statement_im"
                        "ports h WHERE id=:id"
                    )
                ),
                {"id": import_id},
            )
        ).scalar_one()
        assert before == after
        assert (
            originals
            == (
                await session.execute(
                    text(
                        (
                            "SELECT id,imported_expense_id,source_json FROM ame"
                            "x_direct_items WHERE import_id=:id ORDER BY id"
                        )
                    ),
                    {"id": import_id},
                )
            ).all()
        )
        sources = await load_effective_sources(session, import_id)
        assert len(sources) == 6
        assert all(
            s.movement_kind == "debit" and s.imported_expense_id
            for s in sources.values()
        )
        assert sum(s.recovery_id is not None for s in sources.values()) == 5
    replay = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"]
    )
    assert replay.status == "exists"
    conflict = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"], reason="Other reason"
    )
    assert conflict.reason == "recovery_idempotency_conflict"


@pytest.mark.asyncio
async def test_recovery_schema_snapshot(direct_pg):
    from pathlib import Path

    pg = direct_pg
    rows = await pg.conn.fetch(
        (
            "SELECT pg_get_viewdef('amex_direct_effective_sourc"
            "es'::regclass,true) definition"
        )
    )
    Path("/tmp/amex-recovery-view-definition.txt").write_text(rows[0]["definition"])
    rows = await pg.conn.fetch(
        (
            "SELECT c.relname,k.conname,k.contype::text,pg_get_"
            "constraintdef(k.oid) definition FROM pg_constraint"
            " k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_nam"
            "espace n ON n.oid=c.relnamespace WHERE n.nspname=c"
            "urrent_schema() AND c.relname IN ('amex_direct_cov"
            "erage_revisions','amex_direct_recovered_rows') ORD"
            "ER BY c.relname,k.conname"
        )
    )
    Path("/tmp/amex-recovery-catalog.json").write_text(
        json.dumps([dict(r) for r in rows])
    )
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    async with pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert result["ready"], result


async def _insert_revision_only(session, pg, import_id, version):
    from devnous.gastos.services.amex_direct_recovery_service import _plan

    statement, _, _, source, recoverable, plan = await _plan(
        session,
        actor_id=pg.actor.id,
        import_id=import_id,
        period_start=date(2026, 9, 30),
        period_end=date(2026, 10, 31),
        reason="Direct SQL negative proof",
        expected_source_version=version,
    )
    revision = models.AmexDirectCoverageRevision(
        id=uuid4(),
        import_id=import_id,
        source_file_id=source.id,
        file_sha256=source.file_sha256,
        parser_version=statement.parser_version,
        profile=plan["profile"],
        original_period_start=statement.period_start,
        original_period_end=statement.period_end,
        period_start=date(2026, 9, 30),
        period_end=date(2026, 10, 31),
        original_counts=plan["original_counts"],
        effective_counts=plan["effective_counts"],
        expected_item_ids=plan["expected_item_ids"],
        expected_count=plan["expected_count"],
        expected_source_version=version,
        actor_id=pg.actor.id,
        reason="Direct SQL negative proof",
        idempotency_key="sql-negative",
        request_hash="a" * 64,
        preview_hash=plan["preview_hash"],
        membership_digest=plan["membership_digest"],
    )
    session.add(revision)
    await session.flush()
    return revision, recoverable


async def _insert_recovered_row(session, pg, revision, triple, **overrides):
    from devnous.gastos.services.expense_service import create_expense_from_data

    item, receipt, row = triple
    expense = await create_expense_from_data(
        session=session,
        concepto=row["description"],
        gasto_cantidad=float(row["amount"]),
        fecha=row["date"],
        empleado_id=pg.owner.id,
        nombre_enviador=pg.owner.nombre,
        tipo_gasto="manual",
        metodo_pago="TARJETA CREDITO AMEX",
        ultimos_4_digitos=pg.card.last4,
        origen="amex_batch",
    )
    expense.pagado_con_amex_empresa = True
    expense.currency = "MXN"
    await session.flush()
    source = _json_source(row, revision.file_sha256)
    proof_hash = (
        await session.execute(
            text(
                (
                    "SELECT encode(sha256(convert_to(CAST(:source AS js"
                    "onb)::text,'UTF8')),'hex')"
                )
            ),
            {"source": json.dumps(source)},
        )
    ).scalar_one()
    args = dict(
        id=uuid4(),
        revision_id=revision.id,
        direct_item_id=item.id,
        import_item_id=receipt.id,
        imported_expense_id=expense.id,
        row_number=receipt.row_number,
        movement_kind="debit",
        economic_date=row["date"].date(),
        currency="MXN",
        raw_signed_amount=row["raw_signed_amount"],
        raw_amount=row["raw_amount"],
        amount_column=row["amount_column"],
        profile=row["profile"],
        effective_source_json=source,
        proof_hash=proof_hash,
    )
    args.update(overrides)
    session.add(models.AmexDirectRecoveredRow(**args))
    await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [0, 4, 6])
async def test_sql_incomplete_or_extra_recovery_rolls_back_all_anchors(
    direct_pg, row_count
):
    from sqlalchemy.exc import DBAPIError

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory() as session:
        before = (
            await session.execute(text("SELECT count(*) FROM expense_reports"))
        ).scalar_one()
    with pytest.raises(DBAPIError, match="Recovery"):
        async with pg.factory.begin() as session:
            revision, rows = await _insert_revision_only(
                session, pg, import_id, version
            )
            for number in range(row_count):
                await _insert_recovered_row(
                    session, pg, revision, rows[number % len(rows)]
                )
            state = await session.get(models.AmexStatementDirectState, import_id)
            state.source_version += 1
            await session.flush()
    async with pg.factory() as session:
        assert (
            await session.execute(text("SELECT count(*) FROM expense_reports"))
        ).scalar_one() == before
        assert (
            await session.execute(
                text("SELECT count(*) FROM amex_direct_coverage_revisions")
            )
        ).scalar_one() == 0
        assert (
            await session.execute(
                text("SELECT count(*) FROM amex_direct_recovered_rows")
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"raw_signed_amount": -11},
        {"raw_amount": "-11.00"},
        {"economic_date": date(2026, 9, 29)},
        {"profile": "debit_positive"},
        {"proof_hash": "b" * 64},
        {"row_number": 999},
    ],
)
async def test_sql_forged_recovery_economics_rejected_atomically(direct_pg, changes):
    from sqlalchemy.exc import DBAPIError

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    with pytest.raises(DBAPIError, match="Recovery"):
        async with pg.factory.begin() as session:
            revision, rows = await _insert_revision_only(
                session, pg, import_id, version
            )
            await _insert_recovered_row(session, pg, revision, rows[0], **changes)
    async with pg.factory() as session:
        assert (
            await session.execute(
                text("SELECT count(*) FROM amex_direct_coverage_revisions")
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_stale_or_changed_preview_cannot_create_recovery(direct_pg):
    from devnous.gastos.services.amex_direct_common import DirectConflict

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    with pytest.raises(DirectConflict, match="recovery_preview_changed"):
        await recovery_confirm(pg, import_id, version, "0" * 64)
    stale = await recovery_preview(pg, import_id, version - 1)
    assert stale.reason == "source_version_conflict"
    success = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"]
    )
    assert success.status == "recovered"


@pytest.mark.asyncio
async def test_concurrent_recovery_same_request_is_one_atomic_revision(direct_pg):
    import asyncio

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    results = await asyncio.gather(
        *(
            recovery_confirm(pg, import_id, version, preview.progress["preview_hash"])
            for _ in range(2)
        )
    )
    assert sorted(result.status for result in results) == ["exists", "recovered"]
    async with pg.factory() as session:
        assert (
            await session.execute(
                text("SELECT count(*) FROM amex_direct_recovered_rows")
            )
        ).scalar_one() == 5
        assert (
            await session.execute(text("SELECT count(*) FROM expense_reports"))
        ).scalar_one() == 6


@pytest.mark.asyncio
async def test_changed_effective_view_fails_schema_admission(direct_pg):
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    pg = direct_pg
    original = await pg.conn.fetchval(
        "SELECT pg_get_viewdef('amex_direct_effective_sources'::regclass,true)"
    )
    await pg.conn.execute(
        "CREATE OR REPLACE VIEW amex_direct_effective_sources AS "
        + original.replace(
            "LEFT JOIN amex_direct_recovered_rows rr ON rr.direct_item_id = d.id",
            (
                "LEFT JOIN amex_direct_recovered_rows rr ON rr.impo"
                "rt_item_id = d.import_item_id"
            ),
        )
    )
    async with pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert not result["ready"]
    assert any("view definition" in error for error in result["schema_errors"])


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["revision", "row", "expense"])
async def test_recovery_evidence_and_bank_identity_are_immutable(direct_pg, target):
    from sqlalchemy.exc import DBAPIError

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    await recovery_confirm(pg, import_id, version, preview.progress["preview_hash"])
    statements = {
        "revision": "UPDATE amex_direct_coverage_revisions SET reason='changed'",
        "row": "UPDATE amex_direct_recovered_rows SET raw_signed_amount=-12",
        "expense": (
            "UPDATE expense_reports SET gasto_cantidad=12 WHERE"
            " id IN (SELECT imported_expense_id FROM amex_direc"
            "t_recovered_rows)"
        ),
    }
    with pytest.raises(DBAPIError):
        async with pg.factory.begin() as session:
            await session.execute(text(statements[target]))


@pytest.mark.asyncio
async def test_empty_schema_rollback_restores_original_guards(
    direct_pg,
):
    from pathlib import Path

    pg = direct_pg
    root = Path(__file__).resolve().parents[2]
    await pg.conn.execute(
        (
            root
            / "database/migrations/20261015_amex_direct_period_recovery_rollback.sql"
        ).read_text()
    )
    assert (
        await pg.conn.fetchval("SELECT to_regclass('amex_direct_recovered_rows')")
        is None
    )
    assert (
        await pg.conn.fetchval("SELECT to_regclass('amex_direct_effective_sources')")
        is None
    )
    body = await pg.conn.fetchval(
        (
            "SELECT prosrc FROM pg_proc WHERE oid='amex_direct_"
            "consumption_guard()'::regprocedure"
        )
    )
    assert "FROM amex_direct_items" in body
    assert "amex_direct_effective_sources" not in body


@pytest.mark.asyncio
async def test_populated_schema_cannot_downgrade_or_erase_recovery(direct_pg):
    from pathlib import Path

    import asyncpg

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    await recovery_confirm(pg, import_id, version, preview.progress["preview_hash"])
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(asyncpg.PostgresError, match="downgrade is prohibited"):
        await pg.conn.execute(
            (
                root
                / (
                    "database/migrations/20261015_amex_direct_period_re"
                    "covery_rollback.sql"
                )
            ).read_text()
        )
    await pg.conn.execute("ROLLBACK")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_recovered_rows") == 5
    )


@pytest.mark.asyncio
async def test_recovery_explicit_permission_denial_precedes_content_read(direct_pg):
    from devnous.gastos.services.amex_direct_common import DirectAccessError

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory.begin() as session:
        session.add(
            models.AccessControlRule(
                id=uuid4(),
                tool_key="amex_direct",
                action_key="corregir_periodo",
                role_key="finanzas",
                area_key="",
                allowed=False,
                active=True,
            )
        )
    with pytest.raises(DirectAccessError, match="explicit_action_denied"):
        await recovery_preview(pg, import_id, version)


@pytest.mark.asyncio
async def test_omitted_credit_or_unproven_date_cannot_be_recovered(direct_pg):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(
        pg,
        (
            b"Date,Description,Amount\n2026-10-02,Existing,-116.00\n"
            b"2026-09-30,Payment,10.00\n"
        ),
    )
    preview = await recovery_preview(pg, import_id, version)
    assert preview.reason == "recovery_only_period_debits_supported"


@pytest.mark.asyncio
async def test_prior_unanchored_expense_cannot_be_reused_as_recovery_source(direct_pg):
    from datetime import datetime

    from sqlalchemy.exc import DBAPIError

    from devnous.gastos.services.expense_service import create_expense_from_data

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory.begin() as session:
        old = await create_expense_from_data(
            session=session,
            concepto="Previously existing unrelated expense",
            gasto_cantidad=10,
            fecha=datetime(2026, 9, 30),
            empleado_id=pg.owner.id,
            nombre_enviador=pg.owner.nombre,
            tipo_gasto="manual",
            metodo_pago="TARJETA CREDITO AMEX",
            ultimos_4_digitos=pg.card.last4,
            origen="amex_batch",
        )
        old.pagado_con_amex_empresa = True
        old.currency = "MXN"
        await session.flush()
        old_id = old.id
    with pytest.raises(DBAPIError, match="fresh exact bank anchor"):
        async with pg.factory.begin() as session:
            revision, rows = await _insert_revision_only(
                session, pg, import_id, version
            )
            await _insert_recovered_row(
                session, pg, revision, rows[0], imported_expense_id=old_id
            )


@pytest.mark.asyncio
async def test_existing_cfdi_on_omitted_source_blocks_recovery_service_and_sql(
    direct_pg,
):
    from sqlalchemy.exc import DBAPIError
    from test_amex_sat_budget_direct_postgres import _synthetic_sat_xml

    from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory.begin() as session:
        ingested = await ingest_cfdi_xml(session, _synthetic_sat_xml(), source="sat")
        await session.flush()
        await session.execute(
            text(
                "UPDATE amex_direct_items SET cfdi_id=:cfdi WHERE id=(SELECT id FROM "
                "amex_direct_items WHERE import_id=:id "
                "AND movement_kind='skipped' LIMIT 1)"
            ),
            {"cfdi": ingested.cfdi_report.id, "id": import_id},
        )
    preview = await recovery_preview(pg, import_id, version)
    assert preview.reason == "recovery_source_effect_conflict"
    # SQL owner rejects a contradictory link even if caller supplies its own preview.
    async with pg.factory() as session:
        affected = (
            await session.execute(
                text(
                    "SELECT id FROM amex_direct_items WHERE import_id=:id "
                    "AND cfdi_id IS NOT NULL"
                ),
                {"id": import_id},
            )
        ).scalar_one()
    with pytest.raises(DBAPIError, match="contradictory existing effects"):
        async with pg.factory.begin() as session:
            # Build a revision from unmodified source material; the SQL row guard
            # must independently inspect the physical source's current effects.
            await session.execute(
                text("UPDATE amex_direct_items SET cfdi_id=NULL WHERE id=:id"),
                {"id": affected},
            )
            revision, rows = await _insert_revision_only(
                session, pg, import_id, version
            )
            await session.execute(
                text("UPDATE amex_direct_items SET cfdi_id=:cfdi WHERE id=:id"),
                {"id": affected, "cfdi": ingested.cfdi_report.id},
            )
            triple = next(triple for triple in rows if triple[0].id == affected)
            await _insert_recovered_row(session, pg, revision, triple)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period_start,period_end,reason,expected",
    [
        (
            date(2026, 10, 2),
            date(2026, 10, 31),
            "Reason",
            "recovery_period_must_contain_original",
        ),
        (
            date(2026, 9, 30),
            date(2026, 10, 30),
            "Reason",
            "recovery_period_must_contain_original",
        ),
        (date(2026, 9, 30), date(2026, 10, 31), " ", "recovery_reason_required"),
        (
            date(2026, 10, 1),
            date(2026, 10, 31),
            "Reason",
            "recovery_period_excludes_file",
        ),
    ],
)
async def test_recovery_bad_declaration_has_no_business_effects(
    direct_pg, period_start, period_end, reason, expected
):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory() as session:
        preview = await preview_direct_period_recovery(
            session,
            actor_id=pg.actor.id,
            import_id=import_id,
            period_start=period_start,
            period_end=period_end,
            reason=reason,
            expected_source_version=version,
        )
        assert preview.reason == expected
        assert (
            await session.execute(
                text("SELECT count(*) FROM amex_direct_coverage_revisions")
            )
        ).scalar_one() == 0
        assert (
            await session.execute(text("SELECT count(*) FROM expense_reports"))
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_missing_confirmation_cannot_create_recovery(direct_pg):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    blocked = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"], confirmed=False
    )
    assert blocked.reason == "recovery_confirmation_required"


async def _legacy_claim(session, pg, import_id):
    expense_id = (
        await session.execute(
            text(
                "SELECT imported_expense_id FROM amex_direct_items WHERE import_id=:id "
                "AND movement_kind='debit' LIMIT 1"
            ),
            {"id": import_id},
        )
    ).scalar_one()
    session.add(
        models.AmexRecognitionConsumption(
            id=uuid4(),
            source_kind="statement",
            binding_mode="legacy",
            imported_expense_id=expense_id,
            card_account_id=pg.card.id,
            amount=116,
            currency="MXN",
            economic_date=date(2026, 10, 2),
            liability_account_id=pg.liability.id,
            liability_code=pg.liability.codigo,
            classification_json={},
            actor_id=pg.actor.id,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_prior_claim_blocks_recovery_before_any_cut(direct_pg):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    async with pg.factory.begin() as session:
        await _legacy_claim(session, pg, import_id)
    preview = await recovery_preview(pg, import_id, version)
    assert preview.reason == "recovery_recognized_source"


@pytest.mark.asyncio
async def test_sql_claim_created_between_revision_and_rows_rejects_atomically(
    direct_pg,
):
    from sqlalchemy.exc import DBAPIError

    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    with pytest.raises(DBAPIError, match="recognized sources"):
        async with pg.factory.begin() as session:
            revision, rows = await _insert_revision_only(
                session, pg, import_id, version
            )
            await _legacy_claim(session, pg, import_id)
            await _insert_recovered_row(session, pg, revision, rows[0])
