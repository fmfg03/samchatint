"""Real proposal ingress, crypto receipts and official completion on empty PG.

Accounts, faculty and authenticator are synthetic test identities. Source
admission uses its actual signature/CAS. No proposal, challenge, payload,
finality or capture result is inserted by the test; those belong to services.
This is technical acceptance, not a real person's device ceremony or UAT.
"""

import asyncio
import hashlib
import logging
import os
import random
import secrets
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import fitz
import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image
from test_ctt_full_runtime_postgres import (
    canonical,
)
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_full_runtime_postgres import pub, public_review_source
from test_ctt_staff_security_postgres import PORT

from devnous.copa_telmex.batch_admission import sha256_value
from devnous.copa_telmex.staff_capture_runtime import assemble_capture_runtime
from devnous.copa_telmex.staff_capture_transport import (
    CaptureTransportUncertain,
    CaptureUnixClient,
)
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_issuer_client import ReviewIssuerClient
from devnous.copa_telmex.staff_review_operations_channel import (
    StaffReviewOperationDenied,
    StaffReviewOperationsClient,
    StaffReviewOperationsUnavailable,
    create_staff_review_operations_app,
)
from devnous.copa_telmex.staff_review_runtime import (
    assemble_review_issuer,
    assemble_review_workflow,
)
from devnous.copa_telmex.staff_signed_assertion import (
    PLATFORM_POLICY_HASH,
    PLATFORM_PROFILE,
)
from devnous.copa_telmex.staff_source_admission import (
    SourceAdmissionLimits,
    admit_prepared_sources,
    prepare_source_admission,
    sign_source_admission,
)

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]


