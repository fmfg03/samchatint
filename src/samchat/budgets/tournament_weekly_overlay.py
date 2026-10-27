"""Versioned, tournament-scoped weekly planning over immutable financial truth.

This module never provisions schema. Callers own the transaction and must commit
only after all intended tournaments have been applied and verified. Read paths
never invoke the writer. Historic weekly plans remain untouched.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import load_only, raiseload

from .distribution import money


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Identificador inválido") from None


def _week(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 52:
        raise ValueError("Semana fuera de 1–52")
    return value


async def _schema_ready(session: Any) -> bool:
    result = await session.execute(
        text(
            """SELECT
        to_regclass('public.budget_tournament_weekly_revisions') IS NOT NULL AS revisions,
        to_regclass('public.budget_tournament_weekly_lines') IS NOT NULL AS lines
    """
        )
    )
    row = result.mappings().one()
    if bool(row["revisions"]) != bool(row["lines"]):
        raise ValueError("La migración de distribución está incompleta")
    return bool(row["revisions"])


async def _require_budget_actor(session: Any, actor_empleado_id: str) -> None:
    """Use the same active employee and capabilities as the budget routes."""
    from devnous.gastos.models import Empleado
    from devnous.gastos.routes.admin_routes import _budget_access_map

    actor = await session.scalar(
        select(Empleado)
        .options(
            load_only(
                Empleado.id,
                Empleado.activo,
                Empleado.rol,
                Empleado.departamento,
                Empleado.correo,
            ),
            raiseload("*"),
        )
        .where(Empleado.id == uuid.UUID(actor_empleado_id))
        .execution_options(populate_existing=True)
    )
    if not actor or actor.activo is not True:
        raise PermissionError("El usuario no tiene permisos de aprobación presupuestal")
    access = _budget_access_map(actor)
    if not all(
        access.get(capability) is True
        for capability in ("create", "line_update", "approve")
    ):
        raise PermissionError("El usuario no tiene permisos de aprobación presupuestal")


async def _revision_tip(session: Any, year: int, tournament_id: str) -> str | None:
    rows = (
        (
            await session.execute(
                text(
                    """SELECT r.id::text AS id
        FROM budget_tournament_weekly_revisions r
        WHERE r.edition_year=:year AND r.tournament_id=CAST(:tid AS uuid)
          AND NOT EXISTS (SELECT 1 FROM budget_tournament_weekly_revisions successor
                          WHERE successor.previous_revision_id=r.id)
        """
                ),
                {"year": year, "tid": tournament_id},
            )
        )
        .scalars()
        .all()
    )
    if len(rows) > 1:
        raise ValueError("El historial de distribución tiene ramas incompatibles")
    return str(rows[0]) if rows else None


async def _version(session: Any, version_id: str, *, require_selected: bool) -> dict:
    row = (
        (
            await session.execute(
                text(
                    """SELECT id::text AS id, edition_year, status, source
        FROM budget_versions WHERE id=CAST(:id AS uuid)"""
                ),
                {"id": version_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise ValueError("Versión presupuestal inexistente")
    version = dict(row)
    if require_selected:
        from . import service

        selected = service.resolve_definitive_budget_version_from_versions(
            await service.list_budget_versions(
                session, edition_year=version["edition_year"], ensure_schema=False
            )
        )
        if not selected or str(selected["id"]) != version_id:
            raise ValueError("La versión ya no es la seleccionada para la edición")
        if version["status"] not in {"approved", "frozen"}:
            raise ValueError("La distribución requiere una versión financiera aprobada")
    return version


async def _lines(
    session: Any, version_id: str, tournament_id: str, *, lock: bool = False
) -> list[dict]:
    # A user-editable metadata field alone is never evidence of lineage.
    # Only a canonical distribution revision has a verified parent chain.
    from .distribution import SOURCE, financial_context, revision_context

    context = await revision_context(session, version_id)
    root_version_id, mapping = await financial_context(session, context)
    is_revision = context["source"] == SOURCE
    reverse = {str(target): str(root) for root, target in mapping.items()}
    if len(reverse) != len(mapping):
        raise ValueError("El linaje financiero contiene destinos duplicados")
    statement = """SELECT l.id::text AS source_line_id,l.metadata,
        l.budget_concept_id::text AS budget_concept_id,l.tournament_id::text AS tournament_id,
        l.phase,l.concept_name,l.account_code_final,l.account_code_suggested,
        l.budget_amount,COALESCE(l.line_direction,'expense') AS line_direction
        FROM budget_lines l WHERE l.budget_version_id=CAST(:version_id AS uuid)
          AND l.tournament_id=CAST(:tournament_id AS uuid)
          AND COALESCE(l.line_direction,'expense')='expense'
        ORDER BY l.id""" + (
        " FOR UPDATE OF l" if lock else ""
    )
    rows = (
        (
            await session.execute(
                text(statement),
                {"version_id": version_id, "tournament_id": tournament_id},
            )
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        item = dict(row)
        metadata = item.pop("metadata") or {}
        item["source_line_id"] = _uuid(item["source_line_id"])
        if is_revision:
            financial_id = reverse.get(item["source_line_id"])
            if (
                not financial_id
                or metadata.get("distribution_financial_line_id") != financial_id
                or metadata.get("distribution_financial_version_id") != root_version_id
            ):
                raise ValueError("Linaje financiero de partida no acreditado")
            item["financial_line_id"] = _uuid(financial_id)
        else:
            item["financial_line_id"] = item["source_line_id"]
        item["budget_amount"] = str(money(item["budget_amount"]))
        result.append(item)
    if len({r["financial_line_id"] for r in result}) != len(result):
        raise ValueError("Las partidas tienen linaje financiero duplicado")
    if is_revision:
        root_rows = (
            (
                await session.execute(
                    text(
                        """SELECT l.id::text AS id,
            l.budget_concept_id::text AS budget_concept_id,l.tournament_id::text AS tournament_id,
            l.phase,l.concept_name,l.account_code_final,l.account_code_suggested,
            l.budget_amount,COALESCE(l.line_direction,'expense') AS line_direction
            FROM budget_lines l WHERE l.budget_version_id=CAST(:root AS uuid)
              AND l.tournament_id=CAST(:tid AS uuid)
              AND COALESCE(l.line_direction,'expense')='expense'"""
                    ),
                    {"root": root_version_id, "tid": tournament_id},
                )
            )
            .mappings()
            .all()
        )
        root_by_id = {str(r["id"]): dict(r) for r in root_rows}
        if set(root_by_id) != {r["financial_line_id"] for r in result}:
            raise ValueError("La revisión no conserva el alcance financiero del torneo")
        for item in result:
            original = root_by_id[item["financial_line_id"]]
            original.pop("id")
            original["budget_amount"] = str(money(original["budget_amount"]))
            current = {
                k: v
                for k, v in item.items()
                if k not in {"source_line_id", "financial_line_id"}
            }
            if current != original:
                raise ValueError("La revisión alteró una partida de su raíz financiera")
    return result


def _source_fingerprint(lines: list[dict]) -> str:
    # A copied financial line is equivalent only when its lineage is explicit.
    canonical = [{k: v for k, v in r.items() if k != "source_line_id"} for r in lines]
    return _hash(sorted(canonical, key=lambda r: r["financial_line_id"]))


async def _legacy_plans(session: Any, lines: list[dict]) -> dict[str, list[dict]]:
    ids = [r["source_line_id"] for r in lines]
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    """SELECT budget_line_id::text AS line_id,month_number,
        budget_expense_amount,expected_income_amount FROM budget_line_monthly_plan
        WHERE budget_line_id=ANY(CAST(:ids AS uuid[])) ORDER BY budget_line_id,month_number"""
                ),
                {"ids": ids},
            )
        )
        .mappings()
        .all()
    )
    result: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        result[str(r["line_id"])].append(
            {
                "week": int(r["month_number"]),
                "expense": str(money(r["budget_expense_amount"])),
                "income": str(money(r["expected_income_amount"])),
            }
        )
    return dict(result)


def _legacy_fingerprint(lines: list[dict], plans: dict[str, list[dict]]) -> str:
    values = [
        {
            "financial_line_id": r["financial_line_id"],
            "plan": plans.get(r["source_line_id"], []),
        }
        for r in lines
    ]
    return _hash(sorted(values, key=lambda r: r["financial_line_id"]))


async def preview_tournament_overlay(
    session: Any, *, version_id: str, tournament_id: str, week: int
) -> dict:
    """Read-only preview; caller supplies the resulting hash for application."""
    version_id, tournament_id, week = (
        _uuid(version_id),
        _uuid(tournament_id),
        _week(week),
    )
    version = await _version(session, version_id, require_selected=True)
    from .distribution import financial_context, revision_context

    root_version_id, _ = await financial_context(
        session, await revision_context(session, version_id)
    )
    active = (
        await session.execute(
            text("SELECT active FROM tournaments WHERE id=CAST(:id AS uuid)"),
            {"id": tournament_id},
        )
    ).scalar_one_or_none()
    if active is not True:
        raise ValueError("Torneo inexistente o inactivo")
    lines = await _lines(session, version_id, tournament_id)
    if not lines:
        raise ValueError("El torneo no tiene partidas de gasto en esta versión")
    legacy = await _legacy_plans(session, lines)
    source_fp = _source_fingerprint(lines)
    legacy_fp = _legacy_fingerprint(lines, legacy)
    value = {
        "version_id": version_id,
        "financial_root_version_id": root_version_id,
        "edition_year": int(version["edition_year"]),
        "tournament_id": tournament_id,
        "week": week,
        "source_fingerprint": source_fp,
        "legacy_fingerprint": legacy_fp,
    }
    content_hash = _hash(value)
    schema_ready = await _schema_ready(session)
    current = None
    latest_id = None
    if schema_ready:
        current = (
            (
                await session.execute(
                    text(
                        """SELECT id::text AS id,plan_hash,content_hash FROM budget_tournament_weekly_revisions
            WHERE edition_year=:year AND tournament_id=CAST(:tid AS uuid) AND is_current"""
                    ),
                    {"year": version["edition_year"], "tid": tournament_id},
                )
            )
            .mappings()
            .first()
        )
        latest_id = await _revision_tip(session, version["edition_year"], tournament_id)
    current_id = current["id"] if current else None
    if latest_id and current_id != latest_id:
        tip = (
            (
                await session.execute(
                    text(
                        """SELECT deactivated_at,
            restored_revision_id::text AS restored_revision_id
            FROM budget_tournament_weekly_revisions WHERE id=CAST(:id AS uuid)"""
                    ),
                    {"id": latest_id},
                )
            )
            .mappings()
            .one()
        )
        if not tip["deactivated_at"] or tip["restored_revision_id"] != current_id:
            raise ValueError(
                "La revisión activa no coincide con la última restauración"
            )
    plan_hash = _hash(
        {**value, "current_revision_id": current_id, "latest_revision_id": latest_id}
    )
    return {
        **value,
        "plan_hash": plan_hash,
        "content_hash": content_hash,
        "line_count": len(lines),
        "total": str(sum((money(r["budget_amount"]) for r in lines), Decimal(0))),
        "lines": [
            {**r, "previous_plan": legacy.get(r["source_line_id"], [])} for r in lines
        ],
        "current_revision_id": current_id,
        "latest_revision_id": latest_id,
        "current_plan_hash": current["plan_hash"] if current else None,
        "current_content_hash": current["content_hash"] if current else None,
        "schema_ready": schema_ready,
    }


async def apply_tournament_overlay(
    session: Any,
    *,
    version_id: str,
    tournament_id: str,
    week: int,
    expected_plan_hash: str,
    actor_empleado_id: str,
) -> dict:
    """Apply within caller's transaction; stale plans and unauthorized actors fail closed."""
    version_id, tournament_id, actor_empleado_id = (
        _uuid(version_id),
        _uuid(tournament_id),
        _uuid(actor_empleado_id),
    )
    week = _week(week)
    if not await _schema_ready(session):
        raise ValueError("La migración de distribución no está instalada")
    await _require_budget_actor(session, actor_empleado_id)
    version = await _version(session, version_id, require_selected=False)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
        {"scope": f"budget-weekly:{version['edition_year']}:{tournament_id}"},
    )
    await session.execute(
        text("SELECT id FROM budget_versions WHERE id=CAST(:id AS uuid) FOR UPDATE"),
        {"id": version_id},
    )
    await _lines(session, version_id, tournament_id, lock=True)
    plan = await preview_tournament_overlay(
        session, version_id=version_id, tournament_id=tournament_id, week=week
    )
    previous = plan["current_revision_id"]
    if (
        previous
        and plan["current_content_hash"] == plan["content_hash"]
        and expected_plan_hash in {plan["plan_hash"], plan["current_plan_hash"]}
    ):
        return {
            k: plan[k]
            for k in (
                "plan_hash",
                "source_fingerprint",
                "version_id",
                "financial_root_version_id",
                "tournament_id",
                "edition_year",
                "week",
                "line_count",
                "total",
            )
        } | {
            "revision_id": previous,
            "plan_hash": plan["current_plan_hash"],
            "reviewed_plan_hash": expected_plan_hash,
            "idempotent": True,
        }
    if (
        not isinstance(expected_plan_hash, str)
        or plan["plan_hash"] != expected_plan_hash
    ):
        raise ValueError(
            "El plan o sus fuentes cambiaron; genera una nueva vista previa"
        )
    if previous:
        await session.execute(
            text(
                "UPDATE budget_tournament_weekly_revisions SET is_current=FALSE WHERE id=CAST(:id AS uuid) AND is_current"
            ),
            {"id": previous},
        )
    revision_id = str(uuid.uuid4())
    before = {
        "previous_revision_id": plan["latest_revision_id"],
        "previous_effective_revision_id": previous,
        "source_fingerprint": plan["source_fingerprint"],
        "legacy_fingerprint": plan["legacy_fingerprint"],
        "legacy_plans": {
            r["source_line_id"]: r["previous_plan"] for r in plan["lines"]
        },
    }
    await session.execute(
        text(
            """INSERT INTO budget_tournament_weekly_revisions
        (id,edition_year,tournament_id,financial_version_id,financial_root_version_id,actor_empleado_id,previous_revision_id,previous_effective_revision_id,
         week_number,source_fingerprint,legacy_fingerprint,plan_hash,content_hash,line_count,total,before_snapshot)
        VALUES (CAST(:id AS uuid),:year,CAST(:tid AS uuid),CAST(:vid AS uuid),CAST(:root AS uuid),CAST(:actor AS uuid),
         CAST(:previous AS uuid),CAST(:previous_effective AS uuid),:week,:source_fp,:legacy_fp,:plan_hash,:content_hash,:count,CAST(:total AS numeric),CAST(:before AS jsonb))"""
        ),
        {
            "id": revision_id,
            "year": plan["edition_year"],
            "tid": tournament_id,
            "vid": version_id,
            "root": plan["financial_root_version_id"],
            "actor": actor_empleado_id,
            "previous": plan["latest_revision_id"],
            "previous_effective": previous,
            "week": week,
            "source_fp": plan["source_fingerprint"],
            "legacy_fp": plan["legacy_fingerprint"],
            "plan_hash": plan["plan_hash"],
            "content_hash": plan["content_hash"],
            "count": plan["line_count"],
            "total": plan["total"],
            "before": json.dumps(before, ensure_ascii=False),
        },
    )
    for row in plan["lines"]:
        snapshot = {k: v for k, v in row.items() if k != "previous_plan"}
        await session.execute(
            text(
                """INSERT INTO budget_tournament_weekly_lines
            (revision_id,source_line_id,financial_line_id,source_snapshot,previous_plan,week_number,amount)
            VALUES (CAST(:rid AS uuid),CAST(:sid AS uuid),CAST(:fid AS uuid),CAST(:snapshot AS jsonb),
                    CAST(:previous AS jsonb),:week,CAST(:amount AS numeric))"""
            ),
            {
                "rid": revision_id,
                "sid": row["source_line_id"],
                "fid": row["financial_line_id"],
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "previous": json.dumps(row["previous_plan"], ensure_ascii=False),
                "week": week,
                "amount": row["budget_amount"],
            },
        )
    from . import service

    await service._audit_budget_event(
        session,
        budget_version_id=version_id,
        event_type="tournament_weekly_distribution_applied",
        actor_empleado_id=actor_empleado_id,
        payload={
            "revision_id": revision_id,
            "previous_revision_id": previous,
            "tournament_id": tournament_id,
            "week": week,
            "plan_hash": plan["plan_hash"],
            "source_fingerprint": plan["source_fingerprint"],
            "line_count": plan["line_count"],
            "total": plan["total"],
        },
    )
    await session.flush()
    return {
        k: plan[k]
        for k in (
            "plan_hash",
            "source_fingerprint",
            "version_id",
            "financial_root_version_id",
            "tournament_id",
            "edition_year",
            "week",
            "line_count",
            "total",
        )
    } | {"revision_id": revision_id, "idempotent": False}


