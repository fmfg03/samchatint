"""Private observation-only composition, never a listener/capture bootstrap."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from deployment.ctt_protected_review import runtime
from test_ctt_batch_structural_extraction import case as _case

from devnous.copa_telmex import staff_batch_analysis_runtime as analysis_runtime
from devnous.copa_telmex.staff_batch_contracts import canonical, digest
from devnous.copa_telmex.staff_batch_inventory import InventorySourceAdapter

structural_case = _case


@pytest.fixture
def settings(tmp_path):
    dsn = tmp_path / "verifier.dsn"
    dsn.write_text("postgresql://ctt_test_verifier:synthetic@127.0.0.1:55492/synthetic")
    dsn.chmod(0o600)
    return dict(
        schema="ctt.protected-runtime.v2",
        role="batch-workflow",
        expected_uid=os.geteuid(),
        public_uid=os.geteuid() + 10000,
        backend_pin="synthetic-backend",
        verifier_dsn_file=str(dsn),
        capture_enabled=False,
        preparation_only=True,
    )


def test_source_settings_local_check_has_no_sql_api_or_capture(settings, monkeypatch):
    monkeypatch.setattr(
        runtime.asyncpg, "create_pool", lambda *a, **k: pytest.fail("connected SQL")
    )
    runtime.validate_preparation_settings(settings)
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_local_config(settings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("capture_enabled", True),
        ("preparation_only", False),
        ("role", "writer"),
        ("schema", "old"),
        ("public_uid", -1),
        ("expected_uid", True),
        ("expected_uid", -1),
        ("backend_pin", ""),
    ],
)
def test_preparation_mode_cannot_enable_or_impersonate_capture(settings, field, value):
    settings[field] = value
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_preparation_settings(settings)


def test_public_uid_and_extra_authority_keys_denied(settings):
    settings["public_uid"] = settings["expected_uid"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_preparation_settings(settings)
    settings["public_uid"] += 10000
    settings["writer_dsn_file"] = settings["verifier_dsn_file"]
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.validate_preparation_settings(settings)


def test_invalid_private_dsn_does_not_disclose_contents(settings):
    Path(settings["verifier_dsn_file"]).write_text(
        "https://SENSITIVE_SENTINEL@untrusted.invalid"
    )
    with pytest.raises(runtime.RuntimeConfigurationDenied) as e:
        runtime.validate_preparation_settings(settings)
    assert "SENSITIVE_SENTINEL" not in str(e.value)


def test_structural_factory_pins_endpoint_and_does_not_call_provider(
    structural_case, tmp_path
):
    c = structural_case
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)
    calls = []
    transport = object()
    sdk = SimpleNamespace()

    def factory(**kwargs):
        calls.append(kwargs)
        return sdk

    analyzer, actual = analysis_runtime.create_structural_producer(
        profile=c.profile,
        cache_root=cache,
        credential_reader=lambda: "synthetic-only",
        sdk_factory=factory,
        http_client=transport,
    )
    assert actual is sdk and analyzer.provider.client is sdk
    assert calls == [
        dict(
            api_key="synthetic-only",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=30,
            http_client=transport,
        )
    ]
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("fault", ["profile", "cache"])
def test_bad_profile_or_private_root_before_key_access(
    structural_case, tmp_path, fault
):
    c = structural_case
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)
    if fault == "profile":
        c.profile["provider"]["pricing"]["unit"] = "tokens"
    else:
        cache.chmod(0o755)
    with pytest.raises(Exception):
        analysis_runtime.create_structural_producer(
            profile=c.profile,
            cache_root=cache,
            credential_reader=lambda: pytest.fail("read credential"),
            sdk_factory=lambda **k: pytest.fail("created SDK"),
            http_client=object(),
        )


def test_structural_factory_sanitizes_key_constructor_error(structural_case, tmp_path):
    c = structural_case
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)

    def fail(**kwargs):
        raise RuntimeError("SENSITIVE_SENTINEL")

    with pytest.raises(ValueError) as e:
        analysis_runtime.create_structural_producer(
            profile=c.profile,
            cache_root=cache,
            credential_reader=lambda: "synthetic-only",
            sdk_factory=fail,
            http_client=object(),
        )
    assert "SENSITIVE_SENTINEL" not in str(e.value) and e.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_private_observation_composition_uses_only_verifier_pool(
    settings, structural_case, tmp_path, monkeypatch, completed
):
    c = structural_case
    if completed:
        obs, report = await c.analyzer.analyze(
            c.snapshot, context=c.context, store=c.store
        )
        c.context.update(
            status="completed",
            observations=obs,
            report=report,
            observations_sha256=digest(obs),
            report_sha256=digest(report),
        )
    calls = []

    async def pool(stack, path):
        calls.append(path)
        return "private-verifier-pool"

    monkeypatch.setattr(runtime, "_pool", pool)
    from devnous.copa_telmex import staff_batch_store

    monkeypatch.setattr(
        staff_batch_store, "PostgresBatchReviewStore", lambda p, **k: c.store
    )
    from contextlib import contextmanager

    @contextmanager
    def snapshot(self, inventory):
        assert inventory == c.inventory
        yield c.snapshot

    monkeypatch.setattr(InventorySourceAdapter, "open_verified_inventory", snapshot)
    closed = []

    async def close():
        closed.append(True)

    def producer(**kwargs):
        if completed:
            pytest.fail("SDK/key needed for recovery")
        return c.analyzer, SimpleNamespace(close=close)

    monkeypatch.setattr(analysis_runtime, "create_structural_producer", producer)
    monkeypatch.setattr(
        runtime.uvicorn, "Server", lambda *a, **k: pytest.fail("opened listener")
    )
    obs, report = await runtime.run_source_preparation(
        settings,
        UUID(c.context["preparation_id"]),
        inventory_root=tmp_path,
        cache_root=c.analyzer.cache.root,
    )
    assert calls == [settings["verifier_dsn_file"]]
    assert report["teams_created"] == 0 and len(obs["pages"]) == 2
    assert closed == ([] if completed else [True])
    assert len(c.sdk.calls) == 1


def test_command_local_check_has_no_runtime_admission(
    settings, tmp_path, monkeypatch, capsys
):
    config = tmp_path / "config.json"
    config.write_bytes(canonical(settings))
    config.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    monkeypatch.setattr(
        "sys.argv",
        [
            "runtime",
            "--config",
            str(config),
            "--prepare-sources",
            "10000000-0000-0000-0000-000000000001",
            "--inventory-root",
            str(source),
            "--preparation-cache-root",
            str(cache),
            "--check",
        ],
    )
    monkeypatch.setattr(
        runtime, "run_source_preparation", lambda *a, **k: pytest.fail("ran provider")
    )
    runtime.main()
    body = json.loads(capsys.readouterr().out)
    assert body == dict(
        configuration="valid_local",
        mode="source-preparation",
        runtime_admission_checked=False,
    )


@pytest.mark.asyncio
async def test_private_export_preserves_exact_envelopes_and_denies_replacement(
    structural_case, tmp_path
):
    c = structural_case
    obs, report = await c.analyzer.analyze(c.snapshot, context=c.context, store=c.store)
    out = tmp_path / "export"
    out.mkdir(mode=0o700)
    runtime.export_source_preparation(
        out, envelope=obs, report=report, inventory=c.inventory, profile=c.profile
    )
    runtime.export_source_preparation(
        out, envelope=obs, report=report, inventory=c.inventory, profile=c.profile
    )
    values = {p.name: json.loads(p.read_text()) for p in out.iterdir()}
    assert (
        values["source-observations.json"] == obs
        and values["source-inventory.json"] == c.inventory
    )
    assert (
        values["structural-profile.json"] == c.profile
        and values["source-preparation-report.json"] == report
    )
    assert all(p.stat().st_mode & 0o077 == 0 for p in out.iterdir())
    tampered = copy.deepcopy(report)
    tampered["observations_sha256"] = "f" * 64
    with pytest.raises(runtime.RuntimeConfigurationDenied):
        runtime.export_source_preparation(
            out, envelope=obs, report=tampered, inventory=c.inventory, profile=c.profile
        )
    assert json.loads((out / "source-preparation-report.json").read_text()) == report


@pytest.mark.asyncio
async def test_command_executes_only_observation(
    settings, structural_case, tmp_path, monkeypatch, capsys
):
    c = structural_case
    obs, report = await c.analyzer.analyze(c.snapshot, context=c.context, store=c.store)
    config = tmp_path / "config.json"
    config.write_bytes(canonical(settings))
    config.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    cache = tmp_path / "command-cache"
    cache.mkdir(mode=0o700)
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    args = [
        "runtime",
        "--config",
        str(config),
        "--prepare-sources",
        c.context["preparation_id"],
        "--inventory-root",
        str(source),
        "--preparation-cache-root",
        str(cache),
        "--preparation-output-root",
        str(out),
    ]
    calls = []

    async def run(config, preparation_id, **kwargs):
        calls.append(kwargs)
        return obs, report

    monkeypatch.setattr(runtime, "run_source_preparation", run)
    import asyncio

    monkeypatch.setattr("sys.argv", args)
    await asyncio.to_thread(runtime.main)
    body = json.loads(capsys.readouterr().out)
    assert body["teams_created"] == 0 and body["proposed_documents"] == 1
    assert "EXPLICIT TEAM" not in str(body)
    assert calls == [dict(inventory_root=source, cache_root=cache, output_root=out)]

    async def fail(*args, **kwargs):
        raise RuntimeError("SENSITIVE_SENTINEL")

    monkeypatch.setattr(runtime, "run_source_preparation", fail)
    with pytest.raises(SystemExit) as e:
        await asyncio.to_thread(runtime.main)
    assert "SENSITIVE_SENTINEL" not in str(e.value)


@pytest.mark.parametrize("fault", ["no_roots", "no_mode", "same_output"])
def test_command_rejects_ambiguous_modes(settings, tmp_path, monkeypatch, fault):
    config = tmp_path / "config.json"
    config.write_bytes(canonical(settings))
    config.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    args = ["runtime", "--config", str(config)]
    if fault != "no_mode":
        args += ["--prepare-sources", "10000000-0000-0000-0000-000000000001"]
    if fault != "no_roots":
        args += [
            "--inventory-root",
            str(source),
            "--preparation-cache-root",
            str(cache),
        ]
    if fault == "same_output":
        args += ["--preparation-output-root", str(source)]
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        runtime.main()
