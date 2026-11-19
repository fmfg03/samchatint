"""Effective privilege checks in the harness-owned Unix-only PostgreSQL cluster."""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from samchat.batisenal_composition import ConfigurationError, preflight


@pytest.fixture
def isolated_catalog():
    socket = os.environ.get("BATISENAL_TEST_SOCKET")
    if not socket:
        pytest.skip("Run scripts/batisenal/run_isolated_acceptance.py")
    path = Path(socket).resolve()
    if not (path.parent.name.startswith("bs-accept-") and path.name == "socket"):
        pytest.fail("Only the harness-created Unix cluster is accepted")
    import psycopg
    from psycopg import sql

    suffix = uuid4().hex
    db, owner, runtime = (
        "bs_test_" + suffix,
        "bs_owner_" + suffix,
        "bs_runtime_" + suffix,
    )
    schema = "bs_test_v01_" + suffix
    manifest = dict(
        database=db,
        owner_role=owner,
        runtime_role=runtime,
        schema=schema,
        socket_directory=str(path),
        port=5432,
    )
    with psycopg.connect(
        host=str(path), user="fixture", dbname="postgres", autocommit=True
    ) as admin:
        for role in (owner, runtime):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOBYPASSRLS"
                ).format(sql.Identifier(role))
            )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    setup = psycopg.connect(host=str(path), user="fixture", dbname=db, autocommit=True)
    setup.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    setup.execute(
        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
            sql.Identifier(schema), sql.Identifier(owner)
        )
    )
    setup.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(runtime)
        )
    )
    url = URL.create(
        "postgresql+psycopg",
        username=runtime,
        database=db,
        query={"host": str(path), "port": "5432"},
    )
    engine = create_engine(url, connect_args={"options": "-c search_path=" + schema})
    try:
        yield SimpleNamespace(
            manifest=manifest,
            engine=engine,
            admin=setup,
            sql=sql,
            owner=owner,
            runtime=runtime,
            schema=schema,
        )
    finally:
        engine.dispose()
        setup.close()
        with psycopg.connect(
            host=str(path), user="fixture", dbname="postgres", autocommit=True
        ) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db))
            )
            for role in (runtime, owner):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_minimal_runtime_catalog_passes(isolated_catalog):
    f = isolated_catalog
    preflight(f.engine, f.manifest)


def test_effective_public_schema_access_is_denied(isolated_catalog):
    f = isolated_catalog
    f.admin.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    with pytest.raises(ConfigurationError, match="PRIVILEGES_EXCEED"):
        preflight(f.engine, f.manifest)


def test_role_inheritance_to_owner_is_denied(isolated_catalog):
    f = isolated_catalog
    f.admin.execute(
        f.sql.SQL("GRANT {} TO {}").format(
            f.sql.Identifier(f.owner), f.sql.Identifier(f.runtime)
        )
    )
    with pytest.raises(ConfigurationError, match="AUTHORITY_MISMATCH"):
        preflight(f.engine, f.manifest)


def test_business_write_via_public_is_denied(isolated_catalog):
    f = isolated_catalog
    f.admin.execute(
        f.sql.SQL("CREATE TABLE {}.documentos(id integer)").format(
            f.sql.Identifier(f.schema)
        )
    )
    f.admin.execute(
        f.sql.SQL("GRANT USAGE ON SCHEMA {} TO PUBLIC").format(
            f.sql.Identifier(f.schema)
        )
    )
    f.admin.execute(
        f.sql.SQL("GRANT UPDATE ON {}.documentos TO PUBLIC").format(
            f.sql.Identifier(f.schema)
        )
    )
    with pytest.raises(ConfigurationError, match="PRIVILEGES_EXCEED"):
        preflight(f.engine, f.manifest)


def test_catalog_only_does_not_modify_business_rows(isolated_catalog):
    f = isolated_catalog
    f.admin.execute(
        f.sql.SQL("CREATE TABLE {}.documentos(id integer)").format(
            f.sql.Identifier(f.schema)
        )
    )
    f.admin.execute(
        f.sql.SQL("INSERT INTO {}.documentos VALUES(42)").format(
            f.sql.Identifier(f.schema)
        )
    )
    preflight(f.engine, f.manifest)
    assert f.admin.execute(
        f.sql.SQL("SELECT id FROM {}.documentos").format(f.sql.Identifier(f.schema))
    ).fetchall() == [(42,)]