async def operational_rpc_case(
    *,
    socket,
    database,
    admin,
    scope,
    account,
    changes,
    source_root,
    photo_root,
    tmp_path,
    review_key,
    effect_key,
    channel_key,
    assertion_key,
    gate_key,
    bus_key,
    policy,
    credential,
    private,
):
    """Four real numeric-UID processes; no useradd or operational DB writes."""
    import base64
    import importlib.metadata
    import json
    import pwd
    import shutil
    import socket as sockets
    import stat
    import subprocess
    import sys
    import tempfile
    from urllib.parse import urlencode

    import fido2
    import sqlalchemy
    import staff_review_issuer_v2
    from cryptography.hazmat.primitives import serialization
    from fido2.utils import websafe_decode
    from fido2.webauthn import (
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    controller_uid, controller_gid = os.geteuid(), os.getegid()
    prefix = [] if controller_uid == 0 else ["sudo", "-n"]
    if prefix:
        assert (
            subprocess.run(prefix + ["true"], capture_output=True).returncode == 0
        ), "numeric-UID acceptance requires root or noninteractive sudo"
    roles = dict(workflow=310001, issuer=310002, writer=310003, bus=310004)
    for uid in roles.values():
        try:
            pwd.getpwuid(uid)
        except KeyError:
            continue
        pytest.fail("reserved synthetic numeric UID is already assigned")
    shared_gid = 310005
    assert len(set(roles.values())) == 4 and controller_uid not in roles.values()
    isolated = Path(tempfile.mkdtemp(prefix="ctt-runtime-uid-"))
    isolated.chmod(0o755)
    processes, logs = [], {}
    old_modes = {}
    enrollment_role_created = False
    coverage_target = os.environ.get("COVERAGE_FILE")
    active_coverage = None
    coverage_branch = False
    if coverage_target:
        import coverage

        active_coverage = coverage.Coverage.current()
        if active_coverage is not None:
            coverage_branch = active_coverage.config.branch

    def privileged(args):
        result = subprocess.run(prefix + args, capture_output=True)
        assert result.returncode == 0, "isolated credential/file setup failed"

    def own(path, uid, gid, recursive=False):
        args = ["chown"] + (["-R"] if recursive else [])
        privileged(args + [f"{uid}:{gid}", str(path)])

    def sealed(directory, name, raw):
        path = directory / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return str(path)

    def private_key(key):
        return key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )

    def public(key, kid):
        return {kid: base64.b64encode(pub(key)).decode()}

    def dsn(role):
        return (
            "postgresql://"
            + role
            + "@/"
            + database
            + "?"
            + urlencode({"host": socket, "port": PORT})
        ).encode()

    def free_port():
        with sockets.socket() as offered:
            offered.bind(("127.0.0.1", 0))
            return offered.getsockname()[1]

    bus_port, issuer_port, workflow_port = [free_port() for _ in range(3)]
    assert len({bus_port, issuer_port, workflow_port}) == 3
    gate_dir = Path(staff_review_issuer_v2.__file__).parent
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join(
            map(
                str,
                [
                    ROOT,
                    ROOT / "src",
                    gate_dir,
                    gate_dir.parent / "evidence-bus",
                    Path(fido2.__file__).parents[1],
                ],
            )
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    # Only copy already installed runtime distributions; the operational venv
    # remains untouched and no dependency is installed or upgraded.
    trace_code = """
import sys, sysconfig, pathlib, json
import deployment.ctt_protected_review.runtime
import staff_capture_gate_v2, staff_review_enrollment_v2, staff_review_issuer_v2
import evidence_receipt, durable_event_store
root = pathlib.Path(sysconfig.get_paths()['purelib'])
roots = set()
for module in list(sys.modules.values()):
    filename = getattr(module, '__file__', None)
    if filename and pathlib.Path(filename).is_relative_to(root):
        roots.add(pathlib.Path(filename).relative_to(root).parts[0])
print(json.dumps({'site': str(root), 'roots': sorted(roots)}))
"""
    traced = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", trace_code],
            cwd=ROOT,
            env=env,
        )
    )
    site_packages = Path(traced["site"])
    package_roots = set(traced["roots"]) | {
        "httpcore",
        "h11",
        "certifi",
        "websockets",
        "httptools",
        "uvloop",
        "coverage",
        "fitz",
        "pymupdf",
    }
    companions = set()
    for distribution in importlib.metadata.distributions(path=[str(site_packages)]):
        roots = {
            Path(str(item)).parts[0]
            for item in distribution.files or []
            if Path(str(item)).parts
        } - {"..", ".", "__pycache__"}
        if roots & package_roots:
            companions.update(roots)
    packages = isolated / "runtime-packages"
    packages.mkdir(mode=0o755)
    for name in sorted(package_roots | companions):
        source = site_packages / name
        if source.is_dir():
            shutil.copytree(
                source, packages / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        elif source.is_file():
            shutil.copy2(source, packages / name)
    # A caller may select an existing SQLAlchemy build through PYTHONPATH.
    # Preserve that exact imported distribution in every real child process.
    selected_sqlalchemy = importlib.metadata.distribution("SQLAlchemy")
    assert Path(
        selected_sqlalchemy.locate_file("sqlalchemy/__init__.py")
    ).resolve() == (Path(sqlalchemy.__file__).resolve())
    selected_roots = {
        Path(str(item)).parts[0]
        for item in selected_sqlalchemy.files or []
        if Path(str(item)).parts
    } - {"..", ".", "__pycache__"}
    for old in [packages / "sqlalchemy", *packages.glob("sqlalchemy-*.dist-info")]:
        if old.exists():
            shutil.rmtree(old)
    for name in sorted(selected_roots):
        source = Path(selected_sqlalchemy.locate_file(name))
        target = packages / name
        if source.is_dir():
            shutil.copytree(
                source, target, ignore=shutil.ignore_patterns("__pycache__")
            )
        elif source.is_file():
            shutil.copy2(source, target)
    privileged(["chmod", "-R", "a+rX", str(packages)])
    code = isolated / "code"
    code.mkdir(mode=0o755)
    copied_origins = {}

    def copy_exact_tree(source, target):
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        for copied in target.rglob("*"):
            if copied.is_file():
                original = source / copied.relative_to(target)
                assert (
                    hashlib.sha256(copied.read_bytes()).digest()
                    == hashlib.sha256(original.read_bytes()).digest()
                )
                copied_origins[str(copied.resolve())] = original.resolve()

    copy_exact_tree(ROOT / "src", code / "src")
    (code / "deployment").mkdir()
    copy_exact_tree(
        ROOT / "deployment/ctt_protected_review",
        code / "deployment/ctt_protected_review",
    )
    governance = code / "governance-runtime"
    (governance / "services").mkdir(parents=True)
    copied_gate = governance / "services/samchat-registration-gate"
    copied_bus = governance / "services/evidence-bus"
    copy_exact_tree(gate_dir, copied_gate)
    copy_exact_tree(gate_dir.parent / "evidence-bus", copied_bus)
    schemas = gate_dir.parent.parent / "schemas"
    if schemas.is_dir():
        copy_exact_tree(schemas, governance / "schemas")
    dependencies = code / "dependencies"
    dependencies.mkdir()
    copy_exact_tree(Path(fido2.__file__).parent, dependencies / "fido2")
    fido_metadata = Path(importlib.metadata.distribution("fido2")._path)
    copy_exact_tree(fido_metadata, dependencies / fido_metadata.name)
    privileged(["chmod", "-R", "a+rX", str(code)])
    env["PYTHONPATH"] = os.pathsep.join(
        map(str, (code, code / "src", copied_gate, copied_bus, dependencies, packages))
    )
    child_python = str(Path(sys.executable).resolve())
    assert (
        subprocess.check_output(
            [child_python, "-c", "import sqlalchemy; print(sqlalchemy.__version__)"],
            env=env,
            cwd=code,
            text=True,
        ).strip()
        == sqlalchemy.__version__
    )
    directories = {role: isolated / role for role in roles}
    for path in directories.values():
        path.mkdir(mode=0o700)
    source_copies = {}
    for role in ("workflow", "writer"):
        target = isolated / (role + "-source")
        shutil.copytree(source_root, target)
        own(target, roles[role], roles[role], recursive=True)
        source_copies[role] = target
    shared_photos = isolated / "photos"
    shared_photos.mkdir(mode=0o750)
    own(shared_photos, roles["workflow"], shared_gid)
    socket_root = isolated / "socket"
    socket_root.mkdir(mode=0o710)
    own(socket_root, roles["writer"], shared_gid)
    capture_socket = socket_root / "capture.sock"
    limits = dict(
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    specs = {}
    for role, directory in directories.items():

        def file(name, raw):
            return sealed(directory, name, raw)

        common = dict(
            schema="ctt.protected-runtime.v1",
            role=role,
            expected_uid=roles[role],
            public_uid=controller_uid,
            nonce_dsn_file=file("nonce.dsn", dsn("ctt_full_channel")),
        )
        if role == "bus":
            specs[role] = dict(
                **common,
                port=bus_port,
                signing_kid="bus",
                signing_key_file=file("bus-ed.key", private_key(bus_key)),
                channel_key_file=file("bus-channel.key", b"p" * 32),
                receipt_public_keys=public(bus_key, "bus"),
                journal_path=str(directory / "bus.jsonl"),
            )
        elif role == "issuer":
            specs[role] = dict(
                **common,
                port=issuer_port,
                issuer_dsn_file=file("issuer.dsn", dsn("ctt_full_issuer")),
                signing_kid="assertion",
                signing_key_file=file("issuer-ed.key", private_key(assertion_key)),
                channel_key_file=file("issuer-channel.key", b"h" * 32),
            )
        else:
            common.update(
                gate_public_keys=public(gate_key, "gate"),
                assertion_public_keys=public(assertion_key, "assertion"),
                receipt_public_keys=public(bus_key, "bus"),
                review_key_file=file("review.key", review_key),
                effect_key_file=file("effect.key", effect_key),
                source_root=str(source_copies[role]),
                photo_root=str(shared_photos),
                database_identity="isolated-copa",
                limits=limits,
                finality_channel_key_file=file("finality-channel.key", b"f" * 32),
                capture_socket=str(capture_socket),
            )
            if role == "writer":
                specs[role] = dict(
                    **common,
                    capture_dsn_file=file("capture.dsn", dsn("ctt_full_writer")),
                    channel_key_file=file("capture-channel.key", channel_key),
                    finality_url=f"http://127.0.0.1:{workflow_port}",
                    allowed_client_uid=roles["workflow"],
                    client_gid=shared_gid,
                )
            else:
                specs[role] = dict(
                    **common,
                    port=workflow_port,
                    backend_pin="synthetic",
                    policy_path=file("policy.json", canonical(policy)),
                    policy_hash=policy["policy_hash"],
                    proposal_dsn_file=file("proposal.dsn", dsn("ctt_ingress_service")),
                    enrollment_dsn_file=file(
                        "enrollment.dsn", dsn("ctt_enrollment_service")
                    ),
                    issuer_url=f"http://127.0.0.1:{issuer_port}",
                    issuer_channel_key_file=file("issuer-channel.key", b"h" * 32),
                    capture_server_uid=roles["writer"],
                    capture_client_gid=shared_gid,
                    capture_channel_key_file=file("capture-channel.key", channel_key),
                    operations_channel_key_file=file(
                        "operations-channel.key", b"o" * 32
                    ),
                    gate_identity_key_file=file("gate-identity.key", b"i" * 32),
                    signing_kid="gate",
                    signing_key_file=file("gate-ed.key", private_key(gate_key)),
                    bus_url=f"http://127.0.0.1:{bus_port}",
                    bus_channel_key_file=file("bus-channel.key", b"p" * 32),
                )
        file("runtime.json", canonical(specs[role]))
        if coverage_target:
            file(
                "coverage.ini",
                (
                    "[run]\nsigterm=true\nparallel=true\nconcurrency=thread,greenlet\n"
                    + "branch="
                    + str(coverage_branch).lower()
                    + "\n"
                ).encode(),
            )

        own(directory, roles[role], roles[role], recursive=True)
    assert "signing_key_file" not in specs["writer"]
    assert "gate_identity_key_file" not in specs["writer"]

    def launch(role, command):
        options = dict(cwd=code, env=env, stdin=subprocess.DEVNULL)
        if controller_uid == 0:
            options.update(
                user=roles[role], group=roles[role], extra_groups=[shared_gid]
            )
            argv = command
        else:
            argv = (
                prefix
                + [
                    "setpriv",
                    f"--reuid={roles[role]}",
                    f"--regid={roles[role]}",
                    f"--groups={shared_gid}",
                    "--",
                ]
                + command
            )
        return argv, options

    async def ready(role, port=None):
        process = processes[-1][1]
        async with httpx.AsyncClient(trust_env=False) as check:
            for _ in range(200):
                if process.poll() is not None:
                    pytest.fail(
                        "private runtime exited before readiness: "
                        + role
                        + " "
                        + logs[role].read_text(errors="replace")[-3500:]
                    )
                if port is None:
                    if capture_socket.exists():
                        return
                else:
                    try:
                        response = await check.get(
                            f"http://127.0.0.1:{port}/healthz", timeout=0.3
                        )
                        if response.status_code in (200, 404):
                            return
                    except httpx.HTTPError:
                        pass
                await asyncio.sleep(0.05)
        pytest.fail("private runtime readiness timed out: " + role)

    try:
        await admin.execute("CREATE ROLE ctt_enrollment_service LOGIN")
        enrollment_role_created = True
        await admin.execute(
            "INSERT INTO ctt_staff_private.review_services"
            "(service_role,backend_pin,not_before,not_after,active) "
            "VALUES('ctt_enrollment_service','synthetic',"
            "now()-interval '1 minute',now()+interval '1 hour',true)"
        )
        await admin.execute(
            "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_enrollment_service"
        )
        for signature in (
            "current_review_service()",
            "begin_passkey_enrollment(uuid,text,uuid,uuid,bytea,integer)",
            "load_passkey_enrollment(uuid,uuid)",
            "record_passkey_enrollment_candidate(uuid,uuid,text,bytea,bytea,"
            "bigint,boolean,boolean,text)",
        ):
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                + signature
                + " TO ctt_enrollment_service"
            )
        # Only the disposable cluster is opened for numeric-UID clients. Its
        # data directory remains0700; trust authentication is fixture-local.
        for target in (Path(socket).parent, Path(socket)):
            old_modes[target] = stat.S_IMODE(target.stat().st_mode)
            privileged(["chmod", "0755", str(target)])
        # A writer process cannot read the Gate's Ed25519 key even though the
        # host-admin test controller can create and remove synthetic fixtures.
        argv, options = launch(
            "writer",
            [
                child_python,
                "-c",
                "from pathlib import Path;import sys;Path(sys.argv[1]).read_bytes()",
                specs["workflow"]["signing_key_file"],
            ],
        )
        blocked = subprocess.run(argv, **options, capture_output=True)
        assert (
            blocked.returncode != 0 and b"PermissionError" in blocked.stderr
        ), "writer UID key-read probe did not establish permission denial"
        for role, port in (
            ("bus", bus_port),
            ("issuer", issuer_port),
            ("writer", None),
            ("workflow", workflow_port),
        ):
            logs[role] = isolated / (role + ".log")
            log = logs[role].open("wb")
            command = [child_python]
            if coverage_target:
                command += [
                    "-m",
                    "coverage",
                    "run",
                    "--parallel-mode",
                    "--rcfile",
                    str(directories[role] / "coverage.ini"),
                    "--source=deployment.ctt_protected_review",
                    "--data-file",
                    str(directories[role] / "coverage"),
                ]
            command += [
                "-m",
                "deployment.ctt_protected_review.runtime",
                "--config",
                str(directories[role] / "runtime.json"),
            ]
            argv, options = launch(role, command)
            process = subprocess.Popen(
                argv, **options, stdout=log, stderr=subprocess.STDOUT
            )
            log.close()
            processes.append((role, process))
            await ready(role, port)
        async with httpx.AsyncClient(trust_env=False) as http:
            client = StaffReviewOperationsClient(
                base_url=f"http://127.0.0.1:{workflow_port}",
                channel_key=b"o" * 32,
                http_client=http,
            )
            initial = await client.view(account, scope.session_id)
            assert initial["state"] == "unpublished"
            if "phase_binding" in policy:
                assert all(
                    member["source_card"]["status"] == "verified"
                    for member in initial["players"] + initial["staff"]
                ), [
                    (kind, member["slot"], member["source_card"]["status"])
                    for kind in ("players", "staff")
                    for member in initial[kind]
                    if member["source_card"]["status"] != "verified"
                ]
                assert all(
                    member["presence_proposed"] and not member["presence_confirmed"]
                    for member in initial["staff"]
                )
                assert changes == {"confirm_present_staff": True}
            optional_photo = (
                "phase_binding" in policy and policy["required_photo_kinds"] == []
            )
            expected_photo_hashes = {}
            if optional_photo:
                pending_slots = {2, 5, 7, 9, 10, 12, 13, 15}
                assert {
                    member["slot"]
                    for member in initial["players"]
                    if member["photo_status"] == "pending"
                } == pending_slots
                for member in initial["players"]:
                    if member["slot"] in pending_slots:
                        assert member.get("photo_preview") is None
                    else:
                        assert member["photo_status"] == "photo"
                        expected_photo_hashes[member["last_name"]] = hashlib.sha256(
                            base64.b64decode(member["photo_preview"].split(",", 1)[1])
                        ).hexdigest()
                assert len(expected_photo_hashes) == 8
                assert not any(
                    item["code"].startswith("PHOTO_") for item in initial["blockers"]
                )
            view = await client.prepare(account, scope.session_id, None, changes)
            assert view["ready_to_approve"] is True, view["blockers"]
            if optional_photo:
                assert view["players"] == initial["players"]
                assert len(view["blockers"]) == 0
            if "phase_binding" in policy:
                assert view["identity_policy"] == {
                    "phase": "NACIONAL",
                    "optional_curp_kinds": ["PLAYER", "STAFF"],
                }
            challenge = await client.challenge(
                account, scope.session_id, UUID(view["proposal_id"]), True
            )
            client_data = CollectedClientData.create(
                "webauthn.get",
                websafe_decode(challenge["options"]["publicKey"]["challenge"]),
                "https://sam.chat",
                cross_origin=False,
            )
            auth = AuthenticatorData.create(
                hashlib.sha256(b"sam.chat").digest(),
                AuthenticatorData.FLAG.UP
                | AuthenticatorData.FLAG.UV
                | AuthenticatorData.FLAG.BE
                | AuthenticatorData.FLAG.BS,
                0,
            )
            assertion = AuthenticationResponse(
                raw_id=credential.credential_id,
                response=AuthenticatorAssertionResponse(
                    client_data=client_data,
                    authenticator_data=auth,
                    signature=private.sign(
                        bytes(auth) + client_data.hash, ec.ECDSA(hashes.SHA256())
                    ),
                    user_handle=b"synthetic-handle",
                ),
            )
            result = await client.assert_and_capture(
                account,
                scope.session_id,
                UUID(challenge["attempt_id"]),
                dict(assertion),
            )
            assert result["state"] == "committed"
            assert (
                await client.status(
                    account, scope.session_id, UUID(challenge["attempt_id"])
                )
                == result
            )
            assert (
                await client.assert_and_capture(
                    account, scope.session_id, UUID(challenge["attempt_id"]), None
                )
                == result
            )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.copa_telmex_players "
                "WHERE governance_state='ACTIVE'"
            )
            == 16
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.copa_telmex_team_staff "
                "WHERE governance_state='ACTIVE'"
            )
            == 2
        )
        assert (
            await admin.fetchval(
                "SELECT status FROM public.copa_telmex_registration_review_sessions "
                "WHERE id=$1",
                scope.session_id,
            )
            == "committed"
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM ctt_staff_private.capture_results"
            )
            == 1
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM ctt_staff_private.capture_finalities"
            )
            == 1
        )
        if "phase_binding" in policy:
            for table in ("copa_telmex_players", "copa_telmex_team_staff"):
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM public." + table + " WHERE curp IS NULL"
                    )
                    == 2
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM public." + table + " WHERE curp = ''"
                    )
                    == 0
                )
        if optional_photo:
            rows = await admin.fetch(
                "SELECT last_name, photo_path, photo_sha256, photo_ahash, photo_data "
                "FROM public.copa_telmex_players WHERE governance_state='ACTIVE'"
            )
            absent = [row for row in rows if row["photo_path"] is None]
            provided = [row for row in rows if row["photo_path"] is not None]
            assert len(absent) == 8 and len(provided) == 8
            for row in absent:
                assert all(
                    row[field] is None
                    for field in (
                        "photo_path",
                        "photo_sha256",
                        "photo_ahash",
                        "photo_data",
                    )
                )
            assert {
                row["last_name"]: row["photo_sha256"] for row in provided
            } == expected_photo_hashes
            assert all(
                row["photo_path"] == "ctt-photo:" + row["photo_sha256"]
                for row in provided
            )
        journal_size = subprocess.check_output(
            prefix + ["stat", "-c", "%s", str(directories["bus"] / "bus.jsonl")],
            text=True,
        )
        assert int(journal_size) > 0
    finally:
        for role, process in reversed(processes):
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), 8)
            except asyncio.TimeoutError:
                process.kill()
                await asyncio.to_thread(process.wait)
        if enrollment_role_created:
            await admin.execute("DROP OWNED BY ctt_enrollment_service")
            await admin.execute("DROP ROLE ctt_enrollment_service")
        for target, mode in old_modes.items():
            privileged(["chmod", f"{mode:o}", str(target)])
        own(isolated, controller_uid, controller_gid, recursive=True)
        isolated.chmod(0o700)
        try:
            if coverage_target:
                from coverage import CoverageData

                child_files = []
                for role in roles:
                    files = [
                        p
                        for p in directories[role].glob("coverage.*")
                        if p.name != "coverage.ini"
                    ]
                    assert files, "required private-process coverage is missing"
                    child_files.extend(files)
                mapped_files = []
                for path in child_files:
                    child = CoverageData(basename=str(path))
                    child.read()
                    mapped_path = str(path) + ".mapped"
                    mapped = CoverageData(basename=mapped_path)
                    for context in child.measured_contexts():
                        child.set_query_context(context)
                        mapped.set_context(context)
                        for filename in child.measured_files():
                            original = copied_origins.get(filename)
                            assert original is not None
                            assert hashlib.sha256(
                                Path(filename).read_bytes()
                            ).digest() == (
                                hashlib.sha256(original.read_bytes()).digest()
                            )
                            if child.has_arcs():
                                mapped.add_arcs(
                                    {str(original): child.arcs(filename) or []}
                                )
                            else:
                                mapped.add_lines(
                                    {str(original): child.lines(filename) or []}
                                )
                    mapped.write()
                    mapped_files.append(mapped_path)
                    if active_coverage is not None:
                        active_coverage.get_data().update(mapped)
                if active_coverage is not None:
                    active_coverage.save()
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "coverage",
                        "combine",
                        "--append",
                        "--data-file",
                        str(Path(coverage_target).absolute()),
                        *mapped_files,
                    ],
                    check=True,
                    capture_output=True,
                )
        finally:
            shutil.rmtree(isolated)


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "operational_rpc",
        "operational_rpc_national",
        "operational_rpc_national_optional_photo",
        "short_faculty",
        "incomplete_revision",
        "lost_issuer_reply",
        "lost_before_writer",
        "rollback_result",
        "raw_draft_forgery",
        "stale_proposal",
        "source_corruption",
    ],
)
def test_private_prepare_to_official_committed_session(
    full_pg, tmp_path, monkeypatch, scenario
):
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    import staff_review_issuer_v2
    from staff_capture_gate_v2 import StaffCaptureGateV2
    from test_ctt_staff_capture_source_adapter import candidate

    monkeypatch.syspath_prepend(
        str(Path(staff_review_issuer_v2.__file__).parents[1] / "evidence-bus")
    )
    from durable_event_store import DurableJSONLEventStore
    from evidence_receipt import issue_evidence_receipt
    from fido2.cose import ES256
    from fido2.utils import websafe_decode
    from fido2.webauthn import (
        AttestedCredentialData,
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    optional_photo = scenario == "operational_rpc_national_optional_photo"
    national = scenario in {
        "operational_rpc_national",
        "operational_rpc_national_optional_photo",
    }
    missing_photo_slots = {2, 5, 7, 9, 10, 12, 13, 15} if optional_photo else set()

    async def run():
        socket, database = full_pg
        admin = await asyncpg.connect(
            host=socket, port=int(PORT), user="postgres", database=database
        )
        pools, journal, http, operations_http = [], None, None, None
        role_created = False

        async def pool(role):
            value = await asyncpg.create_pool(
                host=socket,
                port=int(PORT),
                user=role,
                database=database,
                min_size=1,
                max_size=3,
            )
            pools.append(value)
            return value

        try:
            issuer_signatures = (
                "load_review_assertion_context(uuid)",
                (
                    "record_review_assertion_issuance(uuid,text,bigint,bigint"
                    ",text,boolean,boolean,text,bigint)"
                ),
            )
            # full_pg installs earlier migrations and then admits these exact
            # synthetic service grants. New DDL must precede that admission.
            for signature in issuer_signatures:
                await admin.execute(
                    "REVOKE EXECUTE ON FUNCTION ctt_staff_private."
                    + signature
                    + " FROM ctt_full_issuer"
                )
            for name in (
                "20261031_ctt_passkey_enrollment.sql",
                "20261031_ctt_assertion_proof_recovery.sql",
                "20261031_ctt_review_proposals.sql",
            ):
                await admin.execute((ROOT / "database/migrations" / name).read_text())
            await admin.execute(
                (
                    "GRANT EXECUTE ON FUNCTION ctt_staff_private.record_asser"
                    "tion_proof(uuid,bytea) TO "
                    "ctt_full_issuer"
                )
            )
            for signature in issuer_signatures:
                await admin.execute(
                    "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                    + signature
                    + " TO ctt_full_issuer"
                )
            locked, scope, manifest, _, _ = candidate()
            # Sixteen distinct source photos, with the existing physical slot
            # membership (first page 1..8; second page 9..16).
            document = fitz.open()
            changes = {
                "team": {
                    "name": "Ensayo Integrado",
                    "state": "Oaxaca",
                    "municipality": "Oaxaca",
                    "gender": "varonil",
                    "category": "libre",
                },
                "players": [],
                "staff": [],
            }
            players = []
            for page_index in (1, 2):
                page = document.new_page(
                    width=612 if national else 595, height=792 if national else 842
                )
                if national:
                    top, rows = (238, 5) if page_index == 1 else (115, 6)
                    for x in (46, 122, 311, 385, 565):
                        page.draw_line((x, top), (x, top + rows * 97), width=2)
                    for row in range(rows + 1):
                        page.draw_line(
                            (46, top + row * 97), (565, top + row * 97), width=2
                        )
                for local in range(8):
                    slot, y = (page_index - 1) * 8 + local + 1, 35 + local * 78
                    left, photo_x, photo_y, photo_w, photo_h = 55, 480, y, 72, 72
                    if national:
                        row = local // 2 + (1 if page_index == 1 else 0)
                        left, y = (46 if local % 2 == 0 else 311), top + row * 97
                        photo_x, photo_y, photo_w, photo_h = left + 5, y + 5, 64, 84
                    name = f"Persona Apellido{slot}"
                    curp = (
                        None
                        if national and slot <= 2
                        else f"AAAA000101HDFXXX{slot:02d}"
                    )
                    if national:
                        for offset, value in (
                            (20, name),
                            (35, "2000-01-01"),
                            (48, curp or "CURP: no proporcionada"),
                        ):
                            page.insert_text((left + 80, y + offset), value, fontsize=6)
                    else:
                        page.insert_text(
                            (60, y + 15),
                            f"{name} 2000-01-01 {curp or 'CURP: no proporcionada'}",
                            fontsize=9,
                        )
                    rng = random.Random(slot)
                    image = Image.new("RGB", (8, 8))
                    image.putdata(
                        [(v, v, v) for v in [rng.randrange(256) for _ in range(64)]]
                    )
                    encoded = BytesIO()
                    image.resize((photo_w, photo_h)).save(encoded, format="PNG")
                    if slot not in missing_photo_slots:
                        page.insert_image(
                            fitz.Rect(
                                photo_x, photo_y, photo_x + photo_w, photo_y + photo_h
                            ),
                            stream=encoded.getvalue(),
                        )
                    box = {
                        "page_index": page_index,
                        "x": 55,
                        "y": y,
                        "width": 350,
                        "height": 40,
                    }
                    changes["players"].append(
                        {
                            "slot": slot,
                            "first_name": "Persona",
                            "last_name": f"Apellido{slot}",
                            "birth_date": "2000-01-01",
                            "curp": curp,
                            "source_page_index": page_index,
                            "boxes": {
                                key: box
                                for key in ("full_name", "birth_date", "curp")
                                if curp is not None or key != "curp"
                            },
                            "photo_box": (
                                None
                                if slot in missing_photo_slots
                                else {
                                    "page_index": page_index,
                                    "x": photo_x,
                                    "y": photo_y,
                                    "width": photo_w,
                                    "height": photo_h,
                                }
                            ),
                        }
                    )
                    players.append(
                        {
                            "name": name,
                            "birth_date": "2000-01-01",
                            "curp": curp,
                            "slot_ref": f"page:{page_index}:jugador_{slot}",
                            "confidence": 0.9,
                            "needs_review": False,
                        }
                    )
                staff_slot = page_index
                staff_curp = None if national else f"BBBB800101HDFXXX0{staff_slot}"
                if not national:
                    # Legacy rectangular fixture puts staff below the roster.
                    # National staff are already drawn inside the front cards;
                    # a duplicate footer here crosses the last printed border.
                    page.insert_text(
                        (60, 730),
                        f"Tecnico Apellido{staff_slot} 1980-01-01 "
                        f"{staff_curp or 'CURP: no proporcionada'}",
                    )
                box = {
                    "page_index": page_index,
                    "x": 55,
                    "y": 705,
                    "width": 350,
                    "height": 50,
                }
                changes["staff"].append(
                    {
                        "slot": staff_slot,
                        "presence": "PRESENT",
                        "presence_confirmed": True,
                        "first_name": "Tecnico",
                        "last_name": f"Apellido{staff_slot}",
                        "birth_date": "1980-01-01",
                        "curp": staff_curp,
                        "source_page_index": page_index,
                        "boxes": {
                            key: box
                            for key in ("full_name", "birth_date", "curp")
                            if staff_curp is not None or key != "curp"
                        },
                    }
                )
            if national:
                first = document[0]
                for slot in (1, 2):
                    for offset, value in (
                        (20, f"Tecnico Apellido{slot}"),
                        (35, "1980-01-01"),
                        (48, "CURP: no proporcionada"),
                    ):
                        first.insert_text(
                            ((46 if slot == 1 else 311) + 80, 238 + offset),
                            value,
                            fontsize=6,
                        )
            pdf = document.tobytes()
            document.close()
            extraction = manifest["documents"][0]["extraction"]
            extraction["team"], extraction["players"] = changes["team"], players
            for slot, role in enumerate(("director_tecnico", "auxiliar"), 1):
                item = extraction["staff"][slot - 1]
                item.update(
                    slot_ref=f"page:{1 if national else slot}:{role}",
                    presence="present",
                    first_name="Tecnico",
                    last_name=f"Apellido{slot}",
                    birth_date="1980-01-01",
                    curp=None if national else f"BBBB800101HDFXXX0{slot}",
                )
                item["staff_entry_id"] = sha256_value(
                    {key: item[key] for key in ("role", "slot", "slot_ref", "evidence")}
                )
            if national:
                # These exact photo selections are already in the protected
                # draft. The request supplies no identity or photo rectangles.
                for player, reviewed in zip(players, changes["players"]):
                    player["photo_box"] = reviewed["photo_box"]
                changes = {"confirm_present_staff": True}
            manifest["documents"][0]["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
            source_root, original_root, photo_root = [
                tmp_path / name for name in ("sources", "originals", "photos")
            ]
            for directory in (source_root, original_root, photo_root):
                directory.mkdir(mode=0o700)
            original = original_root / manifest["documents"][0]["pdf"]
            original.write_bytes(pdf)
            original.chmod(0o600)
            source_pool = await pool("ctt_full_source")
            source_key = Ed25519PrivateKey.generate()
            limits = SourceAdmissionLimits(*([1_000_000] * 5))
            with prepare_source_admission(
                canonical(manifest), original_root, locked.edition_binding, limits
            ) as prepared:
                now = int(time.time())
                envelope = sign_source_admission(
                    prepared.plan_bytes,
                    admission_id=uuid4(),
                    issued_at=now,
                    expires_at=now + 200,
                    kid="operator",
                    private_key=source_key,
                )
                await admit_prepared_sources(
                    prepared,
                    envelope,
                    public_keys={"operator": pub(source_key)},
                    private_root=source_root,
                    pool=source_pool,
                )
                source_plan = __import__("json").loads(prepared.plan_bytes)
            scope = replace(
                scope,
                tournament_slug=locked.edition_binding.roster_slug,
                session_id=uuid5(
                    NAMESPACE_URL,
                    f"ctt-registration-document:{scope.edition_id}:{scope.document_id}",
                ),
                manifest_sha256=source_plan["manifest_sha256"],
                pdf_sha256=hashlib.sha256(pdf).hexdigest(),
                source_pages=tuple(
                    replace(page, image_sha256=admitted["image_sha256"])
                    for page, admitted in zip(
                        scope.source_pages, source_plan["documents"][0]["pages"]
                    )
                ),
            )
            scope = await public_review_source(
                admin, scope, manifest, source_root, list(range(1, 17))
            )
            # Synthetic protected identity setup. No proposal or future review
            # receipt is seeded; the real issuer will verify its actual key.
            account = uuid4()
            now = int(time.time())
            private = ec.generate_private_key(ec.SECP256R1())
            credential = AttestedCredentialData.create(
                bytes(16),
                secrets.token_bytes(32),
                ES256.from_cryptography_key(private.public_key()),
            )
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.review_principals(principa"
                    "l_id,active) VALUES($1,true)"
                ),
                scope.actor_id,
            )
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.review_credentials(credent"
                    "ial_id,principal_id,attested_credential,user_handle,auth"
                    "_profile,auth_policy_hash,backup_eligible,backup_state,s"
                    "ign_count) VALUES($1,$2,$3,$4,$5,$6,true,true,0)"
                ),
                credential.credential_id,
                scope.actor_id,
                bytes(credential),
                b"synthetic-handle",
                PLATFORM_PROFILE,
                PLATFORM_POLICY_HASH,
            )
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.review_faculties(principal"
                    "_id,tenant_id,edition_id,faculty,valid_from,valid_until)"
                    " VALUES($1,$2,$3,'capture_staff_v2',to_timestamp($4),to_"
                    "timestamp($5))"
                ),
                scope.actor_id,
                scope.tenant_id,
                scope.edition_id,
                now - 10,
                now + (90 if scenario == "short_faculty" else 600),
            )
            await admin.execute("CREATE ROLE ctt_ingress_service LOGIN")
            role_created = True
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.review_services(service_ro"
                    "le,backend_pin,not_before,not_after,active) "
                    "VALUES('ctt_ingress_service','synthetic',to_timestamp($1"
                    "),to_timestamp($2),true)"
                ),
                now - 10,
                now + 600,
            )
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.review_account_links(backe"
                    "nd_pin,account_id,tenant_id,edition_id,principal_id,acto"
                    "r_role,active) VALUES('synthetic',$1,$2,$3,$4,$5,true)"
                ),
                account,
                scope.tenant_id,
                scope.edition_id,
                scope.actor_id,
                scope.actor_role,
            )
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_ingress_service"
            )
            signatures = (
                "resolve_review_source_scope(uuid)",
                "load_review_proposal_context(uuid,uuid)",
                (
                    "register_review_proposal(uuid,uuid,uuid,uuid,bytea,bytea"
                    ",bytea,bytea,bytea,bytea,uuid)"
                ),
                "read_review_proposal(uuid,uuid,uuid)",
                "create_proposal_challenge(uuid,uuid,uuid,bytea,bigint,bigint)",
                "review_proposal_status(uuid,uuid,uuid)",
                "project_review_capture_rows(uuid,uuid,uuid,jsonb,jsonb)",
                "load_review_finality_gate_context(uuid)",
                (
                    "register_proposal_capture_payload(uuid,uuid,uuid,bytea,b"
                    "ytea,uuid,bytea,bytea,bytea)"
                ),
            )
            for signature in signatures:
                await admin.execute(
                    "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                    + signature
                    + " TO ctt_ingress_service"
                )
            review_key, effect_key, channel_key = b"r" * 32, b"e" * 32, b"c" * 32
            assertion_key, gate_key, bus_key = [
                Ed25519PrivateKey.generate() for _ in range(3)
            ]
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.effect_keys(kid,secret,not"
                    "_before,not_after) VALUES('effect',$1,to_timestamp($2),t"
                    "o_timestamp($3))"
                ),
                effect_key,
                now - 10,
                now + 600,
            )
            await admin.execute(
                (
                    "INSERT INTO ctt_staff_private.effect_writers(writer_role"
                    ",kid) VALUES('ctt_full_writer','effect')"
                )
            )
            policy = dict(
                policy_id="samchat_registration_identity_policy_v2",
                workflow_id="samchat_registration_identity_v2",
                policy_version="2.0.0",
                effective_sal="SAL-3",
                field_order=["full_name", "birth_date", "curp"],
                minimum_effective_pixels=12000,
                allowed_roles=[scope.actor_role],
                minimum_roster_size=16,
                maximum_roster_size=35,
                required_staff_slots=[1, 2],
                required_photo_kinds=[] if optional_photo else ["PLAYER"],
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
                )
            policy["policy_hash"] = (
                "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()
            )
            if scenario in {
                "operational_rpc",
                "operational_rpc_national",
                "operational_rpc_national_optional_photo",
            }:
                monkeypatch.syspath_prepend(str(ROOT))
                await operational_rpc_case(
                    socket=socket,
                    database=database,
                    admin=admin,
                    scope=scope,
                    account=account,
                    changes=changes,
                    source_root=source_root,
                    photo_root=photo_root,
                    tmp_path=tmp_path,
                    review_key=review_key,
                    effect_key=effect_key,
                    channel_key=channel_key,
                    assertion_key=assertion_key,
                    gate_key=gate_key,
                    bus_key=bus_key,
                    policy=policy,
                    credential=credential,
                    private=private,
                )
                return
            journal = DurableJSONLEventStore(
                str(tmp_path / "bus.jsonl"), v2_public_keys={"bus": pub(bus_key)}
            )

            def publish(event):
                wrapped = {
                    **event,
                    "type": event["event_type"],
                    "execution_receipt_ref": "zaubern:registration-gate:precommit",
                }
                return journal.append_idempotent_receipt(
                    wrapped,
                    lambda: issue_evidence_receipt(
                        wrapped,
                        None,
                        kid="bus",
                        alg="Ed25519",
                        ed25519_private_key=bus_key,
                    ),
                )

            gate = StaffCaptureGateV2(
                identity_key=b"i" * 32,
                review_key=review_key,
                effect_key=effect_key,
                signing_kid="gate",
                signing_key=gate_key,
                assertion_public_keys={"assertion": pub(assertion_key)},
                receipt_public_keys={"bus": pub(bus_key)},
                policy_artifact=policy,
                expected_policy_hash=policy["policy_hash"],
                publisher=publish,
                max_page_bytes=1_000_000,
            )
            writer_pool, nonce_pool, issuer_pool, proposal_pool = [
                await pool(role)
                for role in (
                    "ctt_full_writer",
                    "ctt_full_channel",
                    "ctt_full_issuer",
                    "ctt_ingress_service",
                )
            ]
            logging.getLogger("fido2.server").setLevel(logging.WARNING)
            issuer_app = assemble_review_issuer(
                issuer_pool=issuer_pool,
                nonce_pool=nonce_pool,
                signing_kid="assertion",
                signing_key=assertion_key,
                channel_key=b"h" * 32,
            )
            issuer_transport = httpx.ASGITransport(app=issuer_app)

            class LostIssuerReply(httpx.AsyncBaseTransport):
                lost = False

                async def handle_async_request(self, request):
                    response = await issuer_transport.handle_async_request(request)
                    if not self.lost:
                        self.lost = True
                        await response.aclose()
                        raise httpx.ReadError(
                            "synthetic response lost after issuer commit",
                            request=request,
                        )
                    return response

            http = httpx.AsyncClient(
                transport=(
                    LostIssuerReply()
                    if scenario == "lost_issuer_reply"
                    else issuer_transport
                ),
                base_url="http://127.0.0.1:9101",
                trust_env=False,
            )
            issuer_client = ReviewIssuerClient(
                url="http://127.0.0.1:9101", channel_key=b"h" * 32, http_client=http
            )
            runtime = assemble_capture_runtime(
                capture_pool=writer_pool,
                nonce_pool=nonce_pool,
                private_source_root=source_root,
                private_photo_root=photo_root,
                gate_public_keys={"gate": pub(gate_key)},
                assertion_public_keys={"assertion": pub(assertion_key)},
                receipt_public_keys={"bus": pub(bus_key)},
                finality_service=gate,
                database_identity="isolated-copa",
                channel_key=channel_key,
                review_key=review_key,
                effect_key=effect_key,
                allowed_uids=frozenset({os.getuid()}),
                max_source_bytes=1_000_000,
                max_asset_bytes=1_000_000,
                max_manifest_bytes=1_000_000,
            )
            sock = tmp_path / "capture.sock"
            async with runtime.serve(sock):
                capture = CaptureUnixClient(
                    path=sock, key=channel_key, server_uid=os.getuid(), timeout=60
                )
                if scenario == "lost_before_writer":
                    execute = capture.execute
                    disconnected = False

                    async def disconnected_once(*args, **kwargs):
                        nonlocal disconnected
                        if not disconnected:
                            disconnected = True
                            raise CaptureTransportUncertain(
                                "synthetic connection loss before writer"
                            )
                        return await execute(*args, **kwargs)

                    monkeypatch.setattr(capture, "execute", disconnected_once)
                workflow = assemble_review_workflow(
                    proposal_pool=proposal_pool,
                    enrollment_pool=await pool("ctt_ingress_service"),
                    backend_pin="synthetic",
                    issuer_client=issuer_client,
                    business_gate=gate,
                    capture_client=capture,
                    private_source_root=source_root,
                    private_photo_root=photo_root,
                    review_key=review_key,
                    effect_key=effect_key,
                    gate_public_keys={"gate": pub(gate_key)},
                    policy_artifact=policy,
                    expected_policy_hash=policy["policy_hash"],
                    max_source_bytes=1_000_000,
                    max_asset_bytes=1_000_000,
                    max_manifest_bytes=1_000_000,
                )
                operations_app = create_staff_review_operations_app(
                    service=workflow,
                    channel_key=b"o" * 32,
                    consume_nonce=PostgresTransportNonces(nonce_pool),
                )
                operations_http = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=operations_app),
                    base_url="http://127.0.0.1:9103",
                    trust_env=False,
                )
                client_workflow = StaffReviewOperationsClient(
                    base_url="http://127.0.0.1:9103",
                    channel_key=b"o" * 32,
                    http_client=operations_http,
                )
                initial = await client_workflow.view(account, scope.session_id)
                assert initial["state"] == "unpublished"
                assert initial["proposal_id"] is None
                assert initial["ready_to_approve"] is False
                expected = None
                if scenario == "incomplete_revision":
                    incomplete = await client_workflow.prepare(
                        account, scope.session_id, None, {}
                    )
                    assert incomplete["ready_to_approve"] is False
                    assert incomplete["blockers"]
                    expected = UUID(incomplete["proposal_id"])
                    with pytest.raises(
                        (StaffReviewOperationDenied, StaffReviewOperationsUnavailable)
                    ):
                        await client_workflow.challenge(
                            account, scope.session_id, expected, True
                        )
                view = await client_workflow.prepare(
                    account, scope.session_id, expected, changes
                )
                assert view["ready_to_approve"] is True, view["blockers"]
                page_bytes = await client_workflow.source_page(
                    account,
                    scope.session_id,
                    UUID(view["proposal_id"]),
                    1,
                )
                assert (
                    "sha256:" + hashlib.sha256(page_bytes).hexdigest()
                    == scope.source_pages[0].image_sha256
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                    )
                    == 0
                )
                if scenario in {
                    "raw_draft_forgery",
                    "stale_proposal",
                    "source_corruption",
                }:
                    if scenario == "raw_draft_forgery":
                        with pytest.raises(asyncpg.RaiseError, match="immutable"):
                            await admin.execute(
                                (
                                    "UPDATE public.copa_telmex_registration_r"
                                    "eview_drafts SET extraction='{}'::json "
                                    "WHERE id=$1"
                                ),
                                scope.draft_id,
                            )
                        assert (
                            await admin.fetchval(
                                (
                                    "SELECT count(*) FROM ctt_staff_private.c"
                                    "apture_attempt_anchors"
                                )
                            )
                            == 0
                        )
                        assert (
                            await admin.fetchval(
                                "SELECT count(*) FROM public.copa_telmex_teams"
                            )
                            == 0
                        )
                        return
                    elif scenario == "source_corruption":
                        page = scope.source_pages[0]
                        path = source_root / "pages" / (page.image_sha256[7:] + ".png")
                        # The fixture owns this sealed object. Deliberately
                        # tamper, then restore its mode before testing the hash.
                        sealed_mode = path.stat().st_mode & 0o777
                        path.chmod(0o600)
                        try:
                            path.write_bytes(b"synthetic corrupted source")
                        finally:
                            path.chmod(sealed_mode)
                    else:
                        await client_workflow.prepare(
                            account,
                            scope.session_id,
                            UUID(view["proposal_id"]),
                            changes,
                        )
                    with pytest.raises(
                        (StaffReviewOperationDenied, StaffReviewOperationsUnavailable)
                    ):
                        await client_workflow.challenge(
                            account, scope.session_id, UUID(view["proposal_id"]), True
                        )
                    assert (
                        await admin.fetchval(
                            (
                                "SELECT count(*) FROM ctt_staff_private.captu"
                                "re_attempt_anchors"
                            )
                        )
                        == 0
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_teams"
                        )
                        == 0
                    )
                    return
                challenge = await client_workflow.challenge(
                    account, scope.session_id, UUID(view["proposal_id"]), True
                )
                client = CollectedClientData.create(
                    "webauthn.get",
                    websafe_decode(challenge["options"]["publicKey"]["challenge"]),
                    "https://sam.chat",
                    cross_origin=False,
                )
                auth = AuthenticatorData.create(
                    hashlib.sha256(b"sam.chat").digest(),
                    AuthenticatorData.FLAG.UP
                    | AuthenticatorData.FLAG.UV
                    | AuthenticatorData.FLAG.BE
                    | AuthenticatorData.FLAG.BS,
                    0,
                )
                assertion = AuthenticationResponse(
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
                attempt = UUID(challenge["attempt_id"])
                if scenario == "rollback_result":
                    await admin.execute(
                        (
                            "CREATE FUNCTION public.synthetic_fail_result() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$BEGIN "
                            "RAISE EXCEPTION 'synthetic result failure'; "
                            "END$$; CREATE TRIGGER synthetic_fail_result "
                            "BEFORE INSERT ON ctt_staff_private.capture_resul"
                            "ts FOR EACH ROW EXECUTE FUNCTION "
                            "public.synthetic_fail_result()"
                        )
                    )
                if scenario == "lost_issuer_reply":
                    with pytest.raises(StaffReviewOperationsUnavailable):
                        await client_workflow.assert_and_capture(
                            account, scope.session_id, attempt, dict(assertion)
                        )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private.issuer_proofs"
                        )
                        == 1
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private.capture_payloads"
                        )
                        == 0
                    )
                    assert (
                        await client_workflow.status(account, scope.session_id, attempt)
                    )["state"] == "uncertain"
                    result = await client_workflow.assert_and_capture(
                        account, scope.session_id, attempt, None
                    )
                else:
                    result = await client_workflow.assert_and_capture(
                        account, scope.session_id, attempt, dict(assertion)
                    )
                if scenario in {"lost_before_writer", "rollback_result"}:
                    assert result["state"] == "uncertain"
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_players"
                        )
                        == 0
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_team_staff"
                        )
                        == 0
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private.capture_results"
                        )
                        == 0
                    )
                    assert (
                        await admin.fetchval(
                            (
                                "SELECT count(*) FROM ctt_staff_private.revie"
                                "w_challenge_consumptions"
                            )
                        )
                        == 0
                    )
                    assert (
                        await admin.fetchval(
                            (
                                "SELECT status FROM public.copa_telmex_regist"
                                "ration_review_sessions WHERE "
                                "id=$1"
                            ),
                            scope.session_id,
                        )
                        == "reviewing"
                    )
                    if scenario == "rollback_result":
                        await admin.execute(
                            (
                                "DROP TRIGGER synthetic_fail_result ON "
                                "ctt_staff_private.capture_results"
                            )
                        )
                    result = await client_workflow.assert_and_capture(
                        account, scope.session_id, attempt, None
                    )
                assert result["state"] == "committed"
                assert (
                    await client_workflow.assert_and_capture(
                        account, scope.session_id, UUID(challenge["attempt_id"]), None
                    )
                    == result
                )
                assert (
                    await admin.fetchval(
                        (
                            "SELECT count(*) FROM public.copa_telmex_players "
                            "WHERE governance_state='ACTIVE'"
                        )
                    )
                    == 16
                )
                assert (
                    await admin.fetchval(
                        (
                            "SELECT count(*) FROM public.copa_telmex_team_sta"
                            "ff WHERE governance_state='ACTIVE'"
                        )
                    )
                    == 2
                )
                assert (
                    await admin.fetchval(
                        (
                            "SELECT status FROM public.copa_telmex_registrati"
                            "on_review_sessions WHERE "
                            "id=$1"
                        ),
                        scope.session_id,
                    )
                    == "committed"
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.capture_results"
                    )
                    == 1
                )
        finally:
            if operations_http:
                await operations_http.aclose()
            if http:
                await http.aclose()
            if journal:
                journal.close()
            for item in reversed(pools):
                await item.close()
            if role_created:
                await admin.execute("DROP OWNED BY ctt_ingress_service")
                await admin.execute("DROP ROLE ctt_ingress_service")
            await admin.close()

    asyncio.run(run())
