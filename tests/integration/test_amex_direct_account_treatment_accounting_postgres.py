"""Real treatment-to-cut proof for benefits, project expenses and partner assets."""

import importlib.util
import io
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from devnous.gastos import models
from devnous.gastos.services import amex_direct_account_treatment_service as treatments
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_budget_service as budget
from devnous.gastos.services import amex_direct_no_cfdi_service as fiscal

_spec = importlib.util.spec_from_file_location(
    "treatment_accounting_fixture",
    Path(__file__).resolve().parents[1]
    / "acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)
direct_pg = owner.direct_pg


async def configure(pg, *, install=True):
    pg.debtor = models.CuentaContable(
        id=uuid4(),
        codigo="1170-002-123",
        nombre="Synthetic partner",
        tipo="deudor",
        activo=True,
    )
    async with pg.factory.begin() as session:
        session.add(pg.debtor)
    configs = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    for config in configs:
        config["benefit_budget_line_ids"] = [str(pg.line_id)]
        config["partner_debtor_account_ids"] = [str(pg.debtor.id)]
        for scope in config["actor_scopes"]:
            scope["benefit_budget_line_ids"] = [str(pg.line_id)]
            scope["partner_debtor_account_ids"] = [str(pg.debtor.id)]
    pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(configs))
    if install:
        await owner.install_account_treatment_policy(pg)


async def current(pg, import_id):
    return await owner._direct_current(pg, import_id)


async def imported(pg, rows):
    result = await owner._direct_import(
        pg, ("Date,Description,Amount\n" + rows).encode()
    )
    assert result.status in {"created", "imported"}, result.reason
    return await current(pg, result.statement.id)


async def treatment(pg, view, item_id, kind):
    item = next(i for i in view.items if i.id == item_id)
    request = dict(
        actor_id=pg.actor.id,
        import_id=view.statement.id,
        item_ids=[item.id],
        treatment=kind,
        reason="Explicit synthetic destination",
        expected_versions={str(item.id): item.source_version},
        debtor_account_id=pg.debtor.id if kind == "partner_receivable" else None,
    )
    async with pg.factory() as session:
        planned = await treatments.preview_direct_account_treatment(session, **request)
    assert planned.status == "preview", planned.reason
    async with pg.factory.begin() as session:
        result = await treatments.confirm_direct_account_treatment(
            session,
            **request,
            expected_preview_hash=planned.progress["preview_hash"],
            idempotency_key="treatment-" + str(item.id),
            confirmed=True,
        )
    assert result.status == "ready", result.reason
    return await current(pg, view.statement.id)


async def declare(pg, view, item_id):
    item = next(i for i in view.items if i.id == item_id)
    async with pg.factory.begin() as session:
        result = await fiscal.declare_no_cfdi_items(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=[item.id],
            expected_versions={str(item.id): item.source_version},
            expected_source_version=view.source_version,
            reason="This synthetic charge will have no invoice",
            request_key="no-cfdi-" + str(item.id),
            confirmed=True,
        )
    assert result.status == "ready", result.reason
    return await current(pg, view.statement.id)


async def submit_assign(pg, view, ids):
    async with pg.factory.begin() as session:
        result = await budget.send_direct_items_to_budget(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=ids,
            expected_versions={
                str(i.id): i.source_version for i in view.items if i.id in ids
            },
            confirmed=True,
        )
    assert result.status == "sent", result.reason
    view = await current(pg, view.statement.id)
    for item in view.items:
        if item.id not in ids:
            continue
        async with pg.factory.begin() as session:
            assigned = await budget.assign_direct_budget_line(
                session,
                actor_id=pg.cp_actor.id,
                item_id=item.id,
                budget_line_id=pg.line_id,
                expected_source_version=item.source_version,
                expected_source_hash=item.source_hash,
                idempotency_key="CP-" + str(item.id),
            )
        assert assigned.status == "assigned", assigned.reason
    return await current(pg, view.statement.id)


async def preview(pg, view):
    async with pg.factory() as session:
        return await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=view.statement.id
        )


