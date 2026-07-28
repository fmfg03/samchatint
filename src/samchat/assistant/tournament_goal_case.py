"""Persistent, resumable orchestration for the tournament goal shadow."""

from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Tournament

from .analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_WAITING_CONTEXT,
    CASE_WRITE_POLICY,
    AnalystCase,
    AnalystCaseVersion,
)
from .analyst_case_store import (
    AnalystCaseStore,
    AnalystCaseStoreError,
    version_id_for,
)
from .tournament_goal_shadow import (
    TournamentGoalShadow,
    TournamentSnapshot,
    ValidationFinding,
    build_tournament_goal_shadow as build_shadow_contract,
)
from .tournament_goal_source import inspect_tournament_source
from .tournament_case_pointer import set_active_tournament_case_pointer


class TournamentGoalCaseError(ValueError):
    """Raised when a shadow case cannot be created or resumed safely."""


class TournamentGoalCaseForbiddenError(TournamentGoalCaseError):
    """Raised when a caller tries to resume another user's case."""


class TournamentGoalCaseNotFoundError(TournamentGoalCaseError):
    """Raised when an explicit case id does not exist."""


def _case_id(
    *,
    employee_id: str,
    conversation_id: str,
    source_id: str,
    goal: str,
    target_name: str,
) -> str:
    normalized_goal = " ".join(goal.split()).casefold()
    normalized_target = " ".join(target_name.split()).casefold()
    raw = "|".join(
        (
            employee_id,
            conversation_id,
            source_id,
            normalized_goal,
            normalized_target,
            "tournament_goal_shadow_v1",
        )
    )
    return f"analyst_case_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"


def _shadow_status(shadow: TournamentGoalShadow) -> str:
    if shadow.validation.valid and not shadow.missing_information:
        return CASE_STATUS_ANALYZED
    return CASE_STATUS_WAITING_CONTEXT


def _next_questions(shadow: TournamentGoalShadow) -> list[str]:
    questions: list[str] = []
    for finding in shadow.validation.findings:
        if finding.severity == "error":
            questions.append(finding.message)
    component_questions = {
        "rich_tournament_dates": "¿Cuáles serán las fechas clave del torneo nuevo?",
        "rich_tournament_config": "¿Qué reglas y configuración enriquecida deben definirse para el torneo nuevo?",
        "matches_and_schedule": "¿El calendario se creará después o existe una plantilla aprobada para reutilizar?",
        "media": "¿Qué recursos de media deben asociarse al torneo nuevo?",
        "communications": "¿Qué plantillas de comunicación deben prepararse para el torneo nuevo?",
    }
    for component in shadow.source.unavailable_components:
        question = component_questions.get(
            component,
            f"¿Cómo debe resolverse el componente no reutilizable `{component}`?",
        )
        questions.append(question)
    return list(dict.fromkeys(questions))


def _render_answer(shadow: TournamentGoalShadow) -> str:
    validation = shadow.validation
    lines = [
        "Preparé un borrador inerte para crear el torneo.",
        f"Torneo base: {shadow.source.name}.",
        f"Propuesta: {shadow.draft.name or 'nombre pendiente'}.",
        f"Cambios visibles: {shadow.business_diff.change_count}.",
        (
            "Validación: metadatos locales listos; quedan componentes por definir."
            if validation.valid and shadow.missing_information
            else (
                "Validación: lista para revisión."
                if validation.valid
                else f"Validación: {validation.error_count} bloqueo(s) por resolver."
            )
        ),
        "No se creó ni modificó ningún torneo operativo.",
    ]
    return "\n".join(lines)


def _evidence(
    source_payload: Dict[str, Any], shadow: TournamentGoalShadow
) -> list[Dict[str, Any]]:
    return [
        {
            "kind": "tournament_source_snapshot",
            "source_namespace": "local_postgresql",
            "source_id": str(source_payload["project"]["id"]),
            "source_hash": source_payload["source_hash"],
            "unavailable_components": list(source_payload["unavailable_components"]),
            "domain_write_performed": False,
        },
        {
            "kind": "tournament_goal_work_product",
            "work_product_hash": shadow.work_product_hash,
            "draft_hash": shadow.draft.draft_hash,
            "execution_status": "not_executed",
            "operational_writes": False,
        },
    ]


def _answer_contract(
    *,
    source_payload: Dict[str, Any],
    shadow: TournamentGoalShadow,
) -> Dict[str, Any]:
    payload = shadow.to_dict()
    return {
        "schema_version": "goal_to_outcome_v1",
        "kind": "tournament_goal_shadow",
        "source_authority": source_payload,
        **payload,
        "operational_writes": False,
    }


