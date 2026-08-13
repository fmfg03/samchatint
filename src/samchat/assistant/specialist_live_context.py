"""Read-only live context resolver for specialist assistant previews."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from sqlalchemy import func, or_, select

from devnous.gastos.models import CFDIReport, Documento, ExpenseReport

_MAX_ROWS_PER_KIND = 5


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _document_snapshot(documento: Documento) -> Dict[str, Any]:
    return {
        "id": str(documento.id),
        "numero_referencia": _string(getattr(documento, "numero_referencia", None)),
        "tipo": _string(getattr(documento, "tipo", None)),
        "estado": _string(getattr(documento, "estado", None)),
        "referencia_operaciones": _string(
            getattr(documento, "referencia_operaciones", None)
        ),
        "referencia_base": _string(getattr(documento, "referencia_base", None)),
        "monto_solicitado": _amount(getattr(documento, "monto_solicitado", None)),
        "monto_total": _amount(getattr(documento, "monto_total", None)),
        "currency": _string(getattr(documento, "currency", None)),
        "concepto_pago": _string(getattr(documento, "concepto_pago", None)),
        "cuenta_gastos_id": _string(getattr(documento, "cuenta_gastos_id", None)),
        "cfdi_uuid_manual": _string(getattr(documento, "cfdi_uuid_manual", None)),
        "cfdi_report_id": _string(getattr(documento, "cfdi_report_id", None)),
    }


def _expense_snapshot(expense: ExpenseReport) -> Dict[str, Any]:
    return {
        "id": str(expense.id),
        "numero_referencia": _string(getattr(expense, "numero_referencia", None)),
        "concepto": _string(getattr(expense, "concepto", None)),
        "proyecto": _string(getattr(expense, "proyecto", None)),
        "departamento": _string(getattr(expense, "departamento", None)),
        "fase_torneo": _string(getattr(expense, "fase_torneo", None)),
        "fecha": _date(getattr(expense, "fecha", None)),
        "monto": _amount(getattr(expense, "gasto_cantidad", None)),
        "currency": _string(getattr(expense, "currency", None)),
        "estado_gasto": _string(getattr(expense, "estado_gasto", None)),
        "estado_factura": _string(getattr(expense, "estado_factura", None)),
        "referencia_base": _string(getattr(expense, "referencia_base", None)),
        "cuenta_gastos_id": _string(getattr(expense, "cuenta_gastos_id", None)),
        "cfdi_uuid_manual": _string(getattr(expense, "cfdi_uuid_manual", None)),
        "cfdi_report_id": _string(getattr(expense, "cfdi_report_id", None)),
        "pagado_con_amex_empresa": getattr(
            expense, "pagado_con_amex_empresa", None
        ),
    }


def _cfdi_snapshot(cfdi: CFDIReport) -> Dict[str, Any]:
    return {
        "id": str(cfdi.id),
        "cfdi_uuid": _string(getattr(cfdi, "cfdi_uuid", None)),
        "fecha": _date(getattr(cfdi, "fecha", None)),
        "emisor_rfc": _string(getattr(cfdi, "emisor_rfc", None)),
        "emisor_nombre": _string(getattr(cfdi, "emisor_nombre", None)),
        "receptor_rfc": _string(getattr(cfdi, "receptor_rfc", None)),
        "receptor_nombre": _string(getattr(cfdi, "receptor_nombre", None)),
        "total": _amount(getattr(cfdi, "total", None)),
        "subtotal": _amount(getattr(cfdi, "subtotal", None)),
        "tipo_de_comprobante": _string(
            getattr(cfdi, "tipo_de_comprobante", None)
        ),
        "serie": _string(getattr(cfdi, "serie", None)),
        "folio": _string(getattr(cfdi, "folio", None)),
    }


def _scalars(result: Any) -> Sequence[Any]:
    scalars = result.scalars()
    all_rows = getattr(scalars, "all", None)
    if callable(all_rows):
        return all_rows()
    return list(scalars)


async def _execute_scalars(session: Any, statement: Any) -> Sequence[Any]:
    result = await session.execute(statement)
    return _scalars(result)


def _dedupe_snapshots(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("id") or row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:_MAX_ROWS_PER_KIND]


async def resolve_specialist_preview_live_context(
    session: Any, understood_context: Mapping[str, Any]
) -> Dict[str, Any]:
    """Resolve user-mentioned references against SamChat data, read-only.

    The resolver is intentionally non-authoritative. It enriches the preview
    with context found in SamChat but it never authorizes writes or changes the
    specialist preview decision.
    """

    base: Dict[str, Any] = {
        "source": "samchat_db",
        "live_lookup_performed": False,
        "authority": "read_only_context",
        "matched": False,
        "documents": [],
        "expenses": [],
        "cfdis": [],
        "unresolved": {},
        "limits": {"max_rows_per_kind": _MAX_ROWS_PER_KIND},
    }
    if not hasattr(session, "execute"):
        base["status"] = "skipped_no_db_session"
        return base

    document_refs = [ref.upper() for ref in _as_list(understood_context.get("document_refs"))]
    operation_refs = _as_list(understood_context.get("operations_refs"))
    uuid_prefixes = [ref.upper() for ref in _as_list(understood_context.get("uuid_or_prefixes"))]

    if not document_refs and not operation_refs and not uuid_prefixes:
        base["status"] = "skipped_no_reference_hints"
        return base

    base["live_lookup_performed"] = True
    unresolved_documents = set(document_refs)
    unresolved_operations = set(operation_refs)
    unresolved_uuids = set(uuid_prefixes)
    document_rows: List[Dict[str, Any]] = []
    expense_rows: List[Dict[str, Any]] = []
    cfdi_rows: List[Dict[str, Any]] = []

    try:
        s_or_i_refs = [ref for ref in document_refs if not ref.startswith("O-")]
        o_refs = [ref for ref in document_refs if ref.startswith("O-")]

        if s_or_i_refs:
            rows = await _execute_scalars(
                session,
                select(Documento)
                .where(func.upper(Documento.numero_referencia).in_(s_or_i_refs))
                .limit(_MAX_ROWS_PER_KIND),
            )
            for row in rows:
                snap = _document_snapshot(row)
                document_rows.append(snap)
                if snap.get("numero_referencia"):
                    unresolved_documents.discard(str(snap["numero_referencia"]).upper())

        if o_refs:
            rows = await _execute_scalars(
                session,
                select(ExpenseReport)
                .where(func.upper(ExpenseReport.numero_referencia).in_(o_refs))
                .limit(_MAX_ROWS_PER_KIND),
            )
            for row in rows:
                snap = _expense_snapshot(row)
                expense_rows.append(snap)
                if snap.get("numero_referencia"):
                    unresolved_documents.discard(str(snap["numero_referencia"]).upper())

        for ref in operation_refs:
            rows = await _execute_scalars(
                session,
                select(Documento)
                .where(Documento.referencia_operaciones == ref)
                .limit(_MAX_ROWS_PER_KIND),
            )
            if rows:
                unresolved_operations.discard(ref)
            document_rows.extend(_document_snapshot(row) for row in rows)

        for prefix in uuid_prefixes:
            cfdi_matches = await _execute_scalars(
                session,
                select(CFDIReport)
                .where(func.upper(CFDIReport.cfdi_uuid).like(f"{prefix}%"))
                .limit(_MAX_ROWS_PER_KIND),
            )
            expense_matches = await _execute_scalars(
                session,
                select(ExpenseReport)
                .where(func.upper(ExpenseReport.cfdi_uuid_manual).like(f"{prefix}%"))
                .limit(_MAX_ROWS_PER_KIND),
            )
            document_matches = await _execute_scalars(
                session,
                select(Documento)
                .where(
                    or_(
                        func.upper(Documento.cfdi_uuid_manual).like(f"{prefix}%"),
                        func.upper(Documento.numero_referencia).like(f"{prefix}%"),
                    )
                )
                .limit(_MAX_ROWS_PER_KIND),
            )
            if cfdi_matches or expense_matches or document_matches:
                unresolved_uuids.discard(prefix)
            cfdi_rows.extend(_cfdi_snapshot(row) for row in cfdi_matches)
            expense_rows.extend(_expense_snapshot(row) for row in expense_matches)
            document_rows.extend(_document_snapshot(row) for row in document_matches)
    except Exception as exc:  # pragma: no cover - defensive live lookup boundary
        base["status"] = "lookup_error"
        base["error_type"] = type(exc).__name__
        return base

    base["documents"] = _dedupe_snapshots(document_rows)
    base["expenses"] = _dedupe_snapshots(expense_rows)
    base["cfdis"] = _dedupe_snapshots(cfdi_rows)
    base["matched"] = bool(base["documents"] or base["expenses"] or base["cfdis"])
    unresolved: Dict[str, Any] = {}
    if unresolved_documents:
        unresolved["document_refs"] = sorted(unresolved_documents)
    if unresolved_operations:
        unresolved["operations_refs"] = sorted(unresolved_operations)
    if unresolved_uuids:
        unresolved["uuid_or_prefixes"] = sorted(unresolved_uuids)
    base["unresolved"] = unresolved
    base["status"] = "matched" if base["matched"] else "no_matches"
    return base


def _format_money(value: Any) -> str:
    amount = _amount(value)
    if amount is None:
        return "?"
    return f"${amount:,.2f}"


def render_specialist_live_context_markdown(live_context: Mapping[str, Any]) -> str:
    """Render read-only live context for assistant preview messages."""

    lines = ["## Contexto encontrado", ""]
    status = live_context.get("status")
    if not live_context.get("live_lookup_performed"):
        if status == "skipped_no_reference_hints":
            lines.append("- No hubo referencias suficientes para consultar SamChat.")
        elif status == "skipped_no_db_session":
            lines.append("- Lookup live omitido: no hay sesion de BD disponible en este contexto.")
        else:
            lines.append("- Lookup live omitido.")
    elif status == "lookup_error":
        lines.append(
            f"- No pude resolver el contexto live por un error read-only ({live_context.get('error_type', 'unknown')})."
        )
    elif not live_context.get("matched"):
        lines.append("- Consulte SamChat, pero no encontre coincidencias para esas referencias.")
    else:
        for doc in live_context.get("documents") or []:
            lines.append(
                "- Documento "
                f"{doc.get('numero_referencia') or '-'} | {doc.get('tipo') or '-'} | "
                f"estado {doc.get('estado') or '-'} | REF {doc.get('referencia_operaciones') or '-'} | "
                f"monto {_format_money(doc.get('monto_solicitado') if doc.get('monto_solicitado') is not None else doc.get('monto_total'))}"
            )
        for expense in live_context.get("expenses") or []:
            lines.append(
                "- Gasto "
                f"{expense.get('numero_referencia') or '-'} | {expense.get('concepto') or '-'} | "
                f"{_format_money(expense.get('monto'))} | CFDI {expense.get('cfdi_uuid_manual') or '-'}"
            )
        for cfdi in live_context.get("cfdis") or []:
            lines.append(
                "- CFDI "
                f"{cfdi.get('cfdi_uuid') or '-'} | {cfdi.get('emisor_nombre') or cfdi.get('emisor_rfc') or '-'} | "
                f"{_format_money(cfdi.get('total'))} | tipo {cfdi.get('tipo_de_comprobante') or '-'}"
            )
    unresolved = live_context.get("unresolved") or {}
    if unresolved:
        pending = []
        for key, values in unresolved.items():
            pending.append(f"{key}: {', '.join(str(value) for value in values)}")
        lines.append(f"- Sin resolver: {'; '.join(pending)}")
    lines.append(
        "- Alcance: consulta read-only; no autoriza ni ejecuta cambios."
    )
    return "\n".join(lines) + "\n"
