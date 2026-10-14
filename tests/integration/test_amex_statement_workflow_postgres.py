"""Canonical real PostgreSQL statement confirmation/link atomicity."""

from datetime import date
from pathlib import Path
import re
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from test_amex_approved_informe_service_postgres import (
    pg as approved_pg_fixture,
    legacy_pg as legacy_pg_fixture,
    setup_report,
    prepare,
    payload,
)
from test_amex_shared_recognition_postgres import _journal_count
from devnous.gastos import models
from devnous.gastos.services import amex_statement_workflow_service as service
from devnous.gastos.services.amex_attention_policy import (
    EXCLUDED_EMPLOYEE_IDS,
    load_amex_attention_context,
)

approved_pg = approved_pg_fixture
legacy_pg = legacy_pg_fixture


@pytest_asyncio.fixture
async def pg(approved_pg):
    engine = approved_pg.factory.kw["bind"]
    ddl = (
        Path(__file__).resolve().parents[2]
        / "database/migrations/20261013_amex_statement_workflow.sql"
    ).read_text()
    ddl = re.sub(r"(?m)^(BEGIN|COMMIT);\s*$", "", ddl)
    async with engine.begin() as connection:
        for table in [
            "amex_statement_cut_links",
            "amex_statement_import_items",
            "amex_statement_imports",
        ]:
            await connection.execute(text("DROP TABLE " + table))
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute(ddl)
    return approved_pg


async def statement(pg, data, normalized=None):
    async with pg.factory.begin() as session:
        odilon = models.Empleado(
            id=UUID(EXCLUDED_EMPLOYEE_IDS[0]), nombre="Policy", activo=True
        )
        session.add(odilon)
        card = (await session.scalars(select(models.AmexCardAccount))).one()
        bank = await session.get(models.ExpenseReport, data.imports[0].id)
        bank.empleado_id = pg.actor
        row = models.AmexStatementImport(
            id=uuid4(),
            card_account_id=card.id,
            employee_id=pg.actor,
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 31),
            file_sha256="a" * 64,
            filename="test.csv",
            parser_version="test",
            actor_id=pg.actor,
            total_rows=1,
            imported_count=1,
            skipped_count=0,
            summary_json={},
        )
        session.add(row)
        await session.flush()
        session.add(
            models.AmexStatementImportItem(
                import_id=row.id,
                row_number=2,
                imported_expense_id=bank.id,
                status="imported",
                normalized_json=(
                    normalized
                    if normalized is not None
                    else {
                        "amount": str(bank.gasto_cantidad),
                        "currency": bank.currency,
                    }
                ),
            )
        )
        await session.flush()
        return row.id


@pytest.mark.asyncio
async def test_real_cut_and_link_replay_keep_original_ids(pg):
    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    values = payload(data, preview)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        result = await service.confirm_statement_informe(
            session,
            import_id=statement_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            **values,
        )
        assert result.status == "created", result.reason
        cut_id = result.cut.id
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        repeated = await service.confirm_statement_informe(
            session,
            import_id=statement_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            **values,
        )
        assert repeated.status == "exists" and repeated.cut.id == cut_id
        links = (await session.scalars(select(models.AmexStatementCutLink))).all()
        assert (
            len(links) == 1
            and links[0].cut_id == cut_id
            and links[0].actor_id == pg.actor
        )
    assert await _journal_count(pg) == 1
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        dashboard = await service.list_statement_workflows(session, actor=actor)
        assert dashboard["rows"][0]["linked_cut_count"] == 1
        exported = await service.export_statement(
            session, import_id=statement_id, actor=actor, export_format="csv"
        )
        assert exported


