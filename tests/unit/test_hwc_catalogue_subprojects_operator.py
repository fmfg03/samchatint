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
