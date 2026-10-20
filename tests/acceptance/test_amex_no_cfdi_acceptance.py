"""Independent bounded acceptance of explicit no-CFDI authority and coexistence."""

import asyncio
import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import update

from devnous.gastos import models
from devnous.gastos.services import amex_direct_matching_service as matching
from devnous.gastos.services import amex_direct_no_cfdi_service as decisions
from devnous.gastos.services.amex_direct_common import DirectConflict

ROOT = Path(__file__).resolve().parents[2]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


backend = module(
    "independent_no_cfdi_backend",
    "tests/integration/test_amex_direct_no_cfdi_backend_postgres.py",
)
history = module(
    "independent_no_cfdi_history",
    "tests/integration/test_amex_settlement_postgres.py",
)
owner = backend._fixture
direct_pg = owner.direct_pg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial", ["inactive", "budget", "unscoped", "card", "entity", "explicit"]
)
async def test_no_cfdi_authority_is_reloaded_before_any_effect(direct_pg, denial):
    pg = direct_pg
    view = await backend.imported(pg)
    request = backend.args(pg, view)
    if denial in {"inactive", "explicit"}:
        async with pg.factory.begin() as session:
            if denial == "inactive":
                await session.execute(
                    update(models.Empleado)
                    .where(models.Empleado.id == pg.actor.id)
                    .values(activo=False)
                )
            else:
                from devnous.gastos.services.access_control_service import (
                    empleado_area,
                    empleado_role,
                )

                session.add(
                    models.AccessControlRule(
                        tool_key="amex_direct",
                        action_key="conciliar",
                        role_key=empleado_role(pg.actor),
                        area_key=empleado_area(pg.actor),
                        allowed=False,
                        active=True,
                    )
                )
    elif denial == "budget":
        request["actor_id"] = pg.cp_actor.id
    else:
        contexts = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
        if denial == "unscoped":
            contexts[0]["actor_scopes"] = []
        elif denial == "card":
            contexts[0]["card_account_ids"] = [str(uuid4())]
        else:
            contexts[0]["company_code"] = "OTHER"
        pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(contexts))
    expected_error = DirectConflict if denial in {"card", "entity"} else PermissionError
    async with pg.factory.begin() as session:
        with pytest.raises(expected_error):
            await decisions.declare_no_cfdi_items(session, **request)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT no_cfdi_decision_id FROM amex_direct_items")
        is None
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["declare", "match"])
async def test_concurrent_declaration_has_one_head_and_no_fiscal_fork(
    direct_pg, competitor
):
    pg = direct_pg
    view = await backend.imported(pg)
    request = backend.args(pg, view)
    gate = asyncio.Event()

    async def run(which):
        await gate.wait()
        async with pg.factory.begin() as session:
            try:
                if which == "match":
                    return await matching.reconcile_direct_statement(
                        session,
                        actor_id=pg.actor.id,
                        import_id=view.statement.id,
                        expected_source_version=view.source_version,
                    )
                return await decisions.declare_no_cfdi_items(session, **request)
            except DirectConflict as exc:
                return exc

    tasks = [asyncio.create_task(run("declare")), asyncio.create_task(run(competitor))]
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    count = await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
    if not count:
        assert competitor == "match"
        assert any(
            isinstance(result, DirectConflict) and "stale" in str(result)
            for result in results
        ), results
        refreshed = await owner._direct_current(pg, view.statement.id)
        async with pg.factory.begin() as session:
            retried = await decisions.declare_no_cfdi_items(
                session, **backend.args(pg, refreshed, key="after-race-refresh")
            )
            assert retried.status == "ready"
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 1
    )
    row = await pg.conn.fetchrow(
        "SELECT no_cfdi_decision_id,cfdi_id FROM amex_direct_items"
    )
    assert row["no_cfdi_decision_id"] is not None and row["cfdi_id"] is None
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )


