"""Authenticated wire tests; stub workflow confers no business authority."""

import os
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from devnous.copa_telmex import staff_review_operations_channel as wire
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_workflow import ReviewWorkflowService

KEY = b"c" * 32
ACCOUNT, SESSION, OBJECT = uuid4(), uuid4(), uuid4()


class Nonces(PostgresTransportNonces):
    def __init__(self):
        self.seen = set()

    async def __call__(self, nonce, expiry):
        if nonce in self.seen:
            return False
        self.seen.add(nonce)
        return True


class Workflow(ReviewWorkflowService):
    def __init__(self):
        self.calls = []

    def validate_channel_key(self, key):
        assert key == KEY

    async def result(self, **params):
        self.calls.append(params)
        return {"state": "ready"}

    view = prepare = challenge = assert_and_capture = status = result
    enrollment_begin = enrollment_complete = enrollment_status = result

    async def source_page(self, **params):
        self.calls.append(params)
        return b"\x89PNG\r\n\x1a\nimage"


def app_fixture():
    workflow = Workflow()
    app = wire.create_staff_review_operations_app(
        service=workflow, channel_key=KEY, consume_nonce=Nonces()
    )
    return app, workflow


@pytest.mark.asyncio
async def test_all_client_operations_roundtrip():
    app, workflow = app_fixture()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        await client.view(ACCOUNT, SESSION)
        await client.prepare(ACCOUNT, SESSION, None, {"players": []})
        await client.challenge(ACCOUNT, SESSION, OBJECT, True)
        await client.assert_and_capture(ACCOUNT, SESSION, OBJECT, b"assertion")
        await client.status(ACCOUNT, SESSION, OBJECT)
        assert (await client.source_page(ACCOUNT, SESSION, OBJECT, 0)).startswith(
            b"\x89PNG"
        )
        await client.enrollment_begin(ACCOUNT, SESSION)
        await client.enrollment_complete(ACCOUNT, SESSION, OBJECT, {"id": "test"})
        await client.enrollment_status(ACCOUNT, SESSION, OBJECT)
        assert len(workflow.calls) == 9
        assert workflow.calls[3]["assertion"] == b"assertion"
        assert workflow.calls[7]["registration"] == b'{"id":"test"}'
        assert all(call["account_id"] == ACCOUNT for call in workflow.calls)
        await client.assert_and_capture(ACCOUNT, SESSION, OBJECT, None)
        assert workflow.calls[-1]["attempt_id"] == OBJECT
        assert workflow.calls[-1]["assertion"] is None


@pytest.mark.parametrize(
    "operation,params",
    [
        ("unknown", {}),
        ("view", {"tenant_id": "x"}),
        ("challenge", {"proposal_id": OBJECT, "confirmed": 1}),
        ("source_page", {"proposal_id": OBJECT, "page_index": True}),
        ("source_page", {"proposal_id": OBJECT, "page_index": -1}),
        ("prepare", {"expected_proposal_id": "notuuid", "changes": {}}),
        ("prepare", {"expected_proposal_id": None, "changes": []}),
        ("assert_and_capture", {"attempt_id": OBJECT, "assertion": b""}),
        (
            "enrollment_complete",
            {"enrollment_id": OBJECT, "registration": b"x" * 65537},
        ),
    ],
)
def test_untrusted_request_types_denied(operation, params):
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.encode_operation(operation, ACCOUNT, SESSION, **params)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{ "x":1}',
        b'{"x":NaN}',
        b'{"x":' + b"[" * 25 + b"0" + b"]" * 25 + b"}",
        b'{"x":"unterminated}',
        b"}{",
        b"[]",
        b'{"x":',
    ],
)
def test_canonical_bounded_json(raw):
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire._json(raw, 1000)


@pytest.mark.parametrize("value", ["%%%", "", "YQ===", "YQ", 5])
def test_invalid_binary(value):
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire._binary(value, 10)


