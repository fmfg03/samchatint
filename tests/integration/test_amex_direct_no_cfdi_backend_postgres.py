"""Real owner guards and decision-to-cut no-CFDI backend acceptance."""

import importlib.util
import io
from pathlib import Path

import asyncpg
import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text

from devnous.gastos import models
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_matching_service as matching
from devnous.gastos.services import amex_direct_no_cfdi_service as decisions
from devnous.gastos.services.amex_direct_common import DirectConflict
from devnous.gastos.services.amex_direct_effective_source_service import (
    load_effective_sources,
)

_path = (
    Path(__file__).resolve().parents[1]
    / "acceptance/test_amex_sat_budget_direct_postgres.py"
)
_spec = importlib.util.spec_from_file_location("no_cfdi_backend_fixture", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
direct_pg = _fixture.direct_pg


async def current(pg, import_id):
    return await _fixture._direct_current(pg, import_id)


def args(pg, view, *, key="declare", reason="Bank charge without invoice"):
    return dict(
        actor_id=pg.actor.id,
        import_id=view.statement.id,
        item_ids=[i.id for i in view.items if i.movement_kind == "debit"],
        expected_versions={
            str(i.id): i.source_version
            for i in view.items
            if i.movement_kind == "debit"
        },
        expected_source_version=view.source_version,
        reason=reason,
        request_key=key,
        confirmed=True,
    )


async def imported(pg, *, profile="debit_negative", two=False):
    sign = "-" if profile == "debit_negative" else ""
    content = f"Date,Description,Amount\n2026-10-02,NO INVOICE,{sign}116.00\n"
    if two:
        content += f"2026-10-03,SECOND NO INVOICE,{sign}232.00\n"
    result = await _fixture._direct_import(pg, content.encode(), profile=profile)
    assert result.status in {"created", "imported"}, result.reason
    return await current(pg, result.statement.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["debit_negative", "debit_positive"])
async def test_declare_classify_budget_confirm_freeze_and_export(direct_pg, profile):
    pg = direct_pg
    view = await imported(pg, profile=profile, two=True)
    request = args(pg, view, reason='=HYPERLINK("danger") <reason>')
    async with pg.factory.begin() as session:
        declared = await decisions.declare_no_cfdi_items(session, **request)
        assert declared.status == "ready"
    async with pg.factory.begin() as session:
        replay = await decisions.declare_no_cfdi_items(session, **request)
        assert replay.status == "exists"
    view = await current(pg, view.statement.id)
    assert all(i.no_cfdi_decision["action"] == "declare" for i in view.items)
    async with pg.factory() as session:
        sources = await load_effective_sources(session, view.statement.id)
        items = (
            (
                await session.execute(
                    select(models.AmexDirectItem).where(
                        models.AmexDirectItem.import_id == view.statement.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for item in items:
            expected = decisions.economic_source_hash(
                view.statement, item, sources[item.id]
            )
            observed = await session.scalar(
                text("SELECT amex_direct_no_cfdi_source_hash(:id)"), {"id": item.id}
            )
            assert observed == expected
    async with pg.factory.begin() as session:
        matched = await matching.reconcile_direct_statement(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            expected_source_version=view.source_version,
        )
        assert matched.source_version == view.source_version
    view = await _fixture._assign_all_items(pg, view)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="budget_release"):
            await decisions.revoke_no_cfdi_items(
                session, **args(pg, view, key="blocked-revoke")
            )
    cut_args = await _fixture._cut_args(pg, view)
    recognized = await _fixture._confirm(pg, cut_args)
    assert recognized.status == "recognized", recognized.reason
    snapshot = recognized.cut.snapshot_json
    assert len(snapshot["lines"]) == 4
    assert all(
        p["coverage_kind"] == "new_no_cfdi" and p["cfdi"] is None
        for p in snapshot["partidas"]
    )
    assert all(
        p["no_cfdi_decision_id"] == p["no_cfdi_decision"]["id"]
        for p in snapshot["partidas"]
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 2
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT sum(debe) FROM accounting_poliza_lines") == 348
    )
    assert (
        await pg.conn.fetchval("SELECT sum(haber) FROM accounting_poliza_lines") == 348
    )
    for fmt in ("csv", "xlsx", "zip"):
        async with pg.factory() as session:
            blob, name, mime = await accounting.export_direct_cut(
                session, actor_id=pg.actor.id, import_id=view.statement.id, format=fmt
            )
        assert blob and name.endswith("." + fmt)
        if fmt == "csv":
            assert "Sin CFDI" in blob.decode("cp1252")
            assert "<reason>" in blob.decode("cp1252")
        if fmt == "xlsx":
            book = load_workbook(io.BytesIO(blob))
            assert all(c.data_type != "f" for sh in book for row in sh for c in row)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="immutable"):
            await decisions.revoke_no_cfdi_items(
                session, **args(pg, view, key="after-cut")
            )


@pytest.mark.asyncio
async def test_revision_chain_payload_conflict_stale_and_invalid_batch_atomic(
    direct_pg,
):
    pg = direct_pg
    view = await imported(pg, two=True)
    request = args(pg, view)
    bad = {
        **request,
        "expected_versions": {
            **request["expected_versions"],
            str(view.items[-1].id): 999,
        },
    }
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="stale_item"):
            await decisions.declare_no_cfdi_items(session, **bad)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 0
    )
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **request)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="idempotency"):
            await decisions.declare_no_cfdi_items(
                session, **{**request, "reason": "Different payload"}
            )
    view = await current(pg, view.statement.id)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="transition_conflict"):
            await decisions.declare_no_cfdi_items(
                session, **args(pg, view, key="already-declared")
            )
    first_heads = [i.no_cfdi_decision_id for i in view.items]
    async with pg.factory.begin() as session:
        await decisions.revoke_no_cfdi_items(session, **args(pg, view, key="revoke"))
    view = await current(pg, view.statement.id)
    assert all(i.no_cfdi_decision["action"] == "revoke" for i in view.items)
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(
            session, **args(pg, view, key="redeclare")
        )
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_direct_no_cfdi_decisions ORDER BY item_id,revision"
    )
    assert len(rows) == 6
    assert [r["action"] for r in rows] == ["declare", "revoke", "declare"] * 2
    assert {r["id"] for r in rows if r["revision"] == 1} == set(first_heads)
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.conn.execute(
            "UPDATE amex_direct_no_cfdi_decisions SET reason='overwritten'"
        )


