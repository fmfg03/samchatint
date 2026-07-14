from __future__ import annotations

import html
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

from .analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_CLOSED,
    CASE_STATUS_OPEN,
    CASE_STATUS_REVIEWED,
    CASE_STATUS_WAITING_CONTEXT,
    AnalystCase,
)
from .analyst_case_store import AnalystCaseStore


@dataclass(frozen=True)
class AnalystWorkspaceListItem:
    case_id: str
    status: str
    role: str
    question: str
    updated_at: str
    evidence_count: int
    version_count: int
    suggested_route_count: int
    has_pending_questions: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystWorkspaceDetail:
    case: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    limits: List[str]
    next_questions: List[str]
    suggested_routes: List[Dict[str, Any]]
    versions: List[Dict[str, Any]]
    available_status_actions: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_case_list_view(
    cases: Iterable[AnalystCase],
) -> List[AnalystWorkspaceListItem]:
    return [_case_list_item(case) for case in cases]


def build_case_detail_view(case: AnalystCase) -> AnalystWorkspaceDetail:
    routes = [_safe_route(route) for route in case.suggested_routes]
    warnings = _workspace_warnings(case, routes)
    return AnalystWorkspaceDetail(
        case={
            "case_id": case.case_id,
            "status": case.status,
            "role": case.role,
            "question": case.question,
            "analyst_intent": dict(case.analyst_intent or {}),
            "current_answer": case.current_answer,
            "writes_policy": dict(case.writes_policy or {}),
        },
        evidence=[dict(item) for item in case.evidence],
        limits=list(case.caveats or []),
        next_questions=list(case.next_questions or []),
        suggested_routes=routes,
        versions=[version.to_dict() for version in case.versions],
        available_status_actions=_available_status_actions(case.status),
        warnings=warnings,
    )


def render_case_list_html(
    items: Iterable[AnalystWorkspaceListItem],
) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{_e(item.case_id)}</td>"
            f"<td>{_e(item.status)}</td>"
            f"<td>{_e(item.role)}</td>"
            f"<td>{_e(item.question)}</td>"
            f"<td>{_e(item.updated_at)}</td>"
            f"<td>{item.evidence_count}</td>"
            f"<td>{item.version_count}</td>"
            f"<td>{item.suggested_route_count}</td>"
            "</tr>"
        )
    body = "".join(rows) or (
        "<tr><td colspan='8'>No hay casos Analyst.</td></tr>"
    )
    return (
        "<section data-analyst-workspace='list'>"
        "<h1>Analyst Workspace</h1>"
        "<table>"
        "<thead><tr>"
        "<th>Caso</th><th>Estado</th><th>Rol</th><th>Pregunta</th>"
        "<th>Actualizado</th><th>Evidencia</th><th>Versiones</th>"
        "<th>Rutas sugeridas</th>"
        "</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
        "</section>"
    )


def render_case_detail_html(detail: AnalystWorkspaceDetail) -> str:
    case = detail.case
    parts = [
        "<section data-analyst-workspace='detail'>",
        f"<h1>{_e(case.get('question'))}</h1>",
        f"<p>Estado: {_e(case.get('status'))}</p>",
        f"<p>Rol: {_e(case.get('role'))}</p>",
        "<h2>Respuesta</h2>",
        f"<p>{_e(case.get('current_answer'))}</p>",
        "<h2>Evidencia</h2>",
        _render_dict_list(detail.evidence, empty="Sin evidencia."),
        "<h2>Límites</h2>",
        _render_text_list(detail.limits, empty="Sin límites registrados."),
        "<h2>Preguntas pendientes</h2>",
        _render_text_list(
            detail.next_questions,
            empty="Sin preguntas pendientes.",
        ),
        "<h2>Rutas sugeridas</h2>",
        _render_routes(detail.suggested_routes),
        "<h2>Historial</h2>",
        _render_versions(detail.versions),
        "<h2>Política de writes</h2>",
        _render_dict(case.get("writes_policy") or {}),
        "<h2>Advertencias</h2>",
        _render_text_list(detail.warnings, empty="Sin advertencias."),
        "</section>",
    ]
    return "".join(parts)


