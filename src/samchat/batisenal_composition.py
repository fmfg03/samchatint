"""Closed configuration and read-only preflight for the isolated support pilot.

No environment lookup, credentials, connections or schema creation at import time.
The caller supplies resources from the separately approved isolated installation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

PREFIX = "/soporte/batisenal/piloto"
MIGRATIONS = (
    "20260919_agent_action_receipts_v01.sql",
    "20261001_private_oauth_storage_review.sql",
    "20261023_support_batisenal.sql",
    "20261023_support_batisenal_agent.sql",
    "20261024_batisenal_pilot.sql",
    "20261025_batisenal_v01_policy_fence.sql",
    "20261025_batisenal_v01_synthetic_protocol.sql",
    "20261025_batisenal_v01_case_storage.sql",
    "20261109_batisenal_real_client_binding.sql",
)
FIELDS = frozenset(
    {
        "version",
        "enabled",
        "release",
        "tree",
        "installation_id",
        "owner_ref",
        "database",
        "socket_directory",
        "port",
        "schema",
        "owner_role",
        "runtime_role",
        "actor_id",
        "tournament_id",
        "case_id",
        "report_id",
        "organization",
        "project",
        "origin",
        "client_id",
        "redirect_uri",
        "registration_sha256",
        "agent_kind",
        "signer_ref",
        "verifier_ref",
        "key_id",
        "public_key_sha256",
        "cookie_ref",
        "cookie_name",
        "audit_path",
        "evidence_path",
        "retention_ref",
        "protocol",
        "approval_ref",
        "policy_sha256",
        "attestation_sha256",
        "migration_hashes",
    }
)


class ConfigurationError(ValueError):
    """Sanitized configuration failure: never include supplied values."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _identifier(value: Any, pattern: str) -> bool:
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _absolute(value: Any) -> bool:
    return (
        type(value) is str
        and value.startswith("/")
        and ".." not in Path(value).parts
        and not any(ord(c) < 32 for c in value)
    )


