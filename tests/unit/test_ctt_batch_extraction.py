"""Real source replay with a fake SDK; no credential or network access."""

import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_ctt_batch_observation_inputs import retained as source_fixture  # noqa: F401

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical,
    digest,
    strict_loads,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_observation_inputs import verify_observation_inputs
from devnous.copa_telmex.staff_batch_source_adapter import SourceSnapshotV2
from devnous.tournaments.core.ctt_batch_extraction import (
    PIPELINE,
    BatchDocumentAnalyzer,
    _blank,
)
from devnous.tournaments.core.ctt_ocr_provider import CttProviderFieldCache
from devnous.tournaments.core.ctt_openai_crop_client import OpenAICttCropClient


class SDK:
    def __init__(self, fields):
        self.fields = fields
        self.responses = self
        self.max_retries = None
        self.calls = []
        self.usage = dict(
            input_tokens=50,
            output_tokens=20,
            total_tokens=70,
            input_tokens_details=dict(cached_tokens=0),
        )
        self.mutate = lambda rows: rows
        self.error = None
        self.model = "synthetic-pinned-2026-01-01"
        self.service_tier = "default"

    def with_options(self, **kwargs):
        self.max_retries = kwargs["max_retries"]
        return self

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        result = []
        for part in kwargs["input"][0]["content"]:
            if part["type"] == "input_text":
                identity = strict_loads(part["text"].encode())
                field = identity["crop_id"].split(":", 1)[1]
                result.append(
                    {**identity, "raw_text": self.fields.get(field), "candidates": []}
                )
        return SimpleNamespace(
            output_parsed={"results": self.mutate(result)},
            usage=self.usage,
            model=self.model,
            service_tier=self.service_tier,
        )


def _rematerialize(snapshot, manifest):
    manifest.pop("manifest_sha256", None)
    manifest = validate_source_manifest(manifest)
    scope = strict_loads(snapshot.scope_bytes)
    scope["manifest_sha256"] = manifest["manifest_sha256"]
    return SourceSnapshotV2(
        canonical(manifest),
        snapshot.pdfs,
        snapshot.page_assets,
        canonical(scope),
        snapshot.max_source_bytes,
    )


def _pin_structure(snapshot, profile):
    manifest = strict_loads(snapshot.manifest_json)
    scope = strict_loads(snapshot.scope_bytes)
    document = next(
        d for d in manifest["documents"] if d["document_id"] == scope["document_id"]
    )
    document["extraction"]["structural_observation"] = dict(
        template_id="synthetic-letter",
        category="UNICA",
        source_refs=[
            {k: p[k] for k in ("pdf_sha256", "pdf_page")} for p in scope["source_pages"]
        ],
        physical_slots=[s["slot_ref"] for s in profile["physical_slots"]],
    )
    return _rematerialize(snapshot, manifest)


