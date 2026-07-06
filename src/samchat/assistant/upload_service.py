from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException, UploadFile

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
        raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Max file size is 15MB")

    content_type = upload.content_type or "application/octet-stream"
    kind = (kind or "").strip().lower()
    if kind not in {"image", "voice", "spreadsheet", "text"}:
        raise HTTPException(
            status_code=400, detail="kind must be image, voice, spreadsheet or text"
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
