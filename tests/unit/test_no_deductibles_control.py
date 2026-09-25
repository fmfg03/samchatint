from samchat.finance_platform.no_deductibles import (
    build_no_deductibles_report,
    has_linked_fiscal_invoice,
    period_bounds,
)
from samchat.finance_platform.no_deductibles_exporter import generate_no_deductibles_xlsx


def test_linked_canonical_cfdi_is_the_only_fiscal_evidence_for_control():
    assert has_linked_fiscal_invoice({"cfdi_report_id": "cfdi-1"}) is True
    assert has_linked_fiscal_invoice({"cfdi_uuid_manual": "typed-but-unlinked"}) is False
    assert has_linked_fiscal_invoice({"cfdi_report_id": ""}) is False


def test_report_marks_missing_cfdi_as_no_deducible_and_preserves_audit_detail():
    report = build_no_deductibles_report(
        [
            {"id": "a", "amount": 1250.50, "cfdi_report_id": "cfdi-a"},
            {"id": "b", "amount": 300, "cfdi_uuid_manual": "not-evidence-yet"},
        ],
        year=2026,
        month=9,
        tournament_id="torneo-1",
    )

    assert report["summary"] == {
        "expense_count": 2,
        "deductible_count": 1,
        "non_deductible_count": 1,
        "total_amount": 1550.5,
        "deductible_amount": 1250.5,
        "non_deductible_amount": 300.0,
        "non_deductible_percent": 19.35,
    }
    assert report["non_deductible_rows"][0]["id"] == "b"
    assert report["non_deductible_rows"][0]["fiscal_reason"] == "Sin factura fiscal (CFDI) vinculada"


def test_period_uses_expense_date_calendar_month():
    start, end = period_bounds(2026, 12)
    assert start.isoformat() == "2026-12-01T00:00:00"
    assert end.isoformat() == "2027-01-01T00:00:00"


def test_xlsx_contains_only_non_deductible_detail_rows():
    report = build_no_deductibles_report(
        [
            {"id": "ded", "amount": 10, "cfdi_report_id": "cfdi-ded"},
            {
                "id": "no-ded",
                "amount": 20,
                "expense_date": "2026-09-03T00:00:00",
                "tournament_name": "Morelos",
                "reference": "G-1",
            },
        ],
        year=2026,
        month=9,
        tournament_id=None,
    )
    payload = generate_no_deductibles_xlsx(report)

    assert payload[:2] == b"PK"
    assert len(payload) > 1000