@pytest.fixture
def setup(source_fixture, tmp_path):  # noqa: F811
    snapshot, pages, profile, reference = source_fixture
    profile = copy.deepcopy(profile)
    snapshot = _pin_structure(snapshot, profile)
    scope = strict_loads(snapshot.scope_bytes)
    artifact = dict(
        phase_binding={
            key: scope[key] for key in ("tenant_id", "edition_id", "manifest_sha256")
        },
        minimum_roster_size=2,
        maximum_roster_size=20,
        required_staff_slots=[1, 2],
        optional_curp_kinds=["PLAYER", "STAFF"],
        required_photo_kinds=[],
    )
    policy_hash = digest(artifact)
    artifact["policy_hash"] = "sha256:" + policy_hash
    layout = Path("config/layout_ctt_2026.json")
    import hashlib

    profile.update(
        layout_sha256=hashlib.sha256(layout.read_bytes()).hexdigest(),
        policy_artifacts={policy_hash: artifact},
        provider=dict(
            name="openai",
            model_revision="synthetic-pinned-2026-01-01",
            pipeline_version=PIPELINE,
            pricing_revision="synthetic-units-v1",
            maximum_call_units=100000,
            batch_size=16,
            max_output_tokens=8000,
            max_request_bytes=4 * 1024**2,
            max_analysis_bytes=4 * 1024**2,
            pricing=dict(
                revision="synthetic-units-v1",
                model_revision="synthetic-pinned-2026-01-01",
                currency="USD",
                unit="micro_usd",
                input_per_million_micro_usd=1000000,
                cached_input_per_million_micro_usd=100000,
                output_per_million_micro_usd=2000000,
                model_input_token_limit=20000,
            ),
        ),
    )
    fields = reference["verification_input"]["proposed_fields"]
    sdk = SDK(fields)
    analyzer = BatchDocumentAnalyzer(
        provider=OpenAICttCropClient(sdk, model_revision="synthetic-pinned-2026-01-01"),
        cache=CttProviderFieldCache(tmp_path / "cache"),
        layout_path=layout,
    )
    reservations, receipts = [], []

    async def reserve(units, fingerprint):
        reservations.append((units, fingerprint))
        return str(uuid4())

    async def reconcile(reservation, actual):
        receipts.append((reservation, actual))

    source = dict(
        document_id=scope["document_id"], source_pages=pages, policy_hash=policy_hash
    )
    return SimpleNamespace(
        snapshot=snapshot,
        pages=pages,
        profile=profile,
        source=source,
        analyzer=analyzer,
        sdk=sdk,
        reservations=reservations,
        receipts=receipts,
        reserve=reserve,
        reconcile=reconcile,
    )


async def analyze(s):
    return await s.analyzer.analyze(
        s.snapshot,
        s.source,
        profile=s.profile,
        reserve=s.reserve,
        reconcile=s.reconcile,
    )


@pytest.mark.asyncio
async def test_grouped_reader_replays_all_source_inputs_and_caches_without_spend(setup):
    result = await analyze(setup)
    assert result["auto_ready"], result["issues"]
    assert result["players"][1]["slot_ref"] == "page:1:jugador_3"
    assert result["players"][0]["birth_date"] == "2000-01-01"
    assert result["source_physical_slots"] == [f"page:1:jugador_{i}" for i in (1, 2, 3)]
    assert (
        verify_observation_inputs(setup.snapshot, setup.pages, setup.profile, result)
        == result["verification_input"]
    )
    assert len(setup.sdk.calls) == 6
    assert len(setup.reservations) == len(setup.receipts) == 6
    assert setup.sdk.max_retries == 0
    assert all(c["store"] is False for c in setup.sdk.calls)
    assert all(c["service_tier"] == "default" for c in setup.sdk.calls)
    assert "verified_by_human" not in str(result)
    again = await analyze(setup)
    assert again == result
    assert len(setup.sdk.calls) == len(setup.reservations) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ["duplicate", "extra", "missing", "hash"])
async def test_exact_requested_ids_and_hashes_required(setup, malformation):
    def mutate(rows):
        if malformation == "duplicate":
            return rows[:-1] + [rows[0]]
        if malformation == "extra":
            return rows[:-1] + [{**rows[-1], "crop_id": "unexpected"}]
        if malformation == "missing":
            return rows[:-1]
        return [{**r, "crop_sha256": "0" * 64} for r in rows]

    setup.sdk.mutate = mutate
    with pytest.raises(BatchContractError):
        await analyze(setup)
    assert len(setup.receipts) == 1
    assert not list(setup.analyzer.cache.root.rglob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, -1, "70", True])
async def test_unknown_usage_keeps_full_reservation_held(setup, usage):
    setup.sdk.usage = usage
    with pytest.raises(BatchContractError, match="USAGE_UNCERTAIN"):
        await analyze(setup)
    assert len(setup.reservations) == 1
    assert not setup.receipts


