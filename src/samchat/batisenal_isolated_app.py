"""Inert FastAPI factory for the separately approved synthetic installation.

Importing this module never loads the financial application or opens a database.
An enabled instance requires explicit resources and an exact owner authorization.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute

from samchat.batisenal_composition import (
    PREFIX,
    ConfigurationError,
    RuntimeResources,
    TargetAuthorization,
    digest,
    preflight,
    validate_engine_url,
    validate_manifest,
    verify_binding,
    verify_build_receipt,
    verify_private_audit,
)

_lock = threading.Lock()
_composition: tuple[str, FastAPI] | None = None


def _private_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class PrivateAuthLog(logging.Filter):
    """Keep isolated auth diagnostics without emails, request text or SQL values."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "Isolated pilot authentication event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def create_app(
    manifest: dict[str, Any] | None = None,
    *,
    resources: RuntimeResources | None = None,
    authorization: TargetAuthorization | None = None,
) -> FastAPI:
    """Create a disabled app or one exact, preflighted composition per process."""
    data = validate_manifest(manifest) if manifest is not None else None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pilot = None

    @app.get("/healthz")
    async def health():
        return JSONResponse({"status": "ok"}, headers=_private_headers())

    @app.get("/readyz")
    async def ready():
        active = bool(app.state.pilot is not None and app.state.pilot.enabled is True)
        return JSONResponse(
            {
                "status": "ready" if active else "disabled",
                "chatty_acceptance": "not_verified",
            },
            status_code=200 if active else 503,
            headers=_private_headers(),
        )

    if data is None or data["enabled"] is False:
        return app
    if resources is None or authorization is None:
        raise ConfigurationError("EXPLICIT_RESOURCES_REQUIRED")
    authorization.validate(data)
    verify_build_receipt(
        Path(__file__).resolve().parents[2], data, resources.build_receipt
    )
    if "copa_telmex_dashboard" in sys.modules:
        raise ConfigurationError("FINANCIAL_PROCESS_DENIED")
    fingerprint = digest(data)
    with _lock:
        global _composition
        if _composition is not None:
            if _composition[0] != fingerprint:
                raise ConfigurationError("PROCESS_ALREADY_COMPOSED")
            return _composition[1]
        for engine in (
            resources.async_engine,
            resources.oauth_engine,
            resources.identity_engine,
        ):
            validate_engine_url(engine, data)
        if (
            resources.identity_engine is resources.oauth_engine
            or resources.identity_engine.get_execution_options().get(
                "postgresql_readonly"
            )
            is not True
        ):
            raise ConfigurationError("READONLY_IDENTITY_ENGINE_REQUIRED")
        if (
            not isinstance(resources.session_secret, str)
            or len(resources.session_secret) < 32
        ):
            raise ConfigurationError("ISOLATED_COOKIE_SECRET_REQUIRED")
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        public_key = resources.signer.private_key.public_key()
        public_bytes = public_key.public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        if (
            hashlib.sha256(public_bytes).hexdigest() != data["public_key_sha256"]
            or resources.signer.key_id != data["key_id"]
        ):
            raise ConfigurationError("SIGNER_FINGERPRINT_MISMATCH")
        # Catalog queries only. Domain engines are not used until their hooks exist.
        preflight(resources.oauth_engine, data)
        preflight(resources.identity_engine, data)
        from devnous.gastos.routes import auth_routes, dependencies
        from devnous.gastos.services import customer_success_audit

        # Canonical login currently performs ensure-DDL. An explicit owner feature
        # is required before this minimal-role host can safely activate it.
        if (
            getattr(customer_success_audit, "PREPROVISIONED_SESSION_SUPPORTED", False)
            is not True
        ):
            raise ConfigurationError("CANONICAL_AUTH_PREFLIGHT_REQUIRED")
        if (
            auth_routes._db_session_maker is not None
            or dependencies._db_session_maker is not None
        ):
            raise ConfigurationError("AUTH_PROCESS_ALREADY_CONFIGURED")
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from starlette.middleware.sessions import SessionMiddleware

        from devnous.gastos.routes.batisenal_pilot_routes import mount_pilot
        from devnous.gastos.services.batisenal_case_service import CaseService
        from devnous.gastos.services.batisenal_pilot_service import Pilot
        from samchat.batisenal_pilot import (
            SUPPORT_SCOPE,
            SupportAuthorizationConfig,
            SupportPilot,
        )
        from samchat.batisenal_synthetic_protocol import SyntheticProtocol
        from samchat.batisenal_transport.store import Store
        from samchat.private_plugin.oauth_identity import (
            OAuthResourceConfig,
            VerificationKey,
        )
        from samchat.private_plugin.oauth_store import OAuthStore

        protocol = SyntheticProtocol(data["schema"], data["case_id"], enabled=True)
        protocol.install(resources.async_engine.sync_engine, synchronous=False)
        protocol.install(resources.oauth_engine, synchronous=True)
        verify_binding(resources.oauth_engine, data)
        verify_private_audit(data["audit_path"])
        sessions = async_sessionmaker(
            resources.async_engine,
            expire_on_commit=False,
            info={"batisenal_preprovisioned_audit": data["schema"]},
        )
        issuer = data["origin"] + PREFIX
        auth = SupportAuthorizationConfig(
            issuer,
            issuer + "/mcp",
            data["client_id"],
            data["redirect_uri"],
            SUPPORT_SCOPE,
        )
        resource = OAuthResourceConfig(
            issuer,
            auth.resource,
            data["installation_id"],
            SUPPORT_SCOPE,
            frozenset({data["client_id"]}),
            {data["key_id"]: VerificationKey(resources.signer.algorithm, public_key)},
        )
        store_kwargs = dict(
            issuer=issuer,
            installation_id=data["installation_id"],
            organization_id=data["organization"],
            support_only=True,
        )

        def clock():
            return int(time.time())

        pilot = SupportPilot(
            sessions=sessions,
            employee_dependency=dependencies.get_current_empleado,
            authorization=auth,
            resource=resource,
            oauth_store=OAuthStore(resources.oauth_engine, **store_kwargs),
            identity_records_store=OAuthStore(
                resources.identity_engine, **store_kwargs
            ),
            signer=resources.signer,
            audit=Store(data["audit_path"], clock),
            pilot=Pilot(
                case_id=data["case_id"],
                organization=data["organization"],
                project=data["project"],
                issuer=issuer,
                installation_id=data["installation_id"],
                clock=clock,
                source_kind=data["agent_kind"],
            ),
            case_service=CaseService(
                sessions=sessions,
                issuer=issuer,
                audience=auth.resource,
                client_id=data["client_id"],
                registration_sha256=(
                    data["registration_sha256"]
                    if data["agent_kind"] == "ai_agent"
                    else None
                ),
                enabled=True,
            ),
            clock=clock,
            enabled=True,
        )
        app.add_middleware(
            SessionMiddleware,
            secret_key=resources.session_secret,
            session_cookie=data["cookie_name"],
            https_only=True,
            same_site="lax",
            max_age=3600,
        )
        native_routes = [
            r
            for r in auth_routes.router.routes
            if isinstance(r, APIRoute) and r.path in {"/login", "/logout"}
        ]
        if {(r.path, m) for r in native_routes for m in (r.methods or set())} != {
            ("/login", "GET"),
            ("/login", "POST"),
            ("/logout", "GET"),
        }:
            raise ConfigurationError("NATIVE_AUTH_ROUTES_CHANGED")
        app.router.routes.extend(native_routes)
        if auth_routes.SLOWAPI_AVAILABLE:
            app.state.limiter = auth_routes.limiter

            def rate_limit(request: Request, exc: Exception):
                if not isinstance(exc, auth_routes.RateLimitExceeded):
                    raise exc
                return auth_routes._rate_limit_exceeded_handler(request, exc)

            app.add_exception_handler(auth_routes.RateLimitExceeded, rate_limit)
        mount_pilot(app, pilot)

        @app.get("/panel")
        async def panel():
            return RedirectResponse(
                PREFIX + "/case", status_code=303, headers=_private_headers()
            )

        auth_routes.logger.addFilter(PrivateAuthLog())
        dependencies.logger.addFilter(PrivateAuthLog())
        customer_success_audit.logger.addFilter(PrivateAuthLog())
        auth_routes.set_db_session_maker(sessions)
        dependencies.set_db_session_maker(sessions)
        app.state.pilot = pilot
        app.state.manifest_sha256 = fingerprint
        _composition = (fingerprint, app)
        return app
