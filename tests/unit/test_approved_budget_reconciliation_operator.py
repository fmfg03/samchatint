"""Fail-closed authority, backend and atomic orchestration of the fixed CLI."""

import copy
import csv
import hashlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

import devnous.gastos  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "approved_reconciliation_operator",
    ROOT / "scripts/reconcile_approved_budget_movements.py",
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def fixed_data():
    return copy.deepcopy(cli._FIXED_CONFIG)


def states():
    data = fixed_data()
    before = {table: [] for table in cli.TABLES}
    before["budget_versions"] = [
        {"id": cli.VERSION, "status": "approved", "note": "Borrador"}
    ]
    before["budget_movement_assignments"] = [
        {
            "id": "old",
            "budget_version_id": cli.VERSION,
            "movement_key": "unrelated",
            "budget_line_id": "historic",
        }
    ]
    before["budget_version_audit_log"] = [
        {"id": "old-event", "event_type": "historical"}
    ]
    after = copy.deepcopy(before)
    records = []
    for row in data["assignments"]:
        ident = str(uuid5(NAMESPACE_URL, row["movement_key"]))
        after["budget_movement_assignments"].append(
            {
                "id": ident,
                "budget_version_id": cli.VERSION,
                "budget_line_id": row["budget_line_id"],
                "budget_concept_id": row["budget_concept_id"],
                "movement_key": row["movement_key"],
                "assigned_by_empleado_id": cli.ACTOR,
            }
        )
        records.append({**row, "assignment_id": ident})
    payload = {
        "plan_sha256": "c" * 64,
        "actor_empleado_id": cli.ACTOR,
        "reason": data["reason"],
        "assignments": records,
    }
    after["budget_version_audit_log"].append(
        {
            "id": "new-event",
            "budget_version_id": cli.VERSION,
            "event_type": cli.EVENT,
            "actor_empleado_id": cli.ACTOR,
            "payload": payload,
        }
    )
    return data, before, after


def insert_params():
    row = fixed_data()["assignments"][0]
    return {
        **row,
        "budget_version_id": cli.VERSION,
        "assigned_by_empleado_id": cli.ACTOR,
    }


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE x(id int)",
        "ALTER TABLE budget_lines ADD x int",
        "DROP TABLE documentos",
        "DELETE FROM budget_movement_assignments",
        "TRUNCATE budget_versions",
        "GRANT ALL ON documentos TO x",
        "CALL do_something()",
        "SELECT 1; INSERT INTO documentos VALUES(1)",
        "WITH x AS (UPDATE documentos SET estado='pagado' RETURNING *) SELECT * FROM x",
        "UPDATE budget_lines SET budget_amount=0",
        "UPDATE budget_movement_assignments SET budget_line_id='x'",
        "INSERT INTO documentos(id) VALUES(:id)",
        "INSERT INTO budget_movement_assignments VALUES(:x) "
        "ON CONFLICT DO UPDATE SET x=1",
    ],
)
def test_sql_blocks_schema_sources_updates_and_compounds(statement):
    with pytest.raises(ValueError):
        cli.sql_guard(statement, True, parameters=insert_params())


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM budget_lines FOR UPDATE",
        "SELECT * FROM empleados FOR SHARE",
        "SELECT pg_advisory_xact_lock(1)",
        "INSERT INTO budget_movement_assignments VALUES(:x)",
    ],
)
def test_read_only_preview_cannot_write_or_lock(statement):
    with pytest.raises(ValueError):
        cli.sql_guard(statement, False, parameters=insert_params())


def test_sql_allows_only_exact_bound_keys_targets_actor_and_root():
    statement = (
        "INSERT INTO budget_movement_assignments(id) VALUES(:id) ON CONFLICT DO NOTHING"
    )
    original = insert_params()
    cli.sql_guard(statement, True, parameters=original)
    for field in (
        "movement_key",
        "budget_line_id",
        "budget_concept_id",
        "budget_version_id",
        "assigned_by_empleado_id",
    ):
        with pytest.raises(ValueError, match="fixed reviewed scope"):
            cli.sql_guard(statement, True, parameters={**original, field: "different"})
    cli.sql_guard("/* safe owner read */ SELECT 1", False)
    cli.sql_guard("SET TRANSACTION READ ONLY", False)