@pytest.mark.asyncio
async def test_partner_no_cfdi_no_budget_cut_and_immutable_export(direct_pg):
    pg = direct_pg
    await configure(pg)
    view = await imported(pg, "2026-10-02,GOOGLE SUBSCRIPTION,-939.00\n")
    item_id = view.items[0].id
    view = await treatment(pg, view, item_id, "partner_receivable")
    from devnous.gastos.services.amex_direct_common import DirectConflict
    from devnous.gastos.services.amex_expense_account_settlement_service import (
        select_settlement_source,
    )

    item = next(i for i in view.items if i.id == item_id)
    async with pg.factory.begin() as session:
        with pytest.raises(
            DirectConflict, match="account_treatment_historical_source_conflict"
        ):
            await select_settlement_source(
                session,
                actor_id=pg.actor.id,
                import_id=view.statement.id,
                item_id=item.id,
                expected_item_version=item.source_version,
                expected_source_version=view.source_version,
                reason="Conflicting historical selection",
                confirmed=True,
                **{
                    key: uuid4()
                    for key in (
                        "origin_poliza_id",
                        "origin_line_id",
                        "origin_expense_id",
                        "origin_document_id",
                        "origin_cuenta_gastos_id",
                        "origin_account_id",
                    )
                },
            )
    unresolved = await preview(pg, view)
    assert (
        unresolved.status == "pending"
        and "partner_fiscal_resolution_required" in unresolved.progress["exceptions"]
    )
    view = await declare(pg, view, item_id)
    ready = await preview(pg, view)
    assert ready.status == "ready", ready.progress
    assert ready.progress["partner_receivable_count"] == 1
    assert ready.progress["new_count"] == 1
    result = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert result.status == "recognized", result.reason
    part = result.cut.snapshot_json["partidas"][0]
    assert part["coverage_kind"] == "partner_receivable"
    assert part["account_treatment_decision_id"] == part["account_treatment"]["id"]
    assert (
        part["no_cfdi_decision"]["treatment"] == "full_amount_partner_receivable_no_tax"
    )
    assert part["cfdi"] is None and part["budget_assignment_id"] is None
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 0
    )
    rows = await pg.conn.fetch(
        "SELECT cuenta_codigo,debe,haber FROM accounting_poliza_lines ORDER BY debe DESC"
    )
    assert [(r["cuenta_codigo"], r["debe"], r["haber"]) for r in rows] == [
        (pg.debtor.codigo, Decimal("939.00"), Decimal(0)),
        (pg.liability.codigo, Decimal(0), Decimal("939.00")),
    ]
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 1
    )
    for fmt in ("csv", "xlsx", "zip"):
        async with pg.factory() as session:
            blob, name, _ = await accounting.export_direct_cut(
                session, actor_id=pg.actor.id, import_id=view.statement.id, format=fmt
            )
        assert blob and name.endswith("." + fmt)
        if fmt == "xlsx":
            book = load_workbook(io.BytesIO(blob))
            assert all(
                cell.data_type != "f" for sheet in book for row in sheet for cell in row
            )
    frozen = json.dumps(result.cut.snapshot_json, default=str, sort_keys=True)
    await pg.conn.execute(
        "UPDATE cuentas_contables SET nombre='Later catalog name' WHERE id=$1",
        pg.debtor.id,
    )
    async with pg.factory() as session:
        await accounting.export_direct_cut(
            session, actor_id=pg.actor.id, import_id=view.statement.id, format="xlsx"
        )
    stored = await pg.conn.fetchval(
        "SELECT snapshot_json::text FROM amex_direct_accounting_cuts"
    )
    assert json.dumps(json.loads(stored), sort_keys=True) == frozen


@pytest.mark.asyncio
async def test_benefit_needs_CP_assignment_but_never_operational_project(direct_pg):
    pg = direct_pg
    await configure(pg)
    view = await imported(pg, "2026-10-02,PARTNER BENEFIT,-116.00\n")
    item_id = view.items[0].id
    view = await treatment(pg, view, item_id, "benefit_expense")
    view = await declare(pg, view, item_id)
    blocked = await preview(pg, view)
    assert (
        blocked.status == "pending"
        and "budget_assignment_required" in blocked.progress["exceptions"]
    )
    view = await submit_assign(pg, view, [item_id])
    row = await pg.conn.fetchrow(
        "SELECT i.tournament_id,i.project_code,e.proyecto FROM amex_direct_items i JOIN amex_direct_effective_sources es ON es.id=i.id JOIN expense_reports e ON e.id=es.imported_expense_id WHERE i.id=$1",
        item_id,
    )
    assert row["tournament_id"] is None and row["project_code"] is None
    assert row["proyecto"] == ""  # Canonical absence in the legacy NOT NULL column.
    result = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert result.status == "recognized", result.reason
    part = result.cut.snapshot_json["partidas"][0]
    assert part["account_treatment"]["treatment"] == "benefit_expense"
    assert all(
        r["raw_row_json"]["treatment"] == "full_amount_expense_no_tax"
        for r in part["journal_lines"]
    )
    assert all(
        r["raw_row_json"]["account_treatment_kind"] == "benefit_expense"
        for r in part["journal_lines"]
    )
    assert part["budget_assignment_id"]
    assert all(
        r["raw_row_json"]["budget_scope"]["tournament_id"] == str(pg.tournament.id)
        for r in part["journal_lines"]
    )
    assert all(
        r["raw_row_json"]["project"] in (None, "") for r in part["journal_lines"]
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 1
    )