def validate_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all local selectors before any database connection is opened."""
    if type(raw) is not dict or set(raw) != FIELDS:
        raise ConfigurationError("MANIFEST_FIELDS_INVALID")
    data: dict[str, Any] = json.loads(json.dumps(raw, allow_nan=False))
    if data["version"] != "1" or type(data["enabled"]) is not bool:
        raise ConfigurationError("MANIFEST_VERSION_INVALID")
    for name in ("release", "tree"):
        if not _identifier(data[name], r"[a-f0-9]{40}"):
            raise ConfigurationError("RELEASE_INVALID")
    for name in (
        "registration_sha256",
        "public_key_sha256",
        "policy_sha256",
        "attestation_sha256",
    ):
        if not _identifier(data[name], r"[a-f0-9]{64}"):
            raise ConfigurationError("DIGEST_INVALID")
    if not _identifier(data["schema"], r"bs_test_v01_[a-f0-9]{32}"):
        raise ConfigurationError("ISOLATED_SCHEMA_REQUIRED")
    if not _identifier(data["database"], r"bs_test_[a-z0-9_]{1,48}"):
        raise ConfigurationError("ISOLATED_DATABASE_REQUIRED")
    for name in ("owner_role", "runtime_role"):
        if not _identifier(data[name], r"bs_[a-z0-9_]{1,48}"):
            raise ConfigurationError("ISOLATED_ROLE_REQUIRED")
    if data["owner_role"] == data["runtime_role"]:
        raise ConfigurationError("SEPARATE_OWNER_REQUIRED")
    if type(data["port"]) is not int or not 1024 <= data["port"] <= 65535:
        raise ConfigurationError("PORT_INVALID")
    for name in ("socket_directory", "audit_path", "evidence_path"):
        if not _absolute(data[name]):
            raise ConfigurationError("PRIVATE_PATH_REQUIRED")
    # Unix-only targets: there is intentionally no arbitrary network DSN field.
    for name in ("actor_id", "tournament_id", "case_id", "report_id"):
        try:
            if str(UUID(data[name])) != data[name]:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ConfigurationError("SELECTOR_INVALID") from None
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
    ):
        if not _identifier(data[name], r"[A-Za-z0-9_:.@/-]{1,256}"):
            raise ConfigurationError("REFERENCE_INVALID")
    if not _identifier(data["cookie_name"], r"batisenal_[a-z0-9_]{1,64}"):
        raise ConfigurationError("ISOLATED_COOKIE_REQUIRED")
    for name in ("origin", "redirect_uri"):
        url = urlsplit(data[name]) if type(data[name]) is str else None
        if (
            url is None
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or (name == "origin" and (url.path or url.query))
        ):
            raise ConfigurationError("HTTPS_URL_INVALID")
    if data["origin"] == "https://sam.chat":
        raise ConfigurationError("PRODUCTION_ORIGIN_DENIED")
    if data["protocol"] != "batisenal-synthetic-v01" or data["agent_kind"] not in {
        "ai_agent",
        "synthetic_stub",
    }:
        raise ConfigurationError("PROTOCOL_INVALID")
    hashes = data["migration_hashes"]
    if (
        type(hashes) is not dict
        or set(hashes) != set(MIGRATIONS)
        or any(not _identifier(h, r"[a-f0-9]{64}") for h in hashes.values())
    ):
        raise ConfigurationError("MIGRATION_HASHES_INVALID")
    return data


@dataclass(frozen=True)
class TargetAuthorization:
    """Owner-supplied exact installation decision; never read from HTTP/model input."""

    manifest_sha256: str
    approval_ref: str
    synthetic_only: bool

    def validate(self, manifest: dict[str, Any]) -> None:
        if (
            self.synthetic_only is not True
            or self.manifest_sha256 != digest(manifest)
            or self.approval_ref != manifest["approval_ref"]
        ):
            raise ConfigurationError("ISOLATED_TARGET_NOT_AUTHORIZED")


@dataclass(frozen=True)
class RuntimeResources:
    """Explicit resource injection; sync PostgreSQL driver is required externally."""

    async_engine: Any = field(repr=False)
    oauth_engine: Any = field(repr=False)
    identity_engine: Any = field(repr=False)
    signer: Any = field(repr=False)
    session_secret: str = field(repr=False)
    build_receipt: Mapping[str, Any] | None = field(default=None, repr=False)


def validate_engine_url(engine: Any, manifest: dict[str, Any], *, owner=False) -> None:
    """Reject external/production/ambiguous DSNs without connecting."""
    url = engine.url
    role = manifest["owner_role"] if owner else manifest["runtime_role"]
    if (
        url.get_backend_name() != "postgresql"
        or url.database != manifest["database"]
        or url.username != role
        or url.host not in (None, "")
        or dict(url.query)
        != {"host": manifest["socket_directory"], "port": str(manifest["port"])}
        or url.port is not None
    ):
        raise ConfigurationError("DATABASE_TARGET_MISMATCH")


def preflight(engine: Any, manifest: dict[str, Any], *, owner=False) -> None:
    """Inspect effective PostgreSQL privileges, before domain SQL or protocol hooks."""
    from sqlalchemy import text

    validate_engine_url(engine, manifest, owner=owner)
    role = manifest["owner_role"] if owner else manifest["runtime_role"]
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT current_database() AS db, current_user AS role,
                   current_schema() AS schema, current_schemas(false) AS schemas,
                   r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls,
                   pg_has_role(current_user, :owner, 'MEMBER') AS owner_member,
                   pg_has_role(current_user, d.datdba, 'MEMBER') AS database_owner,
                   pg_get_userbyid(n.nspowner) AS schema_owner,
                   has_schema_privilege(current_user, :schema, 'CREATE') AS can_create,
                   has_database_privilege(current_user, current_database(),
                                          'CREATE') AS db_create
              FROM pg_roles r, pg_namespace n, pg_database d
             WHERE r.rolname=current_user AND n.nspname=:schema
               AND d.datname=current_database()
        """),
                {"owner": manifest["owner_role"], "schema": manifest["schema"]},
            )
            .mappings()
            .one()
        )
        if (
            row["db"] != manifest["database"]
            or row["role"] != role
            or row["schema"] != manifest["schema"]
            or list(row["schemas"])
            not in ([manifest["schema"]], [manifest["schema"], "pg_catalog"])
            or row["schema_owner"] != manifest["owner_role"]
            or any(
                row[k]
                for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls")
            )
            or (
                not owner
                and any(
                    row[k]
                    for k in (
                        "owner_member",
                        "database_owner",
                        "can_create",
                        "db_create",
                    )
                )
            )
        ):
            raise ConfigurationError("DATABASE_AUTHORITY_MISMATCH")
        if not owner:
            foreign_access = connection.execute(
                text("""
                SELECT EXISTS(SELECT 1 FROM pg_namespace n
                 WHERE n.nspname NOT IN (:schema, 'pg_catalog', 'information_schema')
                   AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                   AND (pg_has_role(current_user,n.nspowner,'MEMBER')
                        OR has_schema_privilege(current_user,n.oid,'USAGE')
                        OR has_schema_privilege(current_user,n.oid,'CREATE')))
            """),
                {"schema": manifest["schema"]},
            ).scalar_one()
            unsafe = connection.execute(
                text("""
                SELECT EXISTS(SELECT 1 FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname=:schema AND c.relkind IN ('r','p')
                 AND (pg_has_role(current_user,c.relowner,'MEMBER') OR
                   (c.relname NOT LIKE 'support_%'
                    AND c.relname NOT LIKE 'private_oauth_%'
                    AND c.relname NOT IN ('agent_action_receipts','auth_logs',
                                         'customer_success_audit_events')
                    AND (has_table_privilege(current_user,c.oid,'INSERT')
                      OR has_table_privilege(current_user,c.oid,'UPDATE')
                      OR has_table_privilege(current_user,c.oid,'DELETE')
                      OR has_table_privilege(current_user,c.oid,'TRUNCATE')))))
            """),
                {"schema": manifest["schema"]},
            ).scalar_one()
            if foreign_access or unsafe:
                raise ConfigurationError("DATABASE_PRIVILEGES_EXCEED_PILOT")


