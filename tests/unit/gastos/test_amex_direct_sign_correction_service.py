"""Source-proof, write-free preview and owner transaction contracts."""

import copy
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_sign_correction_service as svc
from devnous.gastos.services.amex_direct_common import DirectConflict, digest
from devnous.gastos.services.amex_direct_intake_service import parse_direct_csv
from devnous.gastos.services.amex_direct_recovery_service import _json_source


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        return self.rows[0]

    def scalars(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self

    def all(self):
        return self.rows


@pytest.fixture
def case(monkeypatch):
    actor_id, import_id = uuid4(), uuid4()
    content = (
        b"Date,Description,Amount\n2026-09-25,GOOGLE,939.00\n2026-09-26,AMAZON,-46.53\n"
    )
    file_hash = digest(content)
    context = NS(
        id=uuid4(),
        version=1,
        profile="debit_negative",
        currency="MXN",
        payload_hash="c" * 64,
    )
    statement = NS(
        id=import_id,
        summary_json={"profile": "debit_negative", "currency": "MXN"},
        parser_version=svc.PARSER_VERSION,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        total_rows=2,
        imported_count=1,
        credit_count=1,
        unknown_count=0,
        skipped_count=0,
        file_sha256=file_hash,
        card_account_id=uuid4(),
        employee_id=uuid4(),
    )
    state = NS(source_version=7, progress_json={"stale": True})
    source = NS(
        id=uuid4(), content=content, file_sha256=file_hash, byte_size=len(content)
    )
    card = NS(last4="6006", active=True)
    owner = NS(id=statement.employee_id, nombre="Owner", activo=True)
    items, receipts, expenses = [], [], []
    for row in parse_direct_csv(content, "debit_negative", "MXN"):
        expense_id = uuid4() if row["movement_kind"] == "debit" else None
        receipt = NS(
            id=uuid4(),
            import_id=import_id,
            row_number=row["row_number"],
            normalized_json=_json_source(row, file_hash),
            workflow_mode="direct",
            status=row["status"],
            reason_code=row["reason"],
            imported_expense_id=expense_id,
        )
        item = NS(
            id=uuid4(),
            import_id=import_id,
            import_item_id=receipt.id,
            imported_expense_id=expense_id,
            movement_kind=row["movement_kind"],
            source_json=copy.deepcopy(receipt.normalized_json),
            raw_signed_amount=row["raw_signed_amount"],
            raw_amount=row["raw_amount"],
            amount_column=row["amount_column"],
            profile="debit_negative",
            profile_version=svc.PARSER_VERSION,
            context_id=context.id,
            context_version=1,
            source_version=2,
            source_hash="a" * 64,
            accounting_status="pending",
            budget_status="unclassified",
            match_status="pending",
            credit_excluded=False,
        )
        if expense_id:
            expenses.append(
                NS(
                    id=expense_id,
                    estado_gasto="activo",
                    origen="amex_batch",
                    empleado_id=owner.id,
                    fecha=row["date"],
                    gasto_cantidad=float(row["amount"]),
                    concepto=row["description"],
                    currency="MXN",
                    ultimos_4_digitos=card.last4,
                    pagado_con_amex_empresa=True,
                    estado_reembolso="pendiente",
                    estado_factura=None,
                )
            )
        items.append(item)
        receipts.append(receipt)
    session = NS(add=Mock(), flush=AsyncMock())

    @asynccontextmanager
    async def nested():
        yield

    session.begin_nested = nested
    existing = []

    async def execute(query, params=None):
        sql = str(query)
        if "amex_direct_sign_revisions" in sql:
            return Result(existing)
        if "amex_statement_source_files" in sql:
            return Result([source])
        if "amex_statement_import_items" in sql:
            return Result(receipts)
        if "expense_reports" in sql:
            return Result(expenses)
        if "encode(sha256" in sql:
            return Result(["b" * 64])
        raise AssertionError(sql)

    session.execute = AsyncMock(side_effect=execute)

    async def get(model, key):
        return card if model is svc.AmexCardAccount else owner

    session.get = AsyncMock(side_effect=get)
    monkeypatch.setattr(
        svc, "load_statement", AsyncMock(return_value=(statement, state, context, {}))
    )
    monkeypatch.setattr(svc, "load_items", AsyncMock(return_value=items))
    monkeypatch.setattr(svc, "require_unfrozen", AsyncMock())
    monkeypatch.setattr(svc, "_require_independent", AsyncMock())
    event = AsyncMock()
    monkeypatch.setattr(svc, "record_event", event)
    create = AsyncMock(side_effect=lambda **kw: NS(id=uuid4()))
    monkeypatch.setattr(svc, "create_expense_from_data", create)
    args = dict(
        actor_id=actor_id,
        import_id=import_id,
        profile="debit_positive",
        reason="Reviewed opposite signs",
        expected_source_version=7,
    )
    return NS(**locals())


@pytest.mark.asyncio
async def test_preview_reproduces_every_receipt_without_writes(case):
    result = await svc.preview_direct_sign_correction(case.session, **case.args)
    assert result.status == "preview"
    assert result.progress["effective_totals"] == {"debit": "939.00", "credit": "46.53"}
    assert [r["movement_kind"] for r in result.progress["rows"]] == ["debit", "credit"]
    assert result.progress["expected_source_version"] == 7
    assert "GOOGLE" not in str(result.progress)
    case.session.add.assert_not_called()
    case.session.flush.assert_not_called()
    case.create.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_uses_expense_owner_and_cancels_original_with_audit(case):
    preview = await svc.preview_direct_sign_correction(case.session, **case.args)
    result = await svc.confirm_direct_sign_correction(
        case.session,
        **case.args,
        preview_hash=preview.progress["preview_hash"],
        idempotency_key="repair",
        confirmed=True,
    )
    assert result.status == "corrected"
    assert case.state.source_version == 8 and case.state.progress_json is None
    case.create.assert_awaited_once()
    assert case.create.call_args.kwargs["gasto_cantidad"] == 939.0
    assert case.expenses[0].estado_gasto == "cancelado"
    assert case.expenses[0].cancelado_por_id == case.actor_id
    added = [call.args[0] for call in case.session.add.call_args_list]
    revisions = [x for x in added if isinstance(x, svc.AmexDirectSignRevision)]
    rows = [x for x in added if isinstance(x, svc.AmexDirectSignRow)]
    assert len(revisions) == 1 and len(rows) == 2
    assert rows[0].imported_expense_id is not None
    assert rows[1].imported_expense_id is None
    assert rows[1].original_expense_id == case.expenses[0].id
    assert any(isinstance(x, svc.Aprobacion) and x.accion == "cancelar" for x in added)
    assert [i.source_version for i in case.items] == [3, 3]
    assert case.event.await_count == 2
    assert [i.movement_kind for i in case.items] == ["credit", "debit"]
    assert case.statement.summary_json["profile"] == "debit_negative"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("source_version", 8, "source_version_conflict"),
        ("reason", "", "sign_reason_required"),
        ("reason", "x" * 2001, "sign_reason_required"),
        ("profile", "separate_columns", "sign_opposite_profile_required"),
    ],
)
async def test_input_rejects_before_writes(case, field, value, reason):
    if field == "source_version":
        case.state.source_version = value
    else:
        case.args[field] = value
    result = await svc.preview_direct_sign_correction(case.session, **case.args)
    assert result.reason == reason
    case.session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("source_hash", "sign_original_source_invalid"),
        ("parser", "sign_original_source_invalid"),
        ("size", "sign_original_source_invalid"),
        ("context", "sign_parser_context_conflict"),
        ("count", "sign_original_membership_invalid"),
        ("header_counts", "sign_original_membership_invalid"),
        ("receipt", "sign_original_row_proof_mismatch"),
        ("physical", "sign_original_row_proof_mismatch"),
        ("expense", "sign_original_expense_conflict"),
        ("credit_anchor", "sign_original_row_proof_mismatch"),
        ("inactive", "inactive_context"),
    ],
)
async def test_proof_preimage_rejects_mutations(case, mutation, reason):
    if mutation == "source_hash":
        case.source.file_sha256 = "0" * 64
    elif mutation == "parser":
        case.statement.parser_version = "other"
    elif mutation == "size":
        case.source.byte_size += 1
    elif mutation == "context":
        case.context.profile = "debit_positive"
    elif mutation == "count":
        case.statement.total_rows += 1
    elif mutation == "header_counts":
        case.statement.credit_count += 1
    elif mutation == "receipt":
        case.receipts[-1].normalized_json["description"] = "Changed"
    elif mutation == "physical":
        case.items[-1].source_json["description"] = "Changed"
    elif mutation == "expense":
        case.expenses[0].documento_id = uuid4()
    elif mutation == "credit_anchor":
        case.items[0].imported_expense_id = uuid4()
    elif mutation == "inactive":
        case.card.active = False
    result = await svc.preview_direct_sign_correction(case.session, **case.args)
    assert result.reason == reason
    case.session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("cfdi_id", uuid4()),
        ("status_receipt_id", uuid4()),
        ("no_cfdi_decision_id", uuid4()),
        ("budget_line_id", uuid4()),
        ("budget_assignment_id", uuid4()),
        ("tournament_id", uuid4()),
        ("match_status", "matched"),
        ("budget_status", "sent"),
        ("accounting_status", "recognized"),
        ("credit_excluded", True),
        ("match_proof", {"coverage_kind": "prior"}),
    ],
)
async def test_independent_flags_each_fail_closed(case, field, value):
    setattr(case.items[0], field, value)
    result = await svc.preview_direct_sign_correction(case.session, **case.args)
    assert result.reason == "sign_source_effect_conflict"
    case.create.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_hash_mismatch_never_creates_or_cancels(case):
    with pytest.raises(DirectConflict, match="sign_preview_changed"):
        await svc.confirm_direct_sign_correction(
            case.session,
            **case.args,
            preview_hash="f" * 64,
            idempotency_key="key",
            confirmed=True,
        )
    case.create.assert_not_called()
    assert case.expenses[0].estado_gasto == "activo"


