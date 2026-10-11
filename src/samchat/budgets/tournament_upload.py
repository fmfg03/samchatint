"""Evidence-preserving previews for tournament budget workbooks.

Reads never provision schema. Apply delegates to canonical budget services and
requires a fresh, explicitly confirmed plan; the caller owns actor authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from sqlalchemy import text

from samchat.budgets import service

CENT = Decimal("0.01")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Monto inválido.") from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal("10000000000000000"):
        raise ValueError("Monto fuera de rango.")
    return amount


def parse_tournament_budget(
    *, file_bytes: bytes, filename: str, edition_year: int
) -> dict[str, Any]:
    """Parse HWC/LTTB layouts without guessing identity, currency or tax basis."""
    if Path(filename).suffix.lower() != ".xlsx":
        raise ValueError("Usa un archivo XLSX.")
    if not file_bytes or len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Archivo vacío o mayor a 5 MB.")
    try:
        with ZipFile(BytesIO(file_bytes)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 25 * 1024 * 1024:
                raise ValueError("El contenido del archivo excede el límite.")
        book = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    except (BadZipFile, KeyError):
        raise ValueError("Archivo XLSX inválido.") from None
    try:
        sheet = book.active
        years = re.findall(r"\b20\d{2}\b", sheet.title)
        if years != [str(edition_year)]:
            raise ValueError("La edición debe coincidir con el año de la hoja.")
        if sheet.max_row > 1000 or sheet.max_column > 20:
            raise ValueError("La hoja excede el tamaño permitido.")
        headers = [
            service._normalize_budget_key(v or "")
            for v in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        ]
        layout = None
        if headers[:4] == ["tipo", "sub_proyecto", "partida", "monto"]:
            layout = "HWC"
        elif headers[:3] == ["fase", "partida", "monto"]:
            layout = "LTTB"
        if layout is None:
            raise ValueError(
                "Formato no reconocido: tipo/sub_proyecto/partida/monto "
                "o Fase/Partida/Monto."
            )
        rows = []
        keys = set()
        groups: dict[str, Decimal] = defaultdict(Decimal)
        raw_total = Decimal(0)
        for number, cells in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            if all(v is None for v in cells):
                continue
            if layout == "HWC":
                direction, scope, label, value = cells[:4]
                if str(direction).strip().lower() != "egreso":
                    raise ValueError(f"Fila {number}: se esperaba egreso.")
            else:
                scope, label, value = cells[:3]
            scope, label = str(scope or "").strip(), str(label or "").strip()
            if not scope or not label or len(scope) > 80 or len(label) > 200:
                raise ValueError(f"Fila {number}: fase o partida inválida.")
            key = service._scoped_budget_concept_key(label, scope)
            if len(key) > 200:
                raise ValueError(
                    f"Fila {number}: clave de partida excede 200 caracteres."
                )
            if key in keys:
                raise ValueError(
                    f"Fila {number}: partida duplicada dentro de la misma fase."
                )
            keys.add(key)
            raw = _money(value)
            amount = raw.quantize(CENT, rounding=ROUND_HALF_UP)
            raw_total += raw
            groups[scope] += amount
            rows.append(
                {
                    "row": number,
                    "phase": scope,
                    "concept_name": label,
                    "concept_key": key,
                    "original_amount": str(raw),
                    "budget_amount": str(amount),
                }
            )
        if not rows:
            raise ValueError("El archivo no contiene partidas.")
        total = sum(groups.values(), Decimal(0))
        return {
            "layout": layout,
            "edition_year": edition_year,
            "filename": Path(filename).name,
            "sheet": sheet.title,
            "source_sha256": hashlib.sha256(file_bytes).hexdigest(),
            "rows": rows,
            "row_count": len(rows),
            "original_total": str(raw_total),
            "total": str(total),
            "rounding_delta": str(
                total - raw_total.quantize(CENT, rounding=ROUND_HALF_UP)
            ),
            "rounding_rule": "ROUND_HALF_UP per line; no automatic adjustment",
            "by_phase": {k: str(v) for k, v in groups.items()},
        }
    finally:
        book.close()


def build_upload_plan(
    parsed: dict[str, Any],
    *,
    tournament: dict[str, Any],
    version: dict[str, Any],
    concepts: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    currency: str | None = None,
    tax_basis: str | None = None,
    confirmed_rounding: bool = False,
    confirmed_updates: bool = False,
) -> dict[str, Any]:
    """Compare the upload with an exact registered tournament/version snapshot."""
    tournament_id = str(tournament["id"])
    blockers = []
    expected = {
        "HWC": "homeless_world_cup_mexico",
        "LTTB": "liga_telmex_telcel_de_beisbol",
    }
    if (
        service._normalize_budget_key(tournament.get("name") or "")
        != expected[parsed["layout"]]
    ):
        blockers.append("El formato no corresponde al torneo registrado seleccionado.")
    if not tournament.get("active"):
        blockers.append("Torneo inactivo.")
    if int(version["edition_year"]) != parsed["edition_year"]:
        blockers.append("Edición de versión incompatible.")
    if version["status"] not in {"draft", "reforecast"}:
        blockers.append("La versión no permite modificaciones.")
    if not currency or not re.fullmatch(r"[A-Z]{3}", currency):
        blockers.append("Confirma moneda con código de tres letras.")
    if tax_basis not in {"includes_vat", "excludes_vat"}:
        blockers.append("Confirma si los importes incluyen IVA.")
    fractional = any(
        _money(r["original_amount"]) != _money(r["budget_amount"])
        for r in parsed["rows"]
    )
    if fractional and not confirmed_rounding:
        blockers.append("Confirma el redondeo por partida y su diferencia.")
    scoped_catalog = []
    for concept in concepts:
        if (
            str(concept.get("tournament_id")) == tournament_id
            and concept.get("active", True)
            and concept.get("budget_direction", "expense") == "expense"
        ):
            scoped_catalog.append(concept)
    by_concept = defaultdict(list)
    for line in lines:
        if str(line.get("tournament_id")) != tournament_id:
            blockers.append(
                "Hay líneas sin identidad exacta del torneo: "
                "reconciliar antes de cargar."
            )
            continue
        if str(line.get("budget_version_id")) != str(version["id"]):
            blockers.append("Línea de otra versión: reconciliar antes de cargar.")
            continue
        if line.get("line_direction", "expense") == "expense":
            by_concept[str(line.get("budget_concept_id"))].append(line)
    operations = []
    for row in parsed["rows"]:
        matches = []
        for concept in scoped_catalog:
            metadata = concept.get("metadata") or {}
            labels = (metadata.get("applicable_phase_labels") or []) + (
                metadata.get("applicable_subproject_labels") or []
            )
            scope_match = any(
                service._normalize_budget_key(label)
                == service._normalize_budget_key(row["phase"])
                for label in labels
            )
            same_name = service._normalize_budget_key(
                concept.get("concept_name") or ""
            ) == service._normalize_budget_key(row["concept_name"])
            if concept["concept_key"] == row["concept_key"] or (
                same_name and scope_match
            ):
                matches.append(concept)
        op = {
            **row,
            "action": "create",
            "concept_id": None,
            "line_id": None,
            "before_amount": None,
            "before_phase": None,
            "before_upload_source": None,
            "had_upload_source": False,
        }
        if len(matches) > 1:
            op["action"] = "conflict"
        elif matches:
            op["concept_id"] = str(matches[0]["id"])
            existing = by_concept[op["concept_id"]]
            if len(existing) > 1:
                op["action"] = "conflict"
            elif existing:
                current = existing[0]
                op["line_id"] = str(current["id"])
                op["before_phase"] = current.get("phase")
                metadata = current.get("metadata") or {}
                op["had_upload_source"] = "upload_source" in metadata
                op["before_upload_source"] = metadata.get("upload_source")
                op["before_amount"] = str(
                    _money(current["budget_amount"]).quantize(CENT)
                )
                op["action"] = (
                    "unchanged"
                    if op["before_amount"] == row["budget_amount"]
                    and service._normalize_budget_key(current.get("phase") or "")
                    == service._normalize_budget_key(row["phase"])
                    and (current.get("metadata") or {})
                    .get("upload_source", {})
                    .get("source_sha256")
                    == parsed["source_sha256"]
                    and (current.get("metadata") or {})
                    .get("upload_source", {})
                    .get("currency")
                    == currency
                    and (current.get("metadata") or {})
                    .get("upload_source", {})
                    .get("tax_basis")
                    == tax_basis
                    else "update"
                )
        # A legacy unscoped line must never be silently doubled by a new scoped concept.
        if not matches and any(
            service._normalize_budget_key(existing_line.get("concept_name") or "")
            == service._normalize_budget_key(row["concept_name"])
            and (
                not existing_line.get("phase")
                or existing_line.get("phase") == row["phase"]
            )
            for existing_line in lines
        ):
            op["action"] = "conflict"
        if op["action"] == "conflict":
            blockers.append(
                f"Fila {row['row']}: presupuesto existente requiere conciliación."
            )
        operations.append(op)
    concept_use = Counter(op["concept_id"] for op in operations if op["concept_id"])
    line_use = Counter(op["line_id"] for op in operations if op["line_id"])
    for op in operations:
        if concept_use[op["concept_id"]] > 1 or line_use[op["line_id"]] > 1:
            op["action"] = "conflict"
            blockers.append(
                f"Fila {op['row']}: identidad compartida entre fases; "
                "requiere conciliación."
            )
    if any(op["action"] == "update" for op in operations) and not confirmed_updates:
        blockers.append(
            "Confirma las actualizaciones de importes existentes "
            "mostradas en la vista previa."
        )
    plan = {
        **parsed,
        "tournament": tournament,
        "version": version,
        "currency": currency,
        "tax_basis": tax_basis,
        "confirmed_rounding": confirmed_rounding,
        "confirmed_updates": confirmed_updates,
        "operations": operations,
        "blockers": sorted(set(blockers)),
        "counts": dict(Counter(op["action"] for op in operations)),
        "read_only": True,
    }
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return plan


async def preview_tournament_upload(
    session: Any,
    *,
    tournament_id: str,
    version_id: str,
    edition_year: int,
    file_bytes: bytes,
    filename: str,
    currency: str | None = None,
    tax_basis: str | None = None,
    confirmed_rounding: bool = False,
    confirmed_updates: bool = False,
) -> dict[str, Any]:
    """Read exact identities and build a preview without schema or business writes."""
    tournament = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, name, active "
                    "FROM tournaments WHERE id = :id"
                ),
                {"id": tournament_id},
            )
        )
        .mappings()
        .first()
    )
    if not tournament:
        raise ValueError("Torneo no encontrado.")
    version = await service.get_budget_version(
        session, version_id=version_id, ensure_schema=False
    )
    concepts = await service.list_budget_concepts(
        session, tournament_id=tournament_id, limit=5000
    )
    # Include legacy code/name rows as blockers; never silently bypass their amounts.
    code = service._derive_budget_tournament_code(
        tournament["name"], tournament["name"]
    )
    lines = await service.list_budget_lines(
        session,
        version_id=version_id,
        tournament_code=code,
        limit=5000,
        ensure_schema=False,
    )
    exact = await service.list_budget_lines(
        session,
        version_id=version_id,
        tournament_id=tournament_id,
        limit=5000,
        ensure_schema=False,
    )
    lines = list({str(r["id"]): r for r in [*lines, *exact]}.values())
    if len(concepts) >= 5000 or len(lines) >= 5000:
        raise ValueError(
            "El alcance excede el límite: requiere conciliación explícita."
        )
    parsed = parse_tournament_budget(
        file_bytes=file_bytes, filename=filename, edition_year=edition_year
    )
    return build_upload_plan(
        parsed,
        tournament=dict(tournament),
        version=version,
        concepts=concepts,
        lines=lines,
        currency=currency,
        tax_basis=tax_basis,
        confirmed_rounding=confirmed_rounding,
        confirmed_updates=confirmed_updates,
    )


async def apply_tournament_upload(
    session: Any,
    *,
    actor_empleado_id: str,
    approved_plan_sha256: str,
    confirmed: bool,
    **preview_args: Any,
) -> dict[str, Any]:
    """Apply a fresh plan atomically; the caller checks actor authority."""
    if not confirmed or not actor_empleado_id or not approved_plan_sha256:
        raise ValueError("Se requiere actor, confirmación y hash de la vista previa.")
    try:
        # Serialize this import against all writers of its version and tournament.
        await session.execute(
            text("SELECT id FROM budget_versions WHERE id = :id FOR UPDATE"),
            {"id": preview_args["version_id"]},
        )
        await session.execute(
            text("SELECT id FROM tournaments WHERE id = :id FOR UPDATE"),
            {"id": preview_args["tournament_id"]},
        )
        await session.execute(
            text(
                "SELECT id FROM budget_lines WHERE budget_version_id = :version_id "
                "AND tournament_id = :tournament_id FOR UPDATE"
            ),
            {
                "version_id": preview_args["version_id"],
                "tournament_id": preview_args["tournament_id"],
            },
        )
        await session.execute(
            text("SELECT id FROM budget_concepts WHERE tournament_id = :id FOR UPDATE"),
            {"id": preview_args["tournament_id"]},
        )
        plan = await preview_tournament_upload(session, **preview_args)
        if plan["plan_sha256"] != approved_plan_sha256:
            raise ValueError("La vista previa cambió: vuelve a revisarla.")
        if plan["blockers"]:
            raise ValueError("La vista previa tiene pendientes sin resolver.")
        evidence = {
            k: plan[k]
            for k in (
                "filename",
                "sheet",
                "source_sha256",
                "currency",
                "tax_basis",
                "rounding_rule",
            )
        }
        for op in plan["operations"]:
            if op["action"] == "unchanged":
                continue
            provenance = {
                **evidence,
                "row": op["row"],
                "original_amount": op["original_amount"],
                "plan_sha256": plan["plan_sha256"],
            }
            concept_id = op["concept_id"]
            if not concept_id:
                concept = await service.create_budget_concept(
                    session,
                    tournament_id=plan["tournament"]["id"],
                    concept_name=op["concept_name"],
                    concept_key=op["concept_key"],
                    scope_labels=[op["phase"]],
                    budget_direction="expense",
                    actor_empleado_id=actor_empleado_id,
                    source="tournament_upload",
                    extra_metadata={"upload_source": provenance},
                    commit=False,
                    ensure_schema=False,
                )
                concept_id = concept["id"]
            if op["action"] == "update":
                await service.update_budget_line(
                    session,
                    line_id=op["line_id"],
                    actor_empleado_id=actor_empleado_id,
                    updates={
                        "budget_amount": op["budget_amount"],
                        "phase": op["phase"],
                    },
                    extra_metadata={"upload_source": provenance},
                    tournament_id=plan["tournament"]["id"],
                    commit=False,
                    ensure_schema=False,
                )
                continue
            await service.create_budget_line(
                session,
                version_id=plan["version"]["id"],
                actor_empleado_id=actor_empleado_id,
                budget_concept_id=concept_id,
                tournament_name=plan["tournament"]["name"],
                tournament_id=plan["tournament"]["id"],
                line_direction="expense",
                concept_name=op["concept_name"],
                phase=op["phase"],
                budget_amount=op["budget_amount"],
                extra_metadata={"upload_source": provenance},
                commit=False,
                ensure_schema=False,
            )
        await service._audit_budget_event(
            session,
            budget_version_id=plan["version"]["id"],
            event_type="tournament_upload_applied",
            actor_empleado_id=actor_empleado_id,
            payload={
                **evidence,
                "counts": plan["counts"],
                "plan_sha256": plan["plan_sha256"],
                "operations": plan["operations"],
            },
        )
        await session.commit()
        return {
            "applied": True,
            "counts": plan["counts"],
            "plan_sha256": plan["plan_sha256"],
        }
    except Exception:
        await session.rollback()
        raise
