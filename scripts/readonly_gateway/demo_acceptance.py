"""Execute local connector acceptance and hash-bound real repository code read."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from samchat.readonly_gateway.fixtures import (  # noqa: E402
    BASE_COMMIT,
    DOCUMENT,
    fixture,
)
from samchat.readonly_gateway.gateway import Sources  # noqa: E402
from samchat.readonly_gateway.mcp import Connector  # noqa: E402

gateway, actor, db = fixture()
relative = "src/samchat/agent_actions/registry.py"
source_hash = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
gateway.sources = Sources.from_manifest(ROOT, {relative: source_hash}, BASE_COMMIT)
connector = Connector(gateway, lambda: actor)
changes_before = db.total_changes
calls = [
    (
        "samchat.resolve_reference",
        {"reference_kind": "operations", "reference": "SYNTH-001"},
    ),
    ("samchat.document_status", {"document_id": DOCUMENT}),
    ("samchat.document_diagnose", {"document_id": DOCUMENT, "intent": "approve"}),
    ("samchat.document_audit", {"document_id": DOCUMENT}),
    (
        "samchat.study",
        {"date_from": "2026-01-01", "date_to": "2026-02-01", "group_by": "state"},
    ),
    (
        "samchat.source_read",
        {"source_id": gateway.sources.source_id, "path": relative, "line_count": 10},
    ),
]
responses = [
    connector.handle(
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    for index, (name, args) in enumerate(calls)
]
assert all(response and "result" in response for response in responses)
report = {
    "mode": "synthetic_business_data_repository_reviewed_code",
    "production_reference_332": "not_queried_state_unknown",
    "business_database_changes": db.total_changes - changes_before,
    "base_commit": BASE_COMMIT,
    "reviewed_source_hash": source_hash,
    "responses": responses,
    "audit_events": gateway.audit.events,
}
print(json.dumps(report, ensure_ascii=False, indent=2))
db.close()
