from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from devnous.gastos.utils.receipt_bytes import read_upload_limited

from .document_intake import build_document_intake_result
from .file_parsing import (
    extract_document_text_from_bytes,
    spreadsheet_looks_like_roster,
    spreadsheet_preview_text,
    spreadsheet_records_from_bytes,
)
from .upload_context import (
    build_media_upload_context,
    build_roster_upload_context,
    build_spreadsheet_upload_context,
    build_text_upload_context,
)


AnthropicTextExtractor = Callable[..., Awaitable[str]]
ProviderOrderResolver = Callable[..., List[str]]
OpenAIClientFactory = Callable[[Optional[str]], Any]
RosterExtractor = Callable[[List[dict[str, Any]]], dict[str, Any]]

_ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
_ALLOWED_VOICE_MIME_PREFIXES = ("audio/",)
_ALLOWED_VOICE_MIME_TYPES = {
    "video/webm",
}
_ASSISTANT_MEDIA_UPLOAD_MAX_BYTES = 15 * 1024 * 1024


def _looks_like_image_bytes(raw: bytes, content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _looks_like_voice_bytes(raw: bytes, content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in {"audio/wav", "audio/x-wav"}:
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    if mime in {"audio/mpeg", "audio/mp3"}:
        return (
            raw.startswith(b"ID3")
            or raw.startswith(b"\xff\xfb")
            or raw.startswith(b"\xff\xf3")
        )
    if mime in {"audio/ogg", "audio/opus"}:
        return raw.startswith(b"OggS")
    if mime in {"audio/webm", "video/webm"}:
        return raw.startswith(b"\x1a\x45\xdf\xa3")
    if mime in {"audio/mp4", "audio/m4a", "audio/aac"}:
        return len(raw) >= 12 and raw[4:8] == b"ftyp"
    return False


def _document_intake_context(result: Dict[str, Any]) -> str:
    return (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        f"{json.dumps(result, ensure_ascii=False, sort_keys=True)}\n\n"
        "Instrucciones de seguridad: usa este resultado como intake documental. "
        "No afirmes que una accion write fue ejecutada. "
        "Si hay proposed_actions, presentalas como propuestas y pide confirmacion "
        "explicita antes de cualquier ejecucion por action_router/canonical adapters.\n\n"
    )


async def extract_text_from_media(
    *,
    kind: str,
    upload: UploadFile,
    note: Optional[str],
    raw: Optional[bytes] = None,
    openai_api_key: Optional[str] = None,
    extract_text_from_image_anthropic: AnthropicTextExtractor,
    assistant_provider_order: ProviderOrderResolver,
    get_openai_client: OpenAIClientFactory,
    extract_roster_from_records: RosterExtractor,
) -> str:
    if raw is None:
        try:
            raw = await read_upload_limited(
                upload,
                max_bytes=_ASSISTANT_MEDIA_UPLOAD_MAX_BYTES,
                too_large_message="Max file size is 15MB",
                empty_message="Uploaded file is empty",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw) > _ASSISTANT_MEDIA_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Max file size is 15MB")

    content_type = upload.content_type or "application/octet-stream"
    kind = (kind or "").strip().lower()
    if kind not in {"image", "voice", "spreadsheet", "text"}:
        raise HTTPException(
            status_code=400, detail="kind must be image, voice, spreadsheet or text"
        )
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    if kind == "image" and normalized_content_type not in _ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Image uploads must be JPEG, PNG, WEBP, or GIF.",
        )
    if kind == "image" and not _looks_like_image_bytes(raw, normalized_content_type):
        raise HTTPException(
            status_code=400,
            detail="Image upload content does not match the declared image type.",
        )
    if kind == "voice" and not (
        normalized_content_type in _ALLOWED_VOICE_MIME_TYPES
        or any(
            normalized_content_type.startswith(prefix)
            for prefix in _ALLOWED_VOICE_MIME_PREFIXES
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Voice uploads must use an audio MIME type or video/webm.",
        )
    if kind == "voice" and not _looks_like_voice_bytes(raw, normalized_content_type):
        raise HTTPException(
            status_code=400,
            detail="Voice upload content does not match the declared audio type.",
        )

    extracted = ""
    if kind == "spreadsheet":
        records = await asyncio.to_thread(
            spreadsheet_records_from_bytes,
            raw=raw,
            filename=upload.filename or "",
            content_type=content_type,
        )
        intake = build_document_intake_result(
            file_name=upload.filename or "",
            file_kind="spreadsheet",
            records=records,
            text=note or "",
        ).to_dict()
        if not spreadsheet_looks_like_roster(records):
            context = build_spreadsheet_upload_context(
                preview_text=spreadsheet_preview_text(
                    records=records,
                    filename=upload.filename or "",
                    note=note,
                )
            )
            return _document_intake_context(intake) + context
        roster_payload = extract_roster_from_records(records)
        context = build_roster_upload_context(
            roster_payload=roster_payload,
            filename=upload.filename,
            note=note,
        )
        return _document_intake_context(intake) + context

    if kind == "text":
        parsed_text = await asyncio.to_thread(
            extract_document_text_from_bytes,
            raw=raw,
            filename=upload.filename,
            mime_type=content_type,
        )
        parsed_text = (parsed_text or "").strip()
        if not parsed_text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No se pudo extraer texto del archivo. "
                    "Formatos recomendados: .txt, .md, .docx"
                ),
            )
        intake = build_document_intake_result(
            file_name=upload.filename or "",
            file_kind="text",
            text=parsed_text,
        ).to_dict()
        context = build_text_upload_context(
            parsed_text=parsed_text,
            filename=upload.filename,
            note=note,
        )
        return _document_intake_context(intake) + context

    if kind == "voice":
        client = get_openai_client(openai_api_key)
        transcription_model = os.getenv(
            "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"
        )
        audio_file = (upload.filename or "audio_input.webm", raw, content_type)
        tx = await asyncio.to_thread(
            client.audio.transcriptions.create,
            model=transcription_model,
            file=audio_file,
        )
        extracted = (getattr(tx, "text", None) or "").strip()
    else:
        provider_order = assistant_provider_order(capability="vision")
        errors: List[str] = []
        for provider in provider_order:
            try:
                if provider == "anthropic":
                    extracted = await extract_text_from_image_anthropic(
                        raw=raw,
                        content_type=content_type,
                        note=note,
                    )
                else:
                    client = get_openai_client(openai_api_key)
                    b64 = base64.b64encode(raw).decode("ascii")
                    image_url = f"data:{content_type};base64,{b64}"
                    vision_model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
                    vr = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=vision_model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Extrae el texto relevante de este "
                                            "comprobante/imagen para "
                                            "captura de gastos. "
                                            "Incluye monto, fecha, "
                                            "comercio/proveedor y concepto "
                                            "si se ven."
                                        ),
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": image_url},
                                    },
                                ],
                            }
                        ],
                        temperature=0,
                    )
                    extracted = (vr.choices[0].message.content or "").strip()
                if extracted:
                    break
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                continue
        if not extracted and errors:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo extraer texto de imagen: {errors[-1]}",
            )

    if not extracted:
        raise HTTPException(
            status_code=400, detail="No se pudo extraer texto del archivo"
        )

    intake = build_document_intake_result(
        file_name=upload.filename or "",
        file_kind=kind,
        text=extracted,
        user_context={"note": note} if note else None,
    ).to_dict()
    return _document_intake_context(intake) + build_media_upload_context(
        kind=kind,
        extracted_text=extracted,
        note=note,
    )
