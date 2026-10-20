"""Economic consumers resolve recovered sources without rewriting physical rows."""

from datetime import date

import pytest
from sqlalchemy import select

from devnous.gastos.models import ExpenseReport
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_budget_service as budget
from devnous.gastos.services.amex_accounting_identity_service import (
    direct_amex_expense_condition,
    effective_direct_sources_available,
    is_direct_amex_expense,
)
from devnous.gastos.services.amex_direct_common import resolve_item_import
from devnous.gastos.services.amex_direct_effective_source_service import (
    load_effective_sources,
)
from devnous.gastos.services.amex_recognition_service import direct_statement_source

# isort: split
import test_amex_sat_budget_direct_postgres as _fixture
from test_amex_direct_period_recovery_postgres import (
    recovery_confirm,
    recovery_preview,
    seed_previous_period_omission,
)

direct_pg = _fixture.direct_pg
_assigned_statement = _fixture._assigned_statement


@pytest.mark.asyncio
async def test_recovered_sources_excluded_from_every_legacy_ownership_entry(direct_pg):
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    preview = await recovery_preview(pg, import_id, version)
    recovered = await recovery_confirm(
        pg, import_id, version, preview.progress["preview_hash"]
    )
    assert recovered.status == "recovered", recovered.reason
    async with pg.factory() as session:
        sources = await load_effective_sources(session, import_id)
        assert len(sources) == 6
        assert sum(bool(source.recovery_id) for source in sources.values()) == 5
        assert await effective_direct_sources_available(session)
        legacy = list(
            (
                await session.execute(
                    select(ExpenseReport.id).where(
                        ~direct_amex_expense_condition(
                            ExpenseReport, effective_sources=True
                        )
                    )
                )
            ).scalars()
        )
        assert legacy == []
        for source in sources.values():
            assert await is_direct_amex_expense(session, source.imported_expense_id)
            assert await direct_statement_source(session, [source.imported_expense_id])
            assert (
                await resolve_item_import(
                    session,
                    actor_id=pg.actor.id,
                    action="ver",
                    expense_id=source.imported_expense_id,
                )
                == import_id
            )


