"""Candidate source admission, review and capture on a restored empty schema.

Owner/ACL changes use the actual candidate migrations, without fixture owner
rewrites or a direct grant to REG-S07 versions. Source admission uses a real
operator signature and private CAS; identity uses a real synthetic WebAuthn
assertion. Enrollment and review input provenance remain synthetic fixtures,
not production authority, human acceptance or pilot accreditation.
"""

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from devnous.copa_telmex.staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    bind_review_plan,
    bind_review_evidence,
    freeze_capture_effect,
    freeze_review_plan,
)
from devnous.copa_telmex.staff_capture_runtime import assemble_capture_runtime
from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter
from devnous.copa_telmex.staff_capture_private_sources import (
    PrivateAttemptSourceProvider,
)
from devnous.copa_telmex.staff_capture_executor import LockedAttempt
from devnous.copa_telmex.staff_photo_store import (
    PrivateReviewPhotoStore,
    derive_rectangular_review_photo,
)
from devnous.copa_telmex.staff_source_admission import (
    SourceAdmissionLimits,
    admit_prepared_sources,
    prepare_source_admission,
    sign_source_admission,
)
from devnous.copa_telmex.staff_capture_transport import (
    CaptureUnixClient,
    CaptureTransportUncertain,
)
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_challenge import (
    canonical_scope_bytes,
    scope_hash,
)
from devnous.copa_telmex.staff_review_issuer_client import (
    ReviewIssuerClient,
    ReviewIssuerUnavailable,
)
from devnous.copa_telmex.staff_signed_assertion import (
    PLATFORM_POLICY_HASH,
    PLATFORM_PROFILE,
)
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)
ROOT = Path(__file__).resolve().parents[2]
BIN = "/usr/lib/postgresql/16/bin/"


def command(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        pytest.fail(result.stderr.decode("utf-8", "replace")[:2000])


@pytest.fixture
def full_pg(isolated_pg):
    configured = os.environ.get("CTT_SCHEMA_ONLY_DIRECTORY")
    if not configured:
        pytest.skip("Set CTT_SCHEMA_ONLY_DIRECTORY to reviewed schema-only export")
    source = Path(configured).resolve(strict=True)
    assert source.is_relative_to(Path("/tmp"))
    receipt = json.loads((source / "receipt.json").read_text())
    assert receipt["schema_only"] is True
    for name, digest in receipt["hashes"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
    created = []
    database = "ctt_full_runtime"

    async def setup():
        admin = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="postgres",
        )
        try:
            names = json.loads((source / "role-names.json").read_text()) + [
                "ctt_full_owner",
                "ctt_full_writer",
                "ctt_full_issuer",
                "ctt_full_channel",
                "ctt_full_source",
            ]
            for name in names:
                if not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)",
                    name,
                ):
                    quoted = '"' + name.replace('"', '""') + '"'
                    await admin.execute(
                        "CREATE ROLE "
                        + quoted
                        + (
                            " LOGIN"
                            if name
                            in {
                                "ctt_full_writer",
                                "ctt_full_issuer",
                                "ctt_full_channel",
                                "ctt_full_source",
                            }
                            else " NOLOGIN"
                        )
                    )
                    created.append(quoted)
            await admin.execute("CREATE DATABASE " + database)
            conn = await asyncpg.connect(
                host=isolated_pg,
                port=int(PORT),
                user="postgres",
                database=database,
            )
            try:
                await conn.execute("CREATE EXTENSION pgcrypto")
                base = [
                    "-h",
                    isolated_pg,
                    "-p",
                    PORT,
                    "-U",
                    "postgres",
                    "-d",
                    database,
                ]
                command(
                    [
                        BIN + "pg_restore",
                        "--exit-on-error",
                        "--section=pre-data",
                        *base,
                        str(source / "ctt.dump"),
                    ]
                )
                command(
                    [
                        BIN + "psql",
                        "-X",
                        "-v",
                        "ON_ERROR_STOP=1",
                        *base,
                        "-c",
                        "SET search_path=public,pg_catalog",
                        "-f",
                        str(source / "functions.sql"),
                    ]
                )
                command(
                    [
                        BIN + "pg_restore",
                        "--exit-on-error",
                        "--section=post-data",
                        *base,
                        str(source / "ctt.dump"),
                    ]
                )
                for name in [
                    "20261028_ctt_staff_security.sql",
                    "20261028_ctt_staff_review_state.sql",
                    "20261030_ctt_capture_anchors.sql",
                    "20261030_ctt_signed_assertion_guard.sql",
                    "20261030_ctt_capture_payloads.sql",
                    "20261030_ctt_staff_effect_gate.sql",
                    "20261030_ctt_transport_nonces.sql",
                    "20261030_ctt_platform_passkey.sql",
                    "20261030_ctt_assertion_issuer.sql",
                    "20261030_ctt_source_admission.sql",
                    "20261030_ctt_review_plan_scope.sql",
                    "20261030_ctt_legacy_write_boundary.sql",
                    "20261030_ctt_photo_visibility.sql",
                    "20261031_ctt_capture_finality.sql",
                ]:
                    try:
                        await conn.execute(
                            (ROOT / "database/migrations" / name).read_text()
                        )
                    except asyncpg.PostgresError as error:
                        pytest.fail(f"Migration {name}: {error}")
                for table in [
                    "copa_telmex_teams",
                    "copa_telmex_players",
                    "copa_telmex_team_staff",
                ]:
                    await conn.execute(
                        f"GRANT SELECT,INSERT ON public.{table} " "TO ctt_full_writer"
                    )
                await conn.execute(
                    "GRANT SELECT ON copa_telmex_teams TO " "samchat_ctt_staff_owner"
                )
                await conn.execute(
                    "GRANT USAGE ON SCHEMA ctt_staff_private TO "
                    "ctt_full_writer,ctt_full_issuer,ctt_full_channel,ctt_full_source"
                )
                # Candidate fixture only: this allowlist does not establish a
                # production ingress role, ownership cutover or v1 coexistence.
                for signature in [
                    "lock_capture_anchor(uuid)",
                    "load_capture_payload(uuid)",
                    "get_capture_result(uuid)",
                    "consume_review_challenge(uuid,uuid,bytea,text," "bigint,bigint)",
                    "record_signed_assertion_guard(uuid,text,bigint)",
                    "record_capture_result(uuid,uuid,text)",
                    "consume_review_challenge_v2(uuid,uuid,bytea,text,"
                    "bigint,bigint,text,boolean,boolean,text)",
                ]:
                    await conn.execute(
                        "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                        + signature
                        + " TO ctt_full_writer"
                    )
                for signature in [
                    "load_review_assertion_context(uuid)",
                    "record_review_assertion_issuance(uuid,text,bigint,"
                    "bigint,text,boolean,boolean,text,bigint)",
                ]:
                    await conn.execute(
                        "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                        + signature
                        + " TO ctt_full_issuer"
                    )
                await conn.execute(
                    "GRANT EXECUTE ON FUNCTION "
                    "ctt_staff_private.claim_transport_nonce(uuid,bigint) "
                    "TO ctt_full_channel"
                )
                await conn.execute(
                    "GRANT EXECUTE ON FUNCTION "
                    "ctt_staff_private.register_source_admission("
                    "uuid,bytea,text,bigint,bigint,bytea) TO ctt_full_source"
                )
                for signature in [
                    "record_capture_finality(uuid,bytea,bytea)",
                    "load_capture_finality_context(uuid)",
                    "load_capture_finality_inputs(uuid)",
                    "project_capture_finality_player(jsonb)",
                ]:
                    await conn.execute(
                        "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                        + signature
                        + " TO ctt_full_writer"
                    )
                await conn.execute(
                    "GRANT UPDATE(governance_state,finality_receipt_id,updated_at,"
                    "postcommit_revision,postcommit_snapshot_hash) ON "
                    "public.copa_telmex_players TO ctt_full_writer"
                )
                await conn.execute(
                    "GRANT UPDATE(governance_state,finality_receipt_id,updated_at) ON "
                    "public.copa_telmex_team_staff TO ctt_full_writer"
                )
                await conn.execute(
                    "GRANT UPDATE(id) ON public.copa_telmex_teams TO ctt_full_writer"
                )
                await conn.execute(
                    "INSERT INTO ctt_staff_private.finality_database_identity "
                    "(singleton,database_identity) VALUES(true,'isolated-copa')"
                )
            finally:
                await conn.close()
        finally:
            await admin.close()

    async def cleanup():
        admin = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="postgres",
        )
        try:
            await admin.execute("DROP DATABASE IF EXISTS " + database)
            for name in reversed(created):
                await admin.execute("DROP ROLE " + name)
        finally:
            await admin.close()

    try:
        asyncio.run(setup())
        yield isolated_pg, database
    finally:
        asyncio.run(cleanup())


