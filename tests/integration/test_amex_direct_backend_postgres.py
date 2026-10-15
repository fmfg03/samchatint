"""Backend negative paths on the same owner-migrated disposable PG fixture."""

import importlib.util
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_budget_service as budget
from devnous.gastos.services import amex_direct_context_service as context
from devnous.gastos.services import amex_direct_matching_service as matching

_fixture_path = (
    Path(__file__).resolve().parents[1]
    / "acceptance"
    / "test_amex_sat_budget_direct_postgres.py"
)
_spec = importlib.util.spec_from_file_location(
    "direct_backend_pg_fixture", _fixture_path
)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
direct_pg = _fixture.direct_pg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ("card_inactive", "invalid_amex_liability"),
        ("liability_inactive", "invalid_amex_liability"),
        ("concept_inactive", "budget_account_mapping_required"),
        ("version_closed", "budget_version_not_operational"),
        ("source_amount", "fiscal_source_changed"),
    ],
)
async def test_changed_current_evidence_blocks_whole_cut_without_any_journal(
    direct_pg, change, reason
):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    if change == "card_inactive":
        await pg.conn.execute(
            "UPDATE amex_card_accounts SET active=false WHERE id=$1", pg.card.id
        )
    elif change == "liability_inactive":
        await pg.conn.execute(
            "UPDATE cuentas_contables SET activo=false WHERE id=$1", pg.liability.id
        )
    elif change == "concept_inactive":
        await pg.conn.execute(
            "UPDATE budget_concepts SET active=false WHERE id=$1", pg.concept.id
        )
    elif change == "version_closed":
        await pg.conn.execute(
            "UPDATE budget_versions SET status='closed' WHERE id=$1", pg.version_id
        )
    else:
        await pg.conn.execute(
            "UPDATE expense_reports SET gasto_cantidad=117 WHERE id=$1",
            current.items[0].imported_expense_id,
        )
    async with pg.factory.begin() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert preview.status == "pending"
        assert reason in preview.progress["exceptions"]
        outcome = await accounting.confirm_direct_accounting(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            expected_source_key=preview.source_key,
            expected_source_version=preview.source_version,
            accounting_date=date(2026, 10, 31),
            confirmed=True,
            idempotency_key="blocked",
        )
        assert outcome.status == "pending"
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )


@pytest.mark.asyncio
async def test_profile_correction_never_reinterprets_original_receipts(direct_pg):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    async with pg.factory.begin() as session:
        outcome = await context.set_direct_context(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            company_code=current.context.company_code,
            receiver_rfc=current.context.receiver_rfc,
            currency="MXN",
            profile="debit_positive",
            expected_source_version=current.source_version,
            reason="Requested sign change",
            confirmed=True,
        )
        assert outcome.status == "pending"
        assert outcome.reason == "csv_profile_change_requires_review"
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_statement_direct_contexts")
        == 1
    )
    row = await pg.conn.fetchrow(
        "SELECT movement_kind,raw_signed_amount FROM amex_direct_items"
    )
    assert row["movement_kind"] == "debit" and row["raw_signed_amount"] < 0


@pytest.mark.asyncio
async def test_retired_alias_blocks_confirmed_source_without_new_observation(direct_pg):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    async with pg.factory.begin() as session:
        outcome = await matching.confirm_merchant_alias(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            descriptor="COMERCIO ACME CDMX",
            issuer_rfc="CCC010101CCC",
            reason="Explicit alternate merchant identity",
            confirmed=True,
        )
        assert outcome.status == "ready"
    async with pg.factory() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert (
            preview.status == "pending"
            and preview.reason == "merchant_identity_changed"
        )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_merchant_identity_aliases")
        == 2
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_merchant_identity_aliases WHERE active"
        )
        == 1
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
async def test_cp_return_releases_only_recorded_current_mapping_and_preserves_audit(
    direct_pg,
):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    item = current.items[0]
    async with pg.factory.begin() as session:
        result = await budget.reject_direct_budget_item(
            session,
            actor_id=pg.cp_actor.id,
            item_id=item.id,
            expected_source_version=item.source_version,
            reason="Explicit correction requested",
        )
        assert result.status == "rejected"
    row = await pg.conn.fetchrow("SELECT * FROM amex_direct_items")
    expense = await pg.conn.fetchrow("SELECT * FROM expense_reports")
    assert row["budget_assignment_id"] is None and row["budget_status"] == "classified"
    assert expense["cuenta_contable_id"] is None
    assert expense["contra_cuenta_contable_id"] is None
    event = await pg.conn.fetchval(
        "SELECT payload_json FROM amex_direct_events WHERE action='budget_return'"
    )
    assert (
        "released_mapping"
        in (json.loads(event) if isinstance(event, str) else event)["prior"]
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 1
    )