def migration_inventory(root: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for name in MIGRATIONS:
        path = root / "database" / "migrations" / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != manifest["migration_hashes"][name]:
            raise ConfigurationError("MIGRATION_SOURCE_MISMATCH")
        result.append({"name": name, "sha256": actual})
    return result


def verify_binding(engine: Any, manifest: dict[str, Any]) -> None:
    """Match every manifest selector against the persisted owner attestation."""
    from sqlalchemy import text

    with engine.begin() as connection:
        row = connection.execute(text("""
            SELECT b.*, f.attestation_sha256 AS fixture_hash, f.active,
                   d.empleado_id AS report_owner, d.tipo AS report_type,
                   d.torneo_id AS report_tournament,
                   t.requester_empleado_id AS requester, t.page_url
            FROM support_batisenal_v01_case_binding b
            JOIN support_batisenal_pilot_fixtures f ON f.ticket_id=b.ticket_id
            JOIN documentos d ON d.id=b.report_id
            JOIN support_tickets t ON t.id=b.ticket_id
            WHERE b.singleton IS TRUE
        """)).mappings().one_or_none()
        keys = {
            "ticket_id": "case_id",
            "report_id": "report_id",
            "actor_id": "actor_id",
            "tournament_id": "tournament_id",
            "installation_id": "installation_id",
            "organization_namespace": "organization",
            "support_project": "project",
            "attestation_sha256": "attestation_sha256",
            "fixture_hash": "attestation_sha256",
            "report_owner": "actor_id",
            "requester": "actor_id",
            "report_tournament": "tournament_id",
            "agent_kind": "agent_kind",
        }
        if (
            row is None
            or any(str(row[k]) != manifest[v] for k, v in keys.items())
            or row["active"] is not True
            or row["synthetic_only"] is not True
            or row["report_type"] != "INFORME"
            or row["page_url"] != "batisenal:private"
        ):
            raise ConfigurationError("PERSISTED_BINDING_MISMATCH")
        real = manifest["agent_kind"] == "ai_agent"
        expected = (
            manifest["client_id"],
            manifest["origin"] + PREFIX,
            manifest["origin"] + PREFIX + "/mcp",
            manifest["registration_sha256"],
        )
        actual = tuple(
            row[k]
            for k in (
                "agent_client_id",
                "agent_issuer",
                "agent_resource",
                "agent_registration_sha256",
            )
        )
        if actual != (expected if real else (None, None, None, None)):
            raise ConfigurationError("PERSISTED_CLIENT_MISMATCH")


def verify_private_audit(path: str) -> None:
    """Require a provisioned private SQLite audit; never create it on startup."""
    import sqlite3

    file = Path(path)
    if (
        not file.is_file()
        or file.is_symlink()
        or file.stat().st_mode & 0o077
        or file.parent.stat().st_mode & 0o077
    ):
        raise ConfigurationError("PRIVATE_AUDIT_NOT_PROVISIONED")
    try:
        with sqlite3.connect(file.as_uri() + "?mode=ro", uri=True) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(transport_audit)")
            }
        if columns != {"id", "actor_hash", "action", "outcome", "created_at"}:
            raise ConfigurationError("AUDIT_SCHEMA_MISMATCH")
    except sqlite3.Error:
        raise ConfigurationError("AUDIT_SCHEMA_UNAVAILABLE") from None


def _git_object(kind: str, content: bytes) -> str:
    # Git object-format compatibility; plan authorization uses SHA-256 digest.
    return hashlib.sha1(
        kind.encode() + b" " + str(len(content)).encode() + b"\0" + content,
        usedforsecurity=False,
    ).hexdigest()


