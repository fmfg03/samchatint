"""Isolated WebAuthn verification for a proposed staff-review challenge.

This module neither authenticates a caller nor issues business authority. A
future integration must protect challenge and credential state, independently
verify reviewer identity and faculty, and provide a transactional single-use
consumer. The returned result explicitly cannot authorize materialization.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, NoReturn
from uuid import UUID

from fido2.server import Fido2Server
from fido2.utils import websafe_encode
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    PublicKeyCredentialRpEntity,
    UserVerificationRequirement,
)

RP_ID = "sam.chat"
ORIGIN = "https://sam.chat"
MAX_LIFETIME_SECONDS = 300
# Bound each assertion. The HTTP boundary must reject oversized bytes before
# JSON parsing, because this helper receives an already parsed object.
MAX_RESPONSE_BYTES = 65536
MAX_CLIENT_DATA_BYTES = 4096
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 1024
DEVICE_COUNTER_PROFILE = "device-counter-v1"
PLATFORM_PASSKEY_PROFILE = "platform-passkey-v1"
AUTH_PROFILES = frozenset((DEVICE_COUNTER_PROFILE, PLATFORM_PASSKEY_PROFILE))
# Hash of the approved platform profile, with a separate canonical domain.
# This is not taken from an assertion or selected by the browser.
AUTH_POLICY_HASH = (
    "sha256:"
    + hashlib.sha256(
        b"samchat.ctt.auth-policy.v1\x00"
        + json.dumps(
            {
                "backup_eligibility": "pinned",
                "backup_state": "signed-change-audited",
                "challenge": "32-byte-single-use-300s-max",
                "counter": "zero-pair-or-strict-increase",
                "id": PLATFORM_PASSKEY_PROFILE,
                "user_verification": "required",
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
)
_SCOPE = re.compile(r"^sha256:[a-f0-9]{64}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


class ReviewAssertionDenied(ValueError):
    """Review challenge or WebAuthn assertion is not acceptable."""


def _deny() -> NoReturn:
    raise ReviewAssertionDenied("staff review assertion denied") from None


def _reject_duplicate_client_json(raw: bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate client data member")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-standard client data constant")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError("invalid client data")
    return result


def _bounded_json(value: Any, *, max_bytes: int) -> None:
    """Limit an already-parsed JSON value before the library traverses it."""
    stack = [(value, 0)]
    nodes = 0
    size = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _deny()
        if type(item) is dict:
            if nodes + len(stack) + len(item) > MAX_JSON_NODES:
                _deny()
            for key, child in item.items():
                if type(key) is not str or len(key) > max_bytes:
                    _deny()
                size += len(key.encode("utf-8", "strict")) + 4
                if size > max_bytes:
                    _deny()
                stack.append((child, depth + 1))
        elif type(item) is list:
            if nodes + len(stack) + len(item) > MAX_JSON_NODES:
                _deny()
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > max_bytes:
                _deny()
            size += len(item.encode("utf-8", "strict")) + 2
        elif item is None or type(item) is bool:
            size += 5
        elif type(item) is int and item.bit_length() <= 64:
            size += 24
        else:
            _deny()
        if size > max_bytes:
            _deny()


def _limit_json_text_depth(raw: bytes) -> None:
    """Reject excessive nesting before the JSON decoder traverses the input."""
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > MAX_JSON_DEPTH + 1:
                _deny()
        elif byte in (0x7D, 0x5D):
            depth -= 1
            if depth < 0:
                _deny()
    if in_string or depth != 0:
        _deny()


def _preflight_response(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bound and validate client JSON before the WebAuthn library parses it."""
    _bounded_json(response, max_bytes=MAX_RESPONSE_BYTES)
    inner = response.get("response")
    if type(inner) is not dict:
        _deny()
    encoded = inner.get("clientDataJSON")
    if (
        type(encoded) is not str
        or len(encoded) > ((MAX_CLIENT_DATA_BYTES + 2) // 3) * 4
        or not _BASE64URL.fullmatch(encoded)
    ):
        _deny()
    raw = base64.b64decode(
        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
    )
    if len(raw) > MAX_CLIENT_DATA_BYTES:
        _deny()
    _limit_json_text_depth(raw)
    client = _reject_duplicate_client_json(raw)
    _bounded_json(client, max_bytes=MAX_CLIENT_DATA_BYTES)
    return client


def _server() -> Fido2Server:
    return Fido2Server(
        PublicKeyCredentialRpEntity(id=RP_ID, name="SamChat"),
        verify_origin=lambda origin: origin == ORIGIN,
    )


def _valid_uuid(value: str) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


@dataclass(frozen=True, repr=False)
class ReviewChallenge:
    """Store state in a trusted backend; never accept it from a browser."""

    challenge_id: str
    principal_id: str
    scope_hash: str
    challenge: bytes
    issued_at: int
    expires_at: int
    credential: bytes
    user_handle: bytes
    counter: int
    auth_profile: str = DEVICE_COUNTER_PROFILE
    backup_eligible: bool = False


@dataclass(frozen=True, repr=False)
class ChallengeConsumption:
    """Values that a trusted store must check and consume atomically."""

    challenge_id: str
    principal_id: str
    scope_hash: str
    credential_id: bytes
    previous_counter: int
    new_counter: int
    expires_at: int
    auth_profile: str = DEVICE_COUNTER_PROFILE
    backup_eligible: bool = False
    backup_state: bool = False
    auth_policy_hash: str | None = None


def _validate_state(state: ReviewChallenge) -> AttestedCredentialData:
    if (
        not isinstance(state, ReviewChallenge)
        or not _valid_uuid(state.challenge_id)
        or not _valid_uuid(state.principal_id)
        or not isinstance(state.scope_hash, str)
        or not _SCOPE.fullmatch(state.scope_hash)
        or not isinstance(state.challenge, bytes)
        or len(state.challenge) != 32
        or type(state.issued_at) is not int
        or type(state.expires_at) is not int
        or state.issued_at < 0
        or not 0 < state.expires_at - state.issued_at <= MAX_LIFETIME_SECONDS
        or type(state.counter) is not int
        or not 0 <= state.counter <= 0xFFFFFFFF
        or type(state.auth_profile) is not str
        or state.auth_profile not in AUTH_PROFILES
        or type(state.backup_eligible) is not bool
        or (state.auth_profile == DEVICE_COUNTER_PROFILE and state.backup_eligible)
        or not isinstance(state.user_handle, bytes)
        or not 1 <= len(state.user_handle) <= 64
        or not isinstance(state.credential, bytes)
        or not 1 <= len(state.credential) <= 16384
    ):
        _deny()
    try:
        credential = AttestedCredentialData(state.credential)
        if not credential.credential_id:
            _deny()
        return credential
    except Exception:
        _deny()


def prepare_challenge(
    *,
    challenge_id: str,
    principal_id: str,
    scope_hash: str,
    challenge: bytes,
    credential: bytes,
    user_handle: bytes,
    counter: int,
    issued_at: int,
    expires_at: int,
    auth_profile: str = DEVICE_COUNTER_PROFILE,
    backup_eligible: bool = False,
) -> tuple[ReviewChallenge, Mapping[str, Any]]:
    """Create options from trusted challenge and enrolled credential state.

    The profile and BE pin come from the protected enrollment registry, never
    from the assertion request. This helper performs no enrollment itself.
    """
    # Upstream logs credential IDs at DEBUG during challenge preparation.
    if logging.getLogger("fido2.server").isEnabledFor(logging.INFO):
        _deny()
    state = ReviewChallenge(
        challenge_id=challenge_id,
        principal_id=principal_id,
        scope_hash=scope_hash,
        challenge=challenge,
        issued_at=issued_at,
        expires_at=expires_at,
        credential=credential,
        user_handle=user_handle,
        counter=counter,
        auth_profile=auth_profile,
        backup_eligible=backup_eligible,
    )
    public_credential = _validate_state(state)
    options, _ = _server().authenticate_begin(
        [public_credential],
        user_verification=UserVerificationRequirement.REQUIRED,
        challenge=state.challenge,
    )
    return state, dict(options)


def _validate_review_assertion(
    *,
    state: ReviewChallenge,
    expected_principal_id: str,
    expected_scope_hash: str,
    response: Mapping[str, Any],
    now: int,
) -> ChallengeConsumption:
    """Verify cryptographic and scope checks before consumption."""
    credential = _validate_state(state)
    if (
        not _valid_uuid(expected_principal_id)
        or expected_principal_id != state.principal_id
        or not isinstance(expected_scope_hash, str)
        or not _SCOPE.fullmatch(expected_scope_hash)
        or not hmac.compare_digest(expected_scope_hash, state.scope_hash)
        or type(now) is not int
        or not state.issued_at <= now < state.expires_at
        or not isinstance(response, Mapping)
        # Upstream fido2.server logs credential IDs at INFO on success.
        # The integration must configure this logger before any assertion.
        or logging.getLogger("fido2.server").isEnabledFor(logging.INFO)
    ):
        _deny()
    try:
        client_json = _preflight_response(response)
        parsed = AuthenticationResponse.from_dict(response)
        client = parsed.response.client_data
        auth = parsed.response.authenticator_data
        # No cross-origin ceremony is authorized for this flow.
        if client.cross_origin is not False or "topOrigin" in client_json:
            _deny()
        if parsed.response.user_handle is not None and not hmac.compare_digest(
            parsed.response.user_handle, state.user_handle
        ):
            _deny()
        _server().authenticate_complete(
            {
                "challenge": websafe_encode(state.challenge),
                "user_verification": UserVerificationRequirement.REQUIRED,
            },
            [credential],
            parsed,
        )
        # Flags and counters below have now been authenticated by the library.
        # Zero is accepted only for an explicitly enrolled platform profile
        # when both stored and signed values are zero. Challenge consumption
        # remains mandatory; a counter cannot provide single-use semantics.
        counterless_platform = (
            state.auth_profile == PLATFORM_PASSKEY_PROFILE
            and state.counter == 0
            and auth.counter == 0
        )
        if not counterless_platform and auth.counter <= state.counter:
            _deny()
        backup_eligible = auth.is_backup_eligible()
        backup_state = auth.is_backed_up()
        if backup_eligible != state.backup_eligible or (
            backup_state and not backup_eligible
        ):
            _deny()
        return ChallengeConsumption(
            challenge_id=state.challenge_id,
            principal_id=state.principal_id,
            scope_hash=state.scope_hash,
            credential_id=credential.credential_id,
            previous_counter=state.counter,
            new_counter=auth.counter,
            expires_at=state.expires_at,
            auth_profile=state.auth_profile,
            backup_eligible=backup_eligible,
            backup_state=backup_state,
            auth_policy_hash=(
                AUTH_POLICY_HASH
                if state.auth_profile == PLATFORM_PASSKEY_PROFILE
                else None
            ),
        )
    except Exception:
        _deny()


def _non_authorizing_result() -> Mapping[str, Any]:
    return {
        "assertion_verified": True,
        "consumption_reported_by_caller": True,
        "durable_consumption_unproven": True,
        "actor_revalidation_required": True,
        "materialization_authorized": False,
    }


def verify_review_assertion(
    *,
    state: ReviewChallenge,
    expected_principal_id: str,
    expected_scope_hash: str,
    response: Mapping[str, Any],
    now: int,
    consume_once: Callable[[ChallengeConsumption], bool],
) -> Mapping[str, Any]:
    """Verify proof and request synchronous, trusted atomic consumption.

    The consumer must atomically check the current scope, reviewer, faculty,
    revocation, expiry and counter and consume the challenge. This helper
    cannot establish those guarantees and does not grant materialization.
    """
    if not callable(consume_once):
        _deny()
    consumption = _validate_review_assertion(
        state=state,
        expected_principal_id=expected_principal_id,
        expected_scope_hash=expected_scope_hash,
        response=response,
        now=now,
    )
    try:
        if consume_once(consumption) is not True:
            _deny()
    except Exception:
        _deny()
    return _non_authorizing_result()


async def verify_review_assertion_async(
    *,
    state: ReviewChallenge,
    expected_principal_id: str,
    expected_scope_hash: str,
    response: Mapping[str, Any],
    now: int,
    consume_once: Callable[[ChallengeConsumption], Awaitable[bool]],
) -> Mapping[str, Any]:
    """Verify proof and await trusted atomic consumption without authority.

    As in the synchronous variant, transaction ownership and all independent
    identity, faculty, expiry and revocation checks belong to the caller.
    A cancellation or failure must roll back the caller-owned transaction.
    """
    if not callable(consume_once):
        _deny()
    consumption = _validate_review_assertion(
        state=state,
        expected_principal_id=expected_principal_id,
        expected_scope_hash=expected_scope_hash,
        response=response,
        now=now,
    )
    try:
        if await consume_once(consumption) is not True:
            _deny()
    except Exception:
        _deny()
    return _non_authorizing_result()