def _public_response(case: AnalystCase) -> Dict[str, Any]:
    version = case.versions[-1]
    contract = dict(version.answer_contract or {})
    return {
        "case_id": case.case_id,
        "case_version": version.version_number,
        "plan": contract.get("plan") or {},
        "source": {
            "authority": contract.get("source_authority") or {},
            "bound_snapshot": contract.get("source") or {},
        },
        "draft": contract.get("draft") or {},
        "validation": contract.get("validation") or {},
        "diff": contract.get("business_diff") or {},
        "answer": case.current_answer,
        "next_questions": list(case.next_questions),
        "missing_information": list(contract.get("missing_information") or []),
        "operational_writes": False,
    }


def _create_case(
    sync_session: Any,
    *,
    case_id: str,
    employee_id: str,
    role: str,
    goal: str,
    source_payload: Dict[str, Any],
    shadow: TournamentGoalShadow,
) -> AnalystCase:
    status = _shadow_status(shadow)
    answer = _render_answer(shadow)
    evidence = _evidence(source_payload, shadow)
    contract = _answer_contract(source_payload=source_payload, shadow=shadow)
    timestamp = datetime.now(timezone.utc).isoformat()
    version = AnalystCaseVersion(
        version_id=version_id_for(case_id, 1),
        version_number=1,
        created_at=timestamp,
        created_by=employee_id,
        status=status,
        answer=answer,
        evidence=evidence,
        next_questions=_next_questions(shadow),
        suggested_routes=[],
        caveats=["Borrador de stage shadow; no concede autoridad operativa."],
        answer_contract=contract,
        changed_fields=["case_created"],
    )
    case = AnalystCase(
        case_id=case_id,
        user_id=employee_id,
        role=role,
        question=goal,
        analyst_intent={
            "kind": "tournament_goal_shadow",
            "source_tournament_id": shadow.source.tournament_id,
        },
        status=status,
        evidence=evidence,
        current_answer=answer,
        next_questions=_next_questions(shadow),
        suggested_routes=[],
        caveats=["Borrador de stage shadow; no concede autoridad operativa."],
        versions=[version],
        writes_policy=dict(CASE_WRITE_POLICY),
    )
    return AnalystCaseStore(sync_session).create_case(case)


def _persist_case(
    sync_session: Any,
    *,
    case_id: str,
    employee_id: str,
    role: str,
    goal: str,
    source_payload: Dict[str, Any],
    shadow: TournamentGoalShadow,
    explicit_resume: bool,
    expected_case_version: Optional[int],
) -> AnalystCase:
    store = AnalystCaseStore(sync_session)
    existing = store.get_case(case_id)
    if existing is None:
        if explicit_resume:
            raise TournamentGoalCaseNotFoundError("Tournament goal case was not found")
        return _create_case(
            sync_session,
            case_id=case_id,
            employee_id=employee_id,
            role=role,
            goal=goal,
            source_payload=source_payload,
            shadow=shadow,
        )
    if existing.user_id != employee_id:
        raise TournamentGoalCaseForbiddenError(
            "Tournament goal case belongs to another user"
        )
    existing_source_id = str(existing.analyst_intent.get("source_tournament_id") or "")
    if existing_source_id != shadow.source.tournament_id:
        raise TournamentGoalCaseError(
            "A tournament goal case cannot change its source tournament"
        )

    contract = _answer_contract(source_payload=source_payload, shadow=shadow)
    latest = existing.versions[-1]
    if latest.answer_contract == contract:
        return existing

    try:
        return store.update_case(
            case_id,
            status=_shadow_status(shadow),
            current_answer=_render_answer(shadow),
            evidence=_evidence(source_payload, shadow),
            next_questions=_next_questions(shadow),
            suggested_routes=[],
            caveats=["Borrador de stage shadow; no concede autoridad operativa."],
            answer_contract=contract,
            expected_version_number=(
                expected_case_version
                if expected_case_version is not None
                else latest.version_number
            ),
            updated_by=employee_id,
        )
    except AnalystCaseStoreError as exc:
        if "Stale AnalystCase version" not in str(exc):
            raise
        refreshed = store.get_case(case_id)
        if refreshed is not None and refreshed.versions[-1].answer_contract == contract:
            return refreshed
        raise


async def _target_name_finding(
    session: AsyncSession,
    *,
    target_name: str,
    source_id: str,
) -> Optional[ValidationFinding]:
    normalized = str(target_name or "").strip()
    if not normalized:
        return None
    result = await session.execute(
        select(Tournament.id).where(
            func.lower(func.trim(Tournament.name)) == normalized.casefold(),
            Tournament.id != uuid.UUID(source_id),
        )
    )
    if result.first() is None:
        return None
    return ValidationFinding(
        code="name_already_exists",
        severity="error",
        field="name",
        message="Ya existe un torneo local con el nombre propuesto.",
    )


