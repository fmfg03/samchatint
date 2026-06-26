from __future__ import annotations

import json
from typing import Any, Dict, Optional


def _note_block(note: Optional[str]) -> str:
    cleaned = (note or "").strip()
    return f"\nNota del usuario: {cleaned}" if cleaned else ""


def build_roster_upload_context(
    *, roster_payload: Dict[str, Any], filename: Optional[str], note: Optional[str]
) -> str:
    guidance = (
        "Si el usuario pide alta de equipo, usa tool "
        "tournament_team_register_from_roster con este JSON. "
        "Si falta torneo/categoria/equipo, pregunta antes de confirmar."
    )
    return (
        "Entrada spreadsheet procesada para alta de equipo/jugadores.\n"
        f"Archivo: {filename or 'roster'}\n"
        f"Rows parsed: {roster_payload.get('rows_parsed')}\n"
        f"Datos estructurados JSON:\n{json.dumps(roster_payload, ensure_ascii=False)}\n"
        f"{_note_block(note)}\n\n"
        f"{guidance}"
    )


def build_spreadsheet_upload_context(*, preview_text: str) -> str:
    return preview_text


def build_text_upload_context(
    *, parsed_text: str, filename: Optional[str], note: Optional[str]
) -> str:
    return (
        "Entrada texto/documento procesada para contexto.\n"
        f"Archivo: {filename or 'documento'}\n"
        f"Texto extraido:\n{parsed_text[:120000]}{_note_block(note)}\n\n"
        "Usa este contenido como contexto para responder y/o ejecutar tareas."
    )


def build_media_upload_context(
    *, kind: str, extracted_text: str, note: Optional[str]
) -> str:
    return (
        f"Entrada {kind} procesada para captura de gastos.\n"
        f"Texto extraido:\n{extracted_text}{_note_block(note)}\n\n"
        "Con esto, ayuda a registrar o actualizar el gasto. "
        "Si faltan datos obligatorios, preguntalos."
    )