@pytest.mark.asyncio
async def test_cp_queue_retains_assigned_return_only_then_excludes_frozen(direct_pg):
    pg = direct_pg
    current = await _assigned_statement(pg)
    async with pg.factory() as session:
        queue = await budget.list_direct_budget_queue(session, actor_id=pg.cp_actor.id)
        assert len(queue) == 1
        assert queue[0].budget_status == "assigned"
        assert not queue[0].can_assign
        assert queue[0].can_return
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    async with pg.factory.begin() as session:
        result = await accounting.confirm_direct_accounting(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            expected_source_key=preview.source_key,
            expected_source_version=preview.source_version,
            accounting_date=date(2026, 10, 31),
            confirmed=True,
            idempotency_key="cp-freeze-proof",
        )
        assert result.status == "recognized", result.reason
    async with pg.factory() as session:
        assert (
            await budget.list_direct_budget_queue(session, actor_id=pg.cp_actor.id)
            == []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("first_owner", ["recovered", "ordinary"])
async def test_recovered_debit_competes_globally_and_cannot_reuse_full_cfdi(
    direct_pg, first_owner
):
    """Both ownership directions retain reverse uniqueness and shared limits."""
    from devnous.gastos.models import AmexDirectItem
    from devnous.gastos.services import amex_direct_context_service as context
    from devnous.gastos.services import amex_direct_matching_service as matching
    from devnous.gastos.services import amex_direct_status_service as status
    from devnous.gastos.services.amex_direct_common import digest
    from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

    pg = direct_pg
    historical = (
        b"Date,Description,Amount\n2026-10-02,Existing,-232.00\n"
        b"2026-09-30,COMERCIO ACME CDMX,-116.00\n"
    )
    recovered_import, version = await seed_previous_period_omission(pg, historical)
    preview = await recovery_preview(pg, recovered_import, version)
    outcome = await recovery_confirm(
        pg, recovered_import, version, preview.progress["preview_hash"]
    )
    assert outcome.status == "recovered", outcome.reason
    ordinary = await _fixture._direct_import(
        pg, b"Date,Description,Amount\n2026-10-01,COMERCIO ACME CDMX,-116.00\n"
    )
    assert ordinary.status == "imported", ordinary.reason
    async with pg.factory.begin() as session:
        ingested = await ingest_cfdi_xml(
            session,
            _fixture._synthetic_sat_xml().replace("2026-10-03", "2026-10-01"),
            source="sat",
        )
        cfdi = ingested.cfdi_report
        alias = await matching.confirm_merchant_alias(
            session,
            actor_id=pg.actor.id,
            import_id=recovered_import,
            descriptor="COMERCIO ACME CDMX",
            issuer_rfc="AAA010101AAA",
            reason="Explicit synthetic merchant identity",
            confirmed=True,
        )
        assert alias.status == "ready", alias.reason
    async with pg.factory() as session:
        sources = await load_effective_sources(session, recovered_import)
        recovered_source = next(row for row in sources.values() if row.recovery_id)
        recovered_item_id = recovered_source.direct_item_id
        ordinary_view = await context.list_direct_statement(
            session, actor_id=pg.actor.id, import_id=ordinary.statement.id
        )
    observed = await status.consult_selected_direct_cfdi_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=ordinary.statement.id,
        item_id=ordinary_view.items[0].id,
        cfdi_id=cfdi.id,
        expected_source_version=ordinary_view.source_version,
        expected_item_version=ordinary_view.items[0].source_version,
        expected_identity_hash=digest(matching.cfdi_identity(cfdi)),
        request_key="explicit-shared-candidate-observation",
        consented=True,
        status_service=_fixture.ObservedSAT(),
    )
    assert observed.status == "observed", observed.reason
    identities = {
        "recovered": (recovered_import, recovered_item_id),
        "ordinary": (ordinary.statement.id, ordinary_view.items[0].id),
    }
    async with pg.factory() as session:
        for _, item_id in identities.values():
            item = await session.get(AmexDirectItem, item_id)
            source = (await load_effective_sources(session, item.import_id))[item_id]
            expense = await session.get(ExpenseReport, source.imported_expense_id)
            assert await matching._available(session, cfdi, expense)
            # In either direction, the other eligible import is a competitor.
            assert not await matching._reverse_unique(session, item, cfdi)
    first_import, first_item = identities[first_owner]
    second_import, second_item = identities[
        "ordinary" if first_owner == "recovered" else "recovered"
    ]
    async with pg.factory.begin() as session:
        current = await context.list_direct_statement(
            session, actor_id=pg.actor.id, import_id=first_import
        )
        linked = await matching.link_direct_cfdi_manual(
            session,
            actor_id=pg.actor.id,
            import_id=first_import,
            item_id=first_item,
            cfdi_id=cfdi.id,
            expected_source_version=current.source_version,
            reason="Explicit full-invoice application to this debit",
            confirmed=True,
        )
        assert linked.status == "matched", linked.reason
    async with pg.factory.begin() as session:
        second = await session.get(AmexDirectItem, second_item)
        second_source = (await load_effective_sources(session, second_import))[
            second_item
        ]
        expense = await session.get(ExpenseReport, second_source.imported_expense_id)
        assert not await matching._available(session, cfdi, expense)
        # Explicit shared intent cannot create 232 of claims against a 116 CFDI.
        for shared in (False, True):
            current = await context.list_direct_statement(
                session, actor_id=pg.actor.id, import_id=second_import
            )
            blocked = await matching.link_direct_cfdi_manual(
                session,
                actor_id=pg.actor.id,
                import_id=second_import,
                item_id=second_item,
                cfdi_id=cfdi.id,
                expected_source_version=current.source_version,
                reason="Attempted duplicate full-invoice application",
                confirmed=True,
                shared_confirmed=shared,
            )
            assert blocked.status == "pending"
            assert blocked.reason == "cfdi_global_usage_conflict"
        await session.refresh(second)
        await session.refresh(expense)
        assert second.cfdi_id is None and expense.cfdi_report_id is None
        first = await session.get(AmexDirectItem, first_item)
        assert first.cfdi_id == cfdi.id and first.match_status == "matched"
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
