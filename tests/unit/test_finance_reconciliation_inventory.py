from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re

import pytest


SCRIPT = Path("scripts/audit_finance_reconciliation_inventory.py")
SPEC = spec_from_file_location("finance_reconciliation_inventory", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_inventory_query_plan_is_observational_and_covers_required_domains():
    plan = MODULE._query_plan()
    names = {item["name"] for item in plan}
    assert {
        "document_states",
        "cfdi_type_counts",
        "ar_match_states",
        "bank_movement_states",
        "payment_run_closure_states",
        "poliza_origins",
    }.issubset(names)
    forbidden = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE")
    for item in plan:
        sql = item["sql"].upper()
        assert sql.lstrip().startswith(("SELECT", "WITH"))
        assert not any(re.search(rf"\\b{token}\\b", sql) for token in forbidden)


def test_inventory_excludes_payroll_from_cxc_by_classifying_it_for_finance():
    payroll_query = next(
        item for item in MODULE._query_plan() if item["name"] == "payroll_cfdi_in_cxc"
    )
    assert payroll_query["classification"] == "FINANCE_DECISION"
    assert "tipo_de_comprobante" in payroll_query["sql"]
    assert "'N'" in payroll_query["sql"]


def test_exception_queries_report_total_candidates_before_limiting_references():
    for item in MODULE._query_plan():
        if item["kind"] == "exception":
            assert "COUNT(*) OVER() AS total_candidates" in item["sql"]


def test_inventory_requires_receipt_outside_repository(tmp_path):
    outside = tmp_path / "receipt.json"
    assert MODULE._validate_output_path(str(outside)) == outside.resolve()
    with pytest.raises(SystemExit, match="output_path_must_be_outside_repository"):
        MODULE._validate_output_path(str(Path("finance-receipt.json")))
