"""Real HTTP regression coverage without a browser or private statement files."""

from collections import Counter
from contextlib import asynccontextmanager
from decimal import Decimal
from html.parser import HTMLParser
from uuid import uuid4

import httpx
import pytest
import test_amex_qa_remediation_postgres as acceptance
from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.models import Empleado
from devnous.gastos.routes import admin_amex_direct_routes as routes
from devnous.gastos.routes import dependencies
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_status_service as status
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

direct_pg = acceptance.direct_pg
owner = acceptance.owner
recovery = acceptance.recovery


class Forms(HTMLParser):
    """Read actual rendered forms, retaining hidden values and action URLs."""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form":
            self.current = {"action": values.get("action", ""), "data": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            if values.get("type") == "hidden":
                self.current["data"][values["name"]] = values.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None

    def ending(self, suffix):
        return next(form for form in self.forms if form["action"].endswith(suffix))


@asynccontextmanager
async def real_http_client(pg):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated-http-acceptance")

    async def session_dependency():
        async with pg.factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    async def actor_dependency(session=Depends(dependencies.get_db_session)):
        return await session.get(Empleado, pg.actor.id)

    app.dependency_overrides[dependencies.get_db_session] = session_dependency
    app.dependency_overrides[dependencies.get_current_empleado] = actor_dependency
    pg.monkeypatch.setattr(dependencies, "_db_session_maker", pg.factory)
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_http_finder_and_selected_sat_keep_actor_across_rollback(direct_pg):
    pg = direct_pg
    fiscal_uuid = str(uuid4()).upper()
    outside_date_uuid = str(uuid4()).upper()
    outside_amount_uuid = str(uuid4()).upper()
    async with pg.factory.begin() as session:
        for xml in (
            owner._synthetic_sat_xml(fiscal_uuid),
            owner._synthetic_sat_xml(outside_date_uuid).replace(
                "2026-10-03", "2026-10-15"
            ),
            owner._synthetic_sat_xml(outside_amount_uuid)
            .replace("100.00", "200.00")
            .replace("116.00", "232.00")
            .replace("16.00", "32.00"),
        ):
            ingested = await ingest_cfdi_xml(session, xml, source="sat")
            assert ingested.cfdi_report, ingested.reason
    imported = await owner._direct_import(
        pg, b"Date,Description,Amount\n2026-10-02,NO ALIAS,-116.00\n"
    )
    item_id = await pg.conn.fetchval("SELECT id FROM amex_direct_items")
    observed = owner.ObservedSAT("vigente")
    pg.monkeypatch.setattr(status, "SATCFDIStatusService", lambda: observed)
    path = f"{routes.BASE}/{imported.statement.id}/directo/cfdi"
    async with real_http_client(pg) as client:
        response = await client.get(
            path, params={"item_id": str(item_id), "q": fiscal_uuid}
        )
        assert response.status_code == 200, response.text
        assert f'data-cfdi-uuid="{fiscal_uuid}"' in response.text
        assert "fila 2" in response.text
        form = Forms(response.text).ending("/cfdi/vigencia")
        issuer_results = await client.get(
            path, params={"item_id": str(item_id), "q": "AAA010101AAA"}
        )
        assert issuer_results.status_code == 200
        for identity in (fiscal_uuid, outside_date_uuid, outside_amount_uuid):
            assert f'data-cfdi-uuid="{identity}"' in issuer_results.text
        filtered = await client.get(
            path,
            params={
                "item_id": str(item_id),
                "q": "AAA010101AAA",
                "date_from": "2026-10-01",
                "date_to": "2026-10-05",
                "amount": "116.00",
            },
        )
        assert filtered.status_code == 200
        assert f'data-cfdi-uuid="{fiscal_uuid}"' in filtered.text
        for identity in (outside_date_uuid, outside_amount_uuid):
            assert f'data-cfdi-uuid="{identity}"' not in filtered.text
        for invalid_filters, expected_status in (
            ({"date_from": "not-a-date"}, 422),
            ({"date_from": "2026-10-05", "date_to": "2026-10-01"}, 409),
            ({"cursor": "unknown-session-cursor"}, 409),
            ({"item_id": str(uuid4())}, 409),
        ):
            rejected = await client.get(
                path,
                params={
                    "item_id": str(item_id),
                    "q": fiscal_uuid,
                    **invalid_filters,
                },
            )
            assert rejected.status_code == expected_status, rejected.text
            assert "data-cfdi-uuid" not in rejected.text
        assert not observed.requests
        invalid = await client.post(
            form["action"],
            data={**form["data"], "_csrf_token": "invalid", "confirmed": "true"},
        )
        assert invalid.status_code == 403
        missing_consent = await client.post(form["action"], data=form["data"])
        assert missing_consent.status_code == 422
        assert not observed.requests
        result = await client.post(
            form["action"], data={**form["data"], "confirmed": "true"}
        )
        assert result.status_code == 303, result.text
        assert len(observed.requests) == 1
        refreshed = await client.get(result.headers["location"])
        assert refreshed.status_code == 200, refreshed.text
        assert "vigente" in refreshed.text
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_cfdi_status_receipts") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_merchant_identity_aliases")
        == 0
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_items WHERE match_status='matched'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_http_period_preview_consent_stale_and_confirm_preserve_original(
    direct_pg,
):
    pg = direct_pg
    import_id, _ = await recovery.seed_previous_period_omission(pg)
    original = await pg.conn.fetchrow(
        "SELECT * FROM amex_statement_imports WHERE id=$1", import_id
    )
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_statement_import_items ORDER BY row_number"
    )
    path = f"{routes.BASE}/{import_id}/directo/periodo"
    async with real_http_client(pg) as client:
        response = await client.get(path)
        assert response.status_code == 200, response.text
        form = Forms(response.text).ending("/periodo/previsualizar")
        data = {
            **form["data"],
            "period_start": "2026-09-30",
            "period_end": "2026-10-31",
            "reason": "Original CSV proves omitted charges",
        }
        denied = await client.post(
            form["action"], data={**data, "_csrf_token": "invalid"}
        )
        assert denied.status_code == 403
        preview = await client.post(form["action"], data=data)
        assert preview.status_code == 200, preview.text
        assert "Cargos por recuperar: 5" in preview.text
        confirm = Forms(preview.text).ending("/periodo/confirmar")
        no_consent = await client.post(confirm["action"], data=confirm["data"])
        assert no_consent.status_code == 303, no_consent.text
        assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 1
        stale = await client.post(
            confirm["action"],
            data={**confirm["data"], "preview_hash": "0" * 64, "confirmed": "true"},
        )
        assert stale.status_code == 409, stale.text
        assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 1
        result = await client.post(
            confirm["action"], data={**confirm["data"], "confirmed": "true"}
        )
        assert result.status_code == 303, result.text
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 6
    assert (
        await pg.conn.fetchrow(
            "SELECT * FROM amex_statement_imports WHERE id=$1", import_id
        )
        == original
    )
    assert (
        await pg.conn.fetch(
            "SELECT * FROM amex_statement_import_items ORDER BY row_number"
        )
        == rows
    )


