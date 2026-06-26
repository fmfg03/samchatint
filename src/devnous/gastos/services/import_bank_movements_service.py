"""
Bank statement CSV import service.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AccountingImportRun,
    AccountingPoliza,
    AuxLedgerEntry,
    BankMovement,
    ExpenseReport,
    ProveedorCliente,
)


@dataclass
class ParsedBankMovement:
    source_row_number: int
    cuenta_bancaria: Optional[str]
    fecha: Optional[datetime]
    hora: Optional[str]
    sucursal: Optional[str]
    descripcion: Optional[str]
    signo: Optional[str]
    importe: Optional[float]
    saldo: Optional[float]
    referencia_bancaria: Optional[str]
    concepto_banco: Optional[str]
    banco_participante: Optional[str]
    clabe_beneficiario: Optional[str]
    nombre_beneficiario: Optional[str]
    cuenta_ordenante: Optional[str]
    nombre_ordenante: Optional[str]
    codigo_devolucion: Optional[str]
    causa_devolucion: Optional[str]
    rfc_beneficiario: Optional[str]
    rfc_ordenante: Optional[str]
    clave_rastreo: Optional[str]
    descripcion_larga: Optional[str]
    raw_row: Dict[str, Any]


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("'"):
        text = text[1:]
    return text.strip()


def _clean_digits(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits or None


def _parse_float(value: Any) -> Optional[float]:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _parse_bank_date(value: Any) -> Optional[datetime]:
    text = _clean_digits(value)
    if not text:
        return None
    for fmt in ("%d%m%Y", "%d%m%y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _normalize_name(value: Optional[str]) -> str:
    text = (value or "").lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñü\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_bank_movements_csv(filename: str, contents: bytes) -> List[ParsedBankMovement]:
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            decoded = contents.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("No se pudo decodificar el archivo bancario")

    reader = csv.DictReader(io.StringIO(decoded))
    required = {"Cuenta", "Fecha", "Descripcion", "Cargo/Abono", "Importe", "Saldo"}
    missing = required.difference(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Faltan columnas requeridas en CSV bancario: {', '.join(sorted(missing))}")

    rows: List[ParsedBankMovement] = []
    for row_number, row in enumerate(reader, start=2):
        rows.append(
            ParsedBankMovement(
                source_row_number=row_number,
                cuenta_bancaria=_clean_digits(row.get("Cuenta")),
                fecha=_parse_bank_date(row.get("Fecha")),
                hora=_clean_text(row.get("Hora")),
                sucursal=_clean_text(row.get("Sucursal")),
                descripcion=_clean_text(row.get("Descripcion")),
                signo=_clean_text(row.get("Cargo/Abono")),
                importe=_parse_float(row.get("Importe")),
                saldo=_parse_float(row.get("Saldo")),
                referencia_bancaria=_clean_text(row.get("Referencia")),
                concepto_banco=_clean_text(row.get("Concepto")),
                banco_participante=_clean_text(row.get("Banco Participante")),
                clabe_beneficiario=_clean_digits(row.get("Clabe Beneficiario")),
                nombre_beneficiario=_clean_text(row.get("Nombre Beneficiario")),
                cuenta_ordenante=_clean_digits(row.get("Cta Ordenante")),
                nombre_ordenante=_clean_text(row.get("Nombre Ordenante")),
                codigo_devolucion=_clean_text(row.get("Codigo Devolucion")),
                causa_devolucion=_clean_text(row.get("Causa Devolucion")),
                rfc_beneficiario=_clean_text(row.get("RFC Beneficiario")),
                rfc_ordenante=_clean_text(row.get("RFC Ordenante")),
                clave_rastreo=_clean_text(row.get("Clave de Rastreo")),
                descripcion_larga=_clean_text(row.get("Descripcion Larga")),
                raw_row={k: _clean_text(v) for k, v in row.items()},
            )
        )
    return rows


async def _match_proveedor(session: AsyncSession, parsed: ParsedBankMovement) -> Optional[ProveedorCliente]:
    if parsed.clabe_beneficiario:
        result = await session.execute(
            select(ProveedorCliente).where(ProveedorCliente.cuenta_clabe == parsed.clabe_beneficiario)
        )
        proveedores = result.scalars().all()
        if len(proveedores) == 1:
            return proveedores[0]
        if proveedores and parsed.nombre_beneficiario:
            normalized_name = _normalize_name(parsed.nombre_beneficiario)
            for proveedor in proveedores:
                if _normalize_name(getattr(proveedor, "nombre", None)) == normalized_name:
                    return proveedor
            return proveedores[0]

    if parsed.nombre_beneficiario:
        normalized_name = _normalize_name(parsed.nombre_beneficiario)
        result = await session.execute(select(ProveedorCliente))
        for proveedor in result.scalars().all():
            if _normalize_name(getattr(proveedor, "nombre", None)) == normalized_name:
                return proveedor

    return None


def _score_aux_match(parsed: ParsedBankMovement, aux: AuxLedgerEntry) -> int:
    score = 0
    bank_text = " ".join(
        part for part in [parsed.descripcion or "", parsed.concepto_banco or "", parsed.nombre_beneficiario or ""] if part
    ).lower()
    aux_text = (aux.concepto or "").lower()
    if parsed.fecha and aux.fecha and parsed.fecha == aux.fecha:
        score += 2
    if parsed.importe is not None:
        if aux.haber == parsed.importe and (parsed.signo or "") == "-":
            score += 2
        elif aux.debe == parsed.importe and (parsed.signo or "") == "+":
            score += 2
        elif aux.debe == parsed.importe or aux.haber == parsed.importe:
            score += 1
    if parsed.referencia_bancaria and parsed.referencia_bancaria in aux_text:
        score += 4
    if parsed.nombre_beneficiario and _normalize_name(parsed.nombre_beneficiario) in _normalize_name(aux.concepto or ""):
        score += 3
    for token in re.findall(r"[a-z0-9áéíóúñü]{4,}", bank_text):
        if token in aux_text:
            score += 1
    if aux.related_poliza_id:
        score += 3
    return score


async def _match_aux_entry(session: AsyncSession, parsed: ParsedBankMovement) -> Tuple[Optional[AuxLedgerEntry], int]:
    if not parsed.fecha or parsed.importe is None:
        return None, 0

    result = await session.execute(
        select(AuxLedgerEntry).where(
            and_(
                AuxLedgerEntry.fecha == parsed.fecha,
                ((AuxLedgerEntry.debe == parsed.importe) | (AuxLedgerEntry.haber == parsed.importe)),
            )
        )
    )
    candidates = result.scalars().all()
    if not candidates:
        return None, 0
    if len(candidates) == 1:
        candidate = candidates[0]
        return candidate, _score_aux_match(parsed, candidate)
    scored = sorted(((c, _score_aux_match(parsed, c)) for c in candidates), key=lambda item: item[1], reverse=True)
    if scored and scored[0][1] > 0:
        return scored[0][0], scored[0][1]
    return None, 0


async def _match_expense(session: AsyncSession, parsed: ParsedBankMovement) -> Optional[ExpenseReport]:
    if not parsed.fecha or parsed.importe is None:
        return None

    lower_bound = parsed.fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    upper_bound = lower_bound + timedelta(days=8)
    result = await session.execute(
        select(ExpenseReport).where(
            and_(
                ExpenseReport.fecha >= lower_bound - timedelta(days=7),
                ExpenseReport.fecha < upper_bound,
                ExpenseReport.gasto_cantidad >= parsed.importe - 5.0,
                ExpenseReport.gasto_cantidad <= parsed.importe + 5.0,
                ExpenseReport.estado_gasto != "cancelado",
            )
        )
    )
    candidates = result.scalars().all()
    if not candidates:
        return None

    def _score(expense: ExpenseReport) -> int:
        score = 0
        if expense.fecha:
            day_delta = abs((expense.fecha.date() - parsed.fecha.date()).days)
            if day_delta == 0:
                score += 3
            elif day_delta <= 3:
                score += 2
            elif day_delta <= 7:
                score += 1
        diff = abs(float(expense.gasto_cantidad or 0) - float(parsed.importe or 0))
        if diff < 0.01:
            score += 4
        elif diff <= 5:
            score += 2

        bank_text = " ".join(
            part for part in [
                parsed.descripcion or "",
                parsed.concepto_banco or "",
                parsed.nombre_beneficiario or "",
                parsed.referencia_bancaria or "",
            ] if part
        ).lower()
        expense_text = " ".join(
            part for part in [
                expense.concepto or "",
                expense.proyecto or "",
                expense.usuario_nombre or "",
                expense.nombre_enviador or "",
                expense.numero_referencia or "",
            ] if part
        ).lower()

        if parsed.referencia_bancaria and expense.numero_referencia and parsed.referencia_bancaria in expense.numero_referencia:
            score += 5
        if parsed.nombre_beneficiario:
            normalized_provider = _normalize_name(parsed.nombre_beneficiario)
            if normalized_provider and normalized_provider in _normalize_name(expense.nombre_enviador or expense.usuario_nombre or ""):
                score += 3
        for token in re.findall(r"[a-z0-9áéíóúñü]{4,}", bank_text):
            if token in expense_text:
                score += 1
        if expense.cuenta_contable_id:
            score += 1
        if expense.cfdi_report_id:
            score += 1
        return score

    scored = sorted(((expense, _score(expense)) for expense in candidates), key=lambda item: item[1], reverse=True)
    if not scored or scored[0][1] <= 0:
        return None
    if len(scored) == 1:
        return scored[0][0]
    if scored[0][1] == scored[1][1]:
        return None
    return scored[0][0]


def _compute_conciliacion_estado(
    *,
    aux: Optional[AuxLedgerEntry],
    aux_score: int,
    proveedor: Optional[ProveedorCliente],
    expense: Optional[ExpenseReport],
    poliza: Optional[AccountingPoliza],
) -> str:
    if aux and (poliza or aux_score >= 7 or (proveedor and aux_score >= 4)):
        return "high"
    if aux or proveedor or expense:
        return "medium"
    return "unmatched"


async def import_bank_movements_csv(
    session: AsyncSession,
    *,
    filename: str,
    contents: bytes,
    apply_changes: bool,
    started_by_empleado_id: Optional[Any] = None,
) -> Dict[str, Any]:
    rows = parse_bank_movements_csv(filename, contents)
    file_sha = hashlib.sha256(contents).hexdigest()

    run = AccountingImportRun(
        id=uuid4(),
        source_type="banco",
        filename=filename,
        source_sha256=file_sha,
        mode="apply" if apply_changes else "dry_run",
        status="completed",
        started_by_empleado_id=started_by_empleado_id,
        started_at=datetime.utcnow(),
    )
    session.add(run)
    await session.flush()

    created = 0
    updated = 0
    matched_proveedor = 0
    matched_aux = 0
    matched_expense = 0
    related_poliza = 0
    samples: List[Dict[str, Any]] = []

    for parsed in rows:
        existing = (
            await session.execute(
                select(BankMovement).where(
                    BankMovement.source_file == filename,
                    BankMovement.source_row_number == parsed.source_row_number,
                )
            )
        ).scalar_one_or_none()

        proveedor = await _match_proveedor(session, parsed)
        aux, aux_score = await _match_aux_entry(session, parsed)
        expense = await _match_expense(session, parsed)
        poliza = aux.related_poliza if aux else None

        if proveedor:
            matched_proveedor += 1
        if aux:
            matched_aux += 1
        if expense:
            matched_expense += 1
        if poliza:
            related_poliza += 1

        conciliacion_estado = _compute_conciliacion_estado(
            aux=aux,
            aux_score=aux_score,
            proveedor=proveedor,
            expense=expense,
            poliza=poliza,
        )

        payload = {
            "import_run_id": run.id,
            "cuenta_bancaria": parsed.cuenta_bancaria,
            "fecha": parsed.fecha,
            "hora": parsed.hora,
            "sucursal": parsed.sucursal,
            "descripcion": parsed.descripcion,
            "signo": parsed.signo,
            "importe": parsed.importe,
            "saldo": parsed.saldo,
            "referencia_bancaria": parsed.referencia_bancaria,
            "concepto_banco": parsed.concepto_banco,
            "banco_participante": parsed.banco_participante,
            "clabe_beneficiario": parsed.clabe_beneficiario,
            "nombre_beneficiario": parsed.nombre_beneficiario,
            "cuenta_ordenante": parsed.cuenta_ordenante,
            "nombre_ordenante": parsed.nombre_ordenante,
            "codigo_devolucion": parsed.codigo_devolucion,
            "causa_devolucion": parsed.causa_devolucion,
            "rfc_beneficiario": parsed.rfc_beneficiario,
            "rfc_ordenante": parsed.rfc_ordenante,
            "clave_rastreo": parsed.clave_rastreo,
            "descripcion_larga": parsed.descripcion_larga,
            "proveedor_cliente_id": proveedor.id if proveedor else None,
            "matched_aux_entry_id": aux.id if aux else None,
            "related_poliza_id": poliza.id if poliza else None,
            "matched_expense_id": expense.id if expense else None,
            "conciliacion_estado": conciliacion_estado,
            "raw_row_json": parsed.raw_row,
        }

        if existing:
            updated += 1
            if apply_changes:
                for key, value in payload.items():
                    setattr(existing, key, value)
        else:
            created += 1
            if apply_changes:
                session.add(
                    BankMovement(
                        id=uuid4(),
                        source_file=filename,
                        source_row_number=parsed.source_row_number,
                        **payload,
                    )
                )

        if len(samples) < 10:
            samples.append(
                {
                    "row": parsed.source_row_number,
                    "fecha": parsed.fecha.isoformat() if parsed.fecha else None,
                    "descripcion": parsed.descripcion,
                    "importe": parsed.importe,
                    "referencia": parsed.referencia_bancaria,
                    "beneficiario": parsed.nombre_beneficiario,
                    "estado": conciliacion_estado,
                }
            )

    run.finished_at = datetime.utcnow()
    run.summary_json = {
        "entries": len(rows),
        "created": created,
        "updated": updated,
        "matched_proveedor": matched_proveedor,
        "matched_aux": matched_aux,
        "matched_expense": matched_expense,
        "related_poliza": related_poliza,
    }

    if apply_changes:
        await session.commit()
    else:
        await session.rollback()

    return {
        "mode": "apply" if apply_changes else "dry_run",
        "file": filename,
        "sha256": file_sha,
        "entries": len(rows),
        "created": created,
        "updated": updated,
        "matched_proveedor": matched_proveedor,
        "matched_aux": matched_aux,
        "matched_expense": matched_expense,
        "related_poliza": related_poliza,
        "samples": samples,
    }