def test_operator_native_schema_uses_current_canonical_owners(isolated_catalog):
    import importlib.util

    f = isolated_catalog
    spec = importlib.util.spec_from_file_location(
        "host_native_operator", "scripts/batisenal_real_pilot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(
        f.engine.url.set(username=f.owner),
        connect_args={"options": "-c search_path=" + f.schema},
    )
    try:
        with engine.begin() as connection:
            module.prepare_native_schema(connection)
            names = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"),
                    {"schema": f.schema},
                ).scalars()
            )
            assert {
                "empleados",
                "documentos",
                "support_tickets",
                "expense_reports",
                "access_profiles",
                "empleado_access_profiles",
                "authorization_positions",
                "customer_success_audit_events",
            } <= names
    finally:
        engine.dispose()


@pytest.fixture
def recovery_target(isolated_catalog):
    import psycopg

    f = isolated_catalog
    database = "bs_test_restore_" + uuid4().hex
    f.admin.execute(f.sql.SQL("CREATE DATABASE {}").format(f.sql.Identifier(database)))
    with psycopg.connect(
        host=f.manifest["socket_directory"],
        user="fixture",
        dbname=database,
        autocommit=True,
    ) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        admin.execute(
            f.sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                f.sql.Identifier(f.schema), f.sql.Identifier(f.owner)
            )
        )
    engine = create_engine(
        f.engine.url.set(database=database, username=f.owner),
        connect_args={"options": "-c search_path=" + f.schema},
    )
    try:
        yield SimpleNamespace(engine=engine, database=database)
    finally:
        engine.dispose()
        f.admin.execute(
            f.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                f.sql.Identifier(database)
            )
        )


