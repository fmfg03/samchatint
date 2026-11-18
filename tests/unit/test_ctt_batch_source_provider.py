"""Acceptance of retained-source preparation without any external provider call."""

import base64
import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import fitz
import pytest
from test_ctt_batch_observation_inputs import _evidence
from test_ctt_batch_observation_inputs import retained as _pixel_fixture
from test_ctt_batch_plan_builder import proposal as _proposal_fixture
from test_ctt_batch_sources_v2 import EDITION, LIMITS
from test_ctt_batch_sources_v2 import sources as _source_fixture

from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    digest,
    source_refs,
    stable_document_id,
    strict_loads,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_corrections import apply_proposals
from devnous.copa_telmex.staff_batch_observation_inputs import verify_observation_inputs
from devnous.copa_telmex.staff_batch_source_adapter import (
    PrivateBatchSourceProvider,
    SourceBundleV2,
    SourceCheckAdapterV2,
)
from devnous.copa_telmex.staff_batch_source_admission import (
    _publish,
    prepare_source_admission_v2,
)
from devnous.copa_telmex.staff_batch_source_service import (
    BatchCatalogSourceProvider,
    PrivateBatchAnalysisCache,
    analysis_identity,
)
from devnous.copa_telmex.staff_batch_store import BatchDenied
from devnous.copa_telmex.staff_photo_store import derive_rectangular_review_photo
from devnous.tournaments.core.ctt_batch_qualification import calculate_metrics

proposal = _proposal_fixture
sources = _source_fixture
pixel_retained = _pixel_fixture


