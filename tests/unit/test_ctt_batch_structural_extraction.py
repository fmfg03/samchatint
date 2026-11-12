"""Source structure, header references and bounded non-authoritative API reads."""

import copy
import hashlib
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    digest,
    strict_loads,
)
from devnous.copa_telmex.staff_batch_source_service import PrivateBatchAnalysisCache
from devnous.copa_telmex.staff_batch_store import BatchDenied
from devnous.tournaments.core.ctt_batch_structural_extraction import (
    PIPELINE,
    PageBatch,
    StructuralPageAnalyzer,
    validate_structural_profile,
)
from devnous.tournaments.core.ctt_openai_crop_client import OpenAICttCropClient


def fraction(x, y, w, h):
    return dict(x=x / 100, y=y / 100, width=w / 100, height=h / 100)


def rect(x, y, w, h):
    return dict(x=x, y=y, width=w, height=h)


@pytest.fixture
def case(tmp_path):
    images, pages, raw = {}, [], {}
    pdf = "a" * 64
    for number in (1, 2):
        im = Image.new("RGB", (100, 100), "white")
        d = ImageDraw.Draw(im)
        for b in [
            (10, 5, 40, 10),
            (50, 15, 80, 20),
            (10, 15, 40, 20),
            (5, 40, 10, 45),
            (5, 60, 10, 65),
        ]:
            d.rectangle(b, fill="black")
        stream = BytesIO()
        im.save(stream, format="PNG")
        data = stream.getvalue()
        images[(pdf, number)] = data
        page = dict(
            pdf_sha256=pdf,
            pdf_page=number,
            render_sha256=hashlib.sha256(data).hexdigest(),
            width=100,
            height=100,
        )
        pages.append(page)
        pid = f"{pdf}:{number}"

        def anchor(value, box):
            return dict(value=value, page_id=pid, rectangle=box)

        raw[pid] = dict(
            page_id=pid,
            render_sha256=page["render_sha256"],
            side="front" if number == 1 else "back",
            template_id="synthetic-four",
            header_page_id=f"{pdf}:1",
            previous_page_id=None if number == 1 else f"{pdf}:1",
            title=anchor("FORM", rect(10, 5, 30, 5)) if number == 1 else None,
            category=(
                anchor("EXPLICIT CATEGORY", rect(50, 15, 30, 5))
                if number == 1
                else None
            ),
            team=anchor("EXPLICIT TEAM", rect(10, 15, 30, 5)) if number == 1 else None,
            slot_observations=[
                dict(printed_number=number * 2 - 1, rectangle=rect(5, 40, 5, 5)),
                dict(printed_number=number * 2, rectangle=rect(5, 60, 5, 5)),
            ],
        )
    headers = dict(
        title=fraction(9, 4, 32, 7),
        category=fraction(49, 14, 32, 7),
        team=fraction(9, 14, 32, 7),
    )
    probes = [
        dict(rectangle=fraction(10, 5, 30, 5), min_ink=0.9, max_ink=1),
        dict(rectangle=fraction(5, 40, 5, 5), min_ink=0.9, max_ink=1),
    ]
    quote = dict(
        revision="synthetic-quote",
        model_revision="synthetic-2026-01-01",
        currency="USD",
        unit="micro_usd",
        input_per_million_micro_usd=1000000,
        cached_input_per_million_micro_usd=100000,
        output_per_million_micro_usd=2000000,
        model_input_token_limit=10,
    )
    provider = dict(
        name="openai",
        model_revision=quote["model_revision"],
        pipeline_version=PIPELINE,
        timeout_seconds=30,
        pricing_revision=quote["revision"],
        pricing=quote,
        maximum_call_units=1000,
        batch_size=2,
        max_output_tokens=256,
        max_request_bytes=1000000,
        max_analysis_bytes=1000000,
    )
    template = dict(
        id="synthetic-four",
        title_text="FORM",
        sides={
            side: dict(
                slot_regions={
                    str(n): fraction(4, 39 + (i * 20), 7, 7)
                    for i, n in enumerate(numbers)
                },
                probes=copy.deepcopy(probes),
                header_regions=headers if side == "front" else {},
            )
            for side, numbers in [("front", [1, 2]), ("back", [3, 4])]
        },
    )
    profile = dict(
        schema="ctt.batch.structural-profile.v1",
        provider=provider,
        templates=[template],
        segmentation=dict(
            template_ids=["synthetic-four"],
            allowed_page_patterns=dict(front=[1, 2], back=[3, 4]),
            source_groups={},
        ),
    )
    inventory = dict(
        schema="ctt.batch.source-inventory.v1",
        edition=dict(
            tenant_id="synthetic", edition_id=str(uuid4()), roster_slug="synthetic"
        ),
        pdfs=[
            dict(
                filename="MUST_NOT_INFER_FEMENIL.pdf", sha256=pdf, bytes=1, page_count=2
            )
        ],
        pages=pages,
    )
    context = dict(
        preparation_id=str(uuid4()),
        budget_id=str(uuid4()),
        inventory=inventory,
        profile=profile,
        quote=quote,
        inventory_sha256=digest(inventory),
        profile_hash=digest(profile),
        quote_sha256=digest(quote),
    )
    checks = []
    snapshot = SimpleNamespace(
        pages=tuple(pages),
        page_bytes=lambda sha, n: images[(sha, n)],
        native_text=lambda sha, n: "EQUIPO CÉDULA DIRECTOR",
        verify_unchanged=lambda: checks.append(True),
    )
    sdk = SimpleNamespace(max_retries=0, calls=[], fault=None)
    sdk.with_options = lambda **kw: sdk

    async def parse(**kwargs):
        sdk.calls.append(kwargs)
        items = [
            strict_loads(c["text"].encode())
            for c in kwargs["input"][0]["content"]
            if c["type"] == "input_text"
        ]
        selected = [copy.deepcopy(raw[p["page_id"]]) for p in items if p["requested"]]
        if sdk.fault == "network":
            raise RuntimeError("DO_NOT_EXPOSE_SECRET_OR_SOURCE")
        if sdk.fault == "wrong_hash":
            selected[0]["render_sha256"] = "f" * 64
        response = SimpleNamespace(
            model=provider["model_revision"],
            service_tier="default",
            usage=dict(
                input_tokens=3,
                output_tokens=1,
                total_tokens=4,
                input_tokens_details=dict(cached_tokens=0),
            ),
            output_parsed=PageBatch.model_validate(dict(results=selected)),
        )
        if sdk.fault == "model":
            response.model = "alias"
        if sdk.fault == "tier":
            response.service_tier = "priority"
        if sdk.fault == "usage":
            response.usage = None
        if sdk.fault == "result_duplicate":
            response.output_parsed.results.append(response.output_parsed.results[0])
        if sdk.fault == "parsed":
            response.output_parsed = None
        return response

    sdk.responses = SimpleNamespace(parse=parse)
    store = SimpleNamespace(
        reservations=[], settled=[], finished=[], denied=False, changes={}
    )

    async def context_read(pid):
        if store.denied:
            raise BatchDenied("REVIEW_BLOCKED")
        return {**context, **store.changes}

    async def reserve(pid, rid, units):
        if store.denied:
            raise BatchDenied("REVIEW_BLOCKED")
        store.reservations.append((rid, units))
        return dict(reservation_id=str(rid), maximum_units=units)

    async def settle(rid, units, sha):
        store.settled.append((rid, units, sha))

    async def finish(pid, obs, report):
        store.finished.append((strict_loads(obs), strict_loads(report)))

    async def budget(pid):
        return dict(
            budget_id=context["budget_id"],
            maximum_units=25000000,
            charged_units=sum(u for _, u, _ in store.settled),
            held_units=sum(
                u
                for rid, u in store.reservations
                if rid not in {r for r, _, _ in store.settled}
            ),
        )

    store.source_preparation_context = context_read
    store.source_preparation_reserve = reserve
    store.budget_reconcile = settle
    store.source_preparation_finish = finish
    store.source_preparation_budget_report = budget
    cache_root = tmp_path / "cache"
    cache_root.mkdir(mode=0o700)
    analyzer = StructuralPageAnalyzer(
        provider=OpenAICttCropClient(
            sdk, model_revision=provider["model_revision"], timeout_seconds=30
        ),
        cache=PrivateBatchAnalysisCache(cache_root),
    )
    return SimpleNamespace(
        analyzer=analyzer,
        snapshot=snapshot,
        context=context,
        profile=profile,
        inventory=inventory,
        sdk=sdk,
        store=store,
        raw=raw,
        images=images,
        checks=checks,
    )


