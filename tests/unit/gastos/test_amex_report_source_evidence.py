import base64
import hashlib
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
    row = expense(archivo_data=base64.b64encode(b"SENSITIVE-CONTENT").decode())
    row._amex_evidence_attachments = [{"id": str(uuid4()), "payload_sha256": "a" * 64}]
    original = build_amex_report_source_snapshot([row])
    assert "BASE64-SENSITIVE-CONTENT" not in str(original)
    row._amex_evidence_attachments[0]["payload_sha256"] = "b" * 64
    assert (
        original["source_key"] != build_amex_report_source_snapshot([row])["source_key"]
    )


def attachment_session(row, payload):
    attachment = {
        "id": uuid4(),
        "gasto_id": row.id,
        "ruta_archivo": payload,
        "categoria": "receipt",
        "mime_type": "application/pdf",
        "tipo_archivo": "application/pdf",
        "nombre_archivo": "receipt.pdf",
        "activo": True,
    }
    result = MagicMock()
    result.mappings.return_value.all.return_value = [attachment]
    return SimpleNamespace(execute=AsyncMock(return_value=result)), attachment


@pytest.mark.asyncio
async def test_remote_content_change_invalidates_same_url_but_url_rotation_does_not(
    monkeypatch,
):
    from devnous.gastos.services import amex_report_evidence_service as service

    row = expense()
    session, attachment = attachment_session(
        row, "https://example.com/receipt.pdf?signature=first"
    )
    monkeypatch.setattr(
        service, "get_adjunto_columns", AsyncMock(return_value=set(attachment))
    )
    loader = AsyncMock(return_value=(b"original-document", "application/pdf", "r.pdf"))
    monkeypatch.setattr(service, "load_adjunto_payload_bytes", loader)
    await service.load_amex_report_attachment_evidence(session, [row])
    original = build_amex_report_source_snapshot([row])
    assert original["partidas"][0]["attachments"][0]["payload_sha256"] == (
        hashlib.sha256(b"original-document").hexdigest()
    )
    assert "https://" not in str(original) and "original-document" not in str(original)
    attachment["ruta_archivo"] = "https://example.com/receipt.pdf?signature=rotated"
    await service.load_amex_report_attachment_evidence(session, [row], locked=True)
    assert build_amex_report_source_snapshot([row]) == original
    loader.return_value = (b"changed-document", "application/pdf", "r.pdf")
    await service.load_amex_report_attachment_evidence(session, [row])
    assert (
        build_amex_report_source_snapshot([row])["source_key"] != original["source_key"]
    )
    assert loader.await_count == 3


@pytest.mark.asyncio
async def test_base64_representation_and_legacy_receipt_attest_decoded_bytes(
    monkeypatch,
):
    from devnous.gastos.services import amex_report_evidence_service as service

    payload = base64.b64encode(b"document bytes").decode()
    row = expense(archivo_data=payload)
    session, attachment = attachment_session(row, payload)
    monkeypatch.setattr(
        service, "get_adjunto_columns", AsyncMock(return_value=set(attachment))
    )
    await service.load_amex_report_attachment_evidence(session, [row])
    original = build_amex_report_source_snapshot([row])
    expected = hashlib.sha256(b"document bytes").hexdigest()
    assert original["partidas"][0]["legacy_receipt_sha256"] == expected
    assert original["partidas"][0]["attachments"][0]["payload_sha256"] == expected
    attachment["ruta_archivo"] = "data:application/pdf;base64," + payload
    row.archivo_data = payload[:4] + "\n" + payload[4:]
    await service.load_amex_report_attachment_evidence(session, [row])
    assert build_amex_report_source_snapshot([row]) == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "http", "empty", "decode", "invalid_url"]
)
async def test_content_unavailable_fails_closed_without_url_or_payload(
    monkeypatch, failure
):
    import httpx

    from devnous.gastos.services import amex_report_evidence_service as service
    from devnous.gastos.utils.receipt_bytes import ReceiptDecodeError

    row = expense()
    session, attachment = attachment_session(row, "https://example.com/?secret=token")
    monkeypatch.setattr(
        service, "get_adjunto_columns", AsyncMock(return_value=set(attachment))
    )
    loader = AsyncMock(return_value=(b"", "application/pdf", "r.pdf"))
    if failure == "timeout":
        loader.side_effect = httpx.ReadTimeout(attachment["ruta_archivo"])
    elif failure == "http":
        request = httpx.Request("GET", attachment["ruta_archivo"])
        loader.side_effect = httpx.HTTPStatusError(
            attachment["ruta_archivo"],
            request=request,
            response=httpx.Response(403, request=request),
        )
    elif failure == "decode":
        loader.side_effect = ReceiptDecodeError("private payload detail")
    elif failure == "invalid_url":
        loader.side_effect = httpx.InvalidURL(attachment["ruta_archivo"])
    monkeypatch.setattr(service, "load_adjunto_payload_bytes", loader)
    with pytest.raises(
        ValueError, match="^amex_attachment_evidence_unavailable$"
    ) as error:
        await service.load_amex_report_attachment_evidence(session, [row])
    assert error.value.__suppress_context__
    assert "token" not in str(error.value)
    assert "_amex_evidence_attachments" not in vars(row)