@pytest.fixture
def preparation(sources, tmp_path, pixel_retained, request):
    root, manifest = sources
    pixel_snapshot, _, pixel_profile, pixel_analysis = pixel_retained
    profile = copy.deepcopy(pixel_profile)
    profile["policy_artifacts"] = {}
    for index, view in enumerate(profile["observation_views"]):
        view["fields"]["source.continuation"] = {
            "page_index": 2,
            "x": 0.05 if index == 0 else 0.04,
            "y": 0.05 if index == 0 else 0.04,
            "width": 0.4 if index == 0 else 0.42,
            "height": 0.2 if index == 0 else 0.22,
        }
    first_pdf = next(iter(pixel_snapshot.pdfs.values()))
    first_pdf.seek(0)
    raw = first_pdf.read()
    colored = getattr(request, "param", None)
    if colored in {"colored_player_photo", "colored_staff_photo"}:
        field = (
            "players.1.photo_sha256"
            if colored == "colored_player_photo"
            else "staff.1.photo_sha256"
        )
        box = profile["observation_views"][0]["fields"][field]
        with fitz.open(stream=raw, filetype="pdf") as original:
            page = original[0]
            page.draw_rect(
                fitz.Rect(
                    box["x"] * page.rect.width,
                    box["y"] * page.rect.height,
                    (box["x"] + box["width"]) * page.rect.width,
                    (box["y"] + box["height"]) * page.rect.height,
                ),
                color=(0.8, 0.1, 0.1),
                fill=(0.8, 0.1, 0.1),
            )
            raw = original.tobytes()
    (root / manifest["pdfs"][0]["filename"]).write_bytes(raw)
    manifest["pdfs"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    document = manifest["documents"][0]
    document["source_slices"] = [
        {"pdf_sha256": pdf["sha256"], "pages": [1]} for pdf in manifest["pdfs"]
    ]
    document["document_id"] = stable_document_id(
        EDITION["edition_id"], source_refs(document["source_slices"])
    )
    document["extraction"] = {
        "structural_observation": {
            "template_id": "synthetic-letter",
            "category": "UNICA",
            "source_refs": [
                {"pdf_sha256": sha, "pdf_page": page}
                for sha, page in source_refs(document["source_slices"])
            ],
            "physical_slots": [slot["slot_ref"] for slot in profile["physical_slots"]],
        }
    }
    manifest.pop("manifest_sha256")
    manifest = validate_source_manifest(manifest)
    cas = tmp_path / "cas"
    cas.mkdir(mode=0o700)
    cache_root = tmp_path / "observations"
    cache_root.mkdir(mode=0o700)
    with prepare_source_admission_v2(
        canonical(manifest), root, edition=EDITION, limits=LIMITS
    ) as prepared:
        _publish(prepared, cas, uuid4())
        plan = strict_loads(prepared.plan_bytes)
    source = {
        "manifest": manifest,
        "manifest_sha256": manifest["manifest_sha256"],
        "document_id": manifest["documents"][0]["document_id"],
        "source_payload_hash": digest(document["extraction"]),
        "session_id": str(uuid4()),
        "source_pages": [
            {k: v for k, v in page.items() if k != "document_id"}
            for page in plan["pages"]
        ],
    }
    profile_hash = digest(profile)
    policy = "a" * 64
    run = {
        "account_id": str(uuid4()),
        "principal_id": str(uuid4()),
        "edition_id": EDITION["edition_id"],
        "run_id": str(uuid4()),
        "manifest_hashes": [manifest["manifest_sha256"]],
        "profile_hash": profile_hash,
    }
    context = {
        "tenant_id": EDITION["tenant_id"],
        "edition_id": EDITION["edition_id"],
        "run_id": run["run_id"],
        "principal_id": run["principal_id"],
        "profile_hash": profile_hash,
        "documents": [source],
        "capabilities": [
            {"manifest_sha256": manifest["manifest_sha256"], "policy_hash": policy}
        ],
    }
    calls = []

    async def current_context(*args):
        calls.append(("context", args))
        return copy.deepcopy(context)

    def build(actual, actual_source, analysis, snapshot):
        calls.append(("build", actual["policy_hash"]))
        assert actual_source["document_id"] == source["document_id"]
        snapshot.verify_unchanged()
        return {
            "document_id": source["document_id"],
            "source_pages": source["source_pages"],
            "status": "ready",
            "auto_ready": True,
            "snapshot": {"expected_head": "retained-head"},
        }

    cache = PrivateBatchAnalysisCache(cache_root)
    provider = BatchCatalogSourceProvider(
        store=SimpleNamespace(preparation_context=current_context),
        source_adapter=SourceCheckAdapterV2(
            PrivateBatchSourceProvider(cas),
            max_source_bytes=LIMITS.max_pdf_bytes,
            max_asset_bytes=LIMITS.max_page_bytes,
            max_manifest_bytes=LIMITS.max_manifest_bytes,
        ),
        profiles={profile_hash: profile},
        builders={policy: SimpleNamespace(build_batch=build)},
        analysis_cache=cache,
    )
    analysis = copy.deepcopy(pixel_analysis)
    analysis.update(
        document_id=source["document_id"],
        source_binding_hash=digest(source["source_pages"]),
        auto_ready=True,
        issues=[],
        template_id="synthetic-letter",
    )
    with provider.adapter.open_verified_snapshot(
        provider._source_scope(context, source)
    ) as snapshot:
        _evidence(snapshot, source["source_pages"], profile, analysis)
    analysis["readings"] = copy.deepcopy(analysis["verification_input"]["readings"])
    return SimpleNamespace(
        provider=provider,
        context=context,
        run=run,
        source=source,
        analysis=analysis,
        cache=cache,
        calls=calls,
        cas=cas,
        original_root=root,
    )


@pytest.mark.asyncio
async def test_initial_preparation_has_no_executable_rows_or_provider_calls(
    preparation,
):
    p = preparation
    result = await p.provider.prepare_run(p.context, p.run["manifest_hashes"])
    assert p.calls == []
    assert result["sample_documents"] == [p.source["document_id"]]
    assert len(result["source_pages"]) == 2
    member = result["members"][0]
    assert member["status"] == "analysis_pending"
    assert (
        member["payload_hash"] is member["scope_hash"] is member["attempt_id"] is None
    )
    with pytest.raises(BatchDenied, match="ANALYSIS_MISSING"):
        await p.provider.analyze(p.run)
    assert [kind for kind, _ in p.calls] == ["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "template", "category", "refs", "slots"])
async def test_missing_or_changed_structure_is_exception_before_cache_or_producer(
    preparation, monkeypatch, fault
):
    p = preparation
    manifest = copy.deepcopy(p.source["manifest"])
    document = manifest["documents"][0]
    structure = document["extraction"]["structural_observation"]
    if fault == "missing":
        document["extraction"] = {}
    elif fault == "template":
        structure["template_id"] = "unsupported"
    elif fault == "category":
        structure["category"] = None
    elif fault == "refs":
        structure["source_refs"].reverse()
    else:
        structure["physical_slots"].pop()
    manifest.pop("manifest_sha256")
    manifest = validate_source_manifest(manifest)
    p.source.update(
        manifest=manifest,
        manifest_sha256=manifest["manifest_sha256"],
        source_payload_hash=digest(document["extraction"]),
    )
    p.run["manifest_hashes"] = [manifest["manifest_sha256"]]
    p.context["capabilities"][0]["manifest_sha256"] = manifest["manifest_sha256"]

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid structure reached source reader or producer")

    monkeypatch.setattr(p.cache, "read", forbidden)
    monkeypatch.setattr(p.provider.adapter, "open_verified_snapshot", forbidden)
    result = await p.provider.analyze(p.run)
    member = result["members"][0]
    assert member["status"] == "exception"
    assert member["issues"] == [{"code": "STRUCTURAL_OBSERVATION_REQUIRED"}]
    assert member["payload_hash"] is member["scope_hash"] is None
    assert result["metrics"]["teams"] == {"numerator": 0, "denominator": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["template", "category", "outcome"])
async def test_cached_prediction_cannot_select_new_source_strata_or_sample(
    preparation, fault
):
    p = preparation
    before = await p.provider.prepare_run(p.context, p.run["manifest_hashes"])
    analysis = copy.deepcopy(p.analysis)
    if fault == "template":
        analysis["template_id"] = "predicted-different-template"
    elif fault == "category":
        analysis["team"]["category"] = "predicted-different-category"
    else:
        analysis.update(auto_ready=False, issues=[{"code": "READER_DISAGREEMENT"}])
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), analysis)
    after = await p.provider.analyze(p.run)
    assert after["sample_documents"] == before["sample_documents"]
    assert after["documents"] == before["documents"]
    assert after["members"][0]["status"] == "exception"
    assert after["documents"][0]["template_id"] == "synthetic-letter"
    assert after["documents"][0]["category"] == "UNICA"
    assert after["metrics"]["teams"] == {"numerator": 0, "denominator": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["input_hash", "occupied_slot"])
