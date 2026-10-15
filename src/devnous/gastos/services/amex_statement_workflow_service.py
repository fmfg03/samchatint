"""Statement navigation over canonical complete-INFORME accounting owners."""

from hashlib import sha256
from decimal import Decimal
from datetime import date, datetime
import json
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import Date, cast, exists, false, or_, select

from ..models import (
    AmexAccountingCut,
    AmexCardAccount,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    AmexReportBankAssociation,
    AmexStatementImport,
    AmexStatementImportItem,
    AmexStatementCutLink,
    Documento,
    Empleado,
    ExpenseReport,
)
from .amex_attention_policy import load_amex_attention_context
from .amex_accounting_cut_service import (
    confirm_amex_approved_informe_cut,
    _approved_informe_preview,
    preview_amex_approved_informe,
)
from .amex_cut_export_service import cut_expense_cfdis
from .amex_expense_service import FINANCE_AMEX_ROLES, company_amex_sql_condition
from .amex_report_evidence_service import build_amex_report_source_snapshot
from .amex_recognition_service import lock_recognition
from .coi_poliza_exporter import (
    generate_coi_poliza_csv,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)


class WorkflowConflict(ValueError):
    """Safe, explicit workflow validation failure; caller rolls back writes."""


async def _authorize(session: Any, actor: Any) -> Any:
    stored = await session.get(Empleado, actor.id)
    if (
        not stored
        or not stored.activo
        or str(stored.rol).lower() not in FINANCE_AMEX_ROLES
    ):
        raise WorkflowConflict("Se requiere un perfil activo de Finanzas.")
    return stored


async def _statement(session: Any, import_id: UUID, *, locked: bool = False) -> Any:
    query = select(AmexStatementImport).where(AmexStatementImport.id == import_id, AmexStatementImport.workflow_mode == "legacy")
    if locked:
        query = query.with_for_update()
    result = (await session.execute(query)).scalar_one_or_none()
    if result is None:
        raise WorkflowConflict("Estado de cuenta no encontrado.")
    return result


def _selection(values: Any) -> list[str]:
    try:
        ids = [str(UUID(str(value))) for value in values]
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkflowConflict("Fuentes seleccionadas inválidas.") from exc
    if not ids or len(ids) != len(set(ids)):
        raise WorkflowConflict("Selecciona fuentes explícitas sin duplicados.")
    return sorted(ids)


async def _sources(session: Any, values: Any) -> list[Any]:
    ids = _selection(values)
    docs = list(
        (
            await session.execute(
                select(Documento).where(
                    Documento.id.in_([UUID(value) for value in ids]),
                    Documento.tipo == "INFORME",
                    Documento.estado == "aprobado",
                )
            )
        )
        .scalars()
        .all()
    )
    if len(docs) != len(ids):
        raise WorkflowConflict("Falta informe aprobado en las fuentes seleccionadas.")
    context = await load_amex_attention_context(session, documents=docs)
    if any(not context.document_decisions[doc.id].visible for doc in docs):
        raise WorkflowConflict("Este informe se atiende desde su Cuenta de gastos.")
    return docs


