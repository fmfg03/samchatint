from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
import uuid
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HISTORICAL_2023_MANIFEST = {
    "trial_balance": _ROOT
    / "Conta2025"
    / "conta_2023"
    / "reportes_2023"
    / "balanzas_2023_consolidado.csv",
    "policy_headers": _ROOT
    / "Conta2025"
    / "conta_2023"
    / "reportes_2023"
    / "polizas_2023_resumen.csv",
    "policy_lines": _ROOT
    / "Conta2025"
    / "conta_2023"
    / "reportes_2023"
    / "polizas_2023_movimientos.csv",
}
DEFAULT_HISTORICAL_2024_MANIFEST = {
    "trial_balance": _ROOT
    / "Conta2025"
    / "conta_2024"
    / "reportes_2024"
    / "balanzas_2024_consolidado.csv",
    "policy_headers": _ROOT
    / "Conta2025"
    / "conta_2024"
    / "reportes_2024"
    / "polizas_2024_resumen.csv",
    "policy_lines": _ROOT
    / "Conta2025"
    / "conta_2024"
    / "reportes_2024"
    / "polizas_2024_movimientos.csv",
}
DEFAULT_HISTORICAL_2025_MANIFEST = {
    "trial_balance": _ROOT / "Conta2025" / "reportes_2025" / "balanzas_2025_consolidado.csv",
    "policy_headers": _ROOT / "Conta2025" / "reportes_2025" / "polizas_2025_resumen.csv",
    "policy_lines": _ROOT / "Conta2025" / "reportes_2025" / "polizas_2025_movimientos.csv",
}
DEFAULT_HISTORICAL_2026_Q1_MANIFEST = {
    "trial_balance": _ROOT
    / "reports"
    / "accounting_knowledge"
    / "plataforma_sports_q1_2026"
    / "balanzas_q1_2026_normalized.csv",
}
SUPPORTED_HISTORICAL_MANIFESTS = {
    2023: DEFAULT_HISTORICAL_2023_MANIFEST,
    2024: DEFAULT_HISTORICAL_2024_MANIFEST,
    2025: DEFAULT_HISTORICAL_2025_MANIFEST,
    2026: DEFAULT_HISTORICAL_2026_Q1_MANIFEST,
}
DEFAULT_COMPANY_METADATA = {
    "01": {"label": "PSP1705058S4", "rfc": "PSP1705058S4"},
    "02": {"label": "PMD0608162M2", "rfc": "PMD0608162M2"},
    "04": {"label": "PSP1705058S4", "rfc": "PSP1705058S4"},
}
MONTH_NAMES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    raw = _safe_str(value)
    if not raw:
        return 0
    return int(float(raw))


def _safe_decimal(value: Any) -> float:
    raw = _safe_str(value).replace(",", "")
    if not raw:
        return 0.0
    return float(raw)


def _safe_bool(value: Any) -> bool:
    return _safe_str(value).lower() in {"1", "true", "yes", "si", "sí"}


def _parse_date(value: Any) -> Optional[date]:
    raw = _safe_str(value)
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _infer_account_level_from_canonical_code(account_code: Any) -> int:
    parts = _safe_str(account_code).split("-")
    if not parts:
        return 0
    level = 1
    for part in parts[1:]:
        if part and part != "000":
            level += 1
    return level


def _load_balance_only_trial_balance_rows(path: Path) -> list[dict[str, Any]]:
    raw_rows = _load_csv_rows(path)
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw_rows, start=1):
        month_key = _safe_str(row.get("month_key"))
        month_num = _safe_int(month_key.split("-")[-1] if "-" in month_key else "")
        account_code = _safe_str(row.get("account_code"))
        normalized_rows.append(
            {
                "row_number": index,
                "month_num": month_num,
                "account_code": account_code,
                "description": _safe_str(row.get("account_name")),
                "level": _infer_account_level_from_canonical_code(account_code),
                "is_detail": _safe_bool(row.get("is_leaf")),
                "saldo_inicial": _safe_decimal(row.get("opening_balance")),
                "cargos": _safe_decimal(row.get("total_debits")),
                "abonos": _safe_decimal(row.get("total_credits")),
                "saldo_final": _safe_decimal(row.get("closing_balance")),
            }
        )
    return normalized_rows


def _normalize_company_code(value: Any) -> str:
    raw = _safe_str(value)
    if not raw:
        return "01"
    digits = "".join(char for char in raw if char.isdigit())
    if digits:
        return digits.zfill(2)[-2:]
    return raw.upper()


def _default_company_label(company_code: str) -> str:
    normalized = _normalize_company_code(company_code)
    return DEFAULT_COMPANY_METADATA.get(normalized, {}).get(
        "label", f"Empresa COI {normalized}"
    )


def _infer_company_identity_from_path(source_path: str) -> tuple[str, str]:
    lowered = (source_path or "").lower()
    if "empre2" in lowered or "empresa2" in lowered or "numemp=02" in lowered:
        return "02", _default_company_label("02")
    if "empre4" in lowered or "empresa4" in lowered or "numemp=04" in lowered:
        return "04", _default_company_label("04")
    return "01", _default_company_label("01")


def _normalize_coi_account_code(value: Any) -> str:
    digits = "".join(char for char in _safe_str(value) if char.isdigit())
    if len(digits) < 10:
        return _safe_str(value)
    return f"{digits[:4]}-{digits[4:7]}-{digits[7:10]}"


