"""Owner import acceptance on a uniquely named disposable PostgreSQL database."""

import asyncio
import csv
import hashlib
import json
import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from samchat.accounting_historical import service


@pytest_asyncio.fixture
async def database():
    local = "postgresql+asyncpg://postgres@127.0.0.1:55479/postgres"
    ci = "postgresql+asyncpg://test_user:test_password@127.0.0.1:55471/devnous_test"
    if os.environ.get("SAMCHAT_WEEKLY_TEST_DATABASE_URL") == local:
        server = local
    elif (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("TEST_AMEX_DATABASE_URL") == ci
    ):
        server = ci
    else:
        pytest.skip("Requires the exact disposable PostgreSQL server")
    admin = await asyncpg.connect(server.replace("+asyncpg", ""))
    name = "historical_manifest_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    engine = create_async_engine(server.rsplit("/", 1)[0] + "/" + name)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE accounting_import_runs(id uuid PRIMARY KEY,source_type text,filename text,source_sha256 text,mode text,status text,started_by_empleado_id text,started_at timestamptz,finished_at timestamptz,summary_json jsonb,error_text text)"
                )
            )
        async with AsyncSession(engine) as session:
            await service.ensure_historical_accounting_schema(session)

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def no_ddl(conn, cursor, statement, parameters, context, executemany):
            assert statement.lstrip().split()[0].lower() not in {
                "create",
                "alter",
                "drop",
                "truncate",
            }, "DDL during explicit import"

        yield engine
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
def manifest(tmp_path):
    identity = {
        "fiscal_year": "2026",
        "company_code": "02",
        "company_label": "PMD0608162M2",
    }
    balances = []
    headers = []
    lines = []
    for month in range(1, 10):
        base = {**identity, "month_num": str(month)}
        pid = f"2026-{month:02d}:Dr:1"
        if month < 9:
            balances.append(
                {
                    **base,
                    "account_code": "1120-001-001",
                    "description": "Source bank",
                    "level": "3",
                    "is_detail": "True",
                    "saldo_inicial": "0",
                    "cargos": "10",
                    "abonos": "10",
                    "saldo_final": "0",
                    "row_number": str(month),
                }
            )
        headers.append(
            {
                **base,
                "policy_id": pid,
                "policy_type": "Dr",
                "policy_number": "1",
                "policy_date": f"2026-{month:02d}-01",
                "description": "Source policy",
                "line_count": "2",
                "total_debe": "10",
                "total_haber": "10",
                "has_summary": "False",
                "summary_debe": "0",
                "summary_haber": "0",
            }
        )
        for index in range(2):
            lines.append(
                {
                    **base,
                    "policy_id": pid,
                    "row_number": str(month * 2 + index),
                    "account_code": "1120-001-001",
                    "account_name": "Source bank",
                    "debe": "10" if not index else "0",
                    "haber": "0" if not index else "10",
                    "concept": "Source concept",
                }
            )
    doc = {
        "schema_version": "samchat.historical.explicit.v1",
        "fiscal_year": 2026,
        "company_code": "02",
        "company_label": "PMD0608162M2",
        "originals": [],
        "derived": {},
    }
    for family in ["trial_balance", "policy_lines"]:
        path = tmp_path / (family + ".xlsx")
        path.write_bytes(b"original")
        doc["originals"].append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_family": "original_" + family,
            }
        )
    for kind, rows in [
        ("trial_balance", balances),
        ("policy_headers", headers),
        ("policy_lines", lines),
    ]:
        path = tmp_path / (kind + ".csv")
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        doc["derived"][kind] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc))
    return path


async def apply(session, manifest):
    return await service.import_historical_accounting_pilot(
        session,
        fiscal_year=2026,
        source_kind="normalized_manifest",
        source_path=str(manifest),
        company_code="02",
        company_label="PMD0608162M2",
        started_by_empleado_id="actor",
        apply_changes=True,
        ensure_schema=False,
    )


