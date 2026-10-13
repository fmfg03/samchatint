from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_report_evidence_service import (
    build_amex_report_source_snapshot,
    create_operational_report_evidence,
)


def expense(**changes):
    row = SimpleNamespace(
        id=uuid4(),
        pagado_con_amex_empresa=True,
        gasto_cantidad=229.98,
        currency="MXN",
        cfdi_report=None,
    )
    row.__dict__.update(changes)
    return row


def proof():
    now = datetime.now(timezone.utc)
    informe = SimpleNamespace(
        id=uuid4(), tipo="INFORME", estado="aprobado", aprobado_en=now
    )
    approval = SimpleNamespace(
        id=uuid4(),
        tipo_entidad="documento",
        accion="aprobar",
        entidad_id=informe.id,
        aprobador_id=uuid4(),
        fecha=now,
    )
    return informe, approval


def test_snapshot_deterministic_complete_missing_not_zero_and_no_decision():
    one = expense(ultimos_4_digitos=None, iva=None)
    two = expense(pagado_con_amex_empresa=False)
    snapshot = build_amex_report_source_snapshot([one, two])
    assert snapshot == build_amex_report_source_snapshot([two, one])
    assert len(snapshot["partidas"]) == 2
    item = next(row for row in snapshot["partidas"] if row["id"] == str(one.id))
    assert item["iva"] is None and item["ultimos_4_digitos"] is None
    assert "treatment" not in item
    one.gasto_cantidad = 230
    assert (
        snapshot["source_key"]
        != build_amex_report_source_snapshot([one, two])["source_key"]
    )


@pytest.mark.asyncio
async def test_real_operational_actor_snapshot_without_finance_impersonation():
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=MagicMock())
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    version = MagicMock()
    version.scalar_one.return_value = 0
    session.execute.side_effect = [no_existing, version]
    informe, approval = proof()
    evidence = await create_operational_report_evidence(
        session, informe=informe, approval=approval, expenses=[expense()]
    )
    assert evidence.actor_id == approval.aprobador_id
    assert evidence.created_at == approval.fecha
    assert evidence.approval_id == approval.id
    assert evidence.kind == "operational_approval"
    assert evidence.decisions_json is None
    assert evidence.request_payload_hash is None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["area", "entity", "date", "state", "type"])
async def test_reject_incomplete_or_non_final_proof(invalid):
    informe, approval = proof()
    if invalid == "area":
        approval.accion = "aprobar_area"
    elif invalid == "entity":
        approval.entidad_id = uuid4()
    elif invalid == "date":
        approval.fecha = datetime(2000, 1, 1, tzinfo=timezone.utc)
    elif invalid == "state":
        informe.estado = "enviado"
    else:
        informe.tipo = "SOLICITUD"
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=MagicMock())
    with pytest.raises(ValueError, match="invalid_amex_final_approval_proof"):
        await create_operational_report_evidence(
            session, informe=informe, approval=approval, expenses=[expense()]
        )
    session.execute.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_non_amex_unchanged_no_queries_or_mutation():
    informe, approval = proof()
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=MagicMock())
    assert (
        await create_operational_report_evidence(
            session,
            informe=informe,
            approval=approval,
            expenses=[expense(pagado_con_amex_empresa=False)],
        )
        is None
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_evidence_retry_reuses_same_event():
    informe, approval = proof()
    existing = SimpleNamespace(id=uuid4())
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = SimpleNamespace(
        execute=AsyncMock(return_value=result), flush=AsyncMock(), add=MagicMock()
    )
    assert (
        await create_operational_report_evidence(
            session, informe=informe, approval=approval, expenses=[expense()]
        )
        is existing
    )
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


def test_cfdi_same_uuid_tax_changes_invalidate_factual_source():
    cfdi = SimpleNamespace(
        id=uuid4(),
        cfdi_uuid=str(uuid4()),
        total=116,
        subtotal=100,
        impuestos_detalle={"iva": 16},
    )
    row = expense(cfdi_report=cfdi)
    original = build_amex_report_source_snapshot([row])
    cfdi.impuestos_detalle = {"iva": 8}
    changed = build_amex_report_source_snapshot([row])
    assert original["source_key"] != changed["source_key"]
    assert original["partidas"][0]["cfdi"]["impuestos_detalle"] == {"iva": 16}


def test_explicit_attachment_metadata_changes_invalidate_without_snapshot_bytes():
    row = expense(archivo_data="BASE64-SENSITIVE-CONTENT")
    row._amex_evidence_attachments = [{"id": str(uuid4()), "payload_sha256": "a" * 64}]
    original = build_amex_report_source_snapshot([row])
    assert "BASE64-SENSITIVE-CONTENT" not in str(original)
    row._amex_evidence_attachments[0]["payload_sha256"] = "b" * 64
    assert (
        original["source_key"] != build_amex_report_source_snapshot([row])["source_key"]
    )
