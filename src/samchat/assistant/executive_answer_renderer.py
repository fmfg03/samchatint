from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


_SIMPLE_TEXT_KEYS = (
    'rendered_text',
    'assistant_message',
    'message',
    'answer',
    'short_answer',
)
_STRUCTURED_KEYS = (
    'status',
    'state',
    'summary',
    'headline',
    'evidence_found',
    'missing_evidence',
    'missing_information',
    'next_actions',
    'next_steps',
    'next_questions',
    'source_reports',
    'sources',
)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_human_text(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    stripped = text.lstrip()
    if stripped.startswith('{"name"') or stripped.startswith("{'name'"):
        return False
    if stripped.startswith('{') and '"arguments"' in stripped[:240]:
        return False
    return True


def _as_lines(value: Any, *, limit: int = 6) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            if item is None or item == '':
                continue
            lines.append(f'{key}: {item}')
            if len(lines) >= limit:
                break
        return lines
    if isinstance(value, (str, bytes)):
        text = value.decode('utf-8', errors='ignore') if isinstance(value, bytes) else value
        return [text.strip()] if text.strip() else []
    if isinstance(value, Sequence):
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                label = item.get('label') or item.get('name') or item.get('title') or item.get('id')
                status = item.get('status') or item.get('state')
                detail = item.get('detail') or item.get('summary') or item.get('value')
                parts = [str(part).strip() for part in (label, status, detail) if str(part or '').strip()]
                text = ' - '.join(parts)
            else:
                text = str(item).strip()
            if text:
                lines.append(text)
            if len(lines) >= limit:
                break
        return lines
    text = str(value).strip()
    return [text] if text else []


def _first_mapping(*values: Any) -> Mapping[str, Any] | None:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return None


def _first_text(mapping: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if _is_human_text(value):
            return str(value).strip()
    return None


def _has_structured_signal(result: Mapping[str, Any]) -> bool:
    return any(key in result and result.get(key) not in (None, '', [], {}) for key in _STRUCTURED_KEYS)


def _render_structured_result(tool_name: str, result: Mapping[str, Any]) -> str:
    headline = _first_text(result, ('headline', 'title')) or 'Respuesta ejecutiva'
    summary = _first_text(result, ('summary', 'short_answer', 'answer'))
    status = _clean_text(result.get('status') or result.get('state'))
    evidence = _as_lines(result.get('evidence_found') or result.get('sources') or result.get('source_reports'))
    missing = _as_lines(result.get('missing_evidence') or result.get('missing_information') or result.get('missing'))
    next_actions = _as_lines(result.get('next_actions') or result.get('next_steps'))
    next_questions = _as_lines(result.get('next_questions'), limit=3)

    lines: list[str] = [headline]
    if summary:
        lines.append(f'\nRespuesta corta: {summary}')
    elif status:
        lines.append(f'\nRespuesta corta: tengo un diagnostico con estado {status}, pero falta mas evidencia para una conclusion completa.')
    else:
        lines.append('\nRespuesta corta: en este momento no tengo informacion suficiente para dar una conclusion completa con evidencia.')

    if status:
        lines.append(f'\nEstado: {status}.')
    if evidence:
        lines.append('\nLo que si tengo soportado:')
        lines.extend(f'- {line}' for line in evidence)
    if missing:
        lines.append('\nLo que todavia falta para afirmarlo con seguridad:')
        lines.extend(f'- {line}' for line in missing)
    if next_actions:
        lines.append('\nSiguiente paso sugerido:')
        lines.extend(f'- {line}' for line in next_actions)
    if next_questions:
        lines.append('\nPreguntas minimas para continuar:')
        lines.extend(f'- {line}' for line in next_questions)

    lines.append('\nFrontera de autoridad: esto es una respuesta de lectura y diagnostico; no ejecute cambios ni asumi datos sin evidencia.')
    lines.append(f'Trazabilidad: respuesta generada desde la herramienta {tool_name}.')
    return '\n'.join(lines)


def render_executive_tool_result(tool_name: str, result: Mapping[str, Any]) -> str | None:
    """Return a human executive answer for a read-tool result when it is safe to do so.

    Tool payloads can be useful evidence, but they are not user-facing prose. This
    function is intentionally generic: domain tools may provide a canonical
    ``conversation_answer.rendered_text``; otherwise we synthesize a concise answer
    from common report fields. Unknown opaque payloads fall through to the model.
    """

    conversation_answer = _first_mapping(result.get('conversation_answer'))
    if conversation_answer:
        rendered = _first_text(conversation_answer, _SIMPLE_TEXT_KEYS)
        if rendered:
            return rendered

    direct = _first_text(result, _SIMPLE_TEXT_KEYS)
    if direct:
        return direct

    if _has_structured_signal(result):
        return _render_structured_result(tool_name, result)

    return None
