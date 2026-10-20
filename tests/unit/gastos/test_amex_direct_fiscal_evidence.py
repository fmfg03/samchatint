"""Real canonical XML matrices, without SAT or a business database."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_fiscal_evidence_service as fiscal
from devnous.gastos.services import expense_accounting_service as accounting
from devnous.gastos.services.amex_direct_common import configuration_for
from devnous.gastos.services.cfdi_parser import parse_cfdi_xml

ISSUER = "SUN010101AB1"
RECEIVER = "PSP1705058S4"
INVOICE_UUID = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"


def invoice_xml(*, method="PUE", kind="taxed", receiver=RECEIVER):
    no_object = kind == "no_object"
    exempt = kind == "exempt"
    zero = kind == "zero"
    total = "100" if no_object or exempt or zero else "116"
    tax = (
        '<cfdi:Traslado Base="100" Impuesto="002" TipoFactor="Exento"/>'
        if exempt
        else f'<cfdi:Traslado Base="100" Impuesto="002" TipoFactor="Tasa" TasaOCuota="{0 if zero else "0.160000"}" Importe="{0 if zero else 16}"/>'
    )
    tax_groups = (
        f"<cfdi:Impuestos><cfdi:Traslados>{tax}</cfdi:Traslados></cfdi:Impuestos>"
    )
    return f"""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0" Fecha="2026-10-06T12:00:00" SubTotal="100" Total="{total}" Moneda="MXN" TipoDeComprobante="I" MetodoPago="{method}">
    <cfdi:Emisor Rfc="{ISSUER}" Nombre="SUNTORY"/><cfdi:Receptor Rfc="{receiver}"/>
    <cfdi:Conceptos><cfdi:Concepto Importe="100" ObjetoImp="{'01' if no_object else '02'}">{'' if no_object else tax_groups}</cfdi:Concepto></cfdi:Conceptos>
    {'' if no_object else tax_groups}
    <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{INVOICE_UUID}"/></cfdi:Complemento></cfdi:Comprobante>"""


def invoice(**kwargs):
    xml = invoice_xml(**kwargs)
    return SimpleNamespace(
        **parse_cfdi_xml(xml), id=uuid4(), xml_parsed=True, origen="sat"
    )


def receipt(*, related=INVOICE_UUID, receiver=RECEIVER, issuer=ISSUER):
    xml = f"""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:pago20="http://www.sat.gob.mx/Pagos20" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0" Total="0" SubTotal="0" TipoDeComprobante="P" Moneda="XXX">
    <cfdi:Emisor Rfc="{issuer}"/><cfdi:Receptor Rfc="{receiver}"/>
    <cfdi:Complemento><pago20:Pagos><pago20:Pago FechaPago="2026-09-29T12:00:00"><pago20:DoctoRelacionado IdDocumento="{related}"/></pago20:Pago></pago20:Pagos>
    <tfd:TimbreFiscalDigital UUID="BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"/></cfdi:Complemento></cfdi:Comprobante>"""
    return SimpleNamespace(
        **parse_cfdi_xml(xml), id=uuid4(), xml_parsed=True, origen="sat"
    )


def session(rows=()):
    return SimpleNamespace(
        no_autoflush=nullcontext(),
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: list(rows))
            )
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("card", ["1005", "1100", "1600", "5007", "5400"])
@pytest.mark.parametrize("method", ["PUE", "PPD"])
@pytest.mark.parametrize("rep_state", ["present", "absent", "invalid"])
async def test_all_card_payment_method_rep_matrix(card, method, rep_state, monkeypatch):
    card_ids = {
        last_four: uuid4() for last_four in ["1005", "1100", "1600", "5007", "5400"]
    }
    monkeypatch.setenv(
        "AMEX_DIRECT_AUTHORIZED_CONTEXTS",
        json.dumps(
            [
                {
                    "entity_code": "configured_entity",
                    "company_code": "configured_company",
                    "receiver_rfc": RECEIVER,
                    "card_account_ids": [str(value) for value in card_ids.values()],
                    "actor_scopes": [],
                }
            ]
        ),
    )
    config = configuration_for(card_ids[card])
    assert config is not None
    cfdi = invoice(method=method)
    rep = receipt()
    if rep_state == "invalid":
        rep.xml_raw = "not xml"
    db = session([rep] if rep_state != "absent" else [])
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        db,
        cfdi,
        receiver_rfc=config["receiver_rfc"],
        company_code=config["company_code"],
    )
    assert result["payment_method"] == method
    assert (
        result["complement_status"]
        == {"present": "available", "absent": "not_local", "invalid": "not_verifiable"}[
            rep_state
        ]
    )
    assert result["policy"]["rep_required"] is False
    assert result["proof_hash"]
    canonical = fiscal.strict_direct_cfdi(cfdi)
    assert (
        accounting.summarize_cfdi_tax_components(canonical, allow_estimated_iva=False)[
            "iva_trasladado"
        ]
        == 16
    )
    assert db.execute.await_count == 1
    assert result["policy_applies"] is True