def pub(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


async def public_review_source(
    admin, scope, manifest, root, player_indices, *, blocking_incident=False
):
    """Seed an ordinary synthetic review, retaining all real source guards."""
    from devnous.copa_telmex.draft_versioning import draft_content_hash

    values = dict(
        ocr_raw=None,
        extraction=manifest["documents"][0]["extraction"],
        validation={
            "incident_policy": {
                "player_results": [
                    {
                        "player_index": index,
                        "eligibility_status": "CLEARED",
                        "incidents": (
                            [
                                {
                                    "incident_type": "SYNTHETIC_BLOCKER",
                                    "blocks_player_eligibility": True,
                                    "blocks_team_registration": True,
                                }
                            ]
                            if blocking_incident
                            else []
                        ),
                    }
                    for index in player_indices
                ]
            }
        },
        review_edits={},
        layout_regions={},
        overall_confidence=1.0,
        needs_review=False,
    )
    digest = draft_content_hash(values)
    scope = replace(scope, draft_content_hash=digest)
    await admin.execute(
        "INSERT INTO public.copa_telmex_registration_review_sessions "
        "(id,status,source,provider,tournament_slug,started_at) "
        "VALUES($1,'reviewing','synthetic','synthetic',$2,clock_timestamp())",
        scope.session_id,
        scope.tournament_slug,
    )
    await admin.execute(
        "INSERT INTO public.copa_telmex_registration_review_drafts "
        "(id,session_id,extraction,validation,review_edits,layout_regions,"
        "overall_confidence,needs_review,draft_version,content_hash,mutation_type,"
        "mutation_operation_id,mutation_decision_id,mutation_receipt_id,created_at) "
        "VALUES($1,$2,$3::json,$4::json,'{}'::json,'{}'::json,1,false,1,$5,"
        "'synthetic_initial','synthetic-initial-full',$6,$6,clock_timestamp())",
        scope.draft_id,
        scope.session_id,
        json.dumps(values["extraction"]),
        json.dumps(values["validation"]),
        digest,
        "sha256:" + "b" * 64,
    )
    for page in scope.source_pages:
        await admin.execute(
            "INSERT INTO public.copa_telmex_registration_review_assets "
            "(id,session_id,page_index,image_path,sha256,width,height,created_at) "
            "VALUES($1,$2,$3,$4,$5,595,842,clock_timestamp())",
            page.asset_id,
            scope.session_id,
            page.page_index,
            str(root / "pages" / (page.image_sha256[7:] + ".png")),
            page.image_sha256[7:],
        )
    return scope


def synthetic_assertion(challenge, *, platform):
    """Build a synthetic authenticator response for the real issuer.

    The optional external gate candidate and isolated fido2 dependency must be
    on PYTHONPATH. Verification and proof signing belong to the issuer;
    SQL runtime owns the later atomic challenge consumption.
    """
    gate = pytest.importorskip("staff_review_webauthn_v2")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.cose import ES256
    from fido2.webauthn import (
        AttestedCredentialData,
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    private = ec.generate_private_key(ec.SECP256R1())
    credential = AttestedCredentialData.create(
        bytes(16),
        b"synthetic-full-runtime-credential",
        ES256.from_cryptography_key(private.public_key()),
    )
    client = CollectedClientData.create(
        "webauthn.get", challenge, gate.ORIGIN, cross_origin=False
    )
    flags = AuthenticatorData.FLAG.UP | AuthenticatorData.FLAG.UV
    if platform:
        flags |= AuthenticatorData.FLAG.BE | AuthenticatorData.FLAG.BS
    auth = AuthenticatorData.create(
        hashlib.sha256(gate.RP_ID.encode()).digest(),
        flags,
        0 if platform else 5,
    )
    response = AuthenticationResponse(
        raw_id=credential.credential_id,
        response=AuthenticatorAssertionResponse(
            client_data=client,
            authenticator_data=auth,
            signature=private.sign(
                bytes(auth) + client.hash, ec.ECDSA(hashes.SHA256())
            ),
            user_handle=b"synthetic-handle",
        ),
    )
    return bytes(credential), json.dumps(dict(response)).encode("utf-8")


@pytest.mark.parametrize("platform", [False, True], ids=["device", "platform"])
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "two_staff",
        "national_missing_curp",
        "rollback",
        "revoked",
        "version",
        "counter",
        "finality_missing",
        "finality_signature",
        "finality_expired",
        "finality_bus_reply_lost",
        "finality_rollback_retry",
        "blocking_incident",
        "missing_finality_inputs",
    ],
)
def test_full_runtime_writes_or_rolls_back_real_rows(
    full_pg, tmp_path, monkeypatch, scenario, platform
):
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    from test_ctt_staff_capture_effect import effect
    from test_ctt_staff_capture_source_adapter import candidate

    issuer_module = pytest.importorskip("staff_review_issuer_v2")
    issuer_http_module = pytest.importorskip("staff_review_http_v2")
    business_module = pytest.importorskip("staff_capture_gate_v2")
    monkeypatch.syspath_prepend(
        str(Path(issuer_module.__file__).resolve().parents[1] / "evidence-bus")
    )
    from durable_event_store import DurableJSONLEventStore
    from evidence_receipt import issue_evidence_receipt
    from devnous.copa_telmex.registration_governance import _derive_geometry

    national = scenario == "national_missing_curp"
    corrupt = scenario == "rollback"
    denied = scenario not in {
        "success",
        "two_staff",
        "national_missing_curp",
        "finality_bus_reply_lost",
        "finality_rollback_retry",
    }

    async def run():
        socket, database = full_pg
        admin = await asyncpg.connect(
            host=socket, port=int(PORT), user="postgres", database=database
        )
        capture_pool = nonce_pool = issuer_pool = issuer_http = source_pool = None
        journal = None
        snapshots = []
        original = SourceCheckAdapter.open_verified_snapshot

        @contextmanager
        def observe(adapter, attempt):
            with original(adapter, attempt) as snapshot:
                snapshots.append(snapshot)
                yield snapshot

        monkeypatch.setattr(SourceCheckAdapter, "open_verified_snapshot", observe)
        try:
            locked, scope, manifest, pdf, images = candidate()
            import fitz

            with fitz.open() as document:
                first = document.new_page()
                for position, value in (
                    (72, "Jugador 3 - Synthetic Player"),
                    (94, "2000-01-01 AAAA000101HDFXXX01"),
                    (182, "Director tecnico - Synthetic Coach"),
                    (204, "1980-01-01 BBBB800101HDFXXX01"),
                ):
                    if national:
                        value = value.replace(
                            "AAAA000101HDFXXX01", "CURP: no proporcionada"
                        )
                        value = value.replace(
                            "BBBB800101HDFXXX01", "CURP: no proporcionada"
                        )
                    first.insert_text((72, position), value)
                if national:
                    first.insert_text((72, 270), "Jugador 4 - Synthetic Second Player")
                    first.insert_text((72, 292), "2000-01-01 CURP: no proporcionada")
                first.draw_rect(
                    fitz.Rect(450, 50, 530, 150), fill=(0.2, 0.5, 0.8), color=None
                )
                if national:
                    first.draw_rect(
                        fitz.Rect(450, 250, 530, 350),
                        fill=(0.8, 0.3, 0.2),
                        color=None,
                    )
                second = document.new_page()
                second.insert_text(
                    (72, 72),
                    (
                        (
                            "Auxiliar - Synthetic Assistant 1985-01-01 "
                            "CURP: no proporcionada"
                            if national
                            else "Auxiliar - Synthetic Assistant 1985-01-01 "
                            "CCCC850101HDFXXX01"
                        )
                        if scenario == "two_staff" or national
                        else "Auxiliar: sin identidad declarada"
                    ),
                )
                pdf = document.tobytes()
            if national:
                manifest["documents"][0]["extraction"]["players"] = [
                    dict(
                        name=name,
                        birth_date="2000-01-01",
                        curp=None,
                        slot_ref=f"page:1:jugador_{slot}",
                    )
                    for slot, name in (
                        (3, "Synthetic Player"),
                        (4, "Synthetic Second Player"),
                    )
                ]
            manifest["documents"][0]["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
            root, originals = tmp_path / "sources", tmp_path / "originals"
            root.mkdir(mode=0o700)
            originals.mkdir(mode=0o700)
            source_path = originals / manifest["documents"][0]["pdf"]
            source_path.write_bytes(pdf)
            source_path.chmod(0o600)
            source_pool = await asyncpg.create_pool(
                host=socket,
                port=int(PORT),
                user="ctt_full_source",
                database=database,
                min_size=1,
                max_size=1,
            )
            operator_key = Ed25519PrivateKey.generate()
            limits = SourceAdmissionLimits(
                1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000
            )
            with prepare_source_admission(
                canonical(manifest), originals, locked.edition_binding, limits
            ) as prepared:
                now = int(time.time())
                source_envelope = sign_source_admission(
                    prepared.plan_bytes,
                    admission_id=uuid4(),
                    issued_at=now,
                    expires_at=now + 180,
                    kid="operator",
                    private_key=operator_key,
                )
                await admit_prepared_sources(
                    prepared,
                    source_envelope,
                    public_keys={"operator": pub(operator_key)},
                    private_root=root,
                    pool=source_pool,
                )
                source_plan = json.loads(prepared.plan_bytes)
                scope = replace(
                    scope,
                    manifest_sha256=source_plan["manifest_sha256"],
                    pdf_sha256=hashlib.sha256(pdf).hexdigest(),
                    source_pages=tuple(
                        replace(page, image_sha256=admitted["image_sha256"])
                        for page, admitted in zip(
                            scope.source_pages, source_plan["documents"][0]["pages"]
                        )
                    ),
                )
            args = effect()
            if scenario == "two_staff" or national:
                assistant = copy.deepcopy(args["staff"][0]["row"])
                assistant.update(id=str(uuid4()), staff_slot=2, role="auxiliar")
                args["staff"][1].update(presence="PRESENT", row=assistant)
            if national:
                second_player = copy.deepcopy(args["players"][0])
                second_player.update(id=str(uuid4()), roster_index=2)
                args["players"].append(second_player)
                args["player_source_refs"].append("page:1:jugador_4")
            args["scope"] = scope
            args["team"].update(
                id=str(scope.team_id), tournament_slug=scope.tournament_slug
            )
            for row in args["players"] + [
                item["row"] for item in args["staff"] if item["row"]
            ]:
                row.update(
                    team_id=str(scope.team_id),
                    governance_draft_id=str(scope.draft_id),
                    governance_draft_version=scope.draft_version,
                )
                row.update(
                    first_name="Synthetic",
                    last_name=(
                        "Player"
                        if row in args["players"]
                        else "Assistant" if row.get("staff_slot") == 2 else "Coach"
                    ),
                    birth_date=(
                        "2000-01-01"
                        if row in args["players"]
                        else (
                            "1985-01-01" if row.get("staff_slot") == 2 else "1980-01-01"
                        )
                    ),
                    curp=(
                        "AAAA000101HDFXXX01"
                        if row in args["players"]
                        else (
                            "CCCC850101HDFXXX01"
                            if row.get("staff_slot") == 2
                            else "BBBB800101HDFXXX01"
                        )
                    ),
                )
                for field in LATE_AUTHORITY_FIELDS:
                    row[field] = None
            if national:
                for row in args["players"] + [s["row"] for s in args["staff"]]:
                    row["curp"] = None
                args["players"][1]["last_name"] = "Second Player"
            for row in [args["team"]] + args["players"]:
                for field in DATABASE_DERIVED_FIELDS:
                    row[field] = None
            review_key, effect_key, channel_key = (
                b"r" * 32,
                b"e" * 32,
                b"c" * 32,
            )
            policy = dict(
                policy_id="samchat_registration_identity_policy_v2",
                workflow_id="samchat_registration_identity_v2",
                policy_version="2.0.0",
                effective_sal="SAL-3",
                field_order=["full_name", "birth_date", "curp"],
                minimum_effective_pixels=12000,
                allowed_roles=["coordinador"],
                minimum_roster_size=1,
                maximum_roster_size=25,
                required_staff_slots=[1],
            )
            if national:
                policy.update(
                    policy_version="2.1.0",
                    phase_binding=dict(
                        phase="NACIONAL",
                        tenant_id=scope.tenant_id,
                        edition_id=str(scope.edition_id),
                        manifest_sha256=scope.manifest_sha256,
                    ),
                    optional_curp_kinds=["PLAYER", "STAFF"],
                    required_staff_slots=[1, 2],
                )
            policy["policy_hash"] = (
                "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()
            )

            def geometry(page, y):
                path = root / "pages" / (page.image_sha256[7:] + ".png")
                result = _derive_geometry(
                    SimpleNamespace(image_path=path, sha256=page.image_sha256[7:]),
                    {"x": 50, "y": y, "width": 350, "height": 100},
                )
                assert result is not None
                return result

            photo_root = tmp_path / "private-photos"
            photo_root.mkdir(mode=0o700)
            page = scope.source_pages[0]
            page_path = root / "pages" / (page.image_sha256[7:] + ".png")
            photo_geometry = _derive_geometry(
                SimpleNamespace(image_path=page_path, sha256=page.image_sha256[7:]),
                {"x": 450, "y": 50, "width": 80, "height": 100},
            )
            assert photo_geometry is not None
            photo = derive_rectangular_review_photo(
                page_path.read_bytes(), photo_geometry, max_page_bytes=1_000_000
            )
            photo_store = PrivateReviewPhotoStore(photo_root)
            assert photo_store.stage(photo) == photo.photo_path
            for player in args["players"]:
                player.update(
                    photo_path=photo.photo_path,
                    photo_sha256=photo.sha256,
                    photo_ahash=photo.ahash,
                    ocr_confidence=1.0,
                    created_at="2026-10-30T12:00:00.123456",
                )
            player_photos = {args["players"][0]["id"]: photo_geometry}
            if national:
                second_geometry = _derive_geometry(
                    SimpleNamespace(image_path=page_path, sha256=page.image_sha256[7:]),
                    {"x": 450, "y": 250, "width": 80, "height": 100},
                )
                assert second_geometry is not None
                second_photo = derive_rectangular_review_photo(
                    page_path.read_bytes(), second_geometry, max_page_bytes=1_000_000
                )
                assert second_photo.sha256 != photo.sha256
                assert photo_store.stage(second_photo) == second_photo.photo_path
                args["players"][1].update(
                    photo_path=second_photo.photo_path,
                    photo_sha256=second_photo.sha256,
                    photo_ahash=second_photo.ahash,
                )
                player_photos[args["players"][1]["id"]] = second_geometry
            members = []
            for player in args["players"]:
                members.append(
                    dict(
                        kind="PLAYER",
                        row_id=player["id"],
                        slot=player["roster_index"],
                        presence="PRESENT",
                        source_page_index=1,
                        fields={
                            field: geometry(
                                scope.source_pages[0],
                                250 if player["roster_index"] == 2 else 50,
                            )
                            for field in policy["field_order"]
                            if not national or field != "curp"
                        },
                        absence_geometry=None,
                        photo_evidence={
                            "mode": "SOURCE_RECTANGLE",
                            "geometry": player_photos[player["id"]],
                        },
                    )
                )
            for staff in args["staff"]:
                present = staff["presence"] == "PRESENT"
                page_index = 2 if staff["slot"] == 2 else 1
                members.append(
                    dict(
                        kind="STAFF",
                        row_id=staff["row"]["id"] if present else None,
                        slot=staff["slot"],
                        presence=staff["presence"],
                        source_page_index=page_index,
                        fields=(
                            {
                                field: geometry(
                                    scope.source_pages[page_index - 1],
                                    160 if page_index == 1 else 50,
                                )
                                for field in policy["field_order"]
                                if not national or field != "curp"
                            }
                            if present
                            else {}
                        ),
                        absence_geometry=(
                            None if present else geometry(scope.source_pages[1], 50)
                        ),
                    )
                )
            evidence = canonical(
                dict(
                    schema="ctt.capture.identity-evidence.v1",
                    members=members,
                    incident_state={"blocking_incident_count": 0},
                )
            )
            present_staff_count = sum(item["row"] is not None for item in args["staff"])
            finality_person_count = len(args["players"]) + present_staff_count
            scope = replace(
                scope,
                decision_hash=None,
                review_receipt_id=None,
                field_evidence_binding=bind_review_evidence(evidence, review_key),
                policy_hash=policy["policy_hash"],
            )
            scope = await public_review_source(
                admin,
                scope,
                manifest,
                root,
                [player["roster_index"] for player in args["players"]],
                blocking_incident=scenario == "blocking_incident",
            )
            args["scope"] = scope
            plan = freeze_review_plan(**args)
            binding = bind_review_plan(plan, review_key)
            scope = replace(scope, staff_and_changes_binding=binding)
            args["scope"] = scope
            attempt, challenge, gate_nonce = (
                locked.attempt_id,
                locked.challenge_id,
                uuid4(),
            )
            credential = b"synthetic-full-runtime-credential"
            issued = int(time.time())
            expiry = issued + (4 if scenario == "finality_expired" else 180)
            challenge_bytes = secrets.token_bytes(32)
            attested_credential, response_bytes = synthetic_assertion(
                challenge_bytes, platform=platform
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_keys "
                "(kid,secret,not_before,not_after) "
                "VALUES('test-effect',$1,to_timestamp($2),to_timestamp($3))",
                effect_key,
                issued - 10,
                expiry + 60,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_writers "
                "(writer_role,kid) VALUES('ctt_full_writer','test-effect')"
            )
            gate_key, assertion_key = (
                Ed25519PrivateKey.generate(),
                Ed25519PrivateKey.generate(),
            )
            await admin.execute(
                "INSERT INTO "
                "ctt_staff_private.review_principals(principal_id,active"
                ") VALUES($1,true)",
                scope.actor_id,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_credentials "
                "(credential_id,principal_id,attested_credential,user_handle,"
                "auth_profile,auth_policy_hash,backup_eligible,backup_state,"
                "sign_count) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,false,$8)",
                credential,
                scope.actor_id,
                attested_credential,
                b"synthetic-handle",
                PLATFORM_PROFILE if platform else "device-counter-v1",
                PLATFORM_POLICY_HASH if platform else None,
                platform,
                0 if platform else 4,
            )
            await admin.execute(
                "INSERT INTO "
                "ctt_staff_private.review_faculties(principal_id,tenant_"
                "id,edition_id,faculty,valid_from,valid_until) "
                "VALUES($1,$2,$3,'capture_staff_v2',to_timestamp($4),to_"
                "timestamp($5))",
                scope.actor_id,
                scope.tenant_id,
                scope.edition_id,
                issued - 10,
                expiry + 60,
            )
            await admin.execute(
                "INSERT INTO "
                "ctt_staff_private.review_challenges(challenge_id,princi"
                "pal_id,credential_id,tenant_id,edition_id,principal_aut"
                "hority_version,faculty_authority_version,credential_aut"
                "hority_version,scope_hash,challenge_bytes,issued_at,exp"
                "ires_at) "
                "VALUES($1,$2,$3,$4,$5,1,1,1,$6,$7,to_timestamp($8),to_t"
                "imestamp($9))",
                challenge,
                scope.actor_id,
                credential,
                scope.tenant_id,
                scope.edition_id,
                scope_hash(scope),
                challenge_bytes,
                issued,
                expiry,
            )
            await admin.execute(
                "INSERT INTO "
                "ctt_staff_private.capture_attempt_anchors(attempt_id,ch"
                "allenge_id,manifest_sha256,document_id,pdf_sha256,tenan"
                "t_id,edition_id,scope_hash,scope_bytes) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                attempt,
                challenge,
                scope.manifest_sha256,
                scope.document_id,
                scope.pdf_sha256,
                scope.tenant_id,
                scope.edition_id,
                scope_hash(scope),
                canonical_scope_bytes(scope),
            )
            if corrupt:
                await admin.execute(
                    "CREATE SEQUENCE test_corrupt_hits; "
                    "GRANT USAGE ON test_corrupt_hits TO ctt_full_writer; "
                    "CREATE FUNCTION test_corrupt_player() RETURNS trigger "
                    "LANGUAGE plpgsql SET search_path=pg_catalog,public "
                    "AS $$ BEGIN PERFORM nextval('public.test_corrupt_hits'); "
                    "NEW.last_name:='modified'; RETURN NEW; END $$; CREATE "
                    "TRIGGER test_corrupt_player BEFORE INSERT ON "
                    "copa_telmex_players FOR EACH ROW EXECUTE FUNCTION "
                    "test_corrupt_player()"
                )
            capture_pool = await asyncpg.create_pool(
                host=socket,
                port=int(PORT),
                user="ctt_full_writer",
                database=database,
                min_size=1,
                max_size=3,
            )
            nonce_pool = await asyncpg.create_pool(
                host=socket,
                port=int(PORT),
                user="ctt_full_channel",
                database=database,
                min_size=1,
                max_size=2,
            )
            issuer_pool = await asyncpg.create_pool(
                host=socket,
                port=int(PORT),
                user="ctt_full_issuer",
                database=database,
                min_size=1,
                max_size=2,
            )
            issuer = issuer_module.PostgresReviewAssertionIssuer(
                issuer_pool,
                signing_kid="assertion",
                signing_key=assertion_key,
            )
            issuer_channel_key = b"h" * 32
            issuer_app = issuer_http_module.create_review_issuer_app(
                issuer=issuer,
                channel_key=issuer_channel_key,
                consume_nonce=PostgresTransportNonces(nonce_pool),
            )
            issuer_http = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=issuer_app), trust_env=False
            )
            review_client = ReviewIssuerClient(
                url="http://127.0.0.1:9101",
                channel_key=issuer_channel_key,
                http_client=issuer_http,
            )
            async with nonce_pool.acquire() as channel_conn:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await channel_conn.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.transport_nonces"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await channel_conn.fetchrow(
                        "SELECT * FROM "
                        "ctt_staff_private.load_review_assertion_context($1)",
                        attempt,
                    )
            async with issuer_pool.acquire() as issuer_conn:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await issuer_conn.fetchval(
                        "SELECT count(*) FROM " "ctt_staff_private.review_credentials"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await issuer_conn.fetchval(
                        "SELECT ctt_staff_private.get_capture_result($1)",
                        attempt,
                    )
            verified_webauthn = False
            assert not (tmp_path / "events.jsonl").exists()
            signed = await review_client.issue(attempt, response_bytes)
            assert await review_client.issue(attempt, response_bytes) == signed
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.transport_nonces"
                )
                == 2
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_challenge_consumptions"
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_assertion_issuances"
                )
                == 1
            )
            # No business receipt exists until the HTTP issuer has verified
            # this exact review proof. The publisher asserts that ordering.
            verified_webauthn = True
            receipt_key = Ed25519PrivateKey.generate()
            journal = DurableJSONLEventStore(
                str(tmp_path / "events.jsonl"),
                v2_public_keys={"bus": pub(receipt_key)},
            )
            publish_calls = []
            lost_reply_delivered = False

            def publish(event):
                nonlocal lost_reply_delivered, journal
                assert verified_webauthn
                publish_calls.append(copy.deepcopy(event))
                is_finality = event["event_type"].endswith("_finality_attestation_v2")
                is_player_finality = event["event_type"] == (
                    "samchat_registration_player_finality_attestation_v2"
                )
                if (
                    scenario == "finality_missing"
                    and is_finality
                    and not is_player_finality
                ):
                    raise RuntimeError("synthetic finality receipt unavailable")
                if (
                    scenario == "finality_expired"
                    and is_finality
                    and not is_player_finality
                ):
                    time.sleep(5)
                wrapped = {
                    **event,
                    "type": event["event_type"],
                    "execution_receipt_ref": "zaubern:registration-gate:precommit",
                }
                receipt = journal.append_idempotent_receipt(
                    wrapped,
                    lambda: issue_evidence_receipt(
                        wrapped,
                        None,
                        kid="bus",
                        alg="Ed25519",
                        ed25519_private_key=receipt_key,
                    ),
                )
                if scenario == "finality_signature" and is_player_finality:
                    receipt = {**receipt, "signature": "AA=="}
                if (
                    scenario == "finality_bus_reply_lost"
                    and is_player_finality
                    and not lost_reply_delivered
                ):
                    lost_reply_delivered = True
                    # The journal has fsynced the receipt; only its response
                    # is lost. Reopen from disk before the gate retries: the
                    # exact same event must recover the durable receipt.
                    journal.close()
                    journal = DurableJSONLEventStore(
                        str(tmp_path / "events.jsonl"),
                        v2_public_keys={"bus": pub(receipt_key)},
                    )
                    raise ConnectionError("synthetic lost durable bus response")
                return receipt

            business_gate = business_module.StaffCaptureGateV2(
                identity_key=b"i" * 32,
                review_key=review_key,
                effect_key=effect_key,
                signing_kid="gate",
                signing_key=gate_key,
                assertion_public_keys={"assertion": pub(assertion_key)},
                receipt_public_keys={"bus": pub(receipt_key)},
                policy_artifact=policy,
                expected_policy_hash=policy["policy_hash"],
                publisher=publish,
                max_page_bytes=1_000_000,
            )
            current = LockedAttempt(
                attempt,
                challenge,
                scope.actor_id,
                credential,
                scope_hash(scope),
                canonical_scope_bytes(scope),
                locked.edition_binding,
            )
            provider = PrivateAttemptSourceProvider(
                current,
                root,
                max_manifest_bytes=1_000_000,
            )
            source_check = SourceCheckAdapter(
                provider,
                max_source_bytes=1_000_000,
                max_asset_bytes=1_000_000,
                max_manifest_bytes=1_000_000,
            )
            with source_check.open_verified_snapshot(current) as snapshot:
                preauthorization = business_gate.preauthorize(
                    scope=scope,
                    attempt_id=attempt,
                    review_bytes=plan,
                    evidence_bytes=evidence,
                    signed_review=signed,
                    snapshot=snapshot,
                    issued_at=issued,
                    expires_at=expiry,
                )
                assert preauthorization["authorized"] is True
                # Restart the real journal: a retry must recover the original
                # verified receipt, rather than create a second decision.
                journal.close()
                journal = DurableJSONLEventStore(
                    str(tmp_path / "events.jsonl"),
                    v2_public_keys={"bus": pub(receipt_key)},
                )
                assert (
                    business_gate.preauthorize(
                        scope=scope,
                        attempt_id=attempt,
                        review_bytes=plan,
                        evidence_bytes=evidence,
                        signed_review=signed,
                        snapshot=snapshot,
                        issued_at=issued,
                        expires_at=expiry,
                    )
                    == preauthorization
                )
            assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1
            for row in args["players"] + [
                item["row"] for item in args["staff"] if item["row"]
            ]:
                row.update(preauthorization["authority_metadata"][row["id"]])
            for model, rows, revision in (
                ("team", [args["team"]], 1),
                ("player", args["players"], 0),
            ):
                for row in rows:
                    row["postcommit_revision"] = revision
                    row["postcommit_snapshot_hash"] = await admin.fetchval(
                        "SELECT public.regs07_snapshot_hash("
                        f"public.regs07_{model}_snapshot(jsonb_populate_record("
                        f"NULL::public.copa_telmex_{model}s,$1::jsonb)))",
                        json.dumps(row),
                    )
            final = freeze_capture_effect(**args)
            gate_envelope = business_gate.authorize_effect(
                scope=scope,
                attempt_id=attempt,
                nonce=gate_nonce,
                review_bytes=plan,
                effect_bytes=final,
                signed_review=signed,
                preauthorization=preauthorization,
                issued_at=issued,
                expires_at=expiry,
            )
            # Synthetic protected candidate input only: the test does not
            # implement an operational review-plan admission endpoint.
            await admin.execute(
                "INSERT INTO ctt_staff_private.capture_payloads "
                "(attempt_id,review_bytes,effect_bytes,gate_envelope,nonce) "
                "VALUES($1,$2,$3,$4,$5)",
                attempt,
                plan,
                final,
                gate_envelope,
                gate_nonce,
            )
            if scenario != "missing_finality_inputs":
                await admin.execute(
                    "INSERT INTO ctt_staff_private.finality_inputs "
                    "(attempt_id,preauthorization_bytes,evidence_bytes) "
                    "VALUES($1,$2,$3)",
                    attempt,
                    canonical(preauthorization),
                    evidence,
                )
            runtime = assemble_capture_runtime(
                capture_pool=capture_pool,
                nonce_pool=nonce_pool,
                private_source_root=root,
                private_photo_root=photo_root,
                gate_public_keys={"gate": pub(gate_key)},
                assertion_public_keys={"assertion": pub(assertion_key)},
                receipt_public_keys={"bus": pub(receipt_key)},
                finality_service=business_gate,
                database_identity="isolated-copa",
                channel_key=channel_key,
                review_key=review_key,
                effect_key=effect_key,
                allowed_uids=frozenset({os.getuid()}),
                max_source_bytes=1000000,
                max_asset_bytes=1000000,
                max_manifest_bytes=1000000,
            )
            assert photo_store.read(photo.sha256 + ".jpg") == photo.data
            app_connection = await asyncpg.connect(
                host=socket,
                port=int(PORT),
                user="devnous_user",
                database=database,
            )
            try:
                for statement in (
                    "SELECT count(*) FROM ctt_staff_private.finality_inputs",
                    "SELECT ctt_staff_private.load_capture_finality_inputs($1)",
                    "SELECT ctt_staff_private.load_capture_finality_context($1)",
                    "SELECT ctt_staff_private.record_capture_finality("
                    "$1,'{}'::bytea,'{}'::bytea)",
                    "UPDATE public.copa_telmex_players SET "
                    "governance_state='ACTIVE' WHERE false",
                    "UPDATE public.copa_telmex_team_staff SET "
                    "governance_state='ACTIVE' WHERE false",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await app_connection.execute(
                            statement, *([attempt] if "$1" in statement else [])
                        )
                assert (
                    await app_connection.fetchval(
                        "SELECT public.ctt_photo_is_committed($1)", photo.sha256
                    )
                    is False
                )
            finally:
                await app_connection.close()
            assert (
                await admin.fetchval(
                    "SELECT public.ctt_photo_is_committed($1)", photo.sha256
                )
                is False
            )
            if scenario == "finality_rollback_retry":
                await admin.execute(
                    "CREATE SEQUENCE test_finality_result_hits; "
                    "GRANT USAGE ON test_finality_result_hits "
                    "TO ctt_full_writer,samchat_ctt_staff_owner; "
                    "CREATE FUNCTION test_fail_finality_result() RETURNS trigger "
                    "LANGUAGE plpgsql SET search_path=pg_catalog,public "
                    "AS $$ BEGIN PERFORM nextval('public.test_finality_result_hits'); "
                    "RAISE EXCEPTION 'synthetic failure after finalities'; END $$; "
                    "CREATE TRIGGER test_fail_finality_result BEFORE INSERT ON "
                    "ctt_staff_private.capture_results FOR EACH ROW "
                    "EXECUTE FUNCTION test_fail_finality_result()"
                )
            if scenario in {"revoked", "version", "counter"}:
                changes = {
                    "revoked": "revoked_at=clock_timestamp(),"
                    "authority_version=authority_version+1",
                    "version": "authority_version=authority_version+1",
                    "counter": "sign_count=sign_count+1",
                }
                await admin.execute(
                    "UPDATE ctt_staff_private.review_credentials SET "
                    + changes[scenario]
                    + " WHERE credential_id=$1",
                    credential,
                )
                with pytest.raises(ReviewIssuerUnavailable):
                    await review_client.issue(attempt, response_bytes)
            request_nonce = uuid4()
            path = tmp_path / "writer.sock"
            async with runtime.serve(path):
                client = CaptureUnixClient(
                    path=path, key=channel_key, server_uid=os.getuid()
                )
                if denied:
                    with pytest.raises(CaptureTransportUncertain):
                        await client.execute(attempt, signed, nonce=request_nonce)
                else:
                    if scenario == "finality_rollback_retry":
                        with pytest.raises(CaptureTransportUncertain):
                            await client.execute(attempt, signed, nonce=request_nonce)
                        assert (
                            await admin.fetchval(
                                "SELECT is_called FROM test_finality_result_hits"
                            )
                            is True
                        )
                        for table in (
                            "public.copa_telmex_teams",
                            "public.copa_telmex_players",
                            "public.copa_telmex_team_staff",
                            "ctt_staff_private.review_challenge_consumptions",
                            "ctt_staff_private.capture_finalities",
                            "ctt_staff_private.capture_results",
                        ):
                            assert (
                                await admin.fetchval("SELECT count(*) FROM " + table)
                                == 0
                            )
                        assert (
                            await admin.fetchval(
                                "SELECT public.ctt_photo_is_committed($1)", photo.sha256
                            )
                            is False
                        )
                        assert (
                            len((tmp_path / "events.jsonl").read_text().splitlines())
                            == 1 + finality_person_count
                        )
                        await admin.execute(
                            "DROP TRIGGER test_fail_finality_result "
                            "ON ctt_staff_private.capture_results"
                        )
                        request_nonce = uuid4()
                    await client.execute(attempt, signed, nonce=request_nonce)
                    result = await admin.fetchval(
                        "SELECT result_id FROM "
                        "ctt_staff_private.capture_results "
                        "WHERE attempt_id=$1",
                        attempt,
                    )
                    await client.execute(attempt, signed, nonce=uuid4())
                    assert (
                        await admin.fetchval(
                            "SELECT result_id FROM "
                            "ctt_staff_private.capture_results "
                            "WHERE attempt_id=$1",
                            attempt,
                        )
                        == result
                    )
                with pytest.raises(CaptureTransportUncertain):
                    await client.execute(attempt, signed, nonce=request_nonce)
            if corrupt:
                # Sequence increments survive rollback: proves the safe
                # trigger fired, rather than failing at the schema boundary.
                assert await admin.fetchval("SELECT is_called FROM test_corrupt_hits")
            expected = int(not denied)
            for table in [
                "copa_telmex_teams",
                "copa_telmex_players",
                "copa_telmex_team_staff",
                "ctt_staff_private.review_challenge_consumptions",
                "ctt_staff_private.signed_assertion_guards",
                "ctt_staff_private.capture_results",
                "ctt_staff_private.effect_staff_writes",
                "ctt_staff_private.capture_finalities",
            ]:
                assert await admin.fetchval(
                    "SELECT count(*) FROM " + table
                ) == expected * (
                    present_staff_count
                    if table
                    in {
                        "copa_telmex_team_staff",
                        "ctt_staff_private.effect_staff_writes",
                    }
                    else len(args["players"]) if table == "copa_telmex_players" else 1
                ), table
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "public.copa_telmex_registration_postcommit_entity_versions"
                )
                == (1 + len(args["players"])) * expected
            )
            assert await admin.fetchval(
                "SELECT public.ctt_photo_is_committed($1)", photo.sha256
            ) is (not denied)
            finality_calls = [
                call
                for call in publish_calls
                if call["event_type"].endswith("_finality_attestation_v2")
            ]
            if scenario in {"blocking_incident", "missing_finality_inputs"}:
                assert finality_calls == []
            elif scenario == "finality_missing":
                assert len(finality_calls) == 2
            elif scenario == "finality_signature":
                assert len(finality_calls) == 1
            elif scenario == "finality_expired":
                assert len(finality_calls) >= 2
            if national:
                for table in ("copa_telmex_players", "copa_telmex_team_staff"):
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM " + table + " WHERE curp IS NULL"
                        )
                        == 2
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM " + table + " WHERE curp = ''"
                        )
                        == 0
                    )
            if not denied:
                for table in (
                    "public.copa_telmex_players",
                    "public.copa_telmex_team_staff",
                ):
                    states = await admin.fetch(
                        "SELECT governance_state,finality_receipt_id FROM " + table
                    )
                    assert states
                    assert all(
                        state["governance_state"] == "ACTIVE" for state in states
                    )
                    assert all(
                        state["finality_receipt_id"].startswith("sha256:")
                        for state in states
                    )
                assert (
                    await admin.fetchval(
                        "SELECT bool_and(postcommit_revision=1) "
                        "FROM public.copa_telmex_players"
                    )
                    is True
                )
                assert (
                    await admin.fetchval(
                        "SELECT bool_and(p.postcommit_snapshot_hash="
                        "public.regs07_snapshot_hash("
                        "public.regs07_player_snapshot(p))) "
                        "FROM public.copa_telmex_players p"
                    )
                    is True
                )
                async with capture_pool.acquire() as writer_connection:
                    assert (
                        await writer_connection.fetchval(
                            "SELECT has_column_privilege(current_user,"
                            "'public.copa_telmex_teams','id','UPDATE')"
                        )
                        is True
                    )
                    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="CTT"):
                        await writer_connection.execute(
                            "UPDATE public.copa_telmex_teams SET id=id WHERE id=$1",
                            scope.team_id,
                        )
                assert len((tmp_path / "events.jsonl").read_text().splitlines()) == (
                    1
                    + finality_person_count
                    * (2 if scenario == "finality_rollback_retry" else 1)
                )
                if scenario == "finality_bus_reply_lost":
                    assert lost_reply_delivered
                    assert finality_calls[0] == finality_calls[1]
                    assert (
                        finality_calls[0]["operation_id"]
                        == finality_calls[1]["operation_id"]
                    )
                if scenario == "finality_rollback_retry":
                    assert len(finality_calls) == 4
                    assert (
                        finality_calls[0]["database_transaction_id"]
                        != finality_calls[2]["database_transaction_id"]
                    )
                    assert (
                        finality_calls[0]["attempt_id"]
                        != finality_calls[2]["attempt_id"]
                    )
                    assert (
                        finality_calls[0]["operation_id"]
                        != finality_calls[2]["operation_id"]
                    )
            assert await admin.fetchval(
                "SELECT sign_count FROM "
                "ctt_staff_private.review_credentials WHERE "
                "credential_id=$1",
                credential,
            ) == (0 if platform else 4) + expected * int(not platform) + int(
                scenario == "counter"
            )
            assert await admin.fetchval(
                "SELECT backup_state FROM "
                "ctt_staff_private.review_credentials "
                "WHERE credential_id=$1",
                credential,
            ) is (platform and not denied)
            consumption = await admin.fetchrow(
                "SELECT auth_profile,auth_policy_hash,backup_eligible,"
                "backup_state,prior_counter,new_counter FROM "
                "ctt_staff_private.review_challenge_consumptions "
                "WHERE challenge_id=$1",
                challenge,
            )
            if denied:
                assert consumption is None
            else:
                assert dict(consumption) == dict(
                    auth_profile=(
                        PLATFORM_PROFILE if platform else "device-counter-v1"
                    ),
                    auth_policy_hash=(PLATFORM_POLICY_HASH if platform else None),
                    backup_eligible=platform,
                    backup_state=platform,
                    prior_counter=0 if platform else 4,
                    new_counter=0 if platform else 5,
                )
            assert await admin.fetchval(
                "SELECT count(*) FROM " "ctt_staff_private.review_authenticator_audit"
            ) == 1 + int(platform and not denied)
            if platform and not denied:
                changed = await admin.fetchrow(
                    "SELECT old_backup_state,new_backup_state,"
                    "new_policy_hash,recorded_by FROM "
                    "ctt_staff_private.review_authenticator_audit "
                    "WHERE old_backup_state=false"
                )
                assert dict(changed) == dict(
                    old_backup_state=False,
                    new_backup_state=True,
                    new_policy_hash=PLATFORM_POLICY_HASH,
                    recorded_by="ctt_full_writer",
                )
            assert len(snapshots) == 2 + int(scenario == "finality_rollback_retry")
            assert all(snapshot.pdf.closed for snapshot in snapshots)
            # Issuance commits before capture, and survives a later capture
            # rollback. It never consumes the review challenge on its own.
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_assertion_issuances"
                )
                == 1
            )
            if not denied:
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM "
                        "ctt_staff_private.review_assertion_issuances i JOIN "
                        "ctt_staff_private.signed_assertion_guards g "
                        "ON g.challenge_id=i.challenge_id "
                        "WHERE i.proof_sha256=g.proof_sha256"
                    )
                    == 1
                )
            assert await admin.fetchval(
                "SELECT count(*) FROM ctt_staff_private.transport_nonces"
            ) == 3 + int(scenario in {"revoked", "version", "counter"}) + int(
                not denied
            ) + int(
                scenario == "finality_rollback_retry"
            )
        finally:
            if issuer_http is not None:
                await issuer_http.aclose()
            if capture_pool is not None:
                await capture_pool.close()
            if nonce_pool is not None:
                await nonce_pool.close()
            if issuer_pool is not None:
                await issuer_pool.close()
            if source_pool is not None:
                await source_pool.close()
            if journal is not None:
                journal.close()
            await admin.close()

    asyncio.run(run())
