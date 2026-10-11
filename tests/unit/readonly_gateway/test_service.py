"""Protected-composition and framing tests; all external effects mocked."""

import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from samchat.readonly_gateway import service
from samchat.readonly_gateway.contracts import ReadError
from samchat.readonly_gateway.delegation import DelegationFile, DelegationFileBinding
from samchat.readonly_gateway.fixtures import BASE_COMMIT
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.live_informes import (
    ACTOR,
    ReportDelegation,
    load_report_delegation,
)
from samchat.readonly_gateway.trusted_tunnel import assert_tunnel_client
from samchat.readonly_gateway.unix_bridge import (
    UnixReadServer,
    UnixReportProxy,
    _receive,
    _send,
)

BRIDGE_HASHES = {
    "executor.py": "66612837a951f2b522d65b55731629dda849277db532e3adbe3258af7513eb7a",
    "ish-manifest.json": "e80e49702994de80ecb93d430328103c06799624d86eeae3764c9f681b449fe1",
    "work_tools.py": "659dea7f04f2326d22a908dc316cfd57976fb385792db8de40fdbd77e3880ae8",
}


def bridge_fixture_bytes(directory):
    """Admitted source only: verify all three files before loading any code."""
    result = {}
    for name, expected in BRIDGE_HASHES.items():
        path = directory / (name + ".fixture")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Pinned bridge fixture missing or symlink")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Pinned bridge fixture integrity mismatch")
        result[name] = data
    return result