def test_audit_insert_requires_exact_batch_payload_and_status():
    data, _, after = states()
    params = {
        "budget_version_id": cli.VERSION,
        "actor_empleado_id": cli.ACTOR,
        "event_type": cli.EVENT,
        "from_status": "approved",
        "to_status": "approved",
        "payload": json.dumps(after["budget_version_audit_log"][-1]["payload"]),
    }
    statement = "INSERT INTO budget_version_audit_log(id) VALUES(:id)"
    cli.sql_guard(statement, True, parameters=params)
    for field in (
        "budget_version_id",
        "actor_empleado_id",
        "event_type",
        "from_status",
        "to_status",
    ):
        with pytest.raises(ValueError):
            cli.sql_guard(statement, True, parameters={**params, field: "different"})
    for payload in (
        {},
        {"assignments": []},
        {**after["budget_version_audit_log"][-1]["payload"], "reason": "wrong"},
    ):
        with pytest.raises(ValueError, match="payload"):
            cli.sql_guard(
                statement, True, parameters={**params, "payload": json.dumps(payload)}
            )


def test_exact_append_preserves_all_history_and_repeat_is_noop():
    data, before, after = states()
    assert cli.completed_core_hash(before, data) is None
    assert cli.completed_core_hash(after, data) == "c" * 64
    assert (
        cli.verify_preservation(before, after, data, completed=False)[
            "new_assignment_count"
        ]
        == 16
    )
    assert (
        cli.verify_preservation(after, after, data, completed=True)[
            "new_assignment_count"
        ]
        == 0
    )


@pytest.mark.parametrize("table", cli.PROTECTED)
def test_any_change_to_protected_financial_sources_is_rejected(table):
    data, before, after = states()
    after[table].append({"unexpected": "change"})
    with pytest.raises(ValueError, match="Protected financial source"):
        cli.verify_preservation(before, after, data, completed=False)


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "target",
        "actor",
        "audit_missing",
        "audit_wrong_reason",
        "audit_duplicate",
        "audit_assignment_id",
    ],
)
def test_incomplete_or_unaccredited_completed_batch_is_rejected(change):
    data, _, after = states()
    if change == "partial":
        after["budget_movement_assignments"].pop()
    elif change in ("target", "actor"):
        after["budget_movement_assignments"][-1][
            "budget_line_id" if change == "target" else "assigned_by_empleado_id"
        ] = "wrong"
    elif change == "audit_missing":
        after["budget_version_audit_log"].pop()
    elif change == "audit_wrong_reason":
        after["budget_version_audit_log"][-1]["payload"]["reason"] = "wrong"
    elif change == "audit_duplicate":
        after["budget_version_audit_log"].append(
            copy.deepcopy(after["budget_version_audit_log"][-1])
        )
    else:
        after["budget_version_audit_log"][-1]["payload"]["assignments"][0][
            "assignment_id"
        ] = "wrong"
    with pytest.raises(ValueError):
        cli.completed_core_hash(after, data)


@pytest.mark.parametrize(
    "change", ["old_assignment", "extra_assignment", "old_audit", "extra_audit"]
)
def test_preservation_rejects_other_assignment_or_audit_effects(change):
    data, before, after = states()
    if change == "old_assignment":
        after["budget_movement_assignments"][0]["budget_line_id"] = "wrong"
    elif change == "extra_assignment":
        after["budget_movement_assignments"].append({"id": "unreviewed"})
    elif change == "old_audit":
        after["budget_version_audit_log"][0]["event_type"] = "rewritten"
    else:
        after["budget_version_audit_log"].append({"id": "unexpected"})
    with pytest.raises(ValueError):
        cli.verify_preservation(before, after, data, completed=False)