@pytest.mark.asyncio
async def test_link_error_reverts_real_cut_movements(pg, monkeypatch):
    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    values = payload(data, preview)
    monkeypatch.setattr(
        service,
        "_link",
        AsyncMock(side_effect=service.WorkflowConflict("invalid link")),
    )
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        with pytest.raises(service.WorkflowConflict):
            await service.confirm_statement_informe(
                session,
                import_id=statement_id,
                actor=actor,
                selected_informe_ids=[data.informe.id],
                **values,
            )
        assert not (await session.scalars(select(models.AmexAccountingCut))).all()
        assert not (
            await session.scalars(select(models.AmexReportSourceEvidence))
        ).all()
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
async def test_bank_rows_remain_visible_and_get_does_not_flush(pg):
    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        report = await session.get(models.Documento, data.informe.id)
        account = await session.get(models.CuentaDeGastos, report.cuenta_gastos_id)
        report.beneficiario_empleado_id = UUID(EXCLUDED_EMPLOYEE_IDS[0])
        account.beneficiario_empleado_id = UUID(EXCLUDED_EMPLOYEE_IDS[0])
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert len(view["items"]) == 1 and not view["reports"]
        assert list(view["item_states"].values())[0]["status"] == "pendiente"
        bank = await session.get(models.ExpenseReport, data.imports[0].id)
        ctx = await load_amex_attention_context(session, expenses=[bank])
        assert ctx.expense_decisions[bank.id].visible


@pytest.mark.asyncio
async def test_incorporation_uses_frozen_full_cut_without_reposting(pg):
    from devnous.gastos.services.amex_accounting_cut_service import (
        confirm_amex_approved_informe_cut,
    )

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        confirmed = await confirm_amex_approved_informe_cut(
            session, actor=actor, **payload(data, preview)
        )
        assert confirmed.status == "created", confirmed.reason
        cut_id = confirmed.cut.id
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        link = await service.incorporate_statement_cut(
            session,
            import_id=statement_id,
            cut_id=cut_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            confirmed=True,
        )
        first_id = link.id
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        repeated = await service.incorporate_statement_cut(
            session,
            import_id=statement_id,
            cut_id=cut_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            confirmed=True,
        )
        assert repeated.id == first_id
        with pytest.raises(service.WorkflowConflict):
            await service.incorporate_statement_cut(
                session,
                import_id=statement_id,
                cut_id=cut_id,
                actor=actor,
                selected_informe_ids=[data.informe.id],
                confirmed=True,
                reason="changed",
            )
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_incorporation_blocks_changed_current_source(pg):
    from devnous.gastos.services.amex_accounting_cut_service import (
        confirm_amex_approved_informe_cut,
    )

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        confirmed = await confirm_amex_approved_informe_cut(
            session, actor=actor, **payload(data, preview)
        )
        cut_id = confirmed.cut.id
    async with pg.factory.begin() as session:
        expense = await session.get(models.ExpenseReport, data.reports[0].id)
        expense.concepto = "Edited after frozen cut"
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        with pytest.raises(service.WorkflowConflict, match="revisión o ajuste"):
            await service.incorporate_statement_cut(
                session,
                import_id=statement_id,
                cut_id=cut_id,
                actor=actor,
                selected_informe_ids=[data.informe.id],
                confirmed=True,
            )
        assert not (await session.scalars(select(models.AmexStatementCutLink))).all()
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("informe_state", ["missing", "borrador"])
async def test_orphan_confirmation_cannot_post_and_keeps_bank_total(pg, informe_state):
    from decimal import Decimal

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    if informe_state == "borrador":
        async with pg.factory.begin() as session:
            doc = await session.get(models.Documento, data.informe.id)
            doc.estado = "borrador"
        informe_id = data.informe.id
    else:
        informe_id = uuid4()
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        with pytest.raises(service.WorkflowConflict, match="Falta informe aprobado"):
            await service.confirm_statement_informe(
                session,
                import_id=statement_id,
                informe_id=informe_id,
                actor=actor,
                selected_informe_ids=[informe_id],
            )
        assert not (await session.scalars(select(models.AmexAccountingCut))).all()
        assert not (await session.scalars(select(models.AmexStatementCutLink))).all()
    assert await _journal_count(pg) == 0
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert view["imported_totals"] == {"MXN": Decimal("116.0")}
        assert view["operational_pending_count"] == 1
        assert view["bank_pending_count"] == 1
        assert view["bank_linked_count"] == 0
        assert view["financial_progress"]["coverage_unknown"] is True
        assert view["financial_progress"]["selected_source_count"] is None
        assert (
            "Falta informe aprobado"
            in next(iter(view["item_states"].values()))["blockers"]
        )


