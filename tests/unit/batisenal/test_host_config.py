"""Local configuration tests: no network, database or real identities."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.engine import URL

from samchat.batisenal_composition import (
    MIGRATIONS,
    ConfigurationError,
    TargetAuthorization,
    digest,
    validate_engine_url,
    validate_manifest,
)
from samchat.batisenal_isolated_app import create_app


def manifest():
    data = {
        name: "test"
        for name in (
            "installation_id",
            "owner_ref",
            "organization",
            "project",
            "client_id",
            "signer_ref",
            "verifier_ref",
            "key_id",
            "cookie_ref",
            "retention_ref",
            "approval_ref",
        )
    }
    data.update(
        version="1",
        enabled=False,
        release="a" * 40,
        tree="b" * 40,
        database="bs_test_host",
        schema="bs_test_v01_" + "c" * 32,
        socket_directory="/tmp/batisenal-test/socket",
        port=5432,
        owner_role="bs_owner",
        runtime_role="bs_runtime",
        cookie_name="batisenal_test",
        origin="https://pilot.example.test",
        redirect_uri="https://client.example.test/callback",
        agent_kind="synthetic_stub",
        protocol="batisenal-synthetic-v01",
        audit_path="/tmp/batisenal-test/audit.sqlite",
        evidence_path="/tmp/batisenal-test/evidence",
        migration_hashes={name: "d" * 64 for name in MIGRATIONS},
    )
    for index, key in enumerate(
        ("actor_id", "tournament_id", "case_id", "report_id"), 1
    ):
        data[key] = str(UUID(int=index))
    for key in (
        "registration_sha256",
        "public_key_sha256",
        "policy_sha256",
        "attestation_sha256",
    ):
        data[key] = "e" * 64
    return data


def test_closed_manifest_preserves_input_and_hash():
    source = manifest()
    actual = validate_manifest(source)
    assert actual == source and actual is not source
    assert digest(actual) == digest(dict(reversed(list(source.items()))))
    TargetAuthorization(digest(source), "test", True).validate(source)


@pytest.mark.parametrize(
    "key,value",
    [
        ("extra", "secret"),
        ("enabled", "true"),
        ("version", 1),
        ("schema", "public"),
        ("database", "samchat"),
        ("owner_role", "postgres"),
        ("runtime_role", "bs_owner"),
        ("socket_directory", "/tmp/../run"),
        ("port", True),
        ("port", 80),
        ("actor_id", "alice"),
        ("origin", "https://sam.chat"),
        ("origin", "http://pilot.example.test"),
        ("origin", "https://pilot.example.test/path"),
        ("redirect_uri", "https://secret@client.example.test/callback"),
        ("client_id", "user supplied"),
        ("cookie_name", "session"),
        ("agent_kind", "Chatty"),
        ("protocol", "other"),
        ("registration_sha256", "bad"),
        ("migration_hashes", {}),
    ],
)
def test_rejects_ambiguous_or_external_configuration(key, value):
    source = manifest()
    source[key] = value
    with pytest.raises(ConfigurationError):
        validate_manifest(source)


def test_authorization_exact_hash_ref_and_synthetic_target():
    source = manifest()
    for approval in (
        TargetAuthorization("0" * 64, "test", True),
        TargetAuthorization(digest(source), "other", True),
        TargetAuthorization(digest(source), "test", False),
    ):
        with pytest.raises(ConfigurationError, match="TARGET_NOT_AUTHORIZED"):
            approval.validate(source)


def test_url_rejected_before_connect():
    data = manifest()
    url = URL.create(
        "postgresql+psycopg",
        username=data["runtime_role"],
        database=data["database"],
        query={"host": data["socket_directory"], "port": "5432"},
    )
    validate_engine_url(SimpleNamespace(url=url), data)
    for changed in (
        url.set(host="localhost"),
        url.set(database="samchat"),
        url.set(username="postgres"),
        url.set(query={"host": "/run/postgresql"}),
    ):
        with pytest.raises(ConfigurationError, match="TARGET_MISMATCH"):
            validate_engine_url(SimpleNamespace(url=changed), data)


def test_default_off_only_health_no_canonical_auth_or_financial_imports():
    import sys

    before = set(sys.modules)
    app = create_app()
    assert {route.path for route in app.routes} == {"/healthz", "/readyz"}
    assert app.state.pilot is None
    assert "copa_telmex_dashboard" not in set(sys.modules) - before
    assert "devnous.gastos.routes.auth_routes" not in set(sys.modules) - before
    assert create_app(manifest()).state.pilot is None


def test_enabled_requires_explicit_authorized_resources():
    data = manifest()
    data["enabled"] = True
    with pytest.raises(ConfigurationError, match="EXPLICIT_RESOURCES_REQUIRED"):
        create_app(data)


def test_schema_document_has_same_closed_fields():
    import json

    from samchat.batisenal_composition import FIELDS

    document = json.loads(
        Path("config/batisenal-pilot-manifest.schema.json").read_text()
    )
    assert set(document["required"]) == FIELDS
    assert set(document["properties"]) == FIELDS
    assert document["additionalProperties"] is False


def test_private_transport_store_requires_explicit_schema(tmp_path):
    import sqlite3

    from samchat.batisenal_composition import verify_private_audit

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "audit.sqlite"
    with pytest.raises(ConfigurationError, match="NOT_PROVISIONED"):
        verify_private_audit(str(path))
    path.touch(mode=0o600)
    with pytest.raises(ConfigurationError, match="SCHEMA_MISMATCH"):
        verify_private_audit(str(path))
    with sqlite3.connect(path) as db:
        db.executescript(
            Path(
                "database/migrations/20261023_batisenal_transport_sqlite.sql"
            ).read_text()
        )
    verify_private_audit(str(path))
    path.chmod(0o644)
    with pytest.raises(ConfigurationError, match="NOT_PROVISIONED"):
        verify_private_audit(str(path))


def test_metadata_status_never_claims_chatty_acceptance():
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {
            "status": "disabled",
            "chatty_acceptance": "not_verified",
        }
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/login").status_code == 404
        assert client.post("/soporte/batisenal/piloto/mcp").status_code == 404


def synthetic_build_receipt(data, root=None):
    """Hash local source into a synthetic, uncommitted Git test object."""
    from samchat.batisenal_composition import (
        _git_object,
        _receipt_tree,
        policy_fingerprint,
    )

    root = root or Path.cwd()
    names = {str(path.relative_to(root)) for path in (root / "src").rglob("*.py")}
    names.update(
        {
            "scripts/batisenal_real_pilot.py",
            "requirements-runtime.txt",
            "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md",
        }
    )
    entries = [
        {
            "path": name,
            "mode": ("100755" if (root / name).stat().st_mode & 0o111 else "100644"),
            "sha1": _git_object("blob", (root / name).read_bytes()),
        }
        for name in sorted(names)
    ]
    tree = _receipt_tree(entries)
    commit = (
        f"tree {tree}\n"
        "author Synthetic test <test@example.test> 1 +0000\n"
        "committer Synthetic test <test@example.test> 1 +0000\n\n"
        "Uncommitted technical test receipt; never release evidence\n"
    )
    data.update(
        release=_git_object("commit", commit.encode()),
        tree=tree,
        policy_sha256=policy_fingerprint(root),
    )
    return {"commit": commit, "entries": entries}