@pytest.mark.parametrize("which", ["operation", "timestamp", "nonce", "body", "key"])
def test_request_mac_typed(which):
    args = [KEY, "view", uuid4(), int(time.time()), b"{}"]
    args[{"key": 0, "operation": 1, "nonce": 2, "timestamp": 3, "body": 4}[which]] = {
        "key": b"x",
        "operation": "unknown",
        "nonce": "no",
        "timestamp": True,
        "body": b"",
    }[which]
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.operation_request_mac(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["mac", "timestamp", "replay", "type", "large", "extra", "duplicate"]
)
async def test_server_denies_untrusted_before_workflow(attack):
    app, workflow = app_fixture()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001"
    ) as http:
        raw = wire.encode_operation("view", ACCOUNT, SESSION)
        nonce, now = uuid4(), int(time.time())
        if attack == "timestamp":
            now -= 100
        if attack == "extra":
            raw = wire._canonical(
                {
                    "account_id": str(ACCOUNT),
                    "session_id": str(SESSION),
                    "role": "owner",
                }
            )
        if attack == "large":
            raw = b"x" * (wire.MAX_REQUEST_BYTES + 1)
        headers = {
            "Content-Type": "application/json",
            wire.NONCE_HEADER: str(nonce),
            wire.TIME_HEADER: str(now),
            wire.MAC_HEADER: "invalid",
        }
        if attack not in {"large", "mac"}:
            headers[wire.MAC_HEADER] = wire.operation_request_mac(
                KEY, "view", nonce, now, raw
            )
        if attack == "type":
            headers["Content-Type"] = "text/plain"
        if attack == "duplicate":
            headers = list(headers.items()) + [(wire.MAC_HEADER, "duplicate")]
        if attack == "replay":
            assert (
                await http.post(wire.PREFIX + "view", content=raw, headers=headers)
            ).status_code == 200
            workflow.calls.clear()
        result = await http.post(wire.PREFIX + "view", content=raw, headers=headers)
        assert result.status_code == 503
        assert not workflow.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["mac", "status", "encoding", "type", "large", "shape", "binary"]
)
async def test_client_rejects_unbound_response(attack):
    async def respond(request):
        nonce = wire._uuid(request.headers[wire.NONCE_HEADER])
        body = wire._canonical({"result": {"state": "ready"}})
        if attack == "shape":
            body = wire._canonical({"result": []})
        if attack == "binary":
            body = wire._canonical({"result": {"png": "%%%"}})
        if attack == "large":
            body = b"x" * (wire.MAX_RESPONSE_BYTES + 1)
        status = 503 if attack == "status" else 200
        headers = {"Content-Type": "application/json"}
        if attack != "large":
            headers[wire.RESPONSE_HEADER] = wire.operation_response_mac(
                KEY,
                "source_page" if attack == "binary" else "view",
                nonce,
                request.content,
                200,
                body,
            )
        if attack == "mac":
            headers[wire.RESPONSE_HEADER] = "a" * 64
        if attack == "encoding":
            headers["Content-Encoding"] = "unrecognized"
        if attack == "type":
            headers["Content-Type"] = "text/plain"
        return httpx.Response(status, content=body, headers=headers)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        with pytest.raises(wire.StaffReviewOperationsUnavailable):
            if attack == "binary":
                await client.source_page(ACCOUNT, SESSION, OBJECT, 0)
            else:
                await client.view(ACCOUNT, SESSION)


@pytest.mark.asyncio
async def test_workflow_denial_response_authenticated_generic():
    app, workflow = app_fixture()

    async def fail(**params):
        raise ValueError("private person and key details")

    workflow.view = fail
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        with pytest.raises(wire.StaffReviewOperationsUnavailable) as exc:
            await client.view(ACCOUNT, SESSION)
        assert "private" not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8001",
        "http://example.com:80",
        "http://127.0.0.1",
        "http://u:p@127.0.0.1:80",
        "http://127.0.0.1:80/path",
        "http://127.0.0.1:80?x=1",
        "http://127.0.0.1:80#x",
    ],
)
def test_client_loopback_only(url):
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.StaffReviewOperationsClient(
            base_url=url, channel_key=KEY, http_client=SimpleNamespace(trust_env=False)
        )


@pytest.mark.parametrize("change", ["service", "nonce", "skew", "timeout", "inflight"])
def test_factory_refuses_arbitrary_authority(change):
    params = dict(service=Workflow(), channel_key=KEY, consume_nonce=Nonces())
    params[
        {
            "service": "service",
            "nonce": "consume_nonce",
            "skew": "max_clock_skew",
            "timeout": "timeout_seconds",
            "inflight": "max_inflight",
        }[change]
    ] = {
        "service": object(),
        "nonce": lambda *x: True,
        "skew": True,
        "timeout": float("nan"),
        "inflight": 0,
    }[
        change
    ]
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.create_staff_review_operations_app(**params)