@pytest.mark.asyncio
async def test_actual_bank_association_and_selected_policy_progress_are_separate(pg):
    from devnous.gastos.services import amex_recognition_service as recognition

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        result = await service.confirm_statement_informe(
            session,
            import_id=statement_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            **payload(data, preview),
        )
        assert result.status == "created"
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(
            session, statement_id, actor=actor, selected_informe_ids=[data.informe.id]
        )
        assert view["financial_progress"]["selected_generated_count"] == 1
        assert view["financial_progress"]["selected_source_count"] == 1
        assert view["financial_progress"]["coverage_unknown"] is True
        assert view["bank_pending_count"] == 1
    async with pg.factory.begin() as session:
        consumption = (
            await session.scalars(
                select(models.AmexRecognitionConsumption).where(
                    models.AmexRecognitionConsumption.source_report_expense_id
                    == data.reports[0].id
                )
            )
        ).one()
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=[consumption.id],
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(
            session, statement_id, actor=actor, selected_informe_ids=[data.informe.id]
        )
        assert view["bank_linked_count"] == 1 and view["bank_pending_count"] == 0
        assert view["operational_pending_count"] == 0, view["item_states"]
        assert view["financial_progress"]["coverage_unknown"] is False
        assert next(iter(view["item_states"].values()))["source_ids"] == [
            str(data.informe.id)
        ]
    async with pg.factory.begin() as session:
        doc = await session.get(models.Documento, data.informe.id)
        account = await session.get(models.CuentaDeGastos, doc.cuenta_gastos_id)
        doc.beneficiario_empleado_id = UUID(EXCLUDED_EMPLOYEE_IDS[0])
        account.beneficiario_empleado_id = UUID(EXCLUDED_EMPLOYEE_IDS[0])
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert not view["reports"] and len(view["items"]) == 1
        assert view["operational_pending_count"] == 0, view["item_states"]
        assert next(iter(view["item_states"].values()))["by_account"] is True


@pytest.mark.asyncio
async def test_statement_total_is_original_immutable_receipt_not_editable_expense(pg):
    from decimal import Decimal

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    async with pg.factory.begin() as session:
        bank = await session.get(models.ExpenseReport, data.imports[0].id)
        bank.gasto_cantidad = 999
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert view["imported_total"] == Decimal("116.0")
        assert view["imported_totals"] == {"MXN": Decimal("116.0")}
        assert view["totals_unknown_count"] == 0
        assert view["expenses"][data.imports[0].id].gasto_cantidad == 999


