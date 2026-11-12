"""Sealed synthetic startup settings; never open an operational database.

Functional assembly with real PostgreSQL is a separate acceptance cohort.
"""

import base64
import copy
import hashlib
import importlib
import json
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deployment.ctt_protected_review import runtime


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture
def configured(tmp_path):
    tmp_path.chmod(0o700)
    counter = 0

    def private(raw):
        nonlocal counter
        counter += 1
        file = tmp_path / f"private-{counter}"
        file.write_bytes(raw)
        file.chmod(0o600)
        return str(file)

    def key():
        return private(secrets.token_bytes(32))

    def registry():
        signer = Ed25519PrivateKey.generate()
        return {
            "synthetic": base64.b64encode(
                signer.public_key().public_bytes_raw()
            ).decode()
        }

    def dsn(name):
        password = secrets.token_hex(16)
        return private(
            f"postgresql://{name}:{password}@127.0.0.1:55492/isolated_config".encode()
        )

    policy = dict(
        workflow_id="samchat_registration_identity_v2",
        policy_id="samchat_registration_identity_policy_v2",
        policy_version="2.0.0",
        effective_sal="SAL-3",
        field_order=["full_name", "birth_date", "curp"],
        minimum_effective_pixels=12000,
        allowed_roles=["coordinador"],
        minimum_roster_size=16,
        maximum_roster_size=35,
        required_staff_slots=[1, 2],
        required_photo_kinds=["PLAYER"],
    )
    import hashlib

    policy["policy_hash"] = "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()
    policyfile = private(canonical(policy))
    source, photo = tmp_path / "source", tmp_path / "photo"
    source.mkdir(mode=0o700)
    photo.mkdir(mode=0o700)
    common = dict(
        schema="ctt.protected-runtime.v1",
        expected_uid=os.geteuid(),
        public_uid=os.geteuid() + 10000,
    )
    configs = {}
    for role in runtime.ROLE_FIELDS:
        value = {**common, "role": role}
        for name in runtime.ROLE_FIELDS[role]:
            if name.endswith("_dsn_file"):
                value[name] = dsn("ctt_synthetic_" + name[:-9])
            elif name.endswith("_key_file"):
                value[name] = key()
            elif name.endswith("_public_keys"):
                value[name] = registry()
        configs[role] = value
    configs["issuer"].update(port=8022, signing_kid="synthetic")
    configs["bus"].update(
        port=8024, signing_kid="synthetic", journal_path=str(tmp_path / "bus.jsonl")
    )
    for role in ("bus", "issuer"):
        seed = Path(configs[role]["signing_key_file"]).read_bytes()
        public = (
            Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        )
        if role == "bus":
            configs[role]["receipt_public_keys"] = {
                "synthetic": base64.b64encode(public).decode()
            }
    limits = dict(
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    configs["workflow"].update(
        port=8023,
        backend_pin="isolated-runtime",
        source_root=str(source),
        photo_root=str(photo),
        policy_path=policyfile,
        policy_hash=policy["policy_hash"],
        issuer_url="http://127.0.0.1:8022",
        bus_url="http://127.0.0.1:8024",
        capture_socket=str(tmp_path / "capture.sock"),
        capture_server_uid=os.geteuid() + 1,
        capture_client_gid=os.getegid(),
        signing_kid="synthetic",
        limits=limits,
        database_identity="isolated-runtime",
    )
    seed = Path(configs["workflow"]["signing_key_file"]).read_bytes()
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    configs["workflow"]["gate_public_keys"] = {
        "synthetic": base64.b64encode(public).decode()
    }
    configs["writer"].update(
        source_root=str(source),
        photo_root=str(photo),
        finality_url="http://127.0.0.1:8023",
        database_identity="isolated-runtime",
        allowed_client_uid=os.geteuid() + 1,
        capture_socket=str(tmp_path / "capture.sock"),
        client_gid=os.getegid(),
        limits=limits,
    )
    return configs, private


@pytest.mark.parametrize("role", ["issuer", "workflow", "writer", "bus"])
def test_valid_explicit_local_configuration(configured, role):
    configs, private = configured
    config = configs[role]
    assert set(config) == runtime.COMMON | runtime.ROLE_FIELDS[role]
    loaded = runtime.load_config(Path(private(canonical(config))))
    assert loaded == config


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o777])
def test_private_json_mode_is_owner_only(configured, mode):
    configs, private = configured
    path = Path(private(canonical(configs["issuer"])))
    path.chmod(mode)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(path)