@pytest.mark.asyncio
async def test_explicit_config_lifecycle(tmp_path):
    os.chmod(tmp_path, 0o700)
    path = tmp_path / "channel.key"
    path.write_bytes(KEY)
    path.chmod(0o600)
    app = SimpleNamespace(state=SimpleNamespace())
    assert (
        wire.configure_staff_review_client(app, wire.StaffReviewClientSettings())
        is None
    )
    client = wire.configure_staff_review_client(
        app,
        wire.StaffReviewClientSettings(
            enabled=True, base_url="http://127.0.0.1:8001", key_path=path
        ),
    )
    assert app.state.ctt_staff_review_client is client
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.configure_staff_review_client(app, wire.StaffReviewClientSettings())
    await wire.close_staff_review_client(app)
    assert app.state.ctt_staff_review_client is None
    assert client._client.is_closed
    await wire.close_staff_review_client(app)


@pytest.mark.parametrize(
    "attack",
    ["parentmode", "filemode", "size", "symlink", "parentlink", "hardlink", "relative"],
)
def test_keyfile_safety(tmp_path, attack):
    tmp_path.chmod(0o700)
    path = tmp_path / "key"
    path.write_bytes(KEY)
    path.chmod(0o600)
    if attack == "parentmode":
        tmp_path.chmod(0o755)
    if attack == "filemode":
        path.chmod(0o644)
    if attack == "size":
        path.write_bytes(b"x")
    if attack == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    if attack == "parentlink":
        link = tmp_path.parent / (tmp_path.name + "-link")
        link.symlink_to(tmp_path)
        path = link / "key"
    if attack == "hardlink":
        os.link(path, tmp_path / "other")
    if attack == "relative":
        path = wire.Path("relative")
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire._read_key(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["operation", "nonce", "request", "status"])
async def test_real_response_mac_cannot_cross_context(changed):
    async def respond(request):
        operation, nonce, raw, status = (
            "view",
            wire._uuid(request.headers[wire.NONCE_HEADER]),
            request.content,
            200,
        )
        if changed == "operation":
            operation = "enrollment_begin"
        if changed == "nonce":
            nonce = uuid4()
        if changed == "request":
            raw = wire.encode_operation("view", uuid4(), SESSION)
        if changed == "status":
            status = 403
        body = wire._canonical({"result": {"state": "ready"}})
        return httpx.Response(
            200,
            content=body,
            headers={
                "Content-Type": "application/json",
                wire.RESPONSE_HEADER: wire.operation_response_mac(
                    KEY, operation, nonce, raw, status, body
                ),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        with pytest.raises(wire.StaffReviewOperationsUnavailable):
            await client.view(ACCOUNT, SESSION)


def test_keyfile_repository_path_denied():
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire._read_key(wire.Path(__file__).absolute().parents[2] / "secrets.key")


def test_response_mac_rejects_bool_status():
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.operation_response_mac(KEY, "view", uuid4(), b"{}", True, b"{}")


def test_factory_requires_correct_settings():
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.configure_staff_review_client(app, object())
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.configure_staff_review_client(
            app, wire.StaffReviewClientSettings(enabled=1)
        )
    with pytest.raises(wire.StaffReviewOperationsUnavailable):
        wire.configure_staff_review_client(
            app, wire.StaffReviewClientSettings(enabled=True)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(wire.SAFE_DOMAIN_CODES))
async def test_safe_domain_errors_remain_typed_and_signed(code):
    from devnous.copa_telmex.staff_review_workflow import ReviewWorkflowDenied

    app, workflow = app_fixture()

    async def fail(**params):
        raise ReviewWorkflowDenied(code)

    workflow.view = fail
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        with pytest.raises(wire.StaffReviewOperationDenied) as exc:
            await client.view(ACCOUNT, SESSION)
        assert exc.value.code == code


@pytest.mark.asyncio
async def test_arbitrary_exception_code_does_not_become_domain_denial():
    app, workflow = app_fixture()

    class ForgedError(ValueError):
        code = "REVIEW_EXPIRED"

    async def fail(**params):
        raise ForgedError("private")

    workflow.view = fail
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), trust_env=False
    ) as http:
        client = wire.StaffReviewOperationsClient(
            base_url="http://127.0.0.1:8001", channel_key=KEY, http_client=http
        )
        with pytest.raises(wire.StaffReviewOperationsUnavailable) as exc:
            await client.view(ACCOUNT, SESSION)
        assert type(exc.value) is wire.StaffReviewOperationsUnavailable