@pytest.fixture
def source_files(monkeypatch, tmp_path):
    data = fixed_data()
    root = tmp_path / "root"
    artifacts = tmp_path / "approval"
    root.mkdir()
    artifacts.mkdir()
    (root / cli.CONFIG).parent.mkdir(parents=True)
    for name, content in [
        (cli.STORY, "Approved story"),
        (cli.SPEC, "Approved technical specification"),
    ]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        field = "story_sha256" if name == cli.STORY else "spec_sha256"
        data[field] = cli._file_sha(target)
        (
            artifacts / ("historia.md" if name == cli.STORY else "especificacion.md")
        ).write_text(content)
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "movement_key",
            "budget_line_id",
            "concept_id",
            "phase",
            "kind",
            "document_id",
            "expense_id",
        ],
    )
    writer.writeheader()
    for row in data["assignments"]:
        writer.writerow(
            {
                k: row["budget_concept_id"] if k == "concept_id" else row.get(k)
                for k in writer.fieldnames
            }
        )
    (artifacts / "16-propuestas-nuevas.csv").write_text(stream.getvalue())
    data["csv_sha256"] = cli._file_sha(artifacts / "16-propuestas-nuevas.csv")
    (artifacts / "aprobacion-historia.json").write_text(
        json.dumps(
            {
                "answer": "Aprobar historia de reparación",
                "approved_sha256": data["story_sha256"],
            }
        )
    )
    (artifacts / "aprobacion-especificacion.json").write_text(
        json.dumps({"answer": "aprobado", "spec_sha256": data["spec_sha256"]})
    )
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(cli, "ARTIFACTS", artifacts)
    monkeypatch.setattr(cli, "CSV_SHA", data["csv_sha256"])

    def persist():
        (root / cli.CONFIG).write_text(json.dumps(data))
        monkeypatch.setattr(cli, "CONFIG_SHA", cli._file_sha(root / cli.CONFIG))

    persist()
    return data, root, artifacts, persist


def test_inputs_require_approved_story_spec_exact_original_csv(source_files):
    data, root, artifacts, _ = source_files
    assert cli.inputs() == data
    (artifacts / "especificacion.md").write_text("silently changed")
    with pytest.raises(ValueError, match="Approved source"):
        cli.inputs()


@pytest.mark.parametrize(
    "change",
    ["actor", "year", "version", "count", "duplicate", "project", "target", "expense"],
)
def test_inputs_reject_scope_changes_even_if_config_bytes_are_rehashed(
    source_files, change
):
    data, _, _, persist = source_files
    if change == "actor":
        data["actor_empleado_id"] = "wrong"
    elif change == "year":
        data["edition_year"] = 2025
    elif change == "version":
        data["budget_version_id"] = "wrong"
    elif change == "count":
        data["assignments"].pop()
    elif change == "duplicate":
        data["assignments"][-1] = copy.deepcopy(data["assignments"][0])
    elif change == "project":
        data["assignments"][0]["tournament_id"] = "wrong"
    elif change == "target":
        data["assignments"][0]["budget_line_id"] = "wrong"
    else:
        data["assignments"][0]["expense_id"] = "wrong"
    persist()
    with pytest.raises(ValueError):
        cli.inputs()


def test_changed_config_bytes_fail_before_source_use(source_files):
    _, root, _, _ = source_files
    (root / cli.CONFIG).write_text("{}")
    with pytest.raises(ValueError, match="configuration changed"):
        cli.inputs()


def approval(data, source):
    return {
        "status": "FINAL_DIFF_AND_BATCH_APPROVED",
        "action": "apply_fixed_16_approved_budget_reconciliations",
        "answer": "aprobado",
        "config_sha256": cli.CONFIG_SHA,
        "csv_sha256": cli.CSV_SHA,
        "spec_sha256": data["spec_sha256"],
        "story_sha256": data["story_sha256"],
        "assignments_sha256": cli.digest(cli.assignments(data)),
        "actor_empleado_id": cli.ACTOR,
        "reviewed_code_files": source["files"],
        "reviewed_tree_sha": source["tree"],
        "final_diff_sha256": "a" * 64,
    }