async def test_invalid_reader_evidence_is_exception_before_freezing_automatic_yield(
    preparation, fault
):
    p = preparation
    analysis = copy.deepcopy(p.analysis)
    if fault == "input_hash":
        analysis["verification_input"]["readings"][0]["input_hash"] = "0" * 64
    else:
        analysis["players"].pop()
        with p.provider.adapter.open_verified_snapshot(
            p.provider._source_scope(p.context, p.source)
        ) as snapshot:
            _evidence(
                snapshot,
                p.source["source_pages"],
                p.provider.profiles[p.run["profile_hash"]],
                analysis,
                statuses=["PRESENT", "ABSENT", "ABSENT"],
            )
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), analysis)
    result = await p.provider.analyze(p.run)
    assert result["members"][0]["status"] == "exception"
    assert result["members"][0]["issues"] == [{"code": "OBSERVATION_INPUTS_INVALID"}]
    assert result["metrics"]["teams"] == {"numerator": 0, "denominator": 1}
    assert all(kind != "build" for kind, _ in p.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["colored_player_photo"], indirect=True)
async def test_two_null_photo_readings_cannot_drop_a_supplied_source_photo(preparation):
    p = preparation
    analysis = copy.deepcopy(p.analysis)
    assert analysis["players"][0]["photo"] is None
    assert all(
        reading["fields"]["players.1.photo_sha256"] is None
        for reading in analysis["verification_input"]["readings"]
    )
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), analysis)
    result = await p.provider.analyze(p.run)
    assert result["members"][0]["status"] == "exception"
    assert result["members"][0]["issues"] == [{"code": "OBSERVATION_INPUTS_INVALID"}]
    assert result["metrics"]["teams"] == {"numerator": 0, "denominator": 1}
    assert all(kind != "build" for kind, _ in p.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["colored_staff_photo"], indirect=True)
async def test_supplied_staff_crop_cannot_use_another_source_rectangle(
    preparation, proposal
):
    p = preparation
    builder = proposal[0]
    profile = p.provider.profiles[p.run["profile_hash"]]
    analysis = copy.deepcopy(p.analysis)
    with p.provider.adapter.open_verified_snapshot(
        p.provider._source_scope(p.context, p.source)
    ) as snapshot:
        source_page = p.source["source_pages"][0]
        geometry_page = SimpleNamespace(
            page_index=1,
            asset_id=UUID(source_page["asset_id"]),
            image_sha256="sha256:" + source_page["image_sha256"],
        )

        def supplied(box):
            geometry = builder._geometry(box, geometry_page, snapshot, minimum_pixels=1)
            photo = derive_rectangular_review_photo(
                snapshot.page_assets[geometry_page.asset_id],
                geometry,
                max_page_bytes=16 * 1024 * 1024,
            )
            return {
                "photo_path": photo.photo_path,
                "sha256": photo.sha256,
                "ahash": photo.ahash,
            }, {
                "mode": "SOURCE_RECTANGLE",
                "geometry": geometry,
            }

        spec = profile["observation_views"][0]["fields"]["staff.1.photo_sha256"]
        exact = {
            "page_index": 1,
            "x": round(spec["x"] * source_page["width"]),
            "y": round(spec["y"] * source_page["height"]),
            "width": round(spec["width"] * source_page["width"]),
            "height": round(spec["height"] * source_page["height"]),
        }
        staff = analysis["staff"][0]
        staff["photo"], staff["photo_evidence"] = supplied(exact)
        _evidence(snapshot, p.source["source_pages"], profile, analysis)
        assert (
            verify_observation_inputs(
                snapshot, p.source["source_pages"], profile, analysis
            )
            == analysis["verification_input"]
        )
        staff["photo"], staff["photo_evidence"] = supplied(
            analysis["staff"][1]["boxes"]["full_name"]
        )
        _evidence(snapshot, p.source["source_pages"], profile, analysis)
        analysis["readings"] = copy.deepcopy(analysis["verification_input"]["readings"])
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), analysis)
    result = await p.provider.analyze(p.run)
    assert result["members"][0]["status"] == "exception"
    assert result["members"][0]["issues"] == [{"code": "OBSERVATION_INPUTS_INVALID"}]
    assert result["metrics"]["teams"] == {"numerator": 0, "denominator": 1}
    assert all(kind != "build" for kind, _ in p.calls)