async def _list_statement_workflows(
    session: Any,
    *,
    actor: Any,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    await _authorize(session, actor)
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
        or isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size < 1
    ):
        raise WorkflowConflict("Selecciona una página válida.")
    page_size = min(page_size, 50)
    imports = (
        (
            await session.execute(
                select(AmexStatementImport)
                .where(AmexStatementImport.workflow_mode == "legacy")
                .order_by(
                    AmexStatementImport.created_at.desc(), AmexStatementImport.id.desc()
                )
                .limit(page_size + 1)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )
    result = []
    for row in imports[:page_size]:
        view = await _statement_workflow_view(session, row.id, actor=actor)
        result.append(
            {
                "import": row,
                "import_id": row.id,
                "items_count": row.total_rows,
                "imported_count": row.imported_count,
                "skipped_count": row.skipped_count,
                "card": view["card"],
                "linked_cut_count": len(view["links"]),
                **{
                    key: view[key]
                    for key in (
                        "imported_totals",
                        "totals_unknown_count",
                        "imported_total",
                        "imported_currency",
                        "omitted_count",
                        "operational_pending_count",
                        "bank_pending_count",
                        "bank_linked_count",
                        "financial_progress",
                    )
                },
            }
        )
    return {
        "rows": result,
        "page": page,
        "page_size": page_size,
        "has_next": len(imports) > page_size,
        "has_previous": page > 1,
    }


def _statement_report_eligibility(statement: Any, card: Any) -> Any:
    """One same-partida contextual predicate for discovery and new GET choices."""
    if (
        card is None
        or card.id != statement.card_account_id
        or not str(card.last4 or "").isdigit()
        or len(str(card.last4)) != 4
        or not isinstance(statement.period_start, date)
        or not isinstance(statement.period_end, date)
        or isinstance(statement.period_start, datetime)
        or isinstance(statement.period_end, datetime)
        or statement.period_end < statement.period_start
    ):
        return false()
    represented_conflict = (
        exists()
        .where(
            AmexRecognitionRepresentation.expense_id == ExpenseReport.id,
            AmexRecognitionRepresentation.consumption_id
            == AmexRecognitionConsumption.id,
            AmexRecognitionConsumption.card_account_id != card.id,
        )
        .correlate(ExpenseReport)
    )
    anchored_conflict = (
        exists()
        .where(
            or_(
                AmexRecognitionConsumption.source_report_expense_id == ExpenseReport.id,
                AmexRecognitionConsumption.imported_expense_id == ExpenseReport.id,
            ),
            AmexRecognitionConsumption.card_account_id != card.id,
        )
        .correlate(ExpenseReport)
    )
    linked = (
        exists()
        .where(
            ExpenseReport.cuenta_gastos_id == Documento.cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
            company_amex_sql_condition(),
            ExpenseReport.ultimos_4_digitos == card.last4,
            cast(ExpenseReport.fecha, Date).between(
                statement.period_start, statement.period_end
            ),
            ~represented_conflict,
            ~anchored_conflict,
        )
        .correlate(Documento)
    )
    return (
        (Documento.tipo == "INFORME")
        & (Documento.estado == "aprobado")
        & (Documento.cuenta_gastos_id.is_not(None))
        & linked
    )


async def _statement_workflow_view(
    session: Any, import_id: UUID, *, actor: Any, selected_informe_ids: Any = ()
) -> dict:
    await _authorize(session, actor)
    statement = await _statement(session, import_id)
    card = await session.get(AmexCardAccount, statement.card_account_id)
    eligibility = _statement_report_eligibility(statement, card)
    items = list(
        (
            await session.execute(
                select(AmexStatementImportItem)
                .where(AmexStatementImportItem.import_id == import_id)
                .order_by(AmexStatementImportItem.row_number)
            )
        )
        .scalars()
        .all()
    )
    ids = [row.imported_expense_id for row in items if row.imported_expense_id]
    expenses = (
        {
            row.id: row
            for row in (
                await session.execute(
                    select(ExpenseReport).where(ExpenseReport.id.in_(ids))
                )
            )
            .scalars()
            .all()
        }
        if ids
        else {}
    )
    # Bank items are never passed through attention filtering.
    docs = list(
        (await session.execute(select(Documento).where(eligibility))).scalars().all()
    )
    context = await load_amex_attention_context(session, documents=docs)
    reports = [doc for doc in docs if context.document_decisions[doc.id].visible]
    selection_warning = None
    selection_rejected = False
    selected = []
    if selected_informe_ids:
        requested = _selection(selected_informe_ids)
        available = {str(doc.id): doc for doc in reports}
        if not set(requested).issubset(available):
            selection_rejected = True
            selection_warning = (
                "El informe seleccionado ya no cumple la tarjeta, el período "
                "o la atención de este estado. Elige una opción vigente."
            )
        else:
            selected = [available[value] for value in requested]
    previews = [
        {
            "document": doc,
            "preview": await preview_amex_approved_informe(
                session, informe_id=doc.id, actor=actor
            ),
        }
        for doc in selected
    ]
    links = list(
        (
            await session.execute(
                select(AmexStatementCutLink).where(
                    AmexStatementCutLink.import_id == import_id
                )
            )
        )
        .scalars()
        .all()
    )
    cuts = (
        list(
            (
                await session.execute(
                    select(AmexAccountingCut).where(
                        AmexAccountingCut.id.in_([link.cut_id for link in links])
                    )
                )
            )
            .scalars()
            .all()
        )
        if links
        else []
    )
    # A real bank association/report representation, never a navigation cutlink,
    # is evidence of bank linkage.
    headers = (
        list(
            (
                await session.execute(
                    select(AmexRecognitionConsumption).where(
                        AmexRecognitionConsumption.imported_expense_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids
        else []
    )
    header_ids = [header.id for header in headers]
    associations = (
        list(
            (
                await session.execute(
                    select(AmexReportBankAssociation).where(
                        AmexReportBankAssociation.statement_consumption_id.in_(
                            header_ids
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if header_ids
        else []
    )
    representations = (
        list(
            (
                await session.execute(
                    select(AmexRecognitionRepresentation).where(
                        AmexRecognitionRepresentation.consumption_id.in_(header_ids),
                        AmexRecognitionRepresentation.role == "report",
                    )
                )
            )
            .scalars()
            .all()
        )
        if header_ids
        else []
    )
    linked_headers = {row.statement_consumption_id for row in associations} | {
        row.consumption_id for row in representations
    }
    bank_linked = {
        row.imported_expense_id for row in headers if row.id in linked_headers
    }
    item_states: dict[UUID, dict[str, Any]] = {}
    for item in items:
        if item.status != "imported":
            state, reason = "omitido", "Fila omitida: requiere atención de importación."
        elif (
            item.imported_expense_id in expenses
            and expenses[item.imported_expense_id].estado_gasto == "cancelado"
        ):
            state, reason = (
                "cancelado",
                "Cargo importado cancelado; conserva el importe original del estado.",
            )
        elif item.imported_expense_id in bank_linked:
            state, reason = "vinculado", "Asociación bancaria registrada."
        else:
            state, reason = (
                "pendiente",
                (
                    "Falta vínculo bancario explícito; "
                    "no bloquea por sí solo la póliza del informe."
                ),
            )
        item_states[item.id] = {
            "status": state,
            "bank_associated": item.imported_expense_id in bank_linked,
            "reason": reason,
            "action_url": "/admin/gastos/amex/conciliacion",
        }
    if selected:
        extra = list(
            (
                await session.execute(
                    select(AmexAccountingCut).where(
                        AmexAccountingCut.informe_id.in_([doc.id for doc in selected])
                    )
                )
            )
            .scalars()
            .all()
        )
        cuts = list({cut.id: cut for cut in cuts + extra}.values())
    # Resolve only persisted relationships; selected navigation sources are not
    # a bank match and cannot make an orphan bank row appear accounted.
    report_consumption_ids = [row.report_consumption_id for row in associations]
    report_consumptions = (
        list(
            (
                await session.execute(
                    select(AmexRecognitionConsumption).where(
                        AmexRecognitionConsumption.id.in_(report_consumption_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if report_consumption_ids
        else []
    )
    report_ids_by_header: dict[UUID, set[UUID]] = {}
    for row in representations:
        report_ids_by_header.setdefault(row.consumption_id, set()).add(row.expense_id)
    report_by_consumption = {row.id: row for row in report_consumptions}
    for association in associations:
        report_consumption = report_by_consumption.get(
            association.report_consumption_id
        )
        if report_consumption and report_consumption.source_report_expense_id:
            report_ids_by_header.setdefault(
                association.statement_consumption_id, set()
            ).add(report_consumption.source_report_expense_id)
    report_expense_ids = (
        set().union(*report_ids_by_header.values()) if report_ids_by_header else set()
    )
    report_expenses = (
        {
            row.id: row
            for row in (
                await session.execute(
                    select(ExpenseReport).where(
                        ExpenseReport.id.in_(report_expense_ids)
                    )
                )
            )
            .scalars()
            .all()
        }
        if report_expense_ids
        else {}
    )
    source_ids_by_bank: dict[UUID, set[UUID]] = {}
    for header in headers:
        for expense_id in report_ids_by_header.get(header.id, set()):
            expense = report_expenses.get(expense_id)
            if expense and expense.informe_documento_id:
                source_ids_by_bank.setdefault(header.imported_expense_id, set()).add(
                    expense.informe_documento_id
                )
    for expense in expenses.values():
        if expense.informe_documento_id:
            source_ids_by_bank.setdefault(expense.id, set()).add(
                expense.informe_documento_id
            )
    exact_source_ids = (
        set().union(*source_ids_by_bank.values()) if source_ids_by_bank else set()
    )
    source_docs = (
        list(
            (
                await session.execute(
                    select(Documento).where(Documento.id.in_(exact_source_ids))
                )
            )
            .scalars()
            .all()
        )
        if exact_source_ids
        else []
    )
    source_context = await load_amex_attention_context(session, documents=source_docs)
    source_doc_map = {doc.id: doc for doc in source_docs}
    source_previews = {}
    for doc in source_docs:
        if (
            doc.tipo == "INFORME"
            and doc.estado == "aprobado"
            and source_context.document_decisions[doc.id].visible
        ):
            source_previews[doc.id] = await preview_amex_approved_informe(
                session, informe_id=doc.id, actor=actor
            )
    totals: dict[str, Decimal] = {}
    totals_unknown_count = 0
    pending_rows = 0
    for item in items:
        item_state = item_states[item.id]
        item_state["blockers"] = []
        if item_state["status"] == "cancelado":
            item_state["blockers"].append(
                "Cargo importado cancelado: requiere revisión"
            )
        item_state["source_ids"] = sorted(
            str(value)
            for value in source_ids_by_bank.get(item.imported_expense_id, set())
        )
        item_state["by_account"] = False
        if item.status != "imported":
            continue
        # Statement totals come from immutable original row receipts, not the
        # editable expense projection. Missing data never becomes a zero.
        try:
            normalized = item.normalized_json
            amount = Decimal(str(normalized["amount"]))
            if not amount.is_finite():
                raise ValueError("invalid amount")
            currency = str(normalized.get("currency") or "Sin moneda")
            totals[currency] = totals.get(currency, Decimal("0")) + amount
        except (KeyError, TypeError, ValueError, ArithmeticError):
            totals_unknown_count += 1
            item_state["blockers"].append("Falta importe original válido en el estado")
        expense = expenses.get(item.imported_expense_id)
        if expense is None:
            item_state["blockers"].append("Falta evidencia del cargo importado")
        else:
            if not expense.ultimos_4_digitos:
                item_state["blockers"].append("Falta tarjeta explícita")
        source_ids = source_ids_by_bank.get(item.imported_expense_id, set())
        approved_sources = [
            source_doc_map[value]
            for value in source_ids
            if value in source_doc_map
            and source_doc_map[value].tipo == "INFORME"
            and source_doc_map[value].estado == "aprobado"
        ]
        if not approved_sources or len(approved_sources) != len(source_ids):
            item_state["blockers"].append("Falta informe aprobado")
            if expense is not None and not expense.cfdi_report_id:
                item_state["blockers"].append(
                    "Falta evidencia fiscal o tratamiento en el informe"
                )
        else:
            for doc in approved_sources:
                if not source_context.document_decisions[doc.id].visible:
                    item_state["by_account"] = True
                    continue
                preview = source_previews[doc.id]
                if preview.status != "ready":
                    item_state["blockers"].append(
                        preview.reason or "Falta revisión del informe"
                    )
                else:
                    covered = any(
                        cut.informe_id == doc.id
                        and cut.snapshot_json.get("finance_confirmation_payload_hash")
                        and cut.snapshot_json.get("review_versions")
                        == preview.current_versions
                        and {
                            part["expense_id"]
                            for part in cut.snapshot_json.get("partidas", [])
                        }
                        == {str(part.id) for part in preview.expenses}
                        and all(
                            part.id in preview.fresh_review_ids
                            for part in preview.expenses
                        )
                        and all(
                            next(
                                (
                                    part.get("report_source_key")
                                    for part in cut.snapshot_json.get("partidas", [])
                                    if part["expense_id"] == str(exp.id)
                                ),
                                None,
                            )
                            == build_amex_report_source_snapshot([exp])["source_key"]
                            for exp in preview.expenses
                        )
                        for cut in cuts
                    )
                    item_state["blockers"].extend(
                        str(reason)
                        for reason in preview.exceptions.values()
                        if not (
                            covered and reason == "missing_amex_operational_attestation"
                        )
                    )
        item_state["blockers"] = list(dict.fromkeys(item_state["blockers"]))
        if item_state["blockers"]:
            pending_rows += 1
            item_state["reason"] += " " + "; ".join(item_state["blockers"]) + "."
    linked_source_ids = {
        cut.informe_id for cut in cuts if cut.id in {link.cut_id for link in links}
    }
    selected_ids = {doc.id for doc in selected}
    # Successful receipts retain the explicit declaration, including sources
    # still awaiting their individual confirmation. Validate it before counting.
    cut_by_id = {cut.id: cut for cut in cuts}
    for link in links:
        cut = cut_by_id.get(link.cut_id)
        payload = link.request_payload_json
        try:
            canonical, digest = _payload(
                import_id,
                link.cut_id,
                payload["selected_informe_ids"],
                payload["reason"],
                payload["mode"],
            )
            if (
                cut is not None
                and canonical == payload
                and digest == link.request_payload_hash
                and str(cut.informe_id) in payload["selected_informe_ids"]
                and payload["mode"] in {"confirm", "incorporate"}
            ):
                selected_ids.update(
                    UUID(value) for value in payload["selected_informe_ids"]
                )
        except (KeyError, TypeError, AttributeError, WorkflowConflict):
            pass  # Malformed evidence is not a denominator or proof of coverage.
    progress = {
        "linked_cut_count": len(links),
        "linked_informe_count": len(linked_source_ids),
        "selected_source_count": len(selected_ids) if selected_ids else None,
        "selected_generated_count": (
            len(selected_ids & linked_source_ids) if selected_ids else None
        ),
        "coverage_unknown": any(
            item.status == "imported"
            and not source_ids_by_bank.get(item.imported_expense_id)
            for item in items
        ),
    }
    if not selected and selected_ids and not selection_rejected:
        saved = list(
            (
                await session.execute(
                    select(Documento).where(Documento.id.in_(selected_ids))
                )
            )
            .scalars()
            .all()
        )
        saved_context = await load_amex_attention_context(session, documents=saved)
        selected = [
            doc for doc in saved if saved_context.document_decisions[doc.id].visible
        ]
        previews = [
            {
                "document": doc,
                "preview": await preview_amex_approved_informe(
                    session, informe_id=doc.id, actor=actor
                ),
            }
            for doc in selected
        ]
        if selected:
            saved_cuts = list(
                (
                    await session.execute(
                        select(AmexAccountingCut).where(
                            AmexAccountingCut.informe_id.in_(
                                [doc.id for doc in selected]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            cuts = list({cut.id: cut for cut in cuts + saved_cuts}.values())
    blockers = []
    if statement.skipped_count:
        blockers.append("Importación parcial: revisa las filas omitidas.")
    if not selected and not selected_ids:
        blockers.append("Falta informe aprobado: selecciona una fuente explícita.")
    return {
        "import": statement,
        "items": items,
        "expenses": expenses,
        "reports": reports,
        "attention_decisions": context.document_decisions,
        "selected_reports": previews,
        "cuts": cuts,
        "card": card,
        "selection_warning": selection_warning,
        "selection_rejected": selection_rejected,
        "item_states": item_states,
        "imported_totals": totals,
        "totals_unknown_count": totals_unknown_count,
        "imported_total": (
            next(iter(totals.values()))
            if len(totals) == 1 and not totals_unknown_count
            else None
        ),
        "imported_currency": next(iter(totals)) if len(totals) == 1 else None,
        "omitted_count": statement.skipped_count,
        "operational_pending_count": pending_rows,
        "bank_pending_count": sum(
            item.status == "imported" and item.imported_expense_id not in bank_linked
            for item in items
        ),
        "bank_linked_count": sum(
            item.status == "imported" and item.imported_expense_id in bank_linked
            for item in items
        ),
        "financial_progress": progress,
        "declared_informe_ids": sorted(str(value) for value in selected_ids),
        "selected_informe_ids": [doc.id for doc in selected],
        "linked_cut_ids": {link.cut_id for link in links},
        "links": links,
        "warnings": list(context.warnings)
        + (
            ["Falta tarjeta o período declarado válido para ofrecer informes."]
            if card is None or str(eligibility) == "false"
            else []
        ),
        "blockers": blockers,
    }


def _payload(
    import_id: UUID, cut_id: UUID, selected: Any, reason: str, mode: str
) -> tuple[dict, str]:
    payload = {
        "version": 1,
        "import_id": str(import_id),
        "cut_id": str(cut_id),
        "selected_informe_ids": _selection(selected),
        "reason": reason.strip(),
        "mode": mode,
    }
    digest = sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return payload, digest


async def _link(
    session: Any,
    *,
    import_id: UUID,
    cut: Any,
    actor: Any,
    selected_informe_ids: Any,
    reason: str,
    mode: str,
) -> Any:
    payload, digest = _payload(import_id, cut.id, selected_informe_ids, reason, mode)
    old = (
        await session.execute(
            select(AmexStatementCutLink).where(
                AmexStatementCutLink.import_id == import_id,
                AmexStatementCutLink.cut_id == cut.id,
            )
        )
    ).scalar_one_or_none()
    if old:
        if old.request_payload_hash != digest or old.request_payload_json != payload:
            raise WorkflowConflict(
                "El vínculo existente tiene otra declaración de fuentes."
            )
        return old
    link = AmexStatementCutLink(
        import_id=import_id,
        cut_id=cut.id,
        actor_id=actor.id,
        request_payload_hash=digest,
        request_payload_json=payload,
    )
    session.add(link)
    await session.flush()
    return link


async def confirm_statement_informe(
    session: Any,
    *,
    import_id: UUID,
    informe_id: UUID,
    actor: Any,
    selected_informe_ids: Any,
    **kwargs: Any,
) -> Any:
    """Savepoint encloses canonical journal and FK receipt; no partial commit."""
    stored = await _authorize(session, actor)
    async with session.begin_nested():
        await _statement(session, import_id, locked=True)
        docs = await _sources(session, selected_informe_ids)
        if informe_id not in {doc.id for doc in docs}:
            raise WorkflowConflict("El informe no pertenece a las fuentes declaradas.")
        result = await confirm_amex_approved_informe_cut(
            session, informe_id=informe_id, actor=stored, **kwargs
        )
        if result.status in {"created", "exists"} and result.cut is not None:
            await _link(
                session,
                import_id=import_id,
                cut=result.cut,
                actor=stored,
                selected_informe_ids=selected_informe_ids,
                reason=str(kwargs.get("reason") or ""),
                mode="confirm",
            )
        return result


async def incorporate_statement_cut(
    session: Any,
    *,
    import_id: UUID,
    cut_id: UUID,
    actor: Any,
    selected_informe_ids: Any,
    confirmed: bool,
    reason: str = "",
) -> Any:
    """Explicit reuse requires current complete coverage, never another posting."""
    stored = await _authorize(session, actor)
    if not confirmed:
        raise WorkflowConflict("Confirma la incorporación del corte.")
    async with session.begin_nested():
        await _statement(session, import_id, locked=True)
        docs = await _sources(session, selected_informe_ids)
        cut = await session.get(AmexAccountingCut, cut_id)
        if cut is None or cut.informe_id not in {doc.id for doc in docs}:
            raise WorkflowConflict("El corte no pertenece a las fuentes declaradas.")
        # First preserve successful exact replay, including original actor/date.
        payload, digest = _payload(
            import_id, cut.id, selected_informe_ids, reason, "incorporate"
        )
        old = (
            await session.execute(
                select(AmexStatementCutLink).where(
                    AmexStatementCutLink.import_id == import_id,
                    AmexStatementCutLink.cut_id == cut.id,
                )
            )
        ).scalar_one_or_none()
        if old:
            if (
                old.request_payload_hash != digest
                or old.request_payload_json != payload
            ):
                raise WorkflowConflict(
                    "El vínculo existente tiene otra declaración de fuentes."
                )
            return old
        await lock_recognition(session)
        preview = await _approved_informe_preview(
            session, informe_id=cut.informe_id, actor=stored, locked=True
        )
        frozen = {
            row["expense_id"]: row for row in cut.snapshot_json.get("partidas", [])
        }
        if preview.status != "ready" or set(frozen) != {
            str(e.id) for e in preview.expenses
        }:
            raise WorkflowConflict("El informe requiere revisión o ajuste de su corte.")
        for expense in preview.expenses:
            row = frozen[str(expense.id)]
            key = build_amex_report_source_snapshot([expense])["source_key"]
            if (
                row.get("report_source_key") != key
                or cut.snapshot_json.get("review_versions") != preview.current_versions
                or expense.id not in preview.fresh_review_ids
            ):
                raise WorkflowConflict(
                    "El informe requiere revisión o ajuste de su corte."
                )
        cut_expense_cfdis(cut)
        return await _link(
            session,
            import_id=import_id,
            cut=cut,
            actor=stored,
            selected_informe_ids=selected_informe_ids,
            reason=reason,
            mode="incorporate",
        )


async def _export_statement(
    session: Any,
    *,
    import_id: UUID,
    actor: Any,
    export_format: str,
    cut_ids: Any = None,
) -> bytes:
    """Fail closed across requested frozen full-report policies, without writes."""
    await _authorize(session, actor)
    await _statement(session, import_id)
    generators: dict[str, Callable[[Any], bytes]] = {
        "csv": generate_coi_poliza_csv,
        "xlsx": generate_coi_poliza_xlsx,
        "zip": generate_coi_poliza_zip,
    }
    if export_format not in generators:
        raise WorkflowConflict("Formato no disponible.")
    links = list(
        (
            await session.execute(
                select(AmexStatementCutLink).where(
                    AmexStatementCutLink.import_id == import_id
                )
            )
        )
        .scalars()
        .all()
    )
    available = {str(link.cut_id) for link in links}
    chosen = _selection(cut_ids) if cut_ids is not None else sorted(available)
    if not chosen or not set(chosen).issubset(available):
        raise WorkflowConflict("Selecciona cortes vinculados a este estado.")
    cuts = list(
        (
            await session.execute(
                select(AmexAccountingCut).where(
                    AmexAccountingCut.id.in_([UUID(value) for value in chosen])
                )
            )
        )
        .scalars()
        .all()
    )
    if len(cuts) != len(chosen):
        raise WorkflowConflict("Falta evidencia de un corte seleccionado.")
    link_by_cut = {str(link.cut_id): link for link in links}
    items = []
    for cut in sorted(cuts, key=lambda row: str(row.id)):
        link = link_by_cut[str(cut.id)]
        payload = link.request_payload_json
        try:
            canonical, digest = _payload(
                import_id,
                cut.id,
                payload["selected_informe_ids"],
                payload["reason"],
                payload["mode"],
            )
            valid = (
                canonical == payload
                and digest == link.request_payload_hash
                and str(cut.informe_id) in payload["selected_informe_ids"]
                and payload["mode"] in {"confirm", "incorporate"}
            )
        except (KeyError, TypeError, AttributeError, WorkflowConflict):
            valid = False
        if not valid:
            raise WorkflowConflict("El vínculo contiene evidencia inválida.")
        items.extend(cut_expense_cfdis(cut))
    return generators[export_format](items)


async def list_statement_workflows(session: Any, **kwargs: Any) -> dict[str, Any]:
    """Read-only dashboard loading."""
    with session.no_autoflush:
        return await _list_statement_workflows(session, **kwargs)


async def statement_workflow_view(session: Any, import_id: UUID, **kwargs: Any) -> dict:
    """Read-only statement detail loading."""
    with session.no_autoflush:
        return await _statement_workflow_view(session, import_id, **kwargs)


async def export_statement(session: Any, **kwargs: Any) -> bytes:
    """Frozen export never flushes pending ORM mutations."""
    with session.no_autoflush:
        try:
            return await _export_statement(session, **kwargs)
        except WorkflowConflict:
            raise
        except ValueError as exc:
            raise WorkflowConflict(
                "No se puede exportar: un corte contiene evidencia inválida."
            ) from exc