@pytest.mark.asyncio
async def test_timeout_holds_reservation_and_retry_reserves_a_new_attempt(setup):
    setup.sdk.error = TimeoutError("synthetic")
    with pytest.raises(TimeoutError):
        await analyze(setup)
    assert len(setup.reservations) == 1 and not setup.receipts
    setup.sdk.error = None
    await analyze(setup)
    assert len(setup.reservations) == 7
    assert len(set(fingerprint for _, fingerprint in setup.reservations)) == 7


@pytest.mark.asyncio
async def test_source_or_pinned_configuration_errors_make_zero_calls(setup):
    setup.source["document_id"] = "0" * 64
    with pytest.raises(BatchContractError, match="SOURCE_MISMATCH"):
        await analyze(setup)
    assert not setup.sdk.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("model_revision", "different"),
        ("pipeline_version", "legacy"),
        ("batch_size", 7),
        ("batch_size", 17),
        ("maximum_call_units", 0),
        ("max_output_tokens", True),
        ("max_request_bytes", 1),
        ("pricing_revision", ""),
    ],
)
async def test_provider_budget_and_revisions_are_explicit_not_defaults(
    setup, key, value
):
    setup.profile["provider"][key] = value
    with pytest.raises(BatchContractError):
        await analyze(setup)
    assert not setup.sdk.calls and not setup.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alteration",
    ["layout", "policy_hash", "policy_scope", "policy_invalid", "provider_missing"],
)
async def test_exact_full_policy_scope_and_layout_are_checked(setup, alteration):
    if alteration == "layout":
        setup.profile["layout_sha256"] = "0" * 64
    elif alteration == "provider_missing":
        setup.analyzer.provider = None
    else:
        artifact = next(iter(setup.profile["policy_artifacts"].values()))
        if alteration == "policy_hash":
            artifact["minimum_roster_size"] = 1
        elif alteration == "policy_scope":
            artifact["phase_binding"]["manifest_sha256"] = "0" * 64
        else:
            artifact["minimum_roster_size"] = 0
            old = setup.source["policy_hash"]
            key = digest({k: v for k, v in artifact.items() if k != "policy_hash"})
            artifact["policy_hash"] = "sha256:" + key
            setup.profile["policy_artifacts"] = {key: artifact}
            setup.source["policy_hash"] = key
            assert key != old
    with pytest.raises(BatchContractError):
        await analyze(setup)
    assert not setup.sdk.calls


@pytest.mark.asyncio
async def test_request_memory_bound_checked_before_reserve(setup):
    setup.profile["provider"]["max_request_bytes"] = 1024
    with pytest.raises(BatchContractError, match="REQUEST_TOO_LARGE"):
        await analyze(setup)
    assert not setup.reservations and not setup.sdk.calls


@pytest.mark.asyncio
async def test_missing_budget_store_closed_before_calls(setup):
    with pytest.raises(BatchContractError, match="RESERVATION_REQUIRED"):
        await setup.analyzer.analyze(
            setup.snapshot, setup.source, profile=setup.profile
        )
    assert not setup.sdk.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("slots.1.1.presence", "ABSENT"),
        ("slots.1.2.presence", "PRESENT"),
        ("staff.1.role", "auxiliar"),
        ("staff.1.first_name", None),
        ("players.1.birth_date", "1/1/00"),
    ],
)
async def test_doubt_and_unmapped_printed_slots_remain_exceptions(setup, field, value):
    setup.sdk.fields[field] = value
    result = await analyze(setup)
    assert not result["auto_ready"]
    assert result["issues"]


@pytest.mark.asyncio
async def test_second_observation_does_not_see_first_output_and_disagreement_blocks(
    setup,
):
    original = setup.sdk.mutate

    def mutate(rows):
        if rows[0]["crop_id"].startswith("context-v1:"):
            return [
                (
                    {**r, "raw_text": "Changed"}
                    if r["crop_id"].endswith("first_name")
                    else r
                )
                for r in rows
            ]
        return original(rows)

    setup.sdk.mutate = mutate
    result = await analyze(setup)
    assert not result["auto_ready"]
    assert "READINGS_DISAGREE" in result["issues"]
    for call in setup.sdk.calls:
        assert all("assistant" != x["role"] for x in call["input"])


