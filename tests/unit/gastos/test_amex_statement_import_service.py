"""Parser compatibility and safe omission receipts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_statement_import_service as service
from devnous.gastos.services.amex_statement_import_service import (
    StatementValidationError,
    parse_amex_csv,
)


@pytest.mark.parametrize(
    "date",
    [
        "26 Nov 2025",
        "26 November 2025",
        "2025-11-26",
        "26/11/2025",
        "11/26/2025",
        "2025/11/26",
        "26-11-2025",
        "11-26-2025",
    ],
)
def test_legacy_date_and_absolute_amount(date):
    rows = parse_amex_csv(
        f"Date,Description,Amount\n{date},Merchant,-229.98\n".encode()
    )
    assert rows[0]["amount"] == 229.98
    assert rows[0]["fecha"].isoformat() == "2025-11-26T00:00:00"
    assert rows[0]["row_number"] == 2


@pytest.mark.parametrize(
    "row,code",
    [
        (" ,merchant,1", "missing_date"),
        ("bad,merchant,1", "invalid_date"),
        ("2026-10-01,,1", "missing_description"),
        ("2026-10-01,merchant,", "missing_amount"),
        ("2026-10-01,merchant,zero", "invalid_amount"),
        ("2026-10-01,merchant,0", "invalid_amount"),
        ("2026-10-01,merchant,NaN", "invalid_amount"),
        ("2026-10-01,merchant,Infinity", "invalid_amount"),
    ],
)
def test_skips_do_not_persist_raw_data(row, code):
    receipt = parse_amex_csv(("Date,Description,Amount\n" + row + "\n").encode())[0]
    assert receipt["reason_code"] == code
    assert receipt["normalized_json"] == {}


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "missing_headers"),
        (b"foo,bar\nx,y", "missing_columns"),
        (b"\xff", "invalid_utf8"),
    ],
)
def test_invalid_file(content, code):
    with pytest.raises(StatementValidationError, match=code):
        parse_amex_csv(content)


def test_monthly_restriction_uses_original_date_even_if_description_invalid():
    content = b"Date,Description,Amount\n2026-09-30,,1\n2026-10-01,merchant,1\n"
    assert len(parse_amex_csv(content)) == 2
    with pytest.raises(StatementValidationError, match="monthly_cfdi_multiple_months"):
        parse_amex_csv(content, "uuid")


def test_bom_and_spanish_columns():
    rows = parse_amex_csv(
        (
            '\ufeffFecha de compra,Descripción,Importe\n2026-10-01,Café,"$1,2'
            '00.01"\n'
        ).encode()
    )
    assert rows[0]["amount"] == 1200.01


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tip,confidence,counter",
    [
        (0, "exact", "auto_cfdi_match_count"),
        (12, "tip", "auto_tip_match_count"),
        (0, "pase_monthly", "auto_pase_match_count"),
    ],
)
async def test_shared_helper_preserves_existing_matcher_semantics(
    monkeypatch, tip, confidence, counter
):
    expense = SimpleNamespace(id=uuid4())
    match = SimpleNamespace(
        cfdi=SimpleNamespace(id=uuid4(), cfdi_uuid=" lower-case "),
        inferred_tip=tip,
        confidence=confidence,
    )
    create = AsyncMock(return_value=expense)
    finder = AsyncMock(return_value=match)
    monkeypatch.setattr(service, "create_expense_from_data", create)
    monkeypatch.setattr(service, "find_amex_cfdi_match", finder)
    session = SimpleNamespace(
        add=Mock(), flush=AsyncMock(), commit=AsyncMock(), execute=AsyncMock()
    )
    employee = SimpleNamespace(id=uuid4(), nombre="Owner")
    card = SimpleNamespace(last4="5007")
    result = await service.import_amex_csv_rows(
        session,
        employee,
        card,
        b"Date,Description,Amount\n2026-10-01,Merchant,100\ninvalid,,x\n",
    )
    assert result[counter] == 1
    assert result["created_count"] == 1 and result["skipped_count"] == 1
    assert expense.cfdi_report_id == match.cfdi.id
    assert expense.cfdi_uuid_manual == "LOWER-CASE"
    assert expense.pagado_con_amex_empresa
    assert create.await_args.kwargs["origen"] == "amex_batch"
    assert finder.await_args.kwargs["charge_amount"] == 100
    session.commit.assert_not_awaited()
    if tip:
        assert expense.propina_no_deducible == 12


@pytest.mark.asyncio
async def test_shared_helper_monthly_without_matcher_and_no_commit(monkeypatch):
    expense = SimpleNamespace(id=uuid4())
    monthly = str(uuid4()).upper()
    monkeypatch.setattr(
        service, "create_expense_from_data", AsyncMock(return_value=expense)
    )
    finder = AsyncMock()
    monkeypatch.setattr(service, "find_amex_cfdi_match", finder)
    report = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: report)
        ),
    )
    result = await service.import_amex_csv_rows(
        session,
        SimpleNamespace(id=uuid4(), nombre="Owner"),
        SimpleNamespace(last4="5007"),
        b"Date,Description,Amount\n2026-10-01,Merchant,100\n",
        monthly,
    )
    assert expense.cfdi_uuid_manual == monthly and expense.cfdi_report_id == report.id
    assert result["cfdi_monthly_linked"]
    finder.assert_not_awaited()
    session.commit.assert_not_awaited()
