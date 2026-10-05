"""Frozen AMEX cuts export one policy with exact expense/partner movements."""

import copy
import csv
import io
import zipfile
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from devnous.gastos.services.amex_cut_export_service import cut_expense_cfdis
from devnous.gastos.services.coi_poliza_exporter import (
    build_coi_poliza_rows,
    generate_coi_poliza_csv,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)


def frozen_cut(adjustment=False):
    def line(account, debit=0, credit=0):
        return dict(
            cuenta_contable_id=str(uuid4()),
            cuenta_codigo=account,
            concepto="Evidence frozen",
            debe=str(debit),
            haber=str(credit),
            raw_row_json={},
        )

    cfdi = dict(
        uuid="11111111-1111-1111-1111-111111111111",
        date="2026-09-15T12:00:00",
        rfc_emisor="AAA010101AAA",
        rfc_receptor="BBB010101BBB",
        folio="F1",
        nombre_emisor="Provider",
    )
    if adjustment:
        journals = [
            [
                line("1170-002-004", 116),
                line("5300-001-001", 0, 100),
                line("1180-001-001", 0, 16),
            ],
            [
                line("1170-002-005", 232),
                line("5300-001-001", 0, 200),
                line("1180-001-001", 0, 32),
            ],
        ]
    else:
        journals = [
            [
                line("5300-001-001", 100),
                line("1180-001-001", 16),
                line("2120-002-062", 0, 116),
            ],
            [line("1170-002-004", 232), line("2120-002-063", 0, 232)],
        ]
    items = [
        dict(
            expense_id=str(uuid4()),
            amount=str(amount),
            reference=f"G-{i}",
            concepto="Automatically classified expense",
            treatment=treatment,
            cfdi=copy.deepcopy(cfdi),
            journal_lines=journal,
        )
        for i, (amount, treatment, journal) in enumerate(
            zip([116, 232], ["expense", "partner_receivable"], journals)
        )
    ]
    return SimpleNamespace(
        id=uuid4(),
        informe_id=uuid4(),
        kind="adjustment" if adjustment else "initial",
        accounting_date=date(2026, 9, 30),
        snapshot_json=dict(
            partidas=items,
            lines=copy.deepcopy([r for item in items for r in item["journal_lines"]]),
            informe_reference="I-1",
        ),
    )


def assert_rows(rows, cut):
    assert sum(row[0] == "Eg" for row in rows) == 1
    assert sum(row[1] == "FIN_PARTIDAS" for row in rows) == 1
    actual = [
        (row[1], Decimal(str(row[5] or 0)), Decimal(str(row[6] or 0)))
        for row in rows
        if row[1] and str(row[1])[0].isdigit() and "-" in str(row[1])
    ]
    expected = [
        (r["cuenta_codigo"], Decimal(r["debe"]), Decimal(r["haber"]))
        for r in cut.snapshot_json["lines"]
    ]
    assert actual == expected
    assert sum(r[1] for r in actual) == sum(r[2] for r in actual)
    assert sum(row[2] == "INICIO_CFDI" for row in rows) == 2
    if cut.kind == "adjustment":
        assert all(not r[0].startswith("2120") for r in actual)


@pytest.mark.parametrize("adjustment", [False, True])
@pytest.mark.parametrize("format", ["xlsx", "csv", "zip"])
def test_real_export_formats_one_header_exact_frozen_lines_and_shared_cfdi(
    adjustment, format
):
    cut = frozen_cut(adjustment)
    before = copy.deepcopy(cut.snapshot_json)
    items = cut_expense_cfdis(cut)
    assert items[1].iva_amount == 0
    assert items[1].cfdi_uuid == items[0].cfdi_uuid
    assert cut.snapshot_json == before
    if format == "csv":
        rows = list(
            csv.reader(io.StringIO(generate_coi_poliza_csv(items).decode("latin-1")))
        )
    else:
        content = (
            generate_coi_poliza_xlsx(items)
            if format == "xlsx"
            else generate_coi_poliza_zip(items)
        )
        if format == "zip":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                assert len(archive.namelist()) == 1
                content = archive.read(archive.namelist()[0])
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        rows = [
            [v if v is not None else "" for v in row] for row in workbook.active.values
        ]
    assert_rows(rows, cut)


@pytest.mark.parametrize(
    "corruption", ["missing", "mismatch", "negative", "blank", "unbalanced"]
)
def test_corrupt_snapshot_fails_closed(corruption):
    cut = frozen_cut()
    if corruption == "missing":
        cut.snapshot_json["lines"] = []
    elif corruption == "mismatch":
        cut.snapshot_json["lines"][0]["debe"] = "101"
    else:
        row = cut.snapshot_json["partidas"][0]["journal_lines"][0]
        if corruption == "negative":
            row["debe"] = "-100"
        elif corruption == "blank":
            row["cuenta_codigo"] = ""
        else:
            row["debe"] = "101"
        cut.snapshot_json["lines"] = copy.deepcopy(
            [r for i in cut.snapshot_json["partidas"] for r in i["journal_lines"]]
        )
    with pytest.raises(ValueError):
        cut_expense_cfdis(cut)