@pytest.mark.asyncio
async def test_legacy_workflow_never_lists_direct_statement_as_missing_informe(
    direct_pg,
):
    from devnous.gastos.services.amex_statement_workflow_service import (
        list_statement_workflows,
    )

    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    async with pg.factory() as session:
        direct = await context.list_direct_statements(session, actor_id=pg.actor.id)
        assert len(direct) == 1 and direct[0]["statement"].id == current.statement.id
        assert direct[0]["counts"]["classified"] == 1
        legacy = await list_statement_workflows(session, actor=pg.actor)
        assert not legacy["rows"]


@pytest.mark.asyncio
async def test_frozen_export_preserves_statement_identity_after_authority_config_grows(
    direct_pg, monkeypatch
):
    import os

    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    cut = (await _fixture._confirm(pg, await _fixture._cut_args(pg, current))).cut
    config = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    config[0]["operator_note"] = "New allowed configuration metadata"
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(config))
    async with pg.factory() as session:
        data, filename, mime = await accounting.export_direct_cut(
            session, actor_id=pg.actor.id, import_id=current.statement.id, format="csv"
        )
    assert str(cut.id) in filename and mime == "text/csv"
    assert b"Corte AMEX directo" in data
    assert str(current.statement.id).encode() in data
    assert b"INFORME" not in data
    # Fresh scope is still mandatory even for frozen receipts.
    config[0]["actor_scopes"] = []
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(config))
    with pytest.raises(PermissionError, match="actor_scope_denied"):
        async with pg.factory() as session:
            await accounting.export_direct_cut(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                format="csv",
            )


@pytest.mark.asyncio
async def test_real_scoped_catalog_options_and_cp_queue_preserve_role_ownership(
    direct_pg,
):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    async with pg.factory() as session:
        intake = await context.list_direct_intake_options(session, actor_id=pg.actor.id)
        assert [row["id"] for row in intake["cards"]] == [str(pg.card.id)]
        choices = await context.list_direct_classification_options(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert [row["id"] for row in choices["concepts"]] == [str(pg.concept.id)]
        assert choices["edition_years"] == [2026]
        lines = await budget.list_direct_budget_lines(
            session, actor_id=pg.cp_actor.id, item_id=current.items[0].id
        )
        assert [row["id"] for row in lines] == [str(pg.line_id)]
        queue = await budget.list_direct_budget_queue(session, actor_id=pg.cp_actor.id)
        assert [row.id for row in queue] == [current.items[0].id]
        assert queue[0].statement_id == current.statement.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("confirmation", "manual_matching_confirmation_required"),
        ("missing_cfdi", "cfdi_missing"),
        ("negative_tip", "invalid_confirmed_tip"),
        ("excess_tip", "invalid_confirmed_tip"),
        ("applied_mismatch", "manual_applied_amount_conflict"),
    ],
)
async def test_manual_fiscal_link_requires_explicit_exact_evidence(
    direct_pg, case, reason
):
    from decimal import Decimal
    from uuid import uuid4

    pg = direct_pg
    current, observed, cfdi = await _fixture._matched_statement(pg)
    args = dict(
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        item_id=current.items[0].id,
        cfdi_id=cfdi.id,
        expected_source_version=current.source_version,
        reason="Operator manual evidence",
        confirmed=True,
    )
    if case == "confirmation":
        args["confirmed"] = False
    elif case == "missing_cfdi":
        args["cfdi_id"] = uuid4()
    elif case == "negative_tip":
        args["tip_amount"] = Decimal("-1")
    elif case == "excess_tip":
        args["tip_amount"] = Decimal("116")
    else:
        args["applied_amount"] = Decimal("115")
    async with pg.factory.begin() as session:
        result = await matching.link_direct_cfdi_manual(session, **args)
        assert result.status == "pending" and result.reason == reason
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
async def test_manual_link_uses_canonical_owner_and_records_confirmed_decision(
    direct_pg,
):
    pg = direct_pg
    current, observed, cfdi = await _fixture._matched_statement(pg)
    async with pg.factory.begin() as session:
        result = await matching.link_direct_cfdi_manual(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_id=current.items[0].id,
            cfdi_id=cfdi.id,
            expected_source_version=current.source_version,
            reason="Confirmed fiscal evidence",
            confirmed=True,
        )
        assert result.status == "matched"
        assert result.items[0].match_proof["manual"] is True
    assert (
        await pg.conn.fetchval("SELECT cfdi_report_id FROM expense_reports") == cfdi.id
    )


