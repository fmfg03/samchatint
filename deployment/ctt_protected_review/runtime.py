"""Explicit configuration and lifecycle of the four private pilot processes.

No import opens a database, loads a credential or starts a listener. Secrets
are read only from sealed local files at operator-selected paths. The public
web process is never a supported private identity in the deployed settings.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import signal
import stat
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_capture_runtime import assemble_capture_runtime
from devnous.copa_telmex.staff_capture_transport import CaptureUnixClient
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_issuer_client import ReviewIssuerClient
from devnous.copa_telmex.staff_review_operations_channel import (
    create_staff_review_operations_app,
)
from devnous.copa_telmex.staff_review_runtime import (
    assemble_review_issuer,
    assemble_review_workflow,
)

from .evidence_channel import EvidencePublisher, create_evidence_app
from .finality_channel import (
    BatchFinalityGateRPCServer,
    BatchFinalityRPCClient,
    FinalityGateRPCServer,
    FinalityRPCClient,
    mount_finality_rpc,
)

COMMON = {"schema", "role", "expected_uid", "public_uid"}
ROLE_FIELDS = {
    "issuer": {
        "port",
        "issuer_dsn_file",
        "nonce_dsn_file",
        "signing_kid",
        "signing_key_file",
        "channel_key_file",
    },
    "bus": {
        "port",
        "nonce_dsn_file",
        "signing_kid",
        "signing_key_file",
        "channel_key_file",
        "receipt_public_keys",
        "journal_path",
    },
    "workflow": {
        "port",
        "backend_pin",
        "source_root",
        "photo_root",
        "policy_path",
        "policy_hash",
        "proposal_dsn_file",
        "enrollment_dsn_file",
        "nonce_dsn_file",
        "issuer_url",
        "issuer_channel_key_file",
        "capture_socket",
        "capture_server_uid",
        "capture_client_gid",
        "capture_channel_key_file",
        "operations_channel_key_file",
        "finality_channel_key_file",
        "gate_identity_key_file",
        "review_key_file",
        "effect_key_file",
        "signing_kid",
        "signing_key_file",
        "gate_public_keys",
        "assertion_public_keys",
        "receipt_public_keys",
        "bus_url",
        "bus_channel_key_file",
        "limits",
        "database_identity",
    },
    "writer": {
        "capture_dsn_file",
        "nonce_dsn_file",
        "source_root",
        "photo_root",
        "gate_public_keys",
        "assertion_public_keys",
        "receipt_public_keys",
        "channel_key_file",
        "review_key_file",
        "effect_key_file",
        "finality_url",
        "finality_channel_key_file",
        "database_identity",
        "allowed_client_uid",
        "capture_socket",
        "client_gid",
        "limits",
    },
}

BATCH_WRITER_FIELDS = {
    "batch_enabled",
    "batch_source_root",
    "batch_assertion_public_keys",
    "batch_finality_url",
    "batch_finality_channel_key_file",
}
BATCH_ISSUER_FIELDS = {
    "batch_enabled",
    "batch_issuer_dsn_file",
    "batch_signing_kid",
    "batch_signing_key_file",
    "batch_verifier_public_keys",
    "batch_channel_key_file",
}
BATCH_CAPTURE_FIELDS = {
    "worker_dsn_file",
    "batch_issuer_url",
    "batch_issuer_channel_key_file",
    "capture_socket",
    "capture_server_uid",
    "capture_client_gid",
    "capture_channel_key_file",
}
BATCH_ANALYSIS_FIELDS = {"analysis_layout_path", "analysis_cache_root"}
ROLE_FIELDS["batch-workflow"] = {
    "port",
    "backend_pin",
    "source_root",
    "photo_root",
    "analysis_root",
    "profile_path",
    "profile_hash",
    "policy_paths",
    "workflow_dsn_file",
    "verifier_dsn_file",
    "nonce_dsn_file",
    "capture_enabled",
    "retained_analysis_only",
    "operations_channel_key_file",
    "finality_channel_key_file",
    "gate_identity_key_file",
    "review_key_file",
    "effect_key_file",
    "signing_kid",
    "signing_key_file",
    "verifier_signing_kid",
    "verifier_signing_key_file",
    "gate_public_keys",
    "assertion_public_keys",
    "verifier_public_keys",
    "receipt_public_keys",
    "bus_url",
    "bus_channel_key_file",
    "limits",
    "database_identity",
}


class RuntimeConfigurationDenied(ValueError):
    """Private settings are unavailable or inconsistent; no values are logged."""


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _path(value):
    if type(value) is not str:
        raise RuntimeConfigurationDenied("absolute private path required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise RuntimeConfigurationDenied("absolute sealed path required")
    return path


def _read(path, *, maximum=65536, secret=True):
    path = _path(str(path))
    if secret and path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise RuntimeConfigurationDenied("credentials must remain outside source tree")
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    target = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        parent = os.fstat(directory)
        if secret and (
            stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid not in (0, os.geteuid())
        ):
            raise RuntimeConfigurationDenied("sealed private directory required")
        target = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        info = os.fstat(target)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid())
            or not 0 < info.st_size <= maximum
            or (secret and stat.S_IMODE(info.st_mode) not in (0o400, 0o600))
        ):
            raise RuntimeConfigurationDenied("sealed private file required")
        chunks, count = [], 0
        while True:
            chunk = os.read(target, min(65536, maximum + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            if count > maximum:
                raise RuntimeConfigurationDenied("file limit exceeded")
        return b"".join(chunks)
    except (OSError, ValueError):
        raise RuntimeConfigurationDenied("private file unavailable") from None
    finally:
        if target is not None:
            os.close(target)
        os.close(directory)


def _json(raw):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError
        canonical(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeConfigurationDenied("invalid runtime JSON") from None


def _validate_shape(value):
    if type(value) is not dict:
        raise RuntimeConfigurationDenied("invalid runtime settings")
    role = value.get("role")
    additional = set()
    if role == "writer" and BATCH_WRITER_FIELDS & set(value):
        additional = BATCH_WRITER_FIELDS
        if value.get("batch_enabled") is not True:
            raise RuntimeConfigurationDenied(
                "explicit batch writer enablement required"
            )
    if role == "issuer" and BATCH_ISSUER_FIELDS & set(value):
        additional = BATCH_ISSUER_FIELDS
        if value.get("batch_enabled") is not True:
            raise RuntimeConfigurationDenied(
                "explicit batch issuer enablement required"
            )
    if role == "batch-workflow":
        if (
            type(value.get("capture_enabled")) is not bool
            or type(value.get("retained_analysis_only")) is not bool
        ):
            raise RuntimeConfigurationDenied("explicit batch flags required")
        if value["capture_enabled"]:
            additional = BATCH_CAPTURE_FIELDS
        if value.get("retained_analysis_only") is False:
            additional = additional | BATCH_ANALYSIS_FIELDS
    if (
        value.get("schema")
        != (
            "ctt.protected-runtime.v2"
            if role == "batch-workflow"
            else "ctt.protected-runtime.v1"
        )
        or type(role) is not str
        or role not in ROLE_FIELDS
        or set(value) != COMMON | ROLE_FIELDS[role] | additional
        or type(value["expected_uid"]) is not int
        or type(value["public_uid"]) is not int
        or value["expected_uid"] < 0
        or value["public_uid"] < 0
        or value["expected_uid"] != os.geteuid()
        or value["expected_uid"] == value["public_uid"]
    ):
        raise RuntimeConfigurationDenied("private runtime identity mismatch")
    if role != "writer" and (
        type(value["port"]) is not int or not 1024 <= value["port"] <= 65535
    ):
        raise RuntimeConfigurationDenied("explicit loopback port required")
    for name, item in value.items():
        if name.endswith("_file") or name in {
            "source_root",
            "photo_root",
            "journal_path",
            "capture_socket",
            "policy_path",
            "profile_path",
            "analysis_root",
            "batch_source_root",
            "analysis_layout_path",
            "analysis_cache_root",
        }:
            _path(item)
    if role in {"workflow", "writer", "batch-workflow"}:
        limits = value["limits"]
        if (
            type(limits) is not dict
            or set(limits)
            != {"max_source_bytes", "max_asset_bytes", "max_manifest_bytes"}
            or any(
                type(n) is not int or not 1 <= n <= 512 * 1024 * 1024
                for n in limits.values()
            )
        ):
            raise RuntimeConfigurationDenied("explicit source limits required")
        if (
            type(value["database_identity"]) is not str
            or not value["database_identity"]
        ):
            raise RuntimeConfigurationDenied("database identity required")
    return value


def load_config(path):
    value = _json(_read(path))
    validate_local_config(value)
    return value


def _key(value):
    raw = _read(value, maximum=64)
    if len(raw) != 32:
        raise RuntimeConfigurationDenied("32-byte key required")
    return raw


def _public(value):
    if type(value) is not dict or not value:
        raise RuntimeConfigurationDenied("pinned public keys required")
    try:
        result = {
            kid: base64.b64decode(raw, validate=True) for kid, raw in value.items()
        }
        if any(
            type(kid) is not str or not kid or len(raw) != 32
            for kid, raw in result.items()
        ):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise RuntimeConfigurationDenied("invalid public key registry") from None


async def _pool(stack, file):
    raw = _read(file, maximum=4096).decode("utf-8").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.username:
        raise RuntimeConfigurationDenied(
            "explicit PostgreSQL service credential required"
        )
    try:
        pool = await asyncpg.create_pool(
            raw,
            min_size=1,
            max_size=3,
            timeout=10,
            server_settings={"statement_timeout": "60000", "lock_timeout": "5000"},
        )
        stack.push_async_callback(pool.close)
        async with pool.acquire() as connection:
            if await connection.fetchval("SELECT session_user") != parsed.username:
                raise ValueError
        return pool
    except Exception:
        raise RuntimeConfigurationDenied(
            "private PostgreSQL service unavailable"
        ) from None


def validate_local_config(config):
    """Validate files and key domains before any database/listener is opened."""
    _validate_shape(config)
    role = config["role"]
    keys = {}
    dsn_users = {}
    public_values = set()
    for name, value in config.items():
        if name.endswith("_key_file"):
            keys[name] = _key(value)
        elif name.endswith("_dsn_file"):
            try:
                raw = _read(value, maximum=4096).decode("utf-8").strip()
                parsed = urlsplit(raw)
                if (
                    parsed.scheme not in {"postgres", "postgresql"}
                    or not parsed.username
                    or "\0" in raw
                ):
                    raise ValueError
                dsn_users[name] = parsed.username
            except (ValueError, UnicodeError):
                raise RuntimeConfigurationDenied(
                    "invalid private PostgreSQL credential"
                ) from None
        elif name.endswith("_public_keys"):
            public_values.update(_public(value).values())
        elif name.endswith("_url"):
            try:
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "http"
                    or not ipaddress.ip_address(parsed.hostname).is_loopback
                    or parsed.port is None
                    or parsed.path not in ("", "/")
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError
            except (ValueError, TypeError):
                raise RuntimeConfigurationDenied(
                    "explicit loopback service URL required"
                ) from None
    if len(set(keys.values())) != len(keys):
        raise RuntimeConfigurationDenied("separate private key domains required")
    if "signing_key_file" in keys:
        public_values.add(
            Ed25519PrivateKey.from_private_bytes(keys["signing_key_file"])
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
    if set(keys.values()) & public_values:
        raise RuntimeConfigurationDenied("public values cannot be private keys")
    if "nonce_dsn_file" in dsn_users and any(
        user == dsn_users["nonce_dsn_file"]
        for name, user in dsn_users.items()
        if name != "nonce_dsn_file"
    ):
        raise RuntimeConfigurationDenied("independent transport nonce role required")
    if (
        role == "workflow"
        and dsn_users["proposal_dsn_file"] == dsn_users["enrollment_dsn_file"]
    ):
        raise RuntimeConfigurationDenied(
            "independent proposal and enrollment roles required"
        )
    for name in (
        "capture_server_uid",
        "capture_client_gid",
        "allowed_client_uid",
        "client_gid",
    ):
        if name in config and (type(config[name]) is not int or config[name] < 0):
            raise RuntimeConfigurationDenied("explicit private peer identity required")
    if role == "issuer" and config.get("batch_enabled"):
        if len(set(dsn_users.values())) != len(dsn_users):
            raise RuntimeConfigurationDenied(
                "separate independent issuer service roles required"
            )
        if (
            type(config["batch_signing_kid"]) is not str
            or not config["batch_signing_kid"]
        ):
            raise RuntimeConfigurationDenied("batch issuer key id required")
        verifier_pins = _public(config["batch_verifier_public_keys"])
        batch_public = (
            Ed25519PrivateKey.from_private_bytes(keys["batch_signing_key_file"])
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        if batch_public in verifier_pins.values() or batch_public in keys.values():
            raise RuntimeConfigurationDenied(
                "independent batch issuer signing domain required"
            )
    if role == "batch-workflow":
        _validate_batch_local(config, keys, dsn_users)
    if role == "writer" and config.get("batch_enabled"):
        batch_domains = set(_public(config["batch_assertion_public_keys"]).values())
        if batch_domains & (
            set(_public(config["gate_public_keys"]).values())
            | set(_public(config["receipt_public_keys"]).values())
        ):
            raise RuntimeConfigurationDenied(
                "independent batch signature domains required"
            )
    if role == "workflow":
        if config["capture_server_uid"] in (
            config["public_uid"],
            config["expected_uid"],
        ):
            raise RuntimeConfigurationDenied(
                "independent capture process identity required"
            )
        policy = _json(_read(config["policy_path"], secret=False))
        if (
            policy.get("policy_hash") != config["policy_hash"]
            or config["policy_hash"]
            != "sha256:"
            + hashlib.sha256(
                canonical({k: v for k, v in policy.items() if k != "policy_hash"})
            ).hexdigest()
        ):
            raise RuntimeConfigurationDenied("policy pin mismatch")
        try:
            from staff_capture_gate_v2 import StaffCaptureGateV2

            StaffCaptureGateV2._validate_policy(policy, config["policy_hash"])
            minimum, maximum = (
                policy["minimum_roster_size"],
                policy["maximum_roster_size"],
            )
            if (
                type(minimum) is not int
                or type(maximum) is not int
                or not 16 <= minimum <= maximum <= 35
            ):
                raise ValueError
        except Exception:
            raise RuntimeConfigurationDenied(
                "invalid accredited policy structure"
            ) from None
    if role == "writer" and config["allowed_client_uid"] in (
        config["public_uid"],
        config["expected_uid"],
    ):
        raise RuntimeConfigurationDenied("independent workflow peer required")
    if role in {"workflow", "writer", "batch-workflow"}:
        registries = [
            _public(config[name])
            for name in (
                "gate_public_keys",
                "assertion_public_keys",
                "receipt_public_keys",
            )
        ]
        domains = [set(registry.values()) for registry in registries]
        if set(keys.values()) & set().union(*domains):
            raise RuntimeConfigurationDenied("public values cannot be private keys")
        if any(domains[i] & domains[j] for i in range(3) for j in range(i + 1, 3)):
            raise RuntimeConfigurationDenied("independent signature domains required")
    if role in {"workflow", "bus"}:
        registry_name = (
            "gate_public_keys" if role == "workflow" else "receipt_public_keys"
        )
        signing = Ed25519PrivateKey.from_private_bytes(keys["signing_key_file"])
        derived = signing.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if _public(config[registry_name]).get(config["signing_kid"]) != derived:
            raise RuntimeConfigurationDenied("signing identity does not match pin")


def _validate_batch_local(config, keys, dsn_users):
    from staff_capture_gate_v3 import StaffCaptureGateV3

    from devnous.copa_telmex.staff_batch_contracts import digest

    if len(set(dsn_users.values())) != len(dsn_users):
        raise RuntimeConfigurationDenied(
            "separate private batch service roles required"
        )
    profile = _json(_read(config["profile_path"], secret=False))
    if digest(profile) != config["profile_hash"]:
        raise RuntimeConfigurationDenied("batch profile pin mismatch")
    if not config["retained_analysis_only"]:
        from devnous.copa_telmex.staff_batch_analysis_runtime import (
            validate_analysis_profile,
        )

        try:
            _read(config["analysis_layout_path"], secret=False)
            validate_analysis_profile(
                profile,
                layout_path=_path(config["analysis_layout_path"]),
                cache_root=_path(config["analysis_cache_root"]),
            )
        except Exception:
            raise RuntimeConfigurationDenied(
                "external analysis configuration unavailable"
            ) from None
    policies = config["policy_paths"]
    if type(policies) is not dict or not policies:
        raise RuntimeConfigurationDenied("exact batch policies required")
    if not config["retained_analysis_only"] and set(profile["policy_artifacts"]) != set(
        policies
    ):
        raise RuntimeConfigurationDenied(
            "analysis and capture policy inventory mismatch"
        )
    for raw_hash, path in policies.items():
        policy = _json(_read(_path(path), secret=False))
        StaffCaptureGateV3._validate_policy(policy, "sha256:" + raw_hash)
        if not config["retained_analysis_only"] and canonical(
            profile["policy_artifacts"][raw_hash]
        ) != canonical(policy):
            raise RuntimeConfigurationDenied("analysis policy artifact mismatch")
        if (
            not 16
            <= policy["minimum_roster_size"]
            <= policy["maximum_roster_size"]
            <= 35
        ):
            raise RuntimeConfigurationDenied("batch roster bounds invalid")
    for key_file, kid_name, registry in (
        ("signing_key_file", "signing_kid", "gate_public_keys"),
        ("verifier_signing_key_file", "verifier_signing_kid", "verifier_public_keys"),
    ):
        public = (
            Ed25519PrivateKey.from_private_bytes(keys[key_file])
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        if _public(config[registry]).get(config[kid_name]) != public:
            raise RuntimeConfigurationDenied("batch signing identity pin mismatch")
    domains = [
        set(_public(config[name]).values())
        for name in (
            "gate_public_keys",
            "assertion_public_keys",
            "verifier_public_keys",
            "receipt_public_keys",
        )
    ]
    if any(domains[i] & domains[j] for i in range(4) for j in range(i + 1, 4)):
        raise RuntimeConfigurationDenied("independent batch signature domains required")
    if config["capture_enabled"] and config["capture_server_uid"] in (
        config["expected_uid"],
        config["public_uid"],
    ):
        raise RuntimeConfigurationDenied(
            "independent capture process identity required"
        )


async def _build_batch(stack, config, nonce_pool):
    from staff_capture_gate_v3 import StaffCaptureGateV3

    from devnous.copa_telmex.staff_batch_contracts import digest
    from devnous.copa_telmex.staff_batch_runtime import assemble_batch_review_runtime

    client = httpx.Client(trust_env=False)
    stack.callback(client.close)
    publisher = EvidencePublisher(
        base_url=config["bus_url"],
        channel_key=_key(config["bus_channel_key_file"]),
        http_client=client,
    )
    signing = Ed25519PrivateKey.from_private_bytes(_key(config["signing_key_file"]))
    gates = {}
    for raw_hash, path in config["policy_paths"].items():
        policy = _json(_read(path, secret=False))
        gates[raw_hash] = StaffCaptureGateV3(
            identity_key=_key(config["gate_identity_key_file"]),
            review_key=_key(config["review_key_file"]),
            effect_key=_key(config["effect_key_file"]),
            signing_kid=config["signing_kid"],
            signing_key=signing,
            assertion_public_keys=_public(config["assertion_public_keys"]),
            verifier_public_keys=_public(config["verifier_public_keys"]),
            receipt_public_keys=_public(config["receipt_public_keys"]),
            policy_artifact=policy,
            expected_policy_hash="sha256:" + raw_hash,
            publisher=publisher,
            max_page_bytes=config["limits"]["max_asset_bytes"],
        )
    profile = _json(_read(config["profile_path"], secret=False))
    workflow_pool = await _pool(stack, config["workflow_dsn_file"])
    verifier_pool = await _pool(stack, config["verifier_dsn_file"])
    analysis_producer = None
    if not config["retained_analysis_only"]:
        from openai import AsyncOpenAI

        from devnous.copa_telmex.staff_batch_analysis_runtime import (
            create_analysis_producer,
        )

        analysis_http = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        stack.push_async_callback(analysis_http.aclose)
        analysis_producer, analysis_sdk = create_analysis_producer(
            profile=profile,
            layout_path=_path(config["analysis_layout_path"]),
            cache_root=_path(config["analysis_cache_root"]),
            credential_reader=lambda: os.environ.get("OPENAI_API_KEY"),
            sdk_factory=AsyncOpenAI,
            http_client=analysis_http,
        )
        stack.push_async_callback(analysis_sdk.close)
    capture = {}
    if config["capture_enabled"]:
        from devnous.copa_telmex.staff_batch_issuer_channel import BatchIssuerClient

        issuer_http = httpx.AsyncClient(trust_env=False)
        stack.push_async_callback(issuer_http.aclose)
        batch_issuer_client = BatchIssuerClient(
            url=config["batch_issuer_url"],
            channel_key=_key(config["batch_issuer_channel_key_file"]),
            http_client=issuer_http,
            issuer_public_keys=_public(config["assertion_public_keys"]),
        )
        capture = dict(
            worker_pool=await _pool(stack, config["worker_dsn_file"]),
            issuer_public_keys=_public(config["assertion_public_keys"]),
            issuer_client=batch_issuer_client,
            capture_client=CaptureUnixClient(
                path=_path(config["capture_socket"]),
                key=_key(config["capture_channel_key_file"]),
                server_uid=config["capture_server_uid"],
                client_gid=config["capture_client_gid"],
                timeout=60,
            ),
        )
    runtime = assemble_batch_review_runtime(
        enabled=True,
        capture_enabled=config["capture_enabled"],
        retained_analysis_only=config["retained_analysis_only"],
        analysis_producer=analysis_producer,
        workflow_pool=workflow_pool,
        verifier_pool=verifier_pool,
        nonce_pool=nonce_pool,
        backend_pin=config["backend_pin"],
        private_source_root=_path(config["source_root"]),
        private_photo_root=_path(config["photo_root"]),
        private_analysis_root=_path(config["analysis_root"]),
        profiles={digest(profile): profile},
        profile_hash=config["profile_hash"],
        business_gates=gates,
        verifier_kid=config["verifier_signing_kid"],
        verifier_signing_key=Ed25519PrivateKey.from_private_bytes(
            _key(config["verifier_signing_key_file"])
        ),
        channel_key=_key(config["operations_channel_key_file"]),
        review_key=_key(config["review_key_file"]),
        effect_key=_key(config["effect_key_file"]),
        gate_public_keys=_public(config["gate_public_keys"]),
        **config["limits"],
        **capture,
    )
    if config["capture_enabled"]:
        finality = BatchFinalityGateRPCServer(
            gate=runtime.finality_service,
            store=runtime.service.store,
            private_source_root=_path(config["source_root"]),
            database_identity=config["database_identity"],
            **config["limits"],
        )
        mount_finality_rpc(
            runtime.app,
            service=finality,
            channel_key=_key(config["finality_channel_key_file"]),
            operations_channel_key=_key(config["operations_channel_key_file"]),
            consume_nonce=PostgresTransportNonces(nonce_pool),
        )

    @runtime.app.get("/healthz", include_in_schema=False)
    async def batch_health():
        return {"status": "ok", "role": "ctt-batch-review-workflow"}

    # Starting this loop occurs only inside an explicitly selected process build;
    # module import, local --check and factory construction never start tasks.
    runtime.start()
    stack.push_async_callback(runtime.aclose)
    return runtime.app


@asynccontextmanager
async def build(config):
    """Build only operator-selected concrete services; close resources on exit."""
    validate_local_config(config)
    logging.getLogger("fido2.server").setLevel(logging.WARNING)
    async with AsyncExitStack() as stack:
        role = config["role"]
        nonce_pool = await _pool(stack, config["nonce_dsn_file"])
        nonces = PostgresTransportNonces(nonce_pool)
        if role == "batch-workflow":
            yield await _build_batch(stack, config, nonce_pool)
            return
        if role == "issuer":
            issuer_pool = await _pool(stack, config["issuer_dsn_file"])
            signing = Ed25519PrivateKey.from_private_bytes(
                _key(config["signing_key_file"])
            )
            app = assemble_review_issuer(
                issuer_pool=issuer_pool,
                nonce_pool=nonce_pool,
                signing_kid=config["signing_kid"],
                signing_key=signing,
                channel_key=_key(config["channel_key_file"]),
            )
            if config.get("batch_enabled"):
                from staff_batch_issuer_v3 import PostgresBatchAssertionIssuer

                from devnous.copa_telmex.staff_batch_issuer_channel import (
                    mount_batch_issuer_rpc,
                )

                batch_pool = await _pool(stack, config["batch_issuer_dsn_file"])
                batch_issuer = PostgresBatchAssertionIssuer(
                    batch_pool,
                    signing_kid=config["batch_signing_kid"],
                    signing_key=Ed25519PrivateKey.from_private_bytes(
                        _key(config["batch_signing_key_file"])
                    ),
                    verifier_public_keys=_public(config["batch_verifier_public_keys"]),
                )
                mount_batch_issuer_rpc(
                    app,
                    issuer=batch_issuer,
                    channel_key=_key(config["batch_channel_key_file"]),
                    consume_nonce=nonces,
                )
            yield app
            return
        if role == "bus":
            from durable_event_store import DurableJSONLEventStore

            public = _public(config["receipt_public_keys"])
            signing = Ed25519PrivateKey.from_private_bytes(
                _key(config["signing_key_file"])
            )
            derived = signing.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            if public.get(config["signing_kid"]) != derived:
                raise RuntimeConfigurationDenied(
                    "Bus signing identity does not match pin"
                )
            journal = DurableJSONLEventStore(
                str(_path(config["journal_path"])), v2_public_keys=public
            )
            stack.callback(journal.close)
            yield create_evidence_app(
                journal=journal,
                signing_key=signing,
                signing_kid=config["signing_kid"],
                channel_key=_key(config["channel_key_file"]),
                consume_nonce=nonces,
            )
            return
        common = dict(
            gate_public_keys=_public(config["gate_public_keys"]),
            assertion_public_keys=_public(config["assertion_public_keys"]),
            receipt_public_keys=_public(config["receipt_public_keys"]),
            review_key=_key(config["review_key_file"]),
            effect_key=_key(config["effect_key_file"]),
        )
        if role == "writer":
            client = httpx.Client(trust_env=False)
            stack.callback(client.close)
            finality = FinalityRPCClient(
                base_url=config["finality_url"],
                channel_key=_key(config["finality_channel_key_file"]),
                http_client=client,
                **common,
            )
            capture_pool = await _pool(stack, config["capture_dsn_file"])
            batch_options = {}
            if config.get("batch_enabled"):
                batch_finality = BatchFinalityRPCClient(
                    base_url=config["batch_finality_url"],
                    channel_key=_key(config["batch_finality_channel_key_file"]),
                    http_client=client,
                    **{
                        **common,
                        "assertion_public_keys": _public(
                            config["batch_assertion_public_keys"]
                        ),
                    },
                )
                batch_options = dict(
                    batch_enabled=True,
                    batch_private_source_root=_path(config["batch_source_root"]),
                    batch_assertion_public_keys=_public(
                        config["batch_assertion_public_keys"]
                    ),
                    batch_finality_service=batch_finality,
                    request_timeout=60.0,
                )
            yield assemble_capture_runtime(
                capture_pool=capture_pool,
                nonce_pool=nonce_pool,
                private_source_root=_path(config["source_root"]),
                private_photo_root=_path(config["photo_root"]),
                channel_key=_key(config["channel_key_file"]),
                allowed_uids=frozenset({config["allowed_client_uid"]}),
                client_gid=config["client_gid"],
                finality_service=finality,
                database_identity=config["database_identity"],
                **common,
                **config["limits"],
                **batch_options,
            )
            return
        from staff_capture_gate_v2 import StaffCaptureGateV2

        from devnous.copa_telmex.staff_review_proposal_store import (
            PostgresReviewProposalStore,
        )

        policy = _json(_read(config["policy_path"], secret=False))
        if (
            policy.get("policy_hash") != config["policy_hash"]
            or config["policy_hash"]
            != "sha256:"
            + hashlib.sha256(
                canonical({k: v for k, v in policy.items() if k != "policy_hash"})
            ).hexdigest()
        ):
            raise RuntimeConfigurationDenied("policy pin mismatch")
        client = httpx.Client(trust_env=False)
        stack.callback(client.close)
        publisher = EvidencePublisher(
            base_url=config["bus_url"],
            channel_key=_key(config["bus_channel_key_file"]),
            http_client=client,
        )
        signing = Ed25519PrivateKey.from_private_bytes(_key(config["signing_key_file"]))
        if common["gate_public_keys"].get(
            config["signing_kid"]
        ) != signing.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ):
            raise RuntimeConfigurationDenied("Gate signing identity does not match pin")
        gate = StaffCaptureGateV2(
            identity_key=_key(config["gate_identity_key_file"]),
            review_key=common["review_key"],
            effect_key=common["effect_key"],
            signing_kid=config["signing_kid"],
            signing_key=signing,
            assertion_public_keys=common["assertion_public_keys"],
            receipt_public_keys=common["receipt_public_keys"],
            policy_artifact=policy,
            expected_policy_hash=config["policy_hash"],
            publisher=publisher,
            max_page_bytes=config["limits"]["max_asset_bytes"],
        )
        proposal_pool = await _pool(stack, config["proposal_dsn_file"])
        enrollment_pool = await _pool(stack, config["enrollment_dsn_file"])
        async_client = httpx.AsyncClient(trust_env=False)
        stack.push_async_callback(async_client.aclose)
        issuer = ReviewIssuerClient(
            url=config["issuer_url"],
            channel_key=_key(config["issuer_channel_key_file"]),
            http_client=async_client,
            timeout_seconds=60,
        )
        capture = CaptureUnixClient(
            path=_path(config["capture_socket"]),
            key=_key(config["capture_channel_key_file"]),
            server_uid=config["capture_server_uid"],
            client_gid=config["capture_client_gid"],
            timeout=60,
        )
        workflow = assemble_review_workflow(
            proposal_pool=proposal_pool,
            enrollment_pool=enrollment_pool,
            backend_pin=config["backend_pin"],
            issuer_client=issuer,
            business_gate=gate,
            capture_client=capture,
            private_source_root=_path(config["source_root"]),
            private_photo_root=_path(config["photo_root"]),
            policy_artifact=policy,
            expected_policy_hash=config["policy_hash"],
            gate_public_keys=common["gate_public_keys"],
            review_key=common["review_key"],
            effect_key=common["effect_key"],
            **config["limits"],
        )
        app = create_staff_review_operations_app(
            service=workflow,
            channel_key=_key(config["operations_channel_key_file"]),
            consume_nonce=nonces,
        )
        finality_server = FinalityGateRPCServer(
            gate=gate,
            store=PostgresReviewProposalStore(
                proposal_pool, backend_pin=config["backend_pin"]
            ),
            private_source_root=_path(config["source_root"]),
            database_identity=config["database_identity"],
            **config["limits"],
        )
        mount_finality_rpc(
            app,
            service=finality_server,
            channel_key=_key(config["finality_channel_key_file"]),
            operations_channel_key=_key(config["operations_channel_key_file"]),
            consume_nonce=nonces,
        )

        @app.get("/healthz", include_in_schema=False)
        async def health():
            return {"status": "ok", "role": "ctt-review-workflow"}

        yield app


async def run(config):
    async with build(config) as service:
        if config["role"] == "writer":
            stopped = asyncio.Event()
            loop = asyncio.get_running_loop()
            for item in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(item, stopped.set)
            async with service.serve(_path(config["capture_socket"])):
                await stopped.wait()
        else:
            server = uvicorn.Server(
                uvicorn.Config(
                    service,
                    host="127.0.0.1",
                    port=config["port"],
                    access_log=False,
                    log_level="warning",
                    proxy_headers=False,
                )
            )
            await server.serve()


def validate_preparation_settings(config):
    """A minimal mode of the private workflow, with no listener/capture keys."""
    required = {
        "schema",
        "role",
        "expected_uid",
        "public_uid",
        "backend_pin",
        "verifier_dsn_file",
        "capture_enabled",
        "preparation_only",
    }
    if (
        type(config) is not dict
        or set(config) != required
        or config["schema"] != "ctt.protected-runtime.v2"
        or config["role"] != "batch-workflow"
        or config["preparation_only"] is not True
        or config["capture_enabled"] is not False
        or type(config["expected_uid"]) is not int
        or type(config["public_uid"]) is not int
        or config["expected_uid"] != os.geteuid()
        or config["public_uid"] == config["expected_uid"]
        or config["public_uid"] < 0
        or type(config["backend_pin"]) is not str
        or not config["backend_pin"].strip()
    ):
        raise RuntimeConfigurationDenied(
            "explicit private preparation-only configuration required"
        )
    try:
        raw = _read(config["verifier_dsn_file"], maximum=4096).decode("utf-8").strip()
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.username
            or "\0" in raw
        ):
            raise ValueError
    except Exception:
        raise RuntimeConfigurationDenied(
            "private verifier credential unavailable"
        ) from None


def export_source_preparation(output_root, *, envelope, report, inventory, profile):
    """Publish exact private evidence for the existing offline manifest CLI."""
    from contextlib import ExitStack
    from io import BytesIO
    from uuid import NAMESPACE_URL, uuid5

    from devnous.copa_telmex.staff_batch_contracts import canonical, digest
    from devnous.copa_telmex.staff_source_admission import _open_root, _publish_file

    if (
        envelope.get("inventory_sha256") != digest(inventory)
        or envelope.get("profile_hash") != digest(profile)
        or report.get("observations_sha256") != digest(envelope)
    ):
        raise RuntimeConfigurationDenied("source observation export binding invalid")
    values = {
        "source-observations.json": envelope,
        "source-inventory.json": inventory,
        "structural-profile.json": profile,
        "source-preparation-report.json": report,
    }
    with ExitStack() as stack:
        root = _open_root(_path(str(output_root)), stack)
        for filename, value in values.items():
            raw = canonical(value)
            _publish_file(
                root,
                filename,
                BytesIO(raw),
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                uuid5(NAMESPACE_URL, "ctt-source-export:" + digest(value)),
            )


async def run_source_preparation(
    config, preparation_id, *, inventory_root, cache_root, output_root=None
):
    """Run an explicit observation job with SQL admission and no web listener."""
    from openai import AsyncOpenAI

    from devnous.copa_telmex.staff_batch_analysis_runtime import (
        create_structural_producer,
    )
    from devnous.copa_telmex.staff_batch_inventory import InventorySourceAdapter
    from devnous.copa_telmex.staff_batch_store import PostgresBatchReviewStore
    from devnous.tournaments.core.ctt_batch_structural_extraction import (
        validate_structural_profile,
    )

    validate_preparation_settings(config)
    async with AsyncExitStack() as stack:
        pool = await _pool(stack, config["verifier_dsn_file"])
        store = PostgresBatchReviewStore(pool, backend_pin=config["backend_pin"])
        context = await store.source_preparation_context(preparation_id)
        validate_structural_profile(context["profile"])
        adapter = InventorySourceAdapter(_path(str(inventory_root)))
        with adapter.open_verified_inventory(context["inventory"]) as snapshot:
            # No credential is needed to recover a completed observation.
            if context.get("status") == "completed":
                from types import SimpleNamespace

                from devnous.copa_telmex.staff_batch_source_service import (
                    PrivateBatchAnalysisCache,
                )
                from devnous.tournaments.core.ctt_batch_structural_extraction import (
                    StructuralPageAnalyzer,
                )
                from devnous.tournaments.core.ctt_openai_crop_client import (
                    OpenAICttCropClient,
                )

                cfg = context["profile"]["provider"]
                analyzer = StructuralPageAnalyzer(
                    provider=OpenAICttCropClient(
                        SimpleNamespace(),
                        model_revision=cfg["model_revision"],
                        timeout_seconds=cfg["timeout_seconds"],
                    ),
                    cache=PrivateBatchAnalysisCache(_path(str(cache_root))),
                )
                result = await analyzer.analyze(snapshot, context=context, store=store)
                if output_root is not None:
                    export_source_preparation(
                        output_root,
                        envelope=result[0],
                        report=result[1],
                        inventory=context["inventory"],
                        profile=context["profile"],
                    )
                return result
            http = httpx.AsyncClient(trust_env=False, follow_redirects=False)
            stack.push_async_callback(http.aclose)
            analyzer, sdk = create_structural_producer(
                profile=context["profile"],
                cache_root=_path(str(cache_root)),
                credential_reader=lambda: os.environ.get("OPENAI_API_KEY"),
                sdk_factory=AsyncOpenAI,
                http_client=http,
            )
            stack.push_async_callback(sdk.close)
            result = await analyzer.analyze(snapshot, context=context, store=store)
            if output_root is not None:
                export_source_preparation(
                    output_root,
                    envelope=result[0],
                    report=result[1],
                    inventory=context["inventory"],
                    profile=context["profile"],
                )
            return result


def main():
    parser = argparse.ArgumentParser(
        description="Start an explicitly configured private CTT process"
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate local settings without opening databases/listeners",
    )
    parser.add_argument(
        "--prepare-sources",
        type=UUID,
        help="Observe an operator-admitted inventory; never capture",
    )
    parser.add_argument("--inventory-root", type=Path)
    parser.add_argument("--preparation-cache-root", type=Path)
    parser.add_argument("--preparation-output-root", type=Path)
    args = parser.parse_args()
    if args.prepare_sources:
        if args.inventory_root is None or args.preparation_cache_root is None:
            parser.error(
                "source observation requires both private inventory and cache roots"
            )
        config = _json(_read(args.config))
        validate_preparation_settings(config)
        from contextlib import ExitStack

        from devnous.copa_telmex.staff_source_admission import _open_root

        with ExitStack() as check:
            _open_root(_path(str(args.inventory_root)), check)
            _open_root(_path(str(args.preparation_cache_root)), check)
            if args.preparation_output_root is not None:
                if (
                    args.preparation_output_root.absolute()
                    == args.inventory_root.absolute()
                ):
                    parser.error(
                        "observation export must use a separate private directory"
                    )
                _open_root(_path(str(args.preparation_output_root)), check)
        if args.check:
            print(
                json.dumps(
                    {
                        "configuration": "valid_local",
                        "mode": "source-preparation",
                        "runtime_admission_checked": False,
                    }
                )
            )
            return
        try:
            _, report = asyncio.run(
                run_source_preparation(
                    config,
                    args.prepare_sources,
                    inventory_root=args.inventory_root,
                    cache_root=args.preparation_cache_root,
                    output_root=args.preparation_output_root,
                )
            )
        except Exception:
            raise SystemExit(
                "Source observation failed; consult its private status"
            ) from None
        print(
            json.dumps(
                {
                    "physical_pages": report["physical_pages"],
                    "proposed_documents": report["proposed_documents"],
                    "stop_reason": report["stop_reason"],
                    "teams_created": 0,
                    "api_budget": report["api_budget"],
                }
            )
        )
        return
    if any(
        p is not None
        for p in (
            args.inventory_root,
            args.preparation_cache_root,
            args.preparation_output_root,
        )
    ):
        parser.error("observation paths require explicit --prepare-sources")
    config = load_config(args.config)
    if args.check:
        print(json.dumps({"configuration": "valid", "role": config["role"]}))
        return
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