@pytest.mark.asyncio
async def test_three_destinations_share_one_cut_without_partner_budget_spend(direct_pg):
    from samchat.budgets.service import build_budget_actuals_snapshot

    pg = direct_pg
    await configure(pg)
    view = await imported(
        pg,
        "2026-10-02,BENEFIT,-116.00\n2026-10-03,PARTNER,-232.00\n2026-10-04,PROJECT,-348.00\n",
    )
    by_name = {i.source_json["description"]: i.id for i in view.items}
    for name, kind in (
        ("BENEFIT", "benefit_expense"),
        ("PARTNER", "partner_receivable"),
        ("PROJECT", "project_expense"),
    ):
        view = await treatment(pg, view, by_name[name], kind)
        view = await declare(pg, view, by_name[name])
    project = next(i for i in view.items if i.id == by_name["PROJECT"])
    async with pg.factory.begin() as session:
        classified = await budget.classify_direct_items(
            session,
            actor_id=pg.actor.id,
            import_id=view.statement.id,
            item_ids=[project.id],
            tournament_id=pg.tournament.id,
            project_code=None,
            budget_concept_id=pg.concept.id,
            edition_year=2026,
            expected_versions={str(project.id): project.source_version},
            reason="Actual project expense",
        )
    assert classified.status == "classified", classified.reason
    view = await current(pg, view.statement.id)
    view = await submit_assign(pg, view, [by_name["BENEFIT"], by_name["PROJECT"]])
    args = await owner._cut_args(pg, view)
    result = await owner._confirm(pg, args)
    assert result.status == "recognized", result.reason
    repeated = await owner._confirm(pg, args)
    assert repeated.status == "existing"
    parts = result.cut.snapshot_json["partidas"]
    assert Counter(p["coverage_kind"] for p in parts) == {
        "new_no_cfdi": 2,
        "partner_receivable": 1,
    }
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 3
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 2
    )
    assert await pg.conn.fetchval(
        "SELECT sum(debe) FROM accounting_poliza_lines"
    ) == Decimal("696.00")
    async with pg.factory() as session:
        snapshot = await build_budget_actuals_snapshot(
            session,
            edition_year=2026,
            version_id=str(pg.version_id),
            tournament_id=str(pg.tournament.id),
            tournament_name=pg.tournament.name,
        )
    partner_expense = next(
        p["expense_id"] for p in parts if p["coverage_kind"] == "partner_receivable"
    )
    own = [
        r
        for r in snapshot["movements"]
        if r.get("expense_id") in {p["expense_id"] for p in parts}
    ]
    assert not any(r.get("expense_id") == partner_expense for r in own)
    assert len(own) == 2 and sum(Decimal(str(r["amount"])) for r in own) == Decimal(
        "464.00"
    )


