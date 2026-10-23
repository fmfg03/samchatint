"""Reviewed owner policy admission cannot select identity, grants or files silently."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_direct_common import (
    DirectConflict,
    base_context_config,
    digest,
)

_spec = importlib.util.spec_from_file_location(
    "treatment_policy_cli",
    Path(__file__).parents[3]
    / "scripts/configure_amex_direct_account_treatment_policies.py",
)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def config(actor):
    line, account = str(uuid4()), str(uuid4())
    return {
        "entity_code": "TEST",
        "company_code": "TEST",
        "receiver_rfc": "BBB010101BBB",
        "card_account_ids": [str(uuid4())],
        "benefit_budget_line_ids": [line],
        "partner_debtor_account_ids": [account],
        "actor_scopes": [
            {
                "actor_id": str(actor),
                "benefit_budget_line_ids": [line],
                "partner_debtor_account_ids": [account],
            }
        ],
    }


def args():
    return NS(
        command="apply",
        actor_id=uuid4(),
        reason="Reviewed exact grants",
        request_id="owner-reviewed-policy",
        confirmed=True,
        preview_hash=None,
        manifest="unused",
        manifest_output=None,
        database_url_env="TEST_POLICY_URL",
    )


def manifest(arguments, configs):
    plans = [
        {
            "id": str(uuid4()),
            "actor_id": str(arguments.actor_id),
            "reason": arguments.reason.strip(),
            "base_config_hash": digest(base_context_config(c)),
            "config_hash": digest(c),
            "config_text": cli.canonical(c),
            "base_config_text": cli.canonical(base_context_config(c)),
            "revision": 1,
            "previous_policy_id": None,
            "request_id": arguments.request_id + ":" + digest(base_context_config(c)),
            "anchors": [],
            "catalog": {},
        }
        for c in configs
    ]
    result = {
        "schema_version": cli.VERSION,
        "actor_id": str(arguments.actor_id),
        "reason": arguments.reason.strip(),
        "request_id": arguments.request_id,
        "guard_proof": {},
        "policies": plans,
    }
    result["preview_hash"] = digest(result)
    arguments.preview_hash = result["preview_hash"]
    return result


def test_manifest_binds_actor_reason_full_base_configuration_and_hash():
    arguments = args()
    configs = [config(arguments.actor_id)]
    value = manifest(arguments, configs)
    cli.validate_manifest(value, arguments, configs)
    for field in (
        "schema_version",
        "actor_id",
        "reason",
        "request_id",
        "guard_proof",
        "policies",
        "preview_hash",
    ):
        altered = {**value, field: None}
        with pytest.raises((DirectConflict, KeyError, TypeError)):
            cli.validate_manifest(altered, arguments, configs)
    configs[0]["partner_debtor_account_ids"].append(str(uuid4()))
    with pytest.raises(DirectConflict, match="configuration_changed"):
        cli.validate_manifest(value, arguments, configs)


def test_configured_policy_grants_are_explicit_scoped_and_secret_free(monkeypatch):
    arguments = args()
    value = config(arguments.actor_id)
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([value]))
    assert cli.configured_policies() == [value]
    value["actor_scopes"][0]["partner_debtor_account_ids"].append(str(uuid4()))
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([value]))
    with pytest.raises(DirectConflict, match="actor_grant_outside_context"):
        cli.configured_policies()
    value = config(arguments.actor_id)
    value["nested"] = {"password": "DO-NOT-PRINT"}
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([value]))
    with pytest.raises(DirectConflict, match="secret_config_field_denied"):
        cli.configured_policies()


def test_duplicate_base_or_actor_is_never_silently_resolved(monkeypatch):
    arguments = args()
    value = config(arguments.actor_id)
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([value, value]))
    with pytest.raises(DirectConflict, match="ambiguous_base"):
        cli.configured_policies()
    value["actor_scopes"].append(value["actor_scopes"][0].copy())
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([value]))
    with pytest.raises(DirectConflict, match="ambiguous_actor"):
        cli.configured_policies()


def test_private_manifest_exclusive_mode_and_symlink_rejection(tmp_path):
    path = tmp_path / "preview.json"
    cli.write_private_manifest(str(path), {"preview_hash": "a" * 64})
    assert path.stat().st_mode & 0o777 == 0o600
    assert cli._read_manifest(str(path)) == {"preview_hash": "a" * 64}
    with pytest.raises(FileExistsError):
        cli.write_private_manifest(str(path), {})
    path.chmod(0o644)
    with pytest.raises(DirectConflict, match="private_manifest"):
        cli._read_manifest(str(path))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        cli._read_manifest(str(link))
    fifo = tmp_path / "not-a-file"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(DirectConflict, match="private_manifest"):
        cli._read_manifest(str(fifo))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["confirmed", "preview_hash", "manifest"])
async def test_apply_missing_confirmation_never_connects(monkeypatch, missing):
    arguments = args()
    arguments.preview_hash = "a" * 64
    setattr(arguments, missing, None)
    monkeypatch.setattr(
        cli, "configured_policies", lambda: [config(arguments.actor_id)]
    )
    create = Mock()
    monkeypatch.setattr(cli, "create_async_engine", create)
    with pytest.raises(DirectConflict, match="confirmation_required"):
        await cli.run(arguments, "do-not-connect")
    create.assert_not_called()


def test_parser_actor_request_and_reason_have_no_default():
    actor = uuid4()
    parsed = cli.parse_args(
        [
            "--actor-id",
            str(actor),
            "--reason",
            "Reviewed",
            "--request-id",
            "review-1",
            "--manifest-output",
            "private.json",
        ]
    )
    assert (
        parsed.command == "preview"
        and not parsed.confirmed
        and parsed.actor_id == actor
    )
    with pytest.raises(SystemExit):
        cli.parse_args(["--reason", "Reviewed", "--request-id", "review-1"])


def test_main_missing_database_url_never_exposes_secret(monkeypatch, capsys):
    arguments = args()
    monkeypatch.setattr(cli, "parse_args", lambda: arguments)
    monkeypatch.delenv("TEST_POLICY_URL", raising=False)
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "reason": "explicit_database_url_required",
    }


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("sql", "policy_database_operation_failed"),
        ("file", "policy_manifest_invalid"),
        ("json", "policy_manifest_invalid"),
        ("missing", "policy_manifest_invalid"),
        ("conflict", "policy_owner_guard_mismatch"),
    ],
)
def test_main_failures_are_safe_codes_without_database_or_file_secrets(
    monkeypatch, capsys, kind, expected
):
    from sqlalchemy.exc import SQLAlchemyError

    arguments = args()
    monkeypatch.setattr(cli, "parse_args", lambda: arguments)
    monkeypatch.setenv(
        "TEST_POLICY_URL", "postgresql+asyncpg://sensitive-value@invalid/never-connect"
    )
    errors = {
        "sql": SQLAlchemyError("sensitive-value"),
        "file": OSError("sensitive-value"),
        "json": ValueError("sensitive-value"),
        "missing": KeyError("sensitive-value"),
        "conflict": DirectConflict("policy_owner_guard_mismatch"),
    }

    def fail(coroutine):
        coroutine.close()
        raise errors[kind]

    monkeypatch.setattr(cli.asyncio, "run", fail)
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "sensitive-value" not in output
    assert json.loads(output) == {"status": "blocked", "reason": expected}


def test_main_prints_only_success_receipt_without_configuration(monkeypatch, capsys):
    arguments = args()
    monkeypatch.setattr(cli, "parse_args", lambda: arguments)
    monkeypatch.setenv("TEST_POLICY_URL", "do-not-print")

    def complete(coroutine):
        coroutine.close()
        return {
            "status": "installed",
            "policy_ids": ["reviewed-id"],
            "preview_hash": "a" * 64,
        }

    monkeypatch.setattr(cli.asyncio, "run", complete)
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "do-not-print" not in output and "config_text" not in output
    assert json.loads(output)["status"] == "installed"