def review_case(
    store: AnalystCaseStore,
    case_id: str,
    *,
    updated_by: str,
) -> AnalystCase:
    return store.update_case(
        case_id,
        status=CASE_STATUS_REVIEWED,
        updated_by=updated_by,
    )


def close_case(
    store: AnalystCaseStore,
    case_id: str,
    *,
    closed_by: str,
) -> AnalystCase:
    return store.update_case(
        case_id,
        status=CASE_STATUS_CLOSED,
        closed_by=closed_by,
    )


def _case_list_item(case: AnalystCase) -> AnalystWorkspaceListItem:
    updated_at = case.versions[-1].created_at if case.versions else ""
    return AnalystWorkspaceListItem(
        case_id=case.case_id,
        status=case.status,
        role=case.role,
        question=case.question,
        updated_at=updated_at,
        evidence_count=len(case.evidence),
        version_count=len(case.versions),
        suggested_route_count=len(case.suggested_routes),
        has_pending_questions=bool(case.next_questions),
    )


def _safe_route(route: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(route)
    current["execution_status"] = "not_executed"
    current["writes_enabled"] = False
    return current


def _workspace_warnings(
    case: AnalystCase,
    routes: Iterable[Dict[str, Any]],
) -> List[str]:
    warnings = [
        "Las rutas sugeridas son propuestas de seguimiento en estado "
        "not_executed.",
        "Los writes operativos permanecen deshabilitados.",
    ]
    if case.writes_policy.get("operational_writes_allowed") is not False:
        warnings.append("La política de writes requiere revisión.")
    for route in routes:
        if route.get("execution_status") != "not_executed":
            warnings.append("Una ruta fue normalizada como no ejecutada.")
    return warnings


def _available_status_actions(status: str) -> List[str]:
    if status == CASE_STATUS_CLOSED:
        return []
    if status in {
        CASE_STATUS_OPEN,
        CASE_STATUS_WAITING_CONTEXT,
        CASE_STATUS_ANALYZED,
    }:
        return ["review", "close"]
    if status == CASE_STATUS_REVIEWED:
        return ["close"]
    return []


def _render_routes(routes: Iterable[Dict[str, Any]]) -> str:
    items = []
    for route in routes:
        label = route.get("label") or route.get("route_id") or "Ruta"
        status = route.get("execution_status") or "not_executed"
        writes_enabled = route.get("writes_enabled")
        items.append(
            "<li>"
            f"{_e(label)} — estado: {_e(status)}; "
            f"writes_enabled={_e(writes_enabled)}"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>" if items else (
        "<p>Sin rutas sugeridas.</p>"
    )


def _render_versions(versions: Iterable[Dict[str, Any]]) -> str:
    items = []
    for version in versions:
        items.append(
            "<li>"
            f"v{_e(version.get('version_number'))} "
            f"{_e(version.get('status'))} "
            f"{_e(version.get('created_at'))}"
            "</li>"
        )
    return "<ol>" + "".join(items) + "</ol>" if items else (
        "<p>Sin historial.</p>"
    )


def _render_text_list(values: Iterable[str], *, empty: str) -> str:
    items = [f"<li>{_e(value)}</li>" for value in values if str(value)]
    return (
        "<ul>" + "".join(items) + "</ul>"
        if items
        else f"<p>{_e(empty)}</p>"
    )


def _render_dict_list(
    values: Iterable[Dict[str, Any]],
    *,
    empty: str,
) -> str:
    items = [f"<li>{_render_dict(value)}</li>" for value in values]
    return (
        "<ul>" + "".join(items) + "</ul>"
        if items
        else f"<p>{_e(empty)}</p>"
    )


def _render_dict(value: Dict[str, Any]) -> str:
    parts = [
        f"{_e(key)}={_e(value[key])}"
        for key in sorted(value)
    ]
    return "; ".join(parts)


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)
