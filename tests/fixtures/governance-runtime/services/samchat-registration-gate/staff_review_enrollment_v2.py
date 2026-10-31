"""Supervised platform-passkey enrollment; browser completion creates no faculty.

Service/operator identities and their SQL grants are configured independently.
No credential is inserted into the live registry until the separate operator
activates the exact candidate and fingerprint with explicit scope and validity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import struct
import time
from typing import Any, NoReturn
from uuid import UUID, uuid4

from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestationConveyancePreference,
    AttestedCredentialData,
    AuthenticatorAttachment,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialUserEntity,
    RegistrationResponse,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from staff_review_issuer_v2 import _check_connection, _response
from staff_review_webauthn_v2 import (
    AUTH_POLICY_HASH,
    ORIGIN,
    PLATFORM_PASSKEY_PROFILE,
    RP_ID,
    _preflight_response,
)


class PasskeyEnrollmentDenied(ValueError):
    """A generic enrollment failure; never echo client credential data."""


def _deny() -> NoReturn:
    raise PasskeyEnrollmentDenied("passkey enrollment denied") from None


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def credential_fingerprint(credential_id: bytes, attested: bytes) -> str:
    if (
        type(credential_id) is not bytes
        or not 1 <= len(credential_id) <= 1024
        or type(attested) is not bytes
        or not 1 <= len(attested) <= 16384
    ):
        _deny()
    framed = (
        b"samchat.ctt.enrollment-key.v1\0"
        + struct.pack(">I", len(credential_id))
        + credential_id
        + attested
    )
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _verify_registration(*, server, context, registration, now):
    """Library checks plus profile and bounds which register_complete omits."""
    try:
        if logging.getLogger("fido2.server").isEnabledFor(logging.INFO):
            _deny()
        if (
            type(now) is not int
            or not context["issued_at_seconds"] <= now < context["expires_at_seconds"]
        ):
            _deny()
        raw = _canonical(registration) if type(registration) is dict else registration
        obj = _response(raw)
        if obj.get("type") != "public-key":
            _deny()
        client = _preflight_response(obj)
        if client.get("crossOrigin", False) is not False or "topOrigin" in client:
            _deny()
        parsed = RegistrationResponse.from_dict(obj)
        if parsed.authenticator_attachment not in (
            None,
            AuthenticatorAttachment.PLATFORM,
        ):
            _deny()
        _, state = server.register_begin(
            PublicKeyCredentialUserEntity(
                id=context["user_handle"],
                name="reviewer-" + str(context["principal_id"]),
            ),
            challenge=context["challenge_bytes"],
            user_verification=UserVerificationRequirement.REQUIRED,
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key_requirement=ResidentKeyRequirement.PREFERRED,
        )
        auth = server.register_complete(state, parsed)
        credential = auth.credential_data
        if credential is None or credential.credential_id != parsed.raw_id:
            _deny()
        if type(credential.public_key.get(3)) is not int or credential.public_key.get(
            3
        ) not in {item.alg for item in server.allowed_algorithms}:
            _deny()
        be, bs = auth.is_backup_eligible(), auth.is_backed_up()
        if bs and not be:
            _deny()
        attested = bytes(credential)
        return (
            raw,
            credential.credential_id,
            attested,
            auth.counter,
            be,
            bs,
            credential_fingerprint(credential.credential_id, attested),
        )
    except Exception:
        _deny()


class PasskeyEnrollmentService:
    """An internal factory product, never configured from an HTTP request."""

    def __init__(
        self,
        pool,
        *,
        backend_pin,
        clock=time.time,
        timeout=300,
        rp_id=RP_ID,
        origin=ORIGIN,
    ):
        if (
            type(backend_pin) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", backend_pin)
            or not callable(clock)
            or type(timeout) is not int
            or not 1 <= timeout <= 300
        ):
            raise ValueError("invalid enrollment configuration")
        # Profile policy pins this relying party. Test clients use synthetic
        # responses for this exact origin; no client may choose another RP.
        if rp_id != RP_ID or origin != ORIGIN:
            raise ValueError("invalid enrollment relying party")
        self._pool, self._pin, self._clock, self._timeout = (
            pool,
            backend_pin,
            clock,
            timeout,
        )
        self._server = Fido2Server(
            PublicKeyCredentialRpEntity(id=rp_id, name="SamChat"),
            attestation=AttestationConveyancePreference.NONE,
            verify_origin=lambda value: value == origin,
        )
        # fido2 2.2.1 initializes this documented integer-or-None option as
        # None without an annotation.
        self._server.timeout = timeout * 1000  # type: ignore[assignment]

    async def _connection(self, conn):
        await _check_connection(conn)
        pin = await conn.fetchval("SELECT ctt_staff_private.current_review_service()")
        if pin != self._pin:
            _deny()

    @staticmethod
    def _public(context) -> dict[str, Any]:
        if context is None:
            _deny()
        return {
            "enrollment_id": str(context["enrollment_id"]),
            "tenant_id": context["tenant_id"],
            "edition_id": str(context["edition_id"]),
            "status": context["status"],
            "fingerprint": context["fingerprint"],
            "auth_profile": PLATFORM_PASSKEY_PROFILE,
            "auth_policy_hash": AUTH_POLICY_HASH,
            "grants_faculty": False,
        }

    async def begin(self, account_id: UUID, tenant_id: str, edition_id: UUID) -> dict:
        try:
            if (
                not isinstance(account_id, UUID)
                or not isinstance(edition_id, UUID)
                or type(tenant_id) is not str
            ):
                _deny()
            if logging.getLogger("fido2.server").isEnabledFor(logging.INFO):
                _deny()
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    _deny()
                async with conn.transaction():
                    await self._connection(conn)
                    context = await conn.fetchrow(
                        "SELECT * FROM ctt_staff_private."
                        "begin_passkey_enrollment($1,$2,$3,$4,$5,$6)",
                        account_id,
                        tenant_id,
                        edition_id,
                        uuid4(),
                        secrets.token_bytes(32),
                        self._timeout,
                    )
                    result = self._public(context)
                    if context["status"] == "AWAITING_REGISTRATION":
                        credentials = [
                            AttestedCredentialData(value)
                            for value in context["existing_credentials"]
                        ]
                        options, _ = self._server.register_begin(
                            PublicKeyCredentialUserEntity(
                                id=context["user_handle"],
                                name="reviewer-" + str(context["principal_id"]),
                                display_name="Revisor Copa Telmex",
                            ),
                            credentials=credentials,
                            resident_key_requirement=ResidentKeyRequirement.PREFERRED,
                            user_verification=UserVerificationRequirement.REQUIRED,
                            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                            challenge=context["challenge_bytes"],
                        )
                        result["options"] = dict(options)
            return result
        except Exception:
            _deny()

    async def complete(
        self, account_id: UUID, enrollment_id: UUID, registration: bytes | dict
    ) -> dict:
        try:
            if not isinstance(account_id, UUID) or not isinstance(enrollment_id, UUID):
                _deny()
            if type(registration) is dict:
                raw = _canonical(registration)
            elif isinstance(registration, bytes):
                raw = registration
            else:
                _deny()
            # Bound outer JSON and preserve clientDataJSON byte contents.
            raw = _canonical(_response(raw))
            response_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    _deny()
                async with conn.transaction():
                    await self._connection(conn)
                    context = await conn.fetchrow(
                        "SELECT * FROM ctt_staff_private."
                        "load_passkey_enrollment($1,$2)",
                        account_id,
                        enrollment_id,
                    )
                    if context is None:
                        _deny()
                    if context["response_hash"] is not None:
                        if context["response_hash"] != response_hash:
                            _deny()
                        return self._public(context)
                    _, credential, attested, counter, be, bs, fingerprint = (
                        _verify_registration(
                            server=self._server,
                            context=context,
                            registration=raw,
                            now=int(self._clock()),
                        )
                    )
                    await conn.fetchval(
                        "SELECT ctt_staff_private.record_passkey_enrollment_candidate("
                        "$1,$2,$3,$4,$5,$6,$7,$8,$9)",
                        account_id,
                        enrollment_id,
                        response_hash,
                        credential,
                        attested,
                        counter,
                        be,
                        bs,
                        fingerprint,
                    )
                    context = await conn.fetchrow(
                        "SELECT * FROM ctt_staff_private."
                        "load_passkey_enrollment($1,$2)",
                        account_id,
                        enrollment_id,
                    )
                    result = self._public(context)
            return result
        except Exception:
            _deny()

    async def status(self, account_id: UUID, enrollment_id: UUID) -> dict:
        try:
            if not isinstance(account_id, UUID) or not isinstance(enrollment_id, UUID):
                _deny()
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    _deny()
                async with conn.transaction():
                    await self._connection(conn)
                    context = await conn.fetchrow(
                        "SELECT * FROM ctt_staff_private."
                        "load_passkey_enrollment($1,$2)",
                        account_id,
                        enrollment_id,
                    )
                    result = self._public(context)
            return result
        except Exception:
            _deny()
