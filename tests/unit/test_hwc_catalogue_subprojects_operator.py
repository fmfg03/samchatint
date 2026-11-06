"""Exact scope/economy and fail-closed boundaries of the approved HWC repair."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "hwc_scope_operator", ROOT / "scripts/repair_hwc_catalogue_subprojects.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def fixture():
    data = json.loads((ROOT / cli.CONFIG).read_text())
    state = {table: [] for table in cli.TABLES}
    state["tournaments"] = [
        {
            "id": cli.HWC,
            "etapas": sorted({r["scope_after"] for r in data["scope_updates"]}),
            "categorias": [],
        }
    ]
    for row in data["scope_updates"]:
        state["budget_concepts"].append(
            {
                "id": row["concept_id"],
                "tournament_id": cli.HWC,
                "concept_name": row["concept_name"],
                "active": True,
                "metadata": {
                    "scope_mode": "global",
                    "applicable_phase_labels": [],
                    "applicable_phase_keys": [],
                    "applicable_subproject_labels": [],
                    "applicable_subproject_keys": [],
                    "ssot_subproyecto": "original provenance",
                },
                "source": "admin_ui",
                "updated_at": "before",
            }
        )
    for row in data["untouched_active_concepts"]:
        state["budget_concepts"].append(
            {
                "id": row["id"],
                "concept_name": row["name"],
                "tournament_id": cli.HWC,
                "active": True,
                "metadata": {"scope_mode": "global"},
            }
        )
    state["budget_concepts"].append(
        {
            "id": "retired",
            "tournament_id": cli.HWC,
            "concept_name": "Retired",
            "active": False,
            "metadata": {"scope_mode": "global"},
        }
    )
    state["budget_versions"] = [
        {
            "id": cli.VERSION,
            "status": "approved",
            "notes": "Borrador",
            "updated_at": "before",
        }
    ]
    state["budget_lines"] = [
        {
            "id": cli.ZERO_LINE,
            "tournament_id": cli.HWC,
            "budget_version_id": cli.VERSION,
            "budget_concept_id": data["zero_line_phase_updates"][0][
                "budget_concept_id"
            ],
            "phase": data["zero_line_phase_updates"][0]["phase_before"],
            "budget_amount": "0.00",
            "updated_at": "before",
        }
    ]
    state["budget_tournament_weekly_revisions"] = [
        {
            "id": "revision",
            "tournament_id": cli.HWC,
            "financial_root_version_id": cli.VERSION,
            "week_number": 52,
            "is_current": True,
            "plan_hash": "original",
        }
    ]
    state["documentos"] = [{"id": "paid", "estado": "pagado", "fase": None}]
    after = copy.deepcopy(state)
    for current in after["budget_concepts"]:
        target = next(
            (r for r in data["scope_updates"] if r["concept_id"] == current["id"]), None
        )
        if target:
            current["metadata"] = cli.scope_metadata(
                current, target["scope_after"], state["tournaments"][0]
            )
            current["updated_at"] = "after"
    after["budget_lines"][0]["phase"] = data["zero_line_phase_updates"][0][
        "phase_after"
    ]
    after["budget_lines"][0]["updated_at"] = "after"
    return data, state, after


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE unsafe(id int)",
        "DELETE FROM budget_concepts",
        "UPDATE documentos SET estado='pagado'",
        "INSERT INTO accounting_polizas(id) VALUES(1)",
        "SELECT 1; UPDATE budget_versions SET status='approved'",
        "WITH changed AS (UPDATE budget_lines SET phase='x' RETURNING *) "
        "SELECT * FROM changed",
        "DO $$ BEGIN END $$",
        "CALL repair()",
    ],
)
def test_sql_rejects_schema_operational_and_compound_changes(sql):
    with pytest.raises(ValueError):
        cli.sql_guard(sql, True)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE budget_versions SET status='approved'",
        "SELECT id FROM tournaments FOR SHARE",
        "SELECT id FROM budget_lines FOR UPDATE",
        "SELECT pg_advisory_xact_lock(1)",
    ],
)
def test_preview_cannot_write_or_lock(sql):
    with pytest.raises(ValueError):
        cli.sql_guard(sql, False)


def test_sql_guard_binds_catalogue_and_zero_line_to_exact_owner_scope():
    cid = next(iter(cli.APPROVED_CONCEPT_IDS))
    cli.sql_guard(
        "UPDATE budget_concepts SET metadata=$1 WHERE id=$2",
        True,
        parameters={"concept_id": cid},
        owner_scope=cid,
    )
    cli.sql_guard(
        "UPDATE budget_lines SET phase=$1 WHERE id=$2",
        True,
        parameters={"line_id": cli.ZERO_LINE},
    )
    for params, scope in [
        ({"concept_id": "foreign"}, "foreign"),
        ({"concept_id": cid}, None),
        ({"concept_id": "foreign"}, cid),
    ]:
        with pytest.raises(ValueError):
            cli.sql_guard(
                "UPDATE budget_concepts SET metadata=$1",
                True,
                parameters=params,
                owner_scope=scope,
            )
    with pytest.raises(ValueError):
        cli.sql_guard(
            "UPDATE budget_lines SET phase=$1", True, parameters={"line_id": "positive"}
        )


def test_initial_or_complete_state_and_exact_diff_preserve_history():
    data, before, after = fixture()
    assert cli.validate_state(before, data) is False
    assert cli.validate_state(after, data) is True
    cli.preservation(before, after, data)
    assert after["documentos"] == before["documentos"]
    assert after["budget_concepts"][-1] == before["budget_concepts"][-1]
    assert (
        after["budget_concepts"][0]["metadata"]["ssot_subproyecto"]
        == "original provenance"
    )


def test_partial_or_unknown_scope_is_not_silently_overwritten():
    data, before, after = fixture()
    before["budget_concepts"][0] = after["budget_concepts"][0]
    with pytest.raises(ValueError, match="Partial prior"):
        cli.validate_state(before, data)
    before["budget_concepts"][0]["metadata"]["applicable_phase_labels"] = ["Foreign"]
    with pytest.raises(ValueError, match="scope changed"):
        cli.validate_state(before, data)


@pytest.mark.parametrize(
    "table,index,key,value",
    [
        ("documentos", 0, "estado", "cancelado"),
        ("budget_concepts", -1, "active", True),
        ("budget_concepts", 0, "concept_name", "Renamed"),
        ("budget_concepts", 0, "source", "invented"),
        ("budget_lines", 0, "budget_amount", "0.01"),
        ("budget_lines", 0, "budget_concept_id", "different"),
        ("budget_versions", 0, "notes", "Final"),
        ("budget_tournament_weekly_revisions", 0, "week_number", 51),
    ],
)
def test_preservation_rejects_unapproved_effects(table, index, key, value):
    data, before, after = fixture()
    after[table][index][key] = value
    with pytest.raises(ValueError):
        cli.preservation(before, after, data)


def test_unrelated_weekly_renewal_and_history_deletion_fail():
    data, before, after = fixture()
    after["budget_tournament_weekly_revisions"].append(
        {"id": "foreign", "tournament_id": "foreign", "week_number": 52}
    )
    with pytest.raises(ValueError):
        cli.preservation(before, after, data)
    after["budget_tournament_weekly_revisions"].pop()
    before["budget_version_audit_log"] = [{"id": "existing", "payload": {}}]
    with pytest.raises(ValueError, match="history"):
        cli.preservation(before, after, data)


@pytest.mark.asyncio
async def test_idempotent_owner_application_performs_no_operations(monkeypatch):
    mutation = AsyncMock(side_effect=AssertionError("idempotent repair wrote"))
    monkeypatch.setattr(cli.service, "update_budget_concept", mutation)
    assert await cli.apply_owners(object(), {"completed": True}, {}) == {
        "idempotent": True,
        "revisions": [],
    }
    mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_after_scopes_does_not_reach_financial_owners(monkeypatch):
    data, before, _ = fixture()
    update, deactivate = AsyncMock(), AsyncMock()
    monkeypatch.setattr(cli.service, "update_budget_concept", update)
    monkeypatch.setattr(cli.overlay, "deactivate_tournament_overlay", deactivate)
    with pytest.raises(RuntimeError, match="Injected restored"):
        await cli.apply_owners(
            object(), {"completed": False, "before": before}, data, fail_after=1
        )
    assert update.await_count == 1
    assert update.await_args.kwargs["ensure_schema"] is False
    deactivate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        ["--fail-after", "1"],
        ["--apply", "--fail-after", "1"],
        ["--restore", "--apply", "--fail-after", "81"],
    ],
)
async def test_faults_are_restricted_to_exact_restore_application(extra):
    args = cli.parser().parse_args(["--output-dir", "/tmp/unused-hwc-tests", *extra])
    with pytest.raises(ValueError, match="Failure injection"):
        await cli.run(args)


def test_inputs_fail_closed_before_using_changed_configuration(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / cli.CONFIG).write_text("{}")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="configuration changed"):
        cli.inputs()


def test_production_source_guard_rejects_wrong_expected_commit():
    with pytest.raises(ValueError, match="Exact reviewed"):
        cli.require_serving("not-a-reviewed-sha")


def test_production_source_guard_rejects_any_tracked_release_drift(monkeypatch):
    head = "a" * 40
    monkeypatch.setattr(cli, "source_identity", lambda: {"commit": head})
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["git", "diff"]:
            assert command == ["git", "diff", "--quiet", "HEAD"]
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        cli.require_serving(head)
    assert len(commands) == 2


@pytest.fixture
def backend_binding(monkeypatch):
    runtime = {
        "DATABASE_URL": (
            "postgresql://operator:private-test-password@localhost/devnous_db"
        ),
        "POSTGRESQL_URL": (
            "postgresql+asyncpg://operator:private-test-password"
            "@localhost:5432/devnous_db"
        ),
        "SAMCHAT_ENV_FILE": str(cli.PRODUCTION_ENV),
    }
    canonical = {"DATABASE_URL": runtime["DATABASE_URL"]}
    monkeypatch.setattr(cli, "serving_environment", lambda: ("1234", runtime))
    monkeypatch.setattr(cli, "dotenv_values", lambda path: canonical)
    args = cli.parser().parse_args(["--output-dir", "/tmp/unused-hwc-tests"])
    return args, runtime, canonical


def test_backend_binding_accepts_driver_normalization_and_sanitizes_plan(
    backend_binding, monkeypatch, tmp_path
):
    args, _, _ = backend_binding
    # A candidate source directory is allowed for read-only previews.
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    url, identity = cli.production_backend(args)
    assert url.drivername == "postgresql+asyncpg"
    assert url.port == 5432
    assert identity["host"] == "localhost"
    encoded = json.dumps(identity)
    for secret in ("private-test-password", "operator", "postgresql://", "sha256"):
        assert secret not in encoded
    changed = {**identity, "main_pid": "5678"}
    assert cli.digest({"backend": identity}) != cli.digest({"backend": changed})


@pytest.mark.parametrize(
    "replacement",
    [
        "postgresql://operator:private-test-password@other-host/devnous_db",
        "postgresql://operator:different-password@localhost/devnous_db",
        "postgresql://other-user:private-test-password@localhost/devnous_db",
        "postgresql+psycopg://operator:private-test-password@localhost/devnous_db",
        "postgres://operator:private-test-password@localhost/devnous_db",
        "postgresql://operator:private-test-password@localhost/other_database",
        "postgresql://operator:private-test-password@localhost/devnous_db?host=other",
        "malformed private-test-password",
    ],
)
def test_backend_binding_rejects_other_endpoint_credentials_or_alias(
    backend_binding, replacement
):
    args, _, canonical = backend_binding
    canonical["DATABASE_URL"] = replacement
    with pytest.raises(ValueError) as error:
        cli.production_backend(args)
    assert "private-test-password" not in str(error.value)
    assert "different-password" not in str(error.value)


@pytest.mark.parametrize("key", ["DATABASE_URL", "POSTGRESQL_URL", "SAMCHAT_ENV_FILE"])
def test_backend_binding_rejects_missing_runtime_environment(backend_binding, key):
    args, runtime, _ = backend_binding
    runtime.pop(key)
    with pytest.raises(ValueError, match="environment incomplete"):
        cli.production_backend(args)


def test_backend_binding_rejects_noncanonical_runtime_file(backend_binding):
    args, runtime, _ = backend_binding
    runtime["SAMCHAT_ENV_FILE"] = "/tmp/other.env"
    with pytest.raises(ValueError, match="environment incomplete"):
        cli.production_backend(args)


def test_backend_binding_rejects_divergent_runtime_alias(backend_binding):
    args, runtime, _ = backend_binding
    runtime["POSTGRESQL_URL"] = runtime["POSTGRESQL_URL"].replace(
        "localhost", "different-host"
    )
    with pytest.raises(ValueError, match="active serving backend"):
        cli.production_backend(args)


@pytest.mark.asyncio
async def test_custom_env_file_rejected_before_engine_creation(monkeypatch):
    args = cli.parser().parse_args(
        ["--output-dir", "/tmp/unused-hwc-tests", "--env-file", "/tmp/other.env"]
    )
    monkeypatch.setattr(cli, "inputs", lambda: {})
    monkeypatch.setattr(cli, "source_identity", lambda: {})

    def forbidden(*args, **kwargs):
        pytest.fail("Backend validation must precede environment reads and connects")

    monkeypatch.setattr(cli, "serving_environment", forbidden)
    monkeypatch.setattr(cli, "create_async_engine", forbidden)
    with pytest.raises(ValueError, match="Canonical production environment"):
        await cli.run(args)


def test_missing_active_runtime_fails_closed_without_secrets(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess,
        "check_output",
        lambda *args, **kwargs: "MainPID=0\nActiveState=inactive\n",
    )
    with pytest.raises(ValueError, match="Active serving process unavailable"):
        cli.serving_environment()


def test_source_identity_includes_reviewed_operator_and_pinned_configuration():
    identity = cli.source_identity()
    assert set(identity["files"]) == set(cli.CODE)
    assert identity["files"][cli.CONFIG] == cli.CONFIG_SHA


@pytest.mark.parametrize("tamper", [None, "source", "spec", "plan", "identities"])
def test_inputs_bind_approval_bytes_source_and_exact_scope(
    monkeypatch, tmp_path, tamper
):
    import hashlib

    data = json.loads((ROOT / cli.CONFIG).read_text())
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"controlled original source fixture")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.json").write_bytes(b'{"approved": true}')
    spec = tmp_path / cli.SPEC
    spec.parent.mkdir(parents=True)
    spec.write_bytes(b"approved spec fixture")
    data["source"] = str(source)
    data["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    approval = data["approval"]
    approval["source_sha256"] = data["source_sha256"]
    approval["spec_sha256"] = hashlib.sha256(spec.read_bytes()).hexdigest()
    approval["plan_sha256"] = hashlib.sha256(
        (artifacts / "plan.json").read_bytes()
    ).hexdigest()
    (artifacts / "approval.json").write_text(json.dumps(approval))
    if tamper == "identities":
        data["scope_updates"][1]["concept_id"] = data["scope_updates"][0]["concept_id"]
    config = tmp_path / cli.CONFIG
    config.parent.mkdir()
    config.write_text(json.dumps(data))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ARTIFACTS", artifacts)
    monkeypatch.setattr(
        cli, "CONFIG_SHA", hashlib.sha256(config.read_bytes()).hexdigest()
    )
    if tamper in ("source", "spec", "plan"):
        target = {"source": source, "spec": spec, "plan": artifacts / "plan.json"}[
            tamper
        ]
        target.write_bytes(b"changed after approval")
    if tamper:
        with pytest.raises(ValueError, match="changed|required"):
            cli.inputs()
    else:
        assert cli.inputs() == data


@pytest.mark.parametrize(
    "restart,duplicate", [(False, False), (True, False), (False, True)]
)
def test_service_environment_reads_only_required_process_keys(
    monkeypatch, restart, duplicate
):
    outputs = iter(
        [
            "MainPID=123\nActiveState=active\n",
            f"MainPID={456 if restart else 123}\nActiveState=active\n",
        ]
    )
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: next(outputs))
    raw = (
        b"DATABASE_URL=fake\0POSTGRESQL_URL=fake\0"
        b"SAMCHAT_ENV_FILE=/etc/samchat/samchat.env\0UNRELATED=private\0"
    )
    if duplicate:
        raw += b"DATABASE_URL=other\0"

    def read(path):
        assert str(path) == "/proc/123/environ"
        return raw

    monkeypatch.setattr(Path, "read_bytes", read)
    if restart or duplicate:
        with pytest.raises(ValueError, match="changed|unavailable"):
            cli.serving_environment()
    else:
        pid, environment = cli.serving_environment()
        assert pid == "123"
        assert set(environment) == {
            "DATABASE_URL",
            "POSTGRESQL_URL",
            "SAMCHAT_ENV_FILE",
        }
        assert "private" not in repr(environment)


@pytest.mark.asyncio
async def test_snapshot_captures_every_protected_and_budget_table_in_stable_order():
    class Rows:
        def __init__(self, table):
            self.table = table

        def scalars(self):
            return [{"id": self.table}]

    statements = []

    async def execute(statement):
        sql = str(statement)
        statements.append(sql)
        table = sql.split(" FROM ")[1].split()[0]
        assert sql.endswith("ORDER BY to_jsonb(t)::text")
        return Rows(table)

    from types import SimpleNamespace

    state = await cli.snapshot(SimpleNamespace(execute=execute))
    assert tuple(state) == cli.TABLES
    assert len(statements) == len(cli.TABLES)
    assert all(state[table] == [{"id": table}] for table in cli.TABLES)


@pytest.mark.asyncio
async def test_build_plan_checks_actor_and_selected_version_before_snapshot(
    monkeypatch,
):
    data, before, _ = fixture()
    calls = []

    async def actor(session, actor_id):
        calls.append("actor")
        assert actor_id == cli.ACTOR

    async def version(session, version_id, **kwargs):
        calls.append("version")
        assert version_id == cli.VERSION
        assert kwargs == {"require_selected": True}

    async def snapshot(session):
        assert calls == ["actor", "version"]
        return before

    monkeypatch.setattr(cli.overlay, "_require_budget_actor", actor)
    monkeypatch.setattr(cli.overlay, "_version", version)
    monkeypatch.setattr(cli, "snapshot", snapshot)
    monkeypatch.setattr(cli, "verify_effective", AsyncMock(return_value={"week": 52}))
    plan = await cli.build_plan(object(), {"commit": "reviewed"}, data)
    assert plan["completed"] is False
    assert plan["before"] == before
    assert plan["effective_before"] == {"week": 52}
    assert plan["actor"] == cli.ACTOR


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "economic", "audit"])
async def test_verify_requires_preserved_economics_and_exact_audit(
    monkeypatch, failure
):
    data, before, after = fixture()
    after["budget_version_audit_log"] = [
        {
            "id": "repair-audit",
            "event_type": cli.EVENT,
            "budget_version_id": cli.VERSION,
            "actor_empleado_id": cli.ACTOR,
            "payload": {
                "config_sha256": cli.CONFIG_SHA,
                "source_sha256": data["source_sha256"],
                "scope_updates": data["scope_updates"],
                "zero_line_phase_updates": data["zero_line_phase_updates"],
            },
        }
    ]
    if failure == "audit":
        after["budget_version_audit_log"][0]["payload"]["source_sha256"] = "wrong"
    monkeypatch.setattr(cli, "snapshot", AsyncMock(return_value=after))
    monkeypatch.setattr(
        cli,
        "verify_effective",
        AsyncMock(return_value={"week": 51 if failure == "economic" else 52}),
    )
    plan = {"before": before, "effective_before": {"week": 52}}
    if failure:
        with pytest.raises(ValueError, match="income changed|canonical repair audit"):
            await cli.verify(object(), plan, data)
    else:
        result = await cli.verify(object(), plan, data)
        assert result["protected_preserved"] is True
        assert result["Borrador_preserved"] is True
        assert result["scope_count"] == 74


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "preview",
        "apply",
        "repeat",
        "stale",
        "source_drift",
        "backend_drift",
        "wrong_connected",
        "owner_failure",
        "postcommit_failure",
    ],
)
async def test_run_transaction_authority_receipts_and_failure_boundaries(
    monkeypatch, tmp_path, scenario
):
    """Exercise CLI orchestration; real owner rollback is covered by PG tests."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    data, before, _ = fixture()
    events, statements, listeners = [], [], []
    source = {"commit": "a" * 40, "files": {"operator": "reviewed"}}
    plan = {"before": before, "completed": scenario == "repeat"}
    backend = {"mode": "production", "database": "devnous_db", "main_pid": "123"}
    args = cli.parser().parse_args(["--output-dir", str(tmp_path)])
    args.apply = scenario != "preview"
    args.expected_commit = source["commit"]
    args.expected_hash = cli.digest({**plan, "backend": backend})
    if scenario == "stale":
        args.expected_hash = "stale"
    monkeypatch.setattr(cli, "inputs", lambda: data)
    source_calls = 0

    def identity():
        nonlocal source_calls
        source_calls += 1
        if scenario == "source_drift" and source_calls > 1:
            return {"commit": "changed"}
        return source

    monkeypatch.setattr(cli, "source_identity", identity)
    backend_calls = 0

    def binding(args):
        nonlocal backend_calls
        backend_calls += 1
        if scenario == "backend_drift" and backend_calls > 2:
            return "different-private-url", {**backend, "main_pid": "456"}
        return "private-url-never-saved", backend

    monkeypatch.setattr(cli, "production_backend", binding)
    serving = []
    monkeypatch.setattr(cli, "require_serving", serving.append)

    def listen(engine, name):
        assert name == "before_cursor_execute"

        def register(callback):
            listeners.append(callback)
            return callback

        return register

    monkeypatch.setattr(cli.event, "listens_for", listen)

    class Connection:
        @asynccontextmanager
        async def begin(self):
            events.append("outer_begin")
            try:
                yield
            except Exception:
                events.append("outer_rollback")
                raise
            else:
                events.append("outer_commit")

        async def execute(self, statement, parameters=None):
            statements.append((str(statement), parameters))
            context = SimpleNamespace(compiled_parameters=[parameters or {}])
            listeners[0](None, None, str(statement), parameters, context, False)

        async def scalar(self, statement):
            assert str(statement) == "SELECT current_database()"
            return "other_db" if scenario == "wrong_connected" else "devnous_db"

    connection = Connection()

    class Engine:
        url = SimpleNamespace(database="devnous_db")
        sync_engine = object()

        @asynccontextmanager
        async def connect(self):
            yield connection

        async def dispose(self):
            events.append("disposed")

    engine = Engine()
    monkeypatch.setattr(cli, "create_async_engine", lambda *a, **k: engine)

    class Session:
        def __init__(self, *args, **kwargs):
            if kwargs:
                assert kwargs == {
                    "bind": connection,
                    "join_transaction_mode": "create_savepoint",
                }
            else:
                assert args == (engine,)
                events.append("postcommit_readback")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement, parameters=None):
            await connection.execute(statement, parameters)

        async def commit(self):
            events.append("savepoint_commit")

    monkeypatch.setattr(cli, "AsyncSession", Session)
    monkeypatch.setattr(cli, "build_plan", AsyncMock(return_value=plan))

    async def owners(session, planned, approved, **kwargs):
        assert approved is data
        assert planned["backend"] == backend
        events.append("owners")
        if scenario == "owner_failure":
            raise RuntimeError("Owner rejected fixture")
        return {"idempotent": scenario == "repeat", "revisions": []}

    monkeypatch.setattr(cli, "apply_owners", owners)
    verifications = []

    async def verify(session, planned, approved):
        verifications.append("verify")
        if scenario == "postcommit_failure" and len(verifications) == 2:
            raise ValueError("Readback mismatch")
        return {"protected_preserved": True}

    monkeypatch.setattr(cli, "verify", verify)
    failures = {
        "stale": "Plan drifted",
        "source_drift": "Source/code changed",
        "backend_drift": "Serving backend changed",
        "wrong_connected": "connected database",
        "owner_failure": "Owner rejected",
        "postcommit_failure": "Readback mismatch",
    }
    if scenario in failures:
        with pytest.raises((ValueError, RuntimeError), match=failures[scenario]):
            await cli.run(args)
        assert not list(tmp_path.glob("receipt-*.json"))
        if scenario != "postcommit_failure":
            assert "outer_rollback" in events
            assert "outer_commit" not in events
    else:
        receipt = await cli.run(args)
        assert receipt["status"] == (
            "PREVIEW" if not args.apply else "COMMITTED_VERIFIED"
        )
        if args.apply:
            assert events.index("savepoint_commit") < events.index("outer_commit")
            assert events.index("outer_commit") < events.index("postcommit_readback")
            assert len(verifications) == 2
            assert len(list(tmp_path.glob("receipt-*.json"))) == 1
            assert len(list(tmp_path.glob("pending-*.json"))) == 1
            assert len(list(tmp_path.glob("committed-*.json"))) == 1
        else:
            assert "owners" not in events
            assert not verifications
            assert all(
                "FOR UPDATE" not in sql and "FOR SHARE" not in sql
                for sql, _ in statements
            )
    assert events[-1] == "disposed"
    assert serving == ([source["commit"]] if args.apply else [])
    assert "SERIALIZABLE" in statements[0][0]
    assert ("READ ONLY" in statements[0][0]) == (not args.apply)
    for path in tmp_path.glob("*.json"):
        assert "private-url" not in path.read_text()
    if args.apply and scenario != "wrong_connected":
        lock_sql = [sql for sql, _ in statements]
        assert "FOR SHARE" in lock_sql[3]
        assert "pg_advisory_xact_lock" in lock_sql[4]
        concept_locks = [
            params for sql, params in statements if "FROM budget_concepts" in sql
        ]
        assert concept_locks == [{"ids": sorted(cli.APPROVED_CONCEPT_IDS)}]