@pytest.mark.asyncio
async def test_full_bundle_no_ddl_repeat_and_legacy_preservation(database, manifest):
    old = str(uuid4())
    async with AsyncSession(database) as session:
        await session.execute(
            text(
                "INSERT INTO accounting_import_runs(id,source_type,filename,source_sha256,mode,status,started_at,finished_at) VALUES(CAST(:id AS uuid),'other','preserved','old','apply','completed',NOW()-interval '1 day',NOW()-interval '1 day')"
            ),
            {"id": old},
        )
        await session.commit()
        result = await apply(session, manifest)
        assert (
            result["created"]
            and result["summary"]["trial_balance_rows"] == 8
            and result["summary"]["policy_lines"] == 18
        )
        replay = await apply(session, manifest)
        assert replay["skipped"] and replay["import_run_id"] == result["import_run_id"]
        assert (
            await session.scalar(text("SELECT count(*) FROM accounting_import_runs"))
            == 2
        )
        assert (
            await session.scalar(
                text(
                    "SELECT filename FROM accounting_import_runs WHERE id=CAST(:id AS uuid)"
                ),
                {"id": old},
            )
            == "preserved"
        )
        latest = await service._find_latest_applied_import_run(
            session, fiscal_year=2026, company_code="02"
        )
        assert latest["id"] == result["import_run_id"]
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM historical_policy_lines WHERE project_raw IS NOT NULL OR cost_center_raw IS NOT NULL"
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM historical_trial_balance_rows WHERE fiscal_month=9"
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_outer_rollback_covers_internal_commit_and_all_sources(
    database, manifest
):
    async with database.connect() as conn:
        tx = await conn.begin()
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint"
        ) as session:
            await apply(session, manifest)
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM historical_policy_lines")
                )
                == 18
            )
        async with AsyncSession(database) as observer:
            assert (
                await observer.scalar(
                    text("SELECT count(*) FROM accounting_import_runs")
                )
                == 0
            )
        await tx.rollback()
    async with AsyncSession(database) as observer:
        for table in [
            "accounting_import_runs",
            "historical_accounting_source_files",
            "historical_policy_headers",
            "historical_policy_lines",
            "historical_trial_balance_rows",
        ]:
            assert await observer.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.asyncio
async def test_concurrent_same_bundle_has_one_version(database, manifest):
    async def worker():
        async with AsyncSession(database) as session:
            return await apply(session, manifest)

    results = await asyncio.gather(worker(), worker())
    assert sum(bool(r.get("created")) for r in results) == 1
    assert len({r["import_run_id"] for r in results}) == 1
    async with AsyncSession(database) as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM accounting_import_runs"))
            == 1
        )
        assert (
            await session.scalar(text("SELECT count(*) FROM historical_policy_lines"))
            == 18
        )