@pytest.mark.asyncio
async def test_confirm_explicit_confirmation_and_replay(case):
    assert (
        await svc.confirm_direct_sign_correction(
            case.session,
            **case.args,
            preview_hash="a" * 64,
            idempotency_key="key",
            confirmed=False,
        )
    ).reason == "sign_confirmation_required"
    preview = await svc.preview_direct_sign_correction(case.session, **case.args)
    applied = await svc.confirm_direct_sign_correction(
        case.session,
        **case.args,
        preview_hash=preview.progress["preview_hash"],
        idempotency_key="key",
        confirmed=True,
    )
    revision = next(
        c.args[0]
        for c in case.session.add.call_args_list
        if isinstance(c.args[0], svc.AmexDirectSignRevision)
    )
    case.existing.append(revision)
    replay = await svc.confirm_direct_sign_correction(
        case.session,
        **case.args,
        preview_hash=preview.progress["preview_hash"],
        idempotency_key="key",
        confirmed=True,
    )
    assert replay.status == "exists"
    assert replay.progress["sign_revision_id"] == applied.progress["sign_revision_id"]
    conflict = await svc.confirm_direct_sign_correction(
        case.session,
        **case.args,
        preview_hash=preview.progress["preview_hash"],
        idempotency_key="different",
        confirmed=True,
    )
    assert conflict.reason == "sign_idempotency_conflict"
    assert (
        await svc.preview_direct_sign_correction(case.session, **case.args)
    ).status == "exists"
    case.create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency",
    [
        ("amex_recognition_representations", "expense_id", "expense_reports"),
        ("invoice_reports", "expense_id", "expense_reports"),
        ("bank_movements", "matched_expense_id", "expense_reports"),
        ("budget_actuals_bridge", "expense_report_id", "expense_reports"),
        ("amex_direct_no_cfdi_decisions", "item_id", "amex_direct_items"),
        ("amex_expense_account_settlements", "direct_item_id", "amex_direct_items"),
        ("amex_direct_coverage_revisions", "import_id", "amex_statement_imports"),
        ("amex_direct_accounting_cuts", "import_id", "amex_statement_imports"),
    ],
)
async def test_catalog_dependency_owner_blocks_even_with_pending_flags(dependency):
    table, column, target = dependency
    session = NS(
        execute=AsyncMock(
            side_effect=[
                Result(
                    [
                        dict(
                            schema_name="public",
                            table_name=table,
                            column_name=column,
                            target_name=target,
                        )
                    ]
                ),
                Result([1]),
            ]
        )
    )
    item = NS(id=uuid4(), imported_expense_id=uuid4())
    with pytest.raises(DirectConflict, match="sign_incompatible_dependency"):
        await svc._require_independent(session, uuid4(), [item])
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_catalog_allows_only_original_owners_and_checks_polymorphic_owners():
    session = NS(
        execute=AsyncMock(
            side_effect=[
                Result(
                    [
                        dict(
                            schema_name="public",
                            table_name="amex_direct_items",
                            column_name="imported_expense_id",
                            target_name="expense_reports",
                        ),
                        dict(
                            schema_name="public",
                            table_name="invoice_reports",
                            column_name="expense_id",
                            target_name="expense_reports",
                        ),
                    ]
                ),
                Result(),
            ]
        )
    )
    await svc._require_independent(
        session, uuid4(), [NS(id=uuid4(), imported_expense_id=None)]
    )
    assert session.execute.await_count == 2
    sql = str(session.execute.call_args.args[0])
    assert "budget_movement_assignments" in sql and "accounting_poliza_lines" in sql
    assert (
        "aprobaciones" in sql
        and "ev.action NOT IN ('context_revision','match_pending')" in sql
    )
    catalog = str(session.execute.call_args_list[0].args[0])
    assert "ta.attname='id'" in catalog and "unnest(fk.conkey, fk.confkey)" in catalog