def repin(case):
    case.context.update(
        profile_hash=digest(case.profile),
        inventory_sha256=digest(case.inventory),
        quote_sha256=digest(case.profile["provider"]["pricing"]),
    )


@pytest.mark.asyncio
async def test_headerless_back_uses_exact_front_evidence_and_cache_has_no_second_cost(
    case,
):
    obs, report = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert (
        report["proposed_documents"] == 1
        and report["teams_created"] == report["source_admissions"] == 0
    )
    front, back = obs["pages"]
    assert back["raw_observation"]["category"] is None
    assert back["header_evidence"] == front["header_evidence"]
    assert back["category"] == "EXPLICIT CATEGORY"
    assert [s["printed_number"] for s in back["slot_evidence"]] == [3, 4]
    assert (
        len(case.store.reservations)
        == len(case.store.settled)
        == len(case.sdk.calls)
        == 1
    )
    request = case.sdk.calls[0]
    assert request["service_tier"] == "default" and request["store"] is False
    assert "MUST_NOT_INFER" not in str(request)
    assert len(report["segmentation"]["page_classifications"]) == 2
    assert report["api_budget"]["charged_units"] == 5
    again = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert (
        again == (obs, report)
        and len(case.sdk.calls) == 1
        and len(case.store.reservations) == 1
    )


