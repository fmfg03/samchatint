"""Operator plans are source-bound and perform no mutations without authority."""

import hashlib
import importlib.util
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from test_host_config import manifest, synthetic_build_receipt

from samchat.batisenal_composition import (
    MIGRATIONS,
    ConfigurationError,
    TargetAuthorization,
    digest,
)

spec = importlib.util.spec_from_file_location(
    "pilot_operator", "scripts/batisenal_real_pilot.py"
)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


def fixture_values():
    return dict(
        synthetic_only=True,
        tournament_name="SIMULACION torneo",
        title="SIMULACION consulta de prueba",
        description="SIMULACION informe de prueba aislado",
        reference="SIMULACION 001",
        currency="MXN",
        total="1200.00",
        context={"module": "solicitudes"},
        request_key="synthetic-host-test",
        created_at="2026-11-10T00:00:00+00:00",
        expires_at="2026-11-11T00:00:00+00:00",
    )


def reviewed():
    data = manifest()
    for name in MIGRATIONS:
        data["migration_hashes"][name] = hashlib.sha256(
            (Path("database/migrations") / name).read_bytes()
        ).hexdigest()
    values = fixture_values()
    data["case_id"] = str(
        uuid5(
            NAMESPACE_URL,
            f"batisenal/{data['actor_id']}/{data['organization']}/"
            f"{data['project']}/{values['request_key']}",
        )
    )
    return data, values


def test_plan_pins_all_source_and_explicit_fixture_values():
    data, values = reviewed()
    result = operator.plan(
        Path.cwd(), data, values, build_receipt=synthetic_build_receipt(data)
    )
    assert result["fixture"] == values
    assert result["manifest_sha256"] == digest(data)
    assert len(result["native_sources"]) == 5
    assert result["steps"][0]["kind"] == "native_schema"
    assert result["steps"][-1]["kind"] == "transport"
    assert result["steps"][-2]["kind"] == "binding"


def test_changed_migration_blocks_plan():
    data, values = reviewed()
    data["migration_hashes"][MIGRATIONS[0]] = "0" * 64
    with pytest.raises(ConfigurationError, match="SOURCE_MISMATCH"):
        operator.plan(
            Path.cwd(), data, values, build_receipt=synthetic_build_receipt(data)
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("synthetic_only", False),
        ("title", "real report"),
        ("total", "NaN"),
        ("total", "-1"),
        ("currency", "USD"),
        ("created_at", "2026-11-10"),
        ("context", {"secret": "forbidden"}),
        ("request_key", "another-key"),
    ],
)
def test_fixture_requires_reviewed_synthetic_selectors(key, value):
    data, values = reviewed()
    values[key] = value
    with pytest.raises(ConfigurationError):
        operator.validate_fixture(values, data)


def test_apply_requires_exact_confirmation_before_connecting(tmp_path):
    data, values = reviewed()
    plan = operator.plan(
        Path.cwd(), data, values, build_receipt=synthetic_build_receipt(data)
    )
    with pytest.raises(ConfigurationError, match="EXACT_PLAN_CONFIRMATION_REQUIRED"):
        operator.apply_plan(
            root=Path.cwd(),
            manifest=data,
            approved_plan=plan,
            expected_plan_sha256=digest(plan),
            authorization=TargetAuthorization(digest(data), "test", True),
            engine=None,
            backup=tmp_path / "absent",
            backup_sha256="0" * 64,
            receipt_path=tmp_path / "receipt",
            confirm=False,
            build_receipt=synthetic_build_receipt(data),
        )


def test_receipts_private_and_bound_to_plan(tmp_path):
    path = tmp_path / "receipt"
    assert operator._read_receipts(path) == {}
    operator._write_receipts(path, {"completed": []})
    assert operator._read_receipts(path) == {"completed": []}
    path.chmod(0o644)
    with pytest.raises(ConfigurationError, match="PRIVATE_RECEIPT"):
        operator._read_receipts(path)


def test_other_checkout_with_valid_receipt_cannot_authorize_executing_operator(
    tmp_path,
):
    import os

    from samchat.batisenal_composition import verify_build_receipt

    data, values = reviewed()
    receipt = synthetic_build_receipt(data)
    approved_plan = operator.plan(Path.cwd(), data, values, build_receipt=receipt)
    copied_root = tmp_path / "other-checkout"
    for entry in receipt["entries"]:
        destination = copied_root / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(Path.cwd() / entry["path"], destination)
    # Exact valid bytes and Git identities still do not bind the executing module.
    verify_build_receipt(copied_root, data, receipt)
    with pytest.raises(ConfigurationError, match="EXECUTING_SOURCE_ROOT_MISMATCH"):
        operator.plan(copied_root, data, values, build_receipt=receipt)

    class NoSQL:
        def connect(self):
            pytest.fail("Mismatched source root must be rejected before SQL")

    with pytest.raises(ConfigurationError, match="EXECUTING_SOURCE_ROOT_MISMATCH"):
        operator.apply_plan(
            root=copied_root,
            manifest=data,
            approved_plan=approved_plan,
            expected_plan_sha256=digest(approved_plan),
            authorization=TargetAuthorization(digest(data), data["approval_ref"], True),
            engine=NoSQL(),
            backup=tmp_path / "absent",
            backup_sha256="0" * 64,
            receipt_path=tmp_path / "receipt",
            confirm=True,
            build_receipt=receipt,
        )