@pytest.mark.asyncio
async def test_polymorphic_dependency_blocks_without_fk():
    session = NS(execute=AsyncMock(side_effect=[Result(), Result([1])]))
    with pytest.raises(DirectConflict, match="sign_incompatible_dependency"):
        await svc._require_independent(
            session, uuid4(), [NS(id=uuid4(), imported_expense_id=uuid4())]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,allowed",
    [
        ("context_revision", True),
        ("match_pending", True),
        ("match_confirmed", False),
        ("expense_account_source_selected", False),
        ("match_release", False),
        ("budget_release", False),
        ("no_cfdi_revoke", False),
        ("arbitrary_action", False),
    ],
)
async def test_only_nonbinding_history_allows_sign_preview(action, allowed):
    """Execute the actual event predicate, rather than mirroring its whitelist.

    SQLite supplies standard SQL NOT IN semantics without external services.
    The FK catalog is empty in this isolated owner-boundary case; all the
    separate source-state/financial dependency tests remain in force.
    """
    import sqlite3

    async def execute(query, params=None):
        sql = str(query)
        if "pg_constraint" in sql:
            return Result()
        predicate = sql.split("AND ev.action", 1)[1].split("UNION ALL", 1)[0]
        with sqlite3.connect(":memory:") as connection:
            hit = connection.execute(
                "SELECT 1 WHERE :action " + predicate,
                {"action": action},
            ).fetchone()
        return Result([hit] if hit else [])

    session = NS(execute=AsyncMock(side_effect=execute))
    call = svc._require_independent(
        session, uuid4(), [NS(id=uuid4(), imported_expense_id=uuid4())]
    )
    if allowed:
        await call
    else:
        with pytest.raises(DirectConflict, match="sign_incompatible_dependency"):
            await call
