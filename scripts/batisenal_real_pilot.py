#!/usr/bin/env python3
"""Owner-run isolated migration operator. Default plan never opens a database.

No environment DSN, account enrolment, grants, credentials, network or deployment.
Provision the native fixture graph through its owner before the protocol stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from samchat.batisenal_composition import (  # noqa: E402
    ConfigurationError,
    TargetAuthorization,
    digest,
    migration_inventory,
    preflight,
    validate_manifest,
    verify_build_receipt,
)


def plan(
    root: Path,
    manifest: dict[str, Any],
    fixture: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    *,
    build_receipt=None,
) -> dict[str, Any]:
    """Pure review object: exact selectors and migration bytes, no I/O effects."""
    root = root.resolve()
    if root != Path(__file__).resolve().parents[1]:
        raise ConfigurationError("EXECUTING_SOURCE_ROOT_MISMATCH")
    data = validate_manifest(manifest)
    verify_build_receipt(root, data, build_receipt)
    if fixture is not None:
        validate_fixture(fixture, data)
    return {
        "fixture": fixture,
        "recovery": recovery,
        "build_receipt_sha256": digest(build_receipt),
        "native_sources": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "src/devnous/gastos/models.py",
                "src/devnous/gastos/schema_guard.py",
                "src/devnous/gastos/services/project_authorization_service.py",
                "src/devnous/gastos/services/customer_success_audit.py",
                "scripts/batisenal_real_pilot.py",
            )
        },
        "version": "1",
        "manifest_sha256": digest(data),
        "approval_ref": data["approval_ref"],
        "release": data["release"],
        "tree": data["tree"],
        "database": data["database"],
        "schema": data["schema"],
        "owner_role": data["owner_role"],
        "runtime_role": data["runtime_role"],
        "actor_id": data["actor_id"],
        "case_id": data["case_id"],
        "report_id": data["report_id"],
        "tournament_id": data["tournament_id"],
        "steps": [{"kind": "native_schema", "name": "canonical-model-dependencies"}]
        + [{"kind": "migration", **item} for item in migration_inventory(root, data)]
        + [
            {"kind": "binding", "name": "owner-reviewed-synthetic-binding"},
            {
                "kind": "transport",
                "name": "20261023_batisenal_transport_sqlite.sql",
                "sha256": hashlib.sha256(
                    (
                        root
                        / "database/migrations/20261023_batisenal_transport_sqlite.sql"
                    ).read_bytes()
                ).hexdigest(),
            },
        ],
        "precondition": (
            "Owner must provision and attest the exact synthetic graph "
            "before protocol installation"
        ),
        "effects": (
            "isolated schema only; no grants, identities, credentials "
            "or service activation"
        ),
    }


def _read_receipts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ConfigurationError("PRIVATE_RECEIPT_REQUIRED")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ConfigurationError("RECEIPT_INVALID")
    return value


def _write_receipts(path: Path, receipts: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise ConfigurationError("PRIVATE_RECEIPT_REQUIRED")
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(receipts, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_native_schema(connection: Any) -> None:
    """Explicit owner DDL using canonical metadata, never application startup."""
    import asyncio

    from sqlalchemy import text

    from devnous.gastos.models import (
        AccessControlRule,
        Adjunto,
        Aprobacion,
        CFDIReport,
        CuentaDeGastos,
        Documento,
        Empleado,
        ExpenseReport,
        SupportTicket,
        SupportTicketComment,
        Tournament,
    )
    from devnous.gastos.schema_guard import SCHEMA_PATCHES
    from devnous.gastos.services.customer_success_audit import (
        ensure_customer_success_audit_schema,
    )
    from devnous.gastos.services.project_authorization_service import (
        ensure_project_authorization_schema,
    )

    selected = set()

    def add(table):
        if table in selected:
            return
        selected.add(table)
        for fk in table.foreign_keys:
            add(fk.column.table)

    for model in (
        Empleado,
        Tournament,
        CuentaDeGastos,
        Documento,
        Adjunto,
        Aprobacion,
        CFDIReport,
        ExpenseReport,
        AccessControlRule,
        SupportTicket,
        SupportTicketComment,
    ):
        add(model.__table__)
    Empleado.metadata.create_all(connection, tables=list(selected), checkfirst=False)
    for name, sql in SCHEMA_PATCHES:
        if name in {
            "create_access_profiles_table",
            "create_empleado_access_profiles_table",
        }:
            connection.execute(text(sql))

    class OwnerSession:
        async def execute(self, statement, parameters=None):
            return connection.execute(statement, parameters or {})

    async def canonical_prerequisites():
        # These owner helpers require only an awaitable execute interface.
        owner_session: Any = OwnerSession()
        await ensure_project_authorization_schema(owner_session)
        await ensure_customer_success_audit_schema(owner_session)

    asyncio.run(canonical_prerequisites())


def validate_fixture(fixture: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Every synthetic value is part of the reviewed plan; no real enrolment."""
    from datetime import datetime
    from decimal import Decimal, InvalidOperation
    from uuid import NAMESPACE_URL, uuid5

    fields = {
        "synthetic_only",
        "tournament_name",
        "title",
        "description",
        "reference",
        "currency",
        "total",
        "context",
        "request_key",
        "created_at",
        "expires_at",
    }
    if (
        type(fixture) is not dict
        or set(fixture) != fields
        or fixture["synthetic_only"] is not True
    ):
        raise ConfigurationError("SYNTHETIC_FIXTURE_INVALID")
    for name in ("tournament_name", "title", "description", "reference"):
        if not isinstance(fixture[name], str) or not fixture[name].startswith(
            "SIMULACION "
        ):
            raise ConfigurationError("SYNTHETIC_LABEL_REQUIRED")
    if fixture["currency"] != "MXN" or type(fixture["total"]) is not str:
        raise ConfigurationError("FIXTURE_AMOUNT_INVALID")
    try:
        amount = Decimal(fixture["total"])
        exponent = amount.as_tuple().exponent
        if (
            not amount.is_finite()
            or amount < 0
            or not isinstance(exponent, int)
            or exponent < -2
        ):
            raise ValueError
        created = datetime.fromisoformat(fixture["created_at"])
        expires = datetime.fromisoformat(fixture["expires_at"])
        if (
            created.utcoffset() is None
            or expires.utcoffset() is None
            or expires <= created
        ):
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ConfigurationError("FIXTURE_VALUES_INVALID") from None
    if (
        type(fixture["context"]) is not dict
        or set(fixture["context"]) - {"module", "action", "folio", "error"}
        or any(type(v) is not str or len(v) > 500 for v in fixture["context"].values())
    ):
        raise ConfigurationError("FIXTURE_CONTEXT_INVALID")
    from devnous.gastos.services.batisenal_service import BatisenalError, request_key

    try:
        key = request_key(fixture["request_key"])
    except BatisenalError:
        raise ConfigurationError("FIXTURE_REQUEST_KEY_INVALID") from None
    expected = str(
        uuid5(
            NAMESPACE_URL,
            f"batisenal/{manifest['actor_id']}/{manifest['organization']}/"
            f"{manifest['project']}/{key}",
        )
    )
    if expected != manifest["case_id"]:
        raise ConfigurationError("FIXTURE_CASE_SELECTOR_MISMATCH")