def test_cli_default_plan_is_pure_and_binds_reviewed_files(
    tmp_path, capsys, monkeypatch
):
    import json

    data, values = reviewed()
    receipt = synthetic_build_receipt(data)
    manifest_path, build_path, fixture_path = (
        tmp_path / name for name in ("manifest.json", "build.json", "fixture.json")
    )
    manifest_path.write_text(json.dumps(data))
    build_path.write_text(json.dumps(receipt))
    fixture_path.write_text(json.dumps(values))
    monkeypatch.setattr(
        operator, "preflight", lambda *args, **kwargs: pytest.fail("Plan opened DB")
    )
    before = set(tmp_path.iterdir())
    result = operator.main(
        [
            "--manifest",
            str(manifest_path),
            "--build-receipt",
            str(build_path),
            "--fixture",
            str(fixture_path),
        ]
    )
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sha256"] == digest(output["plan"])
    assert output["plan"]["manifest_sha256"] == digest(data)
    assert output["plan"]["build_receipt_sha256"] == digest(receipt)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("action", ["apply", "inventory"])
def test_cli_refuses_implicit_owner_effects(action, tmp_path, capsys, monkeypatch):
    import json

    data, _ = reviewed()
    receipt = synthetic_build_receipt(data)
    manifest_path, build_path = tmp_path / "manifest.json", tmp_path / "build.json"
    manifest_path.write_text(json.dumps(data))
    build_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(
        operator, "preflight", lambda *args, **kwargs: pytest.fail("Implicit DB")
    )
    assert (
        operator.main(
            [
                action,
                "--manifest",
                str(manifest_path),
                "--build-receipt",
                str(build_path),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "BATISENAL_OPERATOR_BLOCKED\n"


def test_cli_receipt_check_does_not_claim_runtime_verification(tmp_path, capsys):
    import json

    data, _ = reviewed()
    receipt = synthetic_build_receipt(data)
    manifest_path, build_path = tmp_path / "manifest.json", tmp_path / "build.json"
    manifest_path.write_text(json.dumps(data))
    build_path.write_text(json.dumps(receipt))
    assert (
        operator.main(
            [
                "verify",
                "--manifest",
                str(manifest_path),
                "--build-receipt",
                str(build_path),
                "--receipts",
                str(tmp_path / "absent.json"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["receipt_verified"] is False
    assert result["runtime_verified"] is False


def test_cli_malformed_private_input_returns_sanitized_error(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"credential":"private-value",')
    assert (
        operator.main(
            [
                "--manifest",
                str(manifest_path),
                "--build-receipt",
                str(tmp_path / "missing"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.err == "BATISENAL_OPERATOR_BLOCKED\n"
    assert "private-value" not in captured.err + captured.out


def test_receipt_atomic_failure_preserves_prior_evidence(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    operator._write_receipts(path, {"previous": "durable"})

    def fail_replace(*args):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(operator.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated storage failure"):
        operator._write_receipts(path, {"new": "not committed"})
    assert operator._read_receipts(path) == {"previous": "durable"}
    assert not (tmp_path / "receipt.json.new").exists()


def test_receipt_symlink_and_non_object_are_rejected(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("[]")
    path.chmod(0o600)
    with pytest.raises(ConfigurationError, match="RECEIPT_INVALID"):
        operator._read_receipts(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ConfigurationError, match="PRIVATE_RECEIPT_REQUIRED"):
        operator._write_receipts(link, {"replace": "forbidden"})
    assert path.read_text() == "[]"


@pytest.mark.parametrize("kind", ["wrong_plan", "invalid_prefix", "missing_recovery"])
def test_apply_rejects_untrusted_resume_before_sql(kind, tmp_path):
    data, values = reviewed()
    build = synthetic_build_receipt(data)
    expected = operator.plan(Path.cwd(), data, values, build_receipt=build)
    path = tmp_path / "receipt.json"
    if kind == "wrong_plan":
        operator._write_receipts(path, {"plan_sha256": "0" * 64, "completed": []})
        error = "RECEIPT_PLAN_MISMATCH"
    elif kind == "invalid_prefix":
        operator._write_receipts(
            path, {"plan_sha256": digest(expected), "completed": [expected["steps"][1]]}
        )
        error = "RECEIPT_SEQUENCE_INVALID"
    else:
        error = "APPROVED_RECOVERY_PROVENANCE_REQUIRED"

    class NoSQL:
        def connect(self):
            pytest.fail("Invalid resume connected to database")

    with pytest.raises(ConfigurationError, match=error):
        operator.apply_plan(
            root=Path.cwd(),
            manifest=data,
            approved_plan=expected,
            expected_plan_sha256=digest(expected),
            authorization=TargetAuthorization(digest(data), data["approval_ref"], True),
            engine=NoSQL(),
            backup=tmp_path / "absent",
            backup_sha256="0" * 64,
            receipt_path=path,
            confirm=True,
            build_receipt=build,
        )
