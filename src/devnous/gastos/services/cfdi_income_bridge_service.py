"""Link PSP-emitted CFDIs to budget income actuals."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .cfdi_ingestion_service import ingest_cfdi_from_upload


class CFDIIncomeBridgeError(ValueError):
    """Raised when a CFDI cannot be linked as PSP budget income."""


def normalize_rfc(value: Any) -> str:
    """Canonical RFC comparison key: uppercase alphanumeric, no fuzzy matching."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _safe_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise CFDIIncomeBridgeError("El monto de ingreso no es válido.") from exc


def _coerce_income_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    clean = str(value).strip()
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise CFDIIncomeBridgeError("La fecha de ingreso no es válida.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def list_configured_rfc_allowlist(session: AsyncSession) -> dict[str, str]:
    """Return active RFCs configured in /admin/rfc keyed by normalized RFC."""
    rows = (
        await session.execute(
            text(
                """
                SELECT tax_id, name
                FROM rfc_configs
                WHERE active IS TRUE
                  AND NULLIF(TRIM(tax_id), '') IS NOT NULL
                ORDER BY display_order ASC, name ASC
                """
            )
        )
    ).mappings().all()
    return {
        normalized: str(row.get("name") or row.get("tax_id") or "").strip()
        for row in rows
        if (normalized := normalize_rfc(row.get("tax_id")))
    }


def validate_cfdi_emisor_in_rfc_allowlist(
    *,
    emisor_rfc: Any,
    allowlist: dict[str, str],
) -> str:
    """Return normalized emisor RFC when it is strictly present in /admin/rfc."""
    normalized = normalize_rfc(emisor_rfc)
    if not allowlist:
        raise CFDIIncomeBridgeError(
            "No hay RFC activos configurados en /admin/rfc para validar el emisor PSP."
        )
    if not normalized or normalized not in allowlist:
        raise CFDIIncomeBridgeError(
            "El CFDI no tiene un RFC emisor activo configurado en /admin/rfc."
        )
    return normalized


async def _load_cfdi_report(
    session: AsyncSession,
    cfdi_report_id: str,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id, cfdi_uuid, fecha, total, emisor_rfc, emisor_nombre,
                       receptor_rfc, receptor_nombre, tipo_de_comprobante
                FROM cfdi_reports
                WHERE id = CAST(:cfdi_report_id AS uuid)
                LIMIT 1
                """
            ),
            {"cfdi_report_id": str(cfdi_report_id)},
        )
    ).mappings().first()
    if not row:
        raise CFDIIncomeBridgeError("CFDI no encontrado.")
    return dict(row)


async def _load_budget_line(
    session: AsyncSession,
    budget_line_id: str,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id, budget_version_id, budget_concept_id, tournament_id,
                       tournament_code, tournament_name, phase, concept_name,
                       COALESCE(line_direction, 'expense') AS line_direction
                FROM budget_lines
                WHERE id = CAST(:budget_line_id AS uuid)
                LIMIT 1
                """
            ),
            {"budget_line_id": str(budget_line_id)},
        )
    ).mappings().first()
    if not row:
        raise CFDIIncomeBridgeError("Partida presupuestal no encontrada.")
    line = dict(row)
    if str(line.get("line_direction") or "expense").strip().lower() != "income":
        raise CFDIIncomeBridgeError(
            "La partida presupuestal debe ser de ingresos para vincular CFDI PSP."
        )
    return line


