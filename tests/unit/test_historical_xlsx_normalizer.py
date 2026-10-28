"""Accounting source boundaries and precision remain inspectable after normalization."""

import csv
import importlib.util
import json
from pathlib import Path

from openpyxl import Workbook
import pytest

SPEC = importlib.util.spec_from_file_location(
    "historical_xlsx_normalizer",
    Path(__file__).resolve().parents[2] / "scripts/normalize_historical_xlsx.py",
)
normalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(normalizer)


def workbook(path, headers, rows, title):
    book = Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    book.save(path)
    return {"path": str(path), "sha256": normalizer.sha(path)}


def config(tmp_path, policy_rows=None, balance_rows=None):
    policy = workbook(
        tmp_path / "policy.xlsx",
        normalizer.POLICY_HEADERS,
        policy_rows
        or [
            [
                "5300-010-001 Uniformes",
                "Eg",
                3,
                "30/09/2026",
                "Identical source entry",
                5,
                0,
            ],
            [
                "5300-010-001 Uniformes",
                "Eg",
                3,
                "30/09/2026",
                "Identical source entry",
                5,
                0,
            ],
            ["1120-001-001 Banco", "Eg", 3, "30/09/2026", "Payment", 0, 10],
            ["1120-001-001 Banco", "Eg", 2, "31/12/2025", "Previous fiscal year", 1, 0],
        ],
        "History",
    )
    balance = workbook(
        tmp_path / "balance.xlsx",
        normalizer.BALANCE_HEADERS,
        balance_rows
        or [
            ["5300-000-000", "Gastos", 0, 10, 0, 10],
            ["5300-010-000", "Copa", 0, 10, 0, 10],
            ["5300-010-001", "Uniformes", 0, 10, 0, 10],
        ],
        "AGOSTO",
    )
    return {
        "fiscal_year": 2026,
        "company_code": "01",
        "company_label": "PSP1705058S4",
        "files": {"policy": policy, "balance": balance},
    }


def test_source_row_identity_and_coverage_not_invented(tmp_path):
    c = config(tmp_path)
    headers, rows, quality = normalizer.policies(c, Path(c["files"]["policy"]["path"]))
    assert len(headers) == 1 and len(rows) == 3
    assert [r["row_number"] for r in rows] == [2, 3, 4]
    assert rows[0]["concept"] == rows[1]["concept"]
    assert quality["skipped_year_rows"] == {2025: 1}
    assert quality["months"] == [9]
    balances = normalizer.balances(c, Path(c["files"]["balance"]["path"]))
    assert [r["is_detail"] for r in balances] == ["false", "false", "true"]
    assert {r["month_num"] for r in balances} == {8}