async def build_tournament_goal_shadow(
    session: AsyncSession,
    *,
    goal: str,
    source_tournament_id: Optional[str] = None,
    source_tournament_name: Optional[str] = None,
    case_id: Optional[str] = None,
    expected_case_version: Optional[int] = None,
    target_name: Optional[str] = None,
    description: Optional[str] = None,
    active: Optional[bool] = None,
    display_order: Optional[int] = None,
    account: Optional[str] = None,
    etapas: Optional[list[str]] = None,
    categorias: Optional[list[str]] = None,
    visibility_departments: Optional[list[str]] = None,
    current_employee_id: Optional[str] = None,
    current_role: Optional[str] = None,
    current_conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and persist one inert tournament goal case version."""

    employee_id = str(current_employee_id or "").strip()
    role = str(current_role or "").strip()
    conversation_id = str(current_conversation_id or "").strip()
    clean_goal = str(goal or "").strip()
    if not employee_id or not role or not conversation_id:
        raise TournamentGoalCaseError(
            "Trusted assistant identity and conversation are required"
        )
    if not clean_goal:
        raise TournamentGoalCaseError("Goal is required")
    explicit_case_id = str(case_id or "").strip()
    if explicit_case_id and not re.fullmatch(
        r"analyst_case_[0-9a-f]{32}",
        explicit_case_id,
    ):
        raise TournamentGoalCaseError("Invalid tournament goal case_id")
    if expected_case_version is not None and expected_case_version < 1:
        raise TournamentGoalCaseError("expected_case_version must be positive")

    source_authority = await inspect_tournament_source(
        session,
        tournament_id=source_tournament_id,
        tournament_name=source_tournament_name,
    )
    source_payload = source_authority.model_dump(mode="json")
    project = source_authority.project
    source = TournamentSnapshot.from_mapping(
        {
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "active": project.active,
            "display_order": project.display_order,
            "accounting_account": project.cuenta_contable_relacionada,
            "stages": project.etapas,
            "categories": project.categorias,
            "visibility_areas": project.form_visibility_departments,
            "updated_at": (
                project.updated_at.isoformat() if project.updated_at else None
            ),
            "source_authority_hash": source_authority.source_hash,
            "unavailable_components": source_authority.unavailable_components,
        }
    )
    overrides: Dict[str, Any] = {}
    remapped: Mapping[str, tuple[str, Any]] = {
        "description": ("description", description),
        "active": ("active", active),
        "display_order": ("display_order", display_order),
        "account": ("accounting_account", account),
        "etapas": ("stages", etapas),
        "categorias": ("categories", categorias),
        "visibility_departments": ("visibility_areas", visibility_departments),
    }
    for _argument, (field_name, value) in remapped.items():
        if value is not None:
            overrides[field_name] = value

    target_name_finding = await _target_name_finding(
        session,
        target_name=str(target_name or ""),
        source_id=source.tournament_id,
    )
    shadow = build_shadow_contract(
        source,
        requested_name=str(target_name or ""),
        overrides=overrides,
        goal=clean_goal,
        additional_findings=(
            (target_name_finding,) if target_name_finding is not None else ()
        ),
    )
    resolved_case_id = explicit_case_id or _case_id(
        employee_id=employee_id,
        conversation_id=conversation_id,
        source_id=source.tournament_id,
        goal=clean_goal,
        target_name=str(target_name or ""),
    )
    try:
        async with session.begin_nested():
            stored = await session.run_sync(
                lambda sync_session: _persist_case(
                    sync_session,
                    case_id=resolved_case_id,
                    employee_id=employee_id,
                    role=role,
                    goal=clean_goal,
                    source_payload=source_payload,
                    shadow=shadow,
                    explicit_resume=bool(explicit_case_id),
                    expected_case_version=expected_case_version,
                )
            )
    except IntegrityError:
        try:
            stored = await session.run_sync(
                lambda sync_session: _persist_case(
                    sync_session,
                    case_id=resolved_case_id,
                    employee_id=employee_id,
                    role=role,
                    goal=clean_goal,
                    source_payload=source_payload,
                    shadow=shadow,
                    explicit_resume=True,
                    expected_case_version=expected_case_version,
                )
            )
        except AnalystCaseStoreError as exc:
            raise TournamentGoalCaseError(str(exc)) from exc
    except AnalystCaseStoreError as exc:
        raise TournamentGoalCaseError(str(exc)) from exc
    response = _public_response(stored)
    await set_active_tournament_case_pointer(
        session,
        conversation_id=conversation_id,
        employee_id=employee_id,
        case_id=stored.case_id,
        case_version=response["case_version"],
        status="drafting",
    )
    return response


__all__ = [
    "TournamentGoalCaseError",
    "TournamentGoalCaseForbiddenError",
    "TournamentGoalCaseNotFoundError",
    "build_tournament_goal_shadow",
]
