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


def _format_money(value: Any, *, currency: str = 'MXN') -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f'{currency} ${amount:,.2f}'


def _format_pct(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f'{float(value):,.2f}%'
    except (TypeError, ValueError):
        return None


def _render_finance_realtime_report(result: Mapping[str, Any]) -> str:
    title = _first_text(result, ('title',)) or 'Reporte financiero en tiempo real'
    period = _first_mapping(result.get('period')) or {}
    totals = _first_mapping(result.get('totals')) or {}
    budget = _first_mapping(result.get('budget')) or {}
    projection = _first_mapping(result.get('projection')) or {}
    breakdown = _first_mapping(result.get('breakdown')) or {}
    raw_items = breakdown.get('items')
    items = raw_items if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)) else []
    raw_group_by = str(breakdown.get('group_by') or 'agrupador')
    group_by = raw_group_by.replace('_', ' ')
    currency = str(totals.get('moneda') or 'MXN')

    actual_total = totals.get('gasto_total', 0)
    registros = int(totals.get('registros') or 0)
    budget_total = budget.get('budget_total')
    variance = budget.get('variance_amount')
    variance_pct = _format_pct(budget.get('variance_pct'))
    projected_total = projection.get('projected_total')
    run_rate_daily = projection.get('run_rate_daily')

    lines: list[str] = [str(title)]
    if period.get('from') or period.get('to'):
        lines.append(
            f"\nPeriodo: {period.get('from') or 'inicio no definido'} "
            f"a {period.get('to') or 'fin no definido'}."
        )

    lines.append(
        f"\nRespuesta corta: encontré {registros} movimientos por "
        f"{_format_money(actual_total, currency=currency)} en el periodo consultado."
    )

    if budget_total is not None:
        budget_line = (
            f"Presupuesto/base comparativa: {_format_money(budget_total, currency=currency)}; "
            f"variación: {_format_money(variance, currency=currency)}"
        )
        if variance_pct:
            budget_line += f" ({variance_pct})."
        else:
            budget_line += "."
        lines.append(f"\nLectura presupuestal: {budget_line}")
    else:
        lines.append(
            "\nLectura presupuestal: no tengo presupuesto/base comparativa "
            "en el payload de esta consulta."
        )

    if projected_total is not None:
        lines.append(
            f"\nProyección run-rate: {_format_money(projected_total, currency=currency)} "
            f"para el cierre del periodo."
        )
        if run_rate_daily is not None:
            lines.append(
                f"Run-rate diario observado: {_format_money(run_rate_daily, currency=currency)}."
            )

    if items:
        lines.append(f"\nPrincipales grupos por {group_by}:")
        for item in list(items)[:6]:
            if not isinstance(item, Mapping):
                continue
            label = (
                item.get(raw_group_by)
                or item.get('label')
                or item.get('name')
                or item.get('key')
                or '(sin nombre)'
            )
            monto = item.get('monto', 0)
            n = item.get('registros')
            suffix = f" · {int(n)} registros" if n is not None else ""
            lines.append(f"- {label}: {_format_money(monto, currency=currency)}{suffix}")

    comparison = result.get('comparison_yoy')
    if isinstance(comparison, Sequence) and not isinstance(comparison, (str, bytes)) and comparison:
        lines.append("\nComparativo histórico:")
        for item in list(comparison)[:3]:
            if not isinstance(item, Mapping):
                continue
            item_period = _first_mapping(item.get('period')) or {}
            delta_pct = _format_pct(item.get('delta_vs_current_pct'))
            delta_text = _format_money(item.get('delta_vs_current_amount'), currency=currency)
            line = (
                f"- {item_period.get('from') or 'periodo previo'} "
                f"a {item_period.get('to') or 'n/a'}: "
                f"{_format_money(item.get('total'), currency=currency)}; "
                f"diferencia vs actual {delta_text}"
            )
            if delta_pct:
                line += f" ({delta_pct})"
            lines.append(line)

    lines.append(
        "\nFaltantes / cautelas: la proyección es derivada de registros actuales; "
        "si faltan gastos, pagos, CFDI o partidas por capturar, el cierre real puede cambiar."
    )
    lines.append("Frontera de autoridad: esto es lectura y análisis; no ejecuté cambios ni registré movimientos.")
    lines.append(
        "Trazabilidad: respuesta generada directamente desde finance_realtime_report, "
        "sin depender de una segunda respuesta del proveedor."
    )
    return '\n'.join(lines)

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

    if tool_name == 'finance_realtime_report' and (
        isinstance(result.get('totals'), Mapping)
        or isinstance(result.get('projection'), Mapping)
        or isinstance(result.get('breakdown'), Mapping)
    ):
        return _render_finance_realtime_report(result)

    if _has_structured_signal(result):
        return _render_structured_result(tool_name, result)

    return None