@pytest.mark.asyncio
async def test_report_binders_cannot_take_over_a_real_direct_import(direct_pg):
    from uuid import uuid4

    from devnous.gastos.services.amex_recognition_service import (
        bind_amex_consumption,
        bind_amex_report_bank_consumptions,
    )

    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    expense_id = current.items[0].imported_expense_id
    async with pg.factory.begin() as session:
        legacy = await bind_amex_consumption(
            session,
            imported_expense_id=expense_id,
            report_expense_ids=[],
            actor_id=pg.actor.id,
            statement_only_reason="Explicit legacy exception",
        )
        bridge = await bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=expense_id,
            report_consumption_ids=[uuid4()],
            actor_id=pg.actor.id,
        )
        assert legacy.reason == bridge.reason == "direct_statement_owner_required"
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )


@pytest.mark.asyncio
async def test_report_cut_rejects_direct_owned_items_before_any_journal(
    direct_pg, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from devnous.gastos.services import amex_accounting_cut_service as cuts
    from devnous.gastos.services import amex_recognition_service as recognition

    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    async with pg.factory() as session:
        expense = await session.get(
            _fixture.models.ExpenseReport, current.items[0].imported_expense_id
        )
        monkeypatch.setattr(
            cuts,
            "_informe",
            AsyncMock(return_value=SimpleNamespace(cuenta_gastos_id=uuid4())),
        )
        monkeypatch.setattr(cuts, "load_expenses", AsyncMock(return_value=[expense]))
        # The existing report owner must reject even if its earlier selection
        # supplies an item whose real intake receipt belongs to direct AMEX.
        outcome = await cuts.create_amex_accounting_cut(
            session,
            informe_id=uuid4(),
            actor=pg.actor,
            expected_versions={},
            accounting_date=date(2026, 10, 31),
        )
        assert outcome.reason == "direct_statement_owner_required"
        assert await recognition.direct_statement_source(session, [expense.id])
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
async def test_missing_accounting_period_never_implies_open_or_creates_journal(
    direct_pg,
):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    args = await _fixture._cut_args(pg, current)
    await pg.conn.execute("DELETE FROM accounting_close_periods")
    result = await _fixture._confirm(pg, args)
    assert result.status == "pending"
    assert result.reason == "explicit_open_accounting_period_required"
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 0
    )