@pytest.mark.asyncio
async def test_mixed_fiscal_and_no_cfdi_share_cut_and_replace_pending_actuals_once(
    direct_pg,
):
    from decimal import Decimal

    from samchat.budgets.service import build_budget_actuals_snapshot

    pg = direct_pg
    matched, _, cfdi = await _fixture._matched_statement(
        pg,
        content=b"Date,Description,Amount\n2026-10-02,COMERCIO ACME CDMX,-116.00\n2026-10-03,NO INVOICE,-232.00\n",
    )
    view = await current(pg, matched.statement.id)
    request = args(pg, view, key="mixed-declare")
    selected = [i for i in view.items if i.cfdi_id is None]
    request["item_ids"] = [i.id for i in selected]
    request["expected_versions"] = {str(i.id): i.source_version for i in selected}
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **request)
    view = await _fixture._assign_all_items(pg, await current(pg, view.statement.id))

    async def snapshot():
        async with pg.factory() as session:
            return await build_budget_actuals_snapshot(
                session,
                edition_year=2026,
                version_id=str(pg.version_id),
                tournament_id=str(pg.tournament.id),
                tournament_name=pg.tournament.name,
            )

    before = await snapshot()
    pending = [
        r
        for r in before["movements"]
        if r.get("expense_id") == str(selected[0].imported_expense_id)
    ]
    assert len(pending) == 1, pending
    assert Decimal(str(pending[0]["amount"])) == Decimal("232")
    result = await _fixture._confirm(pg, await _fixture._cut_args(pg, view))
    assert result.status == "recognized", result.reason
    assert {p["coverage_kind"] for p in result.cut.snapshot_json["partidas"]} == {
        "new",
        "new_no_cfdi",
    }
    assert len(result.cut.snapshot_json["lines"]) == 5
    after = await snapshot()
    actual = [
        r
        for r in after["movements"]
        if r.get("expense_id") == str(selected[0].imported_expense_id)
    ]
    assert len(actual) == 1 and actual[0]["kind"] == "ledger_expense", actual
    assert Decimal(str(actual[0]["amount"])) == Decimal("232")
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 2
    )