def test_configured_producer_requires_separate_budget_store(preparation):
    p = preparation
    with pytest.raises(ValueError, match="separate analysis budget store"):
        BatchCatalogSourceProvider(
            store=p.provider.store,
            source_adapter=p.provider.adapter,
            profiles=p.provider.profiles,
            builders=p.provider.builders,
            analysis_cache=p.cache,
            analysis_producer=object(),
        )


@pytest.mark.asyncio
async def test_budget_store_is_delivered_to_producer_and_cache_hit_avoids_second_call(
    preparation,
):
    p = preparation
    budget = object()
    produced = []

    async def analyze(snapshot, source, *, profile, run, store):
        assert store is budget and source == {
            **p.source,
            "policy_hash": p.context["capabilities"][0]["policy_hash"],
        }
        assert profile is p.provider.profiles[p.run["profile_hash"]]
        snapshot.verify_unchanged()
        produced.append(run["run_id"])
        return copy.deepcopy(p.analysis)

    p.provider.analysis_producer = SimpleNamespace(analyze=analyze)
    p.provider.analysis_budget_store = budget
    first = await p.provider.analyze(p.run)
    second = await p.provider.analyze(p.run)
    assert produced == [p.run["run_id"]]
    assert first["members"][0]["status"] == second["members"][0]["status"] == "ready"
    assert (
        p.cache.read(analysis_identity(p.run["profile_hash"], p.source)) == p.analysis
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["document_id", "source_binding_hash"])
async def test_cached_observation_cannot_change_source_binding(preparation, changed):
    p = preparation
    analysis = {**p.analysis, changed: "0" * 64}
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), analysis)
    with pytest.raises(BatchDenied, match="SOURCE_UNAVAILABLE"):
        await p.provider.analyze(p.run)
    assert all(kind != "build" for kind, _ in p.calls)


@pytest.mark.asyncio
async def test_changed_original_prevents_retained_observation_reuse(preparation):
    p = preparation
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), p.analysis)
    original = next((p.cas / "pdfs").iterdir())
    original.chmod(0o600)
    original.write_bytes(b"replaced original")
    with pytest.raises(ValueError):
        await p.provider.analyze(p.run)
    assert all(kind != "build" for kind, _ in p.calls)


@pytest.mark.asyncio
async def test_private_source_view_preserves_each_physical_part(preparation):
    p = preparation
    member = {"snapshot": {"source": p.source}}
    run = {"tenant_id": EDITION["tenant_id"], "edition_id": EDITION["edition_id"]}
    pages = [await p.provider.source_page(run, member, index) for index in (1, 2)]
    assert all(raw.startswith(b"\x89PNG\r\n\x1a\n") for raw in pages)
    assert pages[0] != pages[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [True, 0, 3])
async def test_private_source_view_denies_unknown_or_coerced_page(preparation, index):
    p = preparation
    with pytest.raises(BatchDenied):
        await p.provider.source_page(
            {"tenant_id": EDITION["tenant_id"], "edition_id": EDITION["edition_id"]},
            {"snapshot": {"source": p.source}},
            index,
        )


@pytest.fixture
def verification(pixel_retained):
    snapshot, pages, profile, analysis = pixel_retained
    manifest = strict_loads(snapshot.manifest_json)
    doc = manifest["documents"][0]
    doc["extraction"] = {
        "structural_observation": {
            "template_id": profile["template_id"],
            "category": analysis["team"]["category"],
            "source_refs": [
                {"pdf_sha256": sha, "pdf_page": number}
                for sha, number in source_refs(doc["source_slices"])
            ],
            "physical_slots": [s["slot_ref"] for s in profile["physical_slots"]],
        }
    }
    manifest.pop("manifest_sha256")
    manifest = validate_source_manifest(manifest)
    raw_pdfs = {}
    for sha, stream in snapshot.pdfs.items():
        stream.seek(0)
        raw_pdfs[sha] = stream.read()

    @contextmanager
    def open_bundle(scope):
        yield SourceBundleV2(
            manifest,
            {sha: BytesIO(raw) for sha, raw in raw_pdfs.items()},
            {
                UUID(page["asset_id"]): BytesIO(
                    snapshot.page_assets[UUID(page["asset_id"])]
                )
                for page in pages
            },
        )

    profile_hash = digest(profile)
    provider = BatchCatalogSourceProvider(
        store=object(),
        source_adapter=SourceCheckAdapterV2(
            SimpleNamespace(open_bundle=open_bundle),
            max_source_bytes=2 * 1024 * 1024,
            max_asset_bytes=2 * 1024 * 1024,
            max_manifest_bytes=2 * 1024 * 1024,
        ),
        profiles={profile_hash: profile},
        builders={"a" * 64: object()},
        analysis_cache=None,
    )
    scope = strict_loads(snapshot.scope_bytes)
    scope["manifest_sha256"] = manifest["manifest_sha256"]
    member = {
        "source_pages": pages,
        "document_id": doc["document_id"],
        "verification_profile_hash": profile_hash,
        "frozen_snapshot": {
            "scope_bytes": base64.b64encode(canonical(scope)).decode(),
            "profile_hash": profile_hash,
            "analysis": analysis,
        },
    }
    return provider, member, pixel_retained