def test_production_requires_separate_final_approval_of_exact_whole_source(
    source_files,
):
    data, _, artifacts, _ = source_files
    source = {"commit": "a" * 40, "tree": "b" * 40, "files": {"core.py": "c" * 64}}
    path = artifacts / cli.FINAL_APPROVAL
    with pytest.raises(ValueError, match="Separate final"):
        cli.require_final_approval(data, source)
    value = approval(data, source)
    path.write_text(json.dumps(value))
    assert cli.require_final_approval(data, source) == value
    value.pop("reviewed_tree_sha")
    value["reviewed_commit"] = source["commit"]
    path.write_text(json.dumps(value))
    assert cli.require_final_approval(data, source) == value
    for field in [
        "status",
        "action",
        "answer",
        "config_sha256",
        "csv_sha256",
        "spec_sha256",
        "story_sha256",
        "assignments_sha256",
        "actor_empleado_id",
        "reviewed_code_files",
        "reviewed_commit",
        "final_diff_sha256",
    ]:
        altered = copy.deepcopy(value)
        altered[field] = ""
        path.write_text(json.dumps(altered))
        with pytest.raises(ValueError, match="Final approval"):
            cli.require_final_approval(data, source)


@pytest.fixture
def backend_binding(monkeypatch):
    url = "postgresql://actor:private-password@127.0.0.1:5432/devnous_db?ssl=require"
    env = {
        "SAMCHAT_ENV_FILE": str(cli.PRODUCTION_ENV),
        "DATABASE_URL": url,
        "POSTGRESQL_URL": url,
    }
    monkeypatch.setattr(cli, "serving_environment", lambda: ("123", env))
    monkeypatch.setattr(cli, "dotenv_values", lambda _: {"DATABASE_URL": url})
    return env


def test_backend_normalizes_only_driver_and_omits_secrets(backend_binding):
    args = SimpleNamespace(env_file=cli.PRODUCTION_ENV)
    url, identity = cli.production_backend(args)
    assert url.drivername == "postgresql+asyncpg"
    assert "private-password" not in json.dumps(identity)
    assert "actor" not in json.dumps(identity)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://actor:changed@127.0.0.1:5432/devnous_db?ssl=require",
        "postgresql://actor:private-password@remote:5432/devnous_db?ssl=require",
        "postgresql://actor:private-password@127.0.0.1:5433/devnous_db?ssl=require",
        "postgresql://actor:private-password@127.0.0.1:5432/other?ssl=require",
        "postgresql://actor:private-password@127.0.0.1:5432/devnous_db?ssl=disable",
        "postgresql://actor:private-password@127.0.0.1:5432/devnous_db?host=remote",
        "sqlite:///devnous_db",
    ],
)
def test_backend_rejects_different_credentials_endpoint_query_and_database(
    backend_binding, value
):
    backend_binding["DATABASE_URL"] = value
    with pytest.raises(ValueError):
        cli.production_backend(SimpleNamespace(env_file=cli.PRODUCTION_ENV))


@pytest.mark.parametrize("key", ["SAMCHAT_ENV_FILE", "DATABASE_URL", "POSTGRESQL_URL"])
def test_backend_requires_complete_live_environment(backend_binding, key):
    backend_binding.pop(key)
    with pytest.raises(ValueError):
        cli.production_backend(SimpleNamespace(env_file=cli.PRODUCTION_ENV))


def test_custom_env_file_cannot_select_other_backend(backend_binding):
    with pytest.raises(ValueError, match="Canonical production"):
        cli.production_backend(SimpleNamespace(env_file=Path("/tmp/other.env")))


@pytest.mark.parametrize(
    "extra",
    [
        ["--fail-after-insert", "1"],
        ["--restore", "--fail-after-insert", "1"],
        ["--restore", "--apply", "--fail-after-insert", "18"],
    ],
)
@pytest.mark.asyncio
async def test_faults_only_on_fixed_disposable_restore(extra, tmp_path):
    args = cli.parser().parse_args(["--output-dir", str(tmp_path), *extra])
    with pytest.raises(ValueError, match="Failure injection"):
        await cli.run(args)


@pytest.fixture
def process_binding(monkeypatch):
    live = "MainPID=123\nActiveState=active\n"
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: live)
    original = Path.read_bytes
    payload = (
        b"DATABASE_URL=postgresql://actor:secret@localhost/devnous_db\0"
        b"POSTGRESQL_URL=postgresql://actor:secret@localhost/devnous_db\0"
        b"SAMCHAT_ENV_FILE=/etc/samchat/samchat.env\0IGNORED=hidden\0"
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda p: payload if str(p) == "/proc/123/environ" else original(p),
    )
    return original


