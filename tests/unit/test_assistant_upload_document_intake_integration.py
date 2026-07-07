import asyncio
import json
import time
from io import BytesIO

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from samchat.assistant.action_router import supported_actions
import samchat.assistant.upload_service as upload_service
from samchat.assistant.upload_service import (
    _looks_like_image_bytes,
    _looks_like_voice_bytes,
    extract_text_from_media,
)


def _upload(filename: str, content_type: str, body: bytes) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=BytesIO(body),
        headers=Headers({"content-type": content_type}),
    )


class _LargeChunkedUpload:
    def __init__(self, *, total_size: int, content_type: str = "text/plain"):
        self.filename = "large.txt"
        self.content_type = content_type
        self.remaining = total_size
        self.total_size = total_size
        self.bytes_returned = 0
        self.read_calls = 0
        self.seek_calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self.remaining <= 0:
            return b""
        if size is None or size < 0:
            chunk_size = self.remaining
        else:
            chunk_size = min(size, self.remaining)
        self.remaining -= chunk_size
        self.bytes_returned += chunk_size
        return b"x" * chunk_size

    async def seek(self, _offset: int) -> None:
        self.seek_calls += 1


def _parse_intake_context(context: str) -> dict:
    assert context.startswith("DOCUMENT_INTAKE_RESULT JSON:\n")
    raw_json = context.split("DOCUMENT_INTAKE_RESULT JSON:\n", 1)[1].split("\n\n", 1)[0]
    payload = json.loads(raw_json)
    assert "chain" not in raw_json.lower()
    assert "private_reasoning" not in raw_json.lower()
    return payload


async def _forbidden_provider_call(*args, **kwargs):  # pragma: no cover - failure helper
    raise AssertionError("provider/OCR path must not be called for synthetic fixtures")


def _forbidden_provider_order(*args, **kwargs):  # pragma: no cover - failure helper
    raise AssertionError("provider order must not be resolved for text/spreadsheet fixtures")


def _forbidden_openai_client(*args, **kwargs):  # pragma: no cover - failure helper
    raise AssertionError("openai client must not be requested for text/spreadsheet fixtures")


def _roster_payload(records):
    return {
        "team_name": "Tigres",
        "category_name": "Sub-17",
        "players": [
            {"first_name": row.get("Nombre"), "last_name": row.get("Apellido")}
            for row in records
        ],
        "rows_parsed": len(records),
    }


def _run_upload(kind: str, upload: UploadFile, raw: bytes, note: str | None = None) -> str:
    return asyncio.run(
        extract_text_from_media(
            kind=kind,
            upload=upload,
            note=note,
            raw=raw,
            extract_text_from_image_anthropic=_forbidden_provider_call,
            assistant_provider_order=_forbidden_provider_order,
            get_openai_client=_forbidden_openai_client,
            extract_roster_from_records=_roster_payload,
        )
    )


def _assert_existing_canonical_actions_only(intake: dict) -> None:
    allowed = set(supported_actions())
    for action in intake.get("proposed_actions") or []:
        assert action["canonical_action"] in allowed


async def _measure_event_loop_delay(coro, *, sleep_delay: float = 0.02) -> float:
    ticks = []

    async def ticker():
        await asyncio.sleep(sleep_delay)
        ticks.append(time.perf_counter())

    started_at = time.perf_counter()
    await asyncio.gather(coro, ticker())
    assert ticks
    return ticks[0] - started_at


async def _run_upload_async(
    kind: str, upload: UploadFile, raw: bytes, note: str | None = None
) -> str:
    return await extract_text_from_media(
        kind=kind,
        upload=upload,
        note=note,
        raw=raw,
        extract_text_from_image_anthropic=_forbidden_provider_call,
        assistant_provider_order=_forbidden_provider_order,
        get_openai_client=_forbidden_openai_client,
        extract_roster_from_records=_roster_payload,
    )