@pytest.mark.asyncio
async def test_verification_rereads_pixels_and_returns_isolated_source_bound_evidence(
    verification,
):
    provider, member, (_, _, _, analysis) = verification
    evidence = analysis["verification_input"]
    returned = await provider.verification_input(member)
    assert returned == evidence
    returned["readings"][0]["fields"]["team.name"] = "changed caller copy"
    assert evidence["readings"][0]["fields"]["team.name"] == "Synthetic"
    evidence["source_binding_hash"] = "0" * 64
    with pytest.raises(BatchDenied, match="SOURCE_UNAVAILABLE"):
        await provider.verification_input(member)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "input_hash",
        "method",
        "profile",
        "frozen_profile",
        "one_reading",
        "occupied_omitted_slot",
    ],
)
async def test_verification_denies_unrelated_inputs_or_missing_printed_person(
    verification, fault
):
    provider, member, pixel = verification
    analysis = member["frozen_snapshot"]["analysis"]
    readings = analysis["verification_input"]["readings"]
    if fault == "input_hash":
        readings[0]["input_hash"] = "0" * 64
    elif fault == "method":
        readings[0]["method"] = "unrelated-view"
    elif fault == "profile":
        member["verification_profile_hash"] = "0" * 64
    elif fault == "frozen_profile":
        member["frozen_snapshot"]["profile_hash"] = "0" * 64
    elif fault == "one_reading":
        readings.pop()
    else:
        analysis["players"].pop()
        _evidence(*pixel, statuses=["PRESENT", "ABSENT", "ABSENT"])
    with pytest.raises(BatchDenied):
        await provider.verification_input(member)


@pytest.mark.asyncio
@pytest.mark.parametrize("reader_labels_template", [True, False])
async def test_existing_automatic_plan_is_not_rebuilt_after_exception_revision(
    preparation,
    reader_labels_template,
):
    p = preparation
    if not reader_labels_template:
        p.analysis.pop("template_id")
    frozen = {"expected_head": "immutable-head", "analysis": p.analysis}
    member = {
        "document_id": p.source["document_id"],
        "original_auto_ready": True,
        "status": "ready",
        "auto_ready": True,
        "source_pages": p.source["source_pages"],
        "frozen_snapshot": frozen,
    }
    p.run["members"] = [member]
    result = await p.provider.analyze(p.run)
    assert result["members"][0]["snapshot"] is frozen
    assert result["documents"][0]["template_id"] == "synthetic-letter"
    assert result["documents"][0]["category"] == "UNICA"
    assert all(kind != "build" for kind, _ in p.calls)


@pytest.mark.asyncio
async def test_qualification_preserves_initial_cohort_after_later_success(preparation):
    p = preparation
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), p.analysis)
    initial = await p.provider.analyze(p.run)
    p.run.update(
        input_snapshot=copy.deepcopy(initial),
        corpus_hash=initial["corpus_hash"],
        cohort_metrics=copy.deepcopy(initial["metrics"]),
    )
    p.run["input_snapshot"]["metrics"]["teams"] = {"numerator": 0, "denominator": 0}
    observations = [
        {
            "document_id": p.source["document_id"],
            "outcome": "matches",
            "notes": "synthetic",
        }
    ]
    candidate = await p.provider.qualify(p.run, observations)
    assert candidate["metrics"] == initial["metrics"]
    assert candidate["metrics"]["teams"] == {"numerator": 1, "denominator": 1}
    assert "candidate_hash" not in candidate