@pytest.mark.asyncio
async def test_batch_queries_once_and_never_reserves_or_downloads():
    cfdis = [invoice() for _ in range(25)]
    db = session([receipt()])
    result = await fiscal.read_direct_cfdi_fiscal_evidence_batch(
        db, cfdis, receiver_rfc=RECEIVER
    )
    assert len(result) == 25
    assert db.execute.await_count == 1
    sql = str(db.execute.call_args.args[0])
    assert sql.startswith("SELECT") and "FOR UPDATE" not in sql
    assert "receptor_rfc" in sql and "emisor_rfc" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["uuid", "receiver", "issuer", "namespace"])
async def test_rep_requires_exact_xml_relation_and_entity(change):
    rep = receipt(
        related=(
            "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC" if change == "uuid" else INVOICE_UUID
        ),
        receiver="OTR010101AB1" if change == "receiver" else RECEIVER,
        issuer="OTR010101AB1" if change == "issuer" else ISSUER,
    )
    if change == "namespace":
        rep.xml_raw = rep.xml_raw.replace(
            "http://www.sat.gob.mx/Pagos20", "http://example.invalid/"
        )
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        session([rep]), invoice(), receiver_rfc=RECEIVER
    )
    assert result["complement_status"] != "available"


@pytest.mark.asyncio
async def test_other_entity_does_not_inherit_policy_and_raw_receiver_mismatch_is_not_verifiable():
    other = "OTR010101AB1"
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        session(), invoice(receiver=other), receiver_rfc=other
    )
    assert result["policy"]["version"] is None
    assert result["policy"]["rep_required"] is None
    db = session()
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        db, invoice(), receiver_rfc=other
    )
    assert result["complement_status"] == "not_verifiable"
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,stored", [("", ""), ("PUE", "PPD"), ("OTHER", "OTHER")]
)
async def test_missing_or_contradictory_method_is_pending(method, stored):
    cfdi = invoice(method=method)
    cfdi.metodo_pago = stored
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        session(), cfdi, receiver_rfc=RECEIVER
    )
    assert result["payment_method"] is None
    assert result["payment_method_status"] == "pending"


@pytest.mark.parametrize("kind", ["no_object", "zero", "exempt"])
def test_legitimate_zero_tax_evidence_ignores_expense_fallback(kind):
    canonical = fiscal.strict_direct_cfdi(invoice(kind=kind))
    taxes = accounting.summarize_expense_cfdi_tax_components(
        SimpleNamespace(iva=999), canonical, strict_direct=True
    )
    assert taxes["iva_trasladado"] == 0


@pytest.mark.parametrize(
    "alteration",
    [
        "missing_concept",
        "missing_global",
        "bad_amount",
        "missing_amount",
        "bad_total",
        "invalid_xml",
        "unparsed",
        "non_sat",
    ],
)
def test_strict_xml_incomplete_inconsistent_never_estimates(alteration):
    cfdi = invoice()
    if alteration == "missing_concept":
        cfdi.xml_raw = cfdi.xml_raw.replace(
            '<cfdi:Concepto Importe="100" ObjetoImp="02">',
            '<cfdi:Concepto Importe="100" ObjetoImp="02"><skip/>',
        ).replace("</cfdi:Conceptos>", "</cfdi:Conceptos>")
        # Remove only the first tax group, leaving global detail intact.
        start = cfdi.xml_raw.index("<cfdi:Impuestos>")
        end = cfdi.xml_raw.index("</cfdi:Impuestos>", start) + len("</cfdi:Impuestos>")
        cfdi.xml_raw = cfdi.xml_raw[:start] + cfdi.xml_raw[end:]
    elif alteration == "missing_global":
        start = cfdi.xml_raw.rindex("<cfdi:Impuestos>")
        end = cfdi.xml_raw.index("</cfdi:Impuestos>", start) + len("</cfdi:Impuestos>")
        cfdi.xml_raw = cfdi.xml_raw[:start] + cfdi.xml_raw[end:]
    elif alteration == "bad_amount":
        cfdi.xml_raw = cfdi.xml_raw.replace('Importe="16"', 'Importe="15"')
    elif alteration == "missing_amount":
        cfdi.xml_raw = cfdi.xml_raw.replace(' Importe="16"', "")
    elif alteration == "bad_total":
        cfdi.xml_raw = cfdi.xml_raw.replace('Total="116"', 'Total="115"')
    elif alteration == "invalid_xml":
        cfdi.xml_raw = "not xml"
    elif alteration == "unparsed":
        cfdi.xml_parsed = False
    else:
        cfdi.origen = "other"
    with pytest.raises(ValueError):
        fiscal.strict_direct_cfdi(cfdi)


