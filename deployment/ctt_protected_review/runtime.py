"""Explicit configuration and lifecycle of the four private pilot processes.

No import opens a database, loads a credential or starts a listener. Secrets
are read only from sealed local files at operator-selected paths. The public
web process is never a supported private identity in the deployed settings.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import base64
import hashlib
import json
import ipaddress
import logging
import os
from pathlib import Path
import signal
import stat
from urllib.parse import urlsplit

import asyncpg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import uvicorn

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
    if (
        value.get("schema") != "ctt.protected-runtime.v1"
        or type(role) is not str
        or role not in ROLE_FIELDS
        or set(value) != COMMON | ROLE_FIELDS[role]
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
        }:
            _path(item)
    if role in {"workflow", "writer"}:
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
    if role in {"workflow", "writer"}:
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


@asynccontextmanager
async def build(config):
    """Build only operator-selected concrete services; close resources on exit."""
    validate_local_config(config)
    logging.getLogger("fido2.server").setLevel(logging.WARNING)
    async with AsyncExitStack() as stack:
        role = config["role"]
        nonce_pool = await _pool(stack, config["nonce_dsn_file"])
        nonces = PostgresTransportNonces(nonce_pool)
        if role == "issuer":
            issuer_pool = await _pool(stack, config["issuer_dsn_file"])
            signing = Ed25519PrivateKey.from_private_bytes(
                _key(config["signing_key_file"])
            )
            yield assemble_review_issuer(
                issuer_pool=issuer_pool,
                nonce_pool=nonce_pool,
                signing_kid=config["signing_kid"],
                signing_key=signing,
                channel_key=_key(config["channel_key_file"]),
            )
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
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check:
        print(json.dumps({"configuration": "valid", "role": config["role"]}))
        return
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