async def list_psp_cfdi_income_candidates(
    session: AsyncSession,
    *,
    budget_version_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    allowlist = await list_configured_rfc_allowlist(session)
    if not allowlist:
        return []
    link_version_filter = ""
    params: dict[str, Any] = {
        "allowed_rfcs": sorted(allowlist.keys()),
        "limit": max(1, min(int(limit or 100), 500)),
    }
    if budget_version_id:
        link_version_filter = (
            "AND l.budget_version_id = CAST(:budget_version_id AS uuid)"
        )
        params["budget_version_id"] = str(budget_version_id)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT c.id, c.cfdi_uuid, c.fecha, c.total, c.emisor_rfc,
                       c.emisor_nombre, c.receptor_rfc, c.receptor_nombre
                FROM cfdi_reports c
                WHERE REGEXP_REPLACE(
                    UPPER(COALESCE(c.emisor_rfc, '')),
                    '[^A-Z0-9]',
                    '',
                    'g'
                ) = ANY(CAST(:allowed_rfcs AS text[]))
                  AND NOT EXISTS (
                    SELECT 1
                    FROM budget_cfdi_income_links l
                    WHERE l.cfdi_report_id = c.id
                      AND l.unlinked_at IS NULL
                      {link_version_filter}
                  )
                ORDER BY COALESCE(c.fecha, c.created_at) DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def list_budget_cfdi_income_links(
    session: AsyncSession,
    *,
    budget_version_id: str,
    tournament_id: Optional[str],
) -> list[dict[str, Any]]:
    filters = ["l.budget_version_id = CAST(:budget_version_id AS uuid)"]
    params: dict[str, Any] = {"budget_version_id": str(budget_version_id)}
    if tournament_id:
        filters.append("l.tournament_id = CAST(:tournament_id AS uuid)")
        params["tournament_id"] = str(tournament_id)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT l.id, l.cfdi_report_id, l.budget_line_id, l.budget_version_id,
                       l.tournament_id, l.phase, l.budget_concept_id, l.amount,
                       l.income_date, l.source, l.created_at, l.unlinked_at,
                       c.cfdi_uuid, c.emisor_rfc, c.emisor_nombre,
                       c.receptor_rfc, c.receptor_nombre,
                       bl.concept_name
                FROM budget_cfdi_income_links l
                JOIN cfdi_reports c ON c.id = l.cfdi_report_id
                JOIN budget_lines bl ON bl.id = l.budget_line_id
                WHERE {' AND '.join(filters)}
                ORDER BY
                    l.unlinked_at NULLS FIRST,
                    l.income_date DESC,
                    l.created_at DESC
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def create_cfdi_income_link(
    session: AsyncSession,
    *,
    cfdi_report_id: str,
    budget_line_id: str,
    actor_empleado_id: Optional[str],
    amount: Optional[Any] = None,
    income_date: Optional[Any] = None,
    source: str = "admin_ui",
) -> dict[str, Any]:
    allowlist = await list_configured_rfc_allowlist(session)
    cfdi = await _load_cfdi_report(session, cfdi_report_id)
    matched_rfc = validate_cfdi_emisor_in_rfc_allowlist(
        emisor_rfc=cfdi.get("emisor_rfc"),
        allowlist=allowlist,
    )
    line = await _load_budget_line(session, budget_line_id)
    budget_version_id = str(line["budget_version_id"])
    resolved_amount = _safe_decimal(
        amount if amount not in (None, "") else cfdi.get("total")
    )
    if resolved_amount <= 0:
        raise CFDIIncomeBridgeError(
            "El CFDI no tiene un total válido para ingreso real."
        )
    resolved_income_date = (
        _coerce_income_datetime(income_date)
        or _coerce_income_datetime(cfdi.get("fecha"))
        or datetime.now(timezone.utc)
    )

    existing = (
        await session.execute(
            text(
                """
                SELECT id, budget_line_id
                FROM budget_cfdi_income_links
                WHERE cfdi_report_id = CAST(:cfdi_report_id AS uuid)
                  AND budget_version_id = CAST(:budget_version_id AS uuid)
                  AND unlinked_at IS NULL
                LIMIT 1
                """
            ),
            {
                "cfdi_report_id": str(cfdi_report_id),
                "budget_version_id": budget_version_id,
            },
        )
    ).mappings().first()
    if existing:
        if str(existing["budget_line_id"]) == str(line["id"]):
            await session.execute(
                text(
                    """
                    UPDATE budget_cfdi_income_links
                    SET amount = :amount,
                        income_date = :income_date,
                        updated_at = NOW()
                    WHERE id = CAST(:link_id AS uuid)
                    """
                ),
                {
                    "link_id": str(existing["id"]),
                    "amount": str(resolved_amount),
                    "income_date": resolved_income_date,
                },
            )
            await session.commit()
            return {"status": "updated", "id": str(existing["id"])}
        raise CFDIIncomeBridgeError(
            "Este CFDI ya cuenta como ingreso real en otra partida de esta versión."
        )
    link_id = str(uuid.uuid4())
    metadata = {
        "cfdi_uuid": str(cfdi.get("cfdi_uuid") or ""),
        "matched_emisor_rfc": matched_rfc,
        "matched_rfc_config_name": allowlist.get(matched_rfc, ""),
        "emisor_nombre": str(cfdi.get("emisor_nombre") or ""),
        "receptor_rfc": str(cfdi.get("receptor_rfc") or ""),
        "source": source,
    }
    await session.execute(
        text(
            """
            INSERT INTO budget_cfdi_income_links (
                id, cfdi_report_id, budget_line_id, budget_version_id,
                tournament_id, phase, budget_concept_id, amount, income_date,
                linked_by_empleado_id, source, metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid),
                CAST(:cfdi_report_id AS uuid),
                CAST(:budget_line_id AS uuid),
                CAST(:budget_version_id AS uuid),
                CAST(:tournament_id AS uuid),
                :phase,
                CAST(:budget_concept_id AS uuid), :amount, :income_date,
                CAST(:linked_by_empleado_id AS uuid),
                :source,
                CAST(:metadata AS jsonb),
                NOW(), NOW()
            )
            """
        ),
        {
            "id": link_id,
            "cfdi_report_id": str(cfdi_report_id),
            "budget_line_id": str(line["id"]),
            "budget_version_id": budget_version_id,
            "tournament_id": str(line["tournament_id"] or "") or None,
            "phase": str(line.get("phase") or "").strip() or None,
            "budget_concept_id": str(line["budget_concept_id"] or "") or None,
            "amount": str(resolved_amount),
            "income_date": resolved_income_date,
            "linked_by_empleado_id": str(actor_empleado_id or "") or None,
            "source": source,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )
    await session.commit()
    return {"status": "linked", "id": link_id}


async def ingest_and_link_cfdi_income(
    session: AsyncSession,
    *,
    budget_line_id: str,
    actor_empleado_id: Optional[str],
    xml_bytes: Optional[bytes] = None,
    pdf_bytes: Optional[bytes] = None,
    amount: Optional[Any] = None,
    income_date: Optional[Any] = None,
) -> dict[str, Any]:
    result = await ingest_cfdi_from_upload(
        session,
        xml_bytes=xml_bytes,
        pdf_bytes=pdf_bytes,
        source="psp_income_bridge",
    )
    if result is None:
        raise CFDIIncomeBridgeError("Carga un XML o PDF con CFDI válido.")
    return await create_cfdi_income_link(
        session,
        cfdi_report_id=str(result.cfdi_report.id),
        budget_line_id=budget_line_id,
        actor_empleado_id=actor_empleado_id,
        amount=amount,
        income_date=income_date,
        source="upload",
    )


async def soft_unlink_cfdi_income(
    session: AsyncSession,
    *,
    link_id: str,
    actor_empleado_id: Optional[str],
) -> bool:
    result = await session.execute(
        text(
            """
            UPDATE budget_cfdi_income_links
            SET unlinked_at = NOW(),
                unlinked_by_empleado_id = CAST(:actor_id AS uuid),
                updated_at = NOW()
            WHERE id = CAST(:link_id AS uuid)
              AND unlinked_at IS NULL
            RETURNING id
            """
        ),
        {
            "link_id": str(link_id),
            "actor_id": str(actor_empleado_id or "") or None,
        },
    )
    row = result.mappings().first()
    await session.commit()
    return row is not None