@pytest.mark.asyncio
async def test_partial_replacement_never_falls_back_to_withheld_prior_rows(
    database, manifest
):
    async with AsyncSession(database) as session:
        full = await apply(session, manifest)
        document = json.loads(manifest.read_text())
        partial_dir = manifest.parent / "partial"
        partial_dir.mkdir()
        proof = partial_dir / "full-source-review.json"
        proof.write_text(
            json.dumps(
                {
                    "identity": {
                        "fiscal_year": 2026,
                        "company_code": "02",
                        "company_label": "PMD0608162M2",
                    }
                }
            )
        )
        evidence = {
            "path": str(proof),
            "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
        }
        document["full_validation_report"] = evidence
        document["withheld_months"] = [
            {
                "month_num": 3,
                "reason": "Reviewed source discrepancy",
                "evidence": dict(evidence),
            }
        ]
        for family, entry in document["derived"].items():
            with open(entry["path"]) as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames
                rows = [r for r in reader if int(r["month_num"]) != 3]
            target = partial_dir / (family + ".csv")
            with target.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            document["derived"][family] = {
                "path": str(target),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        partial = partial_dir / "manifest.json"
        partial.write_text(json.dumps(document))
        updated = await apply(session, partial)
        assert updated["created"] and updated["import_run_id"] != full["import_run_id"]
        assert (
            updated["summary"]["trial_balance_rows"] == 7
            and updated["summary"]["policy_lines"] == 16
        )
        selected = await service._find_latest_applied_import_run(
            session, fiscal_year=2026, company_code="02"
        )
        assert selected["id"] == updated["import_run_id"]
        snapshot = await service.load_historical_accounting_snapshot(
            session, fiscal_year=2026, company_code="02"
        )
        assert "2026-03" not in snapshot["summary"]["reconciliation"]["periods"]
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM historical_policy_lines WHERE import_run_id=CAST(:id AS uuid) AND fiscal_month=3"
                ),
                {"id": full["import_run_id"]},
            )
            == 2
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM historical_policy_lines WHERE import_run_id=CAST(:id AS uuid) AND fiscal_month=3"
                ),
                {"id": updated["import_run_id"]},
            )
            == 0
        )
        for mode, status, source_type in [
            ("dry_run", "completed", "historical_accounting"),
            ("apply", "failed", "historical_accounting"),
            ("apply", "completed", "other"),
        ]:
            ghost = str(uuid4())
            await session.execute(
                text(
                    "INSERT INTO accounting_import_runs(id,source_type,filename,source_sha256,mode,status,started_at,finished_at) VALUES(CAST(:id AS uuid),:source,'ignored','ignored',:mode,:status,NOW()+interval '1 day',NOW()+interval '1 day')"
                ),
                {"id": ghost, "source": source_type, "mode": mode, "status": status},
            )
            await session.execute(
                text(
                    "INSERT INTO historical_accounting_source_files(id,import_run_id,fiscal_year,source_family,source_format,source_filename,source_path,company_code,company_label) VALUES(CAST(:id AS uuid),CAST(:run AS uuid),2026,'balanza','csv','ignored.csv','ignored.csv','02','PMD0608162M2')"
                ),
                {"id": str(uuid4()), "run": ghost},
            )
        companies = await service.list_historical_accounting_companies(
            session, fiscal_year=2026
        )
        assert len(companies) == 1
        company = companies[0]
        assert company["company_code"] == "02"
        assert company["trial_balance_periods"] == 7 and company["policy_periods"] == 8
        assert company["policy_headers"] == 8 and company["policy_lines"] == 16
        assert company["source_files"] == len(
            snapshot["summary"]["persisted_source_files"]
        )
        assert (
            3 not in company["trial_balance_months"]
            and 3 not in company["policy_months"]
        )
        assert [item["month_num"] for item in company["withheld_months"]] == [3]
        coverage = await service.list_historical_accounting_coverage(
            session, year_from=2026, year_to=2026
        )
        assert coverage == [{"fiscal_year": 2026, **company}]
        async with AsyncSession(database) as readonly:
            await readonly.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            visible_companies = await service.list_historical_accounting_companies(
                readonly, fiscal_year=2026
            )
            visible_coverage = await service.list_historical_accounting_coverage(
                readonly, year_from=2026, year_to=2026
            )
            assert visible_companies[0]["policy_lines"] == 16
            assert visible_companies[0]["trial_balance_periods"] == 7
            assert 3 not in visible_companies[0]["policy_months"]
            assert visible_coverage == [{"fiscal_year": 2026, **visible_companies[0]}]
            from samchat.assistant.tools import (
                _load_historical_accounting_tables_report,
            )

            async def report(kind, month=None, **filters):
                result = await _load_historical_accounting_tables_report(
                    readonly,
                    report_type=kind,
                    year=2026,
                    company_code="02",
                    month=month,
                    **filters,
                )
                assert result is not None
                assert result["coverage"]["import_run_id"] == updated["import_run_id"]
                return result

            for kind in ("diario", "mayor", "balanza"):
                withheld = await report(kind, 3)
                assert withheld["coverage"]["status"] == "withheld"
                assert withheld["summary"]["debe"] is None
                assert withheld["rows"] == []
                assert "Reviewed source discrepancy" in withheld["artifact_markdown"]
                admitted = await report(kind, 1)
                assert admitted["rows"] and admitted["summary"]["debe"] == 10
                empty_filter = await report(kind, 1, q="no-such-account")
                assert empty_filter["coverage"]["status"] == "no_matching_rows"
                assert empty_filter["summary"]["debe"] is None
                annual = await report(kind)
                assert annual["coverage"]["status"] == "partial"
                assert 3 not in annual["coverage"]["available_months"]
                assert "Cobertura parcial" in annual["artifact_markdown"]
            september = await report("balanza", 9)
            assert september["coverage"]["status"] == "unavailable"
            assert september["summary"]["saldo_final"] is None
        assert (
            await service.list_historical_accounting_coverage(session, year_from=2027)
            == []
        )
        assert (
            await service.list_historical_accounting_companies(
                session, fiscal_year=2025
            )
            == []
        )
        old_snapshot = await service.load_historical_accounting_snapshot(
            session,
            fiscal_year=2026,
            company_code="02",
            import_run_id=full["import_run_id"],
        )
        assert old_snapshot["summary"]["policy_lines"] == 18
        assert "2026-03" in old_snapshot["summary"]["reconciliation"]["periods"]
        with pytest.raises(ValueError, match="superseded"):
            await apply(session, manifest)