@pytest.mark.asyncio
async def test_linked_partner_keeps_CFDI_and_no_tax_posting_and_account_drift_blocks(
    direct_pg,
):
    pg = direct_pg
    await configure(pg)
    matched, _, cfdi = await owner._matched_statement(pg)
    view = await current(pg, matched.statement.id)
    view = await treatment(pg, view, view.items[0].id, "partner_receivable")
    ready = await preview(pg, view)
    assert ready.status == "ready", ready.progress
    assert len(ready.progress["lines"]) == 2
    await pg.conn.execute(
        "UPDATE cuentas_contables SET activo=false WHERE id=$1", pg.debtor.id
    )
    changed = await preview(pg, view)
    assert (
        changed.status == "pending"
        and "invalid_amex_partner_decision" in changed.progress["exceptions"]
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    await pg.conn.execute(
        "UPDATE cuentas_contables SET activo=true WHERE id=$1", pg.debtor.id
    )
    result = await owner._confirm(pg, await owner._cut_args(pg, view))
    assert result.status == "recognized", result.reason
    part = result.cut.snapshot_json["partidas"][0]
    assert part["cfdi"]["uuid"] == cfdi.cfdi_uuid
    assert part["no_cfdi_decision_id"] is None
    assert part["fiscal_retention_proof"] == []
    assert {r["cuenta_codigo"] for r in part["journal_lines"]} == {
        pg.debtor.codigo,
        pg.liability.codigo,
    }


def policy_cli(pg):
    spec = importlib.util.spec_from_file_location(
        "actual_policy_cli",
        Path(__file__).resolve().parents[2]
        / "scripts/configure_amex_direct_account_treatment_policies.py",
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    original = cli.create_async_engine
    pg.monkeypatch.setattr(
        cli,
        "create_async_engine",
        lambda url: original(
            url, connect_args={"server_settings": {"search_path": pg.schema}}
        ),
    )
    return cli


@pytest.mark.asyncio
async def test_real_owner_policy_CLI_preview_apply_replay_and_guards(
    direct_pg, tmp_path
):
    import asyncpg

    from devnous.gastos.services.amex_direct_common import DirectConflict

    pg = direct_pg
    # The live context first attests the pre-existing authority/configuration.
    view = await imported(pg, "2026-10-02,BANK SOURCE,-116.00\n")
    await configure(pg, install=False)
    cli = policy_cli(pg)
    path = tmp_path / "private-manifest.json"
    common = [
        "--actor-id",
        str(pg.actor.id),
        "--reason",
        "Explicit reviewed fixture grants",
        "--request-id",
        "owner-cli-test",
    ]
    planned = await cli.run(
        cli.parse_args(["preview", *common, "--manifest-output", str(path)]),
        pg.factory.kw["bind"].url.render_as_string(hide_password=False),
    )
    assert planned["status"] == "preview" and path.stat().st_mode & 0o777 == 0o600
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 0
    )
    apply_args = cli.parse_args(
        [
            "apply",
            *common,
            "--manifest",
            str(path),
            "--preview-hash",
            planned["preview_hash"],
            "--confirmed",
        ]
    )
    result = await cli.run(
        apply_args, pg.factory.kw["bind"].url.render_as_string(hide_password=False)
    )
    assert result["status"] == "installed"
    repeated = await cli.run(
        apply_args, pg.factory.kw["bind"].url.render_as_string(hide_password=False)
    )
    assert (
        repeated["status"] == "exists"
        and repeated["policy_ids"] == result["policy_ids"]
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 1
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid='amex_direct_account_treatment_policies'::regclass AND NOT tgisinternal"
        )
        == 2
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.conn.execute("TRUNCATE amex_direct_account_treatment_policies CASCADE")
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.conn.execute(
            "UPDATE amex_direct_account_treatment_policies SET reason='forged'"
        )
    # The admitted policy allows a real existing source without altering its context.
    current_view = await current(pg, view.statement.id)
    changed = await treatment(
        pg, current_view, current_view.items[0].id, "partner_receivable"
    )
    assert changed.items[0].budget_status == "not_applicable"
    altered = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    altered[0]["partner_debtor_account_ids"].append(str(uuid4()))
    pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(altered))
    with pytest.raises(DirectConflict, match="configuration_changed"):
        await cli.run(
            apply_args, pg.factory.kw["bind"].url.render_as_string(hide_password=False)
        )


@pytest.mark.asyncio
async def test_policy_CLI_late_DDL_failure_restores_guard_and_no_admission(
    direct_pg, tmp_path
):
    import asyncpg
    from sqlalchemy.exc import SQLAlchemyError

    pg = direct_pg
    await imported(pg, "2026-10-02,BANK SOURCE,-116.00\n")
    await configure(pg, install=False)
    cli = policy_cli(pg)
    path = tmp_path / "preview-private.json"
    common = [
        "--actor-id",
        str(pg.actor.id),
        "--reason",
        "Reviewed atomic admission",
        "--request-id",
        "atomic-policy",
    ]
    url = pg.factory.kw["bind"].url.render_as_string(hide_password=False)
    planned = await cli.run(
        cli.parse_args(["preview", *common, "--manifest-output", str(path)]), url
    )
    pg.monkeypatch.setattr(
        cli, "CREATE_GUARD", "SELECT missing_policy_restore_function()"
    )
    with pytest.raises(SQLAlchemyError):
        await cli.run(
            cli.parse_args(
                [
                    "apply",
                    *common,
                    "--manifest",
                    str(path),
                    "--preview-hash",
                    planned["preview_hash"],
                    "--confirmed",
                ]
            ),
            url,
        )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 0
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid='amex_direct_account_treatment_policies'::regclass AND NOT tgisinternal"
        )
        == 2
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.conn.execute("TRUNCATE amex_direct_account_treatment_policies CASCADE")