class Journal(HTMLParser):
    """Read only the actual journal table, preserving duplicate movements."""

    def __init__(self, html):
        super().__init__()
        self.active = False
        self.cell = None
        self.row = []
        self.rows = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") == "direct-journal":
            self.active = True
        elif self.active and tag == "tr":
            self.row = []
        elif self.active and tag == "td":
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if self.active and tag == "td":
            self.row.append(self.cell)
            self.cell = None
        elif self.active and tag == "tr" and self.row:
            self.rows.append(self.row)
        elif self.active and tag == "table":
            self.active = False

    def movements(self):
        assert self.rows, "The actual page must contain canonical journal rows"
        return Counter((row[0], Decimal(row[2]), Decimal(row[3])) for row in self.rows)


def canonical_movements(lines):
    return Counter(
        (
            str(line["cuenta_codigo"]),
            Decimal(str(line["debe"])),
            Decimal(str(line["haber"])),
        )
        for line in lines
    )


@pytest.mark.asyncio
async def test_http_canonical_journal_then_confirmed_frozen_policy(direct_pg):
    pg = direct_pg
    current = await owner._assigned_statement(pg)
    async with pg.factory() as session:
        proposal = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    assert proposal.status == "ready", proposal.reason
    expected = canonical_movements(proposal.progress["lines"])
    assert sum(debit * n for (_, debit, _), n in expected.items()) == Decimal("116")
    assert sum(credit * n for (_, _, credit), n in expected.items()) == Decimal("116")
    path = f"{routes.BASE}/{current.statement.id}/directo"
    async with real_http_client(pg) as client:
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert "Propuesta contable Debe / Haber" in response.text
        assert Journal(response.text).movements() == expected
        assert "Total Debe: 116.00" in response.text
        assert "Total Haber: 116.00" in response.text
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
        form = Forms(response.text).ending("/confirmar")
        payload = {
            **form["data"],
            "confirmed": "true",
            "accounting_date": "2026-10-31",
            "reason": "Complete synthetic HTTP policy",
        }
        denied = await client.post(
            form["action"], data={**payload, "_csrf_token": "invalid"}
        )
        assert denied.status_code == 403
        no_consent = await client.post(
            form["action"], data={**payload, "confirmed": "false"}
        )
        assert no_consent.status_code == 303, no_consent.text
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
        result = await client.post(form["action"], data=payload)
        assert result.status_code == 303, result.text
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
        async with pg.factory() as session:
            frozen = await accounting.preview_direct_accounting(
                session, actor_id=pg.actor.id, import_id=current.statement.id
            )
            assert frozen.cut is not None
            frozen_lines = frozen.cut.snapshot_json["lines"]
        rendered = await client.get(path)
        assert rendered.status_code == 200, rendered.text
        assert "Póliza congelada Debe / Haber" in rendered.text
        assert Journal(rendered.text).movements() == canonical_movements(frozen_lines)
        assert Journal(rendered.text).movements() == expected
        assert "Total Debe: 116.00" in rendered.text
        assert "Total Haber: 116.00" in rendered.text
        assert not any(
            form["action"].endswith("/confirmar") for form in Forms(rendered.text).forms
        )
        replay = await client.post(form["action"], data=payload)
        assert replay.status_code == 303, replay.text
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0
