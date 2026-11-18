"""Exercise the migrated reader against a dedicated PostgreSQL test database."""

import hashlib
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from samchat.cashflow.bank_statements import (
    INVALID_SOURCE,
    SOURCE_UNAVAILABLE,
    bank_series_sha256,
    load_direction_bank_period,
)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database/migrations/20261118_direction_bank_statement_certifications.sql"
)
ROLLBACK = MIGRATION.with_suffix(".rollback.sql")
ACCOUNT = "9999999826"
SOURCE_SHA = "be" * 32


@pytest_asyncio.fixture
async def isolated_bank_database():
    value = os.environ.get("DIRECTION_BANK_TEST_DSN")
    if not value:
        pytest.skip("DIRECTION_BANK_TEST_DSN: isolated PostgreSQL not selected")
    url = make_url(value)
    socket = str(url.query.get("host", ""))
    port = str(url.query.get("port", ""))
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host is not None
        or url.database != "direction_bank_test"
        or not socket.startswith("/tmp/direction-bank-pg-")
        or not port.isdecimal()
        or int(port) == 5432
    ):
        raise RuntimeError("Require dedicated direction_bank_test PostgreSQL socket")
    owner = await asyncpg.connect(
        user=url.username, database=url.database, host=socket, port=int(port)
    )
    try:
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
            )
            == 0
        )
        await owner.execute("""
            CREATE TABLE public.accounting_import_runs (
                id uuid PRIMARY KEY, source_type text, mode text, status text,
                filename text, started_at timestamptz, source_sha256 text
            );
            CREATE TABLE public.client_executive_portfolios (id uuid PRIMARY KEY, active boolean);
            CREATE TABLE public.client_executive_portfolio_tournaments (
                portfolio_id uuid, tournament_id uuid, active boolean
            );
            CREATE TABLE public.empleados (id uuid PRIMARY KEY);
            CREATE TABLE public.bank_movements (
                id uuid PRIMARY KEY, import_run_id uuid, source_row_number integer,
                fecha timestamp, hora text, signo text, importe float8, saldo float8,
                cuenta_bancaria text
            );
        """)
        try:
            yield value, owner
        finally:
            await owner.execute("SET samchat.direction_bank_archive_verified = 'on'")
            await owner.execute(ROLLBACK.read_text())
            await owner.execute("""
                DROP TABLE public.bank_movements, public.accounting_import_runs,
                           public.client_executive_portfolios, public.empleados,
                           public.client_executive_portfolio_tournaments;
            """)
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_migration_exact_scope_reconciled_receipt_and_revocation(
    isolated_bank_database,
):
    dsn, owner = isolated_bank_database
    portfolio, employee, run_id, receipt, cert_id = (uuid4() for _ in range(5))
    account_id, company_id = uuid4(), uuid4()
    tournament_ids = sorted([uuid4(), uuid4()])
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    await owner.execute(
        "INSERT INTO public.client_executive_portfolios VALUES ($1, TRUE)", portfolio
    )
    await owner.executemany(
        "INSERT INTO public.client_executive_portfolio_tournaments VALUES ($1, $2, TRUE)",
        [(portfolio, tid) for tid in tournament_ids],
    )
    await owner.execute("INSERT INTO public.empleados VALUES ($1)", employee)
    await owner.execute(
        """
        INSERT INTO public.accounting_import_runs
        VALUES ($1, 'banco', 'apply', 'completed', 'synthetic.csv', '2026-08-01', $2)
    """,
        run_id,
        SOURCE_SHA,
    )
    await owner.execute(
        """
        INSERT INTO public.bank_movements VALUES
            ($1, $2, 2, '2026-07-02', '09:00', '+', 120.01, 120.01, $3),
            ($4, $2, 3, '2026-07-10', '11:00', '-', 33.02, 86.99, $3)
    """,
        uuid4(),
        run_id,
        ACCOUNT,
        uuid4(),
    )
    engine = create_async_engine(dsn)

    async def report(
        tournaments=tournament_ids, portfolio_id=portfolio, bounds=(start, end)
    ):
        async with AsyncSession(engine) as session:
            return await load_direction_bank_period(
                session,
                scope={
                    "portfolio_ids": [str(portfolio_id)],
                    "selected": [{"id": str(tid)} for tid in tournaments],
                },
                start=bounds[0],
                end=bounds[1],
            )

    async def certify(certification, batch, scope, bounds, anchors, bank_receipt):
        movements = await owner.fetch(
            "SELECT * FROM public.bank_movements WHERE import_run_id = $1 ORDER BY source_row_number",
            batch,
        )
        await owner.execute(
            """
            INSERT INTO public.direction_bank_statement_certifications (
                id, import_run_id, source_sha256, series_sha256, account_fingerprint,
                account_id, company_id, account_last4, currency, portfolio_id,
                tournament_ids, coverage_start, coverage_end, opening_balance,
                closing_balance, descending_rows, evidence_ref, certified_by, certified_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, '9826', 'MXN', $8, $9,
                      $10, $11, $12, $13, FALSE, $14, $15, now())
        """,
            certification,
            batch,
            SOURCE_SHA,
            bank_series_sha256(movements),
            hashlib.sha256(ACCOUNT.encode()).hexdigest(),
            account_id,
            company_id,
            portfolio,
            scope,
            *bounds,
            *anchors,
            bank_receipt,
            employee,
        )

    async def revoke(certification):
        await owner.execute(
            """
            UPDATE public.direction_bank_statement_certifications
            SET revoked_at = now(), revoked_by = $1, revocation_reason = 'fixture revoked'
            WHERE id = $2
        """,
            employee,
            certification,
        )

    try:
        assert (await report())["gap"] == SOURCE_UNAVAILABLE
        await owner.execute(MIGRATION.read_text())
        await owner.execute(MIGRATION.read_text())
        await certify(
            cert_id,
            run_id,
            tournament_ids,
            (start, end),
            (Decimal("0"), Decimal("86.99")),
            receipt,
        )
        verified = await report()
        period = verified["periods"][0]
        assert [
            period[key]
            for key in ("opening", "origins_total", "applications_total", "closing")
        ] == ["0.00", "120.01", "33.02", "86.99"]
        assert verified["revision"] and str(receipt) in period["source"]
        assert ACCOUNT not in str(verified)
        assert (await report([tournament_ids[0]]))["gap"] == SOURCE_UNAVAILABLE
        assert (await report(portfolio_id=uuid4()))["gap"] == SOURCE_UNAVAILABLE
        await owner.execute(
            "UPDATE client_executive_portfolio_tournaments SET active = FALSE WHERE tournament_id = $1",
            tournament_ids[0],
        )
        assert (await report())["gap"] == SOURCE_UNAVAILABLE
        await owner.execute(
            "UPDATE client_executive_portfolio_tournaments SET active = TRUE"
        )

        overlapping = uuid4()
        await certify(
            overlapping,
            run_id,
            tournament_ids[:1],
            (start, end),
            (Decimal("0"), Decimal("86.99")),
            uuid4(),
        )
        assert (await report(tournament_ids[:1]))["gap"] == INVALID_SOURCE
        assert (await report())["gap"] == INVALID_SOURCE
        await revoke(overlapping)
        assert (await report())["revision"] == verified["revision"]

        # A query for September must load July because August's opening and
        # closing depend on its certified neighbors and source rows.
        for month, last_day, expected in ((8, 31, 96.99), (9, 30, 106.99)):
            batch = uuid4()
            await owner.execute(
                """
                INSERT INTO public.accounting_import_runs
                VALUES ($1, 'banco', 'apply', 'completed', $2, '2026-10-01', $3)
            """,
                batch,
                f"synthetic-{month}.csv",
                SOURCE_SHA,
            )
            await owner.execute(
                """
                INSERT INTO public.bank_movements VALUES
                    ($1, $2, 2, $3, '09:00', '+', 10, $4, $5)
            """,
                uuid4(),
                batch,
                date(2026, month, 10),
                expected,
                ACCOUNT,
            )
            await certify(
                uuid4(),
                batch,
                tournament_ids,
                (date(2026, month, 1), date(2026, month, last_day)),
                (None, None),
                uuid4(),
            )
        later = (await report(bounds=(date(2026, 9, 1), date(2026, 9, 30))))["periods"][
            0
        ]
        assert (later["opening"], later["closing"]) == ("96.99", "106.99")

        await owner.execute(
            """
            INSERT INTO public.accounting_import_runs
            VALUES ($1, 'banco', 'apply', 'completed', 'synthetic.csv', '2026-08-02', $2)
        """,
            uuid4(),
            "af" * 32,
        )
        assert (await report())["gap"] == INVALID_SOURCE

        await revoke(cert_id)
        assert (await report())["gap"] == SOURCE_UNAVAILABLE
        wide_scope = uuid4()
        await certify(
            wide_scope,
            run_id,
            sorted(uuid4() for _ in range(200)),
            (start, end),
            (Decimal("0"), Decimal("86.99")),
            uuid4(),
        )
        await revoke(wide_scope)
        with pytest.raises(asyncpg.RaiseError, match="Archive and verify"):
            await owner.execute(ROLLBACK.read_text())
        await owner.execute("ROLLBACK")
        await owner.execute("SET samchat.direction_bank_archive_verified = 'on'")
        await owner.execute(ROLLBACK.read_text())
        assert (await report())["gap"] == SOURCE_UNAVAILABLE
        assert await owner.fetchval("SELECT count(*) FROM bank_movements") == 4
    finally:
        await engine.dispose()