def _receipt_tree(entries: list[dict[str, str]]) -> str:
    tree: dict[str, Any] = {}
    for entry in entries:
        path = entry["path"]
        parts = path.split("/")
        if any(part in {"", ".", "..", ".git"} for part in parts) or "\\" in path:
            raise ConfigurationError("BUILD_PATH_INVALID")
        branch = tree
        for part in parts[:-1]:
            branch = branch.setdefault(part, {})
            if not isinstance(branch, dict):
                raise ConfigurationError("BUILD_PATH_CONFLICT")
        if parts[-1] in branch:
            raise ConfigurationError("BUILD_PATH_CONFLICT")
        branch[parts[-1]] = (entry["mode"], entry["sha1"])

    def encode(branch: dict[str, Any]) -> str:
        rows = []
        for name, value in branch.items():
            directory = isinstance(value, dict)
            mode, sha1 = ("40000", encode(value)) if directory else value
            rows.append(
                (
                    (name + ("/" if directory else "")).encode(),
                    mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha1),
                )
            )
        return _git_object("tree", b"".join(row[1] for row in sorted(rows)))

    return encode(tree)


def policy_fingerprint(root: Path) -> str:
    """Same immutable rule and source closure used by CaseService.rule_version."""
    base = root / "src/devnous/gastos/services"
    source = (base / "batisenal_case_service.py").read_bytes()
    rule = None
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RULE"
            for target in node.targets
        ):
            rule = ast.literal_eval(node.value)
    if not isinstance(rule, str):
        raise ConfigurationError("BUILD_POLICY_INVALID")
    return hashlib.sha256(
        rule.encode()
        + source
        + (base / "batisenal_case_authority.py").read_bytes()
        + (base / "expense_coi_export_service.py").read_bytes()
    ).hexdigest()


def verify_build_receipt(root: Path, manifest: dict[str, Any], receipt: Any) -> None:
    """Verify Git object identity and local executable bytes without Git/network.

    Release packaging supplies the raw commit and complete git ls-tree entries.
    Their tree binds every declared blob. Runtime source files are independently
    rehashed locally; hashes and the advertised release cannot merely be echoed.
    Technical fixtures may use explicitly synthetic commit objects, never E2E proof.
    """
    if type(receipt) is not dict or set(receipt) != {"commit", "entries"}:
        raise ConfigurationError("BUILD_RECEIPT_REQUIRED")
    commit, entries = receipt["commit"], receipt["entries"]
    if (
        not isinstance(commit, str)
        or not commit
        or len(commit) > 1048576
        or "\0" in commit
        or type(entries) is not list
        or not 1 <= len(entries) <= 50000
    ):
        raise ConfigurationError("BUILD_RECEIPT_INVALID")
    if _git_object("commit", commit.encode()) != manifest["release"]:
        raise ConfigurationError("BUILD_RELEASE_MISMATCH")
    if commit.splitlines()[0] != "tree " + manifest["tree"]:
        raise ConfigurationError("BUILD_TREE_MISMATCH")
    for entry in entries:
        if (
            type(entry) is not dict
            or set(entry) != {"path", "mode", "sha1"}
            or not isinstance(entry["path"], str)
            or not isinstance(entry["mode"], str)
            or entry["mode"] not in {"100644", "100755", "120000"}
            or not _identifier(entry["sha1"], r"[a-f0-9]{40}")
        ):
            raise ConfigurationError("BUILD_ENTRY_INVALID")
    if _receipt_tree(entries) != manifest["tree"]:
        raise ConfigurationError("BUILD_TREE_MISMATCH")
    runtime_prefixes = ("src/", "scripts/", "database/", "static/", "config/")
    declared = {entry["path"] for entry in entries}
    required = {str(path.relative_to(root)) for path in (root / "src").rglob("*.py")}
    required.update(
        {
            "scripts/batisenal_real_pilot.py",
            "requirements-runtime.txt",
            "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md",
        }
    )
    if not required <= declared:
        raise ConfigurationError("BUILD_SOURCE_INCOMPLETE")
    for entry in entries:
        name = entry["path"]
        if not (name.startswith(runtime_prefixes) or name in required):
            continue
        path = root / name
        if any(
            parent.is_symlink()
            for parent in path.parents
            if parent != root and root in parent.parents
        ):
            raise ConfigurationError("BUILD_PATH_INVALID")
        try:
            if entry["mode"] == "120000":
                raise ConfigurationError("BUILD_RUNTIME_SYMLINK_DENIED")
            else:
                if path.is_symlink() or not path.is_file():
                    raise ConfigurationError("BUILD_SOURCE_MISMATCH")
                mode = "100755" if path.stat().st_mode & 0o111 else "100644"
                if mode != entry["mode"]:
                    raise ConfigurationError("BUILD_SOURCE_MODE_MISMATCH")
                content = path.read_bytes()
            if _git_object("blob", content) != entry["sha1"]:
                raise ConfigurationError("BUILD_SOURCE_MISMATCH")
        except OSError:
            raise ConfigurationError("BUILD_SOURCE_MISMATCH") from None
    if policy_fingerprint(root) != manifest["policy_sha256"]:
        raise ConfigurationError("BUILD_POLICY_MISMATCH")