@pytest.mark.asyncio
async def test_policy_CLI_actor_precedes_exclusive_policy_lock(direct_pg, tmp_path):
    import asyncio

    from sqlalchemy import text

    from devnous.gastos.services.amex_direct_common import authorize

    pg = direct_pg
    await imported(pg, "2026-10-02,BANK SOURCE,-116.00\n")
    await configure(pg, install=False)
    cli = policy_cli(pg)
    original_engine = cli.create_async_engine
    application = "policy_lock_order_" + uuid4().hex
    # Reuse the fixture's engine factory while tagging only the installer connection.
    from sqlalchemy.ext.asyncio import create_async_engine

    pg.monkeypatch.setattr(
        cli,
        "create_async_engine",
        lambda url: create_async_engine(
            url,
            connect_args={
                "server_settings": {
                    "search_path": pg.schema,
                    "application_name": application,
                }
            },
        ),
    )
    path = tmp_path / "lock-order-preview.json"
    common = [
        "--actor-id",
        str(pg.actor.id),
        "--reason",
        "Reviewed lock order",
        "--request-id",
        "lock-order-policy",
    ]
    url = pg.factory.kw["bind"].url.render_as_string(hide_password=False)
    planned = await cli.run(
        cli.parse_args(["preview", *common, "--manifest-output", str(path)]), url
    )
    apply_args = cli.parse_args(
        [
            "apply",
            *common,
            "--manifest",
            str(path),
            "--preview-hash",
            planned["preview_hash"],
            "--confirmed",
        ]
    )
    task = None
    try:
        async with pg.factory.begin() as application_session:
            # Real canonical actor authority lock, as obtained by accounting confirmation.
            await authorize(
                application_session, pg.actor.id, "ver", finance=True, locked=True
            )
            task = asyncio.create_task(cli.run(apply_args, url))

            async def wait_actor_lock():
                while True:
                    pid = await pg.conn.fetchval(
                        "SELECT pid FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock' AND query ILIKE '%FOR UPDATE%'",
                        application,
                    )
                    if pid:
                        return pid
                    await asyncio.sleep(0.01)

            pid = await asyncio.wait_for(wait_actor_lock(), 5)
            exclusive = await pg.conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND relation='amex_direct_account_treatment_policies'::regclass AND mode='AccessExclusiveLock' AND granted)",
                pid,
            )
            assert exclusive is False
            assert (
                await asyncio.wait_for(
                    application_session.scalar(
                        text(
                            "SELECT count(*) FROM amex_direct_account_treatment_policies"
                        )
                    ),
                    2,
                )
                == 0
            )
        result = await asyncio.wait_for(task, 5)
        assert result["status"] == "installed"
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        pg.monkeypatch.setattr(cli, "create_async_engine", original_engine)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("debtor", "policy_invalid_partner_account"),
        ("mapping", "policy_invalid_benefit_budget_line"),
        ("actor", "active_actor_required"),
    ],
)
async def test_policy_preview_rejects_invalid_catalog_or_inactive_actor(
    direct_pg, tmp_path, change, expected
):
    from devnous.gastos.services.amex_direct_common import (
        DirectAccessError,
        DirectConflict,
    )

    pg = direct_pg
    await imported(pg, "2026-10-02,BANK SOURCE,-116.00\n")
    await configure(pg, install=False)
    if change == "debtor":
        await pg.conn.execute(
            "UPDATE cuentas_contables SET activo=false WHERE id=$1", pg.debtor.id
        )
    elif change == "mapping":
        await pg.conn.execute(
            "UPDATE budget_concepts SET cuenta_contable_id=NULL WHERE id=$1",
            pg.concept.id,
        )
    else:
        await pg.conn.execute(
            "UPDATE empleados SET activo=false WHERE id=$1", pg.actor.id
        )
    cli = policy_cli(pg)
    path = tmp_path / "rejected-preview.json"
    args = cli.parse_args(
        [
            "preview",
            "--actor-id",
            str(pg.actor.id),
            "--reason",
            "Reviewed negative catalog",
            "--request-id",
            "invalid-catalog",
            "--manifest-output",
            str(path),
        ]
    )
    with pytest.raises((DirectAccessError, DirectConflict), match=expected):
        await cli.run(
            args, pg.factory.kw["bind"].url.render_as_string(hide_password=False)
        )
    assert not path.exists()
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 0
    )