def test_operator_apply_resume_and_immutable_binding(
    isolated_catalog, recovery_target, tmp_path, monkeypatch
):
    import copy
    import hashlib
    import importlib.util
    from uuid import NAMESPACE_URL, UUID, uuid5

    from sqlalchemy import insert

    from devnous.gastos.models import Empleado
    from samchat.batisenal_composition import MIGRATIONS, TargetAuthorization, digest

    f = isolated_catalog

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    operator = load("host_apply_operator", "scripts/batisenal_real_pilot.py")
    helper = load("host_apply_manifest", "tests/unit/batisenal/test_host_config.py")
    data = helper.manifest()
    data.update(f.manifest)
    data["audit_path"] = str(tmp_path / "private" / "audit.sqlite")
    for name in MIGRATIONS:
        data["migration_hashes"][name] = hashlib.sha256(
            (Path("database/migrations") / name).read_bytes()
        ).hexdigest()
    values = dict(
        synthetic_only=True,
        tournament_name="SIMULACION torneo prueba",
        title="SIMULACION consulta aislada",
        description="SIMULACION informe y caso de prueba",
        reference="SIMULACION 001",
        currency="MXN",
        total="1200.00",
        context={"module": "solicitudes"},
        request_key="synthetic-host-test",
        created_at="2026-11-10T00:00:00+00:00",
        expires_at="2026-11-11T00:00:00+00:00",
    )
    data["case_id"] = str(
        uuid5(
            NAMESPACE_URL,
            f"batisenal/{data['actor_id']}/{data['organization']}/"
            f"{data['project']}/{values['request_key']}",
        )
    )
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = ec.generate_private_key(ec.SECP256R1())
    data["public_key_sha256"] = hashlib.sha256(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()
    data["enabled"] = True
    build_receipt = helper.synthetic_build_receipt(data)
    backup = tmp_path / "before.dump"
    owner_engine = create_engine(
        f.engine.url.set(username=f.owner),
        connect_args={"options": "-c search_path=" + f.schema},
    )
    restore_data = dict(data, database=recovery_target.database)
    authorization = TargetAuthorization(digest(data), data["approval_ref"], True)
    restore_authorization = TargetAuthorization(
        digest(restore_data), data["approval_ref"], True
    )
    recovery = operator.capture_recovery(
        manifest=data,
        authorization=authorization,
        engine=owner_engine,
        restore_manifest=restore_data,
        restore_authorization=restore_authorization,
        restore_engine=recovery_target.engine,
        backup=backup,
        receipt_path=tmp_path / "recovery.json",
    )
    plan = operator.plan(
        Path.cwd(), data, values, recovery, build_receipt=build_receipt
    )
    kwargs = dict(
        root=Path.cwd(),
        manifest=data,
        approved_plan=plan,
        expected_plan_sha256=digest(plan),
        authorization=TargetAuthorization(digest(data), data["approval_ref"], True),
        engine=owner_engine,
        backup=backup,
        backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        receipt_path=tmp_path / "receipt.json",
        confirm=True,
        restore_manifest=restore_data,
        restore_authorization=restore_authorization,
        restore_engine=recovery_target.engine,
        build_receipt=build_receipt,
    )
    try:
        # Valid private bytes and a caller hash alone must never permit writes.
        for payload in (b"", b"not a postgres backup"):
            invalid = tmp_path / "not-a-backup"
            invalid.write_bytes(payload)
            invalid.chmod(0o600)
            bad_recovery = dict(
                recovery,
                archive_sha256=hashlib.sha256(payload).hexdigest(),
                archive_size=len(payload),
            )
            bad_plan = operator.plan(
                Path.cwd(), data, values, bad_recovery, build_receipt=build_receipt
            )
            with pytest.raises(ConfigurationError, match="RECOVERY_ARCHIVE_REQUIRED"):
                operator.apply_plan(
                    **dict(
                        kwargs,
                        approved_plan=bad_plan,
                        expected_plan_sha256=digest(bad_plan),
                        backup=invalid,
                        backup_sha256=bad_recovery["archive_sha256"],
                    )
                )
        # A genuine archive from a different database is still the wrong backup.
        foreign_backup = tmp_path / "wrong-target.dump"
        foreign_backup.write_bytes(
            operator._pg_command(
                "pg_dump",
                [
                    *operator._connection_args(restore_data),
                    "--format=custom",
                    "--schema",
                    data["schema"],
                ],
                recovery_target.engine,
            )
        )
        foreign_backup.chmod(0o600)
        foreign_hash = hashlib.sha256(foreign_backup.read_bytes()).hexdigest()
        foreign_receipt = dict(
            recovery,
            archive_sha256=foreign_hash,
            archive_size=foreign_backup.stat().st_size,
        )
        bad_plan = operator.plan(
            Path.cwd(), data, values, foreign_receipt, build_receipt=build_receipt
        )
        with pytest.raises(
            ConfigurationError, match="RECOVERY_ARCHIVE_TARGET_MISMATCH"
        ):
            operator.apply_plan(
                **dict(
                    kwargs,
                    approved_plan=bad_plan,
                    expected_plan_sha256=digest(bad_plan),
                    backup=foreign_backup,
                    backup_sha256=foreign_hash,
                )
            )
        for field in ("database", "schema"):
            forged = copy.deepcopy(recovery)
            forged["source_identity"][field] = "forged_selector"
            bad_plan = operator.plan(
                Path.cwd(), data, values, forged, build_receipt=build_receipt
            )
            with pytest.raises(
                ConfigurationError, match="RECOVERY_SOURCE_READBACK_MISMATCH"
            ):
                operator.apply_plan(
                    **dict(
                        kwargs,
                        approved_plan=bad_plan,
                        expected_plan_sha256=digest(bad_plan),
                    )
                )
        wrong_release = dict(recovery, release="0" * 40)
        bad_plan = operator.plan(
            Path.cwd(), data, values, wrong_release, build_receipt=build_receipt
        )
        with pytest.raises(
            ConfigurationError, match="APPROVED_RECOVERY_PROVENANCE_REQUIRED"
        ):
            operator.apply_plan(
                **dict(
                    kwargs,
                    approved_plan=bad_plan,
                    expected_plan_sha256=digest(bad_plan),
                )
            )
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"),
                    {"schema": data["schema"]},
                ).scalar_one()
                == 0
            )
        with pytest.raises(ConfigurationError, match="OWNER_ENROLLED_ACTOR_REQUIRED"):
            operator.apply_plan(**kwargs)
        # Explicit technical test actor; this is not enrolment of a real person.
        with owner_engine.begin() as connection:
            connection.execute(
                insert(Empleado).values(
                    id=UUID(data["actor_id"]),
                    nombre="SIMULACION piloto",
                    correo="synthetic@example.test",
                    rol="empleado",
                    activo=True,
                    password_hash=__import__("bcrypt")
                    .hashpw(
                        b"synthetic-test-password",
                        __import__("bcrypt").gensalt(rounds=4),
                    )
                    .decode(),
                )
            )
        receipt = operator.apply_plan(**kwargs)
        assert receipt["completed"] == plan["steps"]
        assert operator.verify_receipts(
            Path.cwd(),
            data,
            kwargs["receipt_path"],
            values,
            recovery,
            build_receipt=build_receipt,
        )
        assert operator.apply_plan(**kwargs) == receipt
        with owner_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT agent_kind,actor_id,report_id "
                    "FROM support_batisenal_v01_case_binding"
                )
            ).one()
            assert tuple(str(v) for v in row) == (
                "synthetic_stub",
                data["actor_id"],
                data["report_id"],
            )
        # Test-only owner grants, exact dedicated runtime; never production.
        with owner_engine.connect() as connection:
            tables = list(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"),
                    {"schema": f.schema},
                ).scalars()
            )
        for table in tables:
            f.admin.execute(
                f.sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                    f.sql.Identifier(f.schema),
                    f.sql.Identifier(table),
                    f.sql.Identifier(f.runtime),
                )
            )
            if table.startswith(("support_", "private_oauth_")) or table in {
                "agent_action_receipts",
                "customer_success_audit_events",
            }:
                f.admin.execute(
                    f.sql.SQL("GRANT INSERT,UPDATE,DELETE ON {}.{} TO {}").format(
                        f.sql.Identifier(f.schema),
                        f.sql.Identifier(table),
                        f.sql.Identifier(f.runtime),
                    )
                )
        _exercise_native_factory(f, data, build_receipt, key, monkeypatch)
        with pytest.raises(Exception, match="V01_BINDING_IMMUTABLE"):
            with owner_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE support_batisenal_v01_case_binding "
                        "SET agent_kind='ai_agent'"
                    )
                )
    finally:
        owner_engine.dispose()