@pytest.mark.parametrize(
    "fault",
    [
        "quote",
        "model",
        "pipeline",
        "timeout",
        "batch",
        "bound",
        "probe",
        "region",
        "pattern",
    ],
)
def test_invalid_configuration_before_any_sdk_or_credential(case, fault):
    p = case.profile
    c = p["provider"]
    if fault == "quote":
        c["pricing"]["unit"] = "tokens"
    elif fault == "model":
        c["model_revision"] = "latest"
    elif fault == "pipeline":
        c["pipeline_version"] = "old"
    elif fault == "timeout":
        c["timeout_seconds"] = float("inf")
    elif fault == "batch":
        c["batch_size"] = 13
    elif fault == "bound":
        c["maximum_call_units"] = 1
    elif fault == "probe":
        p["templates"][0]["sides"]["front"]["probes"] = []
    elif fault == "region":
        p["templates"][0]["sides"]["front"]["slot_regions"]["1"]["x"] = 2
    else:
        p["segmentation"]["allowed_page_patterns"]["front"] = [1]
    with pytest.raises(BatchContractError):
        validate_structural_profile(p)
    assert not case.sdk.calls and not case.store.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["network", "model", "tier", "usage", "wrong_hash", "result_duplicate", "parsed"],
)
async def test_uncertain_call_stops_with_reservation_retained_and_no_authority(
    case, fault
):
    case.sdk.fault = fault
    _, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["stop_reason"] and r["teams_created"] == 0 and r["proposed_documents"] == 0
    assert len(case.store.reservations) == len(case.sdk.calls) == 1
    assert "DO_NOT_EXPOSE" not in str(r)
    if fault in {"network", "model", "tier", "usage", "parsed"}:
        assert not case.store.settled
    assert all(
        p["classification"] == "exception"
        for p in r["segmentation"]["page_classifications"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "slot_missing",
        "slot_duplicate",
        "slot_outside",
        "blank_label",
        "bad_header",
        "wrong_template",
        "unproven_other",
        "unlinked_back",
    ],
)
async def test_ambiguous_structure_remains_page_exception_never_padded(case, fault):
    first, second = list(case.raw.values())
    if fault == "slot_missing":
        first["slot_observations"].pop()
    elif fault == "slot_duplicate":
        first["slot_observations"][1] = copy.deepcopy(first["slot_observations"][0])
    elif fault == "slot_outside":
        first["slot_observations"][0]["rectangle"]["x"] = 90
    elif fault == "blank_label":
        first["slot_observations"][0]["rectangle"] = rect(4, 39, 1, 1)
    elif fault == "bad_header":
        first["category"]["rectangle"]["x"] = 0
    elif fault == "wrong_template":
        first["template_id"] = "unknown-layout"
    elif fault == "unproven_other":
        first["side"] = "other"
        first["slot_observations"] = []
    else:
        second["header_page_id"] = None
    obs, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["proposed_documents"] == 0 and r["teams_created"] == 0
    assert len(obs["pages"]) == len(r["segmentation"]["page_classifications"]) == 2
    assert any(p["side"] == "unknown" for p in obs["pages"])


@pytest.mark.asyncio
async def test_reordered_original_pages_do_not_join(case):
    case.inventory["pages"].reverse()
    case.snapshot.pages = tuple(case.inventory["pages"])
    repin(case)
    _, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["proposed_documents"] == 0


