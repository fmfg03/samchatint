from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import or_, select

from devnous.gastos.models import BudgetConcept, ProveedorCliente, Tournament

from .bi_scope import text_matches_bi_scope
from .capability_negotiation import capability_registry_hash
from .context import AssistantContext

DRAFT_KEY = "assistant_receipt_workflow_draft"


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _metadata(conversation: Any) -> dict[str, Any]:
    value = getattr(conversation, "metadata_", None)
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def start_receipt_draft(
    *,
    conversation: Any,
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = _metadata(conversation)
    media = metadata.get("assistant_last_media")
    if not isinstance(media, dict):
        media = {}
    evidence_sha256 = str(intake.get("evidence_sha256") or "")
    media_hash = str(media.get("evidence_sha256") or "")
    if media_hash and evidence_sha256 and media_hash != evidence_sha256:
        raise ValueError("receipt intake evidence does not match uploaded media")
    module_context = metadata.get("module_context")
    if not isinstance(module_context, dict):
        module_context = {}
    entities = intake.get("entities")
    if not isinstance(entities, Mapping):
        entities = {}
    draft = {
        "draft_id": f"receiptdraft_{str(intake.get('intake_id') or '')}",
        "intake_id": str(intake.get("intake_id") or ""),
        "registry_hash": capability_registry_hash(),
        "evidence_sha256": evidence_sha256 or media_hash,
        "media_id": str(media.get("id") or ""),
        "amount": entities.get("amount"),
        "date": entities.get("date"),
        "concept": entities.get("concept"),
        "merchant": entities.get("merchant"),
        "currency": entities.get("currency") or "MXN",
        "tournament_id": module_context.get("tournament_id"),
        "tournament_name": module_context.get("tournament_name")
        or module_context.get("torneo")
        or module_context.get("proyecto"),
        "payment_method": module_context.get("payment_method")
        or module_context.get("metodo_pago"),
        "account_type": module_context.get("account_type")
        or module_context.get("tipo_cuenta"),
        "payment_subject_type": None,
        "provider_id": None,
        "provider_name": None,
        "budget_concept_id": module_context.get("budget_concept_id"),
        "budget_concept_name": module_context.get("budget_concept_name"),
    }
    metadata[DRAFT_KEY] = draft
    conversation.metadata_ = metadata
    return draft


def _missing_fields(draft: Mapping[str, Any]) -> list[str]:
    common = ["amount", "concept", "tournament_id", "payment_subject_type"]
    if draft.get("payment_subject_type") == "personal":
        common.extend(["date", "payment_method", "account_type"])
    elif draft.get("payment_subject_type") == "third_party":
        common.extend(["provider_id", "budget_concept_id"])
    return [field for field in common if not draft.get(field)]


def _prompt_for_missing(draft: Mapping[str, Any], missing: list[str]) -> str:
    if "payment_subject_type" in missing:
        return (
            "Recibí el comprobante. Antes de preparar el borrador, indica si es "
            "un gasto personal/reembolso o un pago a un tercero."
        )
    labels = {
        "amount": "importe",
        "date": "fecha del gasto",
        "concept": "concepto",
        "tournament_id": "torneo/proyecto exacto",
        "payment_method": "método de pago",
        "account_type": "tipo de cuenta: local, viaje, nacional o extranjero",
        "provider_id": "proveedor registrado exacto",
        "budget_concept_id": "partida presupuestal exacta",
    }
    readable = [labels.get(item, item) for item in missing]
    return "Faltan estos datos para preparar el preview: " + ", ".join(readable) + "."


def _proposal_payload(draft: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: draft.get(key)
        for key in (
            "amount",
            "date",
            "concept",
            "currency",
            "tournament_id",
            "payment_method",
            "account_type",
            "provider_id",
            "budget_concept_id",
            "media_id",
            "evidence_sha256",
        )
        if draft.get(key) not in (None, "", [], {})
    }


def _preview(draft: Mapping[str, Any]) -> str:
    subject = str(draft.get("payment_subject_type") or "")
    lines = ["Preparé este borrador:", ""]
    if subject == "personal":
        lines.append("- Flujo: gasto personal, Cuenta de Gastos y solicitud personal")
        lines.append(f"- Tipo de cuenta: {draft.get('account_type')}")
        lines.append(f"- Método de pago: {draft.get('payment_method')}")
    else:
        lines.append("- Flujo: solicitud de pago a tercero")
        lines.append("- Cuenta de Gastos: no aplica para pagos a terceros")
        lines.append(
            f"- Proveedor: {draft.get('provider_name') or draft.get('provider_id')}"
        )
        lines.append(
            "- Partida presupuestal: "
            f"{draft.get('budget_concept_name') or draft.get('budget_concept_id')}"
        )
    lines.extend(
        [
            f"- Proyecto: {draft.get('tournament_name') or draft.get('tournament_id')}",
            f"- Concepto: {draft.get('concept')}",
            f"- Importe: {draft.get('amount')} {draft.get('currency') or 'MXN'}",
            f"- Fecha: {draft.get('date') or 'no aplica'}",
            "",
            "Responde «confirmo» para registrar este borrador o «cancela» para descartarlo.",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class ReceiptDraftAdvance:
    message: str
    pending: Optional[tuple[str, dict[str, Any], str]] = None
    canceled: bool = False


async def advance_receipt_draft(
    *,
    raw_message: str,
    conversation: Any,
    employee_id: Any,
    session: Any,
    writes_enabled: bool = True,
    bi_year: Optional[int] = None,
    bi_scope: Optional[str] = None,
) -> Optional[ReceiptDraftAdvance]:
    metadata = _metadata(conversation)
    draft = metadata.get(DRAFT_KEY)
    if not isinstance(draft, dict):
        return None
    normalized = _normalize(raw_message)
    if normalized in {
        "cancela comprobante",
        "cancela el comprobante",
        "descarta comprobante",
    }:
        metadata.pop(DRAFT_KEY, None)
        conversation.metadata_ = metadata
        await session.commit()
        return ReceiptDraftAdvance(
            message="Descarté el borrador del comprobante. No registré cambios.",
            canceled=True,
        )

    changed = False
    if any(
        token in normalized
        for token in ("personal", "reembolso", "lo pague", "yo pague")
    ):
        if draft.get("payment_subject_type") != "personal":
            draft["payment_subject_type"] = "personal"
            changed = True
    if any(token in normalized for token in ("tercero", "proveedor", "pago directo")):
        if draft.get("payment_subject_type") != "third_party":
            draft["payment_subject_type"] = "third_party"
            changed = True

    for account_type in ("local", "viaje", "nacional", "extranjero"):
        if re.search(rf"\b{account_type}\b", normalized):
            if draft.get("account_type") != account_type:
                draft["account_type"] = account_type
                changed = True
            break

    payment_methods = {
        "efectivo": "Efectivo",
        "tarjeta personal": "Tarjeta Personal",
        "tarjeta de empresa": "Tarjeta de Empresa",
        "transferencia": "Transferencia",
    }
    for token, value in payment_methods.items():
        if token in normalized and draft.get("payment_method") != value:
            draft["payment_method"] = value
            changed = True
            break

    tournaments = (
        (await session.execute(select(Tournament).where(Tournament.active == True)))
        .scalars()
        .all()
    )
    selected_tournament = None
    for tournament in tournaments:
        name = _normalize(str(getattr(tournament, "name", "") or ""))
        if name and name in normalized:
            selected_tournament = tournament
            break

    normalized_scope = str(bi_scope or "").strip().lower()
    if selected_tournament is None and normalized_scope not in {"", "all"}:
        scope_candidates = [
            tournament
            for tournament in tournaments
            if text_matches_bi_scope(
                str(getattr(tournament, "name", "") or ""),
                normalized_scope,
            )
        ]
        if bi_year is not None:
            year_pattern = re.compile(rf"\b{int(bi_year)}\b")
            year_candidates = [
                tournament
                for tournament in scope_candidates
                if year_pattern.search(
                    _normalize(str(getattr(tournament, "name", "") or ""))
                )
            ]
            if year_candidates:
                scope_candidates = year_candidates
        if len(scope_candidates) == 1:
            selected_tournament = scope_candidates[0]

    if selected_tournament is not None and str(draft.get("tournament_id") or "") != str(
        selected_tournament.id
    ):
        draft["tournament_id"] = str(selected_tournament.id)
        draft["tournament_name"] = selected_tournament.name
        changed = True

    if draft.get("payment_subject_type") == "third_party":
        providers = (
            (
                await session.execute(
                    select(ProveedorCliente).where(ProveedorCliente.activo == True)
                )
            )
            .scalars()
            .all()
        )
        for provider in providers:
            name = _normalize(str(getattr(provider, "nombre", "") or ""))
            if name and name in normalized:
                if str(draft.get("provider_id") or "") != str(provider.id):
                    draft["provider_id"] = str(provider.id)
                    draft["provider_name"] = provider.nombre
                    changed = True
                break

        tournament_id = str(draft.get("tournament_id") or "")
        if tournament_id:
            try:
                tournament_uuid = UUID(tournament_id)
            except ValueError:
                tournament_uuid = None
            if tournament_uuid is not None:
                budget_concepts = (
                    (
                        await session.execute(
                            select(BudgetConcept).where(
                                BudgetConcept.active == True,
                                or_(
                                    BudgetConcept.tournament_id == tournament_uuid,
                                    BudgetConcept.tournament_id.is_(None),
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                matches = [
                    concept
                    for concept in budget_concepts
                    if _normalize(str(getattr(concept, "concept_name", "") or ""))
                    in normalized
                ]
                if len(matches) == 1:
                    concept = matches[0]
                    if str(draft.get("budget_concept_id") or "") != str(concept.id):
                        draft["budget_concept_id"] = str(concept.id)
                        draft["budget_concept_name"] = concept.concept_name
                        changed = True

    if not changed and not any(
        token in normalized for token in ("continua", "prepara", "borrador")
    ):
        return None

    draft["registry_hash"] = capability_registry_hash()
    metadata[DRAFT_KEY] = draft
    conversation.metadata_ = metadata
    missing = _missing_fields(draft)
    if missing:
        await session.commit()
        return ReceiptDraftAdvance(message=_prompt_for_missing(draft, missing))

    payload = _proposal_payload(draft)
    proposal_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    payload["proposal_hash"] = proposal_hash
    action = (
        "expenses.create_personal_receipt_workflow"
        if draft.get("payment_subject_type") == "personal"
        else "expenses.create_third_party_receipt_workflow"
    )
    tool_args = {
        "action": action,
        "context": AssistantContext(
            responsible_user_id=str(employee_id),
            tournament_id=str(draft.get("tournament_id")),
            tournament_name=str(draft.get("tournament_name") or ""),
        ).to_dict(),
        "payload": payload,
        "__capability_binding": {
            "registry_hash": draft.get("registry_hash"),
            "evidence_sha256": draft.get("evidence_sha256"),
            "proposal_hash": proposal_hash,
        },
    }
    preview = _preview(draft)
    metadata.pop(DRAFT_KEY, None)
    conversation.metadata_ = metadata
    await session.commit()
    if not writes_enabled:
        preview = preview.rsplit("\n", 1)[0]
        preview += (
            "\nEl registro automático no está habilitado para tu usuario; "
            "este preview no registró cambios."
        )
        return ReceiptDraftAdvance(message=preview)
    return ReceiptDraftAdvance(
        message=preview,
        pending=("assistant_canonical_action", tool_args, preview),
    )