@pytest.mark.asyncio
async def test_same_profile_routes_each_manifest_to_its_exact_policy(
    preparation, monkeypatch
):
    p = preparation
    # This unit isolates catalog dispatch; faithful pixel replay is exercised
    # by the separate positive verification and complete two-PDF analyze cases.
    monkeypatch.setattr(
        "devnous.copa_telmex.staff_batch_source_service.verify_observation_inputs",
        lambda snapshot, pages, profile, analysis: analysis["verification_input"],
    )
    original = p.source["manifest"]
    sources_by_manifest = {}
    for index, pdf in enumerate(original["pdfs"]):
        slices = [{"pdf_sha256": pdf["sha256"], "pages": [1]}]
        document_id = stable_document_id(EDITION["edition_id"], source_refs(slices))
        manifest = validate_source_manifest(
            {
                "schema_version": original["schema_version"],
                "edition": EDITION,
                "pdfs": [pdf],
                "documents": [
                    {
                        "document_id": document_id,
                        "source_slices": slices,
                        "extraction": {
                            "structural_observation": {
                                "template_id": "synthetic-letter",
                                "category": "UNICA",
                                "source_refs": [
                                    {"pdf_sha256": pdf["sha256"], "pdf_page": 1}
                                ],
                                "physical_slots": [
                                    s["slot_ref"]
                                    for s in p.provider.profiles[p.run["profile_hash"]][
                                        "physical_slots"
                                    ]
                                ],
                            }
                        },
                    }
                ],
            }
        )
        with prepare_source_admission_v2(
            canonical(manifest), p.original_root, edition=EDITION, limits=LIMITS
        ) as prepared:
            _publish(prepared, p.cas, uuid4())
            plan = strict_loads(prepared.plan_bytes)
        source = {
            **p.source,
            "manifest": manifest,
            "manifest_sha256": manifest["manifest_sha256"],
            "document_id": document_id,
            "source_payload_hash": digest(manifest["documents"][0]["extraction"]),
            "session_id": str(uuid4()),
            "source_pages": [
                {k: v for k, v in page.items() if k != "document_id"}
                for page in plan["pages"]
            ],
        }
        analysis = {
            **p.analysis,
            "document_id": document_id,
            "source_binding_hash": digest(source["source_pages"]),
        }
        p.cache.retain(analysis_identity(p.run["profile_hash"], source), analysis)
        sources_by_manifest[manifest["manifest_sha256"]] = source
        p.context["capabilities"].append(
            {
                "manifest_sha256": manifest["manifest_sha256"],
                "policy_hash": ("a" if index == 0 else "b") * 64,
            }
        )
    p.context["documents"] = list(sources_by_manifest.values())
    p.run["manifest_hashes"] = list(sources_by_manifest)
    selected = []

    def builder_for(policy):
        def exact(context, source, analysis, snapshot):
            assert context["policy_hash"] == policy
            assert context["manifest_sha256"] == source["manifest_sha256"]
            snapshot.verify_unchanged()
            selected.append((context["manifest_sha256"], policy))
            return {
                "document_id": source["document_id"],
                "source_pages": source["source_pages"],
                "status": "ready",
                "auto_ready": True,
                "snapshot": {},
            }

        return SimpleNamespace(build_batch=exact)

    p.provider.builders = {key: builder_for(key) for key in ("a" * 64, "b" * 64)}
    await p.provider.analyze(p.run)
    assert selected == [
        (manifest, ("a" if index == 0 else "b") * 64)
        for index, manifest in enumerate(p.run["manifest_hashes"])
    ]


def corrected_plan(proposal):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    field = "players.1.birth_date"
    analysis["auto_ready"] = False
    analysis["issues"] = [{"code": "FIELD_DISAGREEMENT"}]
    analysis["players"][0]["birth_date"] = None
    analysis["verification_input"]["proposed_fields"][field] = None
    analysis["verification_input"]["readings"][0]["fields"][field] = "unreadable"
    analysis["readings"] = copy.deepcopy(analysis["verification_input"]["readings"])
    records = [
        {
            "principal_id": context["principal_id"],
            "source_pages": source["source_pages"],
            "edits": [{"field_id": field, "value": "2000-01-01"}],
        }
    ]
    fixed = apply_proposals(
        analysis,
        records=records,
        principal_id=context["principal_id"],
        source_pages=source["source_pages"],
    )
    return builder.build_batch(context, source, fixed, snapshot), source, fixed


def test_corrected_plan_retains_field_provenance_without_humanizing_player(proposal):
    result, _, analysis = corrected_plan(proposal)
    snapshot = result["snapshot"]
    assert snapshot["human_edits"] == analysis["human_edits"]
    assert snapshot["human_edits_hash"] == digest(analysis["human_edits"])
    assert snapshot["raw_snapshot"]["ocr_raw"]["readings"] == analysis["readings"]
    assert len(snapshot["raw_snapshot"]["ocr_raw"]["readings"]) == 2
    player = json.loads(base64.b64decode(snapshot["review_bytes"]))["players"][0]
    assert player["verified_by_human"] is False
    assert result["auto_ready"] is False