@pytest.mark.asyncio
async def test_caught_late_owner_failure_cannot_commit_partial_cut(
    direct_pg, monkeypatch
):
    pg = direct_pg
    view = await imported(pg)
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **args(pg, view))
    view = await _fixture._assign_all_items(pg, await current(pg, view.statement.id))
    cut_args = await _fixture._cut_args(pg, view)
    original = accounting._persist_coverage

    async def fail_after_memberships(*args, **kwargs):
        await original(*args, **kwargs)
        raise DirectConflict("synthetic_late_owner_failure")

    monkeypatch.setattr(accounting, "_persist_coverage", fail_after_memberships)
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match="synthetic_late"):
            await accounting.confirm_direct_accounting(session, **cut_args)
    # The caller caught the exception and committed; the internal savepoint owns rollback.
    for table in (
        "accounting_polizas",
        "accounting_poliza_lines",
        "amex_direct_accounting_cuts",
        "amex_direct_cut_items",
        "amex_recognition_consumptions",
        "amex_recognition_representations",
    ):
        assert await pg.conn.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 1
    )
    assert (
        await pg.conn.fetchval("SELECT accounting_status FROM amex_direct_items")
        == "pending"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ("confirmation", "confirmation_required"),
        ("reason", "reason_required"),
        ("long_reason", "reason_required"),
        ("key", "request_key_required"),
        ("duplicate", "selection_required"),
        ("version", "stale_source_version"),
        ("foreign", "item_context_mismatch"),
        ("tax", "manual_tax_conflict"),
    ],
)
async def test_negative_requests_preserve_evidence_and_do_not_insert(
    direct_pg, change, reason
):
    from uuid import uuid4

    pg = direct_pg
    view = await imported(pg)
    request = args(pg, view)
    if change == "confirmation":
        request["confirmed"] = False
    elif change == "reason":
        request["reason"] = "   "
    elif change == "long_reason":
        request["reason"] = "a" * 1001
    elif change == "key":
        request["request_key"] = ""
    elif change == "duplicate":
        request["item_ids"] *= 2
    elif change == "version":
        request["expected_source_version"] += 1
    elif change == "foreign":
        request["item_ids"] = [uuid4()]
    else:
        await pg.conn.execute(
            "UPDATE expense_reports SET iva=16 WHERE id=$1",
            view.items[0].imported_expense_id,
        )
    async with pg.factory.begin() as session:
        with pytest.raises(DirectConflict, match=reason):
            await decisions.declare_no_cfdi_items(session, **request)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT no_cfdi_decision_id FROM amex_direct_items")
        is None
    )
    if change == "tax":
        assert await pg.conn.fetchval("SELECT iva FROM expense_reports") == 16


@pytest.mark.asyncio
async def test_explicit_revoke_before_context_revision_preserves_revoked_head(
    direct_pg,
):
    import json
    import os
    from datetime import date

    from devnous.gastos.services.amex_direct_context_service import set_direct_context
    from devnous.gastos.services.amex_direct_recovery_service import (
        preview_direct_period_recovery,
    )

    pg = direct_pg
    view = await imported(pg)
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **args(pg, view))
    view = await current(pg, view.statement.id)
    old_context = view.context.id
    contexts = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    revised = dict(contexts[0], receiver_rfc="CCC010101CCC")
    pg.monkeypatch.setenv(
        "AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(contexts + [revised])
    )

    async def revise(view):
        async with pg.factory.begin() as session:
            return await set_direct_context(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                company_code="TEST",
                receiver_rfc="CCC010101CCC",
                currency="MXN",
                profile="debit_negative",
                expected_source_version=view.source_version,
                reason="Explicit receiver correction",
                confirmed=True,
            )

    blocked = await revise(view)
    assert blocked.status == "pending" and blocked.reason == "no_cfdi_revoke_required"
    async with pg.factory() as session:
        recovery = await preview_direct_period_recovery(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 10, 31),
            reason="Expanded coverage",
            expected_source_version=view.source_version,
        )
        assert (
            recovery.status == "pending"
            and recovery.reason == "no_cfdi_revoke_required"
        )
    async with pg.factory.begin() as session:
        await decisions.revoke_no_cfdi_items(
            session, **args(pg, view, key="before-context")
        )
    view = await current(pg, view.statement.id)
    revoked_id = view.items[0].no_cfdi_decision_id
    changed = await revise(view)
    assert changed.status == "ready", changed.reason
    view = await current(pg, view.statement.id)
    assert view.context.id != old_context
    assert view.items[0].no_cfdi_decision_id == revoked_id
    assert view.items[0].no_cfdi_decision["context_id"] == str(old_context)
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(
            session, **args(pg, view, key="new-context")
        )
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_direct_no_cfdi_decisions ORDER BY revision"
    )
    assert [r["action"] for r in rows] == ["declare", "revoke", "declare"]
    assert (
        rows[1]["context_id"] == old_context
        and rows[2]["context_id"] == view.context.id
    )
    assert rows[2]["previous_decision_id"] == revoked_id


@pytest.mark.asyncio
async def test_merchant_only_rfc_is_preserved_and_does_not_imply_invoice(direct_pg):
    pg = direct_pg
    imported_result = await _fixture._direct_import(
        pg,
        b"Date,Description,Amount,Issuer RFC\n2026-10-02,MERCHANT ONLY,-116.00,AAA010101AAA\n",
    )
    view = await current(pg, imported_result.statement.id)
    assert view.items[0].issuer_rfc == "AAA010101AAA"
    async with pg.factory.begin() as session:
        outcome = await decisions.declare_no_cfdi_items(session, **args(pg, view))
        assert outcome.status == "ready"
    row = await pg.conn.fetchrow(
        "SELECT issuer_rfc,cfdi_id,declared_uuid FROM amex_direct_items"
    )
    assert row["issuer_rfc"] == "AAA010101AAA"
    assert row["cfdi_id"] is None and row["declared_uuid"] is None
