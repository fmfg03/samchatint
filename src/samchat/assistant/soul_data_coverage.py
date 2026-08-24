"""Read-only coverage checks for SOUL and needs-data-first artifacts.

This module intentionally does not mutate SamChat state. It gives the assistant
a deterministic way to explain whether the Owner/operations knowledge layer is
ready, partial, or blocked by missing data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


SOUL_DATA_COVERAGE_ONLY = "soul_data_coverage_only"

READY = "ready"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"
SOURCE_MISSING = "source_missing"

BLOCKER = "blocker"
WARNING = "warning"


@dataclass(frozen=True)
class CoverageFinding:
    code: str
    label: str
    severity: str = BLOCKER
    detail: str | None = None
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactCoverage:
    artifact_id: str
    status: str
    score: float
    summary: str
    findings: tuple[CoverageFinding, ...] = field(default_factory=tuple)
    available_sources: tuple[str, ...] = field(default_factory=tuple)
    next_questions: tuple[str, ...] = field(default_factory=tuple)
    safety: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass(frozen=True)
class SoulDataCoverageReport:
    status: str
    score: float
    artifacts: tuple[ArtifactCoverage, ...]
    executive_summary: str
    read_only: bool = True
    tool_policy: str = SOUL_DATA_COVERAGE_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "executive_summary": self.executive_summary,
            "read_only": self.read_only,
            "tool_policy": self.tool_policy,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = _clean(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out)


def _finding(
    code: str,
    label: str,
    *,
    severity: str = BLOCKER,
    detail: str | None = None,
    remediation: str | None = None,
) -> CoverageFinding:
    return CoverageFinding(
        code=code,
        label=label,
        severity=severity,
        detail=detail,
        remediation=remediation,
    )


def _status_from_findings(findings: Sequence[CoverageFinding], *, has_source: bool = True) -> str:
    if not has_source:
        return SOURCE_MISSING
    if any(f.severity == BLOCKER for f in findings):
        return INSUFFICIENT
    if findings:
        return PARTIAL
    return READY


def _score(findings: Sequence[CoverageFinding], *, total_checks: int, has_source: bool = True) -> float:
    if not has_source or total_checks <= 0:
        return 0.0
    blocker_penalty = sum(1 for f in findings if f.severity == BLOCKER)
    warning_penalty = sum(0.5 for f in findings if f.severity != BLOCKER)
    return max(0.0, round((total_checks - blocker_penalty - warning_penalty) / total_checks, 4))


def _phase_rows(soul: Mapping[str, Any]) -> Sequence[Any]:
    operations = _as_mapping(soul.get("operations"))
    phases = operations.get("phases") or soul.get("phases")
    return _as_sequence(phases)


def _phase_has_date(phase: Any) -> bool:
    row = _as_mapping(phase)
    for key in ("date", "fecha", "start_date", "end_date", "starts_at", "ends_at"):
        if _clean(row.get(key)):
            return True
    return False


def _phase_activity_count(phase: Any) -> int:
    row = _as_mapping(phase)
    for key in ("activities", "actividades", "tasks", "milestones"):
        activities = _as_sequence(row.get(key))
        if activities:
            return len(activities)
    return 0


def evaluate_tournament_soul_snapshot(snapshot: Mapping[str, Any] | None) -> ArtifactCoverage:
    """Evaluate whether a tournament SOUL snapshot has enough operational data."""

    root = _as_mapping(snapshot)
    soul = _as_mapping(root.get("soul"))
    findings: list[CoverageFinding] = []
    sources: list[str] = []

    if not root:
        findings.append(
            _finding(
                "missing_tournament_soul",
                "No hay snapshot SOUL cargado para evaluar torneos.",
                remediation="Seleccionar un torneo vivo o cargar su SOUL antes de pedir carpetas/pack del due?o.",
            )
        )
        return ArtifactCoverage(
            artifact_id="tournament.soul_snapshot",
            status=SOURCE_MISSING,
            score=0.0,
            summary="No hay fuente SOUL disponible.",
            findings=tuple(findings),
            next_questions=("?Qu? torneo quieres evaluar?", "?Ya existe SOUL Wizard para ese torneo?"),
            safety={"read_only": True, "can_answer_owner_pack": False},
        )

    tournament = _as_mapping(root.get("tournament") or soul.get("tournament"))
    tournament_name = _clean(tournament.get("name") or root.get("tournament_name") or soul.get("name"))
    if tournament_name:
        sources.append(f"torneo:{tournament_name}")
    else:
        findings.append(
            _finding(
                "missing_tournament_context",
                "El snapshot no identifica claramente el torneo.",
                remediation="Registrar nombre/slug del torneo en el SOUL.",
            )
        )

    entity_seed = _as_mapping(soul.get("entity_folders_seed"))
    entities = _as_sequence(entity_seed.get("entities") or soul.get("entities"))
    if entities:
        sources.append("entidades")
    else:
        findings.append(
            _finding(
                "missing_entities",
                "No hay entidades/carpeta por entidad en el SOUL.",
                remediation="Cargar entidades participantes y responsable operativo por entidad.",
            )
        )

    operations = _as_mapping(soul.get("operations"))
    categories = _as_sequence(operations.get("categories") or soul.get("categories"))
    if categories:
        sources.append("categorias")
    else:
        findings.append(
            _finding(
                "missing_categories",
                "No hay categor?as/g?neros capturados para el torneo.",
                remediation="Completar categor?as y g?nero/edad esperados en SOUL Wizard.",
            )
        )

    phases = _phase_rows(soul)
    if phases:
        sources.append("fases")
        phases_missing_dates = [str(i + 1) for i, phase in enumerate(phases) if not _phase_has_date(phase)]
        phases_missing_activities = [str(i + 1) for i, phase in enumerate(phases) if _phase_activity_count(phase) == 0]
        if phases_missing_dates:
            findings.append(
                _finding(
                    "missing_dates",
                    "Hay fases sin fecha definida.",
                    detail=f"Fases sin fecha: {', '.join(phases_missing_dates)}.",
                    remediation="Completar fecha de inicio/cierre/final por fase.",
                )
            )
        if phases_missing_activities:
            findings.append(
                _finding(
                    "missing_phase_activities",
                    "Hay fases sin actividades operativas.",
                    detail=f"Fases sin actividades: {', '.join(phases_missing_activities)}.",
                    remediation="Registrar actividades por fase para que el asistente pueda explicar avances y faltantes.",
                )
            )
    else:
        findings.append(
            _finding(
                "missing_phases",
                "No hay fases del torneo capturadas.",
                remediation="Crear fases, fechas y actividades base desde SOUL Wizard.",
            )
        )

    status = _status_from_findings(findings)
    summary = "SOUL completo para preguntas operativas base." if status == READY else "SOUL todav?a no est? listo para responder todo el pack."
    return ArtifactCoverage(
        artifact_id="tournament.soul_snapshot",
        status=status,
        score=_score(findings, total_checks=6),
        summary=summary,
        findings=tuple(findings),
        available_sources=_unique(sources),
        next_questions=(
            "?Qu? torneos deben tener SOUL completo esta semana?",
            "?Qu? entidades y fases faltan por capturar?",
        ),
        safety={"read_only": True, "can_answer_owner_pack": status in {READY, PARTIAL}},
    )


def evaluate_accounting_historical_sources(manifests: Sequence[Mapping[str, Any]] | None = None) -> ArtifactCoverage:
    """Validate whether configured historical COI manifests point to usable sources."""

    if manifests is None:
        try:
            from samchat.accounting_historical.service import SUPPORTED_HISTORICAL_MANIFESTS

            manifests = tuple(SUPPORTED_HISTORICAL_MANIFESTS)
        except Exception:
            manifests = ()

    findings: list[CoverageFinding] = []
    sources: list[str] = []
    manifest_rows = list(manifests or ())
    if not manifest_rows:
        findings.append(
            _finding(
                "missing_historical_coi_manifest",
                "No hay manifiestos hist?ricos COI configurados.",
                remediation="Registrar manifests con balanza y p?lizas hist?ricas antes de usar precedentes contables.",
            )
        )
        return ArtifactCoverage(
            artifact_id="accounting.historical_snapshot",
            status=SOURCE_MISSING,
            score=0.0,
            summary="No hay fuentes hist?ricas COI configuradas.",
            findings=tuple(findings),
            next_questions=("?Qu? ejercicios COI deben ser fuente oficial?",),
            safety={"read_only": True, "can_answer_precedent": False},
        )

    for manifest in manifest_rows:
        row = _as_mapping(manifest)
        label = _clean(row.get("label") or row.get("year") or row.get("source_id") or "manifest")
        trial_balance = _clean(row.get("trial_balance") or row.get("trial_balance_path"))
        policy_headers = _clean(row.get("policy_headers") or row.get("policy_headers_path"))
        policy_lines = _clean(row.get("policy_lines") or row.get("policy_lines_path"))
        if trial_balance:
            sources.append(f"{label}:balanza")
            if not Path(trial_balance).exists():
                findings.append(
                    _finding(
                        "missing_historical_trial_balance",
                        "La balanza hist?rica configurada no existe en disco.",
                        detail=f"{label}: {trial_balance}",
                        remediation="Corregir ruta o cargar la fuente COI oficial.",
                    )
                )
        else:
            findings.append(
                _finding(
                    "missing_historical_trial_balance",
                    "Un manifiesto hist?rico no tiene balanza COI.",
                    detail=label,
                    remediation="Agregar balanza hist?rica para ese ejercicio.",
                )
            )

        missing_policy_paths = [path for path in (policy_headers, policy_lines) if path and not Path(path).exists()]
        if missing_policy_paths:
            findings.append(
                _finding(
                    "unvalidated_historical_policy_source",
                    "Hay p?lizas hist?ricas configuradas que no se pueden validar en disco.",
                    severity=WARNING,
                    detail=f"{label}: {', '.join(missing_policy_paths)}",
                    remediation="Cargar encabezados y movimientos COI o marcar expl?citamente balance-only.",
                )
            )
        if policy_headers or policy_lines:
            sources.append(f"{label}:polizas")

    status = _status_from_findings(findings)
    return ArtifactCoverage(
        artifact_id="accounting.historical_snapshot",
        status=status,
        score=_score(findings, total_checks=max(1, len(manifest_rows) * 2)),
        summary="Fuentes hist?ricas COI listas." if status == READY else "Fuentes hist?ricas COI requieren revisi?n antes de usarse como precedente fuerte.",
        findings=tuple(findings),
        available_sources=_unique(sources),
        next_questions=(
            "?Qu? ejercicios deben entrar al precedente contable?",
            "?Alg?n ejercicio es intencionalmente solo balanza?",
        ),
        safety={"read_only": True, "can_answer_precedent": status in {READY, PARTIAL}},
    )


def evaluate_sam_inbox_payload(payload: Mapping[str, Any] | None) -> ArtifactCoverage:
    """Check whether SAM Inbox payload is safe enough for assistant context."""

    root = _as_mapping(payload)
    findings: list[CoverageFinding] = []
    sources: list[str] = []
    if not root:
        findings.append(
            _finding(
                "source_not_connected",
                "No hay payload de SAM Inbox disponible para evaluar.",
                remediation="Conectar build_sam_inbox_payload con empleado autenticado cuando el asistente necesite inbox vivo.",
            )
        )
        return ArtifactCoverage(
            artifact_id="sam_inbox.payload",
            status=SOURCE_MISSING,
            score=0.0,
            summary="Inbox no conectado a esta evaluaci?n.",
            findings=tuple(findings),
            next_questions=("?Qu? usuario autenticado abre el inbox?",),
            safety={"read_only": True, "can_answer_inbox": False},
        )

    items = list(_as_sequence(root.get("items") or root.get("all_items")))
    if items:
        sources.append("items")
    else:
        findings.append(
            _finding(
                "empty_inbox_payload",
                "El inbox no contiene partidas visibles.",
                severity=WARNING,
                remediation="Confirmar si el usuario no tiene pendientes o si falta fuente viva.",
            )
        )

    source_health = _as_mapping(root.get("source_health"))
    if source_health:
        sources.append("source_health")
        unhealthy = [str(k) for k, v in source_health.items() if _as_mapping(v).get("ok") is False]
        if unhealthy:
            findings.append(
                _finding(
                    "inbox_source_unhealthy",
                    "Hay fuentes del inbox con error.",
                    detail=", ".join(unhealthy),
                    remediation="Resolver fuente antes de usar el inbox como panorama completo.",
                )
            )
    else:
        findings.append(
            _finding(
                "missing_inbox_source_health",
                "El payload no declara salud de fuentes.",
                remediation="Incluir source_health para distinguir dato vac?o vs fuente ca?da.",
            )
        )

    tabs = _as_mapping(root.get("tabs"))
    if tabs:
        sources.append("tabs")
    else:
        findings.append(
            _finding(
                "missing_inbox_tabs",
                "El inbox no declara tabs o agrupadores.",
                severity=WARNING,
                remediation="Conservar tabs para explicar de d?nde viene cada pendiente.",
            )
        )

    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        row = _as_mapping(item)
        key = _clean(row.get("id") or row.get("document_id") or row.get("reference") or row.get("href"))
        if not key:
            continue
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        findings.append(
            _finding(
                "inbox_duplicate_payloads",
                "El inbox trae partidas duplicadas.",
                detail=", ".join(sorted(set(duplicates))[:10]),
                remediation="Deduplicar por documento/origen antes de exponer al asistente.",
            )
        )

    actor = root.get("actor") or root.get("current_user") or root.get("empleado")
    filters = root.get("filters") or root.get("visibility") or root.get("permissions")
    if actor and filters:
        sources.append("actor_visibility")
    else:
        findings.append(
            _finding(
                "inbox_permission_unclear",
                "No queda claro con qu? actor/permisos se construy? el inbox.",
                remediation="Agregar actor y filtros/permisos aplicados para evitar respuestas fuera de visibilidad.",
            )
        )

    status = _status_from_findings(findings)
    return ArtifactCoverage(
        artifact_id="sam_inbox.payload",
        status=status,
        score=_score(findings, total_checks=5),
        summary="Inbox apto para contexto operacional." if status == READY else "Inbox requiere cerrar permisos/deduplicaci?n antes de ser contexto fuerte.",
        findings=tuple(findings),
        available_sources=_unique(sources),
        next_questions=(
            "?El inbox debe contestar por usuario o por rol?",
            "?Qu? tabs deben considerarse fuente oficial para el asistente?",
        ),
        safety={"read_only": True, "can_answer_inbox": status in {READY, PARTIAL}},
    )


def build_soul_data_coverage_report(
    *,
    soul_snapshot: Mapping[str, Any] | None = None,
    sam_inbox_payload: Mapping[str, Any] | None = None,
    historical_manifests: Sequence[Mapping[str, Any]] | None = None,
) -> SoulDataCoverageReport:
    artifacts = (
        evaluate_tournament_soul_snapshot(soul_snapshot),
        evaluate_accounting_historical_sources(historical_manifests),
        evaluate_sam_inbox_payload(sam_inbox_payload),
    )
    blocker_count = sum(1 for artifact in artifacts for finding in artifact.findings if finding.severity == BLOCKER)
    source_missing_count = sum(1 for artifact in artifacts if artifact.status == SOURCE_MISSING)
    if blocker_count or source_missing_count:
        status = INSUFFICIENT
    elif any(artifact.status == PARTIAL for artifact in artifacts):
        status = PARTIAL
    else:
        status = READY
    score = round(sum(artifact.score for artifact in artifacts) / len(artifacts), 4)
    if status == READY:
        summary = "Los artefactos SOUL/datos-base est?n listos para preguntas ejecutivas read-only."
    elif status == PARTIAL:
        summary = "Hay datos suficientes para respuestas parciales, pero con advertencias expl?citas."
    else:
        summary = "Todav?a faltan datos base; el asistente debe declarar faltantes antes de responder como si estuviera completo."
    return SoulDataCoverageReport(status=status, score=score, artifacts=artifacts, executive_summary=summary)


def render_soul_data_coverage_answer(report: SoulDataCoverageReport) -> str:
    """Render a concise Spanish executive answer for the assistant UI."""

    lines = [report.executive_summary, ""]
    for artifact in report.artifacts:
        lines.append(f"- {artifact.artifact_id}: {artifact.status} ({artifact.score:.0%}). {artifact.summary}")
        blockers = [f for f in artifact.findings if f.severity == BLOCKER]
        warnings = [f for f in artifact.findings if f.severity != BLOCKER]
        for finding in blockers[:4]:
            detail = f" {finding.detail}" if finding.detail else ""
            lines.append(f"  - Falta: {finding.label}{detail}")
        for finding in warnings[:2]:
            detail = f" {finding.detail}" if finding.detail else ""
            lines.append(f"  - Advertencia: {finding.label}{detail}")
    if report.status != READY:
        lines.extend([
            "",
            "Respuesta segura: esto no est? listo como pack completo; puedo contestar s?lo las variables respaldadas por fuentes y marcar lo faltante.",
        ])
    lines.append("No ejecut? cambios; esta revisi?n es s?lo lectura.")
    return "\n".join(lines)


__all__ = [
    "ArtifactCoverage",
    "CoverageFinding",
    "SoulDataCoverageReport",
    "build_soul_data_coverage_report",
    "evaluate_accounting_historical_sources",
    "evaluate_sam_inbox_payload",
    "evaluate_tournament_soul_snapshot",
    "render_soul_data_coverage_answer",
]
