"""Real Unix sockets with synthetic keys and test-only nonce storage."""

import asyncio
import json
import hashlib
import hmac
import os
import struct
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureResult
from devnous.copa_telmex.staff_capture_transport import (
    CaptureUnixTransport,
    RESPONSE_DOMAIN,
    MAX_FRAME_BYTES,
    encode_request,
)

KEY = b"test-channel-key-separate-from-other-keys"


def unpack_response(raw):
    value = json.loads(raw)
    body = json.dumps(
        value["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    expected = hmac.new(KEY, RESPONSE_DOMAIN + body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(value["mac"], expected)
    return {"ok": value["body"]["ok"]}


async def exchange(path, frame):
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write(frame)
        await writer.drain()
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        return unpack_response(await reader.readexactly(size))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


def fixture(*, allowed=None, timeout=1, slow=False):
    calls, seen, cancellations = [], set(), []

    async def consume(nonce, expiry):
        if nonce in seen:
            return False
        seen.add(nonce)
        return True

    async def handler(attempt, decision):
        calls.append((attempt, decision))
        if slow:
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                cancellations.append(True)
                raise
        return CaptureResult(attempt, uuid4())

    transport = CaptureUnixTransport(
        key=KEY,
        allowed_uids=frozenset({os.geteuid()} if allowed is None else allowed),
        consume_nonce=consume,
        handler=handler,
        clock=lambda: 1000,
        request_timeout=timeout,
    )
    return transport, calls, seen, cancellations


def frame(attempt=None, nonce=None, key=KEY, timestamp=1000):
    return encode_request(
        attempt_id=attempt or uuid4(),
        nonce=nonce or uuid4(),
        signed_decision=b"synthetic signed proof",
        timestamp=timestamp,
        key=key,
    )


def test_real_socket_success_replay_and_private_cleanup(tmp_path):
    async def run():
        transport, calls, _, _ = fixture()
        path = tmp_path / "writer.sock"
        attempt, nonce = uuid4(), uuid4()
        request = frame(attempt, nonce)
        async with transport.serve(path):
            assert path.stat().st_mode & 0o777 == 0o600
            assert await exchange(path, request) == {"ok": True}
            assert await exchange(path, request) == {"ok": False}
        assert calls == [(attempt, b"synthetic signed proof")]
        assert not path.exists()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "mac",
        "uid",
        "expired",
        "future",
        "oversized",
        "duplicate",
        "extra",
        "partial",
    ],
)
def test_invalid_requests_never_reach_nonce_or_handler(tmp_path, kind):
    async def run():
        allowed = {os.geteuid() + 1} if kind == "uid" else None
        transport, calls, seen, _ = fixture(allowed=allowed, timeout=0.05)
        request = frame()
        if kind == "mac":
            request = frame(key=b"other-channel-key-at-least-32-bytes")
        elif kind == "expired":
            request = frame(timestamp=900)
        elif kind == "future":
            request = frame(timestamp=1100)
        elif kind == "oversized":
            request = struct.pack("!I", MAX_FRAME_BYTES + 1)
        elif kind == "partial":
            request = struct.pack("!I", 100)
        elif kind == "duplicate":
            raw = b'{"body":{},"body":{},"mac":"x"}'
            request = struct.pack("!I", len(raw)) + raw
        elif kind == "extra":
            value = json.loads(request[4:])
            value["body"]["sql"] = "SELECT 1"
            raw = json.dumps(value).encode()
            request = struct.pack("!I", len(raw)) + raw
        async with transport.serve(tmp_path / "writer.sock"):
            assert await exchange(tmp_path / "writer.sock", request) == {"ok": False}
        assert not calls
        assert not seen

    asyncio.run(run())


def test_timeout_cancels_handler_and_hides_error(tmp_path):
    async def run():
        transport, calls, seen, cancelled = fixture(timeout=0.05, slow=True)
        async with transport.serve(tmp_path / "writer.sock"):
            assert await exchange(tmp_path / "writer.sock", frame()) == {"ok": False}
        assert len(calls) == len(seen) == len(cancelled) == 1

    asyncio.run(run())


def test_existing_file_is_never_removed(tmp_path):
    async def run():
        path = tmp_path / "writer.sock"
        path.write_text("existing")
        transport, _, _, _ = fixture()
        with pytest.raises(ValueError):
            async with transport.serve(path):
                pytest.fail("should not bind")
        assert path.read_text() == "existing"

    asyncio.run(run())


def test_public_directory_and_symlink_are_denied(tmp_path):
    async def run():
        parent = tmp_path / "public"
        parent.mkdir(mode=0o755)
        transport, _, _, _ = fixture()
        with pytest.raises(ValueError):
            async with transport.serve(parent / "writer.sock"):
                pytest.fail("should not bind")
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(ValueError):
            async with transport.serve(link / "writer.sock"):
                pytest.fail("should not bind")

    asyncio.run(run())