@pytest.mark.asyncio
async def test_context_change_denies_before_call(case):
    case.store.changes["profile_hash"] = "f" * 64
    _, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["stop_reason"] == "STRUCTURAL_ADMISSION_CHANGED"
    assert not case.sdk.calls and not case.store.reservations


@pytest.mark.asyncio
async def test_revoked_or_expired_context_denies_even_cached_read(case):
    await case.analyzer.analyze(case.snapshot, context=case.context, store=case.store)
    case.store.denied = True
    _, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["stop_reason"] == "REVIEW_BLOCKED" and len(case.sdk.calls) == 1


@pytest.mark.asyncio
async def test_inventory_binding_changed_has_zero_calls(case):
    case.context["inventory_sha256"] = "f" * 64
    with pytest.raises(BatchContractError, match="STRUCTURAL_ADMISSION_MISMATCH"):
        await case.analyzer.analyze(
            case.snapshot, context=case.context, store=case.store
        )
    assert not case.sdk.calls


def add_second_document(case):
    pdf = case.inventory["pdfs"][0]["sha256"]
    for number, original in [(3, 1), (4, 2)]:
        old = f"{pdf}:{original}"
        pid = f"{pdf}:{number}"
        page = {**case.inventory["pages"][original - 1], "pdf_page": number}
        case.inventory["pages"].append(page)
        case.images[(pdf, number)] = case.images[(pdf, original)]
        raw = copy.deepcopy(case.raw[old])
        raw["page_id"] = pid
        raw["header_page_id"] = f"{pdf}:3"
        raw["previous_page_id"] = None if number == 3 else f"{pdf}:3"
        for field in ("title", "category", "team"):
            if raw[field]:
                raw[field]["page_id"] = pid
        case.raw[pid] = raw
    case.inventory["pdfs"][0]["page_count"] = 4
    case.snapshot.pages = tuple(case.inventory["pages"])
    repin(case)


@pytest.mark.asyncio
async def test_context_overlap_uses_original_inputs_without_prior_model_answer(case):
    add_second_document(case)
    _, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["proposed_documents"] == 2 and len(case.sdk.calls) == 2
    second = case.sdk.calls[1]
    assert (
        len([c for c in second["input"][0]["content"] if c["type"] == "input_image"])
        == 4
    )
    assert all(
        c.get("type") in {"input_image", "input_text"}
        for c in second["input"][0]["content"]
    )
    assert "EXPLICIT TEAM" not in str(second)
    assert len(case.store.reservations) == 2 and r["api_budget"]["charged_units"] == 10


@pytest.mark.asyncio
async def test_unknown_cost_stops_later_chunks_and_keeps_entire_census(case):
    add_second_document(case)
    case.sdk.fault = "network"
    obs, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert len(case.sdk.calls) == 1 and len(obs["pages"]) == 4
    assert obs["pages"][-1]["reason"] == "STRUCTURAL_NOT_PROCESSED"
    assert r["api_budget"]["held_units"] == 1000


@pytest.mark.asyncio
async def test_known_wrong_title_and_contradictory_back_header_are_exceptions(case):
    first, second = list(case.raw.values())
    first["title"]["value"] = "DIFFERENT FORM"
    second["category"] = copy.deepcopy(first["category"])
    obs, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert [p["reason"] for p in obs["pages"]] == [
        "STRUCTURAL_TEMPLATE_TITLE_MISMATCH",
        "STRUCTURAL_CONTINUATION_HAS_HEADER",
    ]
    assert r["proposed_documents"] == 0


@pytest.mark.asyncio
async def test_bad_continuity_keeps_raw_interpretation(case):
    list(case.raw.values())[1]["previous_page_id"] = "b" * 64 + ":1"
    obs, _ = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert obs["pages"][1]["side"] == "unknown"
    assert obs["pages"][1]["raw_observation"]["previous_page_id"] == "b" * 64 + ":1"


@pytest.mark.asyncio
async def test_completed_result_recovery_does_not_read_provider_or_spend_again(case):
    obs, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    case.context.update(
        status="completed",
        observations=obs,
        report=r,
        observations_sha256=digest(obs),
        report_sha256=digest(r),
    )
    case.sdk.fault = "network"
    recovered = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert (
        recovered == (obs, r)
        and len(case.sdk.calls) == 1
        and len(case.store.finished) == 1
    )
    case.context["report_sha256"] = "0" * 64
    with pytest.raises(BatchContractError, match="STRUCTURAL_RETAINED_RESULT_INVALID"):
        await case.analyzer.analyze(
            case.snapshot, context=case.context, store=case.store
        )
    assert len(case.sdk.calls) == 1


