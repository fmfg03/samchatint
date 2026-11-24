"""Shared snapshots keep filters and attributed financial facts honest."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive.dossier_view import read_dossier_view


@pytest.mark.asyncio
async def test_cut_reads_only_the_recorded_roster():
    sources = AsyncMock()
    captures = AsyncMock()
    registry = AsyncMock(
        return_value={
            "directory": {"rows": [{"representative_name": "Contacto actual"}]},
            "team_roster": {
                "status": "available",
                "rows": [{"label": "Equipo al corte"}],
            },
            "entities": ["Oaxaca"],
        }
    )
    result = await read_dossier_view(
        object(),
        tournament={"id": str(uuid4())},
        edition_year=2026,
        domains={"operations", "finance"},
        years=[2026],
        cut_date=date(2026, 11, 18),
        record_reader=captures,
        source_reader=sources,
        registry_reader=registry,
    )
    assert result["edition_verified"]
    assert "directory" not in result["sources"]
    assert result["sources"]["team_roster"]["rows"][0]["label"] == "Equipo al corte"
    assert result["captures"] == {"status": "not_applicable", "rows": []}
    sources.assert_not_awaited()
    captures.assert_not_awaited()


@pytest.mark.asyncio
async def test_finance_view_reuses_explicit_attributions_without_duplicating_totals():
    project, paid, report, line, wrong, unlinked = (str(uuid4()) for _ in range(6))
    finance = AsyncMock(
        return_value={
            "finance": {
                "status": "available",
                "rows": [
                    {
                        "id": paid,
                        "edition_year": 2026,
                        "phase": "nacional",
                        "amount_paid": "126.00",
                    },
                    {
                        "id": report,
                        "edition_year": 2026,
                        "phase": "nacional",
                        "amount_document": "500.00",
                    },
                    {
                        "id": wrong,
                        "edition_year": 2027,
                        "phase": "nacional",
                        "amount_paid": "900.00",
                    },
                    {
                        "id": unlinked,
                        "edition_year": None,
                        "phase": "nacional",
                        "amount_document": "100.00",
                    },
                ],
            }
        }
    )
    data = AsyncMock(
        return_value={
            "status": "available",
            "rows": [
                {
                    "kind": "financial_link",
                    "entity": "Oaxaca",
                    "phase": "nacional",
                    "category": "Juvenil",
                    "fields": {
                        "document_id": paid,
                        "concept": "hotel_anticipo",
                        "service_type": "Hospedaje",
                    },
                },
                {
                    "kind": "expense_line_link",
                    "entity": "Oaxaca",
                    "phase": "nacional",
                    "category": "Juvenil",
                    "fields": {
                        "expense_id": line,
                        "source_document_id": report,
                        "concept": "hotel_liquidacion",
                        "service_type": "Salón",
                    },
                },
            ],
        }
    )
    expenses = AsyncMock(
        return_value={
            "status": "available",
            "rows": [
                {
                    "id": line,
                    "document_id": report,
                    "amount": "80.00",
                    "currency": "MXN",
                    "expense_date": "2027-01-05",
                    "updated_at": None,
                    "source": "Gastos",
                    "expense_phase": "Fase Nacional",
                    "document_phase": "Nacional",
                    "expense_categories": ["Juvenil"],
                    "document_categories": ["Juvenil"],
                }
            ],
        }
    )
    registry = AsyncMock(
        return_value={
            "directory": {"status": "available", "rows": []},
            "entities": ["Oaxaca", "Puebla"],
        }
    )
    result = await read_dossier_view(
        object(),
        tournament={"id": project},
        edition_year=2026,
        domains={"operations", "finance"},
        years=[2026],
        record_reader=data,
        source_reader=finance,
        registry_reader=registry,
        expense_reader=expenses,
    )
    documents = result["resolved_finance_documents"]
    assert [row["id"] for row in documents] == [paid]
    assert documents[0]["entity"] == "Oaxaca"
    assert documents[0]["service_type"] == "Hospedaje"
    line_data = result["sources"]["finance_lines"]["rows"][0]
    assert line_data["expense_date"] == "2027-01-05"
    assert line_data["amount"] == "80.00"
    assert line_data["service_type"] == "Salón"
    assert "amount_paid" not in line_data
    assert result["sources"]["entities"] == ["Oaxaca", "Puebla"]
    assert expenses.await_args.kwargs["expense_ids"] == (line,)
    assert finance.await_args.kwargs["linked_document_ids"] == (paid,)
    assert result["sources"]["finance_lines"]["status"] == "available"

    rows = expenses.return_value["rows"]
    rows.append({**rows[0], "id": str(uuid4())})
    result = await read_dossier_view(
        object(),
        tournament={"id": project},
        edition_year=2026,
        domains={"operations", "finance"},
        years=[2026],
        record_reader=data,
        source_reader=finance,
        registry_reader=registry,
        expense_reader=expenses,
    )
    assert result["sources"]["finance_lines"]["status"] == "partial"
    assert len(result["sources"]["finance_lines"]["rows"]) == 1

    # The expense owner can move a line to another report without carrying
    # the old executive attribution onto the new parent.
    rows[0]["document_id"] = paid
    result = await read_dossier_view(
        object(),
        tournament={"id": project},
        edition_year=2026,
        domains={"operations", "finance"},
        years=[2026],
        record_reader=data,
        source_reader=finance,
        registry_reader=registry,
        expense_reader=expenses,
    )
    assert result["sources"]["finance_lines"]["rows"] == []
    assert result["sources"]["finance_lines"]["status"] == "partial"


@pytest.mark.asyncio
async def test_directory_and_expense_read_respect_separate_domain_permissions():
    registry = AsyncMock(
        return_value={"directory": {"status": "available", "rows": []}}
    )
    expense = AsyncMock()
    source = AsyncMock(return_value={})
    result = await read_dossier_view(
        object(),
        tournament={"id": str(uuid4())},
        edition_year=2027,
        domains={"operations"},
        years=[],
        directory_contacts_allowed=True,
        record_reader=AsyncMock(return_value={"status": "available", "rows": []}),
        source_reader=source,
        registry_reader=registry,
        expense_reader=expense,
    )
    assert not result["edition_verified"]
    assert registry.await_args.kwargs["contacts_allowed"]
    assert not source.await_args.kwargs["contacts_allowed"]
    assert not source.await_args.kwargs["finance_allowed"]
    expense.assert_not_awaited()


def test_operator_aid_order_uses_only_transfer_evidence_and_observed_dates():
    from samchat.client_executive.dossier_view import _label_operator_transfers

    rows = [
        {
            "entity": "Oaxaca",
            "dossier_concept": "ayuda_operador",
            "effective_payment_date": "2026-01-12",
            "payment_method": "Transferencia",
            "proof_available": True,
        },
        {
            "entity": "Oaxaca",
            "dossier_concept": "ayuda_operador",
            "effective_payment_date": "2026-01-06",
            "payment_method": "SPEI",
            "proof_available": True,
        },
        {
            "entity": "Oaxaca",
            "dossier_concept": "ayuda_operador",
            "effective_payment_date": "2026-01-01",
            "payment_method": "Transferencia",
            "proof_available": False,
        },
        {
            "entity": "Puebla",
            "dossier_concept": "ayuda_operador",
            "effective_payment_date": "2026-01-10",
            "payment_method": "Efectivo",
            "proof_available": True,
        },
    ]
    _label_operator_transfers(rows, full_source=False)
    assert all("aid_payment_stage" not in row for row in rows)
    _label_operator_transfers(rows, full_source=True)
    assert rows[0]["aid_payment_stage"].startswith("Transferencia posterior")
    assert rows[1]["aid_payment_stage"].startswith("Primera fecha vinculada")
    assert all("aid_payment_stage" not in row for row in rows[2:])
