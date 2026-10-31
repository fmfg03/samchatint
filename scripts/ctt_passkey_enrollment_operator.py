"""Private operator CLI. Preview by default; never called from the web app.

The connection role must be independently registered as an enrollment operator.
Execute applies only the reviewed operation through its bounded SQL function;
identity accreditation and role provisioning remain external. Requests contain
exact opaque IDs/fingerprint/scope and explicit faculty dates.
"""

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID

import asyncpg


def request_file(path: Path) -> dict:
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result or type(value) is not str:
                raise ValueError("invalid operator request")
            result[name] = value
        return result

    with path.open("rb") as stream:
        raw = stream.read(65537)
    if not 2 <= len(raw) <= 65536:
        raise ValueError("invalid operator request")
    request = json.loads(raw, object_pairs_hook=pairs)
    if type(request) is not dict:
        raise ValueError("invalid operator request")
    required = {
        "invite": {
            "operation",
            "invitation_id",
            "account_id",
            "principal_id",
            "tenant_id",
            "edition_id",
            "actor_role",
            "user_handle_b64",
            "valid_from",
            "valid_until",
            "approval_ref",
        },
        "activate": {
            "operation",
            "enrollment_id",
            "fingerprint",
            "account_id",
            "principal_id",
            "tenant_id",
            "edition_id",
            "actor_role",
            "faculty",
            "valid_from",
            "valid_until",
            "approval_ref",
        },
        "revoke": {"operation", "credential_record_id", "principal_id", "reason_ref"},
    }
    if request.get("operation") not in required:
        raise ValueError("invalid operator request")
    actual = set(request)
    if request["operation"] == "invite" and "user_handle_b64" not in actual:
        actual.add("user_handle_b64")
    if actual != required[request["operation"]]:
        raise ValueError("invalid operator request")
    for field, value in request.items():
        if field.endswith("_id") and field != "tenant_id":
            if str(UUID(value)) != value:
                raise ValueError("invalid operator UUID")
        if field in {"fingerprint", "approval_ref", "reason_ref"} and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", value
        ):
            raise ValueError("invalid operator reference")
    if request.get("faculty", "capture_staff_v2") != "capture_staff_v2":
        raise ValueError("invalid operator faculty")
    if "valid_from" in request and _date(request["valid_until"]) <= _date(
        request["valid_from"]
    ):
        raise ValueError("invalid operator dates")
    if "user_handle_b64" in request:
        handle = base64.b64decode(request["user_handle_b64"], validate=True)
        if not 1 <= len(handle) <= 64:
            raise ValueError("invalid opaque handle")
    return request


def _date(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("operator dates require timezone")
    return result.astimezone(timezone.utc)


async def run(*, request, dsn, backend_pin, execute=False):
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            pin = await conn.fetchval(
                "SELECT ctt_staff_private.current_enrollment_operator()"
            )
            if pin != backend_pin:
                raise ValueError("operator backend mismatch")
            operation = request["operation"]
            preview = {
                key: value for key, value in request.items() if key != "user_handle_b64"
            }
            preview.update(
                backend_pin=backend_pin,
                executed=False,
                auth_profile="platform-passkey-v1",
            )
            if operation == "activate":
                candidate = await conn.fetchrow(
                    "SELECT * FROM ctt_staff_private.inspect_passkey_enrollment($1)",
                    UUID(request["enrollment_id"]),
                )
                if candidate is None or any(
                    str(candidate[key]) != request[key]
                    for key in [
                        "enrollment_id",
                        "account_id",
                        "principal_id",
                        "tenant_id",
                        "edition_id",
                        "actor_role",
                        "fingerprint",
                    ]
                ):
                    raise ValueError("operator candidate mismatch")
            if not execute:
                return preview
            if operation == "invite":
                handle = (
                    base64.b64decode(request["user_handle_b64"], validate=True)
                    if "user_handle_b64" in request
                    else None
                )
                if handle is not None and not 1 <= len(handle) <= 64:
                    raise ValueError("invalid opaque handle")
                result = await conn.fetchval(
                    "SELECT ctt_staff_private.admit_passkey_enrollment_invitation("
                    "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                    UUID(request["invitation_id"]),
                    UUID(request["account_id"]),
                    UUID(request["principal_id"]),
                    request["tenant_id"],
                    UUID(request["edition_id"]),
                    request["actor_role"],
                    handle,
                    _date(request["valid_from"]),
                    _date(request["valid_until"]),
                    request["approval_ref"],
                )
                preview["invitation_id"] = str(result)
            elif operation == "activate":
                result = await conn.fetchval(
                    "SELECT ctt_staff_private.activate_passkey_enrollment("
                    "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    UUID(request["enrollment_id"]),
                    request["fingerprint"],
                    UUID(request["account_id"]),
                    UUID(request["principal_id"]),
                    request["tenant_id"],
                    UUID(request["edition_id"]),
                    request["actor_role"],
                    request["faculty"],
                    _date(request["valid_from"]),
                    _date(request["valid_until"]),
                    request["approval_ref"],
                )
                preview["credential_record_id"] = str(result)
            else:
                await conn.execute(
                    "SELECT ctt_staff_private.revoke_enrolled_credential($1,$2,$3)",
                    UUID(request["credential_record_id"]),
                    UUID(request["principal_id"]),
                    request["reason_ref"],
                )
            preview["executed"] = True
        return preview
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", type=Path, required=True)
    parser.add_argument("--backend-pin", required=True)
    parser.add_argument("--dsn-env", default="CTT_ENROLLMENT_OPERATOR_DSN")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        request = request_file(args.request_json)
        dsn = os.environ.get(args.dsn_env)
        if not dsn:
            raise ValueError("operator connection not configured")
        result = asyncio.run(
            run(
                request=request,
                dsn=dsn,
                backend_pin=args.backend_pin,
                execute=args.execute,
            )
        )
        result["request_sha256"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        print(json.dumps(result, sort_keys=True))
    except Exception:
        parser.exit(
            1,
            "Enrollment operator request denied; "
            "no credentials or diagnostics displayed.\n",
        )


if __name__ == "__main__":
    main()