@pytest.mark.asyncio
async def test_image_upload_provider_errors_are_not_exposed_to_client():
    async def _leaky_anthropic_error(**_kwargs):
        raise RuntimeError("anthropic leaked SECRET_ANTHROPIC_TRACE")

    class _OpenAIClient:
        class _Chat:
            class _Completions:
                @staticmethod
                def create(**_kwargs):
                    raise RuntimeError("openai leaked SECRET_OPENAI_TRACE")

            completions = _Completions()

        chat = _Chat()

    def _provider_order(**_kwargs):
        return ["anthropic", "openai"]

    def _openai_client(_api_key):
        return _OpenAIClient()

    with pytest.raises(HTTPException) as exc_info:
        await extract_text_from_media(
            kind="image",
            upload=_upload("receipt.png", "image/png", b"\x89PNG\r\n\x1a\nfake"),
            note=None,
            raw=b"\x89PNG\r\n\x1a\nfake",
            extract_text_from_image_anthropic=_leaky_anthropic_error,
            assistant_provider_order=_provider_order,
            get_openai_client=_openai_client,
            extract_roster_from_records=_roster_payload,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No se pudo extraer texto de imagen"
    assert "SECRET_ANTHROPIC_TRACE" not in exc_info.value.detail
    assert "SECRET_OPENAI_TRACE" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_spreadsheet_upload_parsing_does_not_block_event_loop(monkeypatch):
    def _slow_spreadsheet_parser(**_kwargs):
        time.sleep(0.2)
        return [{"Cuenta": "1000", "Descripcion": "Banco"}]

    monkeypatch.setattr(
        upload_service, "spreadsheet_records_from_bytes", _slow_spreadsheet_parser
    )

    delay = await _measure_event_loop_delay(
        _run_upload_async(
            "spreadsheet",
            _upload("balanza.csv", "text/csv", b"Cuenta,Descripcion\n1000,Banco\n"),
            b"Cuenta,Descripcion\n1000,Banco\n",
        )
    )

    assert delay < 0.08


@pytest.mark.asyncio
async def test_text_upload_parsing_does_not_block_event_loop(monkeypatch):
    def _slow_text_parser(**_kwargs):
        time.sleep(0.2)
        return "texto extraido"

    monkeypatch.setattr(
        upload_service, "extract_document_text_from_bytes", _slow_text_parser
    )

    delay = await _measure_event_loop_delay(
        _run_upload_async(
            "text",
            _upload("nota.txt", "text/plain", b"texto"),
            b"texto",
        )
    )

    assert delay < 0.08


@pytest.mark.asyncio
async def test_media_upload_without_preloaded_raw_rejects_before_consuming_full_stream():
    upload = _LargeChunkedUpload(total_size=20 * 1024 * 1024)

    with pytest.raises(HTTPException) as exc_info:
        await extract_text_from_media(
            kind="text",
            upload=upload,
            note=None,
            extract_text_from_image_anthropic=_forbidden_provider_call,
            assistant_provider_order=_forbidden_provider_order,
            get_openai_client=_forbidden_openai_client,
            extract_roster_from_records=_roster_payload,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Max file size is 15MB"
    assert upload.bytes_returned < upload.total_size
    assert upload.read_calls > 1


def test_upload_accounting_balance_context_surfaces_intake_and_blocks_writes() -> None:
    raw = (
        "Cuenta,Descripcion de la cuenta,Total de cargos,Total de abonos,Saldo final\n"
        "1000,Banco,500.00,500.00,100.00\n"
        "2000,Ingresos,100.00,100.00,0.00\n"
    ).encode("utf-8")

    context = _run_upload(
        "spreadsheet",
        _upload("balanza.csv", "text/csv", raw),
        raw,
    )
    intake = _parse_intake_context(context)

    assert intake["detected_document_type"] == "accounting_balance"
    assert {"company", "project", "period"}.issubset(set(intake["missing_fields"]))
    assert intake["proposed_actions"]
    assert all(action["risk_level"] == "read" for action in intake["proposed_actions"])
    assert all(action["requires_confirmation"] is False for action in intake["proposed_actions"])
    assert intake["safety"]["can_execute_without_confirmation"] is False
    _assert_existing_canonical_actions_only(intake)


def test_upload_roster_context_extracts_team_players_and_requires_tournament() -> None:
    raw = (
        "Equipo,Categoria,Nombre,Apellido,CURP\n"
        "Tigres,Sub-17,Ana,Lopez,MALFORMADA\n"
        "Tigres,Sub-17,Luis,Garcia,GALL090101HDFRRS09\n"
    ).encode("utf-8")

    context = _run_upload(
        "spreadsheet",
        _upload("roster.csv", "text/csv", raw),
        raw,
    )
    intake = _parse_intake_context(context)

    assert intake["detected_document_type"] == "roster"
    assert intake["entities"]["team_name"] == "Tigres"
    assert intake["entities"]["category"] == "Sub-17"
    assert intake["entities"]["player_count"] == 2
    assert "tournament" in intake["missing_fields"]
    assert "Confirma torneo" in " ".join(intake["questions_for_user"])
    write_actions = [
        action for action in intake["proposed_actions"] if action["requires_confirmation"]
    ]
    assert write_actions
    assert all(action["write_blocked"] is True for action in write_actions)
    _assert_existing_canonical_actions_only(intake)


def test_upload_cfdi_xml_context_extracts_invoice_fields_without_paid_state() -> None:
    raw = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
    Fecha="2026-05-12T10:00:00" Total="45000.00" Moneda="MXN">
  <cfdi:Emisor Rfc="AAA010101AAA" Nombre="Proveedor SA"/>
  <cfdi:Receptor Rfc="BBB010101BBB"/>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="123E4567-E89B-12D3-A456-426614174000"/></cfdi:Complemento>
</cfdi:Comprobante>
"""

    context = _run_upload(
        "text",
        _upload("factura.xml", "application/xml", raw),
        raw,
    )
    intake = _parse_intake_context(context)

    assert intake["detected_document_type"] == "cfdi_invoice"
    assert intake["entities"]["uuid"] == "123E4567-E89B-12D3-A456-426614174000"
    assert intake["entities"]["issuer_rfc"] == "AAA010101AAA"
    assert intake["entities"]["amount"] == "45000.00"
    assert "expense_or_document_candidate" in intake["missing_fields"]
    assert "paid" not in json.dumps(intake, ensure_ascii=False).lower()
    _assert_existing_canonical_actions_only(intake)


def test_upload_payment_proof_context_requires_candidate_before_registering_payment() -> None:
    raw = (
        "Comprobante de pago SPEI\n"
        "Monto: $45,000.00\n"
        "Fecha: 2026-05-13\n"
        "Clave de rastreo: SPEI123ABC\n"
        "Beneficiario: Proveedor SA\n"
        "Concepto: Informe A\n"
    ).encode("utf-8")

    context = _run_upload(
        "text",
        _upload("spei.txt", "text/plain", raw),
        raw,
    )
    intake = _parse_intake_context(context)

    assert intake["detected_document_type"] == "payment_proof"
    assert intake["entities"]["amount"] == "45,000.00"
    assert intake["entities"]["bank_reference"] == "SPEI123ABC"
    assert "document_or_expense_candidate" in intake["missing_fields"]
    write_action = next(
        action for action in intake["proposed_actions"]
        if action["canonical_action"] == "receipts.register_document_payment"
    )
    assert write_action["requires_confirmation"] is True
    assert write_action["write_blocked"] is True
    _assert_existing_canonical_actions_only(intake)


def test_upload_unknown_context_has_summary_question_and_no_write_proposal() -> None:
    raw = "Notas generales sin workflow deterministico claro.".encode("utf-8")

    context = _run_upload(
        "text",
        _upload("generic.txt", "text/plain", raw),
        raw,
    )
    intake = _parse_intake_context(context)

    assert intake["detected_document_type"] == "unknown_or_generic"
    assert intake["proposed_actions"] == []
    assert intake["missing_fields"] == ["target_workflow"]
    assert intake["questions_for_user"] == ["Indica a que workflow pertenece este documento."]
    assert intake["safety"]["blocked_reason"] == "unsupported_document_type"


@pytest.mark.asyncio
async def test_image_upload_rejects_non_image_mime_before_provider_call() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _run_upload_async(
            "image",
            _upload("nota.txt", "text/plain", b"texto"),
            b"texto",
        )

    assert exc_info.value.status_code == 400
    assert "image" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_voice_upload_rejects_non_audio_mime_before_provider_call() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _run_upload_async(
            "voice",
            _upload("imagen.png", "image/png", b"not really audio"),
            b"not really audio",
        )

    assert exc_info.value.status_code == 400
    assert "audio" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_image_upload_rejects_mime_spoofed_bytes_before_provider_call() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _run_upload_async(
            "image",
            _upload("fake.png", "image/png", b"not actually a png"),
            b"not actually a png",
        )

    assert exc_info.value.status_code == 400
    assert "image" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_voice_upload_rejects_mime_spoofed_bytes_before_provider_call() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _run_upload_async(
            "voice",
            _upload("fake.wav", "audio/wav", b"not actually a wav"),
            b"not actually a wav",
        )

    assert exc_info.value.status_code == 400
    assert "audio" in str(exc_info.value.detail).lower()


def test_upload_magic_byte_helpers_accept_supported_image_and_voice_headers() -> None:
    assert _looks_like_image_bytes(b"\x89PNG\r\n\x1a\npayload", "image/png")
    assert _looks_like_image_bytes(b"\xff\xd8\xffpayload", "image/jpeg")
    assert _looks_like_image_bytes(b"GIF89apayload", "image/gif")
    assert _looks_like_image_bytes(b"RIFF\x00\x00\x00\x00WEBPpayload", "image/webp")

    assert _looks_like_voice_bytes(b"RIFF\x00\x00\x00\x00WAVEpayload", "audio/wav")
    assert _looks_like_voice_bytes(b"ID3payload", "audio/mpeg")
    assert _looks_like_voice_bytes(b"OggSpayload", "audio/ogg")
    assert _looks_like_voice_bytes(b"\x1a\x45\xdf\xa3payload", "audio/webm")