def test_live_process_environment_is_read_without_exporting_other_values(
    process_binding,
):
    pid, env = cli.serving_environment()
    assert pid == "123" and set(env) == {
        "DATABASE_URL",
        "POSTGRESQL_URL",
        "SAMCHAT_ENV_FILE",
    }


@pytest.mark.parametrize(
    "mode", ["inactive", "invalid_pid", "missing", "duplicate", "process_changed"]
)
def test_process_binding_fails_closed_when_identity_is_unstable(
    process_binding, monkeypatch, mode
):
    if mode == "inactive":
        monkeypatch.setattr(
            cli.subprocess,
            "check_output",
            lambda *a, **k: "MainPID=0\nActiveState=inactive\n",
        )
    elif mode == "invalid_pid":
        monkeypatch.setattr(
            cli.subprocess,
            "check_output",
            lambda *a, **k: "MainPID=bad\nActiveState=active\n",
        )
    elif mode == "missing":
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda p: (_ for _ in ()).throw(PermissionError("private")),
        )
    elif mode == "duplicate":
        monkeypatch.setattr(
            Path, "read_bytes", lambda p: b"DATABASE_URL=first\0DATABASE_URL=second\0"
        )
    else:
        values = iter(
            ["MainPID=123\nActiveState=active\n", "MainPID=124\nActiveState=active\n"]
        )
        monkeypatch.setattr(
            cli.subprocess, "check_output", lambda *a, **k: next(values)
        )
    with pytest.raises(ValueError):
        cli.serving_environment()


def test_source_identity_binds_commit_tree_and_code_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "CODE", ("owner.py",))
    (tmp_path / "owner.py").write_text("reviewed")
    monkeypatch.setattr(
        cli.subprocess,
        "check_output",
        lambda args, **k: "a" * 40 if args[-1] == "HEAD" else "b" * 40,
    )
    result = cli.source_identity()
    assert result == {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "files": {"owner.py": hashlib.sha256(b"reviewed").hexdigest()},
    }


def test_serving_guard_requires_whole_tracked_tree_clean_and_effective_cwd(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "source_identity", lambda: {"commit": "a" * 40})
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **k: calls.append(args))
    monkeypatch.setattr(
        cli.subprocess,
        "check_output",
        lambda *a, **k: "MainPID=123\nActiveState=active\nWorkingDirectory="
        + str(tmp_path)
        + "\n",
    )
    original = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda p: tmp_path if str(p) == "/proc/123/cwd" else original(p),
    )
    cli.require_serving("a" * 40)
    assert ["git", "diff", "--quiet", "HEAD"] in calls
    with pytest.raises(ValueError):
        cli.require_serving("b" * 40)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "git diff")
        ),
    )
    with pytest.raises(subprocess.CalledProcessError):
        cli.require_serving("a" * 40)


class FakeTransaction:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        self.before = copy.deepcopy(self.engine.state)
        self.engine.log.append("begin")
        return self

    async def __aexit__(self, kind, value, trace):
        if kind:
            self.engine.state = self.before
            self.engine.log.append("rollback")
        else:
            self.engine.log.append("outer-commit")


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    def begin(self):
        return FakeTransaction(self.engine)

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.engine.log.append(sql)
        context = SimpleNamespace(compiled_parameters=[params] if params else [])
        before = self.engine.listeners.get("before_cursor_execute")
        if before:
            before(None, None, sql, params, context, False)
        if sql.startswith("INSERT INTO budget_movement_assignments"):
            self.engine.state["budget_movement_assignments"].append(
                copy.deepcopy(params)
            )
        elif sql.startswith("INSERT INTO budget_version_audit_log"):
            self.engine.state["budget_version_audit_log"].append(
                {k: json.loads(v) if k == "payload" else v for k, v in params.items()}
            )
        after = self.engine.listeners.get("after_cursor_execute")
        if after:
            after(None, None, sql, params, context, False)

    async def scalar(self, statement):
        await self.execute(statement)
        return self.engine.database


class FakeEngine:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.log = []
        self.listeners = {}
        self.sync_engine = self
        self.database = cli.RESTORE_DB

    def connect(self):
        return FakeConnection(self)

    async def dispose(self):
        self.log.append("dispose")


class FakeSession(FakeConnection):
    def __init__(self, bind, **kwargs):
        super().__init__(bind.engine if isinstance(bind, FakeConnection) else bind)

    async def commit(self):
        self.engine.log.append("release-savepoint")


