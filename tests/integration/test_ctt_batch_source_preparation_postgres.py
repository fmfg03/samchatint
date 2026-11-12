"""Real-role preparation admission and one shared, source-bound API ceiling."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import asyncpg
import pytest
from test_ctt_batch_acceptance_postgres import operational_case
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_staff_security_postgres import PORT

from devnous.copa_telmex.staff_batch_contracts import canonical, digest
from devnous.copa_telmex.staff_batch_store import BatchDenied, PostgresBatchReviewStore

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261112_ctt_batch_source_preparation.sql"
ROLLBACK = (
    ROOT / "database/migrations/20261112_ctt_batch_source_preparation_rollback.sql"
)


async def preparation_case(database, tmp_path, resources):
    case = await operational_case(database, tmp_path, resources, team_count=1)
    admin = case["admin"]
    await admin.execute(MIGRATION.read_text())
    if not await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ctt_preparation_operator')"
    ):
        await admin.execute("CREATE ROLE ctt_preparation_operator LOGIN NOINHERIT")
    await admin.execute(
        "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_preparation_operator"
    )
    await admin.execute(
        "INSERT INTO ctt_staff_private.review_batch_services"
        "(service_role,kind,backend_pin,valid_from,valid_until) "
        "VALUES('ctt_preparation_operator','operator','acceptance',"
        "clock_timestamp()-interval '1minute',clock_timestamp()+interval '1hour')"
    )
    operator = await asyncpg.create_pool(
        host=database[0],
        port=int(PORT),
        database=database[1],
        user="ctt_preparation_operator",
        min_size=1,
        max_size=2,
    )
    resources.append(operator)
    for role, names in (
        (
            "ctt_preparation_operator",
            [
                "source_preparation_admit",
                "source_preparation_bind_run",
                "source_preparation_revoke",
                "source_budget_revoke",
            ],
        ),
        (
            "ctt_full_source",
            [
                "source_preparation_context",
                "source_preparation_reserve",
                "source_preparation_finish",
                "source_preparation_budget_report",
            ],
        ),
    ):
        rows = await admin.fetch(
            "SELECT oid::regprocedure::text AS signature FROM pg_proc "
            "WHERE pronamespace='ctt_staff_private'::regnamespa"
            "ce AND proname=ANY($1::text[])",
            names,
        )
        for row in rows:
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION " + row["signature"] + " TO " + role
            )
    case["operator"] = PostgresBatchReviewStore(operator, backend_pin="acceptance")
    case["verifier"] = PostgresBatchReviewStore(
        case["pools"]["verifier"], backend_pin="acceptance"
    )
    manifest = case["manifest"]
    pages = await admin.fetch(
        "SELECT DISTINCT pdf_sha256,pdf_page,image_sha256,width,height "
        "FROM ctt_staff_private.review_source_pages_v2 ORDER BY pdf_sha256,pdf_page"
    )
    inventory = {
        "schema": "ctt.batch.source-inventory.v1",
        "edition": {
            key: manifest["edition"][key]
            for key in ("tenant_id", "edition_id", "roster_slug")
        },
        "render_profile": dict(
            version="synthetic-test-render",
            dpi=72,
            color="RGB",
            max_page_pixels=25000000,
            max_render_bytes=33554432,
        ),
        "pdfs": [dict(pdf, bytes=1024) for pdf in manifest["pdfs"]],
        "pages": [
            dict(
                pdf_sha256=p["pdf_sha256"],
                pdf_page=p["pdf_page"],
                render_sha256=p["image_sha256"],
                width=p["width"],
                height=p["height"],
            )
            for p in pages
        ],
    }
    quote = dict(
        revision="synthetic-quote",
        model_revision="synthetic-2026-01-01",
        currency="USD",
        unit="micro_usd",
        input_per_million_micro_usd=1,
        cached_input_per_million_micro_usd=0,
        output_per_million_micro_usd=1,
        model_input_token_limit=1,
    )
    profile = dict(
        schema="ctt.batch.structural-profile.v1",
        templates=[{"id": "synthetic"}],
        segmentation={},
        provider=dict(
            name="openai",
            model_revision=quote["model_revision"],
            pipeline_version="openai.ctt.structural-pages.v1",
            timeout_seconds=30,
            pricing_revision=quote["revision"],
            pricing=quote,
            maximum_call_units=1,
            batch_size=8,
            max_output_tokens=256,
            max_request_bytes=4096,
            max_analysis_bytes=4096,
        ),
    )
    # The extraction run has its own full profile. Register its real bytes in
    # the synthetic capability before run creation; the structural profile is
    # a separate stage and must not stand in for it.
    field_profile = copy.deepcopy(case["profile"])
    field_profile["provider"] = copy.deepcopy(profile["provider"])
    field_profile["provider"]["pipeline_version"] = "openai.ctt.batch_pinned_crops.v1"
    await admin.execute(
        "UPDATE ctt_staff_private.review_batch_capabilities SET profile_hash=$1",
        digest(field_profile),
    )
    case["source_context"]["profile_hash"] = digest(field_profile)
    now = datetime.now(timezone.utc)
    budget = dict(
        schema="ctt.batch.shared-api-budget.v1",
        budget_id=str(uuid4()),
        tenant_id=inventory["edition"]["tenant_id"],
        edition_id=inventory["edition"]["edition_id"],
        account_id=case["context"]["account_id"],
        quote_sha256=digest(quote),
        maximum_units=100,
        valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(hours=1)).isoformat(),
    )
    case.update(
        inventory=inventory,
        quote=quote,
        structural_profile=profile,
        field_profile=field_profile,
        shared_budget=budget,
        prep=uuid4(),
        approval="sha256:" + "9" * 64,
    )
    return case


async def admit(
    case,
    *,
    prep=None,
    inventory=None,
    profile=None,
    quote=None,
    budget=None,
    approval=None,
):
    return await case["operator"].source_preparation_admit(
        prep or case["prep"],
        canonical(inventory or case["inventory"]),
        canonical(profile or case["structural_profile"]),
        canonical(quote or case["quote"]),
        canonical(budget or case["shared_budget"]),
        approval or case["approval"],
    )


async def finish(case, *, prep=None, bad_render=False):
    prep = prep or case["prep"]
    observations = dict(
        schema="ctt.batch.structural-observations.v1",
        preparation_id=str(prep),
        inventory_sha256=digest(case["inventory"]),
        profile_hash=digest(case["structural_profile"]),
        pages=[
            dict(
                pdf_sha256=p["pdf_sha256"],
                pdf_page=p["pdf_page"],
                render_sha256=p["render_sha256"],
                side="unknown",
            )
            for p in case["inventory"]["pages"]
        ],
    )
    if bad_render:
        observations["pages"][0]["render_sha256"] = "0" * 64
    report = dict(
        schema="ctt.batch.structural-report.v1",
        preparation_id=str(prep),
        inventory_sha256=digest(case["inventory"]),
        profile_hash=digest(case["structural_profile"]),
        observations_sha256=digest(observations),
    )
    return await case["verifier"].source_preparation_finish(
        prep, canonical(observations), canonical(report)
    )


def test_exact_preparation_admission_context_finish_no_capture(full_pg, tmp_path):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            first = await admit(case)
            assert await admit(case) == first
            ctx = await case["verifier"].source_preparation_context(case["prep"])
            assert ctx["inventory"] == case["inventory"]
            assert ctx["profile"] == case["structural_profile"]
            assert ctx["quote"] == case["quote"]
            assert ctx["inventory_sha256"] == digest(case["inventory"])
            assert ctx["profile_hash"] == digest(case["structural_profile"])
            assert ctx["remaining_units"] == 100
            with pytest.raises(BatchDenied):
                await finish(case, bad_render=True)
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_source_preparations "
                    "WHERE observations_bytes IS NOT NULL"
                )
                == 0
            )
            final = await finish(case)
            assert final["status"] == "completed"
            assert await finish(case) == final
            assert (await case["verifier"].source_preparation_context(case["prep"]))[
                "status"
            ] == "completed"
            with pytest.raises(BatchDenied, match="REVIEW_BLOCKED"):
                await case["verifier"].source_preparation_reserve(
                    case["prep"], uuid4(), 1
                )
            assert (
                await case["verifier"].source_preparation_budget_report(case["prep"])
            )["remaining_units"] == 100
            admin = case["admin"]
            for table in (
                "review_batch_members",
                "review_batch_consents",
                "review_batch_qualifications",
            ):
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private." + table
                    )
                    == 0
                )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            with pytest.raises(asyncpg.PostgresError, match="retains evidence"):
                await admin.execute(ROLLBACK.read_text())
            # A failed owner migration is always rolled back explicitly.
            if admin.is_in_transaction():
                await admin.execute("ROLLBACK")
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_shared_budget_concurrent_preparations_run_and_no_reset(full_pg, tmp_path):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            await admit(case)
            second = uuid4()
            await admit(case, prep=second)
            account = UUID(case["context"]["account_id"])
            run = UUID(case["context"]["run_id"])
            await case["store"].create_run(account, run, case["initial"])
            await case["admin"].execute(
                "UPDATE ctt_staff_private.review_batch_capabilities"
                " SET provider_price_hash=$1",
                digest(case["quote"]),
            )
            with pytest.raises(BatchDenied):
                await case["verifier"].budget_reserve(run, uuid4(), 1)
            with pytest.raises(BatchDenied):
                await case["operator"].source_preparation_bind_run(
                    case["prep"], run, canonical(case["field_profile"])
                )
            await finish(case)
            bound = await case["operator"].source_preparation_bind_run(
                case["prep"], run, canonical(case["field_profile"])
            )
            assert (
                await case["operator"].source_preparation_bind_run(
                    case["prep"], run, canonical(case["field_profile"])
                )
                == bound
            )
            ids = [uuid4(), uuid4()]

            async def reserve(stage, index):
                try:
                    if stage == "source":
                        result = await case["verifier"].source_preparation_reserve(
                            second, ids[index], 60
                        )
                    else:
                        result = await case["verifier"].budget_reserve(
                            run, ids[index], 60
                        )
                    return index, result
                except BatchDenied as error:
                    return index, error

            outcomes = await asyncio.gather(reserve("source", 0), reserve("run", 1))
            winners = [item for item in outcomes if isinstance(item[1], dict)]
            assert len(winners) == 1
            assert sum(isinstance(item[1], BatchDenied) for item in outcomes) == 1
            winner = ids[winners[0][0]]
            report = await case["verifier"].source_preparation_budget_report(second)
            assert (
                report["held_units"] == 60
                and report["charged_units"] == 0
                and report["remaining_units"] == 40
            )
            settled = await case["verifier"].budget_reconcile(winner, 20, "c" * 64)
            assert (
                await case["verifier"].budget_reconcile(winner, 20, "c" * 64) == settled
            )
            for actual, receipt in ((0, "c" * 64), (20, "d" * 64), (61, "c" * 64)):
                with pytest.raises(BatchDenied):
                    await case["verifier"].budget_reconcile(winner, actual, receipt)
            await case["verifier"].source_preparation_reserve(second, uuid4(), 50)
            third = uuid4()
            await admit(case, prep=third)
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_reserve(third, uuid4(), 40)
            await case["verifier"].source_preparation_reserve(third, uuid4(), 30)
            report = await case["verifier"].source_preparation_budget_report(third)
            assert (
                report["charged_units"],
                report["held_units"],
                report["remaining_units"],
            ) == (20, 80, 0)
            with pytest.raises(BatchDenied):
                await case["operator"].source_preparation_bind_run(
                    third, run, canonical(case["field_profile"])
                )
            with pytest.raises(asyncpg.PostgresError, match="immutable"):
                await case["admin"].execute(
                    "UPDATE ctt_staff_private.review_source_api_budgets"
                    " SET maximum_units=1000"
                )
            await case["operator"].source_budget_revoke(
                UUID(case["shared_budget"]["budget_id"])
            )
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_context(third)
            for call in (
                case["verifier"].source_preparation_reserve(third, uuid4(), 1),
                case["verifier"].budget_reserve(run, uuid4(), 1),
            ):
                with pytest.raises(BatchDenied):
                    await call
            assert (
                await case["verifier"].source_preparation_budget_report(third) == report
            )
            newer = copy.deepcopy(case["initial"])
            newer["idempotency_key"] = str(uuid4())
            newer["request_hash"] = digest(
                {
                    k: newer[k]
                    for k in ("edition_id", "source_manifest_hashes", "idempotency_key")
                }
            )
            newer["input_hash"] = digest(
                {k: v for k, v in newer.items() if k != "input_hash"}
            )
            newrun = uuid4()
            for member in newer["members"]:
                member["member_id"] = str(uuid5(newrun, member["document_id"]))
            newer["input_hash"] = digest(
                {k: v for k, v in newer.items() if k != "input_hash"}
            )
            await case["store"].create_run(account, newrun, newer)
            with pytest.raises(BatchDenied):
                await case["verifier"].budget_reserve(newrun, uuid4(), 1)
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM public.copa_telmex_teams"
                )
                == 0
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_admission_rejects_malformed_scope_price_and_source_without_writes(
    full_pg, tmp_path
):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            bad = []
            inventory = copy.deepcopy(case["inventory"])
            inventory["pages"] = inventory["pages"][:-1]
            bad.append(dict(inventory=inventory))
            inventory = copy.deepcopy(case["inventory"])
            inventory["pages"][1]["pdf_page"] = 1
            bad.append(dict(inventory=inventory))
            inventory = copy.deepcopy(case["inventory"])
            inventory["pages"][0]["render_sha256"] = None
            bad.append(dict(inventory=inventory))
            inventory = copy.deepcopy(case["inventory"])
            inventory["pdfs"][0]["sha256"] = None
            bad.append(dict(inventory=inventory))
            inventory = copy.deepcopy(case["inventory"])
            inventory["pdfs"][0]["filename"] = "../source.pdf"
            bad.append(dict(inventory=inventory))
            inventory = copy.deepcopy(case["inventory"])
            inventory["documents"] = []
            bad.append(dict(inventory=inventory))
            profile = copy.deepcopy(case["structural_profile"])
            profile["provider"]["maximum_call_units"] = 0
            bad.append(dict(profile=profile))
            profile = copy.deepcopy(case["structural_profile"])
            profile["provider"]["pricing"]["revision"] = "mismatch"
            bad.append(dict(profile=profile))
            for key, value in (
                ("tenant_id", "other"),
                ("edition_id", str(uuid4())),
                ("quote_sha256", "0" * 64),
                ("maximum_units", True),
            ):
                budget = copy.deepcopy(case["shared_budget"])
                budget[key] = value
                bad.append(dict(budget=budget))
            for kwargs in bad:
                with pytest.raises(BatchDenied):
                    await admit(case, **kwargs)
                assert (
                    await case["admin"].fetchval(
                        "SELECT count(*) FROM ctt_staff_private.review_sour"
                        "ce_preparations"
                    )
                    == 0
                )
                assert (
                    await case["admin"].fetchval(
                        "SELECT count(*) FROM ctt_staff_private.review_sour"
                        "ce_api_budgets"
                    )
                    == 0
                )
            await admit(case)
            changed = copy.deepcopy(case["shared_budget"])
            changed["maximum_units"] = 101
            with pytest.raises(BatchDenied):
                await admit(case, budget=changed)
            changed = copy.deepcopy(case["shared_budget"])
            changed["budget_id"] = str(uuid4())
            with pytest.raises(BatchDenied):
                await admit(case, prep=uuid4(), budget=changed)
            # App and verifier do not inherit operator admission or table authority.
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_admit(
                    case["prep"],
                    canonical(case["inventory"]),
                    canonical(case["structural_profile"]),
                    canonical(case["quote"]),
                    canonical(case["shared_budget"]),
                    case["approval"],
                )
            for table in (
                "review_source_preparations",
                "review_source_api_budgets",
                "review_source_inventories",
                "review_source_budget_run_bindings",
            ):
                assert not await case["admin"].fetchval(
                    "SELECT has_table_privilege('devnous_user',$1,'SELE"
                    "CT,INSERT,UPDATE,DELETE')",
                    "ctt_staff_private." + table,
                )
                assert not await case["admin"].fetchval(
                    "SELECT has_table_privilege('ctt_preparation_operat"
                    "or',$1,'INSERT,UPDATE,DELETE')",
                    "ctt_staff_private." + table,
                )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_expired_and_revoked_preparation_hold_usage_and_retain_finish(
    full_pg, tmp_path
):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            past = copy.deepcopy(case["shared_budget"])
            now = datetime.now(timezone.utc)
            past["valid_from"] = (now - timedelta(hours=2)).isoformat()
            past["valid_until"] = (now - timedelta(hours=1)).isoformat()
            await admit(case, budget=past)
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_context(case["prep"])
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_reserve(
                    case["prep"], uuid4(), 1
                )
            assert (
                await case["verifier"].source_preparation_budget_report(case["prep"])
            )["held_units"] == 0
            # A separate explicit approval is a distinct budget, never a reset.
            live = copy.deepcopy(case["shared_budget"])
            live["budget_id"] = str(uuid4())
            live_prep = uuid4()
            await admit(
                case, prep=live_prep, budget=live, approval="sha256:" + "8" * 64
            )
            reservation = uuid4()
            await case["verifier"].source_preparation_reserve(
                live_prep, reservation, 60
            )
            await case["operator"].source_preparation_revoke(live_prep)
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_context(live_prep)
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_reserve(live_prep, uuid4(), 1)
            report = await case["verifier"].source_preparation_budget_report(live_prep)
            assert report["held_units"] == 60 and report["remaining_units"] == 40
            # In-flight evidence and actual incurred usage remain
            # auditable after revoke.
            final = await finish(case, prep=live_prep)
            assert final["status"] == "completed"
            await case["verifier"].budget_reconcile(reservation, 30, "f" * 64)
            report = await case["verifier"].source_preparation_budget_report(live_prep)
            assert (
                report["charged_units"],
                report["held_units"],
                report["remaining_units"],
            ) == (30, 0, 70)
            with pytest.raises(BatchDenied):
                await admit(
                    case, prep=live_prep, budget=live, approval="sha256:" + "8" * 64
                )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_partial_inventory_overlap_cannot_fall_back_or_bind(full_pg, tmp_path):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            account = UUID(case["context"]["account_id"])
            run = UUID(case["context"]["run_id"])
            await case["store"].create_run(account, run, case["initial"])
            # An owner-declared inventory with only part of this physical PDF is
            # not enough to bind this run; overlap must still forbid fallback.
            partial = copy.deepcopy(case["inventory"])
            partial["pdfs"][0]["page_count"] = 1
            partial["pages"] = partial["pages"][:1]
            await admit(case, inventory=partial)
            case["inventory"] = partial
            await finish(case)
            await case["admin"].execute(
                "UPDATE ctt_staff_private.review_batch_capabilities"
                " SET provider_price_hash=$1",
                digest(case["quote"]),
            )
            with pytest.raises(BatchDenied):
                await case["verifier"].budget_reserve(run, uuid4(), 1)
            with pytest.raises(BatchDenied):
                await case["operator"].source_preparation_bind_run(
                    case["prep"], run, canonical(case["field_profile"])
                )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batc"
                    "h_budget_reservations"
                )
                == 0
            )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_sour"
                    "ce_budget_run_bindings"
                )
                == 0
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_empty_rollback_preserves_legacy_reservations_and_functions(full_pg, tmp_path):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            account = UUID(case["context"]["account_id"])
            run = UUID(case["context"]["run_id"])
            await case["store"].create_run(account, run, case["initial"])
            reservation = uuid4()
            await case["verifier"].budget_reserve(run, reservation, 60)
            originals = await case["admin"].fetch(
                "SELECT signature,function_definition FROM ctt_staf"
                "f_private.review_source_preparation_installation O"
                "RDER BY signature"
            )
            await case["admin"].execute(ROLLBACK.read_text())
            assert (
                await case["admin"].fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_source_preparations')"
                )
                is None
            )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batc"
                    "h_budget_reservations"
                )
                == 1
            )
            for row in originals:
                assert (
                    await case["admin"].fetchval(
                        "SELECT pg_get_functiondef($1::regprocedure)", row["signature"]
                    )
                    == row["function_definition"]
                )
            settled = await case["verifier"].budget_reconcile(reservation, 20, "e" * 64)
            assert settled["actual_units"] == 20
            await case["verifier"].budget_reserve(run, uuid4(), 80)
            with pytest.raises(BatchDenied):
                await case["verifier"].budget_reserve(run, uuid4(), 1)
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_mixed_pdf_manifest_needs_explicit_covering_shared_inventory(full_pg, tmp_path):
    async def check():
        import hashlib
        import time

        import fitz
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from devnous.copa_telmex.staff_batch_contracts import validate_source_manifest
        from devnous.copa_telmex.staff_batch_source_admission import (
            admit_prepared_sources_v2,
            prepare_source_admission_v2,
            sign_source_admission_v2,
        )
        from devnous.copa_telmex.staff_source_admission import SourceAdmissionLimits

        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            await admit(case)
            await finish(case)
            original = case["manifest"]
            source_root = tmp_path / "originals"
            with fitz.open() as pdf:
                pdf.new_page(width=612, height=792)
                raw = pdf.tobytes()
            extra = source_root / "additional-source.pdf"
            extra.write_bytes(raw)
            extra.chmod(0o600)
            mixed = copy.deepcopy(original)
            mixed.pop("manifest_sha256", None)
            mixed["pdfs"].append(
                dict(
                    filename=extra.name,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    page_count=1,
                )
            )
            mixed = validate_source_manifest(mixed)
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            with prepare_source_admission_v2(
                canonical(mixed),
                source_root,
                edition=mixed["edition"],
                limits=SourceAdmissionLimits(
                    4_000_000, 4_000_000, 16_000_000, 4_000_000, 16_000_000
                ),
            ) as prepared:
                proof = sign_source_admission_v2(
                    prepared.plan_bytes,
                    admission_id=uuid4(),
                    issued_at=int(time.time()),
                    expires_at=int(time.time()) + 300,
                    kid="synthetic-mixed-inventory",
                    private_key=key,
                )
                await admit_prepared_sources_v2(
                    prepared,
                    proof,
                    public_keys={"synthetic-mixed-inventory": public},
                    private_root=case["cas"],
                    pool=case["pools"]["verifier"],
                )
            # Explicit operator-side synthetic capability for this actual signed
            # source; no approval, consent, child or canonical rows are inserted.
            new_policy = digest(
                {
                    "test_policy": "mixed-source-funding",
                    "manifest_sha256": mixed["manifest_sha256"],
                }
            )
            await case["admin"].execute(
                "INSERT INTO ctt_staff_private.review_batch_capabilities "
                "SELECT $1,backend_pin,account_id,principal_id,cred"
                "ential_id,tenant_id,edition_id,$2,$3,profile_hash,"
                "actor_role,account_version,principal_version,crede"
                "ntial_version,authority_version,valid_from,valid_u"
                "ntil,revoked_at,approval_ref,provider_budget_units"
                ",$4 "
                "FROM ctt_staff_private.review_batch_capabilities LIMIT 1",
                uuid4(),
                mixed["manifest_sha256"],
                new_policy,
                digest(case["quote"]),
            )
            run = uuid4()
            payload = copy.deepcopy(case["initial"])
            payload["source_manifest_hashes"] = [mixed["manifest_sha256"]]
            payload["idempotency_key"] = str(uuid4())
            for member in payload["members"]:
                member["member_id"] = str(uuid5(run, member["document_id"]))
                member["manifest_sha256"] = mixed["manifest_sha256"]
                member["policy_hash"] = new_policy
            payload["request_hash"] = digest(
                {
                    k: payload[k]
                    for k in ("edition_id", "source_manifest_hashes", "idempotency_key")
                }
            )
            payload["input_hash"] = digest(
                {k: v for k, v in payload.items() if k != "input_hash"}
            )
            await case["store"].create_run(
                UUID(case["context"]["account_id"]), run, payload
            )
            assert await case["admin"].fetchval(
                "SELECT ctt_staff_private.source_budget_overlaps_run($1)", run
            )
            with pytest.raises(BatchDenied):
                await case["verifier"].budget_reserve(run, uuid4(), 1)
            with pytest.raises(BatchDenied):
                await case["operator"].source_preparation_bind_run(
                    case["prep"], run, canonical(case["field_profile"])
                )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batc"
                    "h_budget_reservations"
                )
                == 0
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_micro_usd_quote_requires_full_input_and_output_reservation(full_pg, tmp_path):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            quote = dict(
                revision="synthetic-reviewed-standard-20261112",
                model_revision="gpt-5.4-mini-2026-03-17",
                currency="USD",
                unit="micro_usd",
                input_per_million_micro_usd=750000,
                cached_input_per_million_micro_usd=75000,
                output_per_million_micro_usd=4500000,
                model_input_token_limit=400000,
            )
            profile = copy.deepcopy(case["structural_profile"])
            profile["provider"].update(
                pricing=quote,
                model_revision=quote["model_revision"],
                pricing_revision=quote["revision"],
                max_output_tokens=8000,
                maximum_call_units=335999,
            )
            budget = copy.deepcopy(case["shared_budget"])
            budget.update(quote_sha256=digest(quote), maximum_units=25000000)
            with pytest.raises(BatchDenied):
                await admit(case, profile=profile, quote=quote, budget=budget)
            profile["provider"]["maximum_call_units"] = 336000
            await admit(case, profile=profile, quote=quote, budget=budget)
            with pytest.raises(BatchDenied):
                await case["verifier"].source_preparation_reserve(
                    case["prep"], uuid4(), 335999
                )
            reservation = uuid4()
            await case["verifier"].source_preparation_reserve(
                case["prep"], reservation, 336000
            )
            report = await case["verifier"].source_preparation_budget_report(
                case["prep"]
            )
            assert report["held_units"] == 336000
            assert report["remaining_units"] == 24664000
            await case["verifier"].budget_reconcile(reservation, 1000, "f" * 64)
            report = await case["verifier"].source_preparation_budget_report(
                case["prep"]
            )
            assert report["charged_units"] == 1000 and report["held_units"] == 0
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


@pytest.mark.parametrize("mismatch", ["profile_hash", "actual_pricing"])
def test_shared_run_binds_actual_extraction_profile_not_only_price_label(
    full_pg, tmp_path, mismatch
):
    async def check():
        resources = []
        try:
            case = await preparation_case(full_pg, tmp_path, resources)
            await admit(case)
            await finish(case)
            account = UUID(case["context"]["account_id"])
            run = UUID(case["context"]["run_id"])
            wrong_profile = copy.deepcopy(case["field_profile"])
            wrong_profile["provider"]["pricing"]["output_per_million_micro_usd"] = 2
            registered = (
                wrong_profile if mismatch == "actual_pricing" else case["field_profile"]
            )
            # Model an operator's incorrect price label on a genuinely
            # registered extraction profile, rather than forging a run row.
            await case["admin"].execute(
                "UPDATE ctt_staff_private.review_batch_capabilities "
                "SET profile_hash=$1,provider_price_hash=$2",
                digest(registered),
                digest(case["quote"]),
            )
            await case["store"].create_run(account, run, case["initial"])
            assert await case["admin"].fetchval(
                "SELECT profile_hash FROM ctt_staff_private.review_batch_runs "
                "WHERE run_id=$1",
                run,
            ) == digest(registered)
            assert await case["admin"].fetchval(
                "SELECT provider_price_hash FROM "
                "ctt_staff_private.review_batch_capabilities LIMIT 1"
            ) == digest(case["quote"])
            with pytest.raises(BatchDenied):
                await case["operator"].source_preparation_bind_run(
                    case["prep"], run, canonical(wrong_profile)
                )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_source_budget_run_bindings"
                )
                == 0
            )
            assert (
                await case["admin"].fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_batch_budget_reservations"
                )
                == 0
            )
            assert (
                await case["verifier"].source_preparation_budget_report(case["prep"])
            )["remaining_units"] == 100
            if mismatch == "profile_hash":
                exact = canonical(case["field_profile"])
                result = await case["operator"].source_preparation_bind_run(
                    case["prep"], run, exact
                )
                assert result["field_profile_hash"] == digest(case["field_profile"])
                stored = await case["admin"].fetchrow(
                    "SELECT field_profile_bytes,field_profile_hash FROM "
                    "ctt_staff_private.review_source_budget_run_bindings "
                    "WHERE run_id=$1",
                    run,
                )
                assert stored["field_profile_bytes"] == exact
                assert stored["field_profile_hash"] == digest(case["field_profile"])
                assert (
                    await case["operator"].source_preparation_bind_run(
                        case["prep"], run, exact
                    )
                    == result
                )
                with pytest.raises(BatchDenied):
                    await case["operator"].source_preparation_bind_run(
                        case["prep"], run, exact + b" "
                    )
                assert (
                    await case["admin"].fetchval(
                        "SELECT field_profile_bytes FROM "
                        "ctt_staff_private.review_source_budget_run_bindings "
                        "WHERE run_id=$1",
                        run,
                    )
                    == exact
                )
            assert await case["admin"].fetchval(
                "SELECT to_regprocedure('ctt_staff_private."
                "source_preparation_bind_run(uuid,uuid)') IS NULL"
            )
            assert await case["admin"].fetchval(
                "SELECT to_regprocedure('ctt_staff_private."
                "source_preparation_bind_run(uuid,uuid,bytea)') IS NOT NULL"
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())