@pytest.mark.asyncio
async def test_sat_continuation_caps_queries_and_concurrency_using_server_cursor(
    direct_pg,
):
    import asyncio

    from devnous.gastos.services import amex_direct_status_service as status
    from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg)
    async with pg.factory.begin() as session:
        for index in range(50):
            await ingest_cfdi_xml(
                session, _fixture._synthetic_sat_xml(str(uuid4())), source="sat"
            )

    class Transport:
        calls = 0
        active = 0
        maximum = 0

    transport = Transport()

    from sqlalchemy import text

    observation_lock = asyncio.Lock()

    async def observe(**identity):
        transport.calls += 1
        transport.active += 1
        transport.maximum = max(transport.maximum, transport.active)
        async with observation_lock, pg.factory.begin() as session:
            assert await session.scalar(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtext('samchat:amex:shared-recognition'))"
                )
            )
        await asyncio.sleep(0.002)
        transport.active -= 1
        return {"request": identity, "status": "vigente"}

    transport.consult_status = observe
    first = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=current.source_version,
        request_key="bounded51",
        consented=True,
        status_service=transport,
    )
    assert first.progress["total"] == 51
    assert first.progress["cursor"] == 50
    assert transport.calls == 49  # One real, fresh cached receipt was reused.
    assert transport.maximum <= 2
    second = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=first.source_version,
        request_key="bounded51",
        consented=True,
        status_service=transport,
    )
    assert second.progress["cursor"] == 51 and transport.calls == 50
    assert second.progress["observed"] == 51
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
async def test_sat_deadline_cancels_unobserved_calls_without_fake_receipts(
    direct_pg, monkeypatch
):
    import asyncio

    from devnous.gastos.services import amex_direct_status_service as status

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg)
    await pg.conn.execute("UPDATE amex_direct_items SET match_status='pending'")
    # Cache invalidation is evidenced by a new XML identity, not a fake status.
    await pg.conn.execute("UPDATE cfdi_reports SET xml_raw=xml_raw || E'\\n'")
    monkeypatch.setattr(status, "EXTERNAL_BUDGET_SECONDS", 0.025)
    cancelled = []

    class Slow:
        async def consult_status(self, **identity):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)

    async with asyncio.timeout(0.5):
        result = await status.consult_direct_statement_status(
            pg.factory,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            expected_source_version=current.source_version,
            request_key="deadline",
            consented=True,
            status_service=Slow(),
        )
    assert cancelled
    assert result.progress["cursor"] == 0 and not result.progress["running"]
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_cfdi_status_receipts") == 1


@pytest.mark.asyncio
async def test_sat_observation_cannot_advance_cursor_after_source_revision(direct_pg):
    from devnous.gastos.services import amex_direct_status_service as status

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg)
    await pg.conn.execute("UPDATE cfdi_reports SET xml_raw=xml_raw || E'\\n'")

    class ReviseDuringSOAP:
        async def consult_status(self, **identity):
            async with pg.factory.begin() as session:
                classified = await budget.classify_direct_items(
                    session,
                    actor_id=pg.actor.id,
                    import_id=current.statement.id,
                    item_ids=[current.items[0].id],
                    tournament_id=pg.tournament.id,
                    project_code=None,
                    budget_concept_id=pg.concept.id,
                    edition_year=2026,
                    expected_versions={
                        str(current.items[0].id): current.items[0].source_version
                    },
                    reason="Accounting changed source during SOAP",
                )
                assert classified.status == "classified"
            return {"request": identity, "status": "vigente"}

    result = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=current.source_version,
        request_key="stale-source",
        consented=True,
        status_service=ReviseDuringSOAP(),
    )
    assert result.reason == "sat_progress_source_changed"
    row = await pg.conn.fetchrow(
        "SELECT source_version,progress_json FROM amex_statement_direct_state"
    )
    progress = json.loads(row["progress_json"])
    assert progress["cursor"] == 0 and not progress["completed"]
    assert row["source_version"] > current.source_version
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_cfdi_status_receipts") == 2


@pytest.mark.asyncio
async def test_classification_cannot_assign_expense_to_other_edition_year(
    direct_pg, monkeypatch
):
    import os

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg)
    config = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    config[0]["edition_years"].append(2025)
    for scope in config[0]["actor_scopes"]:
        scope["edition_years"].append(2025)
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(config))
    # A real context revision records the configured authority before classifying.
    async with pg.factory.begin() as session:
        await context.set_direct_context(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            company_code="TEST",
            receiver_rfc="BBB010101BBB",
            currency="MXN",
            profile="debit_negative",
            expected_source_version=current.source_version,
            reason="Added approved edition scope",
            confirmed=True,
        )
    current = await _fixture._direct_current(pg, current.statement.id)
    async with pg.factory.begin() as session:
        result = await budget.classify_direct_items(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_ids=[current.items[0].id],
            tournament_id=pg.tournament.id,
            project_code=None,
            budget_concept_id=pg.concept.id,
            edition_year=2025,
            expected_versions={
                str(current.items[0].id): current.items[0].source_version
            },
        )
    assert result.reason == "expense_date_outside_budget_edition"
    assert await pg.conn.fetchval("SELECT edition_year FROM amex_direct_items") is None


