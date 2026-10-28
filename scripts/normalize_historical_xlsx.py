#!/usr/bin/env python3
"""Normalize explicitly identified XLSX accounting sources without database writes."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from openpyxl import load_workbook

CENT = Decimal("0.01")
ACCOUNT = re.compile(r"^(\d{4}-\d{3}-\d{3})(?:\s+(.*))?$")
MONTHS = {
    name: n
    for n, name in enumerate(
        (
            "ENERO",
            "FEBRERO",
            "MARZO",
            "ABRIL",
            "MAYO",
            "JUNIO",
            "JULIO",
            "AGOSTO",
            "SEPTIEMBRE",
            "OCTUBRE",
            "NOVIEMBRE",
            "DICIEMBRE",
        ),
        1,
    )
}
POLICY_HEADERS = (
    "Cuenta",
    "Tipo Póliza",
    "Numero",
    "Fecha",
    "Concepto",
    "Debe",
    "Haber",
)
BALANCE_HEADERS = (
    "Cuenta",
    "Descripción de la cuenta",
    "Saldo inicial",
    "Total de cargos",
    "Total de abonos",
    "Saldo final",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_source(source: dict) -> Path:
    path = Path(source["path"]).resolve()
    if not path.is_file() or sha(path) != source["sha256"]:
        raise ValueError(f"Source hash differs: {path.name}")
    return path


def amount(value: Any) -> tuple[str, str]:
    if value is None or isinstance(value, bool):
        raise ValueError("Missing or boolean monetary amount")
    try:
        raw = Decimal(str(value if value is not None else 0))
    except InvalidOperation as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not raw.is_finite():
        raise ValueError("Nonfinite monetary amount")
    return str(raw.quantize(CENT, rounding=ROUND_HALF_UP)), str(raw)


def account(value: Any) -> tuple[str, str]:
    found = ACCOUNT.fullmatch(str(value or "").strip())
    if not found:
        raise ValueError(f"Invalid source account: {value!r}")
    return found.group(1), found.group(2) or ""


def source_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%d/%m/%Y").date()
        except ValueError:
            pass
    raise ValueError("Invalid source date")


def identity(config: dict) -> dict:
    year = config["fiscal_year"]
    code, label = str(config["company_code"]), str(config["company_label"])
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError("Invalid fiscal year")
    if not re.fullmatch(r"(?:\d{2}|[A-Z]{2,10})", code):
        raise ValueError("Explicit company identifier required")
    if not re.fullmatch(r"[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}", label):
        raise ValueError("Explicit company RFC required")
    return {"fiscal_year": year, "company_code": code, "company_label": label}


def policies(config: dict, path: Path) -> tuple[list[dict], list[dict], dict]:
    ident = identity(config)
    book = load_workbook(path, read_only=True, data_only=True)
    lines: list[dict] = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    precision: list[dict] = []
    skipped_years: dict[int, int] = defaultdict(int)
    try:
        if len(book.sheetnames) != 1:
            raise ValueError("Policy workbook must have exactly one sheet")
        sheet = book.active
        if tuple(next(sheet.values))[:7] != POLICY_HEADERS:
            raise ValueError("Policy headers differ from approved layout")
        for number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            if not any(v is not None for v in row):
                continue
            day = source_date(row[3])
            if day.year != ident["fiscal_year"]:
                skipped_years[day.year] += 1
                continue
            code, name = account(row[0])
            kind = str(row[1] or "").strip()
            number_value = row[2]
            if isinstance(number_value, bool) or number_value is None:
                raise ValueError("Missing policy number")
            if isinstance(number_value, float) and number_value.is_integer():
                number_value = int(number_value)
            policy_number = str(number_value).strip()
            if not kind or not policy_number:
                raise ValueError("Missing policy identity")
            debit, raw_debit = amount(row[5])
            credit, raw_credit = amount(row[6])
            if Decimal(raw_debit) < 0 or Decimal(raw_credit) < 0:
                raise ValueError("Negative debit/credit")
            pid = f"{day:%Y-%m}:{kind}:{policy_number}"
            line = {
                **ident,
                "month_num": day.month,
                "policy_id": pid,
                "row_number": number,
                "account_code": code,
                "account_name": name,
                "debe": debit,
                "haber": credit,
                "concept": str(row[4] or ""),
                "policy_date": day.isoformat(),
                "policy_type": kind,
                "policy_number": policy_number,
                "raw_debe": raw_debit,
                "raw_haber": raw_credit,
                "source_path": str(path),
                "source_sha256": config["files"]["policy"]["sha256"],
                "source_sheet": sheet.title,
                "source_row_number": number,
                "project_raw": "",
                "cost_center_raw": "",
            }
            lines.append(line)
            grouped[pid].append(line)
            if Decimal(raw_debit) != Decimal(debit) or Decimal(raw_credit) != Decimal(
                credit
            ):
                precision.append(
                    {
                        "row": number,
                        "raw_debe": raw_debit,
                        "raw_haber": raw_credit,
                        "debe": debit,
                        "haber": credit,
                    }
                )
        headers: list[dict] = []
        warnings: list[dict] = []
        for pid, members in grouped.items():
            if len({r["policy_date"] for r in members}) != 1:
                raise ValueError(f"Policy spans multiple dates: {pid}")
            first = members[0]
            debit = sum((Decimal(r["debe"]) for r in members), Decimal(0))
            credit = sum((Decimal(r["haber"]) for r in members), Decimal(0))
            delta = debit - credit
            if abs(delta) > CENT:
                raise ValueError(
                    f"Policy does not reconcile: {pid}, difference {delta}"
                )
            if delta:
                warnings.append(
                    {
                        "policy_id": pid,
                        "difference": str(delta),
                        "kind": "source_rounding_difference_no_adjustment",
                    }
                )
            headers.append(
                {
                    **ident,
                    "month_num": first["month_num"],
                    "policy_id": pid,
                    "policy_type": first["policy_type"],
                    "policy_number": first["policy_number"],
                    "policy_date": first["policy_date"],
                    "description": "",
                    "line_count": len(members),
                    "total_debe": str(debit),
                    "total_haber": str(credit),
                    "summary_debe": "",
                    "summary_haber": "",
                    "has_summary": "false",
                    "source_row_number": first["source_row_number"],
                }
            )
        if not lines:
            raise ValueError("No policy rows in selected fiscal year")
        return (
            headers,
            lines,
            {
                "precision_rows": precision,
                "warnings": warnings,
                "skipped_year_rows": dict(skipped_years),
                "months": sorted({r["month_num"] for r in lines}),
            },
        )
    finally:
        book.close()


def is_leaf(code: str, codes: set[str]) -> bool:
    root, branch, leaf = code.split("-")
    if leaf != "000":
        return True
    if branch == "000":
        return not any(c.startswith(root + "-") and c != code for c in codes)
    return not any(c.startswith(root + "-" + branch + "-") and c != code for c in codes)


def balances(config: dict, path: Path) -> list[dict]:
    ident = identity(config)
    book = load_workbook(path, read_only=True, data_only=True)
    result: list[dict] = []
    seen_months: set[int] = set()
    try:
        for sheet in book:
            title = sheet.title.strip().upper()
            if title not in MONTHS or MONTHS[title] in seen_months:
                raise ValueError("Unknown or duplicate balance month")
            month = MONTHS[title]
            seen_months.add(month)
            if tuple(next(sheet.values))[:6] != BALANCE_HEADERS:
                raise ValueError("Balance headers differ from approved layout")
            staged: list[dict] = []
            codes: set[str] = set()
            for number, row in enumerate(
                sheet.iter_rows(min_row=2, values_only=True), 2
            ):
                if not any(v is not None for v in row):
                    continue
                code, _ = account(row[0])
                if code in codes:
                    raise ValueError(f"Duplicate balance account: {title}, {code}")
                codes.add(code)
                normalized = {}
                raw = {}
                for field, value in zip(
                    ("saldo_inicial", "cargos", "abonos", "saldo_final"), row[2:6]
                ):
                    normalized[field], raw["raw_" + field] = amount(value)
                staged.append(
                    {
                        **ident,
                        "month_num": month,
                        "row_number": number,
                        "account_code": code,
                        "description": str(row[1] or ""),
                        "level": 1 + int(code[5:8] != "000") + int(code[9:] != "000"),
                        **normalized,
                        **raw,
                        "source_path": str(path),
                        "source_sha256": config["files"]["balance"]["sha256"],
                        "source_sheet": sheet.title,
                        "source_row_number": number,
                    }
                )
            for row in staged:
                row["is_detail"] = (
                    "true" if is_leaf(row["account_code"], codes) else "false"
                )
            result.extend(staged)
        if not result:
            raise ValueError("No balance rows")
        return result
    finally:
        book.close()


def reconciliation(balance_rows: list[dict], lines: list[dict]) -> dict:
    posted: dict[tuple[int, str], list[Decimal]] = defaultdict(
        lambda: [Decimal(0), Decimal(0)]
    )
    for row in lines:
        key = (row["month_num"], row["account_code"])
        posted[key][0] += Decimal(row["debe"])
        posted[key][1] += Decimal(row["haber"])
    differences = []
    for row in balance_rows:
        if row["is_detail"] != "true":
            continue
        key = (row["month_num"], row["account_code"])
        debit, credit = posted.pop(key, [Decimal(0), Decimal(0)])
        if debit != Decimal(row["cargos"]) or credit != Decimal(row["abonos"]):
            differences.append(
                {
                    "month_num": key[0],
                    "account_code": key[1],
                    "balance_debits": row["cargos"],
                    "policy_debits": str(debit),
                    "balance_credits": row["abonos"],
                    "policy_credits": str(credit),
                }
            )
    balance_months = {row["month_num"] for row in balance_rows}
    missing = [
        {
            "month_num": m,
            "account_code": c,
            "policy_debits": str(v[0]),
            "policy_credits": str(v[1]),
        }
        for (m, c), v in posted.items()
        if m in balance_months and (v[0] or v[1])
    ]
    return {
        "difference_count": len(differences),
        "differences": differences,
        "policy_accounts_not_in_detail_balance": missing,
        "policy_only_months": sorted({r["month_num"] for r in lines} - balance_months),
        "comparison_note": "Posted debits/credits compared to leaf accounts only; no derived opening/closing balances.",
    }


def write_csv(path: Path, rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Cannot write empty family")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o600)
    return {"path": str(path.resolve()), "sha256": sha(path)}


def validation_report(
    ident: dict,
    headers: list[dict],
    lines: list[dict],
    trial: list[dict],
    quality: dict,
) -> dict:
    report = {
        "identity": ident,
        "policy_headers": len(headers),
        "policy_lines": len(lines),
        "trial_balance_rows": len(trial),
        "policy_quality": quality,
        "balance_months": sorted({r["month_num"] for r in trial}),
        "reconciliation": reconciliation(trial, lines),
    }
    material = []
    warnings = list(quality["warnings"])
    for row in report["reconciliation"]["differences"]:
        difference = {
            **row,
            "debit_difference": str(
                Decimal(row["policy_debits"]) - Decimal(row["balance_debits"])
            ),
            "credit_difference": str(
                Decimal(row["policy_credits"]) - Decimal(row["balance_credits"])
            ),
        }
        if (
            max(
                abs(Decimal(difference["debit_difference"])),
                abs(Decimal(difference["credit_difference"])),
            )
            > CENT
        ):
            material.append(difference)
        else:
            warnings.append(
                {**difference, "kind": "balance_policy_rounding_difference"}
            )
    material.extend(
        {**row, "kind": "policy_account_missing_from_balance"}
        for row in report["reconciliation"]["policy_accounts_not_in_detail_balance"]
    )
    report["material_differences"] = material
    report["warnings"] = warnings
    report["status"] = (
        "BLOCKED_RECONCILIATION"
        if material
        else (
            "VALIDATED_WITH_WARNINGS"
            if warnings or quality["precision_rows"]
            else "VALIDATED"
        )
    )
    return report


def withholding(config: dict, full_report: dict) -> tuple[list[dict], dict | None]:
    items = config.get("withheld_months", [])
    if not isinstance(items, list):
        raise ValueError("Withheld months must be an explicit list")
    if not items:
        return [], None
    full_reference = config.get("full_validation_report") or items[0].get("evidence")
    if not isinstance(full_reference, dict):
        raise ValueError("Full validation evidence required for withholding")
    full_path = checked_source(full_reference)
    full_evidence = json.loads(full_path.read_text())
    # Compare JSON-normalized keys because JSON serializes skipped-year keys as strings.
    if full_evidence != json.loads(json.dumps(full_report)):
        raise ValueError("Full validation evidence does not match the actual sources")
    normalized_reference = {"path": str(full_path), "sha256": sha(full_path)}
    source_months = set(full_report["balance_months"]) | set(
        full_report["policy_quality"]["months"]
    )
    selected: list[dict] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid withheld month")
        month = item.get("month_num")
        reason = item.get("reason")
        if (
            type(month) is not int
            or not 1 <= month <= 12
            or month not in source_months
            or month in seen
        ):
            raise ValueError("Duplicate or invalid withheld month")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Withholding requires an explicit reason")
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("Withholding evidence required")
        evidence_path = checked_source(evidence)
        if (
            evidence_path != full_path
            or evidence["sha256"] != normalized_reference["sha256"]
        ):
            raise ValueError(
                "Withholding evidence differs from the full validation report"
            )
        seen.add(month)
        selected.append(
            {
                "month_num": month,
                "reason": reason.strip(),
                "evidence": normalized_reference,
            }
        )
    return sorted(selected, key=lambda row: row["month_num"]), normalized_reference


def family_coverage(headers: list[dict], lines: list[dict], trial: list[dict]) -> dict:
    return {
        name: sorted({row["month_num"] for row in values})
        for name, values in [
            ("trial_balance", trial),
            ("policy_headers", headers),
            ("policy_lines", lines),
        ]
    }


def normalize_company(config: dict, output: Path) -> dict:
    ident = identity(config)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy = checked_source(config["files"]["policy"])
    balance = checked_source(config["files"]["balance"])
    headers, lines, quality = policies(config, policy)
    trial = balances(config, balance)
    checked_source(config["files"]["policy"])
    checked_source(config["files"]["balance"])
    full_report = validation_report(ident, headers, lines, trial, quality)
    withheld, full_reference = withholding(config, full_report)
    excluded = {row["month_num"] for row in withheld}
    source_counts = {
        "policy_headers": len(headers),
        "policy_lines": len(lines),
        "trial_balance_rows": len(trial),
        "coverage": family_coverage(headers, lines, trial),
    }
    withheld_counts = [
        {
            **item,
            "counts": {
                "policy_headers": sum(
                    row["month_num"] == item["month_num"] for row in headers
                ),
                "policy_lines": sum(
                    row["month_num"] == item["month_num"] for row in lines
                ),
                "trial_balance_rows": sum(
                    row["month_num"] == item["month_num"] for row in trial
                ),
            },
        }
        for item in withheld
    ]
    if excluded:
        headers = [row for row in headers if row["month_num"] not in excluded]
        lines = [row for row in lines if row["month_num"] not in excluded]
        trial = [row for row in trial if row["month_num"] not in excluded]
        admitted_rows = {row["row_number"] for row in lines}
        admitted_policies = {row["policy_id"] for row in headers}
        quality = {
            **quality,
            "precision_rows": [
                row for row in quality["precision_rows"] if row["row"] in admitted_rows
            ],
            "warnings": [
                row
                for row in quality["warnings"]
                if row["policy_id"] in admitted_policies
            ],
            "months": sorted({row["month_num"] for row in lines}),
        }
    if not headers or not lines or not trial:
        raise ValueError("Withholding cannot remove an entire source family")
    report = validation_report(ident, headers, lines, trial, quality)
    if excluded:
        report.update(
            {
                "full_source_counts": source_counts,
                "withheld_months": withheld_counts,
                "full_validation_report": full_reference,
                "admitted_coverage": family_coverage(headers, lines, trial),
            }
        )
    derived = {
        kind: write_csv(output / (kind + ".csv"), values)
        for kind, values in [
            ("trial_balance", trial),
            ("policy_headers", headers),
            ("policy_lines", lines),
        ]
    }
    validation = output / "validation.json"
    validation.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    validation.chmod(0o600)
    manifest = {
        "schema_version": "samchat.historical.explicit.v1",
        **ident,
        "originals": [
            {
                "path": str(balance),
                "sha256": sha(balance),
                "source_family": "original_trial_balance",
            },
            {
                "path": str(policy),
                "sha256": sha(policy),
                "source_family": "original_policy_lines",
            },
        ],
        "derived": derived,
        "expected_coverage": family_coverage(headers, lines, trial),
        "validation_report": {
            "path": str(validation.resolve()),
            "sha256": sha(validation),
        },
    }
    if withheld:
        manifest["withheld_months"] = withheld
        manifest["full_validation_report"] = full_reference
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    (output / "manifest.json").chmod(0o600)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    companies = config["companies"]
    seen = set()
    reports = []
    for company in companies:
        ident = identity(company)
        key = (ident["company_code"], ident["fiscal_year"])
        if key in seen:
            raise ValueError("Duplicate company/year in configuration")
        seen.add(key)
        reports.append(
            normalize_company(company, args.output_dir / ident["company_code"])
        )
    print(
        json.dumps(
            [
                {
                    "identity": r["identity"],
                    "rows": r["policy_lines"],
                    "balance_differences": r["reconciliation"]["difference_count"],
                }
                for r in reports
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