def test_manifest_pins_reports_and_originals_without_database_effects(tmp_path):
    c = config(tmp_path)
    output = tmp_path / "normalized"
    result = normalizer.normalize_company(c, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert result["reconciliation"]["policy_only_months"] == [9]
    assert result["status"] == "BLOCKED_RECONCILIATION"
    assert manifest["validation_report"]["sha256"] == normalizer.sha(
        output / "validation.json"
    )
    assert {x["source_family"] for x in manifest["originals"]} == {
        "original_trial_balance",
        "original_policy_lines",
    }
    for name, file in manifest["derived"].items():
        assert normalizer.sha(Path(file["path"])) == file["sha256"]
        with open(file["path"]) as stream:
            rows = list(csv.DictReader(stream))
        assert all(
            r["fiscal_year"] == "2026"
            and r["company_code"] == "01"
            and r["company_label"] == "PSP1705058S4"
            for r in rows
        )


def test_cent_difference_remains_visible_and_unadjusted(tmp_path):
    c = config(
        tmp_path,
        policy_rows=[
            ["5300-010-001 Uniformes", "Eg", 1, "01/08/2026", "Expense", 1.005, 0],
            ["1120-001-001 Banco", "Eg", 1, "01/08/2026", "Payment", 0, 1],
        ],
    )
    headers, rows, quality = normalizer.policies(c, Path(c["files"]["policy"]["path"]))
    assert rows[0]["debe"] == "1.01" and rows[0]["raw_debe"] == "1.005"
    assert headers[0]["total_debe"] == "1.01" and headers[0]["total_haber"] == "1.00"
    assert quality["warnings"][0]["difference"] == "0.01"
    assert headers[0]["has_summary"] == "false"


def test_material_policy_difference_rejected(tmp_path):
    c = config(
        tmp_path,
        policy_rows=[
            ["5300-010-001 Uniformes", "Eg", 1, "01/08/2026", "Expense", 2, 0]
        ],
    )
    with pytest.raises(ValueError, match="does not reconcile"):
        normalizer.normalize_company(c, tmp_path / "out")
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_balance_duplicate_rejected(tmp_path):
    row = ["5300-010-001", "Uniformes", 0, 10, 0, 10]
    c = config(tmp_path, balance_rows=[row, row])
    with pytest.raises(ValueError, match="Duplicate balance"):
        normalizer.normalize_company(c, tmp_path / "out")


def test_mixed_policy_date_rejected(tmp_path):
    c = config(
        tmp_path,
        policy_rows=[
            ["5300-010-001 Uniformes", "Eg", 1, "01/08/2026", "Expense", 2, 0],
            ["1120-001-001 Banco", "Eg", 1, "02/08/2026", "Payment", 0, 2],
        ],
    )
    with pytest.raises(ValueError, match="multiple dates"):
        normalizer.normalize_company(c, tmp_path / "out")


def test_source_hash_drift_rejected(tmp_path):
    c = config(tmp_path)
    Path(c["files"]["policy"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        normalizer.normalize_company(c, tmp_path / "out")


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "bad"])
def test_missing_and_nonfinite_amount_rejected(value):
    with pytest.raises(ValueError):
        normalizer.amount(value)


def test_cli_writes_manifest_and_reports_real_coverage(tmp_path, monkeypatch, capsys):
    import sys

    c = config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"companies": [c]}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "normalize",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    normalizer.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary[0]["rows"] == 3
    assert (tmp_path / "out" / "01" / "manifest.json").is_file()
    config_path.write_text(json.dumps({"companies": [c, c]}))
    with pytest.raises(ValueError, match="Duplicate company/year"):
        normalizer.main()


def test_hash_rechecked_after_parsing_source(tmp_path, monkeypatch):
    c = config(tmp_path)
    original = normalizer.policies

    def parse_and_change(*args):
        parsed = original(*args)
        Path(c["files"]["policy"]["path"]).write_bytes(b"changed during preparation")
        return parsed

    monkeypatch.setattr(normalizer, "policies", parse_and_change)
    with pytest.raises(ValueError, match="hash differs"):
        normalizer.normalize_company(c, tmp_path / "out")
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_identity_rejects_ambiguous_company_code_and_year(tmp_path):
    c = config(tmp_path)
    for changes in [
        {"fiscal_year": True},
        {"company_code": "RFC1234"},
        {"company_label": "Unknown"},
    ]:
        with pytest.raises(ValueError):
            normalizer.identity({**c, **changes})


def test_negative_subcent_source_amount_not_hidden_by_rounding(tmp_path):
    c = config(
        tmp_path,
        policy_rows=[
            ["5300-010-001 Uniformes", "Eg", 1, "01/08/2026", "Expense", -0.001, 0]
        ],
    )
    with pytest.raises(ValueError, match="Negative debit/credit"):
        normalizer.normalize_company(c, tmp_path / "out")