def _parse_firebird_list_output(
    stdout: str,
    *,
    expected_fields: Optional[set[str]] = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: Optional[str] = None
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            if current:
                rows.append(current)
                current = {}
                last_key = None
            continue
        if expected_fields and raw_line[:1].isspace() and last_key:
            current[last_key] = f"{current.get(last_key, '')} {line.strip()}".strip()
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        key = parts[0].strip()
        if expected_fields and key not in expected_fields and last_key:
            current[last_key] = f"{current.get(last_key, '')} {line.strip()}".strip()
            continue
        value = parts[1].strip() if len(parts) > 1 else ""
        current[key] = value
        last_key = key
    if current:
        rows.append(current)
    return rows


def _run_firebird_query(
    database_path: Path,
    sql: str,
    *,
    expected_fields: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    command = [
        "isql-fb",
        "-user",
        "SYSDBA",
        "-pas",
        "masterkey",
        str(database_path),
        "-q",
    ]
    sql_input = f"set list on;\n{sql.strip().rstrip(';')};\n"
    result = subprocess.run(
        command,
        input=sql_input,
        capture_output=True,
        text=True,
        encoding="latin-1",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Firebird query failed for {database_path}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return _parse_firebird_list_output(
        result.stdout,
        expected_fields=set(expected_fields or []),
    )


def _build_coi_trial_balance_rows(
    *,
    fiscal_year: int,
    coi_balance_rows: list[dict[str, Any]],
    account_catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_row in coi_balance_rows:
        account_key = _safe_str(raw_row.get("NUM_CTA"))
        account_meta = account_catalog.get(account_key, {})
        account_code = _normalize_coi_account_code(account_key)
        opening_balance = _safe_decimal(raw_row.get("INICIAL"))
        for month_num in range(1, 13):
            debits = _safe_decimal(raw_row.get(f"CARGO{month_num:02d}"))
            credits = _safe_decimal(raw_row.get(f"ABONO{month_num:02d}"))
            closing_balance = opening_balance + debits - credits
            rows.append(
                {
                    "month_num": f"{month_num:02d}",
                    "month_name": MONTH_NAMES[month_num],
                    "file_name": f"coi_saldos_{fiscal_year}.fdb",
                    "row_number": len(rows) + 1,
                    "account_code": account_code,
                    "description": _safe_str(account_meta.get("NOMBRE")),
                    "level": _safe_int(account_meta.get("NIVEL")) + 1,
                    "is_detail": "no" if account_code.endswith("-000") else "yes",
                    "saldo_inicial": f"{opening_balance:.2f}",
                    "cargos": f"{debits:.2f}",
                    "abonos": f"{credits:.2f}",
                    "saldo_final": f"{closing_balance:.2f}",
                }
            )
            opening_balance = closing_balance
    return rows


def _build_coi_policy_rows(
    *,
    fiscal_year: int,
    coi_policy_line_rows: list[dict[str, Any]],
    account_catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sequence_by_policy_key: dict[tuple[int, str, str], int] = defaultdict(int)
    totals_by_policy_key: dict[tuple[int, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "line_count": 0,
            "total_debe": 0.0,
            "total_haber": 0.0,
            "policy_date": "",
            "description": "",
        }
    )

    policy_line_rows: list[dict[str, Any]] = []
    for raw_row in coi_policy_line_rows:
        month_num = _safe_int(raw_row.get("PERIODO"))
        policy_type = _safe_str(raw_row.get("TIPO_POLI"))
        policy_number = _safe_str(raw_row.get("NUM_POLIZ"))
        policy_key = (month_num, policy_type, policy_number)
        sequence = sequence_by_policy_key[policy_key] or 1
        debit_amount = _safe_decimal(raw_row.get("MONTOMOV")) if _safe_str(raw_row.get("DEBE_HABER")) == "D" else 0.0
        credit_amount = _safe_decimal(raw_row.get("MONTOMOV")) if _safe_str(raw_row.get("DEBE_HABER")) == "H" else 0.0
        totals = totals_by_policy_key[policy_key]
        totals["line_count"] += 1
        totals["total_debe"] += debit_amount
        totals["total_haber"] += credit_amount
        totals["policy_date"] = totals["policy_date"] or _safe_str(raw_row.get("FECHA_POL")).split(" ")[0]
        totals["description"] = totals["description"] or _safe_str(raw_row.get("CONCEP_PO"))
        policy_id = f"{month_num:02d}-{policy_type}-{policy_number}-{sequence:04d}"
        account_key = _safe_str(raw_row.get("NUM_CTA"))
        account_meta = account_catalog.get(account_key, {})
        policy_line_rows.append(
            {
                "month_num": f"{month_num:02d}",
                "month_name": MONTH_NAMES.get(month_num, ""),
                "file_name": f"coi_polizas_{fiscal_year}.fdb",
                "policy_id": policy_id,
                "policy_type": policy_type,
                "policy_number": policy_number,
                "policy_date": _safe_str(raw_row.get("FECHA_POL")).split(" ")[0],
                "policy_description": _safe_str(raw_row.get("CONCEP_PO")),
                "row_number": _safe_int(raw_row.get("NUM_PART")),
                "account_code": _normalize_coi_account_code(account_key),
                "account_name": _safe_str(account_meta.get("NOMBRE")),
                "debe": f"{debit_amount:.2f}",
                "haber": f"{credit_amount:.2f}",
                "concept": _safe_str(raw_row.get("CONCEP_PO")),
            }
        )

    policy_header_rows: list[dict[str, Any]] = []
    for policy_key in sorted(totals_by_policy_key):
        month_num, policy_type, policy_number = policy_key
        policy_key = (month_num, policy_type, policy_number)
        sequence_by_policy_key[policy_key] += 1
        sequence = sequence_by_policy_key[policy_key]
        totals = totals_by_policy_key[policy_key]
        policy_header_rows.append(
            {
                "month_num": f"{month_num:02d}",
                "month_name": MONTH_NAMES.get(month_num, ""),
                "file_name": f"coi_polizas_{fiscal_year}.fdb",
                "policy_id": f"{month_num:02d}-{policy_type}-{policy_number}-{sequence:04d}",
                "policy_type": policy_type,
                "policy_number": policy_number,
                "policy_date": totals["policy_date"],
                "description": totals["description"],
                "line_count": totals["line_count"],
                "total_debe": f"{totals['total_debe']:.2f}",
                "total_haber": f"{totals['total_haber']:.2f}",
                "summary_debe": f"{totals['total_debe']:.2f}",
                "summary_haber": f"{totals['total_haber']:.2f}",
                "has_summary": "true",
            }
        )

    return policy_header_rows, policy_line_rows


def _restore_coi_backup_to_fdb(source_path: Path) -> tuple[Path, Optional[Path]]:
    if source_path.suffix.lower() == ".fdb":
        return source_path, None
    if source_path.suffix.lower() != ".fbk":
        raise ValueError(f"Unsupported COI source extension: {source_path.suffix}")

    temp_dir = Path(tempfile.mkdtemp(prefix="coi_restore_", dir="/tmp"))
    restored_path = temp_dir / f"{source_path.stem}.fdb"
    result = subprocess.run(
        [
            "gbak",
            "-c",
            "-user",
            "SYSDBA",
            "-pas",
            "masterkey",
            str(source_path),
            str(restored_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Firebird restore failed for {source_path}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return restored_path, temp_dir


def _load_coi_company_identity(restored_path: Path, source_path: Path) -> tuple[str, str]:
    company_code, fallback_label = _infer_company_identity_from_path(str(source_path))
    rows = _run_firebird_query(
        restored_path,
        "SELECT FIRST 1 IDEMP, RFC_EMP FROM PARAMEMP",
        expected_fields=["IDEMP", "RFC_EMP"],
    )
    if not rows:
        return company_code, fallback_label
    row = rows[0]
    inferred_code = _normalize_company_code(row.get("IDEMP") or company_code)
    label = _safe_str(row.get("RFC_EMP")) or fallback_label
    return inferred_code, label


def _load_coi_accounting_source(
    *,
    fiscal_year: int,
    source_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    table_suffix = str(fiscal_year)[-2:]
    restored_path, temp_dir = _restore_coi_backup_to_fdb(source_path)
    try:
        company_code, company_label = _load_coi_company_identity(restored_path, source_path)
        account_rows = _run_firebird_query(
            restored_path,
            f"""
            SELECT
              NUM_CTA,
              NOMBRE,
              NIVEL
            FROM CUENTAS{table_suffix}
            ORDER BY NUM_CTA
            """,
            expected_fields=["NUM_CTA", "NOMBRE", "NIVEL"],
        )
        account_catalog = {
            _safe_str(row.get("NUM_CTA")): row
            for row in account_rows
            if _safe_str(row.get("NUM_CTA"))
        }
        balance_rows = _run_firebird_query(
            restored_path,
            f"""
            SELECT
              NUM_CTA,
              s.INICIAL,
              s.CARGO01, s.ABONO01, s.CARGO02, s.ABONO02, s.CARGO03, s.ABONO03,
              s.CARGO04, s.ABONO04, s.CARGO05, s.ABONO05, s.CARGO06, s.ABONO06,
              s.CARGO07, s.ABONO07, s.CARGO08, s.ABONO08, s.CARGO09, s.ABONO09,
              s.CARGO10, s.ABONO10, s.CARGO11, s.ABONO11, s.CARGO12, s.ABONO12
            FROM SALDOS{table_suffix} s
            ORDER BY NUM_CTA
            """,
            expected_fields=[
                "NUM_CTA",
                "INICIAL",
                "CARGO01",
                "ABONO01",
                "CARGO02",
                "ABONO02",
                "CARGO03",
                "ABONO03",
                "CARGO04",
                "ABONO04",
                "CARGO05",
                "ABONO05",
                "CARGO06",
                "ABONO06",
                "CARGO07",
                "ABONO07",
                "CARGO08",
                "ABONO08",
                "CARGO09",
                "ABONO09",
                "CARGO10",
                "ABONO10",
                "CARGO11",
                "ABONO11",
                "CARGO12",
                "ABONO12",
            ],
        )
        policy_lines = _run_firebird_query(
            restored_path,
            f"""
            SELECT
              TIPO_POLI,
              NUM_POLIZ,
              NUM_PART,
              PERIODO,
              EJERCICIO,
              NUM_CTA,
              FECHA_POL,
              DEBE_HABER,
              MONTOMOV,
              CONCEP_PO
            FROM AUXILIAR{table_suffix} a
            ORDER BY a.FECHA_POL, a.TIPO_POLI, a.NUM_POLIZ, a.NUM_PART
            """,
            expected_fields=[
                "TIPO_POLI",
                "NUM_POLIZ",
                "NUM_PART",
                "PERIODO",
                "EJERCICIO",
                "NUM_CTA",
                "FECHA_POL",
                "DEBE_HABER",
                "MONTOMOV",
                "CONCEP_PO",
            ],
        )
    finally:
        if temp_dir is not None and restored_path.exists():
            restored_path.unlink(missing_ok=True)
            temp_dir.rmdir()

    trial_balance_rows = _build_coi_trial_balance_rows(
        fiscal_year=fiscal_year,
        coi_balance_rows=balance_rows,
        account_catalog=account_catalog,
    )
    policy_header_rows, policy_line_rows = _build_coi_policy_rows(
        fiscal_year=fiscal_year,
        coi_policy_line_rows=policy_lines,
        account_catalog=account_catalog,
    )
    source_files = [
        {
            "source_family": "balanza",
            "source_format": source_path.suffix.lstrip(".").lower(),
            "source_filename": source_path.name,
            "source_path": str(source_path),
            "source_sha256": _sha256_path(source_path),
            "company_code": company_code,
            "company_label": company_label,
            "validation_status": "OK",
            "source_scope": "native_backup",
            "metadata": {
                "derived_artifact": False,
                "source_engine": "firebird",
                "source_table": f"SALDOS{table_suffix}+CUENTAS{table_suffix}",
                "company_rfc": company_label,
                "row_count": len(trial_balance_rows),
            },
        },
        {
            "source_family": "poliza_header",
            "source_format": source_path.suffix.lstrip(".").lower(),
            "source_filename": source_path.name,
            "source_path": str(source_path),
            "source_sha256": _sha256_path(source_path),
            "company_code": company_code,
            "company_label": company_label,
            "validation_status": "OK",
            "source_scope": "native_backup",
            "metadata": {
                "derived_artifact": False,
                "source_engine": "firebird",
                "source_table": f"POLIZAS{table_suffix}",
                "company_rfc": company_label,
                "row_count": len(policy_header_rows),
            },
        },
        {
            "source_family": "poliza_line",
            "source_format": source_path.suffix.lstrip(".").lower(),
            "source_filename": source_path.name,
            "source_path": str(source_path),
            "source_sha256": _sha256_path(source_path),
            "company_code": company_code,
            "company_label": company_label,
            "validation_status": "OK",
            "source_scope": "native_backup",
            "metadata": {
                "derived_artifact": False,
                "source_engine": "firebird",
                "source_table": f"AUXILIAR{table_suffix}+CUENTAS{table_suffix}",
                "company_rfc": company_label,
                "row_count": len(policy_line_rows),
            },
        },
    ]
    return trial_balance_rows, policy_header_rows, policy_line_rows, source_files


def _load_historical_source_dataset(
    *,
    fiscal_year: int,
    source_kind: str,
    source_path: Optional[str] = None,
    company_code: str = "01",
    company_label: Optional[str] = None,
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_company_code = _normalize_company_code(company_code)
    normalized_company_label = _safe_str(company_label) or _default_company_label(
        normalized_company_code
    )
    if source_kind == "csv":
        if fiscal_year not in SUPPORTED_HISTORICAL_MANIFESTS:
            raise ValueError(
                f"Historical pilot currently supports only fiscal_year in "
                f"{sorted(SUPPORTED_HISTORICAL_MANIFESTS.keys())}"
            )
        manifest = {
            key: Path(value) for key, value in SUPPORTED_HISTORICAL_MANIFESTS[fiscal_year].items()
        }
        for key, path in manifest.items():
            if not path.exists():
                raise ValueError(f"Missing pilot source for {key}: {path}")
        if "policy_headers" in manifest and "policy_lines" in manifest:
            trial_balance_rows = _load_csv_rows(manifest["trial_balance"])
            policy_header_rows = _load_csv_rows(manifest["policy_headers"])
            policy_line_rows = _load_csv_rows(manifest["policy_lines"])
        else:
            trial_balance_rows = _load_balance_only_trial_balance_rows(manifest["trial_balance"])
            policy_header_rows = []
            policy_line_rows = []
        source_files = [
            {
                "source_family": "balanza",
                "source_format": manifest["trial_balance"].suffix.lstrip("."),
                "source_filename": manifest["trial_balance"].name,
                "source_path": str(manifest["trial_balance"]),
                "source_sha256": _sha256_path(manifest["trial_balance"]),
                "company_code": normalized_company_code,
                "company_label": normalized_company_label,
                "validation_status": "OK",
                "source_scope": "canonical",
                "metadata": {
                    "derived_artifact": True,
                    "balance_only": "policy_headers" not in manifest,
                    "row_count": len(trial_balance_rows),
                },
            },
        ]
        if "policy_headers" in manifest and "policy_lines" in manifest:
            source_files.extend(
                [
                    {
                        "source_family": "poliza_header",
                        "source_format": manifest["policy_headers"].suffix.lstrip("."),
                        "source_filename": manifest["policy_headers"].name,
                        "source_path": str(manifest["policy_headers"]),
                        "source_sha256": _sha256_path(manifest["policy_headers"]),
                        "company_code": normalized_company_code,
                        "company_label": normalized_company_label,
                        "validation_status": "OK",
                        "source_scope": "canonical",
                        "metadata": {
                            "derived_artifact": True,
                            "row_count": len(policy_header_rows),
                        },
                    },
                    {
                        "source_family": "poliza_line",
                        "source_format": manifest["policy_lines"].suffix.lstrip("."),
                        "source_filename": manifest["policy_lines"].name,
                        "source_path": str(manifest["policy_lines"]),
                        "source_sha256": _sha256_path(manifest["policy_lines"]),
                        "company_code": normalized_company_code,
                        "company_label": normalized_company_label,
                        "validation_status": "OK",
                        "source_scope": "canonical",
                        "metadata": {
                            "derived_artifact": True,
                            "row_count": len(policy_line_rows),
                        },
                    },
                ]
            )
        return (
            {key: str(value) for key, value in manifest.items()},
            trial_balance_rows,
            policy_header_rows,
            policy_line_rows,
            source_files,
        )

    if source_kind != "coi_backup":
        raise ValueError(f"Unsupported source_kind: {source_kind}")
    if not source_path:
        raise ValueError("source_path is required for source_kind='coi_backup'")

    native_source_path = Path(source_path)
    if not native_source_path.exists():
        raise ValueError(f"Missing COI source backup: {native_source_path}")

    trial_balance_rows, policy_header_rows, policy_line_rows, source_files = (
        _load_coi_accounting_source(
            fiscal_year=fiscal_year,
            source_path=native_source_path,
        )
    )
    return (
        {"coi_backup": str(native_source_path)},
        trial_balance_rows,
        policy_header_rows,
        policy_line_rows,
        source_files,
    )


def _build_policy_quality_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    total_debe = _safe_decimal(row.get("total_debe"))
    total_haber = _safe_decimal(row.get("total_haber"))
    summary_debe = _safe_decimal(row.get("summary_debe"))
    summary_haber = _safe_decimal(row.get("summary_haber"))
    has_summary = _safe_bool(row.get("has_summary"))
    if abs(total_debe - total_haber) > 0.01:
        flags.append("unbalanced_policy")
    if not has_summary:
        flags.append("missing_summary_marker")
    if has_summary and (
        abs(total_debe - summary_debe) > 0.01 or abs(total_haber - summary_haber) > 0.01
    ):
        flags.append("summary_totals_mismatch")
    return flags


def _source_files_sha256(source_files: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "|".join(item["source_sha256"] for item in source_files).encode("utf-8")
    ).hexdigest()


def _build_reconciliation_summary(
    *,
    fiscal_year: int,
    trial_balance_rows: list[dict[str, Any]],
    policy_header_rows: list[dict[str, Any]],
    policy_line_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    monthly: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "trial_balance_rows": 0,
            "policy_headers": 0,
            "policy_lines": 0,
            "trial_balance_debits": 0.0,
            "trial_balance_credits": 0.0,
            "policy_header_debits": 0.0,
            "policy_header_credits": 0.0,
            "policy_line_debits": 0.0,
            "policy_line_credits": 0.0,
            "unbalanced_policy_headers": 0,
        }
    )

    for row in trial_balance_rows:
        month_num = _safe_int(row.get("month_num"))
        period_key = f"{fiscal_year}-{month_num:02d}"
        bucket = monthly[period_key]
        bucket["trial_balance_rows"] += 1
        bucket["trial_balance_debits"] += _safe_decimal(row.get("cargos"))
        bucket["trial_balance_credits"] += _safe_decimal(row.get("abonos"))

    for row in policy_header_rows:
        month_num = _safe_int(row.get("month_num"))
        period_key = f"{fiscal_year}-{month_num:02d}"
        bucket = monthly[period_key]
        total_debits = _safe_decimal(row.get("total_debe"))
        total_credits = _safe_decimal(row.get("total_haber"))
        bucket["policy_headers"] += 1
        bucket["policy_header_debits"] += total_debits
        bucket["policy_header_credits"] += total_credits
        if abs(total_debits - total_credits) > 0.01:
            bucket["unbalanced_policy_headers"] += 1

    for row in policy_line_rows:
        month_num = _safe_int(row.get("month_num"))
        period_key = f"{fiscal_year}-{month_num:02d}"
        bucket = monthly[period_key]
        bucket["policy_lines"] += 1
        bucket["policy_line_debits"] += _safe_decimal(row.get("debe"))
        bucket["policy_line_credits"] += _safe_decimal(row.get("haber"))

    normalized_months: dict[str, dict[str, Any]] = {}
    for period_key, bucket in sorted(monthly.items()):
        normalized_months[period_key] = {
            "trial_balance_rows": bucket["trial_balance_rows"],
            "policy_headers": bucket["policy_headers"],
            "policy_lines": bucket["policy_lines"],
            "trial_balance_debits": round(bucket["trial_balance_debits"], 2),
            "trial_balance_credits": round(bucket["trial_balance_credits"], 2),
            "policy_header_debits": round(bucket["policy_header_debits"], 2),
            "policy_header_credits": round(bucket["policy_header_credits"], 2),
            "policy_line_debits": round(bucket["policy_line_debits"], 2),
            "policy_line_credits": round(bucket["policy_line_credits"], 2),
            "unbalanced_policy_headers": bucket["unbalanced_policy_headers"],
        }

    return {
        "months_detected": len(normalized_months),
        "periods": normalized_months,
    }


async def _find_latest_applied_import_run(
    session: AsyncSession,
    *,
    fiscal_year: int,
    company_code: str = "01",
) -> Optional[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT air.id, air.source_sha256, MAX(hs.company_label) AS company_label
            FROM accounting_import_runs air
            JOIN historical_accounting_source_files hs ON hs.import_run_id = air.id
            WHERE air.source_type = 'historical_accounting'
              AND hs.fiscal_year = :fiscal_year
              AND hs.company_code = :company_code
              AND mode = 'apply'
              AND status = 'completed'
            GROUP BY air.id, air.source_sha256, air.finished_at, air.started_at
            ORDER BY air.finished_at DESC NULLS LAST, air.started_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {
            "fiscal_year": fiscal_year,
            "company_code": _normalize_company_code(company_code),
        },
    )
    row = result.first()
    if not row:
        return None
    return {
        "id": str(row[0]),
        "source_sha256": _safe_str(row[1]) or None,
        "company_label": _safe_str(row[2]) or _default_company_label(company_code),
    }


async def _load_historical_source_files_snapshot(
    session: AsyncSession,
    *,
    import_run_id: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
              source_family,
              source_format,
              source_filename,
              source_path,
              source_sha256,
              company_code,
              company_label,
              validation_status,
              source_scope,
              metadata
            FROM historical_accounting_source_files
            WHERE import_run_id = :import_run_id
            ORDER BY source_family, source_filename
            """
        ),
        {"import_run_id": import_run_id},
    )

    files: list[dict[str, Any]] = []
    for row in result:
        metadata = row.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {"raw": metadata}
        files.append(
            {
                "source_family": row.source_family,
                "source_format": row.source_format,
                "source_filename": row.source_filename,
                "source_path": row.source_path,
                "source_sha256": row.source_sha256,
                "company_code": row.company_code,
                "company_label": row.company_label,
                "validation_status": row.validation_status,
                "source_scope": row.source_scope,
                "metadata": metadata or {},
            }
        )
    return files


async def _load_historical_reconciliation_summary(
    session: AsyncSession,
    *,
    fiscal_year: int,
    import_run_id: str,
) -> dict[str, Any]:
    monthly: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "trial_balance_rows": 0,
            "policy_headers": 0,
            "policy_lines": 0,
            "trial_balance_debits": 0.0,
            "trial_balance_credits": 0.0,
            "policy_header_debits": 0.0,
            "policy_header_credits": 0.0,
            "policy_line_debits": 0.0,
            "policy_line_credits": 0.0,
            "unbalanced_policy_headers": 0,
        }
    )

    trial_balance_result = await session.execute(
        text(
            """
            SELECT
              period_key,
              COUNT(*) AS trial_balance_rows,
              COALESCE(SUM(debits), 0) AS trial_balance_debits,
              COALESCE(SUM(credits), 0) AS trial_balance_credits
            FROM historical_trial_balance_rows
            WHERE import_run_id = :import_run_id
              AND fiscal_year = :fiscal_year
            GROUP BY period_key
            ORDER BY period_key
            """
        ),
        {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
    )
    for row in trial_balance_result:
        bucket = monthly[row.period_key]
        bucket["trial_balance_rows"] = int(row.trial_balance_rows or 0)
        bucket["trial_balance_debits"] = float(row.trial_balance_debits or 0)
        bucket["trial_balance_credits"] = float(row.trial_balance_credits or 0)

    policy_header_result = await session.execute(
        text(
            """
            SELECT
              period_key,
              COUNT(*) AS policy_headers,
              COALESCE(SUM(total_debits), 0) AS policy_header_debits,
              COALESCE(SUM(total_credits), 0) AS policy_header_credits,
              COALESCE(SUM(CASE WHEN is_balanced THEN 0 ELSE 1 END), 0) AS unbalanced_policy_headers
            FROM historical_policy_headers
            WHERE import_run_id = :import_run_id
              AND fiscal_year = :fiscal_year
            GROUP BY period_key
            ORDER BY period_key
            """
        ),
        {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
    )
    for row in policy_header_result:
        bucket = monthly[row.period_key]
        bucket["policy_headers"] = int(row.policy_headers or 0)
        bucket["policy_header_debits"] = float(row.policy_header_debits or 0)
        bucket["policy_header_credits"] = float(row.policy_header_credits or 0)
        bucket["unbalanced_policy_headers"] = int(row.unbalanced_policy_headers or 0)

    policy_line_result = await session.execute(
        text(
            """
            SELECT
              period_key,
              COUNT(*) AS policy_lines,
              COALESCE(SUM(debit_amount), 0) AS policy_line_debits,
              COALESCE(SUM(credit_amount), 0) AS policy_line_credits
            FROM historical_policy_lines
            WHERE import_run_id = :import_run_id
              AND fiscal_year = :fiscal_year
            GROUP BY period_key
            ORDER BY period_key
            """
        ),
        {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
    )
    for row in policy_line_result:
        bucket = monthly[row.period_key]
        bucket["policy_lines"] = int(row.policy_lines or 0)
        bucket["policy_line_debits"] = float(row.policy_line_debits or 0)
        bucket["policy_line_credits"] = float(row.policy_line_credits or 0)

    normalized_months: dict[str, dict[str, Any]] = {}
    for period_key, bucket in sorted(monthly.items()):
        normalized_months[period_key] = {
            "trial_balance_rows": bucket["trial_balance_rows"],
            "policy_headers": bucket["policy_headers"],
            "policy_lines": bucket["policy_lines"],
            "trial_balance_debits": round(bucket["trial_balance_debits"], 2),
            "trial_balance_credits": round(bucket["trial_balance_credits"], 2),
            "policy_header_debits": round(bucket["policy_header_debits"], 2),
            "policy_header_credits": round(bucket["policy_header_credits"], 2),
            "policy_line_debits": round(bucket["policy_line_debits"], 2),
            "policy_line_credits": round(bucket["policy_line_credits"], 2),
            "unbalanced_policy_headers": bucket["unbalanced_policy_headers"],
        }

    return {
        "months_detected": len(normalized_months),
        "periods": normalized_months,
    }


async def load_historical_accounting_snapshot(
    session: AsyncSession,
    *,
    fiscal_year: int,
    import_run_id: Optional[str] = None,
    company_code: str = "01",
) -> Optional[dict[str, Any]]:
    normalized_company_code = _normalize_company_code(company_code)
    if import_run_id is None:
        latest_run = await _find_latest_applied_import_run(
            session,
            fiscal_year=fiscal_year,
            company_code=normalized_company_code,
        )
        if not latest_run:
            return None
        import_run_id = latest_run["id"]
        source_bundle_sha256 = latest_run["source_sha256"]
        company_label = latest_run["company_label"]
    else:
        run_result = await session.execute(
            text(
                """
                SELECT source_sha256
                FROM accounting_import_runs
                WHERE id = :import_run_id
                LIMIT 1
                """
            ),
            {"import_run_id": import_run_id},
        )
        run_row = run_result.first()
        source_bundle_sha256 = _safe_str(run_row[0]) if run_row else None
        company_label = None

    source_files = await _load_historical_source_files_snapshot(
        session,
        import_run_id=import_run_id,
    )
    if source_files:
        company_label = (
            _safe_str(source_files[0].get("company_label"))
            or company_label
            or _default_company_label(normalized_company_code)
        )
    reconciliation = await _load_historical_reconciliation_summary(
        session,
        fiscal_year=fiscal_year,
        import_run_id=import_run_id,
    )

    trial_balance_rows = int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM historical_trial_balance_rows
                    WHERE import_run_id = :import_run_id
                      AND fiscal_year = :fiscal_year
                    """
                ),
                {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
            )
        ).scalar_one()
        or 0
    )
    policy_headers = int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM historical_policy_headers
                    WHERE import_run_id = :import_run_id
                      AND fiscal_year = :fiscal_year
                    """
                ),
                {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
            )
        ).scalar_one()
        or 0
    )
    policy_lines = int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM historical_policy_lines
                    WHERE import_run_id = :import_run_id
                      AND fiscal_year = :fiscal_year
                    """
                ),
                {"import_run_id": import_run_id, "fiscal_year": fiscal_year},
            )
        ).scalar_one()
        or 0
    )

    return {
        "ok": True,
        "created": False,
        "import_run_id": import_run_id,
        "summary": {
            "fiscal_year": fiscal_year,
            "company_code": normalized_company_code,
            "company_label": company_label or _default_company_label(normalized_company_code),
            "mode": "applied_snapshot",
            "source": "historical_tables",
            "source_files": len(source_files),
            "trial_balance_rows": trial_balance_rows,
            "policy_headers": policy_headers,
            "policy_lines": policy_lines,
            "source_bundle_sha256": source_bundle_sha256,
            "persisted_source_files": source_files,
            "reconciliation": reconciliation,
        },
    }


async def _find_existing_import_run_id(
    session: AsyncSession,
    *,
    fiscal_year: int,
    source_sha256: str,
    company_code: str = "01",
) -> Optional[str]:
    result = await session.execute(
        text(
            """
            SELECT air.id
            FROM accounting_import_runs air
            JOIN historical_accounting_source_files hs ON hs.import_run_id = air.id
            WHERE air.source_type = 'historical_accounting'
              AND hs.fiscal_year = :fiscal_year
              AND hs.company_code = :company_code
              AND air.source_sha256 = :source_sha256
            GROUP BY air.id, air.started_at
            ORDER BY air.started_at DESC
            LIMIT 1
            """
        ),
        {
            "fiscal_year": fiscal_year,
            "company_code": _normalize_company_code(company_code),
            "source_sha256": source_sha256,
        },
    )
    row = result.first()
    return str(row[0]) if row else None


async def ensure_historical_accounting_schema(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS historical_accounting_source_files (
                id UUID PRIMARY KEY,
                import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_month INTEGER NULL,
                source_family VARCHAR(50) NOT NULL,
                source_format VARCHAR(20) NOT NULL,
                source_filename VARCHAR(255) NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 VARCHAR(64) NULL,
                company_code VARCHAR(10) NULL,
                company_label TEXT NULL,
                validation_status VARCHAR(30) NULL,
                source_scope VARCHAR(30) NOT NULL DEFAULT 'canonical',
                metadata JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS historical_trial_balance_rows (
                id UUID PRIMARY KEY,
                import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
                source_file_id UUID NULL REFERENCES historical_accounting_source_files(id) ON DELETE SET NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_month INTEGER NOT NULL,
                period_key VARCHAR(7) NOT NULL,
                company_code VARCHAR(10) NULL,
                company_label TEXT NULL,
                source_row_number INTEGER NULL,
                account_code_raw VARCHAR(100) NOT NULL,
                account_code_canonical VARCHAR(100) NULL,
                account_name_raw TEXT NOT NULL,
                account_name_canonical TEXT NULL,
                account_level INTEGER NULL,
                is_detail BOOLEAN NOT NULL DEFAULT FALSE,
                opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
                debits NUMERIC(18,2) NOT NULL DEFAULT 0,
                credits NUMERIC(18,2) NOT NULL DEFAULT 0,
                closing_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS historical_policy_headers (
                id UUID PRIMARY KEY,
                import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
                source_file_id UUID NULL REFERENCES historical_accounting_source_files(id) ON DELETE SET NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_month INTEGER NOT NULL,
                period_key VARCHAR(7) NOT NULL,
                company_code VARCHAR(10) NULL,
                company_label TEXT NULL,
                policy_id_natural VARCHAR(120) NOT NULL,
                policy_type VARCHAR(20) NOT NULL,
                policy_number VARCHAR(50) NOT NULL,
                policy_date DATE NULL,
                concept_raw TEXT NULL,
                concept_normalized TEXT NULL,
                line_count INTEGER NULL,
                total_debits NUMERIC(18,2) NOT NULL DEFAULT 0,
                total_credits NUMERIC(18,2) NOT NULL DEFAULT 0,
                summary_debits NUMERIC(18,2) NOT NULL DEFAULT 0,
                summary_credits NUMERIC(18,2) NOT NULL DEFAULT 0,
                is_balanced BOOLEAN NOT NULL DEFAULT FALSE,
                has_summary BOOLEAN NOT NULL DEFAULT FALSE,
                quality_flags JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS historical_policy_lines (
                id UUID PRIMARY KEY,
                import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
                source_file_id UUID NULL REFERENCES historical_accounting_source_files(id) ON DELETE SET NULL,
                policy_header_id UUID NULL REFERENCES historical_policy_headers(id) ON DELETE CASCADE,
                fiscal_year INTEGER NOT NULL,
                fiscal_month INTEGER NOT NULL,
                period_key VARCHAR(7) NOT NULL,
                company_code VARCHAR(10) NULL,
                company_label TEXT NULL,
                policy_id_natural VARCHAR(120) NOT NULL,
                line_number INTEGER NOT NULL,
                account_code_raw VARCHAR(100) NOT NULL,
                account_code_canonical VARCHAR(100) NULL,
                account_name_raw TEXT NOT NULL,
                account_name_canonical TEXT NULL,
                debit_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                credit_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                line_concept TEXT NULL,
                counterparty_raw TEXT NULL,
                cost_center_raw TEXT NULL,
                project_raw TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_accounting_source_files "
            "ADD COLUMN IF NOT EXISTS company_code VARCHAR(10)"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_accounting_source_files "
            "ADD COLUMN IF NOT EXISTS company_label TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_trial_balance_rows "
            "ADD COLUMN IF NOT EXISTS company_code VARCHAR(10)"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_trial_balance_rows "
            "ADD COLUMN IF NOT EXISTS company_label TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_policy_headers "
            "ADD COLUMN IF NOT EXISTS company_code VARCHAR(10)"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_policy_headers "
            "ADD COLUMN IF NOT EXISTS company_label TEXT"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_policy_lines "
            "ADD COLUMN IF NOT EXISTS company_code VARCHAR(10)"
        )
    )
    await session.execute(
        text(
            "ALTER TABLE historical_policy_lines "
            "ADD COLUMN IF NOT EXISTS company_label TEXT"
        )
    )
    await session.execute(
        text(
            """
            UPDATE historical_accounting_source_files
            SET company_code = COALESCE(
                    company_code,
                    CASE
                      WHEN lower(source_path) LIKE '%empre2%' OR lower(source_filename) LIKE '%empre2%' THEN '02'
                      WHEN lower(source_path) LIKE '%empre4%' OR lower(source_filename) LIKE '%empre4%' THEN '04'
                      ELSE '01'
                    END
                ),
                company_label = COALESCE(
                    company_label,
                    CASE
                      WHEN lower(source_path) LIKE '%empre2%' OR lower(source_filename) LIKE '%empre2%' THEN 'PMD0608162M2'
                      ELSE 'PSP1705058S4'
                    END
                )
            WHERE company_code IS NULL OR company_label IS NULL
            """
        )
    )
    await session.execute(
        text(
            """
            UPDATE historical_trial_balance_rows t
            SET company_code = COALESCE(t.company_code, s.company_code),
                company_label = COALESCE(t.company_label, s.company_label)
            FROM historical_accounting_source_files s
            WHERE s.id = t.source_file_id
              AND (t.company_code IS NULL OR t.company_label IS NULL)
            """
        )
    )
    await session.execute(
        text(
            """
            UPDATE historical_policy_headers h
            SET company_code = COALESCE(h.company_code, s.company_code),
                company_label = COALESCE(h.company_label, s.company_label)
            FROM historical_accounting_source_files s
            WHERE s.id = h.source_file_id
              AND (h.company_code IS NULL OR h.company_label IS NULL)
            """
        )
    )
    await session.execute(
        text(
            """
            UPDATE historical_policy_lines l
            SET company_code = COALESCE(l.company_code, s.company_code),
                company_label = COALESCE(l.company_label, s.company_label)
            FROM historical_accounting_source_files s
            WHERE s.id = l.source_file_id
              AND (l.company_code IS NULL OR l.company_label IS NULL)
            """
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_historical_source_files_import_run "
            "ON historical_accounting_source_files(import_run_id)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_historical_source_files_year_company "
            "ON historical_accounting_source_files(fiscal_year, company_code)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_historical_trial_balance_period_account "
            "ON historical_trial_balance_rows(fiscal_year, company_code, fiscal_month, account_code_raw)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_historical_policy_headers_period "
            "ON historical_policy_headers(fiscal_year, company_code, fiscal_month, policy_type, policy_number)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_historical_policy_lines_policy "
            "ON historical_policy_lines(company_code, policy_id_natural, line_number)"
        )
    )
    await session.commit()


async def list_historical_accounting_companies(
    session: AsyncSession,
    *,
    fiscal_year: int,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
              hs.company_code,
              MAX(hs.company_label) AS company_label,
              COUNT(*) AS source_files,
              (
                SELECT COUNT(DISTINCT tb.period_key)
                FROM historical_trial_balance_rows tb
                WHERE tb.fiscal_year = :fiscal_year
                  AND tb.company_code = hs.company_code
              ) AS trial_balance_periods,
              (
                SELECT COUNT(DISTINCT ph.period_key)
                FROM historical_policy_headers ph
                WHERE ph.fiscal_year = :fiscal_year
                  AND ph.company_code = hs.company_code
              ) AS policy_periods,
              (
                SELECT COUNT(*)
                FROM historical_policy_headers ph
                WHERE ph.fiscal_year = :fiscal_year
                  AND ph.company_code = hs.company_code
              ) AS policy_headers,
              (
                SELECT COUNT(*)
                FROM historical_policy_lines pl
                WHERE pl.fiscal_year = :fiscal_year
                  AND pl.company_code = hs.company_code
              ) AS policy_lines
            FROM historical_accounting_source_files hs
            WHERE hs.fiscal_year = :fiscal_year
              AND hs.company_code IS NOT NULL
            GROUP BY hs.company_code
            ORDER BY hs.company_code
            """
        ),
        {"fiscal_year": fiscal_year},
    )
    companies: list[dict[str, Any]] = []
    for row in result:
        trial_balance_periods = int(row.trial_balance_periods or 0)
        policy_periods = int(row.policy_periods or 0)
        companies.append(
            {
                "company_code": _normalize_company_code(row.company_code),
                "company_label": _safe_str(row.company_label)
                or _default_company_label(row.company_code),
                "source_files": int(row.source_files or 0),
                "trial_balance_periods": trial_balance_periods,
                "policy_periods": policy_periods,
                "policy_headers": int(row.policy_headers or 0),
                "policy_lines": int(row.policy_lines or 0),
                "coverage_status": (
                    "full_year"
                    if policy_periods >= 12
                    else ("partial_year" if policy_periods > 0 else "balance_only")
                ),
            }
        )
    return companies


async def list_historical_accounting_coverage(
    session: AsyncSession,
    *,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
) -> list[dict[str, Any]]:
    filters = ["hs.company_code IS NOT NULL"]
    params: dict[str, Any] = {}
    if year_from is not None:
        filters.append("hs.fiscal_year >= :year_from")
        params["year_from"] = int(year_from)
    if year_to is not None:
        filters.append("hs.fiscal_year <= :year_to")
        params["year_to"] = int(year_to)

    result = await session.execute(
        text(
            f"""
            SELECT
              hs.fiscal_year,
              hs.company_code,
              MAX(hs.company_label) AS company_label,
              COUNT(*) AS source_files,
              (
                SELECT COUNT(DISTINCT tb.period_key)
                FROM historical_trial_balance_rows tb
                WHERE tb.fiscal_year = hs.fiscal_year
                  AND tb.company_code = hs.company_code
              ) AS trial_balance_periods,
              (
                SELECT COUNT(DISTINCT ph.period_key)
                FROM historical_policy_headers ph
                WHERE ph.fiscal_year = hs.fiscal_year
                  AND ph.company_code = hs.company_code
              ) AS policy_periods,
              (
                SELECT COUNT(*)
                FROM historical_policy_headers ph
                WHERE ph.fiscal_year = hs.fiscal_year
                  AND ph.company_code = hs.company_code
              ) AS policy_headers,
              (
                SELECT COUNT(*)
                FROM historical_policy_lines pl
                WHERE pl.fiscal_year = hs.fiscal_year
                  AND pl.company_code = hs.company_code
              ) AS policy_lines
            FROM historical_accounting_source_files hs
            WHERE {' AND '.join(filters)}
            GROUP BY hs.fiscal_year, hs.company_code
            ORDER BY hs.fiscal_year DESC, hs.company_code
            """
        ),
        params,
    )
    rows: list[dict[str, Any]] = []
    for row in result:
        trial_balance_periods = int(row.trial_balance_periods or 0)
        policy_periods = int(row.policy_periods or 0)
        rows.append(
            {
                "fiscal_year": int(row.fiscal_year or 0),
                "company_code": _normalize_company_code(row.company_code),
                "company_label": _safe_str(row.company_label)
                or _default_company_label(row.company_code),
                "source_files": int(row.source_files or 0),
                "trial_balance_periods": trial_balance_periods,
                "policy_periods": policy_periods,
                "policy_headers": int(row.policy_headers or 0),
                "policy_lines": int(row.policy_lines or 0),
                "coverage_status": (
                    "full_year"
                    if policy_periods >= 12
                    else ("partial_year" if policy_periods > 0 else "balance_only")
                ),
            }
        )
    return rows


async def build_historical_accounting_comparison(
    session: AsyncSession,
    *,
    company_code: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
) -> dict[str, Any]:
    normalized_company_code = _normalize_company_code(company_code)
    coverage_rows = await list_historical_accounting_coverage(
        session,
        year_from=year_from,
        year_to=year_to,
    )
    rows = [
        item
        for item in coverage_rows
        if _normalize_company_code(item.get("company_code")) == normalized_company_code
    ]
    rows = sorted(rows, key=lambda item: int(item.get("fiscal_year") or 0))
    previous_policy_headers = None
    previous_policy_lines = None
    comparison_rows: list[dict[str, Any]] = []
    for item in rows:
        policy_headers = int(item.get("policy_headers") or 0)
        policy_lines = int(item.get("policy_lines") or 0)
        comparison_rows.append(
            {
                **item,
                "policy_headers_delta": (
                    policy_headers - previous_policy_headers
                    if previous_policy_headers is not None
                    else None
                ),
                "policy_lines_delta": (
                    policy_lines - previous_policy_lines
                    if previous_policy_lines is not None
                    else None
                ),
            }
        )
        previous_policy_headers = policy_headers
        previous_policy_lines = policy_lines

    return {
        "company_code": normalized_company_code,
        "company_label": rows[-1]["company_label"] if rows else _default_company_label(normalized_company_code),
        "rows": comparison_rows,
    }


async def import_historical_accounting_pilot(
    session: AsyncSession,
    *,
    fiscal_year: int = 2024,
    started_by_empleado_id: Optional[str] = None,
    apply_changes: bool = False,
    source_kind: str = "csv",
    source_path: Optional[str] = None,
    company_code: str = "01",
    company_label: Optional[str] = None,
) -> dict[str, Any]:
    normalized_company_code = _normalize_company_code(company_code)
    normalized_company_label = _safe_str(company_label) or _default_company_label(
        normalized_company_code
    )
    await ensure_historical_accounting_schema(session)
    if not apply_changes and hasattr(session, "execute"):
        persisted_snapshot = await load_historical_accounting_snapshot(
            session,
            fiscal_year=fiscal_year,
            company_code=normalized_company_code,
        )
        if persisted_snapshot:
            return persisted_snapshot

    manifest, trial_balance_rows, policy_header_rows, policy_line_rows, source_files = (
        _load_historical_source_dataset(
            fiscal_year=fiscal_year,
            source_kind=source_kind,
            source_path=source_path,
            company_code=normalized_company_code,
            company_label=normalized_company_label,
        )
    )
    if source_files:
        normalized_company_code = _normalize_company_code(source_files[0].get("company_code"))
        normalized_company_label = _safe_str(source_files[0].get("company_label")) or normalized_company_label
    source_bundle_sha256 = _source_files_sha256(source_files)
    reconciliation = _build_reconciliation_summary(
        fiscal_year=fiscal_year,
        trial_balance_rows=trial_balance_rows,
        policy_header_rows=policy_header_rows,
        policy_line_rows=policy_line_rows,
    )

    summary = {
        "fiscal_year": fiscal_year,
        "company_code": normalized_company_code,
        "company_label": normalized_company_label,
        "mode": "apply" if apply_changes else "dry_run",
        "source_files": len(source_files),
        "trial_balance_rows": len(trial_balance_rows),
        "policy_headers": len(policy_header_rows),
        "policy_lines": len(policy_line_rows),
        "source_kind": source_kind,
        "manifest": manifest,
        "source_bundle_sha256": source_bundle_sha256,
        "reconciliation": reconciliation,
    }

    if not apply_changes:
        return {"ok": True, "created": False, "summary": summary}

    existing_import_run_id = await _find_existing_import_run_id(
        session,
        fiscal_year=fiscal_year,
        source_sha256=source_bundle_sha256,
        company_code=normalized_company_code,
    )
    if existing_import_run_id:
        persisted_snapshot = await load_historical_accounting_snapshot(
            session,
            fiscal_year=fiscal_year,
            import_run_id=existing_import_run_id,
            company_code=normalized_company_code,
        )
        return {
            **(persisted_snapshot or {"ok": True, "created": False, "import_run_id": existing_import_run_id, "summary": summary}),
            "skipped": True,
            "reason": "already_imported",
        }

    import_run_id = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO accounting_import_runs (
                id, source_type, filename, source_sha256, mode, status,
                started_by_empleado_id, started_at, finished_at, summary_json, error_text
            ) VALUES (
                :id, :source_type, :filename, :source_sha256, :mode, :status,
                :started_by_empleado_id, NOW(), NOW(), CAST(:summary_json AS jsonb), NULL
            )
            """
        ),
        {
            "id": import_run_id,
            "source_type": "historical_accounting",
            "filename": f"historical_accounting_{fiscal_year}_{normalized_company_code}_pilot",
            "source_sha256": source_bundle_sha256,
            "mode": "apply",
            "status": "completed",
            "started_by_empleado_id": started_by_empleado_id,
            "summary_json": json.dumps(summary, ensure_ascii=False),
        },
    )

    source_file_ids: dict[str, str] = {}
    for item in source_files:
        source_file_id = str(uuid.uuid4())
        source_file_ids[item["source_family"]] = source_file_id
        await session.execute(
            text(
                """
                INSERT INTO historical_accounting_source_files (
                    id, import_run_id, fiscal_year, fiscal_month, source_family, source_format,
                    source_filename, source_path, source_sha256, company_code, company_label, validation_status,
                    source_scope, metadata, created_at
                ) VALUES (
                    :id, :import_run_id, :fiscal_year, NULL, :source_family, :source_format,
                    :source_filename, :source_path, :source_sha256, :company_code, :company_label, :validation_status,
                    :source_scope, CAST(:metadata AS jsonb), NOW()
                )
                """
            ),
            {
                "id": source_file_id,
                "import_run_id": import_run_id,
                "fiscal_year": fiscal_year,
                "source_family": item["source_family"],
                "source_format": item["source_format"],
                "source_filename": item["source_filename"],
                "source_path": item["source_path"],
                "source_sha256": item["source_sha256"],
                "company_code": _normalize_company_code(item.get("company_code")),
                "company_label": _safe_str(item.get("company_label")) or normalized_company_label,
                "validation_status": item["validation_status"],
                "source_scope": item["source_scope"],
                "metadata": json.dumps(item["metadata"], ensure_ascii=False),
            },
        )

    header_id_by_policy: dict[str, str] = {}
    for row in trial_balance_rows:
        month_num = _safe_int(row.get("month_num"))
        await session.execute(
            text(
                """
                INSERT INTO historical_trial_balance_rows (
                    id, import_run_id, source_file_id, fiscal_year, fiscal_month, period_key,
                    company_code, company_label, source_row_number, account_code_raw, account_code_canonical,
                    account_name_raw, account_name_canonical, account_level, is_detail,
                    opening_balance, debits, credits, closing_balance, created_at
                ) VALUES (
                    :id, :import_run_id, :source_file_id, :fiscal_year, :fiscal_month, :period_key,
                    :company_code, :company_label, :source_row_number, :account_code_raw, :account_code_canonical,
                    :account_name_raw, :account_name_canonical, :account_level, :is_detail,
                    :opening_balance, :debits, :credits, :closing_balance, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "import_run_id": import_run_id,
                "source_file_id": source_file_ids["balanza"],
                "fiscal_year": fiscal_year,
                "fiscal_month": month_num,
                "period_key": f"{fiscal_year}-{month_num:02d}",
                "company_code": normalized_company_code,
                "company_label": normalized_company_label,
                "source_row_number": _safe_int(row.get("row_number")),
                "account_code_raw": _safe_str(row.get("account_code")),
                "account_code_canonical": _safe_str(row.get("account_code")),
                "account_name_raw": _safe_str(row.get("description")),
                "account_name_canonical": _safe_str(row.get("description")),
                "account_level": _safe_int(row.get("level")),
                "is_detail": _safe_bool(row.get("is_detail")),
                "opening_balance": _safe_decimal(row.get("saldo_inicial")),
                "debits": _safe_decimal(row.get("cargos")),
                "credits": _safe_decimal(row.get("abonos")),
                "closing_balance": _safe_decimal(row.get("saldo_final")),
            },
        )

    for row in policy_header_rows:
        month_num = _safe_int(row.get("month_num"))
        policy_id = _safe_str(row.get("policy_id"))
        quality_flags = _build_policy_quality_flags(row)
        header_id = str(uuid.uuid4())
        header_id_by_policy[policy_id] = header_id
        total_debits = _safe_decimal(row.get("total_debe"))
        total_credits = _safe_decimal(row.get("total_haber"))
        await session.execute(
            text(
                """
                INSERT INTO historical_policy_headers (
                    id, import_run_id, source_file_id, fiscal_year, fiscal_month, period_key,
                    company_code, company_label, policy_id_natural, policy_type, policy_number, policy_date, concept_raw,
                    concept_normalized, line_count, total_debits, total_credits,
                    summary_debits, summary_credits, is_balanced, has_summary, quality_flags,
                    created_at
                ) VALUES (
                    :id, :import_run_id, :source_file_id, :fiscal_year, :fiscal_month, :period_key,
                    :company_code, :company_label, :policy_id_natural, :policy_type, :policy_number, :policy_date, :concept_raw,
                    :concept_normalized, :line_count, :total_debits, :total_credits,
                    :summary_debits, :summary_credits, :is_balanced, :has_summary, CAST(:quality_flags AS jsonb),
                    NOW()
                )
                """
            ),
            {
                "id": header_id,
                "import_run_id": import_run_id,
                "source_file_id": source_file_ids["poliza_header"],
                "fiscal_year": fiscal_year,
                "fiscal_month": month_num,
                "period_key": f"{fiscal_year}-{month_num:02d}",
                "company_code": normalized_company_code,
                "company_label": normalized_company_label,
                "policy_id_natural": policy_id,
                "policy_type": _safe_str(row.get("policy_type")),
                "policy_number": _safe_str(row.get("policy_number")),
                "policy_date": _parse_date(row.get("policy_date")),
                "concept_raw": _safe_str(row.get("description")),
                "concept_normalized": _safe_str(row.get("description")),
                "line_count": _safe_int(row.get("line_count")),
                "total_debits": total_debits,
                "total_credits": total_credits,
                "summary_debits": _safe_decimal(row.get("summary_debe")),
                "summary_credits": _safe_decimal(row.get("summary_haber")),
                "is_balanced": abs(total_debits - total_credits) <= 0.01,
                "has_summary": _safe_bool(row.get("has_summary")),
                "quality_flags": json.dumps(quality_flags, ensure_ascii=False),
            },
        )

    for row in policy_line_rows:
        month_num = _safe_int(row.get("month_num"))
        policy_id = _safe_str(row.get("policy_id"))
        await session.execute(
            text(
                """
                INSERT INTO historical_policy_lines (
                    id, import_run_id, source_file_id, policy_header_id, fiscal_year,
                    fiscal_month, period_key, company_code, company_label, policy_id_natural, line_number,
                    account_code_raw, account_code_canonical, account_name_raw,
                    account_name_canonical, debit_amount, credit_amount, line_concept,
                    counterparty_raw, cost_center_raw, project_raw, created_at
                ) VALUES (
                    :id, :import_run_id, :source_file_id, :policy_header_id, :fiscal_year,
                    :fiscal_month, :period_key, :company_code, :company_label, :policy_id_natural, :line_number,
                    :account_code_raw, :account_code_canonical, :account_name_raw,
                    :account_name_canonical, :debit_amount, :credit_amount, :line_concept,
                    NULL, NULL, NULL, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "import_run_id": import_run_id,
                "source_file_id": source_file_ids["poliza_line"],
                "policy_header_id": header_id_by_policy.get(policy_id),
                "fiscal_year": fiscal_year,
                "fiscal_month": month_num,
                "period_key": f"{fiscal_year}-{month_num:02d}",
                "company_code": normalized_company_code,
                "company_label": normalized_company_label,
                "policy_id_natural": policy_id,
                "line_number": _safe_int(row.get("row_number")),
                "account_code_raw": _safe_str(row.get("account_code")),
                "account_code_canonical": _safe_str(row.get("account_code")),
                "account_name_raw": _safe_str(row.get("account_name")),
                "account_name_canonical": _safe_str(row.get("account_name")),
                "debit_amount": _safe_decimal(row.get("debe")),
                "credit_amount": _safe_decimal(row.get("haber")),
                "line_concept": _safe_str(row.get("concept")),
            },
        )

    await session.commit()
    persisted_snapshot = await load_historical_accounting_snapshot(
        session,
        fiscal_year=fiscal_year,
        import_run_id=import_run_id,
        company_code=normalized_company_code,
    )
    if persisted_snapshot:
        return {
            **persisted_snapshot,
            "created": True,
        }
    return {
        "ok": True,
        "created": True,
        "import_run_id": import_run_id,
        "summary": summary,
    }