def provision_graph(connection, manifest, fixture):
    """Owner fixture creation using canonical tables; account/grants must exist."""
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy import insert, select

    from devnous.gastos.models import Documento, Empleado, SupportTicket, Tournament
    from devnous.gastos.services import batisenal_service as owner
    from devnous.gastos.services.batisenal_pilot_service import fixtures
    from devnous.gastos.services.support_ticket_service import validate_ticket_input

    validate_fixture(fixture, manifest)
    actor = UUID(manifest["actor_id"])
    active = connection.execute(
        select(Empleado.activo).where(Empleado.id == actor)
    ).scalar_one_or_none()
    if active is not True:
        raise ConfigurationError("OWNER_ENROLLED_ACTOR_REQUIRED")
    title, description, _, _ = validate_ticket_input(
        asunto=fixture["title"],
        descripcion=fixture["description"],
        categoria="bug",
        prioridad="normal",
    )
    ticket, report, tournament = (
        UUID(manifest[k]) for k in ("case_id", "report_id", "tournament_id")
    )
    created = datetime.fromisoformat(fixture["created_at"])
    fingerprint = owner.digest(
        [
            manifest["organization"],
            manifest["project"],
            title,
            description,
            fixture["context"],
            None,
        ]
    )
    connection.execute(
        insert(Tournament).values(
            id=tournament, name=fixture["tournament_name"], active=True
        )
    )
    connection.execute(
        insert(Documento).values(
            id=report,
            empleado_id=actor,
            tipo="INFORME",
            numero_referencia=fixture["reference"],
            estado="borrador",
            torneo_id=tournament,
            currency=fixture["currency"],
            monto_total=Decimal(fixture["total"]),
        )
    )
    connection.execute(
        insert(SupportTicket).values(
            id=ticket,
            requester_empleado_id=actor,
            asunto=title,
            descripcion=description,
            categoria="bug",
            prioridad="normal",
            estado="abierto",
            page_url=owner.MARKER,
            created_at=created,
            updated_at=created,
        )
    )
    connection.execute(
        insert(owner.cases).values(
            ticket_id=ticket,
            organization=manifest["organization"],
            project=manifest["project"],
            actor=str(actor),
            request_key=fixture["request_key"],
            request_hash=fingerprint,
            context_json=json.dumps(fixture["context"]),
            expires_at=datetime.fromisoformat(fixture["expires_at"]),
        )
    )
    connection.execute(
        insert(fixtures).values(
            ticket_id=ticket,
            attestation_sha256=manifest["attestation_sha256"],
            active=True,
        )
    )