def test_database_owner_cannot_hide_authority_by_revoking_create(isolated_catalog):
    f = isolated_catalog
    f.admin.execute(
        f.sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            f.sql.Identifier(f.manifest["database"]), f.sql.Identifier(f.runtime)
        )
    )
    f.admin.execute(
        f.sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
            f.sql.Identifier(f.manifest["database"]), f.sql.Identifier(f.runtime)
        )
    )
    with pytest.raises(ConfigurationError, match="AUTHORITY_MISMATCH"):
        preflight(f.engine, f.manifest)


def test_foreign_schema_owner_cannot_hide_authority_by_revoking_usage(isolated_catalog):
    f = isolated_catalog
    f.admin.execute(
        f.sql.SQL("CREATE SCHEMA other_business AUTHORIZATION {}").format(
            f.sql.Identifier(f.runtime)
        )
    )
    f.admin.execute(
        f.sql.SQL("REVOKE ALL ON SCHEMA other_business FROM {}").format(
            f.sql.Identifier(f.runtime)
        )
    )
    with pytest.raises(ConfigurationError, match="PRIVILEGES_EXCEED"):
        preflight(f.engine, f.manifest)


def test_capture_recovery_restores_real_rows_and_detects_restore_tampering(
    isolated_catalog, recovery_target, tmp_path
):
    import importlib.util

    from samchat.batisenal_composition import TargetAuthorization, digest

    f = isolated_catalog

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    operator = load("host_capture_operator", "scripts/batisenal_real_pilot.py")
    helper = load("host_capture_manifest", "tests/unit/batisenal/test_host_config.py")
    data = helper.manifest()
    data.update(f.manifest)
    restore = dict(data, database=recovery_target.database)
    auth = TargetAuthorization(digest(data), data["approval_ref"], True)
    restore_auth = TargetAuthorization(digest(restore), restore["approval_ref"], True)
    engine = create_engine(
        f.engine.url.set(username=f.owner),
        connect_args={"options": "-c search_path=" + f.schema},
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE recovery_probe(id int primary key, payload text)")
            )
            connection.execute(
                text("INSERT INTO recovery_probe VALUES(7,'synthetic evidence')")
            )
        backup = tmp_path / "probe.dump"
        receipt = operator.capture_recovery(
            manifest=data,
            authorization=auth,
            engine=engine,
            restore_manifest=restore,
            restore_authorization=restore_auth,
            restore_engine=recovery_target.engine,
            backup=backup,
            receipt_path=tmp_path / "proof.json",
        )
        assert receipt["restore_verified"] is True
        with recovery_target.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT payload FROM recovery_probe WHERE id=7")
                ).scalar_one()
                == "synthetic evidence"
            )
        options = dict(
            data=data,
            recovery=receipt,
            engine=engine,
            backup=backup,
            backup_sha256=receipt["archive_sha256"],
            restore_manifest=restore,
            restore_authorization=restore_auth,
            restore_engine=recovery_target.engine,
            first_application=True,
        )
        operator.validate_recovery(**options)
        with recovery_target.engine.begin() as connection:
            connection.execute(text("UPDATE recovery_probe SET payload='tampered'"))
        with pytest.raises(
            ConfigurationError, match="RECOVERY_RESTORE_READBACK_MISMATCH"
        ):
            operator.validate_recovery(**options)
    finally:
        engine.dispose()