def test_manually_corrected_plan_cannot_inflate_automatic_yield(proposal):
    result, source, _ = corrected_plan(proposal)
    result["source_slices"] = [{"pdf_sha256": "a" * 64, "pages": [1, 2]}]
    metrics = calculate_metrics(
        [result],
        source_pages=source["source_pages"],
        cutoff=datetime.now(timezone.utc).isoformat(),
        profile_hash="a" * 64,
        corpus_hash="b" * 64,
    )
    assert metrics["teams"] == {"numerator": 0, "denominator": 1}
    assert metrics["pages"] == {"numerator": 0, "denominator": 2}


@pytest.mark.parametrize("changed", ["principal_id", "source_pages"])
def test_partial_correction_rejects_another_actor_or_original(proposal, changed):
    _, context, source, analysis, _ = proposal
    original = copy.deepcopy(analysis)
    record = {
        "principal_id": context["principal_id"],
        "source_pages": source["source_pages"],
        "edits": [{"field_id": "players.1.first_name", "value": "Changed"}],
    }
    record[changed] = str(uuid4()) if changed == "principal_id" else []
    with pytest.raises(BatchDenied):
        apply_proposals(
            analysis,
            records=[record],
            principal_id=context["principal_id"],
            source_pages=source["source_pages"],
        )
    assert analysis == original


@pytest.mark.parametrize("field", ["team.name", "players.1.first_name", "staff.1.role"])
def test_real_plan_rejects_semantics_that_differ_from_observation_receipt(
    proposal, field
):
    builder, context, source, analysis, snapshot = proposal
    analysis = copy.deepcopy(analysis)
    analysis["verification_input"]["proposed_fields"][field] = "different"
    with pytest.raises(ValueError, match="BATCH_FIELD_PLAN_MISMATCH"):
        builder.build_batch(context, source, analysis, snapshot)


@pytest.mark.parametrize("different", ["policy", "manifest"])
def test_real_plan_never_substitutes_another_exact_manifest_policy(proposal, different):
    builder, context, source, analysis, snapshot = proposal
    context = {
        **context,
        "policy_hash" if different == "policy" else "manifest_sha256": "0" * 64,
    }
    with pytest.raises(ValueError):
        builder.build_batch(context, source, analysis, snapshot)


@pytest.mark.asyncio
async def test_private_verification_replays_pending_geometry_against_original_pixels(
    verification,
):
    from devnous.copa_telmex.staff_batch_authorization import (
        BatchReviewScope,
        BatchSourcePage,
        canonical_batch_scope_bytes,
    )
    from devnous.copa_telmex.staff_batch_corrections import editable_fields
    from devnous.copa_telmex.staff_batch_observation_inputs import (
        verify_manual_observation_inputs,
    )

    provider, member, retained = verification
    snapshot, pages, profile, original = retained
    original = copy.deepcopy(original)
    actor = uuid4()
    oldscope = strict_loads(base64.b64decode(member["frozen_snapshot"]["scope_bytes"]))
    scope = BatchReviewScope(
        tenant_id=oldscope["tenant_id"],
        tournament_slug=oldscope["tournament_slug"],
        edition_id=UUID(oldscope["edition_id"]),
        session_id=uuid4(),
        draft_id=uuid4(),
        draft_version=1,
        draft_content_hash="a" * 64,
        team_id=uuid4(),
        manifest_sha256=oldscope["manifest_sha256"],
        document_id=oldscope["document_id"],
        source_pages=tuple(
            BatchSourcePage(**{**p, "asset_id": UUID(p["asset_id"])}) for p in pages
        ),
        actor_id=actor,
        actor_role="reviewer",
        authority_binding="hmac-sha256:" + "b" * 64,
        staff_and_changes_binding="hmac-sha256:" + "c" * 64,
        policy_hash="d" * 64,
        operation_id=uuid4(),
        field_evidence_binding="hmac-sha256:" + "e" * 64,
        run_id=uuid4(),
        member_id=uuid4(),
    )
    field = "players.1.birth_date"
    original["verification_input"]["readings"][0]["fields"][field] = "bad original date"
    rect = next(
        f["rectangle"]
        for f in editable_fields(original, profile=profile, source_pages=pages)
        if f["field_id"] == field
    )
    records = [
        {
            "principal_id": str(actor),
            "source_pages": pages,
            "edits": [
                {
                    "field_id": field,
                    "value": {
                        "kind": "source_field",
                        "value": "2000-01-01",
                        "rectangle": rect,
                    },
                }
            ],
        }
    ]
    corrected, expected = verify_manual_observation_inputs(
        snapshot, pages, profile, original, records=records, principal_id=str(actor)
    )
    member["human_edits"] = records
    member["frozen_snapshot"].update(
        scope_bytes=base64.b64encode(canonical_batch_scope_bytes(scope)).decode(),
        analysis=corrected,
        manual_original_analysis=original,
    )
    assert await provider.verification_input(member) == expected
    member["frozen_snapshot"]["analysis"]["players"][0][
        "first_name"
    ] = "unreviewed overwrite"
    with pytest.raises(BatchDenied):
        await provider.verification_input(member)