def test_global_metadata_does_not_change_frozen_journal():
    cut = frozen_cut()
    for row in cut.snapshot_json["lines"]:
        row["raw_row_json"]["cut_id"] = str(cut.id)
    assert_rows(build_coi_poliza_rows(cut_expense_cfdis(cut)), cut)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_cut", [False, True])
async def test_document_bundle_amex_uses_cut_or_blocks_without_mutable_builder(
    monkeypatch, has_cut
):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from devnous.gastos.routes import user_routes

    cut = frozen_cut()
    actor = SimpleNamespace(id=uuid4(), rol="finanzas")
    document = SimpleNamespace(
        id=cut.informe_id,
        tipo="INFORME",
        estado="aprobado",
        empleado_id=actor.id,
        cuenta_gastos_id=uuid4(),
    )
    expense = SimpleNamespace(id=uuid4())
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: document),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [expense])),
    ]
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda exp: True)
    monkeypatch.setattr(
        user_routes,
        "_load_initial_amex_cut",
        AsyncMock(return_value=cut if has_cut else None),
    )
    builder = AsyncMock(side_effect=AssertionError("mutable builder must not run"))
    monkeypatch.setattr(user_routes, "build_expense_cfdi_for_export", builder)
    if has_cut:
        result = await user_routes._build_documento_coi_bundle(
            document.id, session, actor, require_complete_informe=True
        )
        assert_rows(build_coi_poliza_rows(result[2]), cut)
    else:
        with pytest.raises(HTTPException) as exc:
            await user_routes._build_documento_coi_bundle(
                document.id, session, actor, require_complete_informe=True
            )
        assert exc.value.status_code == 400
    builder.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_cut", [False, True])
async def test_monthly_collector_amex_snapshot_and_cut_required(monkeypatch, has_cut):
    from unittest.mock import AsyncMock

    from devnous.gastos.routes import user_routes

    cut = frozen_cut()
    document = SimpleNamespace(id=cut.informe_id, numero_referencia="I-1")
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda exp: True)
    monkeypatch.setattr(
        user_routes,
        "_load_initial_amex_cut",
        AsyncMock(return_value=cut if has_cut else None),
    )
    builder = AsyncMock(
        side_effect=AssertionError("mutable classification not export owner")
    )
    monkeypatch.setattr(user_routes, "build_expense_cfdi_for_export", builder)
    rows = [dict(documento=document, tipo_lote="INFORME", expenses=[SimpleNamespace()])]
    if has_cut:
        result = await user_routes._collect_coi_lote_expense_cfdis(AsyncMock(), rows)
        assert_rows(build_coi_poliza_rows(result[0]), cut)
        assert result[2] == 1 and len(result[3]) == 2
        assert all(f"corte:{cut.id}" == row[-1] for row in result[1][1:])
    else:
        with pytest.raises(ValueError, match="corte contable"):
            await user_routes._collect_coi_lote_expense_cfdis(AsyncMock(), rows)
    builder.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_imported_amex_expense_cannot_bypass_cut(monkeypatch):
    from unittest.mock import AsyncMock

    from devnous.gastos.routes import user_routes

    actor = SimpleNamespace(id=uuid4(), rol="finanzas")
    expense = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor.id,
        informe_documento_id=None,
        documento_id=None,
        cuenta_gastos_id=None,
    )
    monkeypatch.setattr(
        user_routes, "load_expense_for_coi_export", AsyncMock(return_value=expense)
    )
    monkeypatch.setattr(user_routes, "is_company_amex_expense", lambda exp: True)
    builder = AsyncMock()
    monkeypatch.setattr(user_routes, "build_expense_cfdi_for_export", builder)
    response = await user_routes.exportar_coi_poliza_gasto_excel(
        expense.id, AsyncMock(), actor
    )
    assert response.status_code == 303
    assert "amex_accounting_cut_required" in response.headers["location"]
    builder.assert_not_awaited()


@pytest.mark.parametrize(
    "broken_snapshot",
    [
        None,
        {"partidas": [None], "lines": [{}]},
        {"partidas": [{"journal_lines": []}], "lines": [{}]},
    ],
)
def test_malformed_evidence_becomes_business_validation_error(broken_snapshot):
    cut = frozen_cut()
    cut.snapshot_json = broken_snapshot
    with pytest.raises(ValueError):
        cut_expense_cfdis(cut)


def test_unchanged_partida_in_adjustment_does_not_create_extra_movement():
    cut = frozen_cut(True)
    cut.snapshot_json["partidas"].append(
        {"expense_id": str(uuid4()), "journal_lines": []}
    )
    assert len(cut_expense_cfdis(cut)) == 2
    assert_rows(build_coi_poliza_rows(cut_expense_cfdis(cut)), cut)