@pytest.mark.parametrize(
    "attack",
    [
        "parentmode",
        "filelink",
        "parentlink",
        "hardlink",
        "empty",
        "oversize",
        "relative",
        "directory",
    ],
)
def test_private_file_path_boundary(configured, tmp_path, attack):
    configs, private = configured
    path = Path(private(canonical(configs["issuer"])))
    if attack == "parentmode":
        tmp_path.chmod(0o755)
    if attack == "filelink":
        other = tmp_path / "symlink"
        other.symlink_to(path)
        path = other
    if attack == "parentlink":
        other = tmp_path.parent / (tmp_path.name + "-symlink")
        other.symlink_to(tmp_path)
        path = other / path.name
    if attack == "hardlink":
        os.link(path, tmp_path / "hardlink")
    if attack == "empty":
        path.write_bytes(b"")
    if attack == "oversize":
        path.write_bytes(b"x" * 65537)
    if attack == "relative":
        path = Path("relative-private-config")
    if attack == "directory":
        path = tmp_path
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(path)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"role":"issuer","role":"bus"}',
        b"[]",
        b"null",
        b'{"v":NaN}',
        b'{"v":Infinity}',
        b"\xff",
        pytest.param(
            b'{"x":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}",
            id="deep_json",
        ),
    ],
)
def test_runtime_json_rejects_ambiguous_or_unbounded_values(raw):
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime._json(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown"),
        ("role", "unknown"),
        ("extra", True),
        ("expected_uid", True),
        ("expected_uid", -1),
        ("expected_uid", "0"),
        ("public_uid", True),
        ("public_uid", -1),
        ("port", True),
        ("port", 80),
        ("port", 65536),
        ("port", 8022.0),
    ],
)
def test_exact_schema_types_and_uid_contract(configured, field, value):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    config[field] = value
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


def test_public_process_cannot_be_private_identity(configured):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    config["public_uid"] = config["expected_uid"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


def test_expected_uid_must_match_current_process(configured):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    config["expected_uid"] += 1
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize(
    "missing", ["nonce_dsn_file", "issuer_dsn_file", "signing_key_file", "signing_kid"]
)
def test_role_never_infers_required_fields(configured, missing):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    del config[missing]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_source_bytes", True),
        ("max_asset_bytes", 0),
        ("max_manifest_bytes", 536870913),
        ("extra", 1),
    ],
)
def test_source_limits_are_explicit_positive_bounded_integers(configured, field, value):
    configs, private = configured
    config = copy.deepcopy(configs["writer"])
    config["limits"][field] = value
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize("length", [0, 1, 31, 33, 64, 65])
def test_keys_are_exact_32_raw_bytes(configured, length):
    _, private = configured
    path = private(secrets.token_bytes(length))
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime._key(path)


@pytest.mark.parametrize(
    "registry",
    [
        {},
        [],
        {"kid": "%%%"},
        {"kid": base64.b64encode(b"x" * 31).decode()},
        {"": "eA=="},
        {"kid": 5},
    ],
)
def test_public_key_registry_is_explicit_and_bounded(registry):
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime._public(registry)


def test_runtime_import_has_no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import attempted an effect")

    with monkeypatch.context() as patch:
        patch.setattr(runtime.asyncpg, "create_pool", forbidden)
        patch.setattr(runtime.httpx, "Client", forbidden)
        patch.setattr(runtime.httpx, "AsyncClient", forbidden)
        patch.setattr(runtime.os, "open", forbidden)
        importlib.reload(runtime)


@pytest.mark.parametrize("role", ["issuer", "workflow", "writer", "bus"])
def test_check_mode_opens_no_database_and_prints_no_private_values(
    configured, role, monkeypatch, capsys
):
    configs, private = configured
    path = Path(private(canonical(configs[role])))
    forbidden = AsyncMock(side_effect=AssertionError("check opened a database"))
    monkeypatch.setattr(runtime.asyncpg, "create_pool", forbidden)
    monkeypatch.setattr(sys, "argv", ["ctt-runtime", "--config", str(path), "--check"])
    runtime.main()
    assert json.loads(capsys.readouterr().out) == {
        "configuration": "valid",
        "role": role,
    }
    assert forbidden.call_count == 0


