"""Bounded activation defaults to read-only and snapshots before any insert."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from scripts import request_merced_cleanup_review as batch


def test_cli_defaults_to_preview_and_sanitizes_failures(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["request_merced_cleanup_review"])

    async def preview(args):
        assert not args.apply
        return {"mode": "dry-run"}

    monkeypatch.setattr(batch, "run", preview)
    assert batch.main() == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"

    async def failed(args):
        raise RuntimeError("sensitive connection data must not appear")

    monkeypatch.setattr(batch, "run", failed)
    assert batch.main() == 1
    output = capsys.readouterr().out
    assert "sensitive" not in output and "RuntimeError" in output


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


@pytest.mark.asyncio
async def test_storage_preflight_rejects_an_incompatible_check():
    async def execute(*a, **kw):
        return Result(["CHECK (accion IN ('pagar'))"])

    with pytest.raises(ValueError, match="historial"):
        await batch.validate_audit_storage(SimpleNamespace(execute=execute))


@pytest.fixture
def harness(monkeypatch):
    manifest = json.loads(batch.MANIFEST.read_text())
    calls = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            calls.append("exit_error" if exc[0] else "exit_ok")

        async def execute(self, query, params=None):
            calls.append(str(query))
            if "pg_constraint" in str(query):
                return Result(["CHECK (accion = ANY (ARRAY['editar'::text]))"])
            return Result(manifest["items"])

        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    async def dispose():
        calls.append("dispose")

    async def states(*a, **kw):
        return {}

    async def authorize(*a, **kw):
        calls.append("authorized")

    async def request(*a, **kw):
        calls.append("request")
        return True

    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/test")
    monkeypatch.setattr(
        batch, "create_async_engine", lambda *a, **kw: SimpleNamespace(dispose=dispose)
    )
    monkeypatch.setattr(batch, "AsyncSession", lambda *a, **kw: Session())
    monkeypatch.setattr(batch, "load_review_states", states)
    monkeypatch.setattr(batch, "authorize_actor", authorize)
    monkeypatch.setattr(batch, "request_review", request)
    return manifest, calls


@pytest.mark.asyncio
async def test_default_preview_is_read_only_and_inserts_nothing(harness):
    _, calls = harness
    result = await batch.run(SimpleNamespace(apply=False))
    assert result["mode"] == "dry-run" and result["count"] == 24
    assert result["requested"] == 0
    assert "SET TRANSACTION READ ONLY" in calls
    assert "rollback" in calls
    assert "request" not in calls and "commit" not in calls


@pytest.mark.asyncio
async def test_apply_snapshots_then_marks_exactly_24_in_one_transaction(
    harness, tmp_path
):
    manifest, calls = harness
    snapshot = tmp_path / "before.json"
    result = await batch.run(
        SimpleNamespace(
            apply=True,
            actor_id=str(uuid4()),
            snapshot=str(snapshot),
            confirm_sha256=batch.validate_selection(manifest, manifest["items"]),
        )
    )
    assert result["requested"] == 24
    assert calls.count("request") == 24 and calls.count("commit") == 1
    assert calls.index("authorized") < calls.index("request") < calls.index("commit")
    assert any("FOR UPDATE OF d, er" in call for call in calls)
    assert json.loads(snapshot.read_text())["selection"] == manifest
    assert snapshot.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_apply_with_wrong_digest_cannot_write(harness, tmp_path):
    _, calls = harness
    with pytest.raises(ValueError, match="Apply requiere"):
        await batch.run(
            SimpleNamespace(
                apply=True,
                actor_id=str(uuid4()),
                snapshot=str(tmp_path / "before.json"),
                confirm_sha256="wrong",
            )
        )
    assert "request" not in calls and "commit" not in calls


@pytest.mark.asyncio
async def test_existing_snapshot_is_never_overwritten(harness, tmp_path):
    manifest, calls = harness
    snapshot = tmp_path / "before.json"
    snapshot.write_text("preserve")
    with pytest.raises(FileExistsError):
        await batch.run(
            SimpleNamespace(
                apply=True,
                actor_id=str(uuid4()),
                snapshot=str(snapshot),
                confirm_sha256=batch.validate_selection(manifest, manifest["items"]),
            )
        )
    assert snapshot.read_text() == "preserve"
    assert "request" not in calls and "commit" not in calls


@pytest.mark.asyncio
async def test_failed_batch_never_commits_partial_requests(
    harness, monkeypatch, tmp_path
):
    manifest, calls = harness

    async def failed(*a, **kw):
        raise ValueError("simulated failure")

    monkeypatch.setattr(batch, "request_review", failed)
    with pytest.raises(ValueError, match="simulated"):
        await batch.run(
            SimpleNamespace(
                apply=True,
                actor_id=str(uuid4()),
                snapshot=str(tmp_path / "before.json"),
                confirm_sha256=batch.validate_selection(manifest, manifest["items"]),
            )
        )
    assert "commit" not in calls and "exit_error" in calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active,role,denied",
    [(False, "admin", False), (True, "empleado", False), (True, "admin", True)],
)
async def test_actor_must_be_active_finance_and_not_explicitly_denied(
    monkeypatch, active, role, denied
):
    actor = SimpleNamespace(
        id=uuid4(), activo=active, rol=role, departamento="Finanzas"
    )

    async def execute(*a, **kw):
        return Result([actor])

    async def permissions(*a, **kw):
        return set()

    async def explicit(*a, **kw):
        return False if denied else None

    monkeypatch.setattr(batch, "_load_effective_permissions", permissions)
    monkeypatch.setattr(batch, "explicit_tool_decision", explicit)
    with pytest.raises((PermissionError, HTTPException)):
        await batch.authorize_actor(SimpleNamespace(execute=execute), actor.id)
