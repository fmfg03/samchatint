"""Governed expense scheduling revisions; annual and financial history stay put."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import uuid
from typing import Any

from sqlalchemy import text

SOURCE = "distribution_revision"
EVENT = "distribution_revision_created"
PROTECTED = (
    "tournament_id",
    "tournament_code",
    "tournament_name",
    "sport",
    "phase",
    "entity_name",
    "concept_name",
    "budget_concept_id",
    "line_direction",
    "account_code_suggested",
    "account_code_final",
    "budget_amount",
    "reference_amount",
    "variance_amount",
    "priority",
    "owner_name",
    "criteria_note",
    "observations",
)


def money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Importe de distribución inválido") from None
    if (
        not amount.is_finite()
        or amount < 0
        or amount >= Decimal("10000000000000000")
        or amount != amount.quantize(Decimal(".01"))
    ):
        raise ValueError(
            "La distribución requiere importes no negativos con dos decimales"
        )
    return amount


def week_bounds(year: int, week: int) -> tuple[date, date]:
    if not 1 <= int(week) <= 52:
        raise ValueError("Semana fuera de 1–52")
    jan1, dec31 = date(year, 1, 1), date(year, 12, 31)
    next_monday = jan1 + timedelta(
        days=7 if jan1.isoweekday() == 1 else 8 - jan1.isoweekday()
    )
    start = jan1 if week == 1 else next_monday + timedelta(weeks=week - 2)
    end = next_monday - timedelta(days=1) if week == 1 else start + timedelta(days=6)
    return start, dec31 if week == 52 else min(end, dec31)


def schedule_state(line: dict, plan: dict) -> dict:
    """Absent is unknown; only an explicit reconciled plan certifies zero periods."""
    try:
        authorized = money(line.get("budget_amount"))
    except ValueError:
        return {"status": "invalid", "authorized": None, "scheduled": None}
    if not plan:
        return {"status": "missing", "authorized": str(authorized), "scheduled": None}
    try:
        total = sum(
            (
                money(v.get("budget_expense_amount"))
                for w, v in plan.items()
                if 1 <= int(w) <= 52
            ),
            Decimal(0),
        )
        if any(not 1 <= int(w) <= 52 for w in plan):
            raise ValueError("Semana inválida")
    except (ValueError, TypeError, AttributeError):
        return {"status": "invalid", "authorized": str(authorized), "scheduled": None}
    return {
        "status": "complete" if total == authorized else "mismatch",
        "authorized": str(authorized),
        "scheduled": str(total),
    }


def period_projection(lines: list[dict], plans: dict, year: int, cutoff: date) -> dict:
    if cutoff.year != year:
        raise ValueError("El corte debe permanecer en la edición presupuestal")
    rows = []
    for line in lines:
        if (line.get("line_direction") or "expense") != "expense":
            continue
        plan = plans.get(str(line["id"]), {})
        state = schedule_state(line, plan)
        monthly = None
        if state["status"] == "complete":
            monthly = {month: Decimal(0) for month in range(1, 13)}
            for week, values in plan.items():
                start, _ = week_bounds(year, int(week))
                monthly[start.month] += money(values["budget_expense_amount"])
        rows.append(
            {
                "line_id": str(line["id"]),
                "concept": line["concept_name"],
                "phase": line.get("phase"),
                **state,
                "budget_month": (
                    str(monthly[cutoff.month]) if monthly is not None else None
                ),
                "budget_ytd": (
                    str(
                        sum(
                            (v for m, v in monthly.items() if m <= cutoff.month),
                            Decimal(0),
                        )
                    )
                    if monthly is not None
                    else None
                ),
                "monthly": (
                    {str(m): str(v) for m, v in monthly.items()}
                    if monthly is not None
                    else None
                ),
            }
        )
    return {
        "rows": rows,
        "status": (
            "complete"
            if rows and all(r["status"] == "complete" for r in rows)
            else "pending"
        ),
        "period": cutoff.strftime("%Y-%m"),
        "convention": "Cada semana se atribuye al mes de su fecha de inicio; acumulado hasta el mes de corte.",
        "source": "budget_line_monthly_plan",
    }


def select_version(versions: list[dict]) -> dict | None:
    """Preserve frozen priority, except for an explicitly approved successor."""
    if not versions:
        return None
    ranked = sorted(
        versions,
        key=lambda v: (v.get("updated_at") or v.get("created_at") or "", str(v["id"])),
        reverse=True,
    )
    original = [v for v in ranked if v.get("source") != SOURCE]
    base = min(
        original or ranked,
        key=lambda v: {
            "frozen": 0,
            "approved": 1,
            "submitted": 2,
            "reforecast": 3,
            "draft": 4,
            "closed": 5,
        }.get(v.get("status"), 99),
    )
    seen = set()
    while base["id"] not in seen:
        seen.add(base["id"])
        successors = [
            v
            for v in ranked
            if v.get("source") == SOURCE
            and v.get("distribution_source_version_id") == base["id"]
            and v.get("edition_year") == base.get("edition_year")
            and v.get("status") in {"approved", "frozen"}
        ]
        if len(successors) > 1:
            raise ValueError(
                "Más de una revisión de distribución aprobada: resolver selección explícita"
            )
        if not successors:
            return base
        base = successors[0]
    raise ValueError("Linaje cíclico de distribución")


async def revision_context(session, version_id: str) -> dict:
    """Canonical read only; no ensure_schema/startup writes."""
    row = (
        (
            await session.execute(
                text("""
        SELECT v.id::text AS id, v.status, v.source, v.edition_year,
          (SELECT a.payload->>'source_version_id' FROM budget_version_audit_log a
           WHERE a.budget_version_id=v.id AND a.event_type=:event
           ORDER BY a.created_at DESC LIMIT 1) AS distribution_source_version_id
        FROM budget_versions v WHERE v.id=CAST(:id AS uuid)
    """),
                {"id": version_id, "event": EVENT},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError("Versión presupuestal inexistente")
    return dict(row)


async def financial_context(
    session, version: dict, *, seen: frozenset[str] = frozenset()
) -> tuple[str, dict[str, str]]:
    version_id = str(version["id"])
    if version_id in seen or len(seen) >= 32:
        raise ValueError("Linaje financiero de distribución cíclico o excesivo")
    if version.get("source") != SOURCE:
        return str(version["id"]), {}
    mapping = {}
    rows = (
        (
            await session.execute(
                text("""
        SELECT id::text AS id, metadata->>'distribution_source_line_id' AS source_line_id
        FROM budget_lines WHERE budget_version_id=CAST(:id AS uuid)
    """),
                {"id": version["id"]},
            )
        )
        .mappings()
        .all()
    )
    mapping = {
        str(r["source_line_id"]): str(r["id"]) for r in rows if r["source_line_id"]
    }
    if len(mapping) != len(rows):
        raise ValueError("Linaje de partidas ausente o duplicado")
    parent_id = version.get("distribution_source_version_id")
    if not parent_id:
        raise ValueError("Revisión sin fuente financiera acreditada")
    parent = await revision_context(session, parent_id)
    if parent["edition_year"] != version["edition_year"]:
        raise ValueError("Fuente financiera de otra edición")
    root, parent_map = await financial_context(
        session, parent, seen=seen | {version_id}
    )
    return root, (
        {old: mapping[new] for old, new in parent_map.items() if new in mapping}
        if parent_map
        else mapping
    )


async def create_revision(
    session, *, source_version_id: str, actor_empleado_id: str
) -> dict:
    """One exact whole-edition clone, no implicit distribution and one commit."""
    from . import service

    if not actor_empleado_id:
        raise ValueError("Actor requerido")
    try:
        await session.execute(
            text(
                "SELECT id FROM budget_versions WHERE id=CAST(:id AS uuid) FOR UPDATE"
            ),
            {"id": source_version_id},
        )
        source = await revision_context(session, source_version_id)
        if source["status"] not in {"approved", "frozen"}:
            raise ValueError(
                "Completar distribución requiere una fuente aprobada o congelada"
            )
        versions = await service.list_budget_versions(
            session, edition_year=source["edition_year"], ensure_schema=False
        )
        active = select_version(versions)
        if active is None or active["id"] != source_version_id:
            raise ValueError(
                "La fuente ya no es la versión seleccionada; vuelve a revisar"
            )
        pending = [
            v
            for v in versions
            if v.get("source") == SOURCE
            and v.get("distribution_source_version_id") == source_version_id
            and v["status"] != "closed"
        ]
        if pending:
            await session.commit()
            return pending[0]
        target_id = str(uuid.uuid4())
        financial_id, source_map = await financial_context(session, source)
        source_roots = {new: old for old, new in source_map.items()}
        await session.execute(
            text("""
            INSERT INTO budget_versions (id,edition_year,version_name,status,source,notes,created_by_empleado_id)
            SELECT CAST(:target AS uuid),edition_year,LEFT(version_name,90)||' · distribución','draft',:source,
                   'Revisión de distribución; conserva el autorizado y la fuente financiera.',CAST(:actor AS uuid)
            FROM budget_versions WHERE id=CAST(:parent AS uuid)
        """),
            {
                "target": target_id,
                "parent": source_version_id,
                "source": SOURCE,
                "actor": actor_empleado_id,
            },
        )
        # Lock all source lines; exact INSERT SELECT preserves fields and metadata.
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id::text AS id FROM budget_lines WHERE budget_version_id=CAST(:parent AS uuid) FOR UPDATE"
                    ),
                    {"parent": source_version_id},
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ValueError("La fuente no tiene partidas")
        fields = ",".join(PROTECTED)
        for row in rows:
            new_id = str(uuid.uuid4())
            params = {
                "new": new_id,
                "target": target_id,
                "old": row["id"],
                "financial_id": financial_id,
                "financial_line": source_roots.get(row["id"], row["id"]),
            }
            await session.execute(
                text(f"""
                INSERT INTO budget_lines (id,budget_version_id,{fields},metadata)
                SELECT CAST(:new AS uuid),CAST(:target AS uuid),{fields},
                  metadata || jsonb_build_object('distribution_source_line_id',CAST(:old AS text),
                    'distribution_financial_version_id',CAST(:financial_id AS text),
                    'distribution_financial_line_id',CAST(:financial_line AS text))
                FROM budget_lines WHERE id=CAST(:old AS uuid)
            """),
                params,
            )
            for table, columns in (
                (
                    "budget_line_monthly_plan",
                    "month_number,budget_expense_amount,expected_income_amount,metadata",
                ),
                (
                    "budget_line_monthly_allocations",
                    "month_number,allocated_amount,metadata",
                ),
            ):
                await session.execute(
                    text(
                        f"INSERT INTO {table} (id,budget_line_id,{columns}) SELECT gen_random_uuid(),CAST(:new AS uuid),{columns} FROM {table} WHERE budget_line_id=CAST(:old AS uuid)"
                    ),
                    params,
                )
        await service._audit_budget_event(
            session,
            budget_version_id=target_id,
            event_type=EVENT,
            actor_empleado_id=actor_empleado_id,
            to_status="draft",
            payload={"source_version_id": source_version_id, "line_count": len(rows)},
        )
        await session.commit()
        return {
            "id": target_id,
            "edition_year": source["edition_year"],
            "status": "draft",
            "source": SOURCE,
        }
    except Exception:
        await session.rollback()
        raise


async def guard_source_transition(session, version_id: str, to_status: str) -> None:
    """A source with a live scheduling successor must remain historical truth."""
    if to_status not in {"reforecast", "closed"}:
        return
    children = (
        (
            await session.execute(
                text("""
        SELECT v.id FROM budget_versions v
        JOIN budget_version_audit_log a ON a.budget_version_id=v.id
        WHERE v.source=:source
          AND a.event_type=:event AND a.payload->>'source_version_id'=:parent
        LIMIT 1
    """),
                {"source": SOURCE, "event": EVENT, "parent": version_id},
            )
        )
        .mappings()
        .first()
    )
    if children:
        raise ValueError(
            "La versión es fuente de una revisión de distribución y conserva su historial. Cambios económicos requieren un recorrido de sustitución separado, fuera de esta revisión de semanas."
        )


async def validate_revision(session, version: dict) -> None:
    """A scheduling successor cannot change any source line or silently fork."""
    from . import service

    parent = version.get("distribution_source_version_id")
    if not parent:
        raise ValueError("Falta linaje de distribución")
    # Serialize sibling approvals against the unchanged original.
    await session.execute(
        text("SELECT id FROM budget_versions WHERE id=CAST(:id AS uuid) FOR UPDATE"),
        {"id": parent},
    )
    versions = await service.list_budget_versions(
        session, edition_year=version["edition_year"], ensure_schema=False
    )
    selected = select_version([v for v in versions if v["id"] != version["id"]])
    if selected is None or selected["id"] != parent:
        raise ValueError("La fuente cambió; esta revisión ya no puede reemplazarla")
    source_rows = (
        (
            await session.execute(
                text(
                    "SELECT * FROM budget_lines WHERE budget_version_id=CAST(:id AS uuid) FOR UPDATE"
                ),
                {"id": parent},
            )
        )
        .mappings()
        .all()
    )
    target_rows = (
        (
            await session.execute(
                text(
                    "SELECT * FROM budget_lines WHERE budget_version_id=CAST(:id AS uuid) FOR UPDATE"
                ),
                {"id": version["id"]},
            )
        )
        .mappings()
        .all()
    )
    originals = {str(r["id"]): r for r in source_rows}
    seen = set()
    for row in target_rows:
        origin_id = (row.get("metadata") or {}).get("distribution_source_line_id")
        origin = originals.get(origin_id)
        if (
            origin is None
            or origin_id in seen
            or any(row[field] != origin[field] for field in PROTECTED)
        ):
            raise ValueError("La revisión alteró una partida o su importe autorizado")
        expected_metadata = {
            **origin["metadata"],
            "distribution_source_line_id": origin_id,
            "distribution_financial_version_id": origin["metadata"].get(
                "distribution_financial_version_id"
            )
            or parent,
            "distribution_financial_line_id": origin["metadata"].get(
                "distribution_financial_line_id"
            )
            or origin_id,
        }
        if row["metadata"] != expected_metadata:
            raise ValueError("La revisión alteró la evidencia original")
        seen.add(origin_id)
    if not originals or seen != set(originals):
        raise ValueError("La revisión debe conservar todas las partidas de la edición")
    plans = await service.list_monthly_plan_for_lines(
        session, line_ids=[str(r["id"]) for r in target_rows], ensure_schema=False
    )
    source_plans = await service.list_monthly_plan_for_lines(
        session, line_ids=list(originals), ensure_schema=False
    )
    for row in target_rows:
        if (
            (row.get("line_direction") or "expense") == "expense"
            and schedule_state(row, plans.get(str(row["id"]), {}))["status"]
            != "complete"
        ):
            raise ValueError(
                "Completa y concilia la distribución de todas las partidas de gasto antes de aprobar"
            )
        if row.get("line_direction") == "income" and plans.get(
            str(row["id"]), {}
        ) != source_plans.get(row["metadata"]["distribution_source_line_id"], {}):
            raise ValueError(
                "Esta revisión no modifica los ingresos esperados originales"
            )
        target_plan = plans.get(str(row["id"]), {})
        original_plan = source_plans.get(
            row["metadata"]["distribution_source_line_id"], {}
        )
        if any(
            money(target_plan.get(w, {}).get("expected_income_amount", 0))
            != money(original_plan.get(w, {}).get("expected_income_amount", 0))
            for w in set(target_plan) | set(original_plan)
        ):
            raise ValueError("La revisión alteró ingresos esperados")
