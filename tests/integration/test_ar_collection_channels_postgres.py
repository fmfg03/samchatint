"""Real owner SQL against in-memory PostgreSQL/WASM; no server/DSN/credentials."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# Import the owning package first, as the web runtime does (avoids legacy cycle).
from devnous.gastos.services.cfdi_income_bridge_service import (
    CFDIIncomeBridgeError,
    confirm_cfdi_income_collection,
    list_budget_cfdi_income_links,
    soft_unlink_cfdi_income,
)
from samchat.ar.collection_matches import (
    ARCollectionMatchError,
    accept_ar_collection_match,
    ensure_ar_collection_match_schema,
    reverse_ar_collection_match,
)


def uid(n):
    return str(UUID(int=n))


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class Session:
    def __init__(self, module):
        self.process = subprocess.Popen(
            [
                shutil.which("node"),
                str(Path(__file__).with_name("budget_distribution_pg.mjs")),
                module,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert json.loads(self.process.stdout.readline()) == {"ready": True}
        self.in_tx = False
        self.statements = []

    def query(self, statement, params=None):
        sql = (
            str(statement.compile(dialect=postgresql.dialect()))
            if hasattr(statement, "compile")
            else statement
        )
        names = []

        def bind(match):
            names.append(match[1])
            return "$" + str(len(names))

        sql = re.sub(r"%\((\w+)\)s", bind, sql)
        self.statements.append(sql)
        self.process.stdin.write(
            json.dumps(
                {"sql": sql, "parameters": [(params or {})[key] for key in names]},
                default=str,
            )
            + "\n"
        )
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        if "error" in result:
            raise RuntimeError(result["error"])
        return Result(result["rows"])

    async def execute(self, statement, params=None):
        if not self.in_tx:
            self.query("BEGIN")
            self.in_tx = True
        return self.query(statement, params)

    async def commit(self):
        if self.in_tx:
            self.query("COMMIT")
            self.in_tx = False

    async def rollback(self):
        if self.in_tx:
            self.query("ROLLBACK")
            self.in_tx = False

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.process.stdout.close()
        self.process.stderr.close()


@pytest.fixture
async def db():
    module = os.environ.get("AR_TEST_PGLITE_MODULE")
    if not module or not Path(module).is_file() or not shutil.which("node"):
        pytest.skip(
            "Set AR_TEST_PGLITE_MODULE to a local PGlite module; never use a production DSN"
        )
    session = Session(module)
    schema = """
      CREATE TABLE accounting_close_periods (fiscal_year int, fiscal_month int, status text, UNIQUE(fiscal_year, fiscal_month));
      CREATE TABLE cfdi_reports (id uuid PRIMARY KEY, cfdi_uuid text, fecha timestamptz,
        moneda text, total numeric, total_impuestos_trasladados numeric,
        emisor_rfc text, emisor_nombre text, receptor_rfc text, receptor_nombre text);
      CREATE TABLE budget_lines (id uuid PRIMARY KEY, concept_name text,
        account_code_final text, account_code_suggested text);
      CREATE TABLE cuentas_contables (id uuid PRIMARY KEY, codigo text, nombre text, activo boolean, tipo text);
      CREATE TABLE accounting_polizas (
        id uuid PRIMARY KEY, source_file text, source_sheet text, tipo_poliza text,
        numero_poliza text, fecha_poliza timestamptz, beneficiario_nombre text,
        concepto text, concepto_resumen text, line_count_declared int, line_count_actual int,
        cfdi_report_id uuid, cfdi_uuid text, origen text, created_at timestamptz, updated_at timestamptz);
      CREATE TABLE accounting_poliza_lines (
        id uuid PRIMARY KEY, poliza_id uuid REFERENCES accounting_polizas(id), line_no int,
        cuenta_codigo text, cuenta_contable_id uuid, concepto text, movimiento_no text,
        debe numeric, haber numeric, raw_row_json jsonb, created_at timestamptz);
      CREATE TABLE budget_cfdi_income_links (
        id uuid PRIMARY KEY, cfdi_report_id uuid, amount numeric, accounting_poliza_id uuid,
        collection_poliza_id uuid, status text, collection_date timestamptz,
        collected_by_empleado_id uuid, updated_at timestamptz, unlinked_at timestamptz,
        budget_line_id uuid, budget_version_id uuid, tournament_id uuid, phase text,
        budget_concept_id uuid, income_date timestamptz, source text, created_at timestamptz,
        unlinked_by_empleado_id uuid);
      CREATE TABLE bank_movements (
        id uuid PRIMARY KEY, signo text, importe numeric, fecha timestamptz,
        cuenta_bancaria text, rfc_ordenante text, nombre_ordenante text,
        descripcion text, concepto_banco text, conciliacion_estado text);
    """
    try:
        for statement in schema.split(";"):
            if statement.strip():
                session.query(statement)
        await ensure_ar_collection_match_schema(session)
        await session.commit()
        session.query(
            text(
                "INSERT INTO cfdi_reports (id, total, total_impuestos_trasladados, moneda) VALUES (:id, 1160, 160, 'MXN')"
            ),
            {"id": uid(1)},
        )
        session.query(
            text(
                "INSERT INTO budget_lines VALUES (:id, 'Synthetic income', '4100-001', NULL)"
            ),
            {"id": uid(11)},
        )
        for number, code, kind in [
            (2, "1120-001-001", "banco"),
            (3, "1150-001", "activo"),
        ]:
            session.query(
                text(
                    "INSERT INTO cuentas_contables VALUES (:id, :code, :code, TRUE, :kind)"
                ),
                {"id": uid(number), "code": code, "kind": kind},
            )
        session.query(
            text(
                "INSERT INTO accounting_polizas (id, cfdi_report_id, origen) VALUES (:id, :cfdi, 'cxc_cfdi_income')"
            ),
            {"id": uid(4), "cfdi": uid(1)},
        )
        session.query(
            text(
                "INSERT INTO accounting_poliza_lines (id, poliza_id, cuenta_codigo, cuenta_contable_id, debe, haber, raw_row_json) VALUES (:id, :policy, '1150-001', :account, 1160, 0, '{\"movement\":\"debe_cxc\"}')"
            ),
            {"id": uid(5), "policy": uid(4), "account": uid(3)},
        )
        session.query(
            text(
                "INSERT INTO budget_cfdi_income_links (id, cfdi_report_id, amount, accounting_poliza_id, status) VALUES (:id, :cfdi, 1160, :policy, 'approved')"
            ),
            {"id": uid(6), "cfdi": uid(1), "policy": uid(4)},
        )
        session.query(
            text(
                "UPDATE budget_cfdi_income_links SET budget_line_id = :line, budget_version_id = :version, tournament_id = :tournament, income_date = '2026-01-01'"
            ),
            {"line": uid(11), "version": uid(9), "tournament": uid(12)},
        )
        for number, code, amount in [(13, "4100-001", 1000), (14, "2140-001", 160)]:
            session.query(
                text(
                    "INSERT INTO accounting_poliza_lines (id, poliza_id, cuenta_codigo, debe, haber) VALUES (:id, :policy, :code, 0, :amount)"
                ),
                {"id": uid(number), "policy": uid(4), "code": code, "amount": amount},
            )
        for number, amount in [(7, 400), (8, 760)]:
            session.query(
                text(
                    "INSERT INTO bank_movements (id, signo, importe, fecha, cuenta_bancaria, rfc_ordenante) VALUES (:id, '+', :amount, '2026-01-02', 'TESTBANK', 'TEST')"
                ),
                {"id": uid(number), "amount": amount},
            )
        yield session
    finally:
        session.close()


async def accept(db, bank=7, item="linked:" + uid(6)):
    return await accept_ar_collection_match(
        db,
        ar_item={
            "ar_item_id": item,
            "budget_line_id": uid(11) if item.startswith("linked:") else None,
            "budget_version_id": uid(9),
            "cfdi_report_id": uid(1),
            "amount": 1160,
            "payer_rfc": "TEST",
        },
        bank_movement_id=uid(bank),
        actor_empleado_id=uid(10),
        acceptance_reason="synthetic identified receipt",
        bank_account_id=uid(2),
        tolerance=0,
    )


async def direct(db):
    return await confirm_cfdi_income_collection(
        db,
        link_id=uid(6),
        actor_empleado_id=uid(10),
        collection_date="2026-01-02",
    )


def seed_legacy_direct(db):
    """Fixture only: historical receipt predates the match-only policy."""
    db.query(
        text(
            "INSERT INTO accounting_polizas (id, cfdi_report_id, origen) VALUES (:id, :cfdi, 'cxc_collection')"
        ),
        {"id": uid(15), "cfdi": uid(1)},
    )
    db.query(
        text(
            "UPDATE budget_cfdi_income_links SET collection_poliza_id = :id, collection_date = '2026-01-02'"
        ),
        {"id": uid(15)},
    )


def counts(db):
    return db.query(
        "SELECT (SELECT COUNT(*) FROM accounting_polizas) AS policies, (SELECT COUNT(*) FROM ar_collection_matches) AS receipts, (SELECT COUNT(*) FROM ar_collection_match_audit_log) AS events"
    ).first()


@pytest.mark.asyncio
async def test_direct_then_match_is_rejected_even_after_unlink(db):
    seed_legacy_direct(db)
    first = await direct(db)
    before = counts(db)
    assert (await direct(db))["poliza_id"] == first["poliza_id"]
    assert counts(db) == before
    db.query("UPDATE budget_cfdi_income_links SET unlinked_at = NOW()")
    with pytest.raises(ARCollectionMatchError, match="collection_channel_conflict"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before
    assert any("FOR UPDATE" in sql and "cfdi_reports" in sql for sql in db.statements)


@pytest.mark.asyncio
async def test_partials_then_direct_rejected_reversal_preserves_history(db):
    first = await accept(db)
    await db.commit()
    second = await accept(db, bank=8)
    await db.commit()
    before = counts(db)
    with pytest.raises(CFDIIncomeBridgeError, match="collection_channel_conflict"):
        await direct(db)
    await db.rollback()
    assert counts(db) == before
    with pytest.raises(ARCollectionMatchError, match="active_match_exists"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before
    from samchat.ar.reversal_preview import prepare_collection_reversal

    preview = await prepare_collection_reversal(db, second)
    assert preview["original_poliza_id"] == second["accounting_poliza_id"]
    assert preview["reversal_accounting_date"] is None
    assert preview["can_post"] is False
    assert {line["account_code"] for line in preview["lines"]} == {
        "1120-001-001",
        "1150-001",
    }
    assert sum(float(line["debe"]) for line in preview["lines"]) == 760
    assert sum(float(line["haber"]) for line in preview["lines"]) == 760
    # A changed mapping must never substitute the original posted account.
    await db.execute(
        text("UPDATE ar_bank_account_mappings SET cuenta_contable_id = :id"),
        {"id": uid(3)},
    )
    assert (await prepare_collection_reversal(db, second))["lines"] == preview["lines"]
    await db.rollback()
    for _ in range(2):
        with pytest.raises(
            ARCollectionMatchError, match="reversal_accounting_policy_required"
        ):
            await reverse_ar_collection_match(
                db,
                match_id=second["id"],
                actor_empleado_id=uid(10),
                reversal_reason="synthetic correction",
            )
        await db.rollback()
        assert counts(db) == before
    assert (
        db.query(
            "SELECT SUM(accepted_amount) AS amount FROM ar_collection_matches WHERE status = 'accepted_collection_match'"
        ).first()["amount"]
        == "1160.00"
    )
    assert (
        db.query(
            text("SELECT status FROM ar_collection_matches WHERE id = :id"),
            {"id": first["id"]},
        ).first()["status"]
        == "accepted_collection_match"
    )


@pytest.mark.asyncio
async def test_same_invoice_stale_candidate_alias_cannot_bypass_residual(db):
    await accept(db)
    await db.commit()
    db.query(
        text("UPDATE bank_movements SET importe = 800 WHERE id = :id"), {"id": uid(8)}
    )
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="ar_source_context_changed"):
        await accept(db, bank=8, item="candidate:" + uid(1))
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_current_posting_amount_is_checked_before_new_collection(db):
    # Same IDs, source posting changed after the UI prepared its form.
    db.query(
        "UPDATE accounting_poliza_lines SET debe = 1100 WHERE raw_row_json->>'movement' = 'debe_cxc'"
    )
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="ar_source_amount_changed"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_link_reader_uses_actual_gross_and_journal_not_tax_estimate(db):
    rows = await list_budget_cfdi_income_links(
        db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=True
    )
    assert len(rows) == 1
    assert float(rows[0]["cfdi_total"]) == 1160
    assert float(rows[0]["recognized_income_amount"]) == 1000
    assert rows[0]["recognized_poliza_id"] == uid(4)
    assert (
        await list_budget_cfdi_income_links(
            db, budget_version_id=uid(9), tournament_id=uid(99), approved_only=True
        )
        == []
    )
    # Absence of the journal income line is missing evidence, not total-minus-tax.
    await db.execute(
        text("DELETE FROM accounting_poliza_lines WHERE cuenta_codigo = '4100-001'")
    )
    rows = await list_budget_cfdi_income_links(
        db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=True
    )
    assert rows[0]["recognized_income_amount"] is None
    await db.rollback()


@pytest.mark.asyncio
async def test_soft_unlink_does_not_reverse_or_delete_accounting_evidence(db):
    before = counts(db)
    assert (
        await soft_unlink_cfdi_income(db, link_id=uid(6), actor_empleado_id=uid(10))
        is True
    )
    assert counts(db) == before
    assert (
        await list_budget_cfdi_income_links(
            db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=True
        )
        == []
    )
    rows = await list_budget_cfdi_income_links(
        db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=False
    )
    assert rows[0]["unlinked_at"]
    assert float(rows[0]["recognized_income_amount"]) == 1000
    assert rows[0]["recognized_poliza_id"] == uid(4)


@pytest.mark.asyncio
async def test_reader_detects_preserved_cross_version_match_evidence(db):
    # Simulate pre-existing overlap; never produce it through a new owner write.
    seed_legacy_direct(db)
    db.query(
        text(
            "INSERT INTO ar_collection_matches (ar_item_id, budget_version_id, cfdi_report_id, bank_movement_id, accepted_amount, status, acceptance_reason) VALUES ('legacy-other-version', :version, :cfdi, :bank, 400, 'accepted_collection_match', 'synthetic historical evidence')"
        ),
        {"version": uid(99), "cfdi": uid(1), "bank": uid(7)},
    )
    rows = await list_budget_cfdi_income_links(
        db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=True
    )
    assert rows[0]["has_accepted_collection"] is True
    before = counts(db)
    with pytest.raises(CFDIIncomeBridgeError, match="collection_channel_conflict"):
        await direct(db)
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_new_manual_collection_rejected_without_new_posting(db):
    before = counts(db)
    for _ in range(2):
        with pytest.raises(CFDIIncomeBridgeError, match="bank_reconciliation_required"):
            await direct(db)
        await db.rollback()
        assert counts(db) == before
    receipt = await accept(db)
    await db.commit()
    assert float(receipt["accepted_amount"]) == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_identity", ["id", "uuid"])
async def test_legacy_accounting_ui_receipt_prevents_second_collection(
    db, legacy_identity
):
    db.query("UPDATE cfdi_reports SET cfdi_uuid = 'SYNTHETIC-UUID'")
    db.query(
        text(
            "INSERT INTO accounting_polizas (id, cfdi_report_id, cfdi_uuid, origen) VALUES (:id, :cfdi, :uuid, 'ingreso_cobrado_ui')"
        ),
        {
            "id": uid(17),
            "cfdi": uid(1) if legacy_identity == "id" else None,
            "uuid": "synthetic-uuid" if legacy_identity == "uuid" else None,
        },
    )
    rows = await list_budget_cfdi_income_links(
        db, budget_version_id=uid(9), tournament_id=uid(12), approved_only=True
    )
    assert rows[0]["manual_collection_poliza_ids"] == [uid(17)]
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="collection_channel_conflict"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("budget_version_id", uid(99)),
        ("budget_line_id", uid(99)),
        ("ar_item_id", "linked:" + uid(99)),
    ],
)
async def test_current_link_context_rejects_foreign_objects(db, field, value):
    item = {
        "ar_item_id": "linked:" + uid(6),
        "budget_version_id": uid(9),
        "budget_line_id": uid(11),
        "cfdi_report_id": uid(1),
        "amount": 1160,
        "payer_rfc": "TEST",
    }
    item[field] = value
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="ar_source_context_changed"):
        await accept_ar_collection_match(
            db,
            ar_item=item,
            bank_movement_id=uid(7),
            actor_empleado_id=uid(10),
            acceptance_reason="synthetic",
            bank_account_id=uid(2),
        )
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_collection_transaction_rolls_back_after_posting_failure(db, monkeypatch):
    from unittest.mock import AsyncMock
    from samchat.ar import collection_matches

    before = counts(db)
    real_audit = collection_matches._audit_match_event
    monkeypatch.setattr(
        collection_matches,
        "_audit_match_event",
        AsyncMock(side_effect=RuntimeError("synthetic audit failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await accept(db)
    # The failure occurs after receipt, mapping, journal and journal lines exist.
    assert counts(db)["policies"] == before["policies"] + 1
    assert counts(db)["receipts"] == before["receipts"] + 1
    await db.rollback()
    assert counts(db) == before
    assert (
        db.query("SELECT COUNT(*) AS count FROM ar_bank_account_mappings").first()[
            "count"
        ]
        == 0
    )
    monkeypatch.setattr(collection_matches, "_audit_match_event", real_audit)
    await accept(db)
    await db.commit()
    assert counts(db) == {
        "policies": before["policies"] + 1,
        "receipts": 1,
        "events": 1,
    }
    assert any(
        "FROM bank_movements" in sql and "FOR UPDATE" in sql for sql in db.statements
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["unlinked", "pending", "missing_policy"])
async def test_stale_approved_link_cannot_create_new_collection(db, changed):
    sql = {
        "unlinked": "UPDATE budget_cfdi_income_links SET unlinked_at = NOW()",
        "pending": "UPDATE budget_cfdi_income_links SET status = 'pending_approval'",
        "missing_policy": "UPDATE budget_cfdi_income_links SET accounting_poliza_id = NULL",
    }[changed]
    db.query(sql)
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="ar_source_context_changed"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_unlinked_candidate_keeps_receivable_without_inventing_budget_link(db):
    db.query("UPDATE budget_cfdi_income_links SET unlinked_at = NOW()")
    receipt = await accept(db, item="candidate:" + uid(1))
    await db.commit()
    assert receipt["budget_line_id"] is None
    assert receipt["cfdi_report_id"] == uid(1)
    assert db.query(
        "SELECT unlinked_at IS NOT NULL AS unlinked FROM budget_cfdi_income_links"
    ).first()["unlinked"]


@pytest.mark.asyncio
async def test_current_link_excess_partial_is_rejected(db):
    await accept(db)
    await db.commit()
    db.query(
        text("UPDATE bank_movements SET importe = 800 WHERE id = :id"), {"id": uid(8)}
    )
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="amount_exceeds_outstanding"):
        await accept(db, bank=8)
    await db.rollback()
    assert counts(db) == before


@pytest.mark.asyncio
async def test_collection_cannot_post_in_existing_closed_period(db):
    db.query("INSERT INTO accounting_close_periods VALUES (2026, 1, 'closed')")
    before = counts(db)
    with pytest.raises(
        ARCollectionMatchError, match="collection_accounting_period_closed"
    ):
        await accept(db)
    await db.rollback()
    assert counts(db) == before
    assert (
        db.query("SELECT status FROM accounting_close_periods").first()["status"]
        == "closed"
    )


@pytest.mark.asyncio
async def test_undated_bank_receipt_never_posts_using_current_date(db):
    db.query("UPDATE bank_movements SET fecha = NULL")
    before = counts(db)
    with pytest.raises(ARCollectionMatchError, match="collection_date_required"):
        await accept(db)
    await db.rollback()
    assert counts(db) == before