@pytest.mark.asyncio
async def test_over_budget_usage_never_marks_ready(setup):
    setup.sdk.usage["input_tokens"] = 20001
    with pytest.raises(BatchContractError, match="USAGE_UNCERTAIN"):
        await analyze(setup)
    assert not setup.receipts


def test_invalid_blank_profile_is_not_null_proof(setup):
    from PIL import Image

    with Image.new("RGB", (10, 10), "white") as image:
        with pytest.raises(BatchContractError):
            _blank(image, (0, 0, 10, 10), {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("revision", "other"),
        ("model_revision", "other"),
        ("currency", "EUR"),
        ("unit", "tokens"),
        ("input_per_million_micro_usd", 0),
        ("output_per_million_micro_usd", -1),
        ("cached_input_per_million_micro_usd", -1),
        ("cached_input_per_million_micro_usd", 1000001),
        ("model_input_token_limit", True),
    ],
)
async def test_price_quote_must_be_explicit_complete_and_revision_bound(
    setup, key, value
):
    setup.profile["provider"]["pricing"][key] = value
    with pytest.raises(BatchContractError, match="PRICING_REQUIRED"):
        await analyze(setup)
    assert not setup.sdk.calls and not setup.reservations


@pytest.mark.asyncio
async def test_full_model_context_price_reserved_before_every_call(setup):
    setup.profile["provider"]["maximum_call_units"] = 35999
    with pytest.raises(BatchContractError, match="BUDGET_BOUND_INSUFFICIENT"):
        await analyze(setup)
    assert not setup.sdk.calls
    setup.profile["provider"]["maximum_call_units"] = 36000
    await analyze(setup)
    assert all(units == 36000 for units, _ in setup.reservations)
    assert all(actual == 90 for _, actual in setup.receipts)


@pytest.mark.asyncio
async def test_known_cached_usage_is_charged_with_quote_and_rounded_up(setup):
    setup.sdk.usage["input_tokens_details"]["cached_tokens"] = 49
    await analyze(setup)
    assert all(actual == 46 for _, actual in setup.receipts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing_cached",
        "cached_exceeds_input",
        "negative_output",
        "output_exceeds_limit",
        "inconsistent_total",
    ],
)
async def test_incomplete_or_inconsistent_usage_holds_and_stops(setup, change):
    if change == "missing_cached":
        setup.sdk.usage["input_tokens_details"] = None
    elif change == "cached_exceeds_input":
        setup.sdk.usage["input_tokens_details"]["cached_tokens"] = 51
    elif change == "negative_output":
        setup.sdk.usage["output_tokens"] = -1
    elif change == "output_exceeds_limit":
        setup.sdk.usage["output_tokens"] = 8001
    else:
        setup.sdk.usage["total_tokens"] = 1
    with pytest.raises(BatchContractError, match="USAGE_UNCERTAIN"):
        await analyze(setup)
    assert len(setup.sdk.calls) == len(setup.reservations) == 1
    assert not setup.receipts


@pytest.mark.asyncio
async def test_no_reservation_means_no_network(setup):
    setup.reserve = lambda *_: None
    with pytest.raises(BatchContractError, match="RESERVATION_REQUIRED"):
        await analyze(setup)
    assert not setup.sdk.calls


@pytest.mark.asyncio
async def test_sdk_must_honor_disabled_internal_retries(setup):
    def ignore(**kwargs):
        return setup.sdk

    setup.sdk.with_options = ignore
    setup.sdk.max_retries = 2
    with pytest.raises(BatchContractError, match="RETRY_POLICY_REQUIRED"):
        await analyze(setup)
    assert not setup.sdk.calls


