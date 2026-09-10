import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("scripts/build_finance_g2_decision_packet.py")
SPEC = spec_from_file_location("finance_g2_decision_packet", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_g2_packet_has_closed_decision_catalog_and_pending_default():
    assert MODULE.DECISIONS[-1] == "PENDING_FINANCE_DECISION"
    expected = {"PAID_REQUEST", "CXP_CFDI", "AMEX", "SOURCE_MISSING"}
    assert expected.issubset(MODULE.DECISIONS)


def test_g2_query_is_observational_and_honors_accepted_treasury_matches():
    sql = MODULE._plan_sql().upper()
    assert sql.lstrip().startswith("WITH")
    assert "ACCEPT_TREASURY_CFDI_MATCH" in sql
    assert "ACCEPT_TREASURY_PAYMENT_REQUEST_MATCH" in sql
    prohibited = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE")
    assert not any(re.search(rf"\b{token}\b", sql) for token in prohibited)


def test_g2_uses_repeatable_read_only_snapshot():
    assert MODULE.READ_ONLY_TRANSACTION_SQL == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


def test_g2_never_auto_accepts_candidate_evidence():
    item = MODULE._decision_item({
        "paid_request_candidates": [{"reference": "SOL-1"}],
        "cxp_cfdi_candidates": [{"uuid": "CFDI-1"}],
    })

    assert item["decision_status"] == "PENDING_FINANCE_DECISION"
    assert item["decision"] is None
    assert item["decision_owner"] is None
    assert item["decision_evidence"] is None