@pytest.mark.asyncio
async def test_whole_cut_combines_no_cfdi_fiscal_prior_settlement_and_credit(direct_pg):
    pg = direct_pg
    _, prior, *_ = await history.historical_prior(
        pg, fiscal_uuid="20000000-0000-4000-8000-000000000002"
    )
    _, settlement, *_ = await history.historical_credit(
        pg, fiscal_uuid="30000000-0000-4000-8000-000000000003"
    )
    matched, _, _ = await owner._matched_statement(
        pg,
        content=(
            b"Date,Description,Amount\n"
            b"2026-10-02,COMERCIO ACME CDMX,-116.00\n"
            b"2026-10-03,NO INVOICE,-232.00\n"
            b"2026-10-02,PRIOR OWNER,-116.00\n"
            b"2026-10-02,SETTLEMENT OWNER,-116.00\n"
            b"2026-10-03,BANK CREDIT,20.00\n"
        ),
    )
    view = await owner._direct_current(pg, matched.statement.id)
    # Select exact historical ledger identities through the real owner.
    for descriptor, row in [("PRIOR OWNER", prior), ("SETTLEMENT OWNER", settlement)]:
        item = next(i for i in view.items if i.descriptor == descriptor)
        async with pg.factory.begin() as session:
            listed = await history.settlements.list_settlement_sources(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=item.id,
            )
            assert any(
                candidate["origin_poliza_id"] == str(row["origin_poliza_id"])
                or candidate["origin_poliza_id"] == row["origin_poliza_id"]
                for candidate in listed["candidates"]
            )
            selected = await history.settlements.select_settlement_source(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=item.id,
                **{key: row[key] for key in history.settlements.IDENTITY_KEYS},
                expected_item_version=listed["item_version"],
                expected_source_version=listed["source_version"],
                reason="Explicit existing ledger identity",
                confirmed=True,
            )
            assert selected.status == "selected", selected.reason
        view = await owner._direct_current(pg, matched.statement.id)
    item = next(i for i in view.items if i.descriptor == "NO INVOICE")
    request = backend.args(pg, view)
    request.update(
        item_ids=[item.id], expected_versions={str(item.id): item.source_version}
    )
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **request)
    view = await owner._direct_current(pg, matched.statement.id)
    credit = next(i for i in view.items if i.movement_kind == "credit")
    async with pg.factory.begin() as session:
        await matching.exclude_direct_credit(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_id=credit.id,
            expected_source_version=view.source_version,
            reason="Explicit bank credit",
            confirmed=True,
        )
    view = await owner._direct_current(pg, matched.statement.id)
    new_items = [
        i for i in view.items if i.descriptor in {"NO INVOICE", "COMERCIO ACME CDMX"}
    ]
    from devnous.gastos.services import amex_direct_budget_service as budget

    selected_ids = {i.id for i in new_items}
    async with pg.factory.begin() as session:
        classified = await budget.classify_direct_items(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=list(selected_ids),
            tournament_id=pg.tournament.id,
            project_code=None,
            budget_concept_id=pg.concept.id,
            edition_year=2026,
            expected_versions={str(i.id): i.source_version for i in new_items},
            reason="Explicit new sources only",
        )
        assert classified.status == "classified", classified.reason
    view = await owner._direct_current(pg, matched.statement.id)
    selected = [i for i in view.items if i.id in selected_ids]
    async with pg.factory.begin() as session:
        submitted = await budget.send_direct_items_to_budget(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=list(selected_ids),
            expected_versions={str(i.id): i.source_version for i in selected},
            confirmed=True,
        )
        assert submitted.status == "sent", submitted.reason
    view = await owner._direct_current(pg, matched.statement.id)
    for item in view.items:
        if item.id not in selected_ids:
            continue
        async with pg.factory.begin() as session:
            assigned = await budget.assign_direct_budget_line(
                session,
                actor_id=pg.cp_actor.id,
                item_id=item.id,
                budget_line_id=pg.line_id,
                expected_source_version=item.source_version,
                expected_source_hash=item.source_hash,
                idempotency_key="independent-line-" + str(item.id),
            )
            assert assigned.status == "assigned", assigned.reason
    view = await owner._direct_current(pg, matched.statement.id)
    before = await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
    result = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert result.status == "recognized", result.reason
    snapshot = result.cut.snapshot_json
    assert {p["coverage_kind"] for p in snapshot["partidas"]} == {
        "new",
        "new_no_cfdi",
        "prior",
        "settlement",
        "credit_exclusion",
    }
    assert len(snapshot["partidas"]) == 5
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == before + 1
    )
    assert sum(Decimal(str(line["debe"])) for line in snapshot["lines"]) == Decimal(
        "464"
    )
    assert sum(Decimal(str(line["haber"])) for line in snapshot["lines"]) == Decimal(
        "464"
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 2
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )
    no_cfdi = next(
        p for p in snapshot["partidas"] if p["coverage_kind"] == "new_no_cfdi"
    )
    assert no_cfdi["cfdi"] is None and len(no_cfdi["journal_lines"]) == 2