class ServiceTests(unittest.TestCase):
    def test_type_target_matches_declared_project_minimum(self):
        import tomllib

        root = Path(__file__).resolve().parents[3]
        config = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertEqual(config["project"]["requires-python"], ">=3.12")
        self.assertEqual(config["tool"]["mypy"]["python_version"], "3.12")

    def test_bridge_fixture_rejects_tampering_and_missing_files(self):
        active = Path(__file__).parent / "fixtures/bridge"
        admitted = bridge_fixture_bytes(active)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for damaged in BRIDGE_HASHES:
                for name, data in admitted.items():
                    (target / (name + ".fixture")).write_bytes(data)
                path = target / (damaged + ".fixture")
                path.write_bytes(admitted[damaged] + b"tampered")
                with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                    bridge_fixture_bytes(target)
                path.unlink()
                with self.assertRaisesRegex(ValueError, "missing or symlink"):
                    bridge_fixture_bytes(target)
                path.symlink_to(active / (damaged + ".fixture"))
                with self.assertRaisesRegex(ValueError, "missing or symlink"):
                    bridge_fixture_bytes(target)
                path.unlink()

    def test_ci_wires_native_slice_and_keeps_combined_coverage(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/test.yml").read_text()
        native = workflow.split(
            "- name: Run isolated readonly PostgreSQL acceptance", 1
        )[1].split("- name: Combine unit coverage", 1)[0]
        for required in (
            "/usr/lib/postgresql/16/bin",
            "mktemp -d /tmp/samchat-ro-native-ci-XXXXXXXX",
            "trap cleanup_readonly_cluster EXIT",
            "--auth-local=trust --auth-host=reject",
            "-c listen_addresses=''",
            'export SAMCHAT_TEST_SOCKET="$samchat_ro_cluster"',
            "run_offline_tests.py --native-informes",
            "coverage report --fail-under=85",
        ):
            self.assertIn(required, native)
        self.assertNotIn("sudo", native)
        self.assertNotIn("127.0.0.1", native)
        self.assertNotIn("/etc/", native)
        generic = workflow.split("- name: Run unit tests", 1)[1].split(
            "- name: Run isolated readonly PostgreSQL acceptance", 1
        )[0]
        self.assertIn("--ignore=tests/unit/readonly_gateway", generic)
        self.assertIn("--ignore=tests/unit/private_plugin", generic)
        combine = next(
            line for line in workflow.splitlines() if "coverage combine" in line
        )
        for source in (
            ".coverage.repository.unit",
            "reports/private-plugin/unit.coverage",
            "reports/readonly_gateway/unit.coverage",
        ):
            self.assertIn(source, combine)

    def test_disconnected_rejected_peer_does_not_terminate_server(self):
        for peer_uid in (os.getuid(), os.getuid() + 1):
            gateway, resolve = Mock(), Mock()
            server = UnixReadServer(
                Path("/synthetic/socket"), gateway, resolve, peer_uid
            )
            left, right = socket.socketpair()
            with left:
                right.sendall(b"invalid-json\n")
                right.close()
                server.handle(left)
            gateway.call.assert_not_called()
            resolve.assert_not_called()

    def test_rejected_uid_reply_survives_close_without_dispatch(self):
        gateway, resolve = Mock(), Mock()
        with tempfile.TemporaryDirectory(prefix="samchat-ro-denial-") as directory:
            path = Path(directory) / "readonly.sock"
            server = UnixReadServer(path, gateway, resolve, os.getuid() + 1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                listener.listen(1)

                def accept_one():
                    connection, _ = listener.accept()
                    with connection:
                        server.handle(connection)

                for _ in range(100):
                    thread = threading.Thread(target=accept_one)
                    thread.start()
                    with self.assertRaises(ReadError) as raised:
                        UnixReportProxy(path, os.getuid()).call(
                            "samchat.document_status", {"document_id": "synthetic"}
                        )
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(raised.exception.code, "AUTH_REQUIRED")
                thread = threading.Thread(target=accept_one)
                thread.start()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                    client.sendall(b"invalid-json\n")
                    self.assertEqual(_receive(client), {"error": "AUTH_REQUIRED"})
                thread.join(5)
                self.assertFalse(thread.is_alive())
        gateway.call.assert_not_called()
        resolve.assert_not_called()

    def test_patch_bootstrap_preserves_legacy_and_adds_five_read_tools(self):
        root = Path(__file__).resolve().parents[3]
        active = Path(__file__).parent / "fixtures/bridge"
        admitted = bridge_fixture_bytes(active)
        with tempfile.TemporaryDirectory(prefix="samchat-ro-patched-") as directory:
            copy = Path(directory) / "executor.py"
            for name, data in admitted.items():
                (Path(directory) / name).write_bytes(data)
            subprocess.run(
                [
                    "patch",
                    "--batch",
                    str(copy),
                    str(
                        root
                        / "deployment/readonly_gateway/agentius-executor-catalog.patch"
                    ),
                ],
                check=True,
                capture_output=True,
            )
            spec = importlib.util.spec_from_file_location(
                "isolated_readonly_bridge", copy
            )
            module = importlib.util.module_from_spec(spec)
            with patch.object(sys, "path", [directory, *sys.path]), patch.dict(
                sys.modules
            ):
                sys.modules.pop("work_tools", None)
                spec.loader.exec_module(module)
            legacy = Mock()
            legacy.tools = {**module.TOOLS, **module.WORK_TOOLS}
            server = Mock()
            with patch.object(module, "Executor", return_value=legacy), patch.object(
                module, "ThreadingHTTPServer", return_value=server
            ), patch.object(module, "WorkClient"), patch.object(
                sys,
                "argv",
                [
                    "executor",
                    "--state",
                    directory,
                    "--work-socket",
                    "/synthetic/worker.sock",
                    "--worker-uid",
                    "988",
                    "--readonly-socket",
                    "/synthetic/readonly.sock",
                ],
            ):
                module.main()
            self.assertEqual(len(server.executor.tools), len(legacy.tools) + 5)
            self.assertEqual(len(legacy.tools), 14)
            expected = {
                "samchat.resolve_reference",
                "samchat.document_status",
                "samchat.document_diagnose",
                "samchat.document_audit",
                "samchat.study",
            }
            self.assertEqual(set(server.executor.tools) - set(legacy.tools), expected)
            self.assertNotIn("samchat.source_read", server.executor.tools)
            for item in server.executor.annotated_catalog():
                if item["name"] in expected:
                    self.assertTrue(item["annotations"]["readOnlyHint"])
                    self.assertFalse(item["annotations"]["destructiveHint"])
                    self.assertFalse(item["annotations"]["openWorldHint"])
            study = next(
                item
                for item in server.executor.annotated_catalog()
                if item["name"] == "samchat.study"
            )
            self.assertEqual(
                study["inputSchema"]["properties"]["dataset"]["enum"], ["documents"]
            )
            server.serve_forever.assert_called_once()

    def test_existing_tcp_tunnel_hop_requires_exact_kernel_uid(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect(listener.getsockname())
                accepted, _ = listener.accept()
                with accepted:
                    assert_tunnel_client(accepted, os.getuid())
                    with self.assertRaises(ReadError):
                        assert_tunnel_client(accepted, os.getuid() + 1)
                    with patch(
                        "samchat.readonly_gateway.trusted_tunnel.Path.read_text",
                        side_effect=PermissionError,
                    ):
                        with self.assertRaises(ReadError):
                            assert_tunnel_client(accepted, os.getuid())

    def test_composition_is_owner_approved_and_no_db_connect_on_start(self):
        record = ReportDelegation(
            ACTOR,
            "fmfg@pm.me",
            "samchat-production",
            "fmfg-binding",
            "v1",
            datetime(2026, 11, 8, tzinfo=timezone.utc),
            "reviewed-v1",
        )
        config = {
            "activation_authorized": True,
            "delegation_sha256": "f" * 64,
            "schema_fingerprint": "f" * 64,
            "source_commit": BASE_COMMIT,
            "source_root": "/synthetic",
            "sources": {},
        }
        reader = Mock()
        reader._read.return_value = json.dumps(config).encode()
        audit = Mock()
        factory = Mock()
        with tempfile.TemporaryDirectory() as credentials:
            (Path(credentials) / "database-password").write_text(
                "synthetic-not-a-credential"
            )
            with patch.object(service.os, "getuid", return_value=988), patch.object(
                service.os, "getgid", return_value=983
            ), patch.object(
                service, "DelegationFile", return_value=reader
            ), patch.object(
                service, "load_report_delegation", return_value=record
            ), patch.object(
                service, "DailyAudit", return_value=audit
            ), patch.object(
                service.Sources,
                "from_manifest",
                return_value=Sources({"src/review.py": b"# fixture"}, BASE_COMMIT),
            ), patch.object(
                service, "AsyncpgConnectionFactory", factory
            ), patch.object(
                service, "UnixReadServer"
            ) as server, patch.dict(
                os.environ,
                {
                    "CREDENTIALS_DIRECTORY": credentials,
                    "SAMCHAT_READONLY_ACTIVATION_SHA256": "f" * 64,
                },
            ), patch(
                "sys.argv", ["service"]
            ):
                service.main()
                server.return_value.serve.assert_called_once()
                connector = factory.call_args.args[0]
                fake = SimpleNamespace(
                    connect=AsyncMock(return_value="synthetic-connection")
                )
                with patch.dict("sys.modules", {"asyncpg": fake}):
                    self.assertEqual(asyncio.run(connector()), "synthetic-connection")
                self.assertEqual(
                    fake.connect.call_args.kwargs["user"], "samchat_ro_fmfg"
                )
                with patch("sys.argv", ["service", "--prune"]):
                    service.main()
                audit.prune.assert_called_once()
                config["activation_authorized"] = False
                reader._read.return_value = json.dumps(config).encode()
                with self.assertRaises(ReadError):
                    service.main()
                reader._read.return_value = b"{}"
                with self.assertRaises(ReadError):
                    service.main()
        with patch.object(service.os, "getuid", return_value=0), patch(
            "sys.argv", ["service"]
        ):
            with self.assertRaises(ReadError):
                service.main()

    def test_dynamic_delegation_hash_expiry_actor_and_transport(self):
        data = {
            "actor_uuid": ACTOR,
            "email": "fmfg@pm.me",
            "installation": "samchat-production",
            "actor_binding": "fmfg-binding",
            "generation": "v1",
            "expires_at": "2026-11-08T23:59:59Z",
            "owner_version": "reviewed-v1",
            "scope_policy": "live_informes_v1",
            "transport_binding": "tunnel_6abc52a731608191a27c90d670fb5502",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delegation.json"
            for field, value in [
                (None, None),
                ("transport_binding", "foreign"),
                ("actor_uuid", "invalid"),
                ("expires_at", "2020-01-01T00:00:00Z"),
                ("scope_policy", "foreign"),
            ]:
                raw = dict(data)
                if field:
                    raw[field] = value
                content = json.dumps(raw).encode()
                path.write_bytes(content)
                path.chmod(0o640)
                source = DelegationFile(
                    DelegationFileBinding(
                        path,
                        hashlib.sha256(content).hexdigest(),
                        os.getuid(),
                        os.getgid(),
                    )
                )
                if field:
                    with self.assertRaises(ReadError):
                        load_report_delegation(source)
                else:
                    self.assertEqual(load_report_delegation(source).actor_uuid, ACTOR)

    def test_closed_frame_errors_and_no_oversize_delivery(self):
        for raw in [b"[]\n", b"bad\n", b"{}\n{}\n", b"x" * 65537, b""]:
            connection = Mock()
            connection.recv.return_value = raw
            with self.assertRaises(ReadError):
                _receive(connection)
        connection = Mock()
        connection.recv.side_effect = [b'{"ok":', b"true}\n"]
        self.assertEqual(_receive(connection), {"ok": True})
        with self.assertRaises(ReadError):
            _send(connection, {"data": "x" * 65536})


if __name__ == "__main__":
    unittest.main()
