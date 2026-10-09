"""Executable evidence for scoped aggregates and exact-context Sam turns."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy.dialects import postgresql

from samchat.ar import collection_matches as matches
from samchat.ar import service as ar
from samchat.client_executive import conversation as chat
from samchat.client_executive import home
from samchat.client_executive.home_ui import render_home
from samchat.finance_platform import service as finance

ACTOR = "10000000-0000-0000-0000-000000000431"
T1 = "20000000-0000-0000-0000-000000000431"
T2 = "20000000-0000-0000-0000-000000000432"
P1 = "30000000-0000-0000-0000-000000000431"
VERSION = "40000000-0000-0000-0000-000000000431"
TODAY = datetime.now(home.TZ).date()
YEAR = TODAY.year


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.first()


class Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class Session:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []
        self.added = []
        self.flushed = False
        self.committed = False

    def begin_nested(self):
        return Savepoint()

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        return Result(self.results.pop(0) if self.results else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def scope():
    return {
        "portfolio_ids": [P1],
        "portfolio_id": None,
        "tournament_id": None,
        "portfolios": [{"id": P1, "label": "Cartera"}],
        "tournaments": [{"id": T1, "name": "Torneo A"}, {"id": T2, "name": "Torneo B"}],
        "selected": [{"id": T1, "name": "Torneo A"}, {"id": T2, "name": "Torneo B"}],
    }


def row(tid=T1, actual="40", **overrides):
    result = {
        "id": tid,
        "name": "Torneo " + tid[-1],
        "version_id": VERSION,
        "values": {
            "budget": "100",
            "actual": actual,
            "committed": "50",
            "paid": "30",
            "forecast": "120",
            "deviation": "20",
            "obligations": "0",
            "receivables": None,
        },
        "gaps": {"actual": [], "receivables": ["Cobranza desconocida"]},
        "operations": {"teams": None, "players": None},
        "payment_evidence": [],
    }
    result.update(overrides)
    return result


def snapshot(rows=None):
    return home.build_snapshot(
        scope(),
        [row(), row(T2, "60")] if rows is None else rows,
        year=YEAR,
        start=date(YEAR, 1, 1),
        end=TODAY,
        observed_at="2026-09-29T23:00:00+00:00",
    )


@pytest.mark.parametrize("raw", [None, "", "bad", float("inf"), float("nan"), True])
def test_invalid_money_never_becomes_zero(raw):
    assert home.amount(raw) is None
    assert home.format_money(raw) == "Sin dato"


def test_decimal_sum_zero_and_partial_coverage():
    assert home.aggregate([Decimal("0")]) == ("0", "available", 1)
    assert home.aggregate([None]) == (None, "unavailable", 0)
    assert home.aggregate([Decimal("0.1"), Decimal("0.2")]) == ("0.3", "available", 2)
    assert home.aggregate([Decimal("0"), None]) == ("0", "partial", 1)
    assert home.aggregate([]) == (None, "unavailable", 0)
    assert home.amount("0") == Decimal("0.00")


def test_period_stays_in_edition():
    assert home.period_bounds(YEAR, None, None) == (date(YEAR, 1, 1), TODAY)
    for start, end in [
        (date(YEAR, 4, 1), date(YEAR, 3, 1)),
        (date(YEAR - 1, 12, 31), TODAY),
        (TODAY, date(YEAR + 1, 1, 1)),
    ]:
        with pytest.raises(ValueError):
            home.period_bounds(YEAR, start, end)


@pytest.mark.parametrize("status", ["draft", "reforecast", "submitted", None])
def test_unapproved_budget_is_not_authorized(status):
    values, gaps = home.budget_values(
        {
            "source": "budget_db",
            "version": {"status": status},
            "summary": {"budget_total": 200},
        },
        start=date(YEAR, 1, 1),
        end=TODAY,
        year=YEAR,
    )
    assert all(v is None for v in values.values())
    assert gaps


def test_unequal_shared_cfdi_cannot_be_published_as_valid_exercised():
    source = {
        "source": "budget_db",
        "version": {"status": "approved"},
        "summary": {
            "budget_total": 1000,
            "actual_total": 500,
            "committed_total": 900,
            "paid_total": 100,
        },
        "forecast": {"projected_close_total": 1300},
        "executive_quality_gaps": ["shared_cfdi_allocation_requires_reconciliation"],
    }
    values, gaps = home.budget_values(
        source, start=date(YEAR, 1, 1), end=TODAY, year=YEAR
    )
    assert values["budget"] == 1000
    assert values["actual"] is None and values["forecast"] is None
    assert values["committed"] == 900 and values["paid"] == 100
    assert gaps
    source["executive_quality_gaps"] = ["mixed_or_unknown_currency"]
    values, _ = home.budget_values(source, start=date(YEAR, 1, 1), end=TODAY, year=YEAR)
    assert values["committed"] is None and values["paid"] is None


def test_interval_is_not_an_annual_forecast():
    source = {
        "source": "budget_db",
        "version": {"status": "frozen"},
        "summary": {"budget_total": 100, "actual_total": 50},
        "forecast": {"projected_close_total": 120},
    }
    values, _ = home.budget_values(source, start=date(YEAR, 1, 1), end=TODAY, year=YEAR)
    assert values["forecast"] == 120 and values["deviation"] == 20
    values, _ = home.budget_values(
        source, start=date(YEAR, 2, 1), end=date(YEAR, 2, 28), year=YEAR
    )
    assert values["forecast"] is None and values["actual"] == 50


def test_priority_cannot_hide_one_tournament_with_the_margin_of_another():
    second = row(T2)
    second["values"].update(deviation="-50", forecast="50")
    payload = snapshot([row(), second])
    assert payload["priorities"][0]["tournament_id"] == T1
    assert payload["priorities"][0]["impact"] == "20.00"
    assert payload["headline"].endswith("cierre mecánico sobre presupuesto")
    assert (
        next(m for m in payload["indicators"] if m["id"] == "deviation")["value"]
        == "-30.00"
    )
    assert all(m["comparison"] is None for m in payload["indicators"])
    assert (
        next(m for m in payload["indicators"] if m["id"] == "liquidity")["value"]
        is None
    )
    assert next(m for m in payload["indicators"] if m["id"] == "overdue")["gaps"]


def test_scope_and_values_change_snapshot_identity():
    a = snapshot()
    b = snapshot([row(actual="41")])
    assert a["snapshot_id"] != b["snapshot_id"]


@pytest.mark.asyncio
async def test_scope_rejects_foreign_portfolio_before_reading(monkeypatch):
    monkeypatch.setattr(
        home.service, "authorized_direction_portfolio_ids", AsyncMock(return_value=[P1])
    )
    session = Session()
    with pytest.raises(home.service.ClientExecutiveAccessError):
        await home.resolve_scope(
            session,
            actor=ACTOR,
            superadmin=False,
            portfolio_id="foreign",
            tournament_id=None,
        )
    assert not session.calls


@pytest.mark.asyncio
async def test_scope_deduplicates_tournaments_and_filters_portfolio(monkeypatch):
    monkeypatch.setattr(
        home.service, "authorized_direction_portfolio_ids", AsyncMock(return_value=[P1])
    )
    monkeypatch.setattr(
        home.service,
        "_authorized_tournaments",
        AsyncMock(
            return_value=[
                {"id": T1, "name": "A"},
                {"id": T1, "name": "A"},
                {"id": T2, "name": "B"},
            ]
        ),
    )
    session = Session([[{"id": P1, "label": "Cartera"}], [{"id": T1}]])
    resolved = await home.resolve_scope(
        session, actor=ACTOR, superadmin=False, portfolio_id=P1, tournament_id=T1
    )
    assert len(resolved["selected"]) == 1
    with pytest.raises(home.service.ClientExecutiveAccessError):
        await home.resolve_scope(
            Session([[{"id": P1, "label": "Cartera"}], [{"id": T1}]]),
            actor=ACTOR,
            superadmin=False,
            portfolio_id=P1,
            tournament_id=T2,
        )


@pytest.mark.asyncio
async def test_optional_read_isolated_and_sanitized():
    loader = AsyncMock(side_effect=RuntimeError("secret connection"))
    assert await home._optional_read(Session(), loader) is None
    loader = AsyncMock(return_value={"ok": True})
    assert await home._optional_read(Session(), loader) == {"ok": True}


@pytest.mark.asyncio
async def test_denied_sources_are_never_read(monkeypatch):
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope()))
    optional = AsyncMock(side_effect=AssertionError("denied source must not run"))
    monkeypatch.setattr(home, "_optional_read", optional)
    monkeypatch.setattr(
        home.service,
        "_build_operational_dossier",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    result, _ = await home.build_home(
        Session(),
        actor=ACTOR,
        superadmin=False,
        year=YEAR,
        source_access={"budget": False, "finance": False},
    )
    assert not optional.await_count
    assert result["tournaments"][0]["operations"]["teams"] is None
    assert all(m["value"] is None for m in result["indicators"])


@pytest.mark.asyncio
async def test_home_reuses_canonical_sources_with_exact_scope_and_period(monkeypatch):
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope()))
    monkeypatch.setattr(
        home.service, "dispatch_registration_snapshot", AsyncMock(return_value=None)
    )

    async def read(_session, loader, **kwargs):
        if loader is home.build_executive_facts:
            assert kwargs["tournament_ids"] == [T1, T2]
            assert kwargs["start"] == date(YEAR, 1, 1) and kwargs["end"] == TODAY
            return {
                "by_tournament": {
                    tid: {
                        "values": {"actual": "40", "committed": "50", "paid": "30"},
                        "gaps": {},
                    }
                    for tid in (T1, T2)
                }
            }
        assert loader is home.service._build_direction_budget_snapshot
        assert kwargs["executive_read"] is True and kwargs["date_to"] == TODAY
        return {
            "source": "budget_db",
            "version": {"id": VERSION, "status": "approved"},
            "summary": {
                "budget_total": 100,
                "actual_total": 40,
                "committed_total": 50,
                "paid_total": 30,
            },
            "forecast": {"projected_close_total": 120},
        }

    monkeypatch.setattr(home, "_optional_read", read)
    monkeypatch.setattr(
        home,
        "payment_values",
        AsyncMock(return_value={"value": Decimal("0"), "gaps": []}),
    )
    monkeypatch.setattr(
        home,
        "receivable_values",
        AsyncMock(return_value={"value": None, "gaps": ["Sin comprobación"]}),
    )
    monkeypatch.setattr(
        home.service,
        "_build_operational_dossier",
        AsyncMock(
            return_value={
                "source_status": "available",
                "summary": {"teams_count": 5, "players_count": 10},
            }
        ),
    )
    result, _ = await home.build_home(
        Session(), actor=ACTOR, superadmin=False, year=YEAR
    )
    actual = next(m for m in result["indicators"] if m["id"] == "actual")
    assert actual["value"] == "80.00" and actual["coverage"] == {
        "covered": 2,
        "total": 2,
    }
    assert result["tournaments"][0]["operations"]["players"] == 10
    assert home.payment_values.await_args_list[0].args[1] == [T1, T2]
    assert home.payment_values.await_count == 1


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "local-test-secret-only")
    result = snapshot()
    result["source_access"] = {"budget": True, "finance": True}
    return result, chat.sign_context(result, ACTOR)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_child", [False, True])
async def test_large_scope_projects_in_bounded_readonly_batches_with_parity(
    monkeypatch, fail_child
):
    import asyncio

    selected = scope()
    selected["selected"] = [
        {"id": str(UUID(int=n)), "name": f"Synthetic {n}"} for n in range(1, 1002)
    ]
    selected["tournaments"] = selected["selected"]
    ids = [t["id"] for t in selected["selected"]]
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=selected))
    main = Session()
    children = []
    active = maximum = closed = 0

    class Child(Session):
        async def __aenter__(self):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            return self

        async def __aexit__(self, *_):
            nonlocal active, closed
            active -= 1
            closed += 1

        def begin(self):
            return Savepoint()

        async def execute(self, statement, params=None):
            assert str(statement) == "SET TRANSACTION READ ONLY"
            if fail_child and self is children[0]:
                raise RuntimeError("synthetic setup failure")
            self.readonly = True
            return Result()

    def factory():
        child = Child()
        children.append(child)
        return child

    async def read(session, loader, **kwargs):
        if loader is home.build_executive_facts:
            assert session is main and kwargs["tournament_ids"] == ids
            return {
                "by_tournament": {
                    tid: {
                        "values": {"actual": "40", "committed": None, "paid": None},
                        "gaps": {},
                    }
                    for tid in ids
                }
            }
        if session is None:
            return None
        assert loader is home.service._build_direction_budget_snapshot
        assert session is main or session.session.readonly
        await asyncio.sleep(0)
        return {
            "source": "budget_db",
            "version": {"id": VERSION, "status": "approved"},
            "summary": {"budget_total": 100, "actual_total": 40},
            "forecast": {"projected_close_total": 120},
        }

    monkeypatch.setattr(home, "_optional_read", read)
    monkeypatch.setattr(
        home.service,
        "_build_operational_dossier",
        AsyncMock(
            return_value={
                "source_status": "available",
                "summary": {"teams_count": 5, "players_count": 10},
            }
        ),
    )
    monkeypatch.setattr(
        home.service,
        "_build_operational_summaries",
        AsyncMock(
            return_value={tid: {"teams_count": 5, "players_count": 10} for tid in ids}
        ),
    )
    monkeypatch.setattr(home, "_tournament_read_factory", lambda session: factory)
    batched, _ = await home.build_home(
        main,
        actor=ACTOR,
        superadmin=True,
        year=YEAR,
        source_access={"budget": True, "finance": False},
    )
    assert 1 < maximum <= home.TOURNAMENT_READ_CONCURRENCY
    assert (
        active == 0
        and closed
        == (len(ids) + home.TOURNAMENT_READ_BATCH_SIZE - 1)
        // home.TOURNAMENT_READ_BATCH_SIZE
    )
    assert batched["tournament_ids"] == ids
    actual = next(m for m in batched["indicators"] if m["id"] == "actual")
    assert actual["value"] == "40040.00" and actual["coverage"]["covered"] == 1001
    budget = next(m for m in batched["indicators"] if m["id"] == "budget")
    assert budget["coverage"]["covered"] == 1001 - (
        home.TOURNAMENT_READ_BATCH_SIZE if fail_child else 0
    )
    if not fail_child:
        monkeypatch.setattr(home, "_tournament_read_factory", lambda session: None)
        serial, _ = await home.build_home(
            main,
            actor=ACTOR,
            superadmin=True,
            year=YEAR,
            source_access={"budget": True, "finance": False},
        )
        assert [
            (m["id"], m["value"], m["coverage"]) for m in batched["indicators"]
        ] == [(m["id"], m["value"], m["coverage"]) for m in serial["indicators"]]
        assert [r["values"] for r in batched["tournaments"]] == [
            r["values"] for r in serial["tournaments"]
        ]
        for field in (
            "operations",
            "concepts",
            "gaps",
            "monthly_execution",
            "previous_period",
        ):
            assert [r[field] for r in batched["tournaments"]] == [
                r[field] for r in serial["tournaments"]
            ]
        monkeypatch.setattr(
            home,
            "_tournament_read_factory",
            lambda session: lambda: pytest.fail(
                "Denied financial sources must not open child sessions"
            ),
        )
        denied, _ = await home.build_home(
            main,
            actor=ACTOR,
            superadmin=True,
            year=YEAR,
            source_access={"budget": False, "finance": False},
        )
        assert all(metric["value"] is None for metric in denied["indicators"])


@pytest.mark.asyncio
async def test_batch_session_factory_reuses_only_the_existing_async_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    assert home._tournament_read_factory(Session()) is None
    assert home._tournament_read_factory(SimpleNamespace(bind=object())) is None
    # Construction is lazy: this test never connects to a database.
    engine = create_async_engine("postgresql+asyncpg://")
    try:
        for bind in (engine, engine.connect()):
            factory = home._tournament_read_factory(SimpleNamespace(bind=bind))
            assert factory.kw["bind"] is engine
            assert factory.kw["autoflush"] is False
            assert factory.kw["expire_on_commit"] is False
    finally:
        await engine.dispose()


def test_signed_context_foreign_tampered_expired(signed, monkeypatch):
    result, token = signed
    assert chat.load_context(token, ACTOR) == result
    for actor, tok in [(T1, token), (ACTOR, token + "tamper")]:
        with pytest.raises(chat.ContextError):
            chat.load_context(tok, actor)
    original = TimestampSigner.get_timestamp
    monkeypatch.setattr(
        TimestampSigner, "get_timestamp", lambda self: original(self) + 901
    )
    with pytest.raises(chat.ContextError):
        chat.load_context(token, ACTOR)


def test_missing_signing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    with pytest.raises(chat.ContextError):
        chat.sign_context(snapshot(), ACTOR)


def test_large_context_receipt_preserves_exact_snapshot_and_signature(
    signed, monkeypatch
):
    import random

    data, _ = signed
    data["synthetic_large_scope_evidence"] = random.Random(431).randbytes(120000).hex()
    assert len(chat._signer().dumps({"actor": ACTOR, "snapshot": data})) > 100000
    token = chat.sign_context(data, ACTOR)
    receipt = chat.context_receipt(data, ACTOR, token)
    assert len(token) < 100000 and receipt
    assert chat.load_context(token, ACTOR, receipt) == data
    for actor, value in [(T1, receipt), (ACTOR, receipt + " "), (ACTOR, None)]:
        with pytest.raises(chat.ContextError):
            chat.load_context(token, actor, value)
    original = TimestampSigner.get_timestamp
    monkeypatch.setattr(
        TimestampSigner, "get_timestamp", lambda self: original(self) + 901
    )
    with pytest.raises(chat.ContextError):
        chat.load_context(token, ACTOR, receipt)


def test_sam_exact_metric_unsupported_request_and_unknown_id():
    data = snapshot()
    answer = chat.answer_snapshot(data, "actual", "¿Qué explica esto?")
    assert answer["metric"]["value"] == "100.00"
    assert answer["snapshot_id"] == data["snapshot_id"]
    assert answer["hypotheses"]  # Explicitly unverified alternatives, never causes.
    assert answer["opinions"] == []
    assert "no acreditan la causa" in answer["assistant_message"]
    assert not chat.answer_snapshot(data, "actual", "Paga esta factura")["supported"]
    assert not chat.answer_snapshot(data, "actual", "Inventa algo sobre otro torneo")[
        "supported"
    ]
    compared = chat.answer_snapshot(data, "actual", "Compara cifras")
    assert any("40.00" in fact for fact in compared["facts"])
    with pytest.raises(chat.ContextError):
        chat.answer_snapshot(data, "foreign", "explica")


def test_sam_partial_is_never_labeled_full_total():
    data = snapshot([row(), row(T2, actual=None)])
    answer = chat.answer_snapshot(data, "actual", "explica esto")
    assert answer["metric"]["status"] == "partial"
    assert "subtotal" in answer["assistant_message"]


@pytest.mark.asyncio
async def test_canonical_conversation_continuity_and_wrong_context():
    data = snapshot()
    answer = chat.answer_snapshot(data, "actual", "explica")
    session = Session()
    cid = await chat.save_turn(
        session,
        actor=ACTOR,
        snapshot=data,
        metric_id="actual",
        question="explica",
        answer=answer,
        conversation_id=None,
    )
    assert session.flushed and session.committed
    assert len(session.added) == 4
    assert session.added[0].metadata_["direction_snapshot_id"] == data["snapshot_id"]
    assert session.added[-1].pending_tool_name is None
    conversation = session.added[0]
    session = Session([[conversation]])
    assert (
        await chat.save_turn(
            session,
            actor=ACTOR,
            snapshot=data,
            metric_id="actual",
            question="fuente",
            answer=answer,
            conversation_id=cid,
        )
        == cid
    )
    assert len(session.added) == 3
    for cid, existing in [
        ("bad", None),
        (str(UUID(ACTOR)), None),
        (
            str(UUID(ACTOR)),
            SimpleNamespace(metadata_={"direction_snapshot_id": "foreign"}),
        ),
    ]:
        with pytest.raises(chat.ContextError):
            await chat.save_turn(
                Session([[existing]] if existing else []),
                actor=ACTOR,
                snapshot=data,
                metric_id="actual",
                question="explica",
                answer=answer,
                conversation_id=cid,
            )


def test_renderer_escapes_untrusted_labels_and_script_data():
    data = snapshot()
    data["tournaments"][0]["name"] = "</script><img src=x onerror=alert(1)>"
    html = render_home(data, scope(), token="signed", csrf="csrf")
    assert "</script><img" not in html
    assert "\\u003c/script>" in html
    assert "&lt;img" in html
    assert "/static/direction_home.js" in html
    assert 'type="application/json"' in html
    assert "Sin meta aprobada" in html


@pytest.mark.asyncio
async def test_scoped_finance_source_never_reads_global_expenses_or_polizas():
    session = Session([[]])
    result = await finance.build_finance_source_snapshot(
        session, tournament_ids=[T1], documents_only=True
    )
    sql = str(session.calls[0][0].compile(dialect=postgresql.dialect()))
    assert "coalesce(documentos.torneo_id, cuentas_de_gastos.torneo_id) IN" in sql
    loader_contexts = [
        ctx for option in session.calls[0][0]._with_options for ctx in option.context
    ]
    assert any(
        "CuentaDeGastos.torneo_id" in str(ctx.path)
        and ("deferred", False) in ctx.strategy
        for ctx in loader_contexts
    )
    assert len(session.calls) == 1
    assert result["source_status"]["polizas_available"] is False
    with pytest.raises(ValueError):
        await finance.build_finance_source_snapshot(Session(), documents_only=True)
    with pytest.raises(ValueError):
        await finance.build_finance_source_snapshot(Session(), tournament_ids=[T1])
    result = await finance.build_finance_source_snapshot(
        Session([[]]), tournament_ids=[], documents_only=True
    )
    assert result["documents"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("a_count,b_count", [(3, 2), (2, 2), (3, 0)])
async def test_payment_scan_completeness_is_per_tournament(
    monkeypatch, a_count, b_count
):
    # A tiny cap exercises the same boundary as 5,000 without giant fixtures.
    rows = [
        SimpleNamespace(torneo_id=tid, currency="MXN")
        for tid, count in [(T1, a_count), (T2, b_count)]
        for _ in range(count)
    ]
    monkeypatch.setattr(
        finance,
        "_serialize_document",
        lambda d: {
            "tipo": "SOLICITUD",
            "estado": "aprobado",
            "monto_total": 10,
            "fecha_pago": TODAY.isoformat(),
        },
    )
    session = Session([rows])
    source = await finance.build_finance_source_snapshot(
        session, tournament_ids=[T1, T2], documents_only=True, limit=2
    )
    sql = str(session.calls[0][0].compile(dialect=postgresql.dialect()))
    assert (
        "PARTITION BY coalesce(documentos.torneo_id, cuentas_de_gastos.torneo_id)"
        in sql
    )
    assert "coalesce(documentos.torneo_id, cuentas_de_gastos.torneo_id) IN" in sql
    assert len(session.calls) == 1
    assert len(source["documents"]) == min(a_count, 2) + b_count
    monkeypatch.setattr(home, "_optional_read", AsyncMock(return_value=source))
    result = await home.payment_values(Session(), [T1, T2], TODAY)
    assert result["by_tournament"][T2]["value"] == b_count * 10
    if a_count > 2:
        assert result["value"] is None
        assert result["by_tournament"][T1]["value"] is None
    else:
        assert result["value"] == (a_count + b_count) * 10
        assert result["by_tournament"][T1]["value"] == a_count * 10


@pytest.mark.asyncio
async def test_obligations_inherit_account_tournament_without_overriding_direct_scope(
    monkeypatch,
):
    rows = [
        SimpleNamespace(
            torneo_id=None, cuenta_gastos=SimpleNamespace(torneo_id=T1), currency="MXN"
        ),
        SimpleNamespace(
            torneo_id=T2, cuenta_gastos=SimpleNamespace(torneo_id=T1), currency="MXN"
        ),
    ]
    monkeypatch.setattr(
        finance,
        "_serialize_document",
        lambda d: {
            "tipo": "SOLICITUD",
            "estado": "aprobado",
            "monto_total": 10,
            "fecha_pago": TODAY.isoformat(),
        },
    )
    source = await finance.build_finance_source_snapshot(
        Session([rows]), tournament_ids=[T1, T2], documents_only=True
    )
    assert [r["tournament_id"] for r in source["documents"]] == [T1, T2]
    monkeypatch.setattr(home, "_optional_read", AsyncMock(return_value=source))
    result = await home.payment_values(Session(), [T1, T2], TODAY)
    assert result["value"] == 20
    assert result["by_tournament"][T1]["value"] == 10
    assert result["by_tournament"][T2]["value"] == 10


@pytest.mark.asyncio
async def test_payment_stock_gap_zero_cutoff_and_payable_amount(monkeypatch):
    rows = [
        {
            "currency": "MXN",
            "tipo": "SOLICITUD",
            "estado": "aprobado",
            "monto_total": 60,
            "monto_solicitado": 100,
            "fecha_pago": TODAY.isoformat(),
            "numero_referencia": "S1",
        },
        {
            "currency": "MXN",
            "tipo": "SOLICITUD",
            "estado": "en_proceso_pago",
            "monto_total": 20,
            "fecha_pago": (TODAY + timedelta(days=31)).isoformat(),
            "numero_referencia": "S2",
        },
        {
            "currency": "MXN",
            "tipo": "SOLICITUD",
            "estado": "pagado",
            "monto_total": 100,
            "fecha_pago": TODAY.isoformat(),
        },
    ]
    read = AsyncMock(
        return_value={
            "documents": rows,
            "expenses": [],
            "polizas": [],
            "source_status": {},
        }
    )
    monkeypatch.setattr(home, "_optional_read", read)
    got = await home.payment_values(Session(), [T1], TODAY)
    assert got["value"] == 60
    assert got["evidence"][0]["reference"] == "S1"
    rows.append(
        {
            "currency": "MXN",
            "tipo": "SOLICITUD",
            "estado": "aprobado",
            "monto_total": 10,
            "fecha_pago": None,
        }
    )
    assert (await home.payment_values(Session(), [T1], TODAY))["value"] is None
    rows.pop()
    rows.append(
        {
            "currency": "MXN",
            "tipo": "SOLICITUD",
            "estado": "aprobado",
            "monto_total": None,
            "monto_solicitado": None,
            "fecha_pago": "invalid",
        }
    )
    assert (await home.payment_values(Session(), [T1], TODAY))["value"] is None
    read.return_value = {
        "documents": [],
        "expenses": [],
        "polizas": [],
        "source_status": {},
    }
    assert (await home.payment_values(Session(), [T1], TODAY))["value"] == 0
    read.return_value = {"source_status": {"document_scan_truncated": True}}
    assert (await home.payment_values(Session(), [T1], TODAY))["value"] is None


@pytest.mark.asyncio
async def test_ar_balance_unconfigured_or_incomplete_not_zero(monkeypatch):
    read = AsyncMock(side_effect=[{}, None])
    monkeypatch.setattr(home, "_optional_read", read)
    assert (await home.receivable_values(Session(), T1, VERSION, YEAR))["value"] is None
    for payload in [
        None,
        {"source_status": {"candidates_truncated": True}},
        {"source_status": {"currency_gap": True}},
        {"source_status": {"amount_gap": True}},
        {"summary": {"collection_gap_count": 1}},
        {"summary": {"matching_gap_count": 1}},
    ]:
        read.side_effect = [{"configured": True}, payload]
        assert (await home.receivable_values(Session(), T1, VERSION, YEAR))[
            "value"
        ] is None
    read.side_effect = [{"configured": True}, {"summary": {"balance_total": 0}}]
    assert (await home.receivable_values(Session(), T1, VERSION, YEAR))["value"] == 0


@pytest.mark.asyncio
async def test_strict_ar_fails_foreign_candidates_before_loading_matches(monkeypatch):
    monkeypatch.setattr(ar, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(ar, "list_monthly_plan_for_lines", AsyncMock(return_value={}))
    monkeypatch.setattr(ar, "list_budget_cfdi_income_links", AsyncMock(return_value=[]))
    candidates = AsyncMock(
        return_value=[{"id": "foreign", "assigned_tournament_id": T2}]
    )
    monkeypatch.setattr(ar, "list_psp_cfdi_income_candidates", candidates)
    matcher = AsyncMock(return_value=[])
    monkeypatch.setattr(ar, "list_ar_collection_matches", matcher)
    with pytest.raises(ValueError):
        await ar.build_ar_read_model(
            Session(),
            budget_version_id=VERSION,
            tournament_id=T1,
            strict_tournament_scope=True,
            ensure_schema=False,
        )
    assert candidates.await_args.kwargs["tournament_id"] == T1
    assert not matcher.await_count
    candidates.return_value = []
    result = await ar.build_ar_read_model(
        Session(),
        budget_version_id=VERSION,
        tournament_id=T1,
        strict_tournament_scope=True,
        ensure_schema=False,
    )
    assert matcher.await_args.kwargs["ar_item_ids"] == []
    assert result["source_status"]["strict_tournament_scope"]
    with pytest.raises(ValueError):
        await ar.build_ar_read_model(
            Session(), budget_version_id=VERSION, strict_tournament_scope=True
        )


@pytest.mark.asyncio
async def test_collection_matches_can_be_scoped_to_exact_items_without_ddl():
    session = Session([[]])
    await matches.list_ar_collection_matches(
        session,
        budget_version_id=VERSION,
        ar_item_ids=["linked:1"],
        ensure_schema=False,
    )
    assert "ar_item_id = ANY" in str(session.calls[0][0])
    assert session.calls[0][1]["ar_item_ids"] == ["linked:1"]


@pytest.mark.asyncio
async def test_executive_budget_rejects_draft_without_artifact_fallback(monkeypatch):
    from samchat.budgets import service as budgets

    monkeypatch.setattr(
        budgets,
        "_select_budget_version",
        AsyncMock(
            return_value={"id": VERSION, "status": "draft", "version_name": "Draft"}
        ),
    )
    monkeypatch.setattr(
        budgets,
        "load_budget_artifact_rows",
        lambda: pytest.fail("No artifact fallback"),
    )
    result = await budgets.build_budget_snapshot(
        Session(), tournament_id=T1, executive_read=True, ensure_schema=False
    )
    assert result["source"] == "budget_scope_unavailable"
    assert (
        home.budget_values(result, start=date(YEAR, 1, 1), end=TODAY, year=YEAR)[0][
            "budget"
        ]
        is None
    )


def test_budget_interval_and_uuid_filter_are_passed_to_both_sources():
    from samchat.budgets.service import _build_budget_scope_filters

    docs, expenses, params = _build_budget_scope_filters(
        edition_year=YEAR,
        tournament_id=T1,
        tournament_name="A",
        tournament_code=None,
        date_from=date(YEAR, 2, 1),
        date_to=date(YEAR, 2, 28),
    )
    assert params["tournament_id"] == T1
    assert params["date_from"] == date(YEAR, 2, 1)
    assert any("DATE(d.creado_en)" in x for x in docs)
    assert any("DATE(e.fecha)" in x for x in expenses)
    assert not any("LIKE" in x and "t.name" in x for x in docs)
    with pytest.raises(ValueError):
        _build_budget_scope_filters(
            edition_year=YEAR,
            tournament_id=T1,
            tournament_name=None,
            tournament_code=None,
            date_from=date(YEAR - 1, 1, 1),
        )


@pytest.mark.asyncio
async def test_currency_gap_cannot_become_an_mxn_obligation(monkeypatch):
    monkeypatch.setattr(
        home,
        "_optional_read",
        AsyncMock(
            return_value={
                "documents": [
                    {
                        "estado": "aprobado",
                        "tipo": "SOLICITUD",
                        "currency": "USD",
                        "monto_total": 10,
                        "fecha_pago": TODAY.isoformat(),
                    }
                ]
            }
        ),
    )
    result = await home.payment_values(Session(), [T1], TODAY)
    assert result["value"] is None and result["gaps"]


def test_monthly_series_reconciles_and_never_fills_absent_source_with_zero():
    start, end = date(YEAR, 1, 1), date(YEAR, 2, 28)
    assert (
        home.monthly_execution({}, Decimal("0"), start, end)["status"] == "unavailable"
    )
    source = {"executive_monthly_actuals": [{"month": 2, "actual_total": "100"}]}
    series = home.monthly_execution(source, Decimal("100"), start, end)
    assert series["rows"] == [
        {"month": 1, "value": "0"},
        {"month": 2, "value": "100.00"},
    ]
    assert (
        home.monthly_execution(source, Decimal("99"), start, end)["status"]
        == "unavailable"
    )
    source["executive_monthly_actuals"][0]["month"] = 3
    assert (
        home.monthly_execution(source, Decimal("100"), start, end)["status"]
        == "unavailable"
    )
    assert home.monthly_execution(source, None, start, end)["status"] == "unavailable"


@pytest.mark.asyncio
async def test_canonical_monthly_source_uses_same_budget_tax_base_and_scope():
    from samchat.budgets.service import (
        _budget_expense_base_amount_sql,
        build_executive_monthly_actuals,
    )

    session = Session([[{"month": 1, "actual_total": Decimal("123")}]])
    result = await build_executive_monthly_actuals(
        session,
        tournament_id=T1,
        edition_year=YEAR,
        date_from=date(YEAR, 1, 1),
        date_to=date(YEAR, 1, 15),
    )
    assert result[0]["actual_total"] == 123
    sql, params = session.calls[0]
    assert _budget_expense_base_amount_sql("e", "cfdi") in str(sql)
    assert "CASE WHEN d.tipo = 'SOLICITUD' THEN d.budget_concept_id END" in str(sql)
    assert params["tournament_id"] == T1 and params["date_to"] == date(YEAR, 1, 15)
    with pytest.raises(ValueError):
        await build_executive_monthly_actuals(
            Session(), tournament_id="", edition_year=YEAR
        )


def test_urgent_obligations_rank_before_larger_mechanical_deviation():
    due = row(T2)
    due["values"]["obligations"] = "5"
    due["payment_evidence"] = [{"date": "2026-09-30", "value": "5"}]
    data = snapshot([row(), due])
    assert data["priorities"][0]["kind"] == "obligation"
    assert data["priorities"][0]["tournament_id"] == T2


@pytest.mark.asyncio
async def test_executive_budget_quality_and_monthly_are_scoped_to_visible_interval(
    monkeypatch,
):
    from samchat.budgets import service as budgets

    version = {
        "id": VERSION,
        "status": "approved",
        "version_name": "Approved",
        "source": "database",
        "artifact_path": None,
        "edition_year": YEAR,
    }
    monkeypatch.setattr(
        budgets, "_select_budget_version", AsyncMock(return_value=version)
    )
    monkeypatch.setattr(
        budgets, "_build_budget_finance_breakdowns", AsyncMock(return_value={})
    )
    comparison = AsyncMock(
        return_value={"actual_total": 40, "committed_total": 50, "paid_total": 30}
    )
    monkeypatch.setattr(budgets, "_build_budget_finance_comparison", comparison)
    line = {
        "tournament_id": T1,
        "tournament_code": "A",
        "tournament_name": "A",
        "concept_name": "Venue",
        "budget_amount": 100,
        "reference_amount": 100,
        "variance_amount": 0,
    }
    session = Session(
        [
            [line],
            [{"shared_count": 1, "currency_count": 0, "missing_amount_count": 1}],
            [{"currency_count": 1, "missing_amount_count": 1}],
            [{"month": 1, "actual_total": 40}],
        ]
    )
    data = await budgets.build_budget_snapshot(
        session,
        tournament_id=T1,
        edition_year=YEAR,
        executive_read=True,
        strict_tournament_scope=True,
        ensure_schema=False,
        date_from=date(YEAR, 1, 1),
        date_to=TODAY,
    )
    assert (
        "shared_cfdi_allocation_requires_reconciliation"
        in data["executive_quality_gaps"]
    )
    assert "mixed_or_unknown_currency" in data["executive_quality_gaps"]
    assert "document_amount_missing" in data["executive_quality_gaps"]
    assert data["executive_monthly_actuals"] == [{"month": 1, "actual_total": 40}]
    assert comparison.await_args.kwargs["date_to"] == TODAY
    sql, params = session.calls[1]
    assert params["tournament_id"] == T1 and params["date_to"] == TODAY
    assert "COALESCE(l.line_direction, 'expense') = 'expense'" in str(
        session.calls[0][0]
    )


def test_paid_fact_is_not_mistaken_for_payment_instruction():
    result = chat.answer_snapshot(
        snapshot(), "paid", "¿Cuánto pagado documental tenemos?"
    )
    assert result["supported"] and result["facts"]
    assert not chat.answer_snapshot(snapshot(), "paid", "Paga esta cantidad")[
        "supported"
    ]


@pytest.mark.asyncio
async def test_obligations_include_population_beyond_cash_preview(monkeypatch):
    rows = [
        {
            "estado": "aprobado",
            "tipo": "SOLICITUD",
            "currency": "MXN",
            "monto_total": 10,
            "fecha_pago": TODAY.isoformat(),
        }
        for _ in range(20)
    ]
    source = {"documents": rows}
    assert len(finance._build_cash_control(source)["approved_unpaid"]) == 15
    monkeypatch.setattr(home, "_optional_read", AsyncMock(return_value=source))
    result = await home.payment_values(Session(), [T1], TODAY)
    assert result["value"] == Decimal("200")
    assert len(result["evidence"]) == 20
    source["documents"] += [
        {**rows[0], "estado": "rechazado", "monto_total": 999},
        {**rows[0], "tipo": "INFORME", "monto_total": 999},
        {**rows[0], "pagado_en": TODAY.isoformat(), "monto_total": 999},
    ]
    result = await home.payment_values(Session(), [T1], TODAY)
    assert result["value"] == Decimal("200")
    assert len(result["evidence"]) == 20
    rows[19]["currency"] = "USD"
    result = await home.payment_values(Session(), [T1], TODAY)
    assert result["value"] is None and result["gaps"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quality", [None, "mixed_or_unknown_currency", "document_amount_missing"]
)
async def test_suppressed_budget_indicators_preserve_source_failure(
    monkeypatch, quality
):
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope()))
    source = {
        "source": "budget_db" if quality else "unavailable",
        "version": {"status": "approved"},
        "summary": {
            "budget_total": 100,
            "actual_total": 40,
            "committed_total": 50,
            "paid_total": 30,
        },
        "forecast": {"projected_close_total": 120},
        "executive_quality_gaps": [quality] if quality else [],
    }
    monkeypatch.setattr(home, "_optional_read", AsyncMock(return_value=source))
    monkeypatch.setattr(
        home, "payment_values", AsyncMock(return_value={"value": None, "gaps": []})
    )
    monkeypatch.setattr(
        home, "receivable_values", AsyncMock(return_value={"value": None, "gaps": []})
    )
    monkeypatch.setattr(
        home.service, "_build_operational_dossier", AsyncMock(return_value={})
    )
    result, _ = await home.build_home(
        Session(), actor=ACTOR, superadmin=False, year=YEAR
    )
    expected = quality or "Presupuesto aprobado y alcance no acreditados."
    for metric in result["indicators"]:
        if (
            metric["id"]
            in {"budget", "actual", "committed", "paid", "forecast", "deviation"}
            and metric["value"] is None
        ):
            expected_gap = (
                "Fuente documental independiente no disponible o no autorizada."
                if metric["id"] in {"actual", "committed", "paid"}
                else expected
            )
            assert any(expected_gap in gap for gap in metric["gaps"])
            answer = chat.answer_snapshot(result, metric["id"], "¿Qué explica esto?")
            assert expected_gap in answer["missing_evidence"]
            assert expected_gap in answer["assistant_message"]


@pytest.mark.parametrize("value", ["x" * 65, "é" * 33])
def test_oversized_receipt_rejected_before_signer_hash_or_json(monkeypatch, value):
    monkeypatch.setattr(chat, "MAX_RECEIPT_BYTES", 64)

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized receipt was processed")

    monkeypatch.setattr(chat, "_signer", forbidden)
    for token in ("receipt.valid-signed-token", "inline-token"):
        with pytest.raises(chat.ContextError, match="tamaño"):
            chat._load_payload(token, value)


def test_receipt_generation_and_utf8_boundaries(signed, monkeypatch):
    monkeypatch.setattr(chat, "MAX_RECEIPT_BYTES", 64)
    assert chat._receipt_bytes("x" * 64) == b"x" * 64
    assert len(chat._receipt_bytes("é" * 32)) == 64
    for call in (
        lambda: chat.sign_context(signed[0], ACTOR),
        lambda: chat.sign_analysis({"text": "x" * 65}, "snapshot", ACTOR),
        lambda: chat.context_receipt(signed[0], ACTOR, "receipt.token"),
        lambda: chat.analysis_receipt(
            {"text": "x" * 65}, "snapshot", ACTOR, "receipt.token"
        ),
    ):
        with pytest.raises(chat.ContextError, match="tamaño"):
            call()


def test_invalid_receipt_unicode_fails_closed():
    with pytest.raises(chat.ContextError, match="válido"):
        chat._receipt_bytes("\ud800")