def _exercise_native_factory(f, data, build_receipt, key, monkeypatch):
    """Actual canonical login with a synthetic account; not human/Chatty acceptance."""
    import asyncio

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    from devnous.gastos.routes import auth_routes, dependencies
    from samchat import batisenal_isolated_app as host
    from samchat.batisenal_composition import (
        RuntimeResources,
        TargetAuthorization,
        digest,
    )
    from samchat.private_plugin.oauth_flow import JWTSigner

    monkeypatch.setattr(host, "_composition", None)
    monkeypatch.setattr(auth_routes, "_db_session_maker", None)
    monkeypatch.setattr(dependencies, "_db_session_maker", None)
    async_engine = create_async_engine(
        f.engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": f.schema}},
    )
    oauth = create_engine(
        f.engine.url, connect_args={"options": "-c search_path=" + f.schema}
    )
    identity = create_engine(
        f.engine.url,
        connect_args={"options": "-c search_path=" + f.schema},
        execution_options={"postgresql_readonly": True},
    )
    statements = []
    event.listen(
        async_engine.sync_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, many: statements.append(
            statement
        ),
    )
    resources = RuntimeResources(
        async_engine,
        oauth,
        identity,
        JWTSigner(data["key_id"], key),
        "synthetic-native-factory-session-key",
        build_receipt=build_receipt,
    )
    before = f.admin.execute(
        f.sql.SQL("SELECT to_jsonb(d) FROM {}.documentos d").format(
            f.sql.Identifier(f.schema)
        )
    ).fetchall()
    try:
        app = host.create_app(
            data,
            resources=resources,
            authorization=TargetAuthorization(digest(data), data["approval_ref"], True),
        )
        assert app.dependency_overrides == {}

        async def exercise():
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url=data["origin"]
                ) as client:
                    assert (await client.get("/login")).status_code == 200
                    response = await client.post(
                        "/login",
                        data={
                            "correo": "synthetic@example.test",
                            "password": "synthetic-test-password",
                        },
                    )
                    assert response.status_code == 303
                    assert response.headers["location"] == "/panel", response.headers
                    assert data["cookie_name"] in client.cookies
                    assert (await client.get("/panel")).headers[
                        "location"
                    ] == "/soporte/batisenal/piloto/case"
                    logout = await client.get("/logout")
                    assert logout.status_code == 303
                    assert logout.headers["location"] == "/login"
                    assert data["cookie_name"] not in client.cookies
            finally:
                await async_engine.dispose()

        asyncio.run(exercise())
        assert not any(
            sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))
            for sql in statements
        )
        rows = f.admin.execute(
            f.sql.SQL(
                "SELECT action FROM {}.customer_success_audit_events "
                "ORDER BY created_at"
            ).format(f.sql.Identifier(f.schema))
        ).fetchall()
        assert rows == [("auth.login.success",), ("auth.logout",)]
        assert (
            f.admin.execute(
                f.sql.SQL("SELECT to_jsonb(d) FROM {}.documentos d").format(
                    f.sql.Identifier(f.schema)
                )
            ).fetchall()
            == before
        )
    finally:
        oauth.dispose()
        identity.dispose()