def test_shutdown_cancels_active_handler_and_closes_socket(tmp_path):
    async def run():
        entered = asyncio.Event()
        cancelled = []

        async def consume(nonce, expiry):
            return True

        async def handler(attempt, decision):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        transport = CaptureUnixTransport(
            key=KEY,
            allowed_uids=frozenset({os.geteuid()}),
            consume_nonce=consume,
            handler=handler,
            request_timeout=1,
            clock=lambda: 1000,
        )
        path = tmp_path / "writer.sock"
        async with transport.serve(path):
            request = asyncio.create_task(exchange(path, frame()))
            await asyncio.wait_for(entered.wait(), 1)
        assert await request == {"ok": False}
        assert cancelled == [True]
        assert not path.exists()

    asyncio.run(run())


def test_instance_rejects_second_listener_and_keeps_first(tmp_path):
    async def run():
        transport, calls, _, _ = fixture()
        first, second = tmp_path / "first.sock", tmp_path / "second.sock"
        async with transport.serve(first):
            with pytest.raises(ValueError):
                async with transport.serve(second):
                    pytest.fail("duplicate listener")
            assert await exchange(first, frame()) == {"ok": True}
        assert len(calls) == 1

    asyncio.run(run())


def test_group_config_requires_exact_directory_gid_and_mode(tmp_path):
    async def run():
        async def nonce(value, expiry):
            return True

        async def handler(attempt, decision):
            return CaptureResult(attempt, uuid4())

        transport = CaptureUnixTransport(
            key=KEY,
            allowed_uids=frozenset({os.geteuid()}),
            client_gid=os.getegid(),
            consume_nonce=nonce,
            handler=handler,
            clock=lambda: 1000,
        )
        path = tmp_path / "writer.sock"
        with pytest.raises(ValueError):
            async with transport.serve(path):
                pytest.fail("group traversal missing")
        tmp_path.chmod(0o710)
        async with transport.serve(path):
            assert path.stat().st_gid == os.getegid()
            assert path.stat().st_mode & 0o777 == 0o660
            assert await exchange(path, frame()) == {"ok": True}

    asyncio.run(run())