@pytest.mark.asyncio
async def test_revoked_head_survives_recovery_then_redeclaration(direct_pg):
    pg = direct_pg
    recovery = module(
        "independent_no_cfdi_recovery",
        "tests/acceptance/test_amex_direct_period_recovery_postgres.py",
    )
    import_id, _ = await recovery.seed_previous_period_omission(pg)
    view = await owner._direct_current(pg, import_id)
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(session, **backend.args(pg, view))
    view = await owner._direct_current(pg, import_id)
    old_item = next(i for i in view.items if i.movement_kind == "debit")
    old_head = old_item.no_cfdi_decision_id
    old_hash = old_item.no_cfdi_decision["economic_source_hash"]
    blocked = await recovery.recovery_preview(pg, import_id, view.source_version)
    assert blocked.reason == "no_cfdi_revoke_required"
    async with pg.factory.begin() as session:
        await decisions.revoke_no_cfdi_items(
            session, **backend.args(pg, view, key="before-recovery")
        )
    view = await owner._direct_current(pg, import_id)
    revoked = next(i for i in view.items if i.id == old_item.id).no_cfdi_decision_id
    preview = await recovery.recovery_preview(pg, import_id, view.source_version)
    assert preview.status == "preview", preview.reason
    recovered = await recovery.recovery_confirm(
        pg, import_id, view.source_version, preview.progress["preview_hash"]
    )
    assert recovered.status in {"recovered", "ready"}, recovered.reason
    view = await owner._direct_current(pg, import_id)
    assert (
        next(i for i in view.items if i.id == old_item.id).no_cfdi_decision_id
        == revoked
    )
    async with pg.factory.begin() as session:
        await decisions.declare_no_cfdi_items(
            session, **backend.args(pg, view, key="after-recovery")
        )
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_direct_no_cfdi_decisions "
        "WHERE item_id=$1 ORDER BY revision",
        old_item.id,
    )
    assert [row["action"] for row in rows] == ["declare", "revoke", "declare"]
    assert rows[0]["id"] == old_head and rows[2]["previous_decision_id"] == revoked
    assert rows[0]["economic_source_hash"] == old_hash
    assert rows[2]["economic_source_hash"] != old_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["context", "recovery", "confirmation"])
async def test_incompatible_source_and_finalization_races_preserve_one_treatment(
    direct_pg, competitor
):
    from devnous.gastos.services.amex_direct_context_service import set_direct_context

    pg = direct_pg
    recovery = None
    if competitor == "recovery":
        recovery = module(
            "independent_no_cfdi_recovery_race",
            "tests/acceptance/test_amex_direct_period_recovery_postgres.py",
        )
        import_id, _ = await recovery.seed_previous_period_omission(pg)
        view = await owner._direct_current(pg, import_id)
        preview = await recovery.recovery_preview(pg, import_id, view.source_version)
        assert preview.status == "preview", preview.reason
    else:
        view = await backend.imported(pg)
    if competitor == "context":
        contexts = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
        contexts.append(dict(contexts[0], receiver_rfc="CCC010101CCC"))
        pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(contexts))
    if competitor == "confirmation":
        async with pg.factory.begin() as session:
            await decisions.declare_no_cfdi_items(session, **backend.args(pg, view))
        view = await owner._assign_all_items(
            pg, await owner._direct_current(pg, view.statement.id)
        )
        cut_args = await owner._cut_args(pg, view)
    gate = asyncio.Event()

    async def decision():
        await gate.wait()
        async with pg.factory.begin() as session:
            try:
                action = (
                    decisions.revoke_no_cfdi_items
                    if competitor == "confirmation"
                    else decisions.declare_no_cfdi_items
                )
                return await action(
                    session, **backend.args(pg, view, key="incompatible-race")
                )
            except DirectConflict as exc:
                return exc

    async def other():
        await gate.wait()
        try:
            if competitor == "confirmation":
                return await owner._confirm(pg, cut_args)
            if competitor == "recovery":
                return await recovery.recovery_confirm(
                    pg,
                    view.statement.id,
                    view.source_version,
                    preview.progress["preview_hash"],
                )
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
        except DirectConflict as exc:
            return exc

    tasks = [asyncio.create_task(decision()), asyncio.create_task(other())]
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    if competitor == "confirmation":
        assert isinstance(results[0], DirectConflict)
        assert results[1].status == "recognized", results
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
            == 1
        )
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
            == 1
        )
    else:
        declared = await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_no_cfdi_decisions"
        )
        if declared:
            assert declared == 1
            assert getattr(results[0], "status", None) == "ready"
            assert (
                isinstance(results[1], DirectConflict) or results[1].status == "pending"
            ), results
        else:
            assert isinstance(results[0], DirectConflict), results
            assert results[1].status in {"ready", "recovered"}, results
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
            == 0
        )
