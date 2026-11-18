"""Independent original -> admitted observation -> proposed manifest acceptance.

Only the SDK response is simulated. PDF rendering, sealed snapshots, operator
and verifier SQL roles, monetary ledger and candidate CLI are actual code.
No production connection, real API credential or capture is used.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import fitz
import pytest
from test_ctt_batch_acceptance_postgres import install
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_staff_security_postgres import PORT

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical,
    digest,
    strict_loads,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_inventory import (
    RENDER_VERSION,
    InventorySourceAdapter,
    prepare_inventory,
)
from devnous.copa_telmex.staff_batch_source_service import PrivateBatchAnalysisCache
from devnous.copa_telmex.staff_batch_store import BatchDenied, PostgresBatchReviewStore
from devnous.tournaments.core.ctt_batch_structural_extraction import (
    PIPELINE,
    PageBatch,
    StructuralPageAnalyzer,
)
from devnous.tournaments.core.ctt_openai_crop_client import OpenAICttCropClient

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261112_ctt_batch_source_preparation.sql"
BACKEND = "independent-source-observation"


def _fraction(x, y, width, height):
    return dict(x=x / 600, y=y / 800, width=width / 600, height=height / 800)


def _word_rectangle(word):
    left, top = math.floor(word[0]), math.floor(word[1])
    return dict(
        x=left,
        y=top,
        width=math.ceil(word[2]) - left,
        height=math.ceil(word[3]) - top,
    )


def _source_pdf(path, side):
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    if side == "unknown":
        page.insert_text((60, 70), "UNRESOLVED ATTACHMENT", fontsize=12)
    else:
        if side == "front":
            page.insert_text((60, 80), "FORM", fontsize=12)
            page.insert_text((340, 140), "LIBRE", fontsize=12)
            page.insert_text((60, 140), "SOURCE", fontsize=12)
        for number, y in zip((1, 2) if side == "front" else (3, 4), (280, 440)):
            page.draw_rect(fitz.Rect(25, y - 30, 560, y + 85), color=(0, 0, 0))
            page.insert_text((36, y), str(number), fontsize=16)
    document.save(path)
    document.close()
    path.chmod(0o600)


def _real_source(tmp_path):
    source = tmp_path / "input"
    source.mkdir(mode=0o700)
    drawn_sides = {
        "00-attachment.pdf": "unknown",
        "01-consolidated-FEMENIL.pdf": "front",
        "02-consolidated-VARONIL.pdf": "back",
    }
    for filename, side in drawn_sides.items():
        _source_pdf(source / filename, side)
    edition = dict(
        tenant_id="synthetic",
        edition_id=str(uuid4()),
        tournament_id=str(uuid4()),
        edition_year=2026,
        roster_slug="ctt-synthetic",
        phase="nacional",
    )
    private = tmp_path / "sealed-inventory"
    inventory = prepare_inventory(
        pdf_root=source,
        output_root=private,
        edition={k: edition[k] for k in ("tenant_id", "edition_id", "roster_slug")},
        render_profile=dict(
            version=RENDER_VERSION,
            dpi=72,
            color="RGB",
            max_page_pixels=1_000_000,
            max_render_bytes=1_000_000,
        ),
        max_total_bytes=1_000_000,
    )
    pdfs = {p["filename"]: p for p in inventory["pdfs"]}
    front, back = (
        pdfs[name]["sha256"]
        for name in ("01-consolidated-FEMENIL.pdf", "02-consolidated-VARONIL.pdf")
    )
    probes = [
        dict(rectangle=_fraction(24, 249, 537, 3), min_ink=0.05, max_ink=1),
        dict(rectangle=_fraction(24, 409, 537, 3), min_ink=0.05, max_ink=1),
    ]
    template = dict(
        id="synthetic-four-source",
        title_text="FORM",
        sides={
            side: dict(
                slot_regions={
                    str(number): _fraction(30, y - 24, 30, 30)
                    for number, y in zip(numbers, (280, 440))
                },
                probes=copy.deepcopy(probes),
                header_regions=(
                    dict(
                        title=_fraction(50, 60, 100, 30),
                        category=_fraction(330, 120, 100, 30),
                        team=_fraction(50, 120, 130, 30),
                    )
                    if side == "front"
                    else {}
                ),
            )
            for side, numbers in (("front", (1, 2)), ("back", (3, 4)))
        },
    )
    quote = dict(
        revision="synthetic-price-for-acceptance",
        model_revision="synthetic-2026-01-01",
        currency="USD",
        unit="micro_usd",
        input_per_million_micro_usd=1_000_000,
        cached_input_per_million_micro_usd=100_000,
        output_per_million_micro_usd=2_000_000,
        model_input_token_limit=10,
    )
    profile = dict(
        schema="ctt.batch.structural-profile.v1",
        provider=dict(
            name="openai",
            model_revision=quote["model_revision"],
            pipeline_version=PIPELINE,
            timeout_seconds=30,
            pricing_revision=quote["revision"],
            pricing=quote,
            maximum_call_units=1000,
            batch_size=1,
            max_output_tokens=256,
            max_request_bytes=1_000_000,
            max_analysis_bytes=1_000_000,
        ),
        templates=[template],
        segmentation=dict(
            template_ids=[template["id"]],
            allowed_page_patterns=dict(front=[1, 2], back=[3, 4]),
            source_groups={"explicit-original-order": [front, back]},
        ),
    )
    raw = {}
    for meta in inventory["pages"]:
        filename = next(
            p["filename"]
            for p in inventory["pdfs"]
            if p["sha256"] == meta["pdf_sha256"]
        )
        # The oracle uses the drawing plan, never a filename-derived inference.
        side = drawn_sides[filename]
        pid = f"{meta['pdf_sha256']}:1"
        with fitz.open(source / filename) as document:
            words = {word[4]: word for word in document[0].get_text("words")}

        def anchor(value):
            return dict(
                value=value, page_id=pid, rectangle=_word_rectangle(words[value])
            )

        raw[pid] = dict(
            page_id=pid,
            render_sha256=meta["render_sha256"],
            side=side,
            template_id=template["id"] if side != "unknown" else None,
            header_page_id=f"{front}:1" if side != "unknown" else None,
            previous_page_id=f"{front}:1" if side == "back" else None,
            title=anchor("FORM") if side == "front" else None,
            category=anchor("LIBRE") if side == "front" else None,
            team=anchor("SOURCE") if side == "front" else None,
            slot_observations=[
                dict(printed_number=n, rectangle=_word_rectangle(words[str(n)]))
                for n in (
                    (1, 2) if side == "front" else (3, 4) if side == "back" else ()
                )
            ],
        )
    return SimpleNamespace(
        input=source,
        private=private,
        edition=edition,
        inventory=inventory,
        profile=profile,
        quote=quote,
        raw=raw,
        original_hashes={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()
        },
    )


class SourceSDK:
    """The only test double: actual image request bytes must match metadata."""

    max_retries = 0

    def __init__(self, source, *, fail=False):
        self.source, self.fail, self.calls = source, fail, []
        self.responses = SimpleNamespace(parse=self.parse)

    def with_options(self, **kwargs):
        assert kwargs == {"max_retries": 0}
        return self

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["service_tier"] == "default" and kwargs["store"] is False
        content = kwargs["input"][0]["content"]
        metadata = []
        for index in range(0, len(content), 2):
            item = strict_loads(content[index]["text"].encode())
            png = base64.b64decode(content[index + 1]["image_url"].split(",", 1)[1])
            assert hashlib.sha256(png).hexdigest() == item["render_sha256"]
            metadata.append(item)
        if self.fail:
            raise RuntimeError("PRIVATE_TEST_RESPONSE_MUST_NOT_ESCAPE")
        results = [
            copy.deepcopy(self.source.raw[p["page_id"]])
            for p in metadata
            if p["requested"]
        ]
        return SimpleNamespace(
            id="synthetic-response-" + str(len(self.calls)),
            model=self.source.quote["model_revision"],
            service_tier="default",
            usage=dict(
                input_tokens=3,
                output_tokens=1,
                total_tokens=4,
                input_tokens_details=dict(cached_tokens=0),
            ),
            output_parsed=PageBatch.model_validate(dict(results=results)),
        )


async def _database(database, resources, source):
    admin = await asyncpg.connect(
        host=database[0], port=int(PORT), database=database[1], user="postgres"
    )
    resources.append(admin)
    await install(admin)
    await admin.execute(MIGRATION.read_text())
    role = "ctt_acceptance_preparation_operator"
    if not await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    ):
        await admin.execute("CREATE ROLE " + role + " LOGIN NOINHERIT")
    for name, kind in (
        (role, "operator"),
        ("ctt_full_source", "verifier"),
        ("ctt_full_channel", "workflow"),
    ):
        await admin.execute("GRANT USAGE ON SCHEMA ctt_staff_private TO " + name)
        await admin.execute(
            "INSERT INTO ctt_staff_private.review_batch_services"
            "(service_role,kind,backend_pin,valid_from,valid_until) "
            "VALUES($1,$2,$3,clock_timestamp()-interval '1minute',"
            "clock_timestamp()+interval '1hour')",
            name,
            kind,
            BACKEND,
        )
    funcs = {
        role: [
            "source_preparation_admit",
            "source_preparation_revoke",
            "source_budget_revoke",
        ],
        "ctt_full_source": [
            "source_preparation_context",
            "source_preparation_reserve",
            "source_preparation_finish",
            "source_preparation_budget_report",
            "batch_reconcile_budget",
        ],
        "ctt_full_channel": ["batch_private_run", "batch_capture_context"],
    }
    for user, names in funcs.items():
        for row in await admin.fetch(
            "SELECT oid::regprocedure::text signature FROM pg_proc WHERE "
            "pronamespace='ctt_staff_private'::regnamespace "
            "AND proname=ANY($1::text[])",
            names,
        ):
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION " + row["signature"] + " TO " + user
            )
    stores = {}
    for key, user in (
        ("operator", role),
        ("verifier", "ctt_full_source"),
        ("workflow", "ctt_full_channel"),
    ):
        pool = await asyncpg.create_pool(
            host=database[0],
            port=int(PORT),
            database=database[1],
            user=user,
            min_size=1,
            max_size=2,
        )
        resources.append(pool)
        stores[key] = PostgresBatchReviewStore(pool, backend_pin=BACKEND)
    now = datetime.now(timezone.utc)
    budget = dict(
        schema="ctt.batch.shared-api-budget.v1",
        budget_id=str(uuid4()),
        tenant_id=source.edition["tenant_id"],
        edition_id=source.edition["edition_id"],
        account_id=str(uuid4()),
        quote_sha256=digest(source.quote),
        maximum_units=25_000_000,
        valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(hours=1)).isoformat(),
    )
    prep = uuid4()
    await stores["operator"].source_preparation_admit(
        prep,
        canonical(source.inventory),
        canonical(source.profile),
        canonical(source.quote),
        canonical(budget),
        "sha256:" + digest({"synthetic_approval": str(prep)}),
    )
    return admin, stores, budget, prep


async def _no_capture_authority(admin):
    for table in (
        "review_batch_runs",
        "review_batch_members",
        "review_batch_capabilities",
        "review_batch_qualifications",
        "review_batch_consents",
        "review_batch_child_authorizations",
        "review_source_manifests_v2",
        "review_source_documents_v2",
        "capture_results",
    ):
        assert (
            await admin.fetchval("SELECT count(*) FROM ctt_staff_private." + table) == 0
        )
    for table in ("copa_telmex_teams", "copa_telmex_players", "copa_telmex_team_staff"):
        assert await admin.fetchval("SELECT count(*) FROM public." + table) == 0


async def _observe(source, stores, prep, tmp_path, *, fail=False):
    cache = tmp_path / "source-response-cache"
    cache.mkdir(mode=0o700)
    sdk = SourceSDK(source, fail=fail)
    analyzer = StructuralPageAnalyzer(
        provider=OpenAICttCropClient(
            sdk, model_revision=source.quote["model_revision"], timeout_seconds=30
        ),
        cache=PrivateBatchAnalysisCache(cache),
    )
    context = await stores["verifier"].source_preparation_context(prep)
    with InventorySourceAdapter(source.private).open_verified_inventory(
        source.inventory
    ) as snapshot:
        observations, report = await analyzer.analyze(
            snapshot, context=context, store=stores["verifier"]
        )
    return analyzer, sdk, observations, report


def test_original_to_observation_to_unadmitted_manifest(full_pg, tmp_path):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            admin, stores, budget, prep = await _database(full_pg, resources, source)
            await _no_capture_authority(admin)
            analyzer, sdk, observations, report = await _observe(
                source, stores, prep, tmp_path
            )
            assert len(sdk.calls) == 3
            # Source filenames intentionally suggest different ramas. No filename
            # or inferred gender enters the model's metadata or interpretation.
            for call in sdk.calls:
                for item in call["input"][0]["content"]:
                    if item["type"] == "input_text":
                        assert "filename" not in strict_loads(item["text"].encode())
                        assert (
                            "FEMENIL" not in item["text"]
                            and "VARONIL" not in item["text"]
                        )
            assert report["physical_pages"] == 3 and report["proposed_documents"] == 1
            assert report["stop_reason"] is None
            assert [
                p["classification"]
                for p in report["segmentation"]["page_classifications"]
            ] == ["exception", "document", "document"]
            assert (
                report["api_budget"]["charged_units"] == 15
                and report["api_budget"]["held_units"] == 0
            )
            front, back = observations["pages"][1:]
            assert back["raw_observation"]["category"] is None
            assert back["category"] == "LIBRE" and back["team_key"] == "SOURCE"
            assert back["header_evidence"] == front["header_evidence"]
            assert [s["printed_number"] for s in back["slot_evidence"]] == [3, 4]
            assert (
                back["header_evidence"]["category"]["pdf_sha256"]
                == source.inventory["pages"][1]["pdf_sha256"]
            )
            assert (
                front["source_group"]
                == back["source_group"]
                == "explicit-original-order"
            )
            retained = await stores["verifier"].source_preparation_context(prep)
            assert (
                retained["status"] == "completed"
                and retained["observations"] == observations
            )
            with InventorySourceAdapter(source.private).open_verified_inventory(
                source.inventory
            ) as snapshot:
                recovered = await analyzer.analyze(
                    snapshot, context=retained, store=stores["verifier"]
                )
            assert recovered == (observations, report) and len(sdk.calls) == 3
            assert (
                len(
                    await admin.fetch(
                        "SELECT * FROM ctt_staff_private."
                        "review_batch_budget_reservations"
                    )
                )
                == 3
            )
            with pytest.raises(BatchDenied):
                await stores["workflow"].private_run(UUID(budget["account_id"]), prep)
            with pytest.raises(BatchDenied):
                await stores["workflow"].private_capture_context(prep)
            for name, value in (
                ("edition.json", source.edition),
                ("observations.json", observations),
                ("profile.json", source.profile),
            ):
                path = tmp_path / name
                path.write_bytes(canonical(value))
                path.chmod(0o600)
            output = tmp_path / "proposed-manifests"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.prepare_ctt_batch_sources",
                    "--pdf-root",
                    str(source.input),
                    "--output-root",
                    str(output),
                    "--edition",
                    str(tmp_path / "edition.json"),
                    "--observations",
                    str(tmp_path / "observations.json"),
                    "--profile",
                    str(tmp_path / "profile.json"),
                    "--source-inventory",
                    str(source.private / "source-inventory.json"),
                    "--max-total-bytes",
                    "1000000",
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=True,
            )
            assert "SOURCE" not in result.stdout
            manifest = validate_source_manifest(
                strict_loads((output / "source-manifest-001.json").read_bytes())
            )
            assert len(manifest["documents"]) == 1 and len(manifest["pdfs"]) == 3
            extraction = manifest["documents"][0]["extraction"]
            assert extraction["preparation_provenance"] == dict(
                preparation_id=str(prep),
                inventory_sha256=digest(source.inventory),
                profile_hash=digest(source.profile),
                observations_sha256=digest(observations),
            )
            assert (
                extraction["retained_structural_observations"]
                == observations["pages"][1:]
            )
            assert extraction["structural_observation"]["physical_slots"] == [
                "page:1:jugador_1",
                "page:1:jugador_2",
                "page:2:jugador_3",
                "page:2:jugador_4",
            ]
            assert (
                strict_loads((output / "source-observations.json").read_bytes())
                == observations
            )
            assert (
                strict_loads((output / "source-inventory.json").read_bytes())
                == source.inventory
            )
            assert (
                strict_loads((output / "structural-profile.json").read_bytes())
                == source.profile
            )
            assert all(
                not (p.stat().st_mode & 0o077) for p in output.rglob("*") if p.is_file()
            )
            assert {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.input.iterdir()
            } == source.original_hashes
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_unknown_api_cost_stops_and_keeps_full_census_and_reservation(
    full_pg, tmp_path
):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            admin, stores, _, prep = await _database(full_pg, resources, source)
            _, sdk, observations, report = await _observe(
                source, stores, prep, tmp_path, fail=True
            )
            assert len(sdk.calls) == 1 and len(observations["pages"]) == 3
            assert report["physical_pages"] == 3 and report["proposed_documents"] == 0
            assert report["stop_reason"] == "STRUCTURAL_PROVIDER_UNAVAILABLE"
            assert "PRIVATE_TEST_RESPONSE" not in str(report)
            assert all(
                p["classification"] == "exception"
                for p in report["segmentation"]["page_classifications"]
            )
            assert observations["pages"][1]["reason"] == "STRUCTURAL_NOT_PROCESSED"
            ledger = await admin.fetch(
                "SELECT maximum_units,actual_units,preparation_id,run_id "
                "FROM ctt_staff_private.review_batch_budget_reservations"
            )
            assert (
                len(ledger) == 1
                and ledger[0]["actual_units"] is None
                and ledger[0]["maximum_units"] == 1000
            )
            assert ledger[0]["preparation_id"] == prep and ledger[0]["run_id"] is None
            assert (
                report["api_budget"]["charged_units"] == 0
                and report["api_budget"]["held_units"] == 1000
            )
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_invalid_or_revoked_admission_has_zero_provider_calls(full_pg, tmp_path):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            admin, stores, _, prep = await _database(full_pg, resources, source)
            cache = tmp_path / "cache"
            cache.mkdir(mode=0o700)
            sdk = SourceSDK(source)
            analyzer = StructuralPageAnalyzer(
                provider=OpenAICttCropClient(
                    sdk,
                    model_revision=source.quote["model_revision"],
                    timeout_seconds=30,
                ),
                cache=PrivateBatchAnalysisCache(cache),
            )
            context = await stores["verifier"].source_preparation_context(prep)
            for field in ("profile_hash", "inventory_sha256", "quote_sha256"):
                changed = copy.deepcopy(context)
                changed[field] = "0" * 64
                with InventorySourceAdapter(source.private).open_verified_inventory(
                    source.inventory
                ) as snapshot:
                    with pytest.raises(
                        BatchContractError, match="STRUCTURAL_ADMISSION_MISMATCH"
                    ):
                        await analyzer.analyze(
                            snapshot, context=changed, store=stores["verifier"]
                        )
            await stores["operator"].source_preparation_revoke(prep)
            with pytest.raises(BatchDenied):
                await stores["verifier"].source_preparation_context(prep)
            # Even a stale previously valid context is rechecked before requests.
            with InventorySourceAdapter(source.private).open_verified_inventory(
                source.inventory
            ) as snapshot:
                observations, report = await analyzer.analyze(
                    snapshot, context=context, store=stores["verifier"]
                )
            assert report["stop_reason"] in {"SCOPE_DENIED", "REVIEW_BLOCKED"}
            assert len(observations["pages"]) == 3
            assert not sdk.calls
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_batch_budget_reservations"
                )
                == 0
            )
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_admitted_source_byte_tamper_prevents_any_provider_or_budget_use(
    full_pg, tmp_path
):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            admin, stores, _, prep = await _database(full_pg, resources, source)
            context = await stores["verifier"].source_preparation_context(prep)
            cache = tmp_path / "cache"
            cache.mkdir(mode=0o700)
            sdk = SourceSDK(source)
            analyzer = StructuralPageAnalyzer(
                provider=OpenAICttCropClient(
                    sdk,
                    model_revision=source.quote["model_revision"],
                    timeout_seconds=30,
                ),
                cache=PrivateBatchAnalysisCache(cache),
            )
            original = (
                source.private / "originals" / source.inventory["pdfs"][0]["filename"]
            )
            original.write_bytes(original.read_bytes() + b" altered")
            with pytest.raises(BatchContractError):
                with InventorySourceAdapter(source.private).open_verified_inventory(
                    source.inventory
                ) as snapshot:
                    await analyzer.analyze(
                        snapshot, context=context, store=stores["verifier"]
                    )
            assert not sdk.calls
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_batch_budget_reservations"
                )
                == 0
            )
            assert (await stores["verifier"].source_preparation_context(prep))[
                "status"
            ] == "pending"
            assert {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.input.iterdir()
            } == source.original_hashes
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


@pytest.mark.parametrize("ambiguity", ["missing_printed_slot", "unadmitted_cross_pdf"])
def test_ambiguous_structure_cannot_create_a_proposed_document(
    full_pg, tmp_path, ambiguity
):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            if ambiguity == "missing_printed_slot":
                back = next(r for r in source.raw.values() if r["side"] == "back")
                back["slot_observations"].pop()
            else:
                source.profile["segmentation"]["source_groups"] = {}
            admin, stores, _, prep = await _database(full_pg, resources, source)
            _, sdk, observations, report = await _observe(
                source, stores, prep, tmp_path
            )
            assert len(sdk.calls) == 3
            assert report["proposed_documents"] == 0
            assert report["physical_pages"] == len(observations["pages"]) == 3
            assert all(
                p["classification"] == "exception"
                for p in report["segmentation"]["page_classifications"]
            )
            back_observation = observations["pages"][2]
            if ambiguity == "missing_printed_slot":
                assert (
                    back_observation["reason"] == "STRUCTURAL_SLOT_INVENTORY_INCOMPLETE"
                )
                assert (
                    len(back_observation["raw_observation"]["slot_observations"]) == 1
                )
            else:
                assert back_observation["reason"] == "STRUCTURAL_CONTINUITY_UNRESOLVED"
                assert back_observation["raw_observation"]["header_page_id"] is not None
            assert report["api_budget"]["charged_units"] == 15
            assert report["api_budget"]["held_units"] == 0
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_wrong_source_response_is_retained_without_cache_reuse(full_pg, tmp_path):
    async def check():
        source, resources = _real_source(tmp_path), []
        try:
            admin, stores, budget, prep = await _database(full_pg, resources, source)
            first = next(iter(source.raw.values()))
            expected_render = first["render_sha256"]
            first["render_sha256"] = "0" * 64
            expected_raw = PageBatch.model_validate(
                {"results": [copy.deepcopy(first)]}
            ).model_dump(mode="json")
            analyzer, sdk, observations, report = await _observe(
                source, stores, prep, tmp_path
            )
            assert len(sdk.calls) == 1
            assert report["stop_reason"] == "STRUCTURAL_RESPONSE_SOURCE_MISMATCH"
            assert report["proposed_documents"] == 0
            assert len(observations["pages"]) == 3
            assert all(
                p["classification"] == "exception"
                for p in report["segmentation"]["page_classifications"]
            )
            assert report["api_budget"]["charged_units"] == 5
            assert report["api_budget"]["held_units"] == 0
            ledger = await admin.fetch(
                "SELECT reservation_id,maximum_units,actual_units,receipt_hash "
                "FROM ctt_staff_private.review_batch_budget_reservations"
            )
            assert len(ledger) == 1 and ledger[0]["actual_units"] == 5
            assert ledger[0]["maximum_units"] == 1000
            retained = list(analyzer.cache.root.glob("*.json"))
            assert len(retained) == 1
            path = retained[0]
            envelope = strict_loads(path.read_bytes())
            identity, record = envelope["identity"], envelope["analysis"]
            assert identity["stage"] == PIPELINE + ".attempt-response"
            assert record["schema"] == "ctt.batch.structural-provider-response.v1"
            assert record["parsed_response"] == expected_raw
            assert record["response_unavailable"] is False
            assert record["response_id"] == "synthetic-response-1"
            assert record["model"] == source.quote["model_revision"]
            assert record["service_tier"] == "default"
            assert record["preparation_id"] == str(prep)
            assert record["reservation_id"] == str(ledger[0]["reservation_id"])
            assert record["request"]["inputs"][0]["render"] == expected_render
            assert len(record["request_fingerprint"]) == 64
            assert ledger[0]["receipt_hash"] == digest(record)
            assert report["provider_receipts"] == [
                {
                    "cache_identity_sha256": digest(identity),
                    "record_sha256": digest(record),
                }
            ]
            assert path.stat().st_mode & 0o077 == 0
            assert analyzer.cache.root.stat().st_mode & 0o077 == 0
            await _no_capture_authority(admin)

            # A new legitimate preparation shares the same allowance and cache.
            # The invalid response must remain audit-only, not a reusable result.
            first["render_sha256"] = expected_render
            new_prep = uuid4()
            await stores["operator"].source_preparation_admit(
                new_prep,
                canonical(source.inventory),
                canonical(source.profile),
                canonical(source.quote),
                canonical(budget),
                "sha256:" + digest({"synthetic_approval": str(prep)}),
            )
            context = await stores["verifier"].source_preparation_context(new_prep)
            with InventorySourceAdapter(source.private).open_verified_inventory(
                source.inventory
            ) as snapshot:
                _, replay_report = await analyzer.analyze(
                    snapshot, context=context, store=stores["verifier"]
                )
            assert len(sdk.calls) == 4  # Three fresh requests after quarantine.
            assert replay_report["proposed_documents"] == 1
            assert replay_report["api_budget"]["charged_units"] == 20
            assert replay_report["api_budget"]["held_units"] == 0
            assert replay_report["api_budget"]["budget_id"] == budget["budget_id"]
            assert strict_loads(path.read_bytes()) == envelope
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_batch_budget_reservations"
                )
                == 4
            )
            await _no_capture_authority(admin)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())
