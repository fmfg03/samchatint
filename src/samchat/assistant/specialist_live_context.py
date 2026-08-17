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


def build_specialist_preview_diagnostics(
    *,
    task_id: str,
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a read-only operational diagnosis from preview context.

    This is intentionally deterministic. It does not decide authority or execute
    work; it only tells the user whether the preview has enough evidence to keep
    preparing the case.
    """

    matched = bool(live_context.get("matched"))
    unresolved = live_context.get("unresolved") or {}
    documents = list(live_context.get("documents") or [])
    expenses = list(live_context.get("expenses") or [])
    cfdis = list(live_context.get("cfdis") or [])
    findings: List[str] = []
    missing: List[str] = []
    risks: List[str] = []
    next_steps: List[str] = []

    if matched:
        findings.append(
            "SamChat encontro contexto operativo para las referencias del mensaje."
        )
    elif live_context.get("live_lookup_performed"):
        missing.append("No se encontraron registros live para las referencias detectadas.")
    else:
        missing.append("No hubo consulta live suficiente; solo hay contexto del mensaje.")

    if documents:
        findings.append(f"Documentos encontrados: {len(documents)}.")
    if expenses:
        findings.append(f"Gastos encontrados: {len(expenses)}.")
    if cfdis:
        findings.append(f"CFDI encontrados: {len(cfdis)}.")

    if unresolved:
        missing.append(
            "Quedaron referencias sin resolver: "
            + "; ".join(
                f"{key}={', '.join(str(value) for value in values)}"
                for key, values in unresolved.items()
            )
        )

    domains = set(_as_list(understood_context.get("domains")))
    if "cxc" in domains and not cfdis:
        missing.append("Para CxC falta identificar al menos un CFDI emitido.")
    if "amex" in domains and not (documents or expenses):
        missing.append("Para AMEX falta ubicar informe, gasto o referencia operativa.")
    if "torneo" in domains and not (documents or expenses or cfdis):
        missing.append("Para torneo falta un registro operativo ligado al caso.")

    if len(documents) > 1:
        risks.append(
            "Hay multiples documentos; revisar que la referencia correcta sea "
            "la intencion del usuario."
        )
    if len(cfdis) > 1:
        risks.append("Hay multiples CFDI; evitar asumir uno sin confirmacion.")
    terminal_states = {"pagado", "cerrado", "liquidado", "aplicado"}
    if any(
        str(doc.get("estado") or "").lower() in terminal_states
        for doc in documents
    ):
        risks.append(
            "Hay documento en estado terminal; cualquier accion futura requiere "
            "frontera de autoridad reforzada."
        )
    if not risks:
        risks.append("No se detectaron riesgos deterministas adicionales en el contexto disponible.")

    if missing:
        readiness = "needs_more_context"
        next_steps.append("Pedir o seleccionar la referencia faltante antes de preparar acciones.")
    else:
        readiness = "ready_for_read_only_preview"
        next_steps.append(
            "Continuar con preview/diff read-only; mantener ejecucion bloqueada "
            "hasta aprobacion humana."
        )

    return {
        "source": "deterministic_preview_diagnostics",
        "task_id": task_id,
        "authority": "read_only_diagnostic",
        "readiness": readiness,
        "findings": findings,
        "missing": missing,
        "risks": risks,
        "next_steps": next_steps,
        "writes_attempted": False,
    }


def render_specialist_preview_diagnostics_markdown(
    diagnostics: Mapping[str, Any]
) -> str:
    """Render deterministic preview diagnostics for assistant messages."""

    readiness = diagnostics.get("readiness") or "unknown"
    lines = ["## Diagnostico operativo", ""]
    lines.append(f"- Estado: {readiness}.")
    for label, key in (
        ("Hallazgos", "findings"),
        ("Faltantes", "missing"),
        ("Riesgos", "risks"),
        ("Siguiente paso", "next_steps"),
    ):
        values = list(diagnostics.get(key) or [])
        if not values:
            continue
        lines.append(f"- {label}:")
        for value in values:
            lines.append(f"  - {value}")
    lines.append(
        "- Alcance: diagnostico read-only; no crea autoridad ni ejecuta cambios."
    )
    return "\n".join(lines) + "\n"


def build_specialist_preview_workspace_cards(
    *,
    task_id: str,
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    preview_render: Mapping[str, Any],
    memory_context: Mapping[str, Any] | None = None,
    continuity_context: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build structured cards for the future assistant operator workspace UI."""

    cards: List[Dict[str, Any]] = []
    cards.append(
        {
            "card_id": "understood_context",
            "title": "Contexto entendido",
            "kind": "context",
            "status": "available",
            "authority": understood_context.get("authority", "context_hint_only"),
            "summary": "Referencias y dominios detectados en el mensaje del usuario.",
            "data": dict(understood_context),
        }
    )
    live_status = live_context.get("status") or "unknown"
    cards.append(
        {
            "card_id": "live_context",
            "title": "Contexto encontrado",
            "kind": "evidence",
            "status": live_status,
            "authority": live_context.get("authority", "read_only_context"),
            "summary": (
                "Coincidencias encontradas en SamChat."
                if live_context.get("matched")
                else "Sin coincidencias live suficientes."
            ),
            "data": dict(live_context),
        }
    )
    readiness = diagnostics.get("readiness") or "unknown"
    if continuity_context is not None:
        continuity_status = continuity_context.get("status") or "unknown"
        cards.append(
            {
                "card_id": "case_continuity",
                "title": "Continuidad del caso",
                "kind": "continuity",
                "status": continuity_status,
                "authority": continuity_context.get("authority", "read_only_continuity"),
                "summary": (
                    "Caso activo ligado a esta conversacion."
                    if continuity_context.get("matched")
                    else "Sin caso activo ligado a esta conversacion."
                ),
                "data": dict(continuity_context),
            }
        )

    if memory_context is not None:
        memory_status = memory_context.get("status") or "unknown"
        snippets = list(memory_context.get("snippets") or [])
        cards.append(
            {
                "card_id": "case_memory",
                "title": "Memoria de casos",
                "kind": "memory",
                "status": memory_status,
                "authority": memory_context.get("authority", "read_only_memory"),
                "summary": (
                    "Precedentes operativos encontrados para orientar la revision."
                    if memory_context.get("matched")
                    else "Sin precedentes deterministas suficientes."
                ),
                "data": {
                    **dict(memory_context),
                    "snippet_count": len(snippets),
                },
            }
        )

    cards.append(
        {
            "card_id": "operational_diagnostics",
            "title": "Diagnostico operativo",
            "kind": "diagnostic",
            "status": readiness,
            "authority": diagnostics.get("authority", "read_only_diagnostic"),
            "summary": "Lectura deterministica de hallazgos, faltantes, riesgos y siguiente paso.",
            "data": dict(diagnostics),
        }
    )
    cards.append(
        {
            "card_id": "business_preview",
            "title": "Preview especialista",
            "kind": "preview",
            "status": preview_render.get("execution_status", "not_executed"),
            "authority": "preview_only",
            "summary": f"Preview deterministico para {task_id}.",
            "data": dict(preview_render),
        }
    )
    cards.append(
        {
            "card_id": "authority_boundary",
            "title": "Frontera de autoridad",
            "kind": "authority",
            "status": "blocked",
            "authority": "human_approval_required",
            "summary": "No se ejecutan acciones ni se crean registros desde este preview.",
            "data": {
                "primary_action_enabled": False,
                "writes_attempted": False,
                "provider_called": False,
                "required_before_writes": [
                    "preview exacto",
                    "aprobacion humana",
                    "idempotency key",
                    "audit trail",
                ],
            },
        }
    )
    return cards


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