@pytest.mark.asyncio
async def test_no_policy_selection_by_guessing_manifest_match(setup):
    del setup.source["policy_hash"]
    with pytest.raises(BatchContractError, match="POLICY_REQUIRED"):
        await analyze(setup)
    assert not setup.sdk.calls


def _rebind(setup, snapshot, pages):
    snapshot = _pin_structure(snapshot, setup.profile)
    setup.snapshot, setup.pages = snapshot, pages
    scope = strict_loads(snapshot.scope_bytes)
    setup.source.update(document_id=scope["document_id"], source_pages=pages)
    artifact = next(iter(setup.profile["policy_artifacts"].values()))
    artifact["phase_binding"] = {
        key: scope[key] for key in ("tenant_id", "edition_id", "manifest_sha256")
    }
    policy_hash = digest({k: v for k, v in artifact.items() if k != "policy_hash"})
    artifact["policy_hash"] = "sha256:" + policy_hash
    setup.profile["policy_artifacts"] = {policy_hash: artifact}
    setup.source["policy_hash"] = policy_hash


@pytest.mark.asyncio
async def test_provided_staff_photo_is_rederived_and_preserved_as_json_metadata(setup):
    from test_ctt_batch_observation_inputs import _paint_photo

    # _paint_photo builds its own evidence from the supplied person schema.
    from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields

    original = await analyze(setup)
    assert semantic_fields(original)
    snapshot, pages, _, _ = _paint_photo(
        (setup.snapshot, setup.pages, setup.profile, original)
    )
    _rebind(setup, snapshot, pages)
    result = await analyze(setup)
    assert result["auto_ready"], result["issues"]
    photo = result["staff"][0]["photo"]
    assert set(photo) == {"sha256", "ahash", "photo_path"}
    assert result["staff"][0]["photo_evidence"]["mode"] == "SOURCE_RECTANGLE"
    verify_observation_inputs(setup.snapshot, setup.pages, setup.profile, result)


@pytest.mark.asyncio
async def test_private_budget_store_adapter_validates_profile_and_charges_micro_usd(
    setup,
):
    class Store:
        def __init__(self):
            self.reservations, self.receipts = [], []

        async def budget_reserve(self, run_id, reservation_id, units):
            self.reservations.append((run_id, reservation_id, units))
            return {"reservation_id": str(reservation_id), "maximum_units": units}

        async def budget_reconcile(self, reservation_id, actual, receipt_hash):
            self.receipts.append((reservation_id, actual, receipt_hash))

    store = Store()
    run = dict(run_id=str(uuid4()), profile_hash=digest(setup.profile))
    result = await setup.analyzer.analyze(
        setup.snapshot, setup.source, profile=setup.profile, run=run, store=store
    )
    assert result["auto_ready"]
    assert len(store.receipts) == len(store.reservations) == 6
    assert all(actual == 90 for _, actual, _ in store.receipts)
    run["profile_hash"] = "0" * 64
    with pytest.raises(BatchContractError, match="RESERVATION_REQUIRED"):
        await setup.analyzer.analyze(
            setup.snapshot, setup.source, profile=setup.profile, run=run, store=store
        )