@pytest.mark.parametrize(
    "attack", ["bad_key", "policy", "remote_url", "uid_bool", "bad_public", "bad_dsn"]
)
def test_invalid_local_config_fails_before_any_database(
    configured, monkeypatch, attack
):
    configs, private = configured
    config = copy.deepcopy(configs["workflow"])
    if attack == "bad_key":
        Path(config["review_key_file"]).write_bytes(b"bad")
    if attack == "policy":
        policy = json.loads(Path(config["policy_path"]).read_bytes())
        policy["minimum_roster_size"] = 17
        Path(config["policy_path"]).write_bytes(canonical(policy))
    if attack == "remote_url":
        config["bus_url"] = "http://example.org:8024"
    if attack == "uid_bool":
        config["capture_server_uid"] = True
    if attack == "bad_public":
        config["gate_public_keys"] = {}
    if attack == "bad_dsn":
        Path(config["nonce_dsn_file"]).write_bytes(b"not-a-dsn")
    forbidden = AsyncMock(
        side_effect=AssertionError("invalid configuration opened database")
    )
    monkeypatch.setattr(runtime.asyncpg, "create_pool", forbidden)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))
    assert forbidden.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["wrong_uid", "extra_field", "unknown_role", "missing_schema"]
)
async def test_build_api_rechecks_common_contract_before_pool(
    configured, monkeypatch, attack
):
    configs, _ = configured
    config = copy.deepcopy(configs["issuer"])
    if attack == "wrong_uid":
        config["expected_uid"] += 1
    if attack == "extra_field":
        config["browser_verified"] = True
    if attack == "unknown_role":
        config["role"] = "unknown"
    if attack == "missing_schema":
        del config["schema"]
    forbidden = AsyncMock(side_effect=AssertionError("build opened a database"))
    monkeypatch.setattr(runtime.asyncpg, "create_pool", forbidden)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        async with runtime.build(config):
            pytest.fail("invalid config produced service")
    assert forbidden.call_count == 0


@pytest.mark.parametrize(
    "attack",
    [
        "key_reuse",
        "signature_domains",
        "public_writer",
        "public_workflow",
        "bad_gid",
        "invalid_path",
        "missing_key",
        "wrong_policy_pin",
    ],
)
def test_local_key_and_private_peer_separation(configured, attack):
    configs, private = configured
    config = copy.deepcopy(configs["workflow"])
    if attack == "key_reuse":
        config["effect_key_file"] = config["review_key_file"]
    if attack == "signature_domains":
        config["assertion_public_keys"] = config["receipt_public_keys"]
    if attack == "public_writer":
        config["capture_server_uid"] = config["public_uid"]
    if attack == "public_workflow":
        config = copy.deepcopy(configs["writer"])
        config["allowed_client_uid"] = config["public_uid"]
    if attack == "bad_gid":
        config["capture_client_gid"] = True
    if attack == "invalid_path":
        config["source_root"] = "relative"
    if attack == "missing_key":
        config["effect_key_file"] = str(
            Path(config["effect_key_file"]).parent / "missing"
        )
    if attack == "wrong_policy_pin":
        config["policy_hash"] = "sha256:" + "0" * 64
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "wrong_session", "connect_failure"])
async def test_pool_lifecycle_binds_exact_session_and_masks_credentials(
    configured, monkeypatch, case
):
    from contextlib import AsyncExitStack

    configs, _ = configured
    path = configs["issuer"]["issuer_dsn_file"]
    credential = Path(path).read_text()
    expected = runtime.urlsplit(credential).username

    class Connection:
        async def fetchval(self, query):
            assert query == "SELECT session_user"
            return expected if case == "success" else "other_service"

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            pass

    class Pool:
        closed = False

        def acquire(self):
            return Acquire()

        async def close(self):
            self.closed = True

    pool = Pool()

    async def create(*args, **kwargs):
        if case == "connect_failure":
            raise RuntimeError(credential)
        return pool

    monkeypatch.setattr(runtime.asyncpg, "create_pool", create)
    if case == "success":
        async with AsyncExitStack() as stack:
            assert await runtime._pool(stack, path) is pool
            assert pool.closed is False
        assert pool.closed is True
    else:
        with pytest.raises(runtime.RuntimeConfigurationDenied) as exc:
            async with AsyncExitStack() as stack:
                await runtime._pool(stack, path)
        assert credential not in str(exc.value)
        if case == "wrong_session":
            assert pool.closed is True