@pytest.mark.asyncio
async def test_pending_geometry_reanalysis_uses_original_receipt_and_zero_auto_yield(
    preparation,
):
    p = preparation
    field = "players.1.birth_date"
    original = copy.deepcopy(p.analysis)
    original["auto_ready"] = False
    original["issues"] = [{"code": "FIELD_DISAGREEMENT"}]
    original["players"][0]["birth_date"] = None
    original["verification_input"]["proposed_fields"][field] = None
    original["verification_input"]["readings"][0]["fields"][field] = "bad raw date"
    from devnous.copa_telmex.staff_batch_corrections import editable_fields

    rect = next(
        f["rectangle"]
        for f in editable_fields(
            original,
            profile=p.provider.profiles[p.run["profile_hash"]],
            source_pages=p.source["source_pages"],
        )
        if f["field_id"] == field
    )
    records = [
        {
            "principal_id": p.run["principal_id"],
            "source_pages": p.source["source_pages"],
            "edits": [
                {
                    "field_id": field,
                    "value": {
                        "kind": "source_field",
                        "value": "2000-01-01",
                        "rectangle": rect,
                    },
                }
            ],
        }
    ]
    p.run["members"] = [
        {
            "document_id": p.source["document_id"],
            "original_auto_ready": False,
            "human_edits": records,
        }
    ]
    p.cache.retain(analysis_identity(p.run["profile_hash"], p.source), original)
    result = await p.provider.analyze(p.run)
    member = result["members"][0]
    assert member["status"] == "ready"
    assert member["snapshot"]["manual_original_analysis"] == original | {
        "source_physical_slots": original["source_physical_slots"]
    }
    assert member["snapshot"]["analysis"]["human_edits"] == records
    assert member["auto_ready"] is False
    assert result["metrics"]["teams"]["numerator"] == 0
    assert (
        member["snapshot"]["analysis"]["verification_input"]["readings"][0]["fields"][
            field
        ]
        == "bad raw date"
    )
    assert p.cache.read(analysis_identity(p.run["profile_hash"], p.source)) == original


def _prior_batch_source(p):
    source = p.context["documents"][0]
    source["operation_id"] = str(uuid4())
    source["committed_team_id"] = str(uuid4())
    source["legacy"] = {"status": "none"}
    source["prior_batch_capture"] = {
        "status": "committed",
        "tenant_id": p.context["tenant_id"],
        "edition_id": str(p.context["edition_id"]),
        "document_id": source["document_id"],
        "operation_id": source["operation_id"],
        "source_payload_hash": source["source_payload_hash"],
        "source_pages": source["source_pages"],
        "team_id": source["committed_team_id"],
        "attempt_id": str(uuid4()),
        "result_id": str(uuid4()),
        "proof_hash": "a" * 64,
    }
    return source


@pytest.mark.asyncio
async def test_prior_batch_capture_without_legacy_bridge_overrides_frozen_ready(
    preparation,
):
    p = preparation
    source = _prior_batch_source(p)
    p.run["members"] = [
        {
            "document_id": source["document_id"],
            "original_auto_ready": True,
            "status": "ready",
            "snapshot": {"obsolete": True},
        }
    ]
    result = await p.provider.analyze(p.run)
    member = result["members"][0]
    assert member["status"] == "already_captured"
    assert member["exclusion_proof"]["evidence_sha256"] == "a" * 64
    assert member["attempt_id"] is None
    assert not any(call[0] == "build" for call in p.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "incomplete"),
        ("result_id", None),
        ("team_id", str(uuid4())),
        ("source_pages", []),
        ("operation_id", str(uuid4())),
        ("proof_hash", "bad"),
    ],
)
async def test_incomplete_or_conflicting_prior_capture_is_blocked(
    preparation, field, value
):
    p = preparation
    source = _prior_batch_source(p)
    source["prior_batch_capture"][field] = value
    member = (await p.provider.prepare_run(p.context, p.run["manifest_hashes"]))[
        "members"
    ][0]
    assert member["status"] == "exception"
    assert member["issues"] == [{"code": "BATCH_CAPTURE_IDENTITY_UNRESOLVED"}]
    assert "exclusion_proof" not in member


@pytest.mark.asyncio
async def test_conflicting_legacy_capture_does_not_override_prior_batch_proof(
    preparation,
):
    p = preparation
    source = _prior_batch_source(p)
    source["legacy"] = {
        "status": "committed",
        "result_id": str(uuid4()),
        "committed_team_id": str(uuid4()),
        "proof_hash": "b" * 64,
    }
    member = (await p.provider.prepare_run(p.context, p.run["manifest_hashes"]))[
        "members"
    ][0]
    assert member["status"] == "exception"
    assert "exclusion_proof" not in member
