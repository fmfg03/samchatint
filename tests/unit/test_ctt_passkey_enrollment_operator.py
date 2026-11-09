"""Operator requests are bounded explicit previews, not web activation flags."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ctt_enrollment_operator", ROOT / "scripts/ctt_passkey_enrollment_operator.py"
)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


def request():
    return dict(
        operation="activate",
        enrollment_id=str(uuid4()),
        fingerprint="sha256:" + "a" * 64,
        account_id=str(uuid4()),
        principal_id=str(uuid4()),
        tenant_id="synthetic",
        edition_id=str(uuid4()),
        actor_role="coordinador",
        faculty="capture_staff_v2",
        valid_from="2026-10-31T00:00:00Z",
        valid_until="2026-11-01T00:00:00Z",
        approval_ref="sha256:" + "b" * 64,
    )


def test_operator_request_requires_all_explicit_scope_and_validity(tmp_path):
    path = tmp_path / "request.json"
    value = request()
    path.write_text(json.dumps(value))
    assert operator.request_file(path) == value


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "bool",
        "role",
        "date",
        "range",
        "uuid",
        "fingerprint",
        "duplicate",
        "oversized",
    ],
)
def test_operator_request_does_not_admit_unreviewable_or_ambiguous_input(
    tmp_path, change
):
    path = tmp_path / "request.json"
    value = request()
    if change == "missing":
        del value["faculty"]
    elif change == "extra":
        value["approved"] = "true"
    elif change == "bool":
        value["valid_until"] = True
    elif change == "role":
        value["faculty"] = "superadmin"
    elif change == "date":
        value["valid_from"] = "2026-10-31"
    elif change == "range":
        value["valid_until"] = value["valid_from"]
    elif change == "uuid":
        value["account_id"] = "someone@example.invalid"
    elif change == "fingerprint":
        value["fingerprint"] = "verified:true"
    raw = json.dumps(value)
    if change == "duplicate":
        raw = raw[:-1] + ',"operation":"activate"}'
    elif change == "oversized":
        raw = "x" * 65537
    path.write_text(raw)
    with pytest.raises((ValueError, TypeError)):
        operator.request_file(path)


def test_operator_failure_never_displays_connection_or_request_diagnostics(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()))
    monkeypatch.setattr(
        "sys.argv",
        ["operator", "--request-json", str(path), "--backend-pin", "isolated"],
    )
    monkeypatch.delenv("CTT_ENROLLMENT_OPERATOR_DSN", raising=False)
    with pytest.raises(SystemExit) as result:
        operator.main()
    assert result.value.code == 1
    assert "operator request denied" in capsys.readouterr().err
