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
                       account_code_final, account_code_suggested,
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


async def ensure_cfdi_income_tournament_assignment_schema(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS cfdi_income_tournament_assignments (
                id uuid PRIMARY KEY,
                cfdi_report_id uuid NOT NULL REFERENCES cfdi_reports(id) ON DELETE CASCADE,
                tournament_id uuid NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                assigned_by_empleado_id uuid NULL REFERENCES empleados(id) ON DELETE SET NULL,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT NOW(),
                updated_at timestamptz NOT NULL DEFAULT NOW(),
                UNIQUE (cfdi_report_id)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_cfdi_income_tournament_assignments_tournament
            ON cfdi_income_tournament_assignments(tournament_id)
            """
        )
    )


async def assign_cfdi_income_tournament(
    session: AsyncSession,
    *,
    cfdi_report_id: str,
    tournament_id: str,
    actor_empleado_id: Optional[str],
) -> dict[str, Any]:
    await ensure_cfdi_income_tournament_assignment_schema(session)
    allowlist = await list_configured_rfc_allowlist(session)
    cfdi = await _load_cfdi_report(session, cfdi_report_id)
    matched_rfc = validate_cfdi_emisor_in_rfc_allowlist(
        emisor_rfc=cfdi.get("emisor_rfc"),
        allowlist=allowlist,
    )
    tournament = (
        await session.execute(
            text(
                """
                SELECT id, name
                FROM tournaments
                WHERE id = CAST(:tournament_id AS uuid)
                  AND active IS TRUE
                LIMIT 1
                """
            ),
            {"tournament_id": str(tournament_id)},
        )
    ).mappings().first()
    if not tournament:
        raise CFDIIncomeBridgeError("Torneo/proyecto no encontrado o inactivo.")
    assignment_id = str(uuid.uuid4())
    metadata = {
        "cfdi_uuid": str(cfdi.get("cfdi_uuid") or ""),
        "matched_emisor_rfc": matched_rfc,
        "matched_rfc_config_name": allowlist.get(matched_rfc, ""),
        "emisor_nombre": str(cfdi.get("emisor_nombre") or ""),
        "receptor_rfc": str(cfdi.get("receptor_rfc") or ""),
        "tournament_name": str(tournament.get("name") or ""),
    }
    await session.execute(
        text(
            """
            INSERT INTO cfdi_income_tournament_assignments (
                id, cfdi_report_id, tournament_id, assigned_by_empleado_id, metadata,
                created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid),
                CAST(:cfdi_report_id AS uuid),
                CAST(:tournament_id AS uuid),
                CAST(:actor_id AS uuid),
                CAST(:metadata AS jsonb),
                NOW(), NOW()
            )
            ON CONFLICT (cfdi_report_id) DO UPDATE SET
                tournament_id = EXCLUDED.tournament_id,
                assigned_by_empleado_id = EXCLUDED.assigned_by_empleado_id,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING id
            """
        ),
        {
            "id": assignment_id,
            "cfdi_report_id": str(cfdi_report_id),
            "tournament_id": str(tournament_id),
            "actor_id": str(actor_empleado_id or "") or None,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )
    await session.commit()
    return {"status": "assigned", "tournament_id": str(tournament_id)}


async def list_psp_cfdi_income_candidates(
    session: AsyncSession,
    *,
    budget_version_id: Optional[str] = None,
    tournament_id: Optional[str] = None,
    assigned_only: bool = False,
    unassigned_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    await ensure_cfdi_income_tournament_assignment_schema(session)
    allowlist = await list_configured_rfc_allowlist(session)
    if not allowlist:
        return []
    link_version_filter = ""
    assignment_filter = ""
    params: dict[str, Any] = {
        "allowed_rfcs": sorted(allowlist.keys()),
        "limit": max(1, min(int(limit or 100), 500)),
    }
    if budget_version_id:
        link_version_filter = (
            "AND l.budget_version_id = CAST(:budget_version_id AS uuid)"
        )
        params["budget_version_id"] = str(budget_version_id)
    if tournament_id:
        assignment_filter = "AND a.tournament_id = CAST(:tournament_id AS uuid)"
        params["tournament_id"] = str(tournament_id)
    if assigned_only:
        assignment_filter += " AND a.id IS NOT NULL"
    if unassigned_only:
        assignment_filter += " AND a.id IS NULL"
    rows = (
        await session.execute(
            text(
                f"""
                SELECT c.id, c.cfdi_uuid, c.fecha, c.total, c.emisor_rfc,
                       c.emisor_nombre, c.receptor_rfc, c.receptor_nombre,
                       c.descripcion_concepto_principal,
                       a.tournament_id AS assigned_tournament_id
                FROM cfdi_reports c
                LEFT JOIN cfdi_income_tournament_assignments a
                  ON a.cfdi_report_id = c.id
                WHERE REGEXP_REPLACE(
                    UPPER(COALESCE(c.emisor_rfc, '')),
                    '[^A-Z0-9]',
                    '',
                    'g'
                ) = ANY(CAST(:allowed_rfcs AS text[]))
                  {assignment_filter}
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




def _receivable_account_code_for_budget_line(line: dict[str, Any]) -> str:
    concept = " ".join(
        str(line.get(key) or "")
        for key in ("tournament_name", "concept_name", "account_code_final", "account_code_suggested")
    ).lower()
    if "patrocin" in concept or "intercambio" in concept or "4100-001-008" in concept:
        return "1150-001-003"
    return "1150-001-001"


async def _load_account_by_code(session: AsyncSession, code: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id, codigo, nombre
                FROM cuentas_contables
                WHERE codigo = :code
                  AND activo IS TRUE
                LIMIT 1
                """
            ),
            {"code": code},
        )
    ).mappings().first()
    if not row:
        raise CFDIIncomeBridgeError(f"No existe cuenta contable activa {code}.")
    return dict(row)


async def ensure_cfdi_income_receivable_posting(
    session: AsyncSession,
    *,
    cfdi_report_id: str,
    budget_line_id: str,
    amount: Any,
    income_date: Any,
) -> dict[str, Any]:
    """Create/update the accounting CxC policy for an emitted PSP CFDI.

    Link semantics:
    - CFDI issued and linked to a tournament/budget line creates receivable:
      Dr 1150-* client, Cr 4100-* income, Cr IVA por pagar when applicable.
    - Collection can later clear this policy through ingreso_cobrado_ui.
    """
    cfdi = await _load_cfdi_report(session, cfdi_report_id)
    line = await _load_budget_line(session, budget_line_id)
    total = _safe_decimal(amount if amount not in (None, "") else cfdi.get("total"))
    if total <= 0:
        raise CFDIIncomeBridgeError("El CFDI no tiene total válido para generar CxC.")
    iva = _safe_decimal(cfdi.get("total_impuestos_trasladados"))
    if iva < 0:
        iva = Decimal("0.00")
    if iva >= total:
        iva = Decimal("0.00")
    ingreso_base = (total - iva).quantize(Decimal("0.01"))
    receivable = await _load_account_by_code(
        session,
        _receivable_account_code_for_budget_line(line),
    )
    income_code = str(line.get("account_code_final") or line.get("account_code_suggested") or "").strip()
    if not income_code:
        raise CFDIIncomeBridgeError("La partida de ingreso no tiene cuenta contable 4100 asignada.")
    income_account = await _load_account_by_code(session, income_code)
    iva_account = await _load_account_by_code(session, "2140-001-001") if iva > 0 else None
    fecha = _coerce_income_datetime(income_date) or _coerce_income_datetime(cfdi.get("fecha")) or datetime.now(timezone.utc)
    folio = "".join(part for part in [str(cfdi.get("serie") or "").strip(), str(cfdi.get("folio") or "").strip()] if part)
    cfdi_ref = folio or str(cfdi.get("cfdi_uuid") or cfdi_report_id)[:8]
    concept = " / ".join(
        part
        for part in [
            f"CxC CFDI {cfdi_ref}",
            str(line.get("tournament_name") or "").strip(),
            str(line.get("concept_name") or "").strip(),
        ]
        if part
    )
    numero_poliza = f"CXC-{str(cfdi_report_id)[:8]}"
    existing = (
        await session.execute(
            text(
                """
                SELECT id
                FROM accounting_polizas
                WHERE origen = 'cxc_cfdi_income'
                  AND cfdi_report_id = CAST(:cfdi_report_id AS uuid)
                LIMIT 1
                """
            ),
            {"cfdi_report_id": str(cfdi_report_id)},
        )
    ).mappings().first()
    poliza_id = str(existing["id"]) if existing else str(uuid.uuid4())
    if existing:
        await session.execute(text("DELETE FROM accounting_poliza_lines WHERE poliza_id = CAST(:poliza_id AS uuid)"), {"poliza_id": poliza_id})
        await session.execute(
            text(
                """
                UPDATE accounting_polizas
                SET source_file = :source_file,
                    source_sheet = 'cxc_cfdi_income',
                    tipo_poliza = 'Diario',
                    numero_poliza = :numero_poliza,
                    fecha_poliza = :fecha,
                    beneficiario_nombre = :beneficiario,
                    concepto = :concepto,
                    concepto_resumen = :concepto,
                    line_count_declared = :line_count,
                    line_count_actual = :line_count,
                    cfdi_uuid = :cfdi_uuid,
                    origen = 'cxc_cfdi_income',
                    updated_at = NOW()
                WHERE id = CAST(:poliza_id AS uuid)
                """
            ),
            {
                "poliza_id": poliza_id,
                "source_file": f"samchat:cxc:{cfdi_report_id}",
                "numero_poliza": numero_poliza,
                "fecha": fecha,
                "beneficiario": str(cfdi.get("receptor_nombre") or cfdi.get("receptor_rfc") or ""),
                "concepto": concept,
                "line_count": 3 if iva_account else 2,
                "cfdi_uuid": str(cfdi.get("cfdi_uuid") or "") or None,
            },
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO accounting_polizas (
                    id, source_file, source_sheet, source_row_start, tipo_poliza,
                    numero_poliza, fecha_poliza, beneficiario_nombre, concepto,
                    concepto_resumen, line_count_declared, line_count_actual,
                    cfdi_uuid, cfdi_report_id, origen, created_at, updated_at
                ) VALUES (
                    CAST(:poliza_id AS uuid), :source_file, 'cxc_cfdi_income', NULL, 'Diario',
                    :numero_poliza, :fecha, :beneficiario, :concepto,
                    :concepto, :line_count, :line_count,
                    :cfdi_uuid, CAST(:cfdi_report_id AS uuid), 'cxc_cfdi_income', NOW(), NOW()
                )
                """
            ),
            {
                "poliza_id": poliza_id,
                "source_file": f"samchat:cxc:{cfdi_report_id}",
                "numero_poliza": numero_poliza,
                "fecha": fecha,
                "beneficiario": str(cfdi.get("receptor_nombre") or cfdi.get("receptor_rfc") or ""),
                "concepto": concept,
                "line_count": 3 if iva_account else 2,
                "cfdi_uuid": str(cfdi.get("cfdi_uuid") or "") or None,
                "cfdi_report_id": str(cfdi_report_id),
            },
        )
    raw_base = {
        "origin": "cxc_cfdi_income",
        "cfdi_report_id": str(cfdi_report_id),
        "budget_line_id": str(budget_line_id),
        "tournament_id": str(line.get("tournament_id") or "") or None,
        "budget_version_id": str(line.get("budget_version_id") or "") or None,
        "cfdi_uuid": str(cfdi.get("cfdi_uuid") or ""),
    }
    line_specs = [
        (1, receivable, total, Decimal("0.00"), "debe_cxc"),
        (2, income_account, Decimal("0.00"), ingreso_base, "haber_ingreso"),
    ]
    if iva_account and iva > 0:
        line_specs.append((3, iva_account, Decimal("0.00"), iva, "haber_iva_trasladado"))
    for line_no, account, debe, haber, movement in line_specs:
        await session.execute(
            text(
                """
                INSERT INTO accounting_poliza_lines (
                    id, poliza_id, line_no, cuenta_codigo, cuenta_contable_id,
                    concepto, movimiento_no, debe, haber, raw_row_json, created_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:poliza_id AS uuid), :line_no, :cuenta_codigo,
                    CAST(:cuenta_contable_id AS uuid), :concepto, :movimiento_no,
                    :debe, :haber, CAST(:raw AS jsonb), NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "poliza_id": poliza_id,
                "line_no": line_no,
                "cuenta_codigo": account["codigo"],
                "cuenta_contable_id": str(account["id"]),
                "concepto": concept,
                "movimiento_no": str(line_no),
                "debe": float(debe),
                "haber": float(haber),
                "raw": json.dumps({**raw_base, "movement": movement}, ensure_ascii=False),
            },
        )
    return {"status": "posted", "poliza_id": poliza_id, "numero_poliza": numero_poliza}


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
            posting = await ensure_cfdi_income_receivable_posting(
                session,
                cfdi_report_id=str(cfdi_report_id),
                budget_line_id=str(line["id"]),
                amount=resolved_amount,
                income_date=resolved_income_date,
            )
            await session.commit()
            return {"status": "updated", "id": str(existing["id"]), "posting": posting}
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
    posting = await ensure_cfdi_income_receivable_posting(
        session,
        cfdi_report_id=str(cfdi_report_id),
        budget_line_id=str(line["id"]),
        amount=resolved_amount,
        income_date=resolved_income_date,
    )
    await session.commit()
    return {"status": "linked", "id": link_id, "posting": posting}


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
