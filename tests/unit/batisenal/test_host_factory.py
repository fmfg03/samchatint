"""Assembly tests with explicit catalog doubles; NOT real-login/Chatty acceptance."""

import hashlib
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from test_host_config import manifest

from samchat import batisenal_isolated_app as host
from samchat.batisenal_composition import (
    ConfigurationError,
    RuntimeResources,
    TargetAuthorization,
    digest,
)
from samchat.private_plugin.oauth_flow import JWTSigner


@pytest.fixture
def assembly(monkeypatch):
    from devnous.gastos.routes import auth_routes, dependencies
    from devnous.gastos.services import customer_success_audit

    monkeypatch.setattr(host, "_composition", None)
    monkeypatch.setattr(auth_routes, "_db_session_maker", None)
    monkeypatch.setattr(dependencies, "_db_session_maker", None)
    for logger in (
        auth_routes.logger,
        dependencies.logger,
        customer_success_audit.logger,
    ):
        monkeypatch.setattr(logger, "filters", [])
    # Technical feature-marker double only. No login POST or real acceptance.
    monkeypatch.setattr(
        customer_success_audit, "PREPROVISIONED_SESSION_SUPPORTED", True, raising=False
    )
    # Assembly doubles do not attest a release; real bytes are tested separately.
    monkeypatch.setattr(host, "verify_build_receipt", lambda *args: None)
    calls = []
    monkeypatch.setattr(host, "preflight", lambda *args: calls.append("catalog"))
    monkeypatch.setattr(host, "verify_binding", lambda *args: calls.append("binding"))
    monkeypatch.setattr(
        host, "verify_private_audit", lambda *args: calls.append("audit")
    )
    data = manifest()
    data["enabled"] = True
    key = ec.generate_private_key(ec.SECP256R1())
    data["public_key_sha256"] = hashlib.sha256(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()
    url = URL.create(
        "postgresql+psycopg",
        username=data["runtime_role"],
        database=data["database"],
        query={"host": data["socket_directory"], "port": "5432"},
    )
    oauth = create_engine(url)
    readonly = create_engine(url, execution_options={"postgresql_readonly": True})
    asynchronous = create_async_engine(url.set(drivername="postgresql+asyncpg"))
    resources = RuntimeResources(
        asynchronous,
        oauth,
        readonly,
        JWTSigner(data["key_id"], key),
        "synthetic-unit-test-session-key-only",
    )
    return SimpleNamespace(
        data=data,
        resources=resources,
        calls=calls,
        auth=auth_routes,
        dependencies=dependencies,
        audit=customer_success_audit,
    )


def create(a):
    return host.create_app(
        a.data,
        resources=a.resources,
        authorization=TargetAuthorization(digest(a.data), a.data["approval_ref"], True),
    )


def test_injected_technical_assembly_mounts_only_native_auth_and_private_case(assembly):
    a = assembly
    app = create(a)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {"/login", "/logout", "/panel", "/healthz", "/readyz"} <= paths
    assert not any(path.startswith("/admin") for path in paths)
    assert not any(
        path.startswith("/soporte/") and "/piloto" not in path for path in paths
    )
    assert a.calls == ["catalog", "catalog", "binding", "audit"]
    assert a.auth._db_session_maker is a.dependencies._db_session_maker
    assert a.auth._db_session_maker.kw["info"] == {
        "batisenal_preprovisioned_audit": a.data["schema"]
    }
    native_login = next(
        r.endpoint
        for r in a.auth.router.routes
        if r.path == "/login" and "GET" in r.methods
    )
    assert (
        next(
            r.endpoint
            for r in app.routes
            if getattr(r, "path", None) == "/login" and "GET" in r.methods
        )
        is native_login
    )
    assert create(a) is app
    a.data["project"] = "other"
    with pytest.raises(ConfigurationError, match="PROCESS_ALREADY_COMPOSED"):
        create(a)


def test_real_agent_assembly_uses_registration_hash_without_claiming_real_call(
    assembly,
):
    assembly.data["agent_kind"] = "ai_agent"
    app = create(assembly)
    assert (
        app.state.pilot.case_service.registration_sha256
        == assembly.data["registration_sha256"]
    )
    assert app.state.pilot.pilot.source_kind == "ai_agent"


def test_financial_import_or_configured_auth_rejected(assembly, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "copa_telmex_dashboard", object())
    with pytest.raises(ConfigurationError, match="FINANCIAL_PROCESS_DENIED"):
        create(assembly)
    monkeypatch.delitem(sys.modules, "copa_telmex_dashboard")
    monkeypatch.setattr(assembly.auth, "_db_session_maker", object())
    with pytest.raises(ConfigurationError, match="AUTH_PROCESS_ALREADY_CONFIGURED"):
        create(assembly)


def test_unfixed_canonical_audit_blocks_activation(assembly, monkeypatch):
    monkeypatch.setattr(assembly.audit, "PREPROVISIONED_SESSION_SUPPORTED", False)
    with pytest.raises(ConfigurationError, match="CANONICAL_AUTH_PREFLIGHT_REQUIRED"):
        create(assembly)
    assert assembly.auth._db_session_maker is None


@pytest.mark.parametrize("broken", ["identity", "cookie", "signer"])
def test_invalid_resources_fail_before_catalog_sql(assembly, broken):
    a = assembly
    r = a.resources
    if broken == "identity":
        a.resources = RuntimeResources(
            r.async_engine, r.oauth_engine, r.oauth_engine, r.signer, r.session_secret
        )
    elif broken == "cookie":
        a.resources = RuntimeResources(
            r.async_engine, r.oauth_engine, r.identity_engine, r.signer, "short"
        )
    else:
        a.data["public_key_sha256"] = "0" * 64
    with pytest.raises(ConfigurationError):
        create(a)
    assert a.calls == []


def test_native_route_contract_change_rejected(assembly, monkeypatch):
    monkeypatch.setattr(assembly.auth.router, "routes", [])
    with pytest.raises(ConfigurationError, match="NATIVE_AUTH_ROUTES_CHANGED"):
        create(assembly)
    assert assembly.auth._db_session_maker is None


def test_isolated_auth_diagnostics_remove_user_and_sql_values():
    import logging

    record = logging.LogRecord(
        "auth",
        logging.WARNING,
        "source",
        1,
        "Failed login %s",
        ("private@example.test",),
        (RuntimeError, RuntimeError("secret"), None),
    )
    assert host.PrivateAuthLog().filter(record)
    assert record.getMessage() == "Isolated pilot authentication event"
    assert record.exc_info is None and record.exc_text is None