@pytest.mark.asyncio
async def test_legacy_remote_receipt_requires_bytes_and_rechecks_changes(monkeypatch):
    from devnous.gastos.services import amex_report_evidence_service as service

    row = expense(archivo_data="https://example.com/legacy.pdf?token=one")
    with pytest.raises(ValueError, match="amex_attachment_evidence_unavailable"):
        build_amex_report_source_snapshot([row])
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        service,
        "get_adjunto_columns",
        AsyncMock(return_value={"id", "gasto_id", "ruta_archivo"}),
    )
    loader = AsyncMock(return_value=(b"legacy bytes", "application/pdf", "r.pdf"))
    monkeypatch.setattr(service, "load_adjunto_payload_bytes", loader)
    await service.load_amex_report_attachment_evidence(session, [row])
    original = build_amex_report_source_snapshot([row])
    row.archivo_data = "https://example.com/legacy.pdf?token=two"
    await service.load_amex_report_attachment_evidence(session, [row])
    assert build_amex_report_source_snapshot([row]) == original
    loader.return_value = (b"changed legacy bytes", "application/pdf", "r.pdf")
    await service.load_amex_report_attachment_evidence(session, [row])
    assert (
        build_amex_report_source_snapshot([row])["source_key"] != original["source_key"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://localhost/r", "https://127.0.0.1/r"])
async def test_existing_loader_private_literal_guard_remains_fail_closed(url):
    from devnous.gastos.services.amex_report_evidence_service import (
        _attachment_content_sha256,
    )

    with pytest.raises(ValueError, match="^amex_attachment_evidence_unavailable$"):
        await _attachment_content_sha256(url)


@pytest.mark.asyncio
async def test_real_async_remote_loader_hashes_document_body_and_rejects_http(
    monkeypatch,
):
    import httpx

    from devnous.gastos.services.amex_report_evidence_service import (
        _attachment_content_sha256,
    )
    from devnous.gastos.utils import receipt_bytes

    document = b"%PDF-1.7 document"
    status = 200
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            status, content=document, headers={"content-type": "application/pdf"}
        )

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        receipt_bytes.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    url = "https://example.com/receipt.pdf?signature=first"
    original = await _attachment_content_sha256(url)
    assert original == hashlib.sha256(document).hexdigest()
    assert original == await _attachment_content_sha256(url.replace("first", "rotated"))
    document = b"%PDF-1.7 changed document"
    assert original != await _attachment_content_sha256(url)
    status = 403
    with pytest.raises(ValueError, match="^amex_attachment_evidence_unavailable$"):
        await _attachment_content_sha256(url)
    assert len(requests) == 4