def provision_binding(connection, manifest):
    from sqlalchemy import text

    values = {
        k: manifest[k]
        for k in (
            "case_id",
            "report_id",
            "actor_id",
            "tournament_id",
            "installation_id",
            "organization",
            "project",
            "attestation_sha256",
            "agent_kind",
        )
    }
    real = manifest["agent_kind"] == "ai_agent"
    values.update(
        client=manifest["client_id"] if real else None,
        issuer=manifest["origin"] + "/soporte/batisenal/piloto" if real else None,
        resource=manifest["origin"] + "/soporte/batisenal/piloto/mcp" if real else None,
        registration=manifest["registration_sha256"] if real else None,
    )
    connection.execute(
        text("""
        INSERT INTO support_batisenal_v01_case_binding
          (singleton,ticket_id,report_id,actor_id,tournament_id,installation_id,
           organization_namespace,support_project,attestation_sha256,synthetic_only,revision,
           agent_kind,agent_client_id,agent_issuer,agent_resource,agent_registration_sha256)
        VALUES(true,CAST(:case_id AS uuid),CAST(:report_id AS uuid),
          CAST(:actor_id AS uuid),
          CAST(:tournament_id AS uuid),:installation_id,:organization,:project,
          :attestation_sha256,
          true,0,:agent_kind,:client,:issuer,:resource,:registration)
    """),
        values,
    )


def inventory(engine: Any, manifest: dict[str, Any]) -> list[str]:
    """Metadata only; caller separately authorizes the exact isolated target."""
    from sqlalchemy import text

    data = validate_manifest(manifest)
    preflight(engine, data, owner=True)
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema "
                    "ORDER BY tablename"
                ),
                {"schema": data["schema"]},
            ).scalars()
        )


_PG_BIN = Path("/usr/lib/postgresql/16/bin")
_RECOVERY_FIELDS = frozenset(
    {
        "version",
        "manifest_sha256",
        "release",
        "tree",
        "approval_ref",
        "source_identity",
        "restore_identity",
        "restore_manifest_sha256",
        "restore_approval_ref",
        "schema_fingerprint",
        "archive_sha256",
        "archive_size",
        "format",
        "created_at",
        "pg_dump_version",
        "pg_restore_version",
        "restore_verified",
    }
)


