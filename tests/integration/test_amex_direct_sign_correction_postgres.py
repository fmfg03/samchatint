"""Independent acceptance of audited sign correction on disposable PostgreSQL.

The 16-row fixture preserves the reported economic pattern without private data.
Business owners and SQL guards are real; no production connections are allowed.
"""

import asyncio
import importlib.util
from collections import Counter
from contextlib import asynccontextmanager
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import asyncpg
import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos import models
from devnous.gastos.routes import admin_amex_direct_routes as routes
from devnous.gastos.routes import dependencies
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_no_cfdi_service as no_cfdi
from devnous.gastos.services import amex_direct_sign_correction_service as correction
from devnous.gastos.services.amex_direct_common import DirectAccessError, DirectConflict

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sign_correction_acceptance_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)
direct_pg = owner.direct_pg


def original_csv():
    amounts = [Decimal("600.00")] * 16
    amounts[0] = Decimal("608.10")
    amounts[4] = Decimal("939.00")
    amounts[8] = Decimal("-46.53")
    amounts[15] = Decimal("-17310.87")
    rows = ["Date,Description,Amount"]
    for index, amount in enumerate(amounts):
        descriptor = {4: "GOOGLE MAPXPLORER", 8: "AMAZON REFUND"}.get(
            index, f"SYNTHETIC BANK ROW {index + 1}"
        )
        rows.append(f"2026-10-25,{descriptor},{amount:.2f}")
    return ("\n".join(rows) + "\n").encode()


async def imported(pg):
    result = await owner._direct_import(pg, original_csv())
    assert result.status in {"created", "imported"}, result.reason
    view = await owner._direct_current(pg, result.statement.id)
    assert Counter(i.movement_kind for i in view.items) == {"debit": 2, "credit": 14}
    return view


async def preview(pg, view):
    request = dict(
        actor_id=pg.actor.id,
        import_id=view.statement.id,
        profile="debit_positive",
        reason="Explicit original sign criterion corrected by Finance",
        expected_source_version=view.source_version,
    )
    async with pg.factory() as session:
        result = await correction.preview_direct_sign_correction(session, **request)
    assert result.status == "preview", result.reason
    return request, result


async def confirm(pg, request, planned, key="acceptance-sign-correction"):
    async with pg.factory.begin() as session:
        return await correction.confirm_direct_sign_correction(
            session,
            **request,
            preview_hash=planned.progress["preview_hash"],
            idempotency_key=key,
            confirmed=True,
        )


async def physical(pg):
    return [
        tuple(row)
        for row in await pg.conn.fetch(
            "SELECT id,import_id,import_item_id,movement_kind,profile,"
            "imported_expense_id,raw_signed_amount,source_json::text "
            "FROM amex_direct_items ORDER BY id"
        )
    ]