async def deactivate_tournament_overlay(
    session: Any,
    *,
    version_id: str,
    tournament_id: str,
    expected_revision_id: str,
    expected_plan_hash: str,
    actor_empleado_id: str,
) -> dict:
    """Restore the prior effective plan without deleting financial or audit history.

    Caller owns the transaction. An exact revision and the hash persisted for its
    application are mandatory; a replay is accepted only while its restored
    predecessor is still the effective state and no newer revision exists.
    """
    version_id, tournament_id, expected_revision_id, actor_empleado_id = (
        _uuid(version_id),
        _uuid(tournament_id),
        _uuid(expected_revision_id),
        _uuid(actor_empleado_id),
    )
    if not await _schema_ready(session):
        raise ValueError("La migración de distribución no está instalada")
    await _require_budget_actor(session, actor_empleado_id)
    version = await _version(session, version_id, require_selected=False)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
        {"scope": f"budget-weekly:{version['edition_year']}:{tournament_id}"},
    )
    await session.execute(
        text("SELECT id FROM budget_versions WHERE id=CAST(:id AS uuid) FOR UPDATE"),
        {"id": version_id},
    )
    revision = (
        (
            await session.execute(
                text(
                    """SELECT id::text AS id,
        financial_version_id::text AS version_id, tournament_id::text AS tournament_id,
        previous_effective_revision_id::text AS previous_effective_revision_id,
        restored_revision_id::text AS restored_revision_id,plan_hash,is_current,deactivated_at
        FROM budget_tournament_weekly_revisions WHERE id=CAST(:id AS uuid) FOR UPDATE"""
                ),
                {"id": expected_revision_id},
            )
        )
        .mappings()
        .first()
    )
    if (
        not revision
        or revision["version_id"] != version_id
        or revision["tournament_id"] != tournament_id
        or not isinstance(expected_plan_hash, str)
        or revision["plan_hash"] != expected_plan_hash
    ):
        raise ValueError("La revisión o el hash no corresponden al plan esperado")
    current = (
        await session.execute(
            text(
                """SELECT id::text AS id
        FROM budget_tournament_weekly_revisions WHERE edition_year=:year
          AND tournament_id=CAST(:tid AS uuid) AND is_current FOR UPDATE"""
            ),
            {"year": version["edition_year"], "tid": tournament_id},
        )
    ).scalar_one_or_none()
    tip = await _revision_tip(session, version["edition_year"], tournament_id)
    if tip != expected_revision_id:
        raise ValueError("Existe una revisión más reciente; no se puede revertir ésta")
    if revision["deactivated_at"]:
        if current != revision["restored_revision_id"]:
            raise ValueError("La distribución cambió después de la reversión")
        return {
            "revision_id": expected_revision_id,
            "version_id": version_id,
            "tournament_id": tournament_id,
            "plan_hash": expected_plan_hash,
            "restored_revision_id": current,
            "deactivated": True,
            "idempotent": True,
        }
    if not revision["is_current"] or current != expected_revision_id:
        raise ValueError("La revisión solicitada no está activa")
    restore = revision["previous_effective_revision_id"]
    if restore:
        prior = (
            await session.execute(
                text(
                    """SELECT id FROM budget_tournament_weekly_revisions
            WHERE id=CAST(:id AS uuid) AND edition_year=:year
              AND tournament_id=CAST(:tid AS uuid) AND NOT is_current AND deactivated_at IS NULL
            FOR UPDATE"""
                ),
                {"id": restore, "year": version["edition_year"], "tid": tournament_id},
            )
        ).scalar_one_or_none()
        if prior is None:
            raise ValueError("No es posible restaurar la revisión anterior")
    await session.execute(
        text(
            """UPDATE budget_tournament_weekly_revisions
        SET is_current=FALSE,deactivated_at=now(),deactivated_by_empleado_id=CAST(:actor AS uuid),
            restored_revision_id=CAST(:restore AS uuid)
        WHERE id=CAST(:id AS uuid) AND is_current"""
        ),
        {"id": expected_revision_id, "actor": actor_empleado_id, "restore": restore},
    )
    if restore:
        await session.execute(
            text(
                """UPDATE budget_tournament_weekly_revisions
            SET is_current=TRUE WHERE id=CAST(:id AS uuid)"""
            ),
            {"id": restore},
        )
    from . import service

    await service._audit_budget_event(
        session,
        budget_version_id=version_id,
        event_type="tournament_weekly_distribution_deactivated",
        actor_empleado_id=actor_empleado_id,
        payload={
            "revision_id": expected_revision_id,
            "restored_revision_id": restore,
            "tournament_id": tournament_id,
            "plan_hash": expected_plan_hash,
        },
    )
    await session.flush()
    return {
        "revision_id": expected_revision_id,
        "version_id": version_id,
        "tournament_id": tournament_id,
        "plan_hash": expected_plan_hash,
        "restored_revision_id": restore,
        "deactivated": True,
        "idempotent": False,
    }


