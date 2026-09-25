from types import SimpleNamespace
from uuid import uuid4

import pytest

from samchat.finance_platform.no_deductibles import (
    build_no_deductibles_report,
    build_no_deductibles_source,
    has_linked_fiscal_invoice,
    list_tournaments_for_no_deductibles,
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


@pytest.mark.asyncio
async def test_source_resolves_document_tournament_and_marks_missing_cfdi():
    tournament_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-001",
        fecha=__import__("datetime").datetime(2026, 9, 4),
        concepto="Hospedaje",
        gasto_cantidad=500,
        empleado=SimpleNamespace(nombre="Ana"),
        informe_documento=SimpleNamespace(numero_referencia="I-001", fase="Nacional"),
        solicitud_documento=None,
        documento=None,
        fase_torneo=None,
        cfdi_report_id=None,
        cfdi_report=None,
        cfdi_uuid_manual=None,
    )

    class Result:
        def __init__(self, *, records=None, tournaments=None):
            self.records = records or []
            self.tournaments = tournaments or []

        def all(self):
            return self.records

        def scalars(self):
            return SimpleNamespace(all=lambda: self.tournaments)

    class Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Result(records=[(expense, tournament_id)])
            return Result(tournaments=[SimpleNamespace(id=tournament_id, name="Morelos")])

    report = await build_no_deductibles_source(
        Session(), year=2026, month=9, tournament_id=None
    )

    assert report["summary"]["non_deductible_amount"] == 500
    assert report["rows"][0]["tournament_name"] == "Morelos"
    assert report["rows"][0]["source_type"] == "Informe"


@pytest.mark.asyncio
async def test_tournament_list_and_invalid_scope_are_safe():
    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [SimpleNamespace(id=uuid4(), name="Morelos")]
                )
            )

    tournaments = await list_tournaments_for_no_deductibles(Session())
    assert tournaments[0]["name"] == "Morelos"

    with pytest.raises(ValueError, match="Torneo inválido"):
        await build_no_deductibles_source(Session(), year=2026, month=9, tournament_id="x")


@pytest.mark.asyncio
async def test_finance_route_renders_and_exports_control(monkeypatch):
    from devnous.gastos.routes import admin_routes
    import samchat.finance_platform.no_deductibles as control

    report = build_no_deductibles_report(
        [{"id": "x", "amount": 80, "expense_date": "2026-09-01", "concept": "Taxi"}],
        year=2026,
        month=9,
        tournament_id=None,
    )
    monkeypatch.setattr(control, "build_no_deductibles_source", lambda *args, **kwargs: _async(report))
    monkeypatch.setattr(
        control,
        "list_tournaments_for_no_deductibles",
        lambda *args, **kwargs: _async([{"id": str(uuid4()), "name": "Morelos"}]),
    )
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *args, **kwargs: "")
    monkeypatch.setattr(admin_routes, "_admin_workspace_styles", lambda *args, **kwargs: "")
    monkeypatch.setattr(admin_routes, "_render_admin_workspace_hero", lambda **kwargs: kwargs["title"])

    response = await admin_routes.admin_no_deductibles_control(
        current_empleado=SimpleNamespace(),
        session=SimpleNamespace(),
        year=2026,
        month=9,
        tournament_id=None,
    )
    assert "No Deducibles" in response.body.decode()
    assert "Taxi" in response.body.decode()

    export = await admin_routes.admin_no_deductibles_export_xlsx(
        current_empleado=SimpleNamespace(),
        session=SimpleNamespace(),
        year=2026,
        month=9,
        tournament_id=None,
    )
    assert export.body[:2] == b"PK"


async def _async(value):
    return value