class Checkboxes(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.selected_ids = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "checkbox":
            if values.get("name") == "item_ids":
                self.selected_ids.add(values.get("value"))


@asynccontextmanager
async def http_client(pg):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-sign-acceptance")

    async def session_dependency():
        async with pg.factory() as session:
            yield session
            await session.rollback()

    async def actor_dependency(session=Depends(dependencies.get_db_session)):
        return await session.get(models.Empleado, pg.actor.id)

    app.dependency_overrides[dependencies.get_db_session] = session_dependency
    app.dependency_overrides[dependencies.get_current_empleado] = actor_dependency
    pg.monkeypatch.setattr(dependencies, "_db_session_maker", pg.factory)
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_real_16_rows_atomic_cancellation_replay_http_policy_and_export(
    direct_pg,
):
    pg = direct_pg
    view = await imported(pg)
    original = await physical(pg)
    original_expenses = {
        i.imported_expense_id for i in view.items if i.imported_expense_id
    }
    assert len(original_expenses) == 2
    request, planned = await preview(pg, view)
    assert await physical(pg) == original
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2
    result = await confirm(pg, request, planned)
    assert result.status == "corrected", result.reason
    replay = await confirm(pg, request, planned)
    assert replay.status == "exists", replay.reason
    assert await physical(pg) == original
    view = await owner._direct_current(pg, view.statement.id)
    assert Counter(i.movement_kind for i in view.items) == {"debit": 14, "credit": 2}
    debits = [i for i in view.items if i.movement_kind == "debit"]
    credits = [i for i in view.items if i.movement_kind == "credit"]
    assert sum(i.amount for i in debits) == Decimal("8747.10")
    assert sum(abs(i.raw_signed_amount) for i in credits) == Decimal("17357.40")
    assert all(i.imported_expense_id for i in debits)
    assert all(i.imported_expense_id is None for i in credits)
    assert original_expenses.isdisjoint({i.imported_expense_id for i in debits})
    async with pg.factory() as session:
        expenses = (await session.execute(select(models.ExpenseReport))).scalars().all()
        assert len(expenses) == 16
        cancelled = [i for i in expenses if i.estado_gasto == "cancelado"]
        assert {i.id for i in cancelled} == original_expenses
        assert all(
            i.cancelado_en and i.cancelado_por_id == pg.actor.id for i in cancelled
        )
        assert all(i.motivo_cancelacion for i in cancelled)
    google = next(i for i in view.items if "GOOGLE" in i.descriptor)
    amazon = next(i for i in view.items if "AMAZON" in i.descriptor)
    assert google.movement_kind == "debit" and google.raw_signed_amount == 939
    assert amazon.movement_kind == "credit" and amazon.raw_signed_amount == Decimal(
        "-46.53"
    )
    async with http_client(pg) as client:
        response = await client.get(f"{routes.BASE}/{view.statement.id}/directo")
        assert response.status_code == 200, response.text
        checkboxes = Checkboxes(response.text).selected_ids
        assert str(google.id) in checkboxes
        assert str(amazon.id) not in checkboxes
        assert len(checkboxes) == 14
    replay_import = await owner._direct_import(pg, original_csv())
    assert replay_import.statement.id == view.statement.id
    corrected_profile_replay = await owner._direct_import(
        pg, original_csv(), profile="debit_positive"
    )
    assert corrected_profile_replay.statement.id == view.statement.id
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_statement_imports") == 1
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 16
    async with pg.factory.begin() as session:
        declared = await no_cfdi.declare_no_cfdi_items(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=[i.id for i in debits],
            expected_versions={str(i.id): i.source_version for i in debits},
            expected_source_version=view.source_version,
            reason="Synthetic Finance explicit no invoice evidence",
            request_key="sign-correction-no-cfdi",
            confirmed=True,
        )
        assert declared.status == "ready", declared.reason
    from devnous.gastos.services.amex_direct_matching_service import (
        exclude_direct_credit,
    )

    for credit in credits:
        view = await owner._direct_current(pg, view.statement.id)
        async with pg.factory.begin() as session:
            excluded = await exclude_direct_credit(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=credit.id,
                expected_source_version=view.source_version,
                reason="Explicit synthetic credit exclusion, no expense",
                confirmed=True,
            )
            assert excluded.status == "ready", excluded.reason
    view = await owner._assign_all_items(
        pg, await owner._direct_current(pg, view.statement.id)
    )
    cut = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert cut.status == "recognized", cut.reason
    assert len(cut.cut.snapshot_json["partidas"]) == 16
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 14
    )
    assert await pg.conn.fetchval(
        "SELECT sum(debe::numeric) FROM accounting_poliza_lines"
    ) == Decimal("8747.10")
    assert await pg.conn.fetchval(
        "SELECT sum(haber::numeric) FROM accounting_poliza_lines"
    ) == Decimal("8747.10")
    for fmt in ("csv", "xlsx", "zip"):
        async with pg.factory() as session:
            blob, name, _ = await accounting.export_direct_cut(
                session, actor_id=pg.actor.id, import_id=view.statement.id, format=fmt
            )
        assert blob and name.endswith("." + fmt)
    assert await physical(pg) == original


@pytest.mark.asyncio
async def test_two_identical_concurrent_confirmations_create_one_revision(direct_pg):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    results = await asyncio.gather(
        confirm(pg, request, planned), confirm(pg, request, planned)
    )
    assert sorted(r.status for r in results) == ["corrected", "exists"]
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 1
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_rows") == 16
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 16


@pytest.mark.asyncio
async def test_stale_and_payload_replay_fail_without_financial_side_effects(direct_pg):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict):
            await correction.confirm_direct_sign_correction(
                session,
                **request,
                preview_hash="0" * 64,
                idempotency_key="bad-preview",
                confirmed=True,
            )
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2
    await confirm(pg, request, planned)
    async with pg.factory.begin() as session:
        replay = await correction.confirm_direct_sign_correction(
            session,
            **{**request, "reason": "Changed payload"},
            preview_hash=planned.progress["preview_hash"],
            idempotency_key="acceptance-sign-correction",
            confirmed=True,
        )
        assert (
            replay.status == "pending" and replay.reason == "sign_idempotency_conflict"
        )
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 16