def _income_only(plan: dict) -> dict:
    """Keep expected income while refusing to present an obsolete expense plan."""
    return {
        int(week): {
            "budget_expense_amount": 0.0,
            "expected_income_amount": values.get("expected_income_amount", 0),
        }
        for week, values in plan.items()
        if money(values.get("expected_income_amount", 0)) != 0
    }


async def effective_plans_for_lines(
    session: Any,
    *,
    version_id: str,
    line_ids: list[str],
    baseline_plans: dict,
) -> tuple[dict, dict]:
    """Return effective plans and conflict reasons only for the requested scope.

    Consumers must explicitly render `conflicts` as unavailable, including
    zero-budget rows with a preserved expected-income-only plan. Historical
    versions keep their original schedule. Raw guards and writers must continue
    using the canonical unmodified `list_monthly_plan_for_lines` reader.
    """
    version_id = _uuid(version_id)
    result = {
        str(k): {int(w): dict(v) for w, v in plan.items()}
        for k, plan in baseline_plans.items()
    }
    ids = sorted({_uuid(v) for v in line_ids})
    if not ids or not await _schema_ready(session):
        return result, {}
    version = await _version(session, version_id, require_selected=False)
    from . import service

    selected = service.resolve_definitive_budget_version_from_versions(
        await service.list_budget_versions(
            session, edition_year=version["edition_year"], ensure_schema=False
        )
    )
    if not selected or str(selected["id"]) != version_id:
        return result, {}
    requested = (
        (
            await session.execute(
                text(
                    """SELECT id::text AS id,tournament_id::text AS tournament_id,
        COALESCE(line_direction,'expense') AS direction FROM budget_lines
        WHERE budget_version_id=CAST(:vid AS uuid) AND id=ANY(CAST(:ids AS uuid[]))"""
                ),
                {"vid": version_id, "ids": ids},
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in requested:
        if row["tournament_id"] and row["direction"] == "expense":
            grouped[str(row["tournament_id"])].append(str(row["id"]))
    if not grouped:
        return result, {}
    headers = (
        (
            await session.execute(
                text(
                    """SELECT id::text AS id,tournament_id::text AS tournament_id,
        financial_root_version_id::text AS financial_root_version_id,
        source_fingerprint,legacy_fingerprint,week_number,line_count,total
        FROM budget_tournament_weekly_revisions WHERE edition_year=:year AND is_current
          AND tournament_id=ANY(CAST(:tids AS uuid[]))"""
                ),
                {"year": version["edition_year"], "tids": list(grouped)},
            )
        )
        .mappings()
        .all()
    )
    from .distribution import financial_context, revision_context

    try:
        current_root, _ = await financial_context(
            session, await revision_context(session, version_id)
        )
    except ValueError:
        current_root = None
    conflicts: dict[str, str] = {}
    for header in headers:
        tournament_id = str(header["tournament_id"])
        lines = []
        if current_root == header["financial_root_version_id"]:
            try:
                lines = await _lines(session, version_id, tournament_id)
            except ValueError:
                # Invalid lineage must make the selected tournament unavailable,
                # never fall back to an older expense plan or disclose others.
                lines = []
        legacy = await _legacy_plans(session, lines)
        stored = (
            (
                await session.execute(
                    text(
                        """SELECT source_line_id::text AS source_line_id,
            financial_line_id::text AS financial_line_id,source_snapshot,week_number,amount
            FROM budget_tournament_weekly_lines WHERE revision_id=CAST(:id AS uuid)"""
                    ),
                    {"id": header["id"]},
                )
            )
            .mappings()
            .all()
        )
        by_financial = {r["financial_line_id"]: r for r in stored}
        valid = (
            bool(lines)
            and len(lines) == int(header["line_count"]) == len(stored)
            and len(by_financial) == len(stored)
            and _source_fingerprint(lines) == header["source_fingerprint"]
            and _legacy_fingerprint(lines, legacy) == header["legacy_fingerprint"]
            and sum((money(r["budget_amount"]) for r in lines), Decimal(0))
            == money(header["total"])
        )
        if valid:
            for line in lines:
                saved = by_financial.get(line["financial_line_id"])
                snapshot = dict(saved["source_snapshot"] or {}) if saved else {}
                expected = {k: v for k, v in line.items() if k != "source_line_id"}
                snapshot.pop("source_line_id", None)
                if (
                    not saved
                    or snapshot != expected
                    or int(saved["week_number"]) != int(header["week_number"])
                    or money(saved["amount"]) != money(line["budget_amount"])
                ):
                    valid = False
                    break
        if not valid:
            conflicts[tournament_id] = "La distribución cambió y requiere conciliación."
            for line_id in grouped[tournament_id]:
                income = _income_only(result.get(line_id, {}))
                if income:
                    result[line_id] = income
                else:
                    result.pop(line_id, None)
            continue
        for line in lines:
            line_id = line["source_line_id"]
            if line_id not in grouped[tournament_id]:
                continue
            plan = _income_only(result.get(line_id, {}))
            week = int(header["week_number"])
            plan.setdefault(
                week, {"budget_expense_amount": 0.0, "expected_income_amount": 0.0}
            )
            plan[week]["budget_expense_amount"] = float(money(line["budget_amount"]))
            result[line_id] = plan
    return result, conflicts