@pytest.fixture
def orchestration(monkeypatch, tmp_path):
    data, before, after = states()
    engine = FakeEngine(before)
    source = {"commit": "a" * 40, "tree": "b" * 40, "files": {"owner": "c" * 64}}
    monkeypatch.setattr(cli, "inputs", lambda: copy.deepcopy(data))
    monkeypatch.setattr(cli, "source_identity", lambda: copy.deepcopy(source))
    monkeypatch.setattr(cli, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(cli, "AsyncSession", FakeSession)
    monkeypatch.setattr(
        cli, "snapshot", AsyncMock(side_effect=lambda s: copy.deepcopy(engine.state))
    )

    def listen(target, name):
        def add(fn):
            target.listeners[name] = fn
            return fn

        return add

    monkeypatch.setattr(cli.event, "listens_for", listen)
    plan = {"plan_sha256": "c" * 64, "assignments": copy.deepcopy(data["assignments"])}

    async def apply(session, **kwargs):
        if cli.completed_core_hash(engine.state, data):
            return {"status": "ALREADY_APPLIED", "plan_sha256": "c" * 64}
        for row in after["budget_movement_assignments"][1:]:
            await session.execute(
                "INSERT INTO budget_movement_assignments(id) VALUES(:id)", row
            )
        row = after["budget_version_audit_log"][-1]
        params = {
            **row,
            "payload": json.dumps(row["payload"]),
            "from_status": "approved",
            "to_status": "approved",
        }
        await session.execute(
            "INSERT INTO budget_version_audit_log(id) VALUES(:id)", params
        )
        return {"status": "APPLIED", "plan_sha256": "c" * 64}

    async def recover(session, **kwargs):
        return {"status": "ALREADY_APPLIED", "plan_sha256": "c" * 64}

    core = SimpleNamespace(
        require_reconciliation_actor=AsyncMock(),
        preview_approved_movement_reconciliation=AsyncMock(return_value=plan),
        apply_approved_movement_reconciliation=AsyncMock(side_effect=apply),
        recover_approved_movement_reconciliation=AsyncMock(side_effect=recover),
    )
    monkeypatch.setattr(cli, "core_module", lambda: core)
    return SimpleNamespace(
        data=data,
        before=before,
        after=after,
        engine=engine,
        core=core,
        source=source,
        output=tmp_path,
    )


def command(fixture, *extra):
    return cli.parser().parse_args(
        ["--output-dir", str(fixture.output), "--restore", *extra]
    )


@pytest.mark.asyncio
async def test_preview_then_atomic_apply_and_receipt_recovery_use_same_core(
    orchestration,
):
    f = orchestration
    preview = await cli.run(command(f))
    assert preview["completed"] is False
    f.core.apply_approved_movement_reconciliation.assert_not_awaited()
    assert not any(s.startswith("INSERT") for s in f.engine.log)
    result = await cli.run(
        command(
            f,
            "--apply",
            "--expected-hash",
            preview["hash"],
            "--expected-commit",
            f.source["commit"],
        )
    )
    assert (
        result["status"] == "COMMITTED_VERIFIED"
        and result["verification"]["new_assignment_count"] == 16
    )
    assert f.engine.log.index(
        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY"
    ) < f.engine.log.index("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
    assert f.engine.log.count("release-savepoint") == 1
    saved = copy.deepcopy(f.engine.state)
    repeat = await cli.run(command(f))
    assert repeat["completed"] is True
    replay = await cli.run(
        command(
            f,
            "--apply",
            "--expected-hash",
            repeat["hash"],
            "--expected-commit",
            f.source["commit"],
        )
    )
    assert replay["core"]["status"] == "ALREADY_APPLIED" and f.engine.state == saved
    assert len(list(f.output.glob("receipt-*.json"))) == 2


@pytest.mark.asyncio
async def test_stale_hash_is_rejected_before_read_write_transaction(orchestration):
    f = orchestration
    with pytest.raises(ValueError, match="Plan drifted"):
        await cli.run(
            command(
                f,
                "--apply",
                "--expected-hash",
                "0" * 64,
                "--expected-commit",
                f.source["commit"],
            )
        )
    f.core.apply_approved_movement_reconciliation.assert_not_awaited()
    assert "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE" not in f.engine.log
    assert f.engine.state == f.before


@pytest.mark.parametrize("count", [1, 17])
@pytest.mark.asyncio
async def test_injected_post_insert_failures_roll_back_entire_outer_batch(
    orchestration, count
):
    f = orchestration
    preview = await cli.run(command(f))
    with pytest.raises(RuntimeError, match="Injected disposable"):
        await cli.run(
            command(
                f,
                "--apply",
                "--expected-hash",
                preview["hash"],
                "--expected-commit",
                f.source["commit"],
                "--fail-after-insert",
                str(count),
            )
        )
    assert f.engine.state == f.before
    assert not list(f.output.glob("committed-*.json"))
    assert "rollback" in f.engine.log


@pytest.mark.asyncio
async def test_wrong_database_never_reaches_classification(orchestration):
    f = orchestration
    f.engine.database = "devnous_db"
    with pytest.raises(ValueError, match="Unexpected connected database"):
        await cli.run(command(f))
    f.core.preview_approved_movement_reconciliation.assert_not_awaited()
    assert f.engine.state == f.before


@pytest.mark.asyncio
async def test_source_code_drift_before_commit_rolls_back_all_inserts(
    orchestration, monkeypatch
):
    f = orchestration
    preview = await cli.run(command(f))
    count = 0

    def source():
        nonlocal count
        count += 1
        return f.source if count <= 3 else {**f.source, "tree": "changed"}

    monkeypatch.setattr(cli, "source_identity", source)
    with pytest.raises(ValueError, match="Source or approved inputs"):
        await cli.run(
            command(
                f,
                "--apply",
                "--expected-hash",
                preview["hash"],
                "--expected-commit",
                f.source["commit"],
            )
        )
    assert f.engine.state == f.before
    assert not list(f.output.glob("committed-*.json"))


@pytest.mark.asyncio
async def test_production_final_approval_is_required_before_engine_creation(
    orchestration, monkeypatch
):
    f = orchestration
    monkeypatch.setattr(cli, "ARTIFACTS", f.output)
    args = cli.parser().parse_args(
        [
            "--output-dir",
            str(f.output),
            "--apply",
            "--expected-hash",
            "0" * 64,
            "--expected-commit",
            f.source["commit"],
        ]
    )
    with pytest.raises(ValueError, match="Separate final"):
        await cli.run(args)
    assert not f.engine.log


@pytest.mark.asyncio
async def test_protected_source_drift_between_two_transactions_blocks_apply(
    orchestration, monkeypatch
):
    f = orchestration
    preview = await cli.run(command(f))
    counter = 0

    async def snapshot(session):
        nonlocal counter
        counter += 1
        state = copy.deepcopy(f.engine.state)
        if counter == 2:
            state["documentos"].append({"new": "concurrent source"})
        return state

    monkeypatch.setattr(cli, "snapshot", snapshot)
    with pytest.raises(ValueError, match="Source changed between preview"):
        await cli.run(
            command(
                f,
                "--apply",
                "--expected-hash",
                preview["hash"],
                "--expected-commit",
                f.source["commit"],
            )
        )
    f.core.apply_approved_movement_reconciliation.assert_not_awaited()
    assert f.engine.state == f.before


@pytest.mark.asyncio
async def test_postcommit_readback_failure_never_claims_verified_success(
    orchestration, monkeypatch
):
    f = orchestration
    preview = await cli.run(command(f))
    counter = 0

    async def snapshot(session):
        nonlocal counter
        counter += 1
        state = copy.deepcopy(f.engine.state)
        if counter == 4:
            state["documentos"].append({"concurrent": "postcommit"})
        return state

    monkeypatch.setattr(cli, "snapshot", snapshot)
    with pytest.raises(ValueError, match="Protected financial source"):
        await cli.run(
            command(
                f,
                "--apply",
                "--expected-hash",
                preview["hash"],
                "--expected-commit",
                f.source["commit"],
            )
        )
    assert len(f.engine.state["budget_movement_assignments"]) == 17
    assert list(f.output.glob("committed-*.json")) and not list(
        f.output.glob("receipt-*.json")
    )
