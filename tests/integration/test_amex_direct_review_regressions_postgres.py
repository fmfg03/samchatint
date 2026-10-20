"""Real owner boundaries and resumable SAT evidence review regressions."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_amex_direct_backend_postgres import _fixture
from test_amex_direct_backend_postgres import direct_pg as _direct_pg_fixture

from devnous.gastos.services import amex_direct_status_service as status

direct_pg = _direct_pg_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["transport", "invalid_request", "error", "desconocido"]
)
async def test_same_sat_request_key_retries_unsuccessful_observation(
    direct_pg, failure
):
    pg = direct_pg
    current, _, _ = await _fixture._matched_statement(pg)
    await pg.conn.execute("UPDATE cfdi_reports SET xml_raw=xml_raw || E'\\n'")

    class Failed:
        async def consult_status(self, **identity):
            if failure == "transport":
                raise OSError("Synthetic unavailable transport")
            if failure == "invalid_request":
                return {"request": {}, "status": "vigente"}
            return {"request": identity, "status": failure}

    failed = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=current.source_version,
        request_key="same-retry-key",
        consented=True,
        status_service=Failed(),
    )
    observed = _fixture.ObservedSAT("vigente")
    retried = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=failed.source_version,
        request_key="same-retry-key",
        consented=True,
        status_service=observed,
    )
    assert len(observed.requests) == 1
    assert retried.progress["cursor"] == 1
    assert retried.progress["observed"] == 1
    assert retried.items[0].match_status == "matched"
    assert len(retried.progress["attempts"]) == 2
    first_attempt = retried.progress["attempts"][0]["outcomes"]["0"]
    if failure in {"error", "desconocido"}:
        assert first_attempt["status"] == failure and first_attempt["receipt_id"]
    else:
        assert first_attempt["receipt_id"] is None


@pytest.mark.asyncio
async def test_same_completed_sat_key_requeries_changed_xml(direct_pg):
    pg = direct_pg
    current, _, _ = await _fixture._matched_statement(pg)
    first = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=current.source_version,
        request_key="completed-key",
        consented=True,
        status_service=_fixture.ObservedSAT("vigente"),
    )
    await pg.conn.execute("UPDATE cfdi_reports SET xml_raw=xml_raw || E'\\n'")
    observed = _fixture.ObservedSAT("vigente")
    retried = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=first.source_version,
        request_key="completed-key",
        consented=True,
        status_service=observed,
    )
    assert len(observed.requests) == 1
    assert retried.progress["observed"] == 1


@pytest.mark.asyncio
async def test_tampered_upload_employee_from_other_context_has_no_source_rows(
    direct_pg, monkeypatch
):
    from devnous.gastos.services import amex_direct_intake_service as intake

    pg = direct_pg
    configs = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    configs[0]["employee_ids"] = [str(pg.actor.id)]
    # A person allowed by another declared entity cannot be selected for TEST.
    other = {
        **configs[0],
        "company_code": "OTHER",
        "receiver_rfc": "CCC010101CCC",
        "employee_ids": [str(pg.owner.id)],
    }
    monkeypatch.setenv(
        "AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([configs[0], other])
    )
    async with pg.factory.begin() as session:
        result = await intake.import_direct_statement(
            session,
            actor_id=pg.actor.id,
            employee_id=pg.owner.id,
            card_account_id=pg.card.id,
            period_start=_fixture.date(2026, 10, 1),
            period_end=_fixture.date(2026, 10, 31),
            content=b"Date,Description,Amount\n2026-10-02,Merchant,-116.00\n",
            filename="tampered.csv",
            mime_type="text/csv",
            profile="debit_negative",
            company_code="TEST",
            receiver_rfc="BBB010101BBB",
            currency="MXN",
            idempotency_key="tampered-employee",
            confirmed=True,
        )
        assert result.status == "pending" and result.reason == "employee_context_denied"
    for table in (
        "amex_statement_imports",
        "amex_statement_source_files",
        "expense_reports",
    ):
        assert await pg.conn.fetchval(f"SELECT count(*) FROM {table}") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_legacy_cfdi_link_handler_cannot_mutate_direct_owned_expense(
    direct_pg, monkeypatch, bulk
):
    from devnous.gastos.routes import user_routes as routes

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg, alias=False)
    suggestion = AsyncMock(return_value=SimpleNamespace(cfdi_uuid=cfdi.cfdi_uuid))
    monkeypatch.setattr(routes, "validate_amex_cfdi_suggestion", suggestion)
    async with pg.factory() as session:
        if bulk:
            await routes.amex_conciliacion_link_cfdi_bulk(
                session=session,
                current_empleado=pg.actor,
                selected_links=[f"{current.items[0].imported_expense_id}:{cfdi.id}"],
                year=2026,
                month=10,
                return_to=None,
            )
        else:
            await routes.amex_conciliacion_link_cfdi(
                session=session,
                current_empleado=pg.actor,
                expense_id=str(current.items[0].imported_expense_id),
                cfdi_report_id=str(cfdi.id),
                year=2026,
                month=10,
                return_to=None,
            )
    assert await pg.conn.fetchval("SELECT cfdi_report_id FROM expense_reports") is None
    suggestion.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_sat_cursor_revalidates_receipt_ttl(direct_pg, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from devnous.gastos.services import amex_direct_matching_service as matching

    pg = direct_pg
    current, _, _ = await _fixture._matched_statement(pg)
    first = await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=current.source_version,
        request_key="completed-ttl",
        consented=True,
        status_service=_fixture.ObservedSAT("vigente"),
    )
    future = datetime.now(timezone.utc) + timedelta(hours=25)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(matching, "datetime", ExpiredClock)
    observed = _fixture.ObservedSAT("vigente")
    await status.consult_direct_statement_status(
        pg.factory,
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_version=first.source_version,
        request_key="completed-ttl",
        consented=True,
        status_service=observed,
    )
    assert len(observed.requests) == 1


@pytest.mark.asyncio
async def test_tampered_employee_multipart_post_cannot_use_other_context_union(
    direct_pg, monkeypatch
):
    import re

    import httpx
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware

    from devnous.gastos.routes import admin_amex_direct_routes as routes

    pg = direct_pg
    configs = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    configs[0]["employee_ids"] = [str(pg.actor.id)]
    other = {
        **configs[0],
        "company_code": "OTHER",
        "receiver_rfc": "CCC010101CCC",
        "employee_ids": [str(pg.owner.id)],
    }
    monkeypatch.setenv(
        "AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([configs[0], other])
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-review-csrf")
    app.include_router(routes.router)

    async def employee():
        return pg.actor

    async def database():
        async with pg.factory() as session:
            yield session

    app.dependency_overrides[routes.get_current_empleado] = employee
    app.dependency_overrides[routes.get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        form = await client.get(routes.BASE + "/directo/nuevo")
        assert form.status_code == 200
        csrf = re.search(r'name="_csrf_token" value="([^"]+)"', form.text).group(1)
        response = await client.post(
            routes.BASE + "/directo/nuevo",
            data={
                "_csrf_token": csrf,
                "employee_id": str(pg.owner.id),
                "card_account_id": str(pg.card.id),
                "profile": "debit_negative",
                "company_code": "TEST",
                "receiver_rfc": "BBB010101BBB",
                "currency": "MXN",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "idempotency_key": "tampered-post",
                "confirmed": "true",
            },
            files={
                "file": (
                    "tampered.csv",
                    b"Date,Description,Amount\n2026-10-02,ACME,-116.00\n",
                    "text/csv",
                )
            },
        )
    assert response.status_code == 409
    for table in (
        "amex_statement_imports",
        "amex_statement_source_files",
        "expense_reports",
    ):
        assert await pg.conn.fetchval(f"SELECT count(*) FROM {table}") == 0


@pytest.mark.asyncio
async def test_context_owner_choices_do_not_require_classification_or_upload(direct_pg):
    from devnous.gastos.services import amex_direct_context_service as context
    from devnous.gastos.services.access_control_service import (
        empleado_area,
        empleado_role,
    )

    pg = direct_pg
    current, _, _ = await _fixture._matched_statement(pg)
    async with pg.factory.begin() as session:
        for action in ("clasificar", "cargar"):
            session.add(
                _fixture.models.AccessControlRule(
                    tool_key="amex_direct",
                    action_key=action,
                    role_key=empleado_role(pg.actor),
                    area_key=empleado_area(pg.actor),
                    allowed=False,
                    active=True,
                )
            )
    async with pg.factory() as session:
        options = await context.list_direct_context_options(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert options["contexts"] == [
            {"company_code": "TEST", "receiver_rfc": "BBB010101BBB", "currency": "MXN"}
        ]
        with pytest.raises(PermissionError):
            await context.list_direct_classification_options(
                session, actor_id=pg.actor.id, import_id=current.statement.id
            )


@pytest.mark.asyncio
async def test_legacy_discovery_and_fiscal_services_exclude_real_direct_receipts(
    direct_pg,
):
    from sqlalchemy import select

    from devnous.gastos.services import amex_cfdi_matching_service as legacy
    from devnous.gastos.services.amex_accounting_identity_service import (
        direct_amex_expense_condition,
    )

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg, alias=False)
    async with pg.factory() as session:
        expense = await session.get(
            _fixture.models.ExpenseReport, current.items[0].imported_expense_id
        )
        discovered = list(
            (
                await session.scalars(
                    select(_fixture.models.ExpenseReport.id).where(
                        _fixture.models.ExpenseReport.origen == "amex_batch",
                        ~direct_amex_expense_condition(_fixture.models.ExpenseReport),
                    )
                )
            ).all()
        )
        assert discovered == []
        assert await legacy.suggest_amex_cfdi_matches(session, expense) == []
        assert (
            await legacy.validate_amex_cfdi_suggestion(session, expense, cfdi.id)
            is None
        )
        assert (
            await legacy.validate_pase_monthly_cfdi_suggestion(
                session, [expense], cfdi.id
            )
            is None
        )


@pytest.mark.asyncio
async def test_legacy_pase_action_does_not_reassign_direct_fiscal_owner(
    direct_pg, monkeypatch
):
    from devnous.gastos.routes import user_routes as routes

    pg = direct_pg
    current, _, cfdi = await _fixture._matched_statement(pg, alias=False)
    monkeypatch.setattr(routes, "is_pase_expense", lambda expense: True)
    suggest = AsyncMock(return_value=SimpleNamespace(cfdi_uuid=cfdi.cfdi_uuid))
    monkeypatch.setattr(routes, "validate_pase_monthly_cfdi_suggestion", suggest)
    async with pg.factory() as session:
        await routes.amex_conciliacion_link_pase_monthly_cfdi(
            session=session,
            current_empleado=pg.actor,
            expense_ids=str(current.items[0].imported_expense_id),
            cfdi_report_id=str(cfdi.id),
            year=2026,
            month=10,
            return_to=None,
        )
    suggest.assert_not_awaited()
    assert await pg.conn.fetchval("SELECT cfdi_report_id FROM expense_reports") is None