def test_configured_peer_from_different_os_uid_can_connect():
    import pwd
    import shutil
    import tempfile

    if os.geteuid() != 0 or not shutil.which("runuser"):
        pytest.skip("Cross-UID test requires root and existing runuser")
    try:
        account = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("Existing nobody OS identity unavailable")
    root = tempfile.mkdtemp(prefix="ctt-transport-cross-uid-")
    os.chown(root, 0, account.pw_gid)
    os.chmod(root, 0o710)

    async def run():
        from pathlib import Path

        calls = []

        async def nonce(value, expiry):
            return True

        async def handler(attempt, decision):
            calls.append(attempt)
            return CaptureResult(attempt, uuid4())

        transport = CaptureUnixTransport(
            key=KEY,
            allowed_uids=frozenset({account.pw_uid}),
            client_gid=account.pw_gid,
            consume_nonce=nonce,
            handler=handler,
            clock=lambda: 1000,
        )
        path = Path(root) / "writer.sock"
        client = (
            "import socket,sys,struct,json; "
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
            "s.connect(sys.argv[1]); s.sendall(sys.stdin.buffer.read()); "
            'n=struct.unpack("!I",s.recv(4))[0]; '
            "print(s.recv(n).decode()); s.close()"
        )
        async with transport.serve(path):
            proc = await asyncio.create_subprocess_exec(
                "runuser",
                "-u",
                account.pw_name,
                "--",
                "/usr/bin/python3",
                "-c",
                client,
                str(path),
                cwd="/tmp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(frame()), 5)
            assert proc.returncode == 0, err.decode()
            assert unpack_response(out) == {"ok": True}
        assert len(calls) == 1

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(root)


def test_extended_acl_is_rejected(tmp_path, monkeypatch):
    async def run():
        transport, _, _, _ = fixture()
        monkeypatch.setattr(os, "getxattr", lambda *a, **k: b"synthetic acl")
        with pytest.raises(ValueError):
            async with transport.serve(tmp_path / "writer.sock"):
                pytest.fail("unaccredited ACL")

    asyncio.run(run())


def test_connection_budget_rejects_second_active_request(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def consume(value, expiry):
            return True

        async def handler(attempt, decision):
            calls.append(attempt)
            entered.set()
            await release.wait()
            return CaptureResult(attempt, uuid4())

        transport = CaptureUnixTransport(
            key=KEY,
            allowed_uids=frozenset({os.geteuid()}),
            consume_nonce=consume,
            handler=handler,
            max_connections=1,
            clock=lambda: 1000,
        )
        path = tmp_path / "writer.sock"
        async with transport.serve(path):
            first = asyncio.create_task(exchange(path, frame()))
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
                await exchange(path, frame())
            release.set()
            assert await first == {"ok": True}
        assert len(calls) == 1

    asyncio.run(run())


def test_nonce_store_failure_cannot_reach_handler(tmp_path):
    async def run():
        calls = []

        async def consume(value, expiry):
            raise RuntimeError("private storage information")

        async def handler(attempt, decision):
            calls.append(attempt)
            return CaptureResult(attempt, uuid4())

        transport = CaptureUnixTransport(
            key=KEY,
            allowed_uids=frozenset({os.geteuid()}),
            consume_nonce=consume,
            handler=handler,
            clock=lambda: 1000,
        )
        path = tmp_path / "writer.sock"
        async with transport.serve(path):
            assert await exchange(path, frame()) == {"ok": False}
        assert not calls

    asyncio.run(run())


def test_authenticated_client_success_replay_uncertain_and_fresh_nonce(
    tmp_path,
):
    from devnous.copa_telmex.staff_capture_transport import (
        CaptureUnixClient,
        CaptureTransportUncertain,
    )

    async def run():
        transport, calls, _, _ = fixture()
        path = tmp_path / "writer.sock"
        client = CaptureUnixClient(
            path=path, key=KEY, server_uid=os.geteuid(), clock=lambda: 1000
        )
        attempt, nonce = uuid4(), uuid4()
        async with transport.serve(path):
            result = await client.execute(attempt, b"proof", nonce=nonce)
            assert result.attempt_id == attempt and result.nonce == nonce
            assert result.request_hash.startswith("sha256:")
            with pytest.raises(CaptureTransportUncertain):
                await client.execute(attempt, b"proof", nonce=nonce)
            next_nonce = uuid4()
            assert (
                await client.execute(attempt, b"proof", nonce=next_nonce)
            ).nonce == next_nonce
        assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "alteration",
    ["mac", "nonce", "attempt", "hash", "unsigned", "timeout", "oversized"],
)
def test_client_rejects_unbound_manipulated_or_missing_response(tmp_path, alteration):
    from devnous.copa_telmex.staff_capture_transport import (
        CaptureUnixClient,
        CaptureTransportUncertain,
        MAX_RESPONSE_BYTES,
    )

    async def run():
        async def fake_server(reader, writer):
            try:
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                raw = await reader.readexactly(size)
                request = json.loads(raw)["body"]
                if alteration == "timeout":
                    await asyncio.sleep(2)
                    return
                if alteration == "oversized":
                    writer.write(struct.pack("!I", MAX_RESPONSE_BYTES + 1))
                    await writer.drain()
                    return
                body = dict(
                    schema="ctt.capture.unix-response.v1",
                    ok=True,
                    nonce=request["nonce"],
                    attempt_id=request["attempt_id"],
                    request_hash="sha256:"
                    + hashlib.sha256(struct.pack("!I", size) + raw).hexdigest(),
                )
                if alteration == "nonce":
                    body["nonce"] = str(uuid4())
                elif alteration == "attempt":
                    body["attempt_id"] = str(uuid4())
                elif alteration == "hash":
                    body["request_hash"] = "sha256:" + "0" * 64
                encoded = json.dumps(
                    body,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
                envelope = dict(
                    body=body,
                    mac=hmac.new(
                        KEY, RESPONSE_DOMAIN + encoded, hashlib.sha256
                    ).hexdigest(),
                )
                if alteration == "mac":
                    envelope["mac"] = "0" * 64
                elif alteration == "unsigned":
                    envelope = {"ok": True}
                raw = json.dumps(
                    envelope,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
                writer.write(struct.pack("!I", len(raw)) + raw)
                await writer.drain()
            finally:
                writer.close()

        path = tmp_path / "fake.sock"
        server = await asyncio.start_unix_server(fake_server, path=str(path))
        path.chmod(0o600)
        try:
            client = CaptureUnixClient(
                path=path, key=KEY, server_uid=os.geteuid(), timeout=0.05
            )
            with pytest.raises(
                CaptureTransportUncertain, match="^capture outcome uncertain$"
            ):
                await client.execute(uuid4(), b"proof", nonce=uuid4())
        finally:
            server.close()
            await server.wait_closed()
            path.unlink()

    asyncio.run(run())


def test_client_pins_server_uid_at_path_and_peer(tmp_path, monkeypatch):
    from devnous.copa_telmex.staff_capture_transport import (
        CaptureUnixClient,
        CaptureTransportUncertain,
    )

    async def run():
        transport, calls, seen, _ = fixture()
        path = tmp_path / "writer.sock"
        client = CaptureUnixClient(path=path, key=KEY, server_uid=os.geteuid() + 1)
        async with transport.serve(path):
            with pytest.raises(CaptureTransportUncertain):
                await client.execute(uuid4(), b"proof", nonce=uuid4())
            # Isolate peer validation independently of the path validation.
            monkeypatch.setattr(client, "_check_path", lambda: None)
            with pytest.raises(CaptureTransportUncertain):
                await client.execute(uuid4(), b"proof", nonce=uuid4())
        assert not calls and not seen

    asyncio.run(run())
