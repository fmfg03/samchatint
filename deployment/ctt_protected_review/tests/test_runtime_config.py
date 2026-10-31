"""Sealed synthetic startup settings; never open an operational database.

Functional assembly with real PostgreSQL is a separate acceptance cohort.
"""

import base64
import copy
import importlib
import json
import os
from pathlib import Path
import secrets
import sys
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