def test_legacy_default_still_recovers_missing_tax_details():
    legacy = SimpleNamespace(
        subtotal=100,
        total=116,
        descuento=0,
        impuestos_detalle={},
        total_impuestos_trasladados=0,
    )
    assert accounting.summarize_cfdi_tax_components(legacy)["iva_trasladado"] == 16
    assert (
        accounting.summarize_cfdi_tax_components(legacy, allow_estimated_iva=False)[
            "iva_trasladado"
        ]
        == 0
    )


def preview_expense(cfdi, *, tip=0, total=None):
    return SimpleNamespace(
        id=uuid4(),
        cfdi_report=cfdi,
        concepto="Restaurante Suntory",
        metodo_pago="AMEX",
        gasto_cantidad=total if total is not None else cfdi.total,
        propina_no_deducible=tip,
        cuenta_contable=None,
        cuenta_iva=None,
    )


@pytest.fixture
def preview_mocks(monkeypatch):
    monkeypatch.setattr(
        accounting, "resolve_counterpart_account", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(accounting, "_load_active_accounts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        accounting, "_resolve_iva_account", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        accounting, "_resolve_no_deducible_account", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        accounting, "load_effective_budget_concept", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        accounting, "resolve_hospedaje_local_tax", lambda *a, **k: {"amount": 0}
    )


@pytest.mark.asyncio
async def test_strict_explicit_zero_tip_rejects_restaurant_difference(preview_mocks):
    expense = preview_expense(invoice(), total=126, tip=0)
    with pytest.raises(ValueError, match="amount_difference_requires_review"):
        await accounting.build_expense_accounting_preview(
            object(), expense, strict_direct=True
        )


@pytest.mark.asyncio
async def test_declared_tip_preserved_and_default_legacy_inference_unchanged(
    preview_mocks,
):
    cfdi = invoice()
    declared = await accounting.build_expense_accounting_preview(
        object(), preview_expense(cfdi, total=126, tip=10), strict_direct=True
    )
    legacy = await accounting.build_expense_accounting_preview(
        object(), preview_expense(cfdi, total=126, tip=0)
    )
    strict = await accounting.build_expense_accounting_preview(
        object(), preview_expense(cfdi), strict_direct=True
    )
    assert (
        declared["taxes"]["iva_trasladado"]
        == legacy["taxes"]["iva_trasladado"]
        == strict["taxes"]["iva_trasladado"]
        == 16
    )
    assert declared["taxes"]["gastos_no_deducibles"][0]["importe"] == 10
    assert legacy["taxes"]["gastos_no_deducibles"][0]["importe"] == 10
    assert strict["taxes"]["gastos_no_deducibles"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_tip", [-1, "NaN", "Infinity"])
async def test_invalid_tip_is_review_pending(preview_mocks, invalid_tip):
    with pytest.raises(ValueError, match="amount_difference_requires_review"):
        await accounting.build_expense_accounting_preview(
            object(), preview_expense(invoice(), tip=invalid_tip), strict_direct=True
        )


@pytest.mark.asyncio
async def test_posting_strict_pending_uses_existing_failure_contract(monkeypatch):
    from devnous.gastos.services import amex_accounting_posting_service as posting

    expense = preview_expense(invoice())
    expense.cuenta_contable = SimpleNamespace(activo=True)
    preview = AsyncMock(side_effect=ValueError("direct_cfdi_tax_evidence_incomplete"))
    monkeypatch.setattr(posting, "build_expense_accounting_preview", preview)
    result = await posting._single_expense_fiscal_lines(
        SimpleNamespace(scalar=AsyncMock(return_value=False)),
        expense,
        meta={},
        strict_direct=True,
    )
    assert result[0] is None
    assert result[1] == 0
    assert result[2] == "direct_cfdi_tax_evidence_incomplete"
    assert preview.await_count == 1
    assert preview.call_args.kwargs == {"strict_direct": True}


@pytest.mark.asyncio
async def test_rep_dates_are_actual_evidence_not_invoice_or_upload_date():
    cfdi = invoice()
    rep = receipt()
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        session([rep]), cfdi, receiver_rfc=RECEIVER
    )
    assert result["invoice_date"] == "2026-10-06T12:00:00"
    assert result["complements"][0]["payment_dates"] == ["2026-09-29T12:00:00"]
    rep.xml_raw = rep.xml_raw.replace('FechaPago="2026-09-29T12:00:00"', "")
    result = await fiscal.read_direct_cfdi_fiscal_evidence(
        session([rep]), cfdi, receiver_rfc=RECEIVER
    )
    assert result["complement_status"] == "not_verifiable"


@pytest.mark.asyncio
async def test_evidence_proof_changes_with_local_receipt_and_xml():
    cfdi = invoice()
    before = await fiscal.read_direct_cfdi_fiscal_evidence(
        session(), cfdi, receiver_rfc=RECEIVER
    )
    after = await fiscal.read_direct_cfdi_fiscal_evidence(
        session([receipt()]), cfdi, receiver_rfc=RECEIVER
    )
    assert before["proof_hash"] != after["proof_hash"]
    assert before["xml_hash"] == after["xml_hash"]
    cfdi.xml_raw = cfdi.xml_raw.replace('MetodoPago="PUE"', 'MetodoPago="PPD"')
    changed = await fiscal.read_direct_cfdi_fiscal_evidence(
        session(), cfdi, receiver_rfc=RECEIVER
    )
    assert changed["payment_method"] is None  # contradiction stays visible
    assert changed["xml_hash"] != before["xml_hash"]


@pytest.mark.asyncio
async def test_strict_missing_xml_returns_pending_without_fabricated_zero(
    preview_mocks,
):
    with pytest.raises(ValueError, match="direct_cfdi_tax_evidence_incomplete"):
        await accounting.build_expense_accounting_preview(
            object(),
            SimpleNamespace(cfdi_report=None, gasto_cantidad=116),
            strict_direct=True,
        )


@pytest.mark.parametrize("object_code", ["", "03"])
def test_missing_tax_evidence_not_zero_when_object_code_does_not_prove_no_tax(
    object_code,
):
    cfdi = invoice(kind="no_object")
    cfdi.xml_raw = cfdi.xml_raw.replace('ObjetoImp="01"', f'ObjetoImp="{object_code}"')
    with pytest.raises(ValueError, match="direct_cfdi_tax_evidence_incomplete"):
        fiscal.strict_direct_cfdi(cfdi)


def test_tax_cache_never_overrides_xml_fiscal_amounts():
    cfdi = invoice()
    cfdi.impuestos_detalle = {"traslados": [{"impuesto": "002", "importe": 999}]}
    canonical = fiscal.strict_direct_cfdi(cfdi)
    assert (
        accounting.summarize_cfdi_tax_components(canonical, allow_estimated_iva=False)[
            "iva_trasladado"
        ]
        == 16
    )
    assert cfdi.impuestos_detalle["traslados"][0]["importe"] == 999


@pytest.mark.asyncio
@pytest.mark.parametrize("lock", [False, True, None])
async def test_fiscal_coverage_preview_has_no_row_locks_and_legacy_default_keeps_them(
    lock, monkeypatch
):
    from decimal import Decimal

    from sqlalchemy.dialects import postgresql

    from devnous.gastos.services import amex_accounting_posting_service as posting
    from devnous.gastos.services import amex_recognition_service as recognition

    cfdi = invoice()
    expense = preview_expense(cfdi)
    expense.cfdi_report_id = cfdi.id
    db = session([expense])
    builder = AsyncMock(
        return_value=([{"debe": "116", "haber": "0"}], Decimal("116"), None)
    )
    monkeypatch.setattr(posting, "_single_expense_fiscal_lines", builder)
    kwargs = {} if lock is None else {"lock": lock}
    lines, net, reason = await recognition.fiscal_lines(
        db, [expense], strict_direct=True, **kwargs
    )
    assert lines and net == 116 and reason is None
    query = db.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    options = query.get_execution_options()
    assert ("FOR UPDATE" in sql) is (lock is not False)
    if lock is False:
        assert "populate_existing" not in options
        assert options["autoflush"] is False
    else:
        assert options["populate_existing"] is True
    assert builder.call_args.kwargs["strict_direct"] is True
