"""Historical identity and legacy bridge authority stay separate from new SAT use."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_expense_account_settlement_service as service
from devnous.gastos.services.amex_direct_common import DirectConflict


@pytest.mark.asyncio
async def test_nonposted_legacy_bridge_does_not_imply_direct_prior_coverage(
    monkeypatch,
):
    """A reader cannot turn a legacy association header into a DIRECT receipt."""
    bridge = SimpleNamespace(binding_mode="report_bridge", accounting_poliza_id=None)
    representation = SimpleNamespace(id=uuid4(), role="statement")
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [(bridge, representation)])
        )
    )
    monkeypatch.setattr(service, "selected_origin", AsyncMock(return_value=None))
    with pytest.raises(DirectConflict, match="prior_bank_requires_explicit_selection"):
        await service.prior_source(
            session,
            item=SimpleNamespace(id=uuid4()),
            source=SimpleNamespace(imported_expense_id=uuid4()),
            context=SimpleNamespace(),
            card=SimpleNamespace(),
            config={},
            actor_id=uuid4(),
        )
    assert session.execute.await_count == 1


def historical_cfdi(xml):
    from devnous.gastos.models import CFDIReport
    from devnous.gastos.services.cfdi_parser import parse_cfdi_xml

    parsed = parse_cfdi_xml(xml)
    columns = set(CFDIReport.__table__.columns.keys())
    fields = {key: value for key, value in parsed.items() if key in columns}
    fields.update(xml_raw=xml, xml_parsed=parsed, origen="user_upload")
    return CFDIReport(**fields)


def xml_fixture():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "acceptance/test_amex_sat_budget_direct_postgres.py"
    )
    spec = importlib.util.spec_from_file_location(
        "historical_identity_xml_fixture", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._synthetic_sat_xml()


def test_historical_uploaded_identity_does_not_relax_new_sat_requirement():
    from devnous.gastos.services.amex_direct_matching_service import cfdi_identity

    cfdi = historical_cfdi(xml_fixture())
    result = service.historical_cfdi_identity(cfdi)
    assert result["uuid"] == cfdi.cfdi_uuid.upper()
    assert result["xml_hash"]
    with pytest.raises(DirectConflict, match="canonical_sat_xml_required"):
        cfdi_identity(cfdi)


@pytest.mark.parametrize("change", ["uuid", "rfc", "malformed", "missing"])
def test_historical_uploaded_identity_requires_exact_xml(change):
    cfdi = historical_cfdi(xml_fixture())
    if change == "uuid":
        cfdi.cfdi_uuid = str(uuid4())
    elif change == "rfc":
        cfdi.receptor_rfc = "CCC010101CCC"
    elif change == "malformed":
        cfdi.xml_raw = "<invalid>"
    else:
        cfdi.xml_raw = None
    with pytest.raises(DirectConflict, match="historical_cfdi_"):
        service.historical_cfdi_identity(cfdi)


@pytest.mark.parametrize(
    "old,new,reason",
    [
        ('TipoDeComprobante="I"', 'TipoDeComprobante="P"', "invoice_type_i_required"),
        ('Moneda="MXN"', 'Moneda="USD"', "unsupported_direct_currency"),
    ],
)
def test_historical_uploaded_identity_keeps_type_and_currency_boundaries(
    old, new, reason
):
    cfdi = historical_cfdi(xml_fixture().replace(old, new))
    with pytest.raises(DirectConflict, match=reason):
        service.historical_cfdi_identity(cfdi)