@pytest.mark.asyncio
async def test_cache_response_source_tampering_is_not_accepted(case):
    await case.analyzer.analyze(case.snapshot, context=case.context, store=case.store)
    p = next(
        p
        for p in case.analyzer.cache.root.iterdir()
        if strict_loads(p.read_bytes())["identity"]["stage"] == PIPELINE
    )
    body = strict_loads(p.read_bytes())
    body["analysis"]["response"]["results"][0]["render_sha256"] = "b" * 64
    from devnous.copa_telmex.staff_batch_contracts import canonical

    p.write_bytes(canonical(body))
    obs, r = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert r["stop_reason"] == "STRUCTURAL_RESPONSE_SOURCE_MISMATCH"
    assert len(case.sdk.calls) == 1 and r["proposed_documents"] == 0


@pytest.mark.asyncio
async def test_normalized_size_limit_keeps_census_once_not_partial_duplicate(case):
    case.profile["provider"]["max_analysis_bytes"] = 1024
    repin(case)
    obs, report = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert report["stop_reason"] == "STRUCTURAL_ANALYSIS_TOO_LARGE"
    assert (
        len(obs["pages"])
        == len({(p["pdf_sha256"], p["pdf_page"]) for p in obs["pages"]})
        == 2
    )
    assert report["proposed_documents"] == 0


@pytest.mark.asyncio
async def test_other_worker_completion_recovers_without_second_cost(
    case,
):
    obs, report = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    case.store.changes.update(
        status="completed",
        observations=obs,
        report=report,
        observations_sha256=digest(obs),
        report_sha256=digest(report),
    )
    case.sdk.fault = "network"
    recovered = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    assert (
        recovered == (obs, report)
        and len(case.sdk.calls) == 1
        and len(case.store.finished) == 1
    )


def test_ambiguous_cross_pdf_groups_deny_before_provider(case):
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    case.profile["segmentation"]["source_groups"] = {"one": [a, b], "two": [a, c]}
    with pytest.raises(BatchContractError, match="STRUCTURAL_SOURCE_GROUP_AMBIGUOUS"):
        validate_structural_profile(case.profile)
    assert not case.sdk.calls and not case.store.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_hash", "result_duplicate", "tier", "network"])
async def test_rejected_provider_response_is_private_audit_never_reusable(case, fault):
    case.sdk.fault = fault
    _, report = await case.analyzer.analyze(
        case.snapshot, context=case.context, store=case.store
    )
    records = [strict_loads(p.read_bytes()) for p in case.analyzer.cache.root.iterdir()]
    assert (
        len(records) == 1
        and records[0]["identity"]["stage"] == PIPELINE + ".attempt-response"
    )
    audit = records[0]["analysis"]
    assert audit["reservation_id"] == str(case.store.reservations[0][0])
    assert (
        audit["request_fingerprint"]
        and audit["request"]["inventory"] == case.context["inventory_sha256"]
    )
    assert report["provider_receipts"] == [
        {
            "cache_identity_sha256": digest(records[0]["identity"]),
            "record_sha256": digest(audit),
        }
    ]
    assert report["proposed_documents"] == 0 and len(case.sdk.calls) == 1
    assert "DO_NOT_EXPOSE" not in str(audit)
    if fault == "network":
        assert audit["response_unavailable"] is True and not case.store.settled
    else:
        assert audit["usage"]["input_tokens"] == 3
        assert audit["parsed_response"]["results"]
        if fault == "tier":
            assert audit["service_tier"] == "priority" and not case.store.settled
        else:
            assert case.store.settled[0][2] == digest(audit)


def test_usage_audit_keeps_nested_reported_sdk_counters_without_client_state():
    from devnous.tournaments.core.ctt_batch_structural_extraction import _usage_record

    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=7,
        total_tokens=17,
        input_tokens_details=SimpleNamespace(cached_tokens=3),
        output_tokens_details=SimpleNamespace(reasoning_tokens=2),
        client_state="NEVER_RETAIN",
    )
    record = _usage_record(usage)
    assert record == dict(
        input_tokens=10,
        output_tokens=7,
        total_tokens=17,
        input_tokens_details=dict(cached_tokens=3),
        output_tokens_details=dict(reasoning_tokens=2),
    )
    assert "NEVER_RETAIN" not in str(record)