@pytest.mark.asyncio
async def test_missing_original_amount_is_unknown_not_zero(pg):
    data = await setup_report(pg)
    statement_id = await statement(pg, data, normalized={})
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert view["imported_totals"] == {}
        assert view["imported_total"] is None
        assert view["totals_unknown_count"] == 1
        assert view["operational_pending_count"] == 1
        assert (
            "Falta importe original válido en el estado"
            in next(iter(view["item_states"].values()))["blockers"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("associated", [False, True])
async def test_canceled_imported_charge_visible_with_original_total_and_bank_history(
    pg, associated
):
    from decimal import Decimal
    from devnous.gastos.services import amex_recognition_service as recognition

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    if associated:
        # The legacy representation owner permits this historic cancellation.
        # Report-first bridges have a stronger guard, tested separately below.
        async with pg.factory.begin() as session:
            bound = await recognition.bind_amex_consumption(
                session,
                imported_expense_id=data.imports[0].id,
                report_expense_ids=[data.reports[0].id],
                actor_id=pg.actor,
            )
            assert bound.status in {
                "created",
                "exists",
                "linked",
                "bound",
            }, bound.reason
    async with pg.factory.begin() as session:
        bank = await session.get(models.ExpenseReport, data.imports[0].id)
        bank.estado_gasto = "cancelado"
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        item_state = next(iter(view["item_states"].values()))
        assert item_state["status"] == "cancelado"
        assert item_state["bank_associated"] is associated
        assert "cancelado" in item_state["reason"]
        assert "Cargo importado cancelado: requiere revisión" in item_state["blockers"]
        assert view["operational_pending_count"] == 1
        assert view["imported_total"] == Decimal("116.0") and len(view["items"]) == 1
        assert view["bank_linked_count"] == int(associated)
        assert view["bank_pending_count"] == int(not associated)
        assert not view["links"]
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
async def test_report_first_bridge_cancellation_guard_is_preserved(pg):
    from sqlalchemy.exc import IntegrityError
    from devnous.gastos.services import amex_recognition_service as recognition

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        result = await service.confirm_statement_informe(
            session,
            import_id=statement_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            **payload(data, preview),
        )
        assert result.status == "created", result.reason
        consumption = (
            await session.scalars(
                select(models.AmexRecognitionConsumption).where(
                    models.AmexRecognitionConsumption.source_report_expense_id
                    == data.reports[0].id
                )
            )
        ).one()
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=[consumption.id],
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
    with pytest.raises(IntegrityError, match="Bridged economic source cannot change"):
        async with pg.factory.begin() as session:
            bank = await session.get(models.ExpenseReport, data.imports[0].id)
            bank.estado_gasto = "cancelado"
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert view["bank_linked_count"] == 1 and view["operational_pending_count"] == 0
        assert next(iter(view["item_states"].values()))["status"] == "vinculado"
        assert len(view["links"]) == 1
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,eligible",
    [
        ("middle", True),
        ("start", True),
        ("end_evening", True),
        ("before", False),
        ("after", False),
        ("other_card", False),
        ("missing_card", False),
        ("cancelled", False),
        ("not_amex", False),
        ("unapproved", False),
        ("missing_account", False),
        ("cross_year", True),
    ],
)
async def test_contextual_selector_real_sql_boundaries(pg, case, eligible):
    from datetime import datetime

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    async with pg.factory.begin() as session:
        expense = await session.get(models.ExpenseReport, data.reports[0].id)
        doc = await session.get(models.Documento, data.informe.id)
        if case == "start":
            expense.fecha = datetime(2026, 10, 1)
        elif case == "end_evening":
            expense.fecha = datetime(2026, 10, 31, 23, 59, 59)
        elif case == "before":
            expense.fecha = datetime(2026, 9, 30, 23, 59, 59)
        elif case == "after":
            expense.fecha = datetime(2026, 11, 1)
        elif case == "other_card":
            expense.ultimos_4_digitos = "6007"
        elif case == "missing_card":
            expense.ultimos_4_digitos = None
        elif case == "cancelled":
            expense.estado_gasto = "cancelado"
        elif case == "not_amex":
            expense.pagado_con_amex_empresa = False
        elif case == "unapproved":
            doc.estado = "borrador"
        elif case == "missing_account":
            expense.cuenta_gastos_id = None
        elif case == "cross_year":
            # Original import declaration is immutable; test the same SQL owner
            # predicate with an explicit alternative read context, not a rewrite.
            expense.fecha = datetime(2027, 1, 2, 23, 59)
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        if case == "cross_year":
            from types import SimpleNamespace

            card = await session.get(models.AmexCardAccount, data.card.id)
            predicate = service._statement_report_eligibility(
                SimpleNamespace(
                    card_account_id=card.id,
                    period_start=date(2026, 12, 31),
                    period_end=date(2027, 1, 2),
                ),
                card,
            )
            reports = (
                await session.scalars(select(models.Documento).where(predicate))
            ).all()
        else:
            view = await service.statement_workflow_view(
                session,
                statement_id,
                actor=actor,
                selected_informe_ids=[data.informe.id],
            )
            reports = view["reports"]
            assert view["selection_rejected"] is (not eligible)
            assert bool(view["selected_reports"]) is eligible
            assert bool(view["selection_warning"]) is (not eligible)
            assert len(view["items"]) == 1 and view["imported_total"] == 116
        assert (data.informe.id in {doc.id for doc in reports}) is eligible


@pytest.mark.asyncio
async def test_contextual_selector_same_partida_and_complete_preview(pg):
    from datetime import datetime

    data = await setup_report(pg, count=3)
    statement_id = await statement(pg, data)
    async with pg.factory.begin() as session:
        first = await session.get(models.ExpenseReport, data.reports[0].id)
        first.fecha = datetime(2026, 9, 30)
        second = await session.get(models.ExpenseReport, data.reports[1].id)
        second.ultimos_4_digitos = "6007"
        third = await session.get(models.ExpenseReport, data.reports[2].id)
        third.fecha = datetime(2026, 11, 1)
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert not view["reports"]  # Card and period on different rows never combine.
    async with pg.factory.begin() as session:
        third = await session.get(models.ExpenseReport, data.reports[2].id)
        third.fecha = datetime(2026, 10, 31, 23, 59)
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(
            session, statement_id, actor=actor, selected_informe_ids=[data.informe.id]
        )
        assert [doc.id for doc in view["reports"]] == [data.informe.id]
        preview = view["selected_reports"][0]["preview"]
        assert {row.id for row in preview.expenses} == {row.id for row in data.reports}
        assert sum(row.gasto_cantidad for row in preview.expenses) == 348


@pytest.mark.asyncio
async def test_contextual_selector_recognized_card_uuid_conflict(pg):
    from devnous.gastos.services import amex_recognition_service as recognition

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    async with pg.factory.begin() as session:
        second_card = models.AmexCardAccount(
            id=uuid4(),
            last4="6007",
            active=True,
            card_label="Other",
            cardholder_key="empresa",
            liability_cuenta_contable_id=data.liability.id,
        )
        session.add(second_card)
        await session.flush()
        bound = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=data.imports[0].id,
            report_expense_ids=[data.reports[0].id],
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
        # Existing legacy identities may be inconsistent. Its actual guard permits
        # this unposted update; no disabling trigger or fabricated bank match.
        bound.consumption.card_account_id = second_card.id
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(
            session, statement_id, actor=actor, selected_informe_ids=[data.informe.id]
        )
        assert not view["reports"] and view["selection_rejected"]
        assert len(view["items"]) == 1


@pytest.mark.asyncio
async def test_contextual_selector_preserves_saved_outside_context_cut_and_export(pg):
    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    preview = await prepare(pg, data)
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        result = await service.confirm_statement_informe(
            session,
            import_id=statement_id,
            actor=actor,
            selected_informe_ids=[data.informe.id],
            **payload(data, preview),
        )
        cut_id = result.cut.id
        # Editing discovery context is not authority to erase frozen evidence.
        card = await session.get(models.AmexCardAccount, data.card.id)
        card.last4 = "6007"
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        view = await service.statement_workflow_view(session, statement_id, actor=actor)
        assert not view["reports"]
        assert view["selected_reports"][0]["document"].id == data.informe.id
        assert {cut.id for cut in view["cuts"]} == {cut_id}
        explicit = await service.statement_workflow_view(
            session, statement_id, actor=actor, selected_informe_ids=[data.informe.id]
        )
        assert explicit["selection_rejected"] and not explicit["selected_reports"]
        assert {cut.id for cut in explicit["cuts"]} == {cut_id}
        assert await service.export_statement(
            session, import_id=statement_id, actor=actor, export_format="csv"
        )
    assert await _journal_count(pg) == 1