@pytest.mark.asyncio
async def test_revision_and_physical_source_append_only_sql_guards(direct_pg):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    await confirm(pg, request, planned)
    for sql in (
        "UPDATE amex_direct_sign_revisions SET reason='tampered'",
        "DELETE FROM amex_direct_sign_revisions",
        "DELETE FROM amex_direct_sign_rows",
        "UPDATE amex_direct_sign_rows SET movement_kind='credit' WHERE movement_kind='debit'",
        "UPDATE amex_direct_items SET movement_kind='credit' WHERE movement_kind='debit'",
        "UPDATE expense_reports SET estado_gasto='activo' WHERE estado_gasto='cancelado'",
        "UPDATE expense_reports SET gasto_cantidad=gasto_cantidad+1 WHERE estado_gasto!='cancelado'",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await pg.conn.execute(sql)
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_rows") == 16


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency", ["classification", "no_cfdi", "credit_exclusion", "inactive_actor"]
)
async def test_new_dependency_or_lost_authority_blocks_apply_atomically(
    direct_pg, dependency
):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    debit = next(i for i in view.items if i.movement_kind == "debit")
    if dependency == "classification":
        from devnous.gastos.services.amex_direct_budget_service import (
            classify_direct_items,
        )

        async with pg.factory.begin() as session:
            result = await classify_direct_items(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_ids=[debit.id],
                tournament_id=pg.tournament.id,
                project_code=None,
                budget_concept_id=pg.concept.id,
                edition_year=2026,
                expected_versions={str(debit.id): debit.source_version},
                reason="Concurrent real accounting classification",
            )
            assert result.status not in {"pending", "error"}, result.reason
    elif dependency == "no_cfdi":
        async with pg.factory.begin() as session:
            result = await no_cfdi.declare_no_cfdi_items(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_ids=[debit.id],
                expected_versions={str(debit.id): debit.source_version},
                expected_source_version=view.source_version,
                reason="Explicit concurrent Finance evidence",
                request_key="before-correction",
                confirmed=True,
            )
            assert result.status == "ready", result.reason
    elif dependency == "credit_exclusion":
        from devnous.gastos.services.amex_direct_matching_service import (
            exclude_direct_credit,
        )

        credit = next(i for i in view.items if i.movement_kind == "credit")
        async with pg.factory.begin() as session:
            result = await exclude_direct_credit(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=credit.id,
                expected_source_version=view.source_version,
                reason="Explicit original bank credit decision",
                confirmed=True,
            )
            assert result.status == "ready", result.reason
    else:
        await pg.conn.execute(
            "UPDATE empleados SET activo=false WHERE id=$1", pg.actor.id
        )
    expected_error = (
        DirectAccessError if dependency == "inactive_actor" else DirectConflict
    )
    with pytest.raises(expected_error):
        await confirm(pg, request, planned)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 0
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_rows") == 0
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM expense_reports WHERE estado_gasto='cancelado'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_mid_creation_fault_rolls_back_expenses_cancellations_and_evidence(
    direct_pg,
):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    before = await physical(pg)
    real_create = correction.create_expense_from_data
    calls = 0

    async def interrupted_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        expense = await real_create(*args, **kwargs)
        if calls == 9:
            raise RuntimeError("Injected failure after old Amazon cancellation")
        return expense

    pg.monkeypatch.setattr(correction, "create_expense_from_data", interrupted_create)
    with pytest.raises(RuntimeError, match="Injected failure"):
        await confirm(pg, request, planned)
    assert calls == 9
    assert await physical(pg) == before
    for table in (
        "amex_direct_sign_revisions",
        "amex_direct_sign_rows",
        "aprobaciones",
    ):
        assert await pg.conn.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM expense_reports WHERE estado_gasto='cancelado'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_two_distinct_keys_cannot_create_second_correction(direct_pg):
    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    results = await asyncio.gather(
        confirm(pg, request, planned, key="distinct-a"),
        confirm(pg, request, planned, key="distinct-b"),
    )
    assert sorted(r.status for r in results) == ["corrected", "pending"]
    assert (
        next(r for r in results if r.status == "pending").reason
        == "sign_idempotency_conflict"
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 1
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 16


@pytest.mark.asyncio
async def test_sql_incomplete_revision_cannot_commit(direct_pg):
    from uuid import UUID, uuid4

    from sqlalchemy.exc import DBAPIError

    pg = direct_pg
    view = await imported(pg)
    request, planned = await preview(pg, view)
    plan = planned.progress
    with pytest.raises(DBAPIError):
        async with pg.factory.begin() as session:
            session.add(
                models.AmexDirectSignRevision(
                    id=uuid4(),
                    import_id=view.statement.id,
                    source_file_id=UUID(plan["source_file_id"]),
                    **{
                        k: plan[k]
                        for k in (
                            "file_sha256",
                            "parser_version",
                            "original_profile",
                            "profile",
                            "context_version",
                            "expected_source_version",
                            "expected_count",
                            "expected_item_ids",
                            "original_counts",
                            "effective_counts",
                            "original_totals",
                            "effective_totals",
                            "membership_digest",
                        )
                    },
                    context_id=UUID(plan["context_id"]),
                    actor_id=pg.actor.id,
                    reason=request["reason"],
                    idempotency_key="incomplete-sql-attempt",
                    request_hash="a" * 64,
                    preview_hash=plan["preview_hash"],
                )
            )
            await session.flush()
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 0
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("opponent", ["classification", "cfdi_link"])
async def test_correction_race_with_real_decision_owner_has_one_winner(
    direct_pg, opponent
):
    from devnous.gastos.services import amex_direct_matching_service as matching
    from devnous.gastos.services import amex_direct_status_service as status
    from devnous.gastos.services.amex_direct_budget_service import classify_direct_items
    from devnous.gastos.services.amex_direct_common import digest
    from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

    pg = direct_pg
    if opponent == "cfdi_link":
        async with pg.factory.begin() as session:
            ingested = await ingest_cfdi_xml(
                session, owner._synthetic_sat_xml(), source="sat"
            )
        result = await owner._direct_import(
            pg,
            b"Date,Description,Amount\n2026-10-02,OLD DEBIT,-116.00\n2026-10-02,NEW DEBIT,200.00\n",
        )
        view = await owner._direct_current(pg, result.statement.id)
        debit = next(i for i in view.items if i.movement_kind == "debit")
        observed = await status.consult_selected_direct_cfdi_status(
            pg.factory,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_id=debit.id,
            cfdi_id=ingested.cfdi_report.id,
            expected_source_version=view.source_version,
            expected_item_version=debit.source_version,
            expected_identity_hash=digest(matching.cfdi_identity(ingested.cfdi_report)),
            request_key="race-observed-cfdi",
            consented=True,
            status_service=owner.ObservedSAT(),
        )
        assert observed.status == "observed", observed.reason
        view = await owner._direct_current(pg, view.statement.id)
    else:
        view = await imported(pg)
    debit = next(i for i in view.items if i.movement_kind == "debit")
    request, planned = await preview(pg, view)

    async def decide():
        async with pg.factory.begin() as session:
            if opponent == "classification":
                return await classify_direct_items(
                    session,
                    actor_id=pg.actor.id,
                    import_id=view.statement.id,
                    item_ids=[debit.id],
                    tournament_id=pg.tournament.id,
                    project_code=None,
                    budget_concept_id=pg.concept.id,
                    edition_year=2026,
                    expected_versions={str(debit.id): debit.source_version},
                    reason="Real owner racing correction",
                )
            return await matching.link_direct_cfdi_manual(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=debit.id,
                cfdi_id=ingested.cfdi_report.id,
                expected_source_version=view.source_version,
                reason="Real owner racing correction",
                confirmed=True,
            )

    corrected, decided = await asyncio.gather(
        confirm(pg, request, planned), decide(), return_exceptions=True
    )
    assert all(
        not isinstance(r, BaseException) or isinstance(r, DirectConflict)
        for r in (corrected, decided)
    )
    revisions = await pg.conn.fetchval(
        "SELECT count(*) FROM amex_direct_sign_revisions"
    )
    async with pg.factory() as session:
        row = await session.get(models.AmexDirectItem, debit.id)
        decision_persisted = bool(
            row.budget_concept_id if opponent == "classification" else row.cfdi_id
        )
    if revisions:
        assert corrected.status == "corrected"
        assert not decision_persisted
        assert isinstance(decided, DirectConflict) or decided.status == "pending"
    else:
        assert decision_persisted
        assert isinstance(corrected, DirectConflict)
        assert decided.status not in {"pending", "error"}
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
async def test_existing_real_cut_prevents_new_correction(direct_pg):
    pg = direct_pg
    matched, _, _ = await owner._matched_statement(pg)
    view = await owner._assign_all_items(
        pg, await owner._direct_current(pg, matched.statement.id)
    )
    recognized = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert recognized.status == "recognized", recognized.reason
    view = await owner._direct_current(pg, view.statement.id)
    async with pg.factory() as session:
        result = await correction.preview_direct_sign_correction(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            profile="debit_positive",
            reason="Forbidden historical reinterpretation",
            expected_source_version=view.source_version,
        )
    assert result.status == "pending" and "immutable" in result.reason
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 0
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