@pytest.mark.asyncio
async def test_real_retention_posts_bank_net_and_freezes_canonical_custom_mapping(
    direct_pg, monkeypatch
):
    from decimal import Decimal

    pg = direct_pg
    retention = _fixture.models.CuentaContable(
        id=uuid4(),
        codigo="2199-TEST-ISR",
        nombre="Retenciones ISR custom",
        tipo="pasivo",
        activo=True,
    )
    async with pg.factory.begin() as session:
        session.add(retention)
    monkeypatch.setenv("ASSISTANT_ACCOUNTING_RETENTION_ISR_CUENTA", retention.codigo)
    original_xml = _fixture._synthetic_sat_xml()
    xml = (
        original_xml.replace('Total="116.00"', 'Total="106.00"')
        .replace(
            '<cfdi:Impuestos TotalImpuestosTrasladados="16.00">',
            '<cfdi:Impuestos TotalImpuestosTrasladados="16.00" '
            'TotalImpuestosRetenidos="10.00">',
        )
        .replace(
            "</cfdi:Traslados></cfdi:Impuestos>\n <cfdi:Complemento>",
            "</cfdi:Traslados><cfdi:Retenciones><cfdi:Retencion "
            'Impuesto="001" Importe="10.00"/></cfdi:Retenciones>'
            "</cfdi:Impuestos>\n <cfdi:Complemento>",
        )
    )
    monkeypatch.setattr(_fixture, "_synthetic_sat_xml", lambda: xml)
    matched, _, _ = await _fixture._matched_statement(
        pg, content=b"Date,Description,Amount\n2026-10-02,COMERCIO ACME CDMX,-106.00\n"
    )
    current = await _fixture._assign_all_items(pg, matched)
    args = await _fixture._cut_args(pg, current)
    from sqlalchemy.exc import DBAPIError

    # Fail after the canonical mapping and its event have been flushed, before
    # sealing the cut. All effects still belong to this one transaction.
    await pg.conn.execute(
        """
        CREATE FUNCTION fail_after_retention_mapping() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'cut failure after retention mapping'; END $$;
        CREATE TRIGGER fail_after_retention_mapping
        BEFORE INSERT ON amex_direct_accounting_cuts
        FOR EACH ROW EXECUTE FUNCTION fail_after_retention_mapping();
    """
    )
    before_mapping = await pg.conn.fetchval(
        "SELECT retencion_cuentas_json FROM expense_reports"
    )
    with pytest.raises(DBAPIError, match="cut failure after retention mapping"):
        await _fixture._confirm(pg, args)
    assert (
        await pg.conn.fetchval("SELECT retencion_cuentas_json FROM expense_reports")
        == before_mapping
    )
    for table in (
        "accounting_polizas",
        "amex_direct_accounting_cuts",
        "amex_recognition_consumptions",
    ):
        assert await pg.conn.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_events "
            "WHERE action='canonical_retention_mapping'"
        )
        == 0
    )
    await pg.conn.execute(
        "DROP TRIGGER fail_after_retention_mapping ON amex_direct_accounting_cuts"
    )
    result = await _fixture._confirm(pg, args)
    assert result.status == "recognized", result.reason
    lines = await pg.conn.fetch(
        "SELECT cuenta_codigo,debe,haber FROM accounting_poliza_lines"
    )
    actual = {row["cuenta_codigo"]: (row["debe"], row["haber"]) for row in lines}
    assert actual[retention.codigo] == (0, Decimal("10"))
    assert actual[pg.liability.codigo] == (0, Decimal("106"))
    assert (
        sum(row["debe"] for row in lines) == sum(row["haber"] for row in lines) == 116
    )
    overrides = await pg.conn.fetchval(
        "SELECT retencion_cuentas_json FROM expense_reports"
    )
    assert json.loads(overrides)["001"] == str(retention.id)
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_events "
            "WHERE action='canonical_retention_mapping'"
        )
        == 1
    )
    assert (await _fixture._confirm(pg, args)).cut.id == result.cut.id
    async with pg.factory() as session:
        payload, _, _ = await accounting.export_direct_cut(
            session, actor_id=pg.actor.id, import_id=current.statement.id, format="xlsx"
        )
        assert payload.startswith(b"PK")