@pytest.mark.parametrize(
    "role,field,registry",
    [
        ("workflow", "finality_channel_key_file", "gate_public_keys"),
        ("writer", "finality_channel_key_file", "receipt_public_keys"),
        ("bus", "channel_key_file", "receipt_public_keys"),
    ],
)
def test_public_key_bytes_cannot_be_private_mac_material(
    configured, role, field, registry
):
    configs, private = configured
    config = copy.deepcopy(configs[role])
    raw = base64.b64decode(next(iter(config[registry].values())))
    Path(config[field]).write_bytes(raw)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize(
    "field,value", [("minimum_effective_pixels", True), ("minimum_roster_size", 1)]
)
def test_honest_hash_does_not_admit_invalid_operational_policy(
    configured, field, value
):
    import hashlib

    configs, private = configured
    config = copy.deepcopy(configs["workflow"])
    policy = json.loads(Path(config["policy_path"]).read_bytes())
    policy[field] = value
    policy["policy_hash"] = (
        "sha256:"
        + hashlib.sha256(
            canonical({k: v for k, v in policy.items() if k != "policy_hash"})
        ).hexdigest()
    )
    config["policy_hash"] = policy["policy_hash"]
    Path(config["policy_path"]).write_bytes(canonical(policy))
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


def test_issuer_public_key_cannot_authenticate_its_channel(configured):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    signer = Ed25519PrivateKey.from_private_bytes(
        Path(config["signing_key_file"]).read_bytes()
    )
    Path(config["channel_key_file"]).write_bytes(signer.public_key().public_bytes_raw())
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize(
    "role,field",
    [
        ("issuer", "issuer_dsn_file"),
        ("workflow", "proposal_dsn_file"),
        ("workflow", "enrollment_dsn_file"),
        ("writer", "capture_dsn_file"),
    ],
)
def test_nonce_sql_role_is_not_a_business_service_role(configured, role, field):
    configs, private = configured
    config = copy.deepcopy(configs[role])
    config[field] = config["nonce_dsn_file"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


def test_proposal_and_enrollment_sql_roles_are_distinct(configured):
    configs, private = configured
    config = copy.deepcopy(configs["workflow"])
    config["proposal_dsn_file"] = config["enrollment_dsn_file"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


@pytest.mark.parametrize(
    "role,field", [("writer", "allowed_client_uid"), ("workflow", "capture_server_uid")]
)
def test_physical_peer_is_a_distinct_process_identity(configured, role, field):
    configs, private = configured
    config = copy.deepcopy(configs[role])
    config[field] = config["expected_uid"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.load_config(Path(private(canonical(config))))


def batch_configuration(configured, *, capture=True):
    from devnous.copa_telmex.staff_batch_contracts import digest

    configs, private = configured
    old = configs["workflow"]
    config = {
        key: copy.deepcopy(old[key])
        for key in (
            "expected_uid",
            "public_uid",
            "backend_pin",
            "source_root",
            "photo_root",
            "operations_channel_key_file",
            "finality_channel_key_file",
            "gate_identity_key_file",
            "review_key_file",
            "effect_key_file",
            "signing_kid",
            "signing_key_file",
            "gate_public_keys",
            "receipt_public_keys",
            "bus_url",
            "bus_channel_key_file",
            "limits",
            "database_identity",
            "nonce_dsn_file",
        )
    }

    def credential(name):
        return private(
            f"postgresql://ctt_batch_{name}:synthetic@127.0.0.1:55492/isolated".encode()
        )

    verifier = Ed25519PrivateKey.generate()
    issuer = Ed25519PrivateKey.generate()
    profile = {"schema": "ctt.batch.config-test.v1", "mode": "retained"}
    analysis = Path(old["source_root"]).parent / "analysis"
    analysis.mkdir(exist_ok=True, mode=0o700)
    config.update(
        schema="ctt.protected-runtime.v2",
        role="batch-workflow",
        port=8025,
        workflow_dsn_file=credential("workflow"),
        verifier_dsn_file=credential("verifier"),
        profile_path=private(canonical(profile)),
        profile_hash=digest(profile),
        analysis_root=str(analysis),
        policy_paths={old["policy_hash"][7:]: old["policy_path"]},
        capture_enabled=capture,
        retained_analysis_only=True,
        verifier_signing_kid="verifier",
        verifier_signing_key_file=private(verifier.private_bytes_raw()),
        verifier_public_keys={
            "verifier": base64.b64encode(
                verifier.public_key().public_bytes_raw()
            ).decode()
        },
        assertion_public_keys={
            "issuer": base64.b64encode(issuer.public_key().public_bytes_raw()).decode()
        },
    )
    if capture:
        config.update(
            worker_dsn_file=credential("worker"),
            batch_issuer_url="http://127.0.0.1:8022",
            batch_issuer_channel_key_file=private(secrets.token_bytes(32)),
            **{
                key: old[key]
                for key in (
                    "capture_socket",
                    "capture_server_uid",
                    "capture_client_gid",
                    "capture_channel_key_file",
                )
            },
        )
    return config


@pytest.mark.parametrize("capture", [False, True])
def test_batch_process_explicit_config_validates_without_sql(
    configured, capture, monkeypatch
):
    monkeypatch.setattr(
        runtime.asyncpg,
        "create_pool",
        lambda *a, **k: pytest.fail("local check must not connect"),
    )
    config = batch_configuration(configured, capture=capture)
    runtime.validate_local_config(config)


def live_reader_configuration(configured):
    from devnous.copa_telmex.staff_batch_contracts import digest
    from devnous.tournaments.core.ctt_batch_extraction import PIPELINE

    config = batch_configuration(configured, capture=False)
    _, private = configured
    layout = b'{"synthetic_layout":true}'
    model = "synthetic-pinned-2026-01-01"
    policy_path = next(iter(config["policy_paths"].values()))
    policy = json.loads(Path(policy_path).read_text())
    policy.pop("policy_hash")
    policy.update(
        policy_version="2.1.0",
        phase_binding={
            "phase": "NACIONAL",
            "tenant_id": "synthetic-tenant",
            "edition_id": "10000000-0000-0000-0000-000000000001",
            "manifest_sha256": "a" * 64,
        },
        optional_curp_kinds=["PLAYER", "STAFF"],
    )
    policy_hash = digest(policy)
    policy["policy_hash"] = "sha256:" + policy_hash
    Path(policy_path).write_bytes(canonical(policy))
    config["policy_paths"] = {policy_hash: policy_path}
    policies = {policy_hash: policy}
    profile = dict(
        layout_sha256=hashlib.sha256(layout).hexdigest(),
        policy_artifacts=policies,
        provider=dict(
            name="openai",
            model_revision=model,
            pipeline_version=PIPELINE,
            timeout_seconds=45,
            pricing_revision="synthetic-pricing-v1",
            maximum_call_units=100000,
            batch_size=16,
            max_output_tokens=1000,
            max_request_bytes=4096,
            max_analysis_bytes=4096,
            pricing=dict(
                revision="synthetic-pricing-v1",
                model_revision=model,
                currency="USD",
                unit="micro_usd",
                input_per_million_micro_usd=1000000,
                cached_input_per_million_micro_usd=100000,
                output_per_million_micro_usd=2000000,
                model_input_token_limit=20000,
            ),
        ),
    )
    cache = Path(config["analysis_root"]).parent / "provider-cache"
    cache.mkdir(mode=0o700)
    config.update(
        retained_analysis_only=False,
        profile_path=private(canonical(profile)),
        profile_hash=digest(profile),
        analysis_layout_path=private(layout),
        analysis_cache_root=str(cache),
    )
    return config, profile


def test_explicit_live_reader_settings_validate_without_any_api_or_sql(
    configured, monkeypatch
):
    config, _ = live_reader_configuration(configured)
    monkeypatch.setattr(
        runtime.asyncpg,
        "create_pool",
        lambda *a, **k: pytest.fail("local check connected"),
    )
    runtime.validate_local_config(config)


@pytest.mark.parametrize(
    "fault", ["price", "layout", "cache", "policy", "missing_paths"]
)
def test_live_reader_partial_or_unbound_settings_are_closed(configured, fault):
    from devnous.copa_telmex.staff_batch_contracts import digest

    config, profile = live_reader_configuration(configured)
    if fault == "price":
        profile["provider"].pop("pricing")
    elif fault == "layout":
        profile["layout_sha256"] = "f" * 64
    elif fault == "cache":
        Path(config["analysis_cache_root"]).chmod(0o755)
    elif fault == "policy":
        profile["policy_artifacts"] = {}
    else:
        config.pop("analysis_layout_path")
    Path(config["profile_path"]).write_bytes(canonical(profile))
    config["profile_hash"] = digest(profile)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_local_config(config)


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "flag",
        "producer",
        "profile",
        "pool",
        "policy",
        "verifier_pin",
        "issuer_pin",
        "capture_peer",
        "extra",
    ],
)
def test_batch_process_partial_or_unsafe_config_denied(configured, fault):
    config = batch_configuration(configured)
    if fault == "version":
        config["schema"] = "ctt.protected-runtime.v1"
    elif fault == "flag":
        config["capture_enabled"] = 1
    elif fault == "producer":
        config["retained_analysis_only"] = False
    elif fault == "profile":
        config["profile_hash"] = "f" * 64
    elif fault == "pool":
        config["verifier_dsn_file"] = config["workflow_dsn_file"]
    elif fault == "policy":
        config["policy_paths"] = {"f" * 64: next(iter(config["policy_paths"].values()))}
    elif fault == "verifier_pin":
        config["verifier_public_keys"] = config["gate_public_keys"]
    elif fault == "issuer_pin":
        config["assertion_public_keys"] = config["gate_public_keys"]
    elif fault == "capture_peer":
        config["capture_server_uid"] = config["expected_uid"]
    else:
        config["automatic_approval"] = True
    with pytest.raises((runtime.RuntimeConfigurationDenied, ValueError)):
        runtime.validate_local_config(config)


def test_writer_requires_complete_explicit_batch_extension(configured):
    configs, private = configured
    value = copy.deepcopy(configs["writer"])
    issuer = Ed25519PrivateKey.generate()
    value.update(
        batch_enabled=True,
        batch_source_root=value["source_root"],
        batch_assertion_public_keys={
            "batch-issuer": base64.b64encode(
                issuer.public_key().public_bytes_raw()
            ).decode()
        },
        batch_finality_url="http://127.0.0.1:8025",
        batch_finality_channel_key_file=private(secrets.token_bytes(32)),
    )
    runtime.validate_local_config(value)
    for field in runtime.BATCH_WRITER_FIELDS:
        damaged = copy.deepcopy(value)
        damaged.pop(field)
        with pytest.raises(runtime.RuntimeConfigurationDenied):
            runtime.validate_local_config(damaged)


@pytest.mark.parametrize("capture", [False, True])
def test_explicit_batch_launcher_builds_real_private_routes(
    configured, capture, monkeypatch
):
    import asyncio

    from devnous.copa_telmex.staff_batch_workflow import BatchReviewWorkflowService

    config = batch_configuration(configured, capture=capture)
    pools = []
    started = []

    async def pool(stack, path):
        # SQL accreditation is covered by the isolated PostgreSQL acceptance;
        # this checks the actual operator-config→factory→HTTP composition.
        value = object()
        pools.append(value)
        return value

    monkeypatch.setattr(runtime, "_pool", pool)
    monkeypatch.setattr(
        BatchReviewWorkflowService, "start", lambda self: started.append(self)
    )

    async def run():
        async with runtime.build(config) as app:
            paths = {route.path for route in app.routes}
            assert "/v3/staff-review/batches/{operation}" in paths
            assert ("/v3/staff-review/batch-finality/{operation}" in paths) is capture
            assert "/v2/staff-review/finality/{operation}" not in paths
            assert "/healthz" in paths
            assert len(started) == 1
            assert started[0].capture_enabled is capture
            assert len(pools) == (4 if capture else 3)

    asyncio.run(run())


@pytest.mark.parametrize(
    "secret_field",
    [
        "issuer_dsn_file",
        "issuer_signing_key_file",
        "batch_issuer_dsn_file",
        "batch_signing_key_file",
    ],
)
def test_workflow_rejects_issuer_private_fields_before_reading(
    configured, secret_field, monkeypatch
):
    config = batch_configuration(configured)
    config[secret_field] = "/never-read-independent-issuer-secret"
    monkeypatch.setattr(
        runtime,
        "_read",
        lambda *a, **k: pytest.fail(
            "shape validation must reject issuer material before reading any file"
        ),
    )
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_local_config(config)


def independent_batch_issuer_config(configured):
    configs, private = configured
    config = copy.deepcopy(configs["issuer"])
    verifier = Ed25519PrivateKey.generate()
    config.update(
        batch_enabled=True,
        batch_issuer_dsn_file=private(
            b"postgresql://ctt_batch_independent:synthetic@127.0.0.1:55492/isolated"
        ),
        batch_signing_kid="batch-issuer",
        batch_signing_key_file=private(
            Ed25519PrivateKey.generate().private_bytes_raw()
        ),
        batch_channel_key_file=private(secrets.token_bytes(32)),
        batch_verifier_public_keys={
            "verifier": base64.b64encode(
                verifier.public_key().public_bytes_raw()
            ).decode()
        },
    )
    return config


def test_independent_issuer_extension_is_explicit_and_complete(configured):
    config = independent_batch_issuer_config(configured)
    runtime.validate_local_config(config)
    for field in runtime.BATCH_ISSUER_FIELDS:
        damaged = copy.deepcopy(config)
        damaged.pop(field)
        with pytest.raises(runtime.RuntimeConfigurationDenied):
            runtime.validate_local_config(damaged)


def test_independent_issuer_launcher_owns_batch_pool_and_seed(configured, monkeypatch):
    import asyncio

    config = independent_batch_issuer_config(configured)
    opened = []

    async def pool(stack, path):
        opened.append(path)
        return object()

    monkeypatch.setattr(runtime, "_pool", pool)

    async def run():
        async with runtime.build(config) as app:
            paths = {route.path for route in app.routes}
            assert "/v3/staff-review/batch-issuer/{operation}" in paths
            assert config["batch_issuer_dsn_file"] in opened
            assert len(opened) == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["shared_pool", "shared_channel", "public_mac", "public_url", "no_url"]
)
def test_independent_issuer_config_rejects_mixed_boundaries(configured, fault):
    if fault in {"public_url", "no_url"}:
        config = batch_configuration(configured)
        if fault == "public_url":
            config["batch_issuer_url"] = "http://192.0.2.1:8022"
        else:
            config.pop("batch_issuer_url")
    else:
        config = independent_batch_issuer_config(configured)
        if fault == "shared_pool":
            config["batch_issuer_dsn_file"] = config["issuer_dsn_file"]
        elif fault == "shared_channel":
            config["batch_channel_key_file"] = config["channel_key_file"]
        else:
            Path(config["batch_channel_key_file"]).write_bytes(
                Ed25519PrivateKey.from_private_bytes(
                    Path(config["batch_signing_key_file"]).read_bytes()
                )
                .public_key()
                .public_bytes_raw()
            )
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_local_config(config)


def test_batch_template_preserves_existing_four_private_uid_boundary():
    units = Path(__file__).resolve().parents[1] / "units"

    def user(name):
        return next(
            line.split("=", 1)[1]
            for line in (units / name).read_text().splitlines()
            if line.startswith("User=")
        )

    previous = {
        name: user(f"ctt-review-{name}.service.in")
        for name in ("issuer", "workflow", "writer", "bus")
    }
    assert len(set(previous.values())) == 4
    assert user("ctt-review-batch-workflow.service.in") == previous["workflow"]
    assert previous["issuer"] != previous["workflow"]
    assert (
        "InaccessiblePaths=@CONFIG_ROOT@/issuer"
        in (units / "ctt-review-batch-workflow.service.in").read_text()
    )
