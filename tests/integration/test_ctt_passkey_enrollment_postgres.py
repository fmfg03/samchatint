"""Real registration/candidate/operator boundaries on disposable PostgreSQL."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path
import secrets
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_staff_security_postgres import isolated_pg as _isolated_pg
from test_ctt_staff_review_state_postgres import connect

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
SERVICE = "ctt_enrollment_test_service"
OPERATOR = "ctt_enrollment_test_operator"
PIN = "isolated-enrollment"
SHA = "sha256:" + "7" * 64


@pytest.fixture
def enrollment_pg(monkeypatch):
    fixture = _isolated_pg.__wrapped__()
    socket = next(fixture)
    logger = logging.getLogger("fido2.server")
    old_level = logger.level
    logger.setLevel(logging.WARNING)

    async def setup():
        conn = await connect(socket)
        try:
            for name in [
                "20261028_ctt_staff_review_state.sql",
                "20261030_ctt_platform_passkey.sql",
                "20261031_ctt_passkey_enrollment.sql",
            ]:
                await conn.execute((MIGRATIONS / name).read_text())
        finally:
            await conn.close()

    try:
        asyncio.run(setup())
        yield socket
    finally:
        fixture.close()
        logger.setLevel(old_level)


async def configure(socket):
    admin = await connect(socket)
    try:
        for role in (SERVICE, OPERATOR):
            await admin.execute(f"CREATE ROLE {role} LOGIN")
            await admin.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {role}")
        for signature in [
            "current_review_service()",
            "begin_passkey_enrollment(uuid,text,uuid,uuid,bytea,integer)",
            "load_passkey_enrollment(uuid,uuid)",
            "record_passkey_enrollment_candidate(uuid,uuid,text,"
            "bytea,bytea,bigint,boolean,boolean,text)",
        ]:
            await admin.execute(
                f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} TO {SERVICE}"
            )
        for signature in [
            "current_enrollment_operator()",
            "admit_passkey_enrollment_invitation(uuid,uuid,uuid,text,"
            "uuid,text,bytea,timestamptz,timestamptz,text)",
            "activate_passkey_enrollment(uuid,text,uuid,uuid,text,"
            "uuid,text,text,timestamptz,timestamptz,text)",
            "revoke_enrolled_credential(uuid,uuid,text)",
            "inspect_passkey_enrollment(uuid)",
        ]:
            await admin.execute(
                f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} TO {OPERATOR}"
            )
        for table, role in [
            ("review_services", SERVICE),
            ("review_enrollment_operators", OPERATOR),
        ]:
            await admin.execute(
                f"INSERT INTO ctt_staff_private.{table}"
                "(service_role,backend_pin,not_before,not_after,active) "
                "VALUES($1,$2,clock_timestamp()-interval '1 minute',"
                "clock_timestamp()+interval '1 hour',true)",
                role,
                PIN,
            )
    finally:
        await admin.close()
    operator = await connect(socket, OPERATOR)
    account, principal, edition = uuid4(), uuid4(), uuid4()
    starts = datetime.now(timezone.utc) - timedelta(minutes=1)
    ends = starts + timedelta(hours=1)
    try:
        await operator.fetchval(
            "SELECT ctt_staff_private.admit_passkey_enrollment_invitation("
            "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            uuid4(),
            account,
            principal,
            "synthetic",
            edition,
            "coordinador",
            secrets.token_bytes(32),
            starts,
            ends,
            SHA,
        )
    finally:
        await operator.close()
    pool = await asyncpg.create_pool(
        host=socket,
        port=55492,
        user=SERVICE,
        database="postgres",
        min_size=1,
        max_size=4,
    )
    from staff_review_enrollment_v2 import PasskeyEnrollmentService

    return (
        pool,
        PasskeyEnrollmentService(pool, backend_pin=PIN),
        account,
        principal,
        edition,
        starts,
        ends,
    )


def registration(
    options,
    *,
    uv=True,
    be=True,
    bs=True,
    origin="https://sam.chat",
    rp="sam.chat",
    cross=False,
    counter=0,
):
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.cose import ES256
    from fido2.utils import websafe_decode, websafe_encode
    from fido2.webauthn import (
        Aaguid,
        AttestationObject,
        AttestedCredentialData,
        AuthenticatorData,
        CollectedClientData,
    )

    credential = AttestedCredentialData.create(
        Aaguid.NONE,
        secrets.token_bytes(32),
        ES256.from_cryptography_key(
            ec.generate_private_key(ec.SECP256R1()).public_key()
        ),
    )
    flags = AuthenticatorData.FLAG.AT | AuthenticatorData.FLAG.UP
    if uv:
        flags |= AuthenticatorData.FLAG.UV
    if be:
        flags |= AuthenticatorData.FLAG.BE
    if bs:
        flags |= AuthenticatorData.FLAG.BS
    auth = AuthenticatorData.create(
        hashlib.sha256(rp.encode()).digest(), flags, counter, credential
    )
    client = CollectedClientData.create(
        "webauthn.create",
        websafe_decode(options["publicKey"]["challenge"]),
        origin,
        cross_origin=cross,
    )
    return {
        "id": websafe_encode(credential.credential_id),
        "rawId": websafe_encode(credential.credential_id),
        "type": "public-key",
        "authenticatorAttachment": "platform",
        "response": {
            "clientDataJSON": websafe_encode(client),
            "attestationObject": websafe_encode(
                AttestationObject.create("none", auth, {})
            ),
        },
    }


def test_empty_enrollment_migration_is_reversible(enrollment_pg):
    async def run():
        conn = await connect(enrollment_pg)
        try:
            await conn.execute(
                (
                    MIGRATIONS / "20261031_ctt_passkey_enrollment.rollback.sql"
                ).read_text()
            )
            assert await conn.fetchval(
                "SELECT to_regprocedure("
                "'ctt_staff_private.current_review_service()') IS NULL"
            )
            await conn.execute(
                (MIGRATIONS / "20261031_ctt_passkey_enrollment.sql").read_text()
            )
        finally:
            await conn.close()

    asyncio.run(run())


def test_real_registration_stays_candidate_until_explicit_operator_activation(
    enrollment_pg,
):
    async def run():
        pool, service, account, principal, edition, starts, ends = await configure(
            enrollment_pg
        )
        try:
            begun = await service.begin(account, "synthetic", edition)
            assert await service.begin(account, "synthetic", edition) == begun
            from uuid import UUID

            enrollment = UUID(begun["enrollment_id"])
            response = registration(begun["options"])
            result = await service.complete(account, enrollment, response)
            assert result["status"] == "PENDING_OPERATOR_REVIEW"
            assert result["grants_faculty"] is False
            assert await service.complete(account, enrollment, response) == result
            assert await service.begin(account, "synthetic", edition) == result
            admin = await connect(enrollment_pg)
            try:
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.review_credentials"
                    )
                    == 0
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.review_faculties"
                    )
                    == 0
                )
                assert (
                    await admin.fetchval(
                        "SELECT active FROM ctt_staff_private.review_principals "
                        "WHERE principal_id=$1",
                        principal,
                    )
                    is False
                )
            finally:
                await admin.close()
            operator = await connect(enrollment_pg, OPERATOR)
            try:
                args = [
                    enrollment,
                    result["fingerprint"],
                    account,
                    principal,
                    "synthetic",
                    edition,
                    "coordinador",
                    "capture_staff_v2",
                    starts,
                    ends,
                    SHA,
                ]
                record_id = await operator.fetchval(
                    "SELECT ctt_staff_private.activate_passkey_enrollment("
                    "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    *args,
                )
                assert (
                    await operator.fetchval(
                        "SELECT ctt_staff_private.activate_passkey_enrollment("
                        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                        *args,
                    )
                    == record_id
                )
                assert (await service.status(account, enrollment))["status"] == "ACTIVE"
                await operator.execute(
                    "SELECT ctt_staff_private.revoke_enrolled_credential($1,$2,$3)",
                    record_id,
                    principal,
                    SHA,
                )
                assert (await service.status(account, enrollment))[
                    "status"
                ] == "REVOKED"
            finally:
                await operator.close()
        finally:
            await pool.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"uv": False},
        {"origin": "https://evil.example"},
        {"rp": "evil.example"},
        {"cross": True},
        {"be": False, "bs": True},
    ],
)
def test_invalid_registration_never_creates_a_candidate(enrollment_pg, change):
    async def run():
        from staff_review_enrollment_v2 import PasskeyEnrollmentDenied
        from uuid import UUID

        pool, service, account, _, edition, _, _ = await configure(enrollment_pg)
        try:
            begun = await service.begin(account, "synthetic", edition)
            with pytest.raises(PasskeyEnrollmentDenied):
                await service.complete(
                    account,
                    UUID(begun["enrollment_id"]),
                    registration(begun["options"], **change),
                )
            admin = await connect(enrollment_pg)
            try:
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private."
                        "review_enrollment_candidates"
                    )
                    == 0
                )
            finally:
                await admin.close()
        finally:
            await pool.close()

    asyncio.run(run())


def test_wrong_account_or_operator_fingerprint_cannot_replace_identity(enrollment_pg):
    async def run():
        from staff_review_enrollment_v2 import PasskeyEnrollmentDenied
        from uuid import UUID

        pool, service, account, principal, edition, starts, ends = await configure(
            enrollment_pg
        )
        try:
            begun = await service.begin(account, "synthetic", edition)
            enrollment = UUID(begun["enrollment_id"])
            response = registration(begun["options"])
            with pytest.raises(PasskeyEnrollmentDenied):
                await service.complete(uuid4(), enrollment, response)
            result = await service.complete(account, enrollment, response)
            with pytest.raises(PasskeyEnrollmentDenied):
                await service.complete(
                    account, enrollment, registration(begun["options"])
                )
            operator = await connect(enrollment_pg, OPERATOR)
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await operator.fetchval(
                        "SELECT ctt_staff_private.activate_passkey_enrollment("
                        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                        enrollment,
                        SHA,
                        account,
                        principal,
                        "synthetic",
                        edition,
                        "coordinador",
                        "capture_staff_v2",
                        starts,
                        ends,
                        SHA,
                    )
            finally:
                await operator.close()
            assert result["status"] == "PENDING_OPERATOR_REVIEW"
        finally:
            await pool.close()

    asyncio.run(run())


def test_application_cannot_call_service_or_operator_or_read_private_candidate(
    enrollment_pg,
):
    async def run():
        app = await connect(enrollment_pg, "devnous_user")
        try:
            for query in [
                "SELECT ctt_staff_private.current_review_service()",
                "SELECT ctt_staff_private.current_enrollment_operator()",
                "SELECT * FROM ctt_staff_private.review_enrollment_candidates",
                "SELECT * FROM ctt_staff_private.review_account_links",
            ]:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.fetch(query)
        finally:
            await app.close()

    asyncio.run(run())


def test_registration_race_is_idempotent_and_replay_with_other_key_fails(enrollment_pg):
    async def run():
        from uuid import UUID

        pool, service, account, _, edition, _, _ = await configure(enrollment_pg)
        try:
            first, second = await asyncio.gather(
                service.begin(account, "synthetic", edition),
                service.begin(account, "synthetic", edition),
            )
            assert first == second
            response = registration(first["options"])
            enrollment = UUID(first["enrollment_id"])
            one, two = await asyncio.gather(
                service.complete(account, enrollment, response),
                service.complete(account, enrollment, response),
            )
            assert one == two
            conn = await connect(enrollment_pg)
            try:
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM ctt_staff_private."
                        "review_enrollment_candidates"
                    )
                    == 1
                )
            finally:
                await conn.close()
        finally:
            await pool.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["TABLES", "FUNCTIONS", "SEQUENCES"])
def test_enrollment_rejects_unknown_default_privileges(enrollment_pg, kind):
    async def run():
        conn = await connect(enrollment_pg)
        try:
            await conn.execute(
                (
                    MIGRATIONS / "20261031_ctt_passkey_enrollment.rollback.sql"
                ).read_text()
            )
            await conn.execute("CREATE ROLE ctt_enrollment_unknown NOLOGIN")
            privilege = (
                "SELECT"
                if kind == "TABLES"
                else "EXECUTE" if kind == "FUNCTIONS" else "USAGE"
            )
            await conn.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                f"GRANT {privilege} ON {kind} TO ctt_enrollment_unknown"
            )
            with pytest.raises(asyncpg.RaiseError, match="ACL invalid"):
                await conn.execute(
                    (MIGRATIONS / "20261031_ctt_passkey_enrollment.sql").read_text()
                )
            await conn.execute("ROLLBACK")
            assert await conn.fetchval(
                "SELECT to_regclass('ctt_staff_private.review_account_links') IS NULL"
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "inactive_service",
        "inactive_operator",
        "broad_service",
        "wrong_pin",
        "rollback_configured",
    ],
)
def test_enrollment_configuration_boundaries_fail_closed(enrollment_pg, attack):
    async def run():
        from staff_review_enrollment_v2 import (
            PasskeyEnrollmentDenied,
            PasskeyEnrollmentService,
        )

        pool, service, account, principal, edition, starts, ends = await configure(
            enrollment_pg
        )
        try:
            admin = await connect(enrollment_pg)
            try:
                if attack == "inactive_service":
                    await admin.execute(
                        "UPDATE ctt_staff_private.review_services SET "
                        "active=false,authority_version=authority_version+1"
                    )
                elif attack == "inactive_operator":
                    await admin.execute(
                        "UPDATE ctt_staff_private.review_enrollment_operators SET "
                        "active=false,authority_version=authority_version+1"
                    )
                elif attack == "broad_service":
                    await admin.execute(f"GRANT pg_read_all_data TO {SERVICE}")
                elif attack == "rollback_configured":
                    with pytest.raises(asyncpg.RaiseError):
                        await admin.execute(
                            (
                                MIGRATIONS
                                / "20261031_ctt_passkey_enrollment.rollback.sql"
                            ).read_text()
                        )
                    await admin.execute("ROLLBACK")
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private."
                            "review_enrollment_invitations"
                        )
                        == 1
                    )
                    return
            finally:
                await admin.close()
            if attack == "inactive_operator":
                operator = await connect(enrollment_pg, OPERATOR)
                try:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await operator.fetchval(
                            "SELECT ctt_staff_private.current_enrollment_operator()"
                        )
                finally:
                    await operator.close()
            else:
                if attack == "wrong_pin":
                    service = PasskeyEnrollmentService(pool, backend_pin="other")
                with pytest.raises(PasskeyEnrollmentDenied):
                    await service.begin(account, "synthetic", edition)
        finally:
            await pool.close()

    asyncio.run(run())


def test_operator_cli_preview_and_exact_execution_use_separate_registered_role(
    enrollment_pg,
):
    async def run():
        import importlib.util
        from uuid import UUID

        spec = importlib.util.spec_from_file_location(
            "ctt_test_enrollment_operator",
            ROOT / "scripts/ctt_passkey_enrollment_operator.py",
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        pool, service, account, principal, edition, starts, ends = await configure(
            enrollment_pg
        )
        try:
            begun = await service.begin(account, "synthetic", edition)
            enrollment = UUID(begun["enrollment_id"])
            candidate = await service.complete(
                account, enrollment, registration(begun["options"])
            )
            request = dict(
                operation="activate",
                enrollment_id=str(enrollment),
                fingerprint=candidate["fingerprint"],
                account_id=str(account),
                principal_id=str(principal),
                tenant_id="synthetic",
                edition_id=str(edition),
                actor_role="coordinador",
                faculty="capture_staff_v2",
                valid_from=starts.isoformat(),
                valid_until=ends.isoformat(),
                approval_ref=SHA,
            )
            dsn = f"postgresql://{OPERATOR}@/postgres?host={enrollment_pg}&port=55492"
            preview = await cli.run(request=request, dsn=dsn, backend_pin=PIN)
            assert preview["executed"] is False
            assert (await service.status(account, enrollment))[
                "status"
            ] == "PENDING_OPERATOR_REVIEW"
            actual = await cli.run(
                request=request, dsn=dsn, backend_pin=PIN, execute=True
            )
            assert actual["executed"] is True
            assert (await service.status(account, enrollment))["status"] == "ACTIVE"
        finally:
            await pool.close()

    asyncio.run(run())