def _pg_command(tool: str, args: list[str], engine=None, *, output=None) -> bytes:
    """Run installed PG tools with explicit target and no ambient connection state."""
    import subprocess

    env = {"PATH": os.defpath, "LANG": "C", "PGPASSFILE": "/dev/null"}
    if engine is not None and engine.url.password:
        env["PGPASSWORD"] = str(engine.url.password)
    try:
        result = subprocess.run(
            [str(_PG_BIN / tool), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        raise ConfigurationError("RECOVERY_TOOL_FAILED") from None
    return result.stdout or b""


def _connection_args(data: dict[str, Any]) -> list[str]:
    return [
        "--host",
        data["socket_directory"],
        "--port",
        str(data["port"]),
        "--username",
        data["owner_role"],
        "--dbname",
        data["database"],
    ]


def _database_identity(connection, data: dict[str, Any]) -> dict[str, str]:
    from sqlalchemy import text

    row = connection.execute(text("""
        SELECT current_database() AS database, current_user AS role,
          current_schema() AS schema, pg_get_userbyid(n.nspowner) AS schema_owner
        FROM pg_namespace n WHERE n.nspname=current_schema()
    """)).mappings().one()
    identity = dict(row)
    if identity != {
        "database": data["database"],
        "role": data["owner_role"],
        "schema": data["schema"],
        "schema_owner": data["owner_role"],
    }:
        raise ConfigurationError("RECOVERY_DATABASE_IDENTITY_MISMATCH")
    identity.update(socket_directory=data["socket_directory"], port=str(data["port"]))
    return identity


def _schema_fingerprint(connection, schema: str) -> str:
    """Hash catalog and actual row values without exporting them in receipts."""
    from sqlalchemy import text

    tables = list(
        connection.execute(
            text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relkind IN ('r','p') ORDER BY c.relname
    """),
            {"schema": schema},
        ).scalars()
    )
    records: list[Any] = []
    quote = connection.dialect.identifier_preparer.quote
    for table in tables:
        columns = [
            tuple(row)
            for row in connection.execute(
                text("""
            SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
                   pg_get_expr(d.adbin,d.adrelid)
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relname=:table AND a.attnum>0
              AND NOT a.attisdropped ORDER BY a.attnum
        """),
                {"schema": schema, "table": table},
            )
        ]
        rows = list(
            connection.execute(
                text(
                    f"SELECT to_jsonb(t)::text FROM {quote(schema)}.{quote(table)} t "
                    "ORDER BY to_jsonb(t)::text"
                )
            ).scalars()
        )
        records.append([table, columns, digest(rows)])
    functions = list(
        connection.execute(
            text("""
        SELECT pg_get_functiondef(p.oid) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=:schema AND p.prokind IN ('f','p')
        ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)
    """),
            {"schema": schema},
        ).scalars()
    )
    constraints = [
        tuple(row)
        for row in connection.execute(
            text("""
        SELECT c.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema
        ORDER BY c.relname,con.conname
    """),
            {"schema": schema},
        )
    ]
    return digest(
        {
            "schema": schema,
            "tables": records,
            "functions": functions,
            "constraints": constraints,
        }
    )


def _archive_inventory(backup: Path, data: dict[str, Any]) -> str:
    import re

    if (
        not backup.is_file()
        or backup.is_symlink()
        or backup.stat().st_mode & 0o077
        or backup.stat().st_size < 100
    ):
        raise ConfigurationError("RECOVERY_ARCHIVE_REQUIRED")
    with backup.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ConfigurationError("RECOVERY_ARCHIVE_REQUIRED")
    listing = _pg_command("pg_restore", ["--list", str(backup)]).decode("utf-8")
    database = re.findall(r"^;\s+dbname: (.+)$", listing, flags=re.MULTILINE)
    schemas = re.findall(
        r"^\d+; \d+ \d+ SCHEMA - (\S+) \S+$", listing, flags=re.MULTILINE
    )
    if database != [data["database"]] or schemas != [data["schema"]]:
        raise ConfigurationError("RECOVERY_ARCHIVE_TARGET_MISMATCH")
    return listing


def capture_recovery(
    *,
    manifest,
    authorization,
    engine,
    restore_manifest,
    restore_authorization,
    restore_engine,
    backup: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Create and restore-check a genuine archive before approving the apply plan.

    Only the separate, explicitly authorized empty restore schema is mutated.
    The source transaction is read-only; it exports the exact dump snapshot.
    """
    import tempfile

    from sqlalchemy import text

    data = validate_manifest(manifest)
    restore = validate_manifest(restore_manifest)
    authorization.validate(data)
    restore_authorization.validate(restore)
    if (
        restore["database"] == data["database"]
        or restore["schema"] != data["schema"]
        or restore["owner_role"] != data["owner_role"]
    ):
        raise ConfigurationError("SEPARATE_RESTORE_TARGET_REQUIRED")
    preflight(engine, data, owner=True)
    preflight(restore_engine, restore, owner=True)
    with restore_engine.connect() as connection:
        restore_identity = _database_identity(connection, restore)
        count = connection.execute(
            text("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema
        """),
            {"schema": restore["schema"]},
        ).scalar_one()
        count += connection.execute(
            text("""
            SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema
        """),
            {"schema": restore["schema"]},
        ).scalar_one()
        if count:
            raise ConfigurationError("EMPTY_RESTORE_TARGET_REQUIRED")
    if backup.exists() or receipt_path.exists() or backup.parent.is_symlink():
        raise ConfigurationError("NEW_RECOVERY_ARTIFACT_REQUIRED")
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    ) as connection:
        with connection.begin():
            source_identity = _database_identity(connection, data)
            snapshot = connection.execute(
                text("SELECT pg_export_snapshot()")
            ).scalar_one()
            created_at = (
                connection.execute(text("SELECT clock_timestamp()"))
                .scalar_one()
                .isoformat()
            )
            fingerprint = _schema_fingerprint(connection, data["schema"])
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                _pg_command(
                    "pg_dump",
                    [
                        *_connection_args(data),
                        "--format=custom",
                        "--schema",
                        data["schema"],
                        "--snapshot",
                        snapshot,
                    ],
                    engine,
                    output=stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
    listing = _archive_inventory(backup, data)
    # The approved restore schema already exists with the correct owner.
    # Exclude only its CREATE SCHEMA entry; never clean/drop a restore target.
    selection = (
        "\n".join(line for line in listing.splitlines() if " SCHEMA - " not in line)
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="bs-restore-list-", delete=True
    ) as toc:
        toc.write(selection)
        toc.flush()
        _pg_command(
            "pg_restore",
            [
                *_connection_args(restore),
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--single-transaction",
                "--schema",
                restore["schema"],
                "--use-list",
                toc.name,
                str(backup),
            ],
            restore_engine,
        )
    with restore_engine.connect() as connection:
        if _schema_fingerprint(connection, restore["schema"]) != fingerprint:
            raise ConfigurationError("RECOVERY_RESTORE_FINGERPRINT_MISMATCH")
    receipt = {
        "version": "1",
        "manifest_sha256": digest(data),
        "release": data["release"],
        "tree": data["tree"],
        "approval_ref": data["approval_ref"],
        "source_identity": source_identity,
        "restore_identity": restore_identity,
        "restore_manifest_sha256": digest(restore),
        "restore_approval_ref": restore["approval_ref"],
        "schema_fingerprint": fingerprint,
        "archive_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "archive_size": backup.stat().st_size,
        "format": "postgresql-custom",
        "created_at": created_at,
        "restore_verified": True,
        "pg_dump_version": _pg_command("pg_dump", ["--version"]).decode().strip(),
        "pg_restore_version": _pg_command("pg_restore", ["--version"]).decode().strip(),
    }
    _write_receipts(receipt_path, receipt)
    return receipt


def validate_recovery(
    *,
    data,
    recovery,
    engine,
    backup,
    backup_sha256,
    restore_manifest,
    restore_authorization,
    restore_engine,
    first_application,
):
    """Validate approved provenance and read back restoration before owner writes."""
    if (
        type(recovery) is not dict
        or set(recovery) != _RECOVERY_FIELDS
        or recovery["version"] != "1"
        or recovery["restore_verified"] is not True
        or recovery["format"] != "postgresql-custom"
        or recovery["manifest_sha256"] != digest(data)
        or any(recovery[k] != data[k] for k in ("release", "tree", "approval_ref"))
    ):
        raise ConfigurationError("APPROVED_RECOVERY_PROVENANCE_REQUIRED")
    restore = validate_manifest(restore_manifest)
    restore_authorization.validate(restore)
    if (
        restore["database"] == data["database"]
        or restore["schema"] != data["schema"]
        or recovery["restore_manifest_sha256"] != digest(restore)
        or recovery["restore_approval_ref"] != restore["approval_ref"]
    ):
        raise ConfigurationError("RECOVERY_RESTORE_TARGET_MISMATCH")
    _archive_inventory(backup, data)
    if (
        hashlib.sha256(backup.read_bytes()).hexdigest() != backup_sha256
        or recovery["archive_sha256"] != backup_sha256
        or recovery["archive_size"] != backup.stat().st_size
    ):
        raise ConfigurationError("RECOVERY_ARCHIVE_HASH_MISMATCH")
    preflight(restore_engine, restore, owner=True)
    with restore_engine.connect() as connection:
        if (
            _database_identity(connection, restore) != recovery["restore_identity"]
            or _schema_fingerprint(connection, restore["schema"])
            != recovery["schema_fingerprint"]
        ):
            raise ConfigurationError("RECOVERY_RESTORE_READBACK_MISMATCH")
    with engine.connect() as connection:
        if _database_identity(connection, data) != recovery["source_identity"] or (
            first_application
            and _schema_fingerprint(connection, data["schema"])
            != recovery["schema_fingerprint"]
        ):
            raise ConfigurationError("RECOVERY_SOURCE_READBACK_MISMATCH")


def apply_plan(
    *,
    root: Path,
    manifest: dict[str, Any],
    approved_plan: dict[str, Any],
    expected_plan_sha256: str,
    authorization: TargetAuthorization,
    engine: Any,
    backup: Path,
    backup_sha256: str,
    receipt_path: Path,
    confirm: bool = False,
    restore_manifest=None,
    restore_authorization=None,
    restore_engine=None,
    build_receipt=None,
) -> dict[str, Any]:
    """Apply exact source bytes with recovery evidence; no implicit re-provisioning.

    An uncertain committed step without its receipt blocks replay rather than
    claiming an automatic repair. The owner must reconcile it from the backup.
    """
    from sqlalchemy import text

    data = validate_manifest(manifest)
    authorization.validate(data)
    expected = plan(
        root,
        data,
        approved_plan.get("fixture"),
        approved_plan.get("recovery"),
        build_receipt=build_receipt,
    )
    if (
        confirm is not True
        or approved_plan != expected
        or digest(expected) != expected_plan_sha256
    ):
        raise ConfigurationError("EXACT_PLAN_CONFIRMATION_REQUIRED")
    receipts = _read_receipts(receipt_path)
    if receipts and receipts.get("plan_sha256") != expected_plan_sha256:
        raise ConfigurationError("RECEIPT_PLAN_MISMATCH")
    completed = receipts.get("completed", [])
    if type(completed) is not list or completed != expected["steps"][: len(completed)]:
        raise ConfigurationError("RECEIPT_SEQUENCE_INVALID")
    if (
        restore_manifest is None
        or restore_authorization is None
        or restore_engine is None
    ):
        raise ConfigurationError("APPROVED_RECOVERY_PROVENANCE_REQUIRED")
    preflight(engine, data, owner=True)
    validate_recovery(
        data=data,
        recovery=approved_plan.get("recovery"),
        engine=engine,
        backup=backup,
        backup_sha256=backup_sha256,
        restore_manifest=restore_manifest,
        restore_authorization=restore_authorization,
        restore_engine=restore_engine,
        first_application=not receipts.get("completed"),
    )
    receipts.setdefault("plan_sha256", expected_plan_sha256)
    receipts.setdefault("completed", [])
    for step in expected["steps"]:
        if step in receipts["completed"]:
            continue
        with engine.begin() as connection:
            if step["kind"] == "native_schema":
                count = connection.execute(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"),
                    {"schema": data["schema"]},
                ).scalar_one()
                if count:
                    raise ConfigurationError("UNRECEIPTED_SCHEMA_REQUIRES_REVIEW")
                prepare_native_schema(connection)
            elif step["kind"] == "binding":
                provision_binding(connection, data)
            elif step["kind"] == "transport":
                import sqlite3

                path = Path(data["audit_path"])
                if path.exists() or path.parent.is_symlink():
                    raise ConfigurationError("UNRECEIPTED_AUDIT_REQUIRES_REVIEW")
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if path.parent.stat().st_mode & 0o077:
                    raise ConfigurationError("PRIVATE_AUDIT_DIRECTORY_REQUIRED")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                with sqlite3.connect(path) as audit:
                    audit.executescript(
                        (root / "database/migrations" / step["name"]).read_text()
                    )
            else:
                if step["name"] == "20261025_batisenal_v01_policy_fence.sql":
                    if approved_plan.get("fixture") is None:
                        raise ConfigurationError("REVIEWED_SYNTHETIC_FIXTURE_REQUIRED")
                    provision_graph(connection, data, approved_plan["fixture"])
                # This stage requires owner-provisioned records, not title labels.
                if step["name"] == "20261025_batisenal_v01_synthetic_protocol.sql":
                    graph = connection.execute(
                        text(
                            "SELECT count(*) FROM support_batisenal_pilot_fixtures "
                            "WHERE ticket_id=CAST(:case AS uuid) AND active IS TRUE "
                            "AND attestation_sha256=:attestation"
                        ),
                        {
                            "case": data["case_id"],
                            "attestation": data["attestation_sha256"],
                        },
                    ).scalar_one()
                    if graph != 1:
                        raise ConfigurationError("OWNER_ATTESTED_GRAPH_REQUIRED")
                    connection.execute(
                        text(
                            "INSERT INTO support_batisenal_v01_policy_guard "
                            "VALUES(true,0)"
                        )
                    )
                sql = (root / "database" / "migrations" / step["name"]).read_text()
                # Historical scripts own BEGIN/COMMIT; use exact script in its
                # own DBAPI transaction, not lexical rewriting of SQL bodies.
                if not sql.strip():
                    raise ConfigurationError("MIGRATION_EMPTY")
                raw = connection.connection.driver_connection
                raw.execute(sql)
        receipts["completed"].append(step)
        _write_receipts(receipt_path, receipts)
    return receipts


def verify_receipts(
    root: Path,
    manifest: dict[str, Any],
    receipt_path: Path,
    fixture=None,
    recovery=None,
    *,
    build_receipt=None,
) -> bool:
    expected = plan(root, manifest, fixture, recovery, build_receipt=build_receipt)
    receipt = _read_receipts(receipt_path)
    return (
        receipt.get("plan_sha256") == digest(expected)
        and receipt.get("completed") == expected["steps"]
    )


def verify(
    root: Path,
    manifest: dict[str, Any],
    receipt_path: Path,
    fixture=None,
    recovery=None,
    *,
    build_receipt=None,
    engine,
    authorization: TargetAuthorization,
) -> bool:
    """Read back the exact target, runtime privileges, binding and private audit."""
    from samchat.batisenal_composition import verify_binding, verify_private_audit

    data = validate_manifest(manifest)
    authorization.validate(data)
    if not verify_receipts(
        root, data, receipt_path, fixture, recovery, build_receipt=build_receipt
    ):
        raise ConfigurationError("MIGRATION_RECEIPTS_INCOMPLETE")
    preflight(engine, data)
    verify_binding(engine, data)
    verify_private_audit(data["audit_path"])
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="plan",
        choices=("plan", "inventory", "apply", "verify"),
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--recovery-receipt", type=Path)
    parser.add_argument("--build-receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data = validate_manifest(json.loads(args.manifest.read_text()))
        build_receipt = json.loads(args.build_receipt.read_text())
        fixture = json.loads(args.fixture.read_text()) if args.fixture else None
        recovery = (
            json.loads(args.recovery_receipt.read_text())
            if args.recovery_receipt
            else None
        )
        if args.action == "plan":
            result = plan(
                args.root, data, fixture, recovery, build_receipt=build_receipt
            )
            print(
                json.dumps({"plan": result, "sha256": digest(result)}, sort_keys=True)
            )
        elif args.action == "verify" and args.receipts:
            print(
                json.dumps(
                    {
                        "receipt_verified": verify_receipts(
                            args.root,
                            data,
                            args.receipts,
                            fixture,
                            recovery,
                            build_receipt=build_receipt,
                        ),
                        "runtime_verified": False,
                        "next": "explicit owner resources required",
                    }
                )
            )
        else:
            # No implicit identity/DSN selection. Owner invokes the explicit API
            # with resources/custody from the reviewed operational plan.
            raise ConfigurationError("EXPLICIT_OWNER_RESOURCES_REQUIRED")
    except (ConfigurationError, OSError, ValueError):
        print("BATISENAL_OPERATOR_BLOCKED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