@pytest.mark.asyncio
async def test_empty_mapped_interior_slot_is_preserved_without_reindexing(setup):
    slots = setup.profile["physical_slots"]
    slots[1]["person_index"] = 2
    slots[2]["person_index"] = 3
    for view in setup.profile["observation_views"]:
        fields = view["fields"]
        occupied = {
            k.replace("players.2.", "players.3."): v
            for k, v in fields.items()
            if k.startswith("players.2.")
        }
        fields.update(occupied)
        for key in (
            "first_name",
            "last_name",
            "birth_date",
            "curp",
            "presence",
            "photo_sha256",
        ):
            fields["players.2." + key] = copy.deepcopy(fields["slots.1.2.presence"])
    old_values = copy.deepcopy(setup.sdk.fields)
    for field, value in old_values.items():
        if field.startswith("players.2."):
            setup.sdk.fields[field.replace("players.2.", "players.3.")] = value
    for key in ("first_name", "last_name", "birth_date", "curp", "photo_sha256"):
        setup.sdk.fields["players.2." + key] = None
    setup.sdk.fields["players.2.presence"] = "ABSENT"
    result = await analyze(setup)
    assert [p["slot_ref"] for p in result["players"]] == [s["slot_ref"] for s in slots]
    assert result["players"][1]["presence"] == "ABSENT"
    assert result["players"][1]["first_name"] is None
    assert result["players"][2]["first_name"] == "Charlie"
    assert "NONCONTIGUOUS_PERSON_MAPPING" not in result["issues"]
    assert "ROSTER_SIZE_INVALID" not in result["issues"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", [None, "different-2026-01-01", "synthetic-pinned-latest"]
)
async def test_returned_model_must_equal_snapshot_before_reconciliation_and_cache(
    setup, model
):
    setup.sdk.model = model
    with pytest.raises(BatchContractError, match="MODEL_UNCERTAIN"):
        await analyze(setup)
    assert len(setup.reservations) == len(setup.sdk.calls) == 1
    assert not setup.receipts
    assert not list(setup.analyzer.cache.root.rglob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["synthetic-latest", "synthetic", "synthetic-2026-02-30"]
)
async def test_only_valid_dated_model_snapshots_are_pinned(setup, model):
    setup.analyzer.provider.model_revision = model
    setup.profile["provider"]["model_revision"] = model
    with pytest.raises(BatchContractError, match="SNAPSHOT_REQUIRED"):
        await analyze(setup)
    assert not setup.sdk.calls and not setup.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "template", "slots", "refs", "category"])
async def test_only_exact_cas_structural_template_inventory_precedes_predictions(
    setup, change
):
    manifest = strict_loads(setup.snapshot.manifest_json)
    document = manifest["documents"][0]
    structure = document["extraction"]["structural_observation"]
    if change == "missing":
        document["extraction"] = {}
    elif change == "template":
        structure["template_id"] = "unknown"
    elif change == "slots":
        structure["physical_slots"] = structure["physical_slots"][:-1]
    elif change == "refs":
        structure["source_refs"][0]["pdf_page"] = 2
    else:
        structure["category"] = ""
    snapshot = _rematerialize(setup.snapshot, manifest)
    with pytest.raises(BatchContractError, match="SOURCE_STRUCTURE_REQUIRED"):
        await setup.analyzer.analyze(
            snapshot,
            setup.source,
            profile=setup.profile,
            reserve=setup.reserve,
            reconcile=setup.reconcile,
        )
    assert not setup.sdk.calls and not setup.reservations


@pytest.mark.asyncio
async def test_compatible_plural_template_uses_cas_identity_without_geometry_mutation(
    setup,
):
    setup.profile.pop("template_id")
    setup.profile["template_ids"] = ["synthetic-letter", "synthetic-compatible-alias"]
    original_geometry = copy.deepcopy(setup.profile["observation_views"])
    result = await analyze(setup)
    assert result["auto_ready"], result["issues"]
    assert result["template_id"] == "synthetic-letter"
    assert setup.profile["observation_views"] == original_geometry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tier", [None, "auto", "flex", "scale", "priority", "fast", "ultrafast"]
)
async def test_actual_nonstandard_or_unknown_service_tier_holds_budget_and_stops(
    setup, tier
):
    # Enum values come from the installed official SDK ServiceTier alias.
    setup.sdk.service_tier = tier
    with pytest.raises(BatchContractError, match="SERVICE_TIER_UNCERTAIN"):
        await analyze(setup)
    assert setup.sdk.calls[0]["service_tier"] == "default"
    assert len(setup.sdk.calls) == len(setup.reservations) == 1
    assert not setup.receipts
    assert not list(setup.analyzer.cache.root.rglob("*.json"))