def withholding_config(tmp_path):
    rows = []
    for month, total in [(7, 20), (8, 11), (9, 30)]:
        rows.extend(
            [
                [
                    "5300-010-001 Uniformes",
                    "Eg",
                    month,
                    f"01/{month:02d}/2026",
                    "Expense",
                    total,
                    0,
                ],
                [
                    "1120-001-001 Banco",
                    "Eg",
                    month,
                    f"01/{month:02d}/2026",
                    "Payment",
                    0,
                    total,
                ],
            ]
        )
    c = config(
        tmp_path,
        policy_rows=rows,
        balance_rows=[
            ["5300-010-001", "Uniformes", 0, 10, 0, 10],
            ["1120-001-001", "Banco", 10, 0, 10, 0],
        ],
    )
    from openpyxl import load_workbook

    path = Path(c["files"]["balance"]["path"])
    book = load_workbook(path)
    sheet = book.create_sheet("JULIO")
    sheet.append(normalizer.BALANCE_HEADERS)
    sheet.append(["5300-010-001", "Uniformes", 0, 20, 0, 20])
    sheet.append(["1120-001-001", "Banco", 20, 0, 20, 0])
    book.save(path)
    c["files"]["balance"]["sha256"] = normalizer.sha(path)
    output = tmp_path / "full"
    normalizer.normalize_company(c, output)
    ref = {
        "path": str(output / "validation.json"),
        "sha256": normalizer.sha(output / "validation.json"),
    }
    return c, ref


def test_whole_month_withheld_from_every_family_preserves_full_evidence(tmp_path):
    c, ref = withholding_config(tmp_path)
    before = Path(ref["path"]).read_bytes()
    c["withheld_months"] = [
        {"month_num": 8, "reason": "Authorised pending reconciliation", "evidence": ref}
    ]
    report = normalizer.normalize_company(c, tmp_path / "admitted")
    manifest = json.loads((tmp_path / "admitted" / "manifest.json").read_text())
    assert report["status"] == "VALIDATED"
    assert report["policy_lines"] == 4 and report["policy_headers"] == 2
    assert report["trial_balance_rows"] == 2
    assert report["full_source_counts"]["policy_lines"] == 6
    assert report["withheld_months"][0]["counts"] == {
        "policy_headers": 1,
        "policy_lines": 2,
        "trial_balance_rows": 2,
    }
    assert manifest["expected_coverage"] == {
        "trial_balance": [7],
        "policy_headers": [7, 9],
        "policy_lines": [7, 9],
    }
    assert manifest["full_validation_report"] == ref
    assert (
        manifest["withheld_months"][0]["reason"] == "Authorised pending reconciliation"
    )
    assert Path(ref["path"]).read_bytes() == before
    for entry in manifest["derived"].values():
        with open(entry["path"]) as stream:
            assert all(int(row["month_num"]) != 8 for row in csv.DictReader(stream))
    assert manifest["originals"][0]["sha256"] == c["files"]["balance"]["sha256"]
    assert report["reconciliation"]["policy_only_months"] == [9]


@pytest.mark.parametrize(
    "bad", ["duplicate", "reason", "absent_month", "changed_hash", "wrong_identity"]
)
def test_withholding_requires_exact_explicit_source_evidence(tmp_path, bad):
    c, ref = withholding_config(tmp_path)
    item = {
        "month_num": 8,
        "reason": "Pending source reconciliation",
        "evidence": dict(ref),
    }
    c["withheld_months"] = [item]
    if bad == "duplicate":
        c["withheld_months"].append(dict(item))
    elif bad == "reason":
        item["reason"] = " "
    elif bad == "absent_month":
        item["month_num"] = 10
    elif bad == "changed_hash":
        item["evidence"]["sha256"] = "0" * 64
    elif bad == "wrong_identity":
        p = Path(ref["path"])
        d = json.loads(p.read_text())
        d["identity"]["company_code"] = "GOAT"
        p.write_text(json.dumps(d))
        item["evidence"]["sha256"] = normalizer.sha(p)
    with pytest.raises(ValueError):
        normalizer.normalize_company(c, tmp_path / "admitted")
    assert not (tmp_path / "admitted" / "manifest.json").exists()
