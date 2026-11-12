"""Independent batch acceptance on restored empty PostgreSQL, synthetic PDFs.

No operational database, provider call, real credential or capture is used.
Positive cases exercise the actual canonical writer and optional finality RPC;
this remains technical acceptance, not real-corpus qualification or human UAT.
"""

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import asyncpg
import fitz
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_review_proposals_postgres import install_recovery
from test_ctt_staff_security_postgres import PORT

from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    digest,
    source_refs,
    stable_document_id,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_source_admission import (
    admit_prepared_sources_v2,
    prepare_source_admission_v2,
    sign_source_admission_v2,
)
from devnous.copa_telmex.staff_batch_store import PostgresBatchReviewStore
from devnous.copa_telmex.staff_signed_assertion import (
    PLATFORM_POLICY_HASH,
    PLATFORM_PROFILE,
)
from devnous.copa_telmex.staff_source_admission import SourceAdmissionLimits

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]


async def connect(database, user="postgres"):
    return await asyncpg.connect(
        host=database[0], port=int(PORT), user=user, database=database[1]
    )


async def install(conn):
    await conn.execute(
        (ROOT / "database/migrations/20261031_ctt_passkey_enrollment.sql").read_text()
    )
    await install_recovery(conn)
    await conn.execute(
        (ROOT / "database/migrations/20261031_ctt_review_proposals.sql").read_text()
    )
    for name in ("sources_v2", "review_state", "consent"):
        await conn.execute(
            (ROOT / f"database/migrations/20261109_ctt_batch_{name}.sql").read_text()
        )
    assert await conn.fetchval(
        (
            "SELECT to_regclass('ctt_staff_private.capture_attempt_registry') "
            "IS NOT NULL"
        )
    )


def test_individual_v2_real_capture_with_batch_migrations_installed(
    full_pg, tmp_path, monkeypatch
):
    ("Established V2 proposal/UV/writer with the new migration bytes " "installed.")
    from test_ctt_review_workflow_postgres import (
        test_private_prepare_to_official_committed_session as individual,
    )

    original_read = Path.read_text
    applied = []

    def read_with_batch_migrations(path, *args, **kwargs):
        raw = original_read(path, *args, **kwargs)
        if path == ROOT / "database/migrations/20261031_ctt_review_proposals.sql":
            # Execute the candidate DDL immediately after its true prerequisites,
            # before the established fixture admits its restricted services.
            for name in ("sources_v2", "review_state", "consent"):
                raw += "\n" + original_read(
                    ROOT / f"database/migrations/20261109_ctt_batch_{name}.sql"
                )
                applied.append(name)
        return raw

    monkeypatch.setattr(Path, "read_text", read_with_batch_migrations)
    individual(full_pg, tmp_path, monkeypatch, "success")
    assert applied == ["sources_v2", "review_state", "consent"]


def test_restored_schema_preserves_private_acl_and_no_implicit_authority(full_pg):
    async def check():
        conn = await connect(full_pg)
        try:
            await install(conn)
            assert not await conn.fetchval(
                "SELECT has_schema_privilege('devnous_user',"
                "'ctt_staff_private','USAGE,CREATE')"
            )
            rows = await conn.fetch(
                "SELECT p.oid::regprocedure::text signature,p.prosecdef,"
                "p.proconfig,r.rolname FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner "
                "WHERE n.nspname='ctt_staff_private' AND p.proname LIKE 'batch_%'"
            )
            assert len(rows) >= 30
            for row in rows:
                assert row["prosecdef"]
                assert row["proconfig"] == ["search_path=pg_catalog"]
                assert row["rolname"] == "samchat_ctt_staff_owner"
                assert not await conn.fetchval(
                    "SELECT has_function_privilege('devnous_user',$1,'EXECUTE')",
                    row["signature"],
                )
            for table in (
                "review_batch_services",
                "review_batch_capabilities",
                "review_batch_consents",
                "review_batch_child_authorizations",
                "review_batch_consumptions",
                "review_source_legacy_bridges_v2",
                "capture_attempt_registry",
                "capture_results",
            ):
                assert (
                    await conn.fetchval(
                        f"SELECT count(*) FROM ctt_staff_private.{table}"
                    )
                    == 0
                )
                assert not await conn.fetchval(
                    "SELECT has_table_privilege('devnous_user',$1,"
                    "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
                    "ctt_staff_private." + table,
                )
            app = await connect(full_pg, "devnous_user")
            try:
                await app.execute("SET ctt.batch_preparation = 'true'")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.fetchval(
                        "SELECT ctt_staff_private.batch_catalog($1)", uuid4()
                    )
            finally:
                await app.close()
        finally:
            await conn.close()

    asyncio.run(check())


def test_real_role_budget_concurrency_and_expired_analysis_fencing(full_pg, tmp_path):
    async def check():
        from devnous.copa_telmex.staff_batch_store import BatchDenied

        resources = []
        try:
            case = await operational_case(full_pg, tmp_path, resources)
            context, store, admin = case["context"], case["store"], case["admin"]
            account, run = UUID(context["account_id"]), UUID(context["run_id"])
            await store.create_run(account, run, case["initial"])
            verifier = PostgresBatchReviewStore(
                case["pools"]["verifier"], backend_pin="acceptance"
            )

            async def reserve():
                try:
                    return await verifier.budget_reserve(run, uuid4(), 60)
                except BatchDenied as exc:
                    return exc

            outcomes = await asyncio.gather(reserve(), reserve())
            assert sum(isinstance(value, dict) for value in outcomes) == 1
            assert sum(isinstance(value, BatchDenied) for value in outcomes) == 1
            assert (
                await admin.fetchval(
                    (
                        "SELECT sum(maximum_units) FROM "
                        "ctt_staff_private.review_batch_budget_reservations"
                    )
                )
                == 60
            )
            owner, successor = uuid4(), uuid4()
            first = await store.claim_analysis(owner)
            await admin.execute(
                (
                    "UPDATE ctt_staff_private.review_batch_analysis_jobs SET "
                    "lease_until=clock_timestamp()-interval '1second' WHERE run_id=$1"
                ),
                run,
            )
            second = await store.claim_analysis(successor)
            assert second["run_id"] == first["run_id"]
            assert second["lease_epoch"] == first["lease_epoch"] + 1
            with pytest.raises(BatchDenied):
                await store.analysis_heartbeat(run, owner, first["lease_epoch"])
            await store.analysis_heartbeat(run, successor, second["lease_epoch"])
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_batch_capture_identities"
                )
                == 5
            )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_scale_sql_rejects_missing_or_invalid_frozen_metrics(full_pg, tmp_path):
    """Negative SQL boundary only: deliberately invalid private owner fixtures.

    Synthetic candidate/completed-block rows do not represent consent, a real
    earlier capture or a passing full pipeline. They reach the scale guard so
    malformed private evidence cannot bypass its numeric requirements.
    """

    async def check():
        resources = []
        try:
            case = await operational_case(full_pg, tmp_path, resources)
            admin, store = case["admin"], case["store"]
            account, run = UUID(case["context"]["account_id"]), UUID(
                case["context"]["run_id"]
            )
            await store.create_run(account, run, case["initial"])
            good = {"numerator": 80, "denominator": 100}
            invalid = [
                None,
                {},
                {"numerator": None, "denominator": 100},
                {"numerator": "80", "denominator": 100},
                {"numerator": True, "denominator": 100},
                {"numerator": -1, "denominator": 100},
                {"numerator": 80.1, "denominator": 100},
                {"numerator": 0, "denominator": 0},
                {"numerator": 101, "denominator": 100},
                {"numerator": 79, "denominator": 100},
            ]
            first = True
            for side in ("teams", "pages"):
                for metric in invalid + [good]:
                    metrics = {"teams": good, "pages": good, side: metric}
                    raw = canonical({"metrics": metrics, "probe": str(uuid4())})
                    qualification = uuid4()
                    await admin.execute(
                        "INSERT INTO "
                        "ctt_staff_private.review_batch_qualifications("
                        "qualification_id,run_id,candidate_bytes,"
                        "candidate_hash,profile_hash,corpus_hash) "
                        "VALUES($1,$2,$3,$4,$5,$6)",
                        qualification,
                        run,
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                        digest(case["profile"]),
                        case["initial"]["corpus_hash"],
                    )
                    await admin.execute(
                        "UPDATE ctt_staff_private.review_batch_runs SET "
                        "qualification_id=$1,status='qualified' WHERE run_id=$2",
                        qualification,
                        run,
                    )
                    if first:
                        await admin.execute(
                            "INSERT INTO ctt_staff_private.review_batch_blocks("
                            "block_id,run_id,qualification_id,manifest_sha256,"
                            "policy_hash,root_bytes,root_hash,status) "
                            "VALUES($1,$2,$3,$4,$5,$6,$7,'completed')",
                            uuid4(),
                            run,
                            qualification,
                            case["manifest"]["manifest_sha256"],
                            case["context"]["policy_hash"],
                            b"{}",
                            "a" * 64,
                        )
                        first = False
                    async with case["pools"]["workflow"].acquire() as workflow:
                        expected = (
                            "root binding denied"
                            if metric == good
                            else "automation (metrics incomplete|"
                            "qualification insufficient)"
                        )
                        with pytest.raises(asyncpg.PostgresError, match=expected):
                            await workflow.fetchval(
                                "SELECT ctt_staff_private."
                                "batch_prepare_block($1,$2,$3,1,$4::jsonb,$5)",
                                account,
                                run,
                                uuid4(),
                                json.dumps([str(uuid4())]),
                                b"{}",
                            )
            async with case["pools"]["workflow"].acquire() as workflow:
                for count, expected in (
                    (50, "root binding denied"),
                    (51, "member set denied"),
                ):
                    with pytest.raises(asyncpg.PostgresError, match=expected):
                        await workflow.fetchval(
                            "SELECT ctt_staff_private."
                            "batch_prepare_block($1,$2,$3,1,$4::jsonb,$5)",
                            account,
                            run,
                            uuid4(),
                            json.dumps([str(uuid4()) for _ in range(count)]),
                            b"{}",
                        )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
                )
                == 0
            )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


def test_signed_cross_pdf_source_admission_replay_and_immutability(full_pg, tmp_path):
    source = tmp_path / "originals"
    source.mkdir(mode=0o700)
    cas = tmp_path / "cas"
    cas.mkdir(mode=0o700)
    edition = {
        "tenant_id": "acceptance-synthetic",
        "edition_id": str(uuid4()),
        "tournament_id": str(uuid4()),
        "edition_year": 2026,
        "roster_slug": "ctt-acceptance-synthetic",
        "phase": "national",
    }
    pdfs = []
    for index in range(2):
        with fitz.open() as pdf:
            page = pdf.new_page(width=612, height=792)
            page.insert_text((40, 40), f"SYNTHETIC continuation {index}")
            raw = pdf.tobytes()
        path = source / f"part-{index}.pdf"
        path.write_bytes(raw)
        path.chmod(0o600)
        pdfs.append(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "page_count": 1,
            }
        )
    slices = [{"pdf_sha256": pdf["sha256"], "pages": [1]} for pdf in pdfs]
    document = stable_document_id(edition["edition_id"], source_refs(slices))
    manifest = validate_source_manifest(
        {
            "schema_version": "ctt.registration.source-manifest.v2",
            "edition": edition,
            "pdfs": pdfs,
            "documents": [
                {"document_id": document, "source_slices": slices, "extraction": {}}
            ],
        }
    )
    limits = SourceAdmissionLimits(
        2_000_000, 2_000_000, 8_000_000, 2_000_000, 8_000_000
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    async def check():
        admin = await connect(full_pg)
        pool = None
        try:
            await install(admin)
            # Existing limited source role; no superuser/set-role execution.
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                "register_batch_source_admission(uuid,bytea,bytea,bytea,"
                "bytea,jsonb,jsonb) TO ctt_full_source"
            )
            pool = await asyncpg.create_pool(
                host=full_pg[0],
                port=int(PORT),
                user="ctt_full_source",
                database=full_pg[1],
                min_size=1,
                max_size=1,
            )
            with prepare_source_admission_v2(
                canonical(manifest), source, edition=edition, limits=limits
            ) as prepared:
                now = int(time.time())
                proof = sign_source_admission_v2(
                    prepared.plan_bytes,
                    admission_id=uuid4(),
                    issued_at=now,
                    expires_at=now + 300,
                    kid="acceptance-source",
                    private_key=key,
                )
                kwargs = dict(
                    public_keys={"acceptance-source": public},
                    private_root=cas,
                    pool=pool,
                )
                first = await admit_prepared_sources_v2(prepared, proof, **kwargs)
                assert (
                    await admit_prepared_sources_v2(prepared, proof, **kwargs) == first
                )
            pages = await admin.fetch(
                "SELECT page_index,pdf_sha256,pdf_page FROM "
                "ctt_staff_private.review_source_pages_v2 ORDER BY page_index"
            )
            assert [
                (p["page_index"], p["pdf_sha256"], p["pdf_page"]) for p in pages
            ] == [(i + 1, pdf["sha256"], 1) for i, pdf in enumerate(pdfs)]
            for sql in (
                "UPDATE ctt_staff_private.review_source_pages_v2 SET pdf_page=2",
                "DELETE FROM ctt_staff_private.review_source_documents_v2",
                "TRUNCATE ctt_staff_private.review_source_manifests_v2 CASCADE",
            ):
                with pytest.raises(
                    asyncpg.PostgresError, match="immutable|mutation|evidence"
                ):
                    await admin.execute(sql)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_source_manifests_v2"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_source_pages_v2"
                )
                == 2
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_source_legacy_bridges_v2"
                )
                == 0
            )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_results"
                )
                == 0
            )
        finally:
            if pool is not None:
                await pool.close()
            await admin.close()

    asyncio.run(check())


def synthetic_observation_profile(analysis):
    """Declare both crop methods and every printed slot before predictions."""
    from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields

    values = semantic_fields(analysis)
    slots = []
    for index, player in enumerate(analysis["players"], 1):
        page, slot = player["slot_ref"].split(":")[1:]
        number = int(slot.removeprefix("jugador_"))
        field = f"slots.{page}.{number}.presence"
        slots.append(
            dict(slot_ref=player["slot_ref"], presence_field=field, person_index=index)
        )
        values[field] = "PRESENT"
    views = []
    for method in ("first", "second"):
        fields = {}
        for field in values:
            parts = field.split(".")
            if parts[0] == "team":
                page, x, y, w, h = 1, 20, 0, 650, 60
            else:
                if parts[0] == "slots":
                    ref = f"page:{parts[1]}:jugador_{parts[2]}"
                    person = next(
                        p for p in analysis["players"] if p["slot_ref"] == ref
                    )
                    name = "presence"
                else:
                    person = analysis[parts[0]][int(parts[1]) - 1]
                    name = parts[2]
                box = person["boxes"][
                    "birth_date" if name == "birth_date" else "full_name"
                ]
                page = person["source_page_index"]
                x, y, w, h = box["x"], box["y"], box["width"], box["height"]
                if name == "presence":
                    x, w = 20, 670
                if name in {"curp", "photo_sha256"}:
                    x, w = 650, 40
            if method == "second":
                x, y, w, h = max(0, x - 5), max(0, y - 5), w + 10, h + 10
            fields[field] = dict(
                page_index=page, x=x / 700, y=y / 900, width=w / 700, height=h / 900
            )
        views.append(dict(method=method, fields=fields))
    return (
        dict(
            template_id="acceptance-two-page",
            physical_slots=slots,
            observation_views=views,
            blank_profile=dict(white_threshold=250, max_nonwhite_fraction=0.001),
        ),
        values,
    )


def native_supplied_photo_source(base_analysis):
    """Actual calibrated twenty-slot source; public portrait, no detector mock."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from devnous.copa_telmex.staff_batch_observation_inputs import _player_photo_box
    from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields
    from devnous.tournaments.core.ctt_batch_photo_alignment import (
        _face_present,
        extract_batch_photo,
        prepare_batch_page,
    )

    face_path = ROOT / "tests/fixtures/ctt-public-photo/grace_hopper.jpg"
    assert (
        hashlib.sha256(face_path.read_bytes()).hexdigest()
        == "a8ca6d734765703b09728ab47fe59f473d93ae3967fc24c7c0288c3c7adb7130"
    )
    observed = copy.deepcopy(base_analysis)
    observed["team"]["name"] = "SyntheticPhoto"
    observed["players"] = []
    for number in range(1, 21):
        person = copy.deepcopy(base_analysis["players"][0])
        page = 1 if number <= 8 else 2
        row = (number - 1) // 2 + 1 if page == 1 else (number - 9) // 2
        col = (number - 1) % 2 if page == 1 else (number - 9) % 2
        top = (238 if page == 1 else 115) + row * 97
        x, width = (125, 180) if col == 0 else (389, 174)
        person.update(
            first_name=f"Synthetic{number}",
            last_name="NativePlayer",
            birth_date=f"2000-01-{number:02d}",
            source_page_index=page,
            slot_ref=f"page:{page}:jugador_{number}",
        )
        person["boxes"] = {
            name: dict(page_index=page, x=x, y=top + 3, width=width, height=90)
            for name in ("full_name", "birth_date")
        }
        observed["players"].append(person)
    for number, person in enumerate(observed["staff"], 1):
        x, width = (125, 180) if number == 1 else (389, 174)
        person["boxes"] = {
            name: dict(page_index=1, x=x, y=241, width=width, height=90)
            for name in ("full_name", "birth_date")
        }
    images = []
    with Image.open(face_path) as portrait:
        assert _face_present(portrait.resize((70, 91)))
        for page_index in (1, 2):
            image = Image.new("RGB", (612, 792), "white")
            draw = ImageDraw.Draw(image)
            top, rows = (238, 5) if page_index == 1 else (115, 6)
            for x in (46, 122, 311, 385, 565):
                draw.line((x, top, x, top + rows * 97), fill="black", width=2)
            for row in range(rows + 1):
                draw.line(
                    (46, top + row * 97, 565, top + row * 97), fill="black", width=2
                )
            draw.text((20, 20), "SyntheticPhoto UNICA femenil", fill="black")
            if page_index == 1:
                image.paste(portrait.resize((66, 87)), (51, 340))
                with portrait.crop((100, 0, 450, 450)) as staff_portrait:
                    image.paste(staff_portrait.resize((60, 80)), (54, 246))
            for kind in ("players", "staff"):
                for person in observed[kind]:
                    if person["source_page_index"] != page_index:
                        continue
                    box = person["boxes"]["full_name"]
                    draw.text(
                        (box["x"], box["y"] + 12),
                        person["first_name"] + " " + person["last_name"],
                        fill="black",
                    )
                    draw.text(
                        (box["x"], box["y"] + 34), person["birth_date"], fill="black"
                    )
                    if kind == "staff":
                        draw.text(
                            (box["x"], box["y"] + 56), person["role"], fill="black"
                        )
            images.append(image)
    with fitz.open() as pdf:
        for image in images:
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            page = pdf.new_page(width=612, height=792)
            page.insert_image(page.rect, stream=buffer.getvalue())
        raw_pdf = pdf.tobytes()
    rendered = []
    with fitz.open(stream=raw_pdf, filetype="pdf") as pdf:
        rendered = [p.get_pixmap(alpha=False).tobytes("png") for p in pdf]
    with Image.open(BytesIO(rendered[0])) as image:
        crop, status = extract_batch_photo(image, page_index=1, slot=1)
        assert status == "photo" and crop is not None
        crop.close()
    grids = []
    for page_index, raw_png in enumerate(rendered, 1):
        with Image.open(BytesIO(raw_png)) as page_image:
            grid = prepare_batch_page(page_image, page_index)
            assert grid is not None
            grids.append(grid)
    for kind in ("players", "staff"):
        for index, person in enumerate(observed[kind], 1):
            page = person["source_page_index"]
            n = index
            row = (
                0
                if kind == "staff"
                else ((n - 1) // 2 + 1 if page == 1 else (n - 9) // 2)
            )
            top, bottom = (
                grids[page - 1].boundaries[row],
                grids[page - 1].boundaries[row + 1],
            )
            for box in person["boxes"].values():
                box.update(y=int(top) + 3, height=int(bottom - top) - 6)
    values = semantic_fields(observed)
    slots = []
    for index, p in enumerate(observed["players"], 1):
        page = p["source_page_index"]
        field = f"slots.{page}.{index}.presence"
        values[field] = "PRESENT"
        slots.append(
            dict(slot_ref=p["slot_ref"], presence_field=field, person_index=index)
        )
    first = {}
    for field in values:
        parts = field.split(".")
        if parts[0] == "team":
            page, x, y, w, h = 1, 20, 20, 570, 190
        else:
            if parts[0] == "slots":
                person = observed["players"][int(parts[2]) - 1]
                name = "presence"
            else:
                person = observed[parts[0]][int(parts[1]) - 1]
                name = parts[2]
            page = person["source_page_index"]
            box = person["boxes"]["full_name"]
            x, y, w, h = box["x"], box["y"], box["width"], box["height"]
            if parts[0] == "staff":
                col = int(parts[1]) - 1
                row = 0
            else:
                n = int(person["slot_ref"].split("jugador_")[1])
                col = (n - 1) % 2 if page == 1 else (n - 9) % 2
                row = (n - 1) // 2 + 1 if page == 1 else (n - 9) // 2
            top, bottom = (
                grids[page - 1].boundaries[row],
                grids[page - 1].boundaries[row + 1],
            )
            row_top = int(top)
            if name == "presence":
                x, y, w, h = (
                    (44 if col == 0 else 309),
                    row_top,
                    (265 if col == 0 else 258),
                    int(bottom - top),
                )
                if row == (4 if page == 1 else 5):
                    h += 10
            elif name == "photo_sha256":
                if parts[0] == "staff":
                    grid = grids[0]
                    middle = (top + bottom) / 2
                    rail_left, rail_right = grid.rails[col * 2], grid.rails[col * 2 + 1]
                    x = int(rail_left[0] * middle + rail_left[1]) + 5
                    right = int(rail_right[0] * middle + rail_right[1]) - 5
                    y, w, h = int(top) + 5, right - x, int(bottom - top) - 10
                else:
                    _, bounds = _player_photo_box(
                        rendered[page - 1], person["slot_ref"]
                    )
                    x, y, w, h = (
                        bounds[0],
                        bounds[1],
                        bounds[2] - bounds[0],
                        bounds[3] - bounds[1],
                    )
        first[field] = dict(
            page_index=int(page),
            x=float(x) / 612,
            y=float(y) / 792,
            width=float(w) / 612,
            height=float(h) / 792,
        )
    second = {
        k: {
            **v,
            "x": max(0, v["x"] - 1 / 612),
            "y": max(0, v["y"] - 1 / 792),
            "width": v["width"] + 2 / 612,
            "height": v["height"] + 2 / 792,
        }
        for k, v in first.items()
    }
    profile = dict(
        template_id="acceptance-native-twenty",
        physical_slots=slots,
        observation_views=[
            dict(method="first", fields=first),
            dict(method="second", fields=second),
        ],
        blank_profile=dict(white_threshold=250, max_nonwhite_fraction=0.001),
    )
    observed["verification_input"].update(
        expected_fields=list(values),
        proposed_fields=values,
        required_fields=[k for k, v in values.items() if v is not None],
    )
    for reading in observed["verification_input"]["readings"]:
        reading["fields"] = dict(values)
    return [observed], profile, raw_pdf


async def operational_case(
    database,
    tmp_path,
    resources,
    *,
    team_count=5,
    supplied_photos=False,
    manual_photo=False,
    mixed=False,
):
    ("Real admitted sources and protected synthetic identity; no " "approval rows.")
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.cose import ES256
    from fido2.webauthn import AttestedCredentialData

    spec = importlib.util.spec_from_file_location(
        "acceptance_batch_plan_fixture",
        ROOT / "tests/unit/test_ctt_batch_plan_builder.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder, context, _, analysis, _ = module.proposal.__wrapped__(tmp_path)
    source_root = tmp_path / "originals"
    source_root.mkdir(mode=0o700)
    cas = tmp_path / "cas"
    cas.mkdir(mode=0o700)
    observations = []
    from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields

    for team_index in range(team_count):
        observed = copy.deepcopy(analysis)
        observed["team"]["name"] = f"Synthetic{team_index}"
        for kind in ("players", "staff"):
            for number, person in enumerate(observed[kind], 1):
                person["last_name"] += f"Team{team_index}"
                row = (number - 1) % 8 if kind == "players" else number + 7
                for name, x in (("full_name", 20), ("birth_date", 350)):
                    person["boxes"][name].update(
                        x=x, y=80 + row * 65, width=300, height=45
                    )
        fields = semantic_fields(observed)
        observed["verification_input"]["proposed_fields"] = fields
        for reading in observed["verification_input"]["readings"]:
            reading["fields"] = dict(fields)
        observations.append(observed)
    profile, _ = synthetic_observation_profile(observations[0])
    for observed in observations:
        _, fields = synthetic_observation_profile(observed)
        observed["verification_input"]["expected_fields"] = list(fields)
        observed["verification_input"]["proposed_fields"] = fields
        for reading in observed["verification_input"]["readings"]:
            reading["fields"] = dict(fields)
    with fitz.open() as pdf:
        for page_number in range(team_count * 2):
            page = pdf.new_page(width=700, height=900)
            observed = observations[page_number // 2]
            page.insert_text((20, 20), observed["team"]["name"] + " UNICA femenil")
            if manual_photo and page_number == 10:
                from io import BytesIO

                from PIL import Image

                with Image.open(
                    ROOT / "tests/fixtures/ctt-public-photo/grace_hopper.jpg"
                ) as face:
                    encoded = BytesIO()
                    face.resize((36, 40)).save(encoded, format="PNG")
                page.insert_image(
                    fitz.Rect(652, 602, 688, 642), stream=encoded.getvalue()
                )
            for kind in ("players", "staff"):
                for person in observed[kind]:
                    if person["source_page_index"] != page_number % 2 + 1:
                        continue
                    name_box, date_box = (
                        person["boxes"]["full_name"],
                        person["boxes"]["birth_date"],
                    )
                    page.insert_text(
                        (name_box["x"], name_box["y"] + 20),
                        person["first_name"] + " " + person["last_name"],
                    )
                    page.insert_text(
                        (date_box["x"], date_box["y"] + 20), person["birth_date"]
                    )
        raw = pdf.tobytes()
    if supplied_photos:
        assert team_count == 1
        observations, profile, raw = native_supplied_photo_source(analysis)
    path = source_root / "consolidated.pdf"
    path.write_bytes(raw)
    path.chmod(0o600)
    pdf_hash = hashlib.sha256(raw).hexdigest()
    edition = dict(
        tenant_id="synthetic",
        edition_id=context["edition_id"],
        tournament_id=str(uuid4()),
        edition_year=2026,
        roster_slug="ctt-synthetic",
        phase="national",
    )
    documents = []
    for number in range(team_count):
        slices = [{"pdf_sha256": pdf_hash, "pages": [2 * number + 1, 2 * number + 2]}]
        documents.append(
            dict(
                document_id=stable_document_id(
                    edition["edition_id"], source_refs(slices)
                ),
                source_slices=slices,
                extraction={
                    "structural_observation": {
                        "template_id": profile["template_id"],
                        "category": "UNICA",
                        "physical_slots": [
                            slot["slot_ref"] for slot in profile["physical_slots"]
                        ],
                        "source_refs": [
                            dict(pdf_sha256=sha, pdf_page=page)
                            for sha, page in source_refs(slices)
                        ],
                    }
                },
            )
        )
    manifest = validate_source_manifest(
        dict(
            schema_version="ctt.registration.source-manifest.v2",
            edition=edition,
            pdfs=[dict(filename=path.name, sha256=pdf_hash, page_count=team_count * 2)],
            documents=documents,
        )
    )
    builder._policy["phase_binding"]["manifest_sha256"] = manifest["manifest_sha256"]
    builder._phase_binding["manifest_sha256"] = manifest["manifest_sha256"]
    builder._policy.update(
        policy_id="samchat_registration_identity_policy_v2",
        workflow_id="samchat_registration_identity_v2",
        allowed_roles=["reviewer"],
    )
    builder._policy.pop("policy_hash")
    builder._policy_hash = "sha256:" + digest(builder._policy)
    builder._policy["policy_hash"] = builder._policy_hash
    context["policy_hash"] = builder._policy_hash.removeprefix("sha256:")
    context["manifest_sha256"] = manifest["manifest_sha256"]
    context["account_id"] = str(uuid4())
    admin = await connect(database)
    pools = {}
    resources.append(admin)
    await install(admin)
    if not await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ctt_accept_worker')"
    ):
        await admin.execute("CREATE ROLE ctt_accept_worker LOGIN NOINHERIT")
    await admin.execute("GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_accept_worker")
    for kind, role in (
        ("worker", "ctt_accept_worker"),
        ("workflow", "ctt_full_channel"),
        ("verifier", "ctt_full_source"),
        ("issuer", "ctt_full_issuer"),
        ("writer", "ctt_full_writer"),
    ):
        await admin.execute(
            (
                "INSERT INTO "
                "ctt_staff_private.review_batch_services(service_role,kind,backend"
                "_pin,valid_from,valid_until) "
                "VALUES($1,$2,'acceptance',clock_timestamp()-interval "
                "'1minute',clock_timestamp()+interval '2hours')"
            ),
            role,
            kind,
        )
        # Grant only the typed functions needed by each private component.
        names = {
            "workflow": [
                "batch_source_context",
                "batch_create_run",
                "batch_private_run",
                "batch_read_run",
                "batch_claim_analysis",
                "batch_analysis_heartbeat",
                "batch_complete_preparation",
                "batch_block_analysis",
                "batch_register_sample",
                "batch_refresh_verifications",
                "batch_prepare_block",
                "batch_private_block",
                "batch_create_challenge",
                "batch_recover_consent",
                "batch_pause",
                "batch_resume",
                "batch_revoke",
                "batch_transition",
                "batch_capture_context",
                "batch_private_result",
                "batch_project_capture_rows",
                "batch_stage_capture_payload",
                "batch_load_finality_gate_context",
                "batch_propose_member_corrections",
                "batch_member_context",
                "batch_member_detail",
            ],
            "verifier": [
                "batch_reserve_budget",
                "batch_reconcile_budget",
                "batch_verifier_context",
            ],
            "worker": ["batch_claim", "batch_recover_job", "batch_mark_uncertain"],
            "issuer": [
                "batch_load_consent_context",
                "batch_register_consent",
                "batch_load_child_context",
                "batch_issue_child",
            ],
            "writer": [
                "batch_lock_child",
                "batch_consume_child",
                "batch_record_result",
                "batch_lock_legacy_capture_identity",
            ],
        }[kind]
        rows = await admin.fetch(
            (
                "SELECT oid::regprocedure::text signature FROM pg_proc WHERE "
                "pronamespace='ctt_staff_private'::regnamespace AND "
                "proname=ANY($1::text[])"
            ),
            names,
        )
        for row in rows:
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION " + row["signature"] + " TO " + role
            )
        pools[kind] = await asyncpg.create_pool(
            host=database[0],
            port=int(PORT),
            user=role,
            database=database[1],
            min_size=1,
            max_size=2,
        )
        resources.append(pools[kind])
    await admin.execute(
        (
            "GRANT EXECUTE ON FUNCTION "
            "ctt_staff_private.register_batch_source_admission(uuid,bytea,byte"
            "a,bytea,bytea,jsonb,jsonb) TO ctt_full_source"
        )
    )
    source_key = Ed25519PrivateKey.generate()
    public = source_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    limits = SourceAdmissionLimits(
        4_000_000, 4_000_000, 16_000_000, 4_000_000, 16_000_000
    )
    with prepare_source_admission_v2(
        canonical(manifest), source_root, edition=edition, limits=limits
    ) as prepared:
        proof = sign_source_admission_v2(
            prepared.plan_bytes,
            admission_id=uuid4(),
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 300,
            kid="synthetic-source",
            private_key=source_key,
        )
        await admit_prepared_sources_v2(
            prepared,
            proof,
            public_keys={"synthetic-source": public},
            private_root=cas,
            pool=pools["verifier"],
        )
    private = ec.generate_private_key(ec.SECP256R1())
    credential = AttestedCredentialData.create(
        bytes(16),
        b"synthetic-acceptance-credential",
        ES256.from_cryptography_key(private.public_key()),
    )
    actor, account, edition_id = (
        UUID(context["principal_id"]),
        UUID(context["account_id"]),
        UUID(context["edition_id"]),
    )
    await admin.execute(
        (
            "INSERT INTO "
            "ctt_staff_private.review_principals(principal_id,active) "
            "VALUES($1,true)"
        ),
        actor,
    )
    await admin.execute(
        (
            "INSERT INTO "
            "ctt_staff_private.review_credentials(credential_id,principal_id,a"
            "ttested_credential,user_handle,auth_profile,auth_policy_hash,back"
            "up_eligible,backup_state,sign_count) "
            "VALUES($1,$2,$3,$4,$5,$6,true,true,0)"
        ),
        credential.credential_id,
        actor,
        bytes(credential),
        b"synthetic-handle",
        PLATFORM_PROFILE,
        PLATFORM_POLICY_HASH,
    )
    await admin.execute(
        (
            "INSERT INTO "
            "ctt_staff_private.review_account_links(backend_pin,account_id,ten"
            "ant_id,edition_id,principal_id,actor_role,active) "
            "VALUES('acceptance',$1,'synthetic',$2,$3,'reviewer',true)"
        ),
        account,
        edition_id,
        actor,
    )
    await admin.execute(
        (
            "INSERT INTO "
            "ctt_staff_private.review_batch_capabilities(capability_id,backend"
            "_pin,account_id,principal_id,credential_id,tenant_id,edition_id,m"
            "anifest_sha256,policy_hash,profile_hash,actor_role,account_versio"
            "n,principal_version,credential_version,valid_from,valid_until,app"
            "roval_ref,provider_budget_units,provider_price_hash) "
            "VALUES($1,'acceptance',$2,$3,$4,'synthetic',$5,$6,$7,$8,'reviewer"
            "',1,1,1,clock_timestamp()-interval "
            "'1minute',clock_timestamp()+interval '1hour',$9,100,$10)"
        ),
        uuid4(),
        account,
        actor,
        credential.credential_id,
        edition_id,
        manifest["manifest_sha256"],
        context["policy_hash"],
        digest(profile),
        "sha256:" + "a" * 64,
        "b" * 64,
    )
    store = PostgresBatchReviewStore(pools["workflow"], backend_pin="acceptance")
    legacy = None
    if mixed:
        legacy = await seed_same_legacy_source(
            admin, pools, cas, source_root, manifest, observations[0], edition
        )
    source_context = await store.preparation_context(
        account, edition_id, [manifest["manifest_sha256"]]
    )
    run_id = UUID(context["run_id"])
    members = [
        dict(
            member_id=str(uuid5(run_id, s["document_id"])),
            document_id=s["document_id"],
            manifest_sha256=s["manifest_sha256"],
            policy_hash=context["policy_hash"],
            source_payload_hash=s["source_payload_hash"],
            source_pages=s["source_pages"],
            status="analysis_pending",
            payload_hash=None,
            scope_hash=None,
            attempt_id=None,
            snapshot=None,
            issues=[],
            label="synthetic",
        )
        for s in source_context["documents"]
    ]
    initial = dict(
        edition_id=str(edition_id),
        source_manifest_hashes=[manifest["manifest_sha256"]],
        idempotency_key=str(uuid4()),
        request_hash="c" * 64,
        corpus_hash=digest(
            {"edition": str(edition_id), "manifests": [manifest["manifest_sha256"]]}
        ),
        input_hash="e" * 64,
        members=members,
    )
    from devnous.tournaments.core.ctt_batch_qualification import select_sample

    initial["documents"] = [
        {
            "document_id": d["document_id"],
            "source_slices": d["source_slices"],
            "template_id": d["extraction"]["structural_observation"]["template_id"],
            "category": d["extraction"]["structural_observation"]["category"],
        }
        for d in manifest["documents"]
    ]
    initial["sample_documents"] = list(
        select_sample(initial["documents"], corpus_hash=initial["corpus_hash"])
    )
    initial["request_hash"] = digest(
        {
            k: initial[k]
            for k in ("edition_id", "source_manifest_hashes", "idempotency_key")
        }
    )
    initial["input_hash"] = digest(
        {k: v for k, v in initial.items() if k != "input_hash"}
    )
    return dict(
        legacy=legacy,
        mixed=mixed,
        team_count=team_count,
        player_count=20 if supplied_photos else 16,
        supplied_photos=supplied_photos,
        manual_photo=manual_photo,
        resources=resources,
        database=database,
        profile=profile,
        admin=admin,
        pools=pools,
        store=store,
        builder=builder,
        context=context,
        analysis=analysis,
        observations=observations,
        source_context=source_context,
        manifest=manifest,
        cas=cas,
        initial=initial,
        credential=credential,
        private=private,
    )


def bind_supplied_photos(case, observation, snapshot, source, tmp_path):
    """Recreate both public-source photographs from actual retained pixels."""
    from types import SimpleNamespace

    from devnous.copa_telmex.registration_governance import _derive_geometry
    from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields
    from devnous.copa_telmex.staff_photo_store import derive_rectangular_review_photo

    player = observation["players"][0]
    actual_player = snapshot.derive_player_photo(player["slot_ref"])
    player.update(
        photo={k: getattr(actual_player, k) for k in ("photo_path", "sha256", "ahash")},
        photo_evidence=dict(mode="BATCH_PHYSICAL_SLOT", slot_ref=player["slot_ref"]),
    )
    page = source["source_pages"][0]
    raw = snapshot.page_assets[UUID(page["asset_id"])]
    spec = case["profile"]["observation_views"][0]["fields"]["staff.1.photo_sha256"]
    coords = {
        k: round(spec[k] * page["width" if k in {"x", "width"} else "height"])
        for k in ("x", "y", "width", "height")
    }
    image_path = tmp_path / "retained-staff-source.png"
    image_path.write_bytes(raw)
    geometry = _derive_geometry(
        SimpleNamespace(image_path=image_path, sha256=page["image_sha256"]), coords
    )
    assert geometry is not None
    actual_staff = derive_rectangular_review_photo(
        raw, geometry, max_page_bytes=4_000_000
    )
    observation["staff"][0].update(
        photo={k: getattr(actual_staff, k) for k in ("photo_path", "sha256", "ahash")},
        photo_evidence=dict(mode="SOURCE_RECTANGLE", geometry=geometry),
    )
    assert actual_player.sha256 != actual_staff.sha256
    fields = semantic_fields(observation)
    fields.update(
        {
            slot["presence_field"]: "PRESENT"
            for slot in case["profile"]["physical_slots"]
        }
    )
    observation["verification_input"].update(
        expected_fields=list(fields),
        proposed_fields=fields,
        required_fields=[k for k, v in fields.items() if v is not None],
    )
    for reading in observation["verification_input"]["readings"]:
        reading["fields"] = dict(fields)
    case["expected_supplied_photos"] = dict(player=actual_player, staff=actual_staff)


def supplied_photo_negative_cases(case, observation, snapshot, source):
    """Equal cached readings cannot hide or swap an actual source photograph."""
    from devnous.copa_telmex.staff_batch_observation_inputs import (
        verify_observation_inputs,
    )
    from devnous.copa_telmex.staff_batch_store import BatchDenied

    verify_observation_inputs(
        snapshot, source["source_pages"], case["profile"], observation
    )
    for kind in ("players", "staff"):
        for fault in ("drop", "swap"):
            modified = copy.deepcopy(observation)
            person = modified[kind][0]
            if fault == "drop":
                person.update(photo=None, photo_evidence=None)
                value = None
            else:
                other = "staff" if kind == "players" else "players"
                person["photo"] = copy.deepcopy(modified[other][0]["photo"])
                value = person["photo"]["sha256"]
            field = kind + ".1.photo_sha256"
            modified["verification_input"]["proposed_fields"][field] = value
            for reading in modified["verification_input"]["readings"]:
                reading["fields"][field] = value
            with pytest.raises(BatchDenied):
                verify_observation_inputs(
                    snapshot, source["source_pages"], case["profile"], modified
                )
            snapshot.verify_unchanged()


async def qualify_and_consent(case, analyzed):
    ("Real verifier signatures, qualification activation and " "authenticator UV.")
    import secrets

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.webauthn import (
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )
    from staff_batch_issuer_v3 import PostgresBatchAssertionIssuer

    from devnous.copa_telmex.staff_batch_source_adapter import (
        PrivateBatchSourceProvider,
        SourceCheckAdapterV2,
    )
    from devnous.copa_telmex.staff_batch_source_service import (
        BatchCatalogSourceProvider,
        PrivateBatchAnalysisCache,
    )
    from devnous.copa_telmex.staff_batch_store import BatchDenied
    from devnous.copa_telmex.staff_batch_verification import BatchPrivateVerifier
    from devnous.copa_telmex.staff_batch_workflow import BatchReviewWorkflowService

    admin, store = case["admin"], case["store"]
    account, run = UUID(case["context"]["account_id"]), UUID(case["context"]["run_id"])
    verifier_key, issuer_key = (
        Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
    )
    for kind, key in (("verifier", verifier_key), ("issuer", issuer_key)):
        await admin.execute(
            (
                "UPDATE ctt_staff_private.review_batch_services SET "
                "signing_kid=$1,public_key=$2,profile_hash=$3 WHERE kind=$1"
            ),
            kind,
            key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ),
            case["source_context"]["profile_hash"],
        )
    adapter = SourceCheckAdapterV2(
        PrivateBatchSourceProvider(case["cas"]),
        max_source_bytes=4_000_000,
        max_asset_bytes=4_000_000,
        max_manifest_bytes=4_000_000,
    )
    profile = case["profile"]
    provider = BatchCatalogSourceProvider(
        store=store,
        source_adapter=adapter,
        profiles={digest(profile): profile},
        builders={case["context"]["policy_hash"]: case["builder"]},
        analysis_cache=PrivateBatchAnalysisCache(case["cas"]),
    )
    verifier = BatchPrivateVerifier(
        source_reader=provider,
        store=PostgresBatchReviewStore(
            case["pools"]["verifier"], backend_pin="acceptance"
        ),
        signing_key=verifier_key,
        kid="verifier",
        profile_hash=digest(profile),
    )
    workflow = BatchReviewWorkflowService(
        store=store, source_provider=provider, verifier=verifier
    )
    initial_chosen = [m for m in analyzed["members"] if m["status"] == "ready"][:5]
    proposed = await workflow.sample(
        account,
        run,
        {
            "expected_run_version": 2,
            "observations": [
                dict(
                    document_id=doc,
                    outcome=(
                        "matches"
                        if doc in analyzed["metrics"]["automatic_document_ids"]
                        else "exception_confirmed"
                    ),
                    notes="Known synthetic source matches retained fields",
                )
                for doc in analyzed["sample_documents"]
            ],
        },
    )
    assert proposed["status"] == "qualified"
    with pytest.raises(BatchDenied):
        await store.prepare_block(
            account,
            run,
            uuid4(),
            proposed["version"],
            [UUID(m["member_id"]) for m in initial_chosen] + [uuid4()],
            canonical({}),
        )
    assert (
        await admin.fetchval(
            (
                "SELECT count(*) FROM "
                "ctt_staff_private.review_batch_qualification_activations"
            )
        )
        == 0
    )
    block = await workflow.prepare(
        account,
        run,
        {
            "expected_run_version": proposed["version"],
            "member_ids": [m["member_id"] for m in initial_chosen],
            "idempotency_key": str(uuid4()),
        },
    )
    assert block["member_count"] == len(initial_chosen)
    block_id = UUID(block["block_id"])
    challenge = await store.create_challenge(
        account, block_id, block["version"], uuid4(), secrets.token_bytes(32)
    )
    with pytest.raises(BatchDenied):
        await store.create_challenge(
            account, block_id, block["version"], uuid4(), secrets.token_bytes(32)
        )
    client = CollectedClientData.create(
        "webauthn.get",
        base64.b64decode(challenge["challenge"]),
        "https://sam.chat",
        cross_origin=False,
    )
    authenticator = AuthenticatorData.create(
        hashlib.sha256(b"sam.chat").digest(),
        AuthenticatorData.FLAG.UP
        | AuthenticatorData.FLAG.UV
        | AuthenticatorData.FLAG.BE
        | AuthenticatorData.FLAG.BS,
        0,
    )
    response = AuthenticationResponse(
        raw_id=case["credential"].credential_id,
        response=AuthenticatorAssertionResponse(
            client_data=client,
            authenticator_data=authenticator,
            signature=case["private"].sign(
                bytes(authenticator) + client.hash, ec.ECDSA(hashes.SHA256())
            ),
            user_handle=b"synthetic-handle",
        ),
    )
    issuer = PostgresBatchAssertionIssuer(
        case["pools"]["issuer"],
        signing_kid="issuer",
        signing_key=issuer_key,
        verifier_public_keys={
            "verifier": verifier_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        },
    )
    if case.get("use_issuer_rpc"):
        issuer = await remote_issuer(case, issuer, issuer_key)
    challenge_id = UUID(challenge["challenge_id"])
    proof = await issuer.issue_consent(
        challenge_id, canonical(json.loads(json.dumps(dict(response))))
    )
    assert (
        await issuer.issue_consent(
            challenge_id, canonical(json.loads(json.dumps(dict(response))))
        )
        == proof
    )
    active = await store.recover_consent(account, block_id)
    assert active["status"] == "active"
    parent_body = json.loads(proof)["body"]
    assert 0 < parent_body["expires_at"] - parent_body["issued_at"] <= 3600
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
        )
        == 1
    )
    assert (
        await admin.fetchval(
            (
                "SELECT count(*) FROM "
                "ctt_staff_private.review_batch_qualification_activations"
            )
        )
        == 1
    )
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM "
            "ctt_staff_private.review_batch_child_authorizations"
        )
        == 0
    )
    worker_store = PostgresBatchReviewStore(
        case["pools"]["worker"], backend_pin="acceptance"
    )
    worker_id = uuid4()
    job = await worker_store.claim(worker_id)
    assert job["status"] == "leased"
    assert (await worker_store.claim(uuid4()))["status"] == "busy"
    child = await issuer.issue_child(
        UUID(job["consent_id"]), UUID(job["member_id"]), worker_id, job["lease_epoch"]
    )
    assert (
        await issuer.issue_child(
            UUID(job["consent_id"]),
            UUID(job["member_id"]),
            worker_id,
            job["lease_epoch"],
        )
        == child
    )
    child_body = json.loads(child)["body"]
    assert 0 < child_body["expires_at"] - child_body["issued_at"] <= 300
    assert child_body["expires_at"] <= parent_body["expires_at"]
    return dict(
        case=case,
        workflow=workflow,
        issuer=issuer,
        block=block,
        active=active,
        proof=proof,
        job=job,
        child=child,
        worker_id=worker_id,
        worker_store=worker_store,
        issuer_key=issuer_key,
        verifier_key=verifier_key,
        adapter=adapter,
    )


async def remote_issuer(case, issuer, issuer_key):
    """Real independent HTTP endpoint with only the synthetic issuer SQL pool."""
    import socket
    from types import SimpleNamespace

    import httpx
    import uvicorn
    from fastapi import FastAPI

    from devnous.copa_telmex.staff_batch_issuer_channel import (
        BatchIssuerClient,
        mount_batch_issuer_rpc,
    )
    from devnous.copa_telmex.staff_capture_transport_store import (
        PostgresTransportNonces,
    )

    app = FastAPI()
    mount_batch_issuer_rpc(
        app,
        issuer=issuer,
        channel_key=b"j" * 32,
        consume_nonce=PostgresTransportNonces(case["pools"]["workflow"]),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))

    async def stop():
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()

    case["resources"].append(SimpleNamespace(close=stop))
    deadline = time.monotonic() + 10
    while not server.started:
        assert not task.done() and time.monotonic() < deadline
        await asyncio.sleep(0.01)
    http = httpx.AsyncClient(trust_env=False)
    case["resources"].append(SimpleNamespace(close=http.aclose))
    return BatchIssuerClient(
        url=f"http://127.0.0.1:{listener.getsockname()[1]}",
        channel_key=b"j" * 32,
        http_client=http,
        issuer_public_keys={
            "issuer": issuer_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        },
    )


@asynccontextmanager
async def finality_route(case, gate, tmp_path, keys, *, rpc):
    """Optional real loopback Uvicorn/MAC path with the genuine SQL loader."""
    if not rpc:
        yield gate
        return
    import socket

    import httpx
    import uvicorn
    from deployment.ctt_protected_review.finality_channel import (
        BatchFinalityGateRPCServer,
        BatchFinalityRPCClient,
        mount_finality_rpc,
    )
    from fastapi import FastAPI

    from devnous.copa_telmex.staff_batch_runtime import BatchPolicyFinalityService
    from devnous.copa_telmex.staff_capture_transport_store import (
        PostgresTransportNonces,
    )

    service = BatchFinalityGateRPCServer(
        gate=BatchPolicyFinalityService({case["context"]["policy_hash"]: gate}),
        store=case["store"],
        private_source_root=case["cas"],
        database_identity="isolated-copa",
        max_source_bytes=4_000_000,
        max_asset_bytes=4_000_000,
        max_manifest_bytes=4_000_000,
    )
    app = FastAPI()
    mount_finality_rpc(
        app,
        service=service,
        channel_key=b"z" * 32,
        operations_channel_key=b"o" * 32,
        consume_nonce=PostgresTransportNonces(case["pools"]["workflow"]),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    http = httpx.Client(trust_env=False)
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert not task.done() and time.monotonic() < deadline
            await asyncio.sleep(0.01)
        yield BatchFinalityRPCClient(
            base_url=f"http://127.0.0.1:{port}",
            channel_key=b"z" * 32,
            http_client=http,
            effect_key=b"e" * 32,
            review_key=b"r" * 32,
            **keys,
            timeout_seconds=30,
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        http.close()
        listener.close()


async def revocation_commit_race(consent_case, adapter, writer_errors):
    """A revoke waits for the real writer transaction already past its guard."""
    from unittest.mock import patch

    from devnous.copa_telmex.staff_batch_authorization import BatchAuthorizationDenied
    from devnous.copa_telmex.staff_batch_issuer_channel import BatchIssuerUnavailable
    from devnous.copa_telmex.staff_capture_postgres import _CaptureTransaction

    case = consent_case["case"]
    admin, store = case["admin"], case["store"]
    block = UUID(consent_case["block"]["block_id"])
    account = UUID(case["context"]["account_id"])
    current = await store.private_block(account, block)
    entered, release = asyncio.Event(), asyncio.Event()
    original = _CaptureTransaction.capture_batch
    writer_pid = None

    async def held_after_effect(tx, *args, **kwargs):
        nonlocal writer_pid
        result = await original(tx, *args, **kwargs)
        writer_pid = tx._conn.get_server_pid()
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return result

    capture, revoke = None, None
    with patch.object(_CaptureTransaction, "capture_batch", held_after_effect):
        try:
            capture = asyncio.create_task(
                adapter.prepare_and_capture(consent_case["job"], consent_case["child"])
            )
            await asyncio.wait_for(entered.wait(), 15)
            # Other sessions cannot observe the uncommitted full roster/result.
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            revoke = asyncio.create_task(
                store.transition(account, block, current["version"], "revoke")
            )
            deadline = time.monotonic() + 5
            blocked = None
            while time.monotonic() < deadline:
                blocked = await admin.fetchrow(
                    "SELECT pid,pg_blocking_pids(pid) blockers "
                    "FROM pg_stat_activity WHERE datname=current_database() "
                    "AND usename='ctt_full_channel' "
                    "AND query LIKE 'SELECT ctt_staff_private.batch_revoke%' "
                    "AND wait_event_type='Lock'"
                )
                if blocked and writer_pid in blocked["blockers"]:
                    break
                assert not revoke.done()
                await asyncio.sleep(0.01)
            assert blocked and writer_pid in blocked["blockers"]
            assert not revoke.done()
            release.set()
            result = await asyncio.wait_for(capture, 20)
            if writer_errors:
                raise AssertionError("race writer failed") from writer_errors[0]
            assert result["status"] == "committed"
            revoked = await asyncio.wait_for(revoke, 10)
            assert revoked["status"] == "revoked"
            assert (
                await adapter.recover(UUID(consent_case["job"]["attempt_id"])) == result
            )
            for table, count in (
                ("copa_telmex_teams", 1),
                ("copa_telmex_players", 16),
                ("copa_telmex_team_staff", 2),
            ):
                assert (
                    await admin.fetchval(f"SELECT count(*) FROM public.{table}")
                    == count
                )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_results"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_consumptions"
                )
                == 1
            )
            other = await admin.fetchval(
                "SELECT member_id FROM "
                "ctt_staff_private.review_batch_block_members "
                "WHERE block_id=$1 AND member_id<>$2 LIMIT 1",
                block,
                UUID(consent_case["job"]["member_id"]),
            )
            with pytest.raises((BatchAuthorizationDenied, BatchIssuerUnavailable)):
                await consent_case["issuer"].issue_child(
                    UUID(consent_case["job"]["consent_id"]),
                    other,
                    consent_case["worker_id"],
                    consent_case["job"]["lease_epoch"],
                )
            assert (await consent_case["worker_store"].claim(uuid4()))[
                "status"
            ] == "idle"
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_batch_child_authorizations"
                )
                == 1
            )
        finally:
            release.set()
            for task in (capture, revoke):
                if task is not None and not task.done():
                    try:
                        await asyncio.wait_for(task, 20)
                    except Exception:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)


async def canonical_captures(
    consent_case,
    tmp_path,
    *,
    rpc=False,
    rollback=False,
    revoke_race=False,
    manual_photo=False,
):
    ("Use real signed Bus events, Unix transport and canonical owner " "writer.")
    import os

    from durable_event_store import DurableJSONLEventStore
    from evidence_receipt import issue_evidence_receipt
    from staff_capture_gate_v2 import StaffCaptureGateV2
    from staff_capture_gate_v3 import StaffCaptureGateV3

    from devnous.copa_telmex.staff_batch_authorization import BatchCaptureAdapter
    from devnous.copa_telmex.staff_capture_runtime import assemble_capture_runtime
    from devnous.copa_telmex.staff_capture_transport import CaptureUnixClient

    case = consent_case["case"]
    admin = case["admin"]

    def pub(key):
        return key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    gate_key, bus_key, individual_key = [Ed25519PrivateKey.generate() for _ in range(3)]
    journal = DurableJSONLEventStore(
        str(tmp_path / "bus.jsonl"), v2_public_keys={"bus": pub(bus_key)}
    )

    def publish(event):
        wrapped = {
            **event,
            "type": event["event_type"],
            "execution_receipt_ref": "zaubern:registration-gate:precommit",
        }
        return journal.append_idempotent_receipt(
            wrapped,
            lambda: issue_evidence_receipt(
                wrapped, None, kid="bus", alg="Ed25519", ed25519_private_key=bus_key
            ),
        )

    gate_args = dict(
        identity_key=b"i" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        signing_kid="gate",
        signing_key=gate_key,
        receipt_public_keys={"bus": pub(bus_key)},
        policy_artifact=case["builder"]._policy,
        expected_policy_hash=case["builder"]._policy_hash,
        publisher=publish,
        max_page_bytes=4_000_000,
    )
    batch_gate = StaffCaptureGateV3(
        **gate_args,
        assertion_public_keys={"issuer": pub(consent_case["issuer_key"])},
        verifier_public_keys={"verifier": pub(consent_case["verifier_key"])},
    )
    individual_args = dict(gate_args)
    if case["mixed"]:
        individual_policy = copy.deepcopy(case["builder"]._policy)
        individual_policy["phase_binding"]["manifest_sha256"] = case["legacy"][
            "manifest"
        ]["manifest_sha256"]
        individual_policy.pop("policy_hash")
        individual_policy["policy_hash"] = "sha256:" + digest(individual_policy)
        individual_args.update(
            policy_artifact=individual_policy,
            expected_policy_hash=individual_policy["policy_hash"],
        )
    individual_gate = StaffCaptureGateV2(
        **individual_args, assertion_public_keys={"individual": pub(individual_key)}
    )
    await admin.execute(
        (
            "INSERT INTO "
            "ctt_staff_private.effect_keys(kid,secret,not_before,not_after) "
            "VALUES('effect',$1,clock_timestamp()-interval "
            "'1minute',clock_timestamp()+interval '1hour')"
        ),
        b"e" * 32,
    )
    await admin.execute(
        (
            "INSERT INTO ctt_staff_private.effect_writers(writer_role,kid) "
            "VALUES('ctt_full_writer','effect')"
        )
    )
    async with finality_route(
        case,
        batch_gate,
        tmp_path,
        dict(
            gate_public_keys={"gate": pub(gate_key)},
            receipt_public_keys={"bus": pub(bus_key)},
            assertion_public_keys={"issuer": pub(consent_case["issuer_key"])},
        ),
        rpc=rpc,
    ) as finality:
        runtime = assemble_capture_runtime(
            capture_pool=case["pools"]["writer"],
            nonce_pool=case["pools"]["workflow"],
            private_source_root=case["cas"],
            private_photo_root=tmp_path / "photos",
            gate_public_keys={"gate": pub(gate_key)},
            assertion_public_keys={"individual": pub(individual_key)},
            receipt_public_keys={"bus": pub(bus_key)},
            finality_service=individual_gate,
            database_identity="isolated-copa",
            channel_key=b"c" * 32,
            review_key=b"r" * 32,
            effect_key=b"e" * 32,
            allowed_uids=frozenset({os.getuid()}),
            max_source_bytes=4_000_000,
            max_asset_bytes=4_000_000,
            max_manifest_bytes=4_000_000,
            batch_enabled=True,
            batch_private_source_root=case["cas"],
            batch_assertion_public_keys={"issuer": pub(consent_case["issuer_key"])},
            batch_finality_service=finality,
            request_timeout=60,
        )
        writer_errors = []
        original_handler = runtime._handler

        async def diagnostic_handler(*args, **kwargs):
            try:
                return await original_handler(*args, **kwargs)
            except Exception as exc:
                while exc.__context__ is not None:
                    exc = exc.__context__
                writer_errors.append(exc)
                raise

        runtime._handler = diagnostic_handler
        socket = tmp_path / "capture.sock"
        async with runtime.serve(socket):
            client = CaptureUnixClient(
                path=socket, key=b"c" * 32, server_uid=os.getuid(), timeout=60
            )
            adapter = BatchCaptureAdapter(
                store=case["store"],
                gate=batch_gate,
                source_adapter=consent_case["adapter"],
                capture_client=client,
                public_keys={"gate": pub(gate_key)},
                issuer_public_keys={"issuer": pub(consent_case["issuer_key"])},
                review_key=b"r" * 32,
                effect_key=b"e" * 32,
            )
            if case["mixed"]:
                await mixed_capture_race(
                    consent_case,
                    adapter,
                    client,
                    individual_gate,
                    individual_key,
                    gate_key,
                    bus_key,
                    publish,
                    tmp_path,
                    writer_errors,
                )
                return
            if revoke_race:
                await revocation_commit_race(consent_case, adapter, writer_errors)
                return
            job, child = consent_case["job"], consent_case["child"]
            capture_count = 5 if manual_photo else case["team_count"]
            for number in range(capture_count):
                if rollback and number == 1:
                    await admin.execute(
                        "CREATE FUNCTION public.acceptance_fail_result() RETURNS "
                        "trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION "
                        "'acceptance forced result failure';END$$;"
                        "CREATE TRIGGER acceptance_fail_result BEFORE INSERT ON "
                        "ctt_staff_private.capture_results FOR EACH ROW "
                        "EXECUTE FUNCTION public.acceptance_fail_result()"
                    )
                if rpc and number > 0:

                    async def issue_after_source_open():
                        return await consent_case["issuer"].issue_child(
                            UUID(job["consent_id"]),
                            UUID(job["member_id"]),
                            consent_case["worker_id"],
                            job["lease_epoch"],
                        )

                    result = await adapter.prepare_and_capture(
                        job, proof_factory=issue_after_source_open
                    )
                else:
                    result = await adapter.prepare_and_capture(job, child)
                if rollback and number == 1:
                    assert result["status"] == "unknown"
                    assert len(writer_errors) == 1
                    assert "acceptance forced result failure" in str(writer_errors[0])
                    for table, expected in (
                        ("copa_telmex_teams", 1),
                        ("copa_telmex_players", 16),
                        ("copa_telmex_team_staff", 2),
                    ):
                        assert (
                            await admin.fetchval(f"SELECT count(*) FROM public.{table}")
                            == expected
                        )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM "
                            "ctt_staff_private.review_batch_consumptions"
                        )
                        == 1
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private.capture_results"
                        )
                        == 1
                    )
                    await admin.execute(
                        "DROP TRIGGER acceptance_fail_result ON "
                        "ctt_staff_private.capture_results"
                    )
                    writer_errors.clear()
                    # Exact retained proof/payload/attempt; no second human UV
                    # and no replacement identity after the uncertain result.
                    await client.execute(UUID(job["attempt_id"]), child, nonce=uuid4())
                    result = await adapter.recover(UUID(job["attempt_id"]))
                if writer_errors:
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_teams"
                        )
                        == number
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM ctt_staff_private.capture_results"
                        )
                        == number
                    )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM "
                            "ctt_staff_private.review_batch_consumptions"
                        )
                        == number
                    )
                    raise AssertionError(
                        (
                            "real canonical writer failed; "
                            "transaction rolled back without "
                            "partial roster"
                        )
                    ) from writer_errors[0]
                assert result["status"] == "committed"
                exact = await adapter.recover(UUID(job["attempt_id"]))
                assert exact == result
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM public.copa_telmex_teams"
                    )
                    == number + 1
                )
                if number < capture_count - 1:
                    job = await consent_case["worker_store"].claim(
                        consent_case["worker_id"]
                    )
                    if job["status"] == "idle" and case.get("pagination"):
                        job = await activate_real_next_block(consent_case)
                    assert job["status"] == "leased"
                    if rpc:
                        continue
                    child = await consent_case["issuer"].issue_child(
                        UUID(job["consent_id"]),
                        UUID(job["member_id"]),
                        consent_case["worker_id"],
                        job["lease_epoch"],
                    )
            if manual_photo:
                await manual_photo_after_first_block(
                    consent_case, adapter, writer_errors
                )
        assert (
            await admin.fetchval("SELECT count(*) FROM public.copa_telmex_players")
            == case["team_count"] * case["player_count"]
        )
        assert (
            await admin.fetchval("SELECT count(*) FROM public.copa_telmex_team_staff")
            == case["team_count"] * 2
        )
        assert (
            await admin.fetchval(
                (
                    "SELECT count(*) FROM public.copa_telmex_players WHERE "
                    "verified_by_human OR ocr_confidence IS NOT NULL"
                )
            )
            == 0
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM ctt_staff_private.capture_results"
            )
            == case["team_count"]
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM " "ctt_staff_private.review_batch_consumptions"
            )
            == case["team_count"]
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM ctt_staff_private.review_batch_page_consumptions"
            )
            == case["team_count"] * 2
        )
        if case.get("pagination"):
            await assert_paginated_reconciliation(consent_case)
        if case["supplied_photos"]:
            expected = case["expected_supplied_photos"]
            player = await admin.fetchrow(
                "SELECT photo_path,photo_sha256,photo_ahash FROM "
                "public.copa_telmex_players WHERE roster_index=1"
            )
            assert (
                player["photo_path"],
                player["photo_sha256"],
                player["photo_ahash"],
            ) == tuple(
                getattr(expected["player"], k)
                for k in ("photo_path", "sha256", "ahash")
            )
            staff = await admin.fetchval(
                "SELECT photo_path FROM public.copa_telmex_team_staff "
                "WHERE staff_slot=1"
            )
            assert staff == expected["staff"].photo_path
        consumed = await admin.fetchrow(
            "SELECT edition_id,pdf_sha256,pdf_page,operation_id FROM "
            "ctt_staff_private.review_batch_page_consumptions LIMIT 1"
        )
        refs = json.dumps(
            [dict(pdf_sha256=consumed["pdf_sha256"], pdf_page=consumed["pdf_page"])]
        )

        async def overlapping(operation):
            connection = await connect(case["database"])
            try:
                try:
                    return await connection.fetchval(
                        (
                            "SELECT "
                            "ctt_staff_private."
                            "batch_lock_capture_source_pages($1,$2::jsonb,$3"
                            ")"
                        ),
                        consumed["edition_id"],
                        refs,
                        operation,
                    )
                except asyncpg.ObjectNotInPrerequisiteStateError:
                    return "denied"
            finally:
                await connection.close()

        assert await asyncio.gather(
            overlapping(consumed["operation_id"]), overlapping(uuid4())
        ) == [True, "denied"]


async def activate_real_next_block(consent_case):
    """Approve the next bounded exact set with another genuine synthetic UV."""
    import secrets

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.webauthn import (
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    case = consent_case["case"]
    store, workflow = case["store"], consent_case["workflow"]
    account, run = UUID(case["context"]["account_id"]), UUID(case["context"]["run_id"])
    frozen = await store.private_run(account, run)
    chosen = [m for m in frozen["members"] if m["status"] == "ready"][:50]
    assert chosen
    count_before = await case["admin"].fetchval(
        "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
    )
    block = await workflow.prepare(
        account,
        run,
        {
            "expected_run_version": frozen["version"],
            "member_ids": [m["member_id"] for m in chosen],
            "idempotency_key": str(uuid4()),
        },
    )
    assert block["member_count"] == len(chosen) <= 50
    assert (await consent_case["worker_store"].claim(uuid4()))["status"] == "idle"
    challenge = await store.create_challenge(
        account,
        UUID(block["block_id"]),
        block["version"],
        uuid4(),
        secrets.token_bytes(32),
    )
    client = CollectedClientData.create(
        "webauthn.get",
        base64.b64decode(challenge["challenge"]),
        "https://sam.chat",
        cross_origin=False,
    )
    auth = AuthenticatorData.create(
        hashlib.sha256(b"sam.chat").digest(),
        AuthenticatorData.FLAG.UP
        | AuthenticatorData.FLAG.UV
        | AuthenticatorData.FLAG.BE
        | AuthenticatorData.FLAG.BS,
        0,
    )
    response = AuthenticationResponse(
        raw_id=case["credential"].credential_id,
        response=AuthenticatorAssertionResponse(
            client_data=client,
            authenticator_data=auth,
            signature=case["private"].sign(
                bytes(auth) + client.hash, ec.ECDSA(hashes.SHA256())
            ),
            user_handle=b"synthetic-handle",
        ),
    )
    await consent_case["issuer"].issue_consent(
        UUID(challenge["challenge_id"]),
        canonical(json.loads(json.dumps(dict(response)))),
    )
    assert (
        await case["admin"].fetchval(
            "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
        )
        == count_before + 1
    )
    return await consent_case["worker_store"].claim(consent_case["worker_id"])


async def assert_paginated_reconciliation(consent_case):
    """Actual product run pages plus >1000 genuine canonical player rows."""
    from scripts.reconcile_ctt_batch_capture import reconcile

    case = consent_case["case"]
    account, run = UUID(case["context"]["account_id"]), UUID(case["context"]["run_id"])
    pages = [
        await case["store"].view(account, run, offset, 50) for offset in (0, 50, 100)
    ]
    assert [len(p["members"]) for p in pages] == [50, 13, 0]
    members = [m for p in pages for m in p["members"]]
    assert len({m["member_id"] for m in members}) == 63
    assert all(m["status"] == "committed" for m in members)
    assert pages[0]["counts"]["committed"] == 63
    app = await connect(case["database"], "devnous_user")
    try:
        seen = []
        sizes = []
        cursor = None
        while True:
            rows = await app.fetch(
                (
                    "SELECT id,team_id,roster_index FROM "
                    "public.copa_telmex_players WHERE ($1::uuid IS NULL OR "
                    "id>$1) ORDER BY id LIMIT 257"
                ),
                cursor,
            )
            sizes.append(len(rows))
            if not rows:
                break
            seen.extend(r["id"] for r in rows)
            cursor = rows[-1]["id"]
        assert sizes == [257, 257, 257, 237, 0]
        assert len(seen) == len(set(seen)) == 1008
        expected_ids = {
            row["id"]
            for row in await case["admin"].fetch(
                "SELECT id FROM public.copa_telmex_players"
            )
        }
        assert set(seen) == expected_ids
    finally:
        await app.close()
    frozen = await case["store"].private_run(account, run)
    assert frozen["cohort_metrics"]["teams"] == {"numerator": 63, "denominator": 63}
    result_rows = await case["admin"].fetch(
        (
            "SELECT attempt_id,result_id,team_id FROM "
            "ctt_staff_private.capture_results ORDER BY attempt_id"
        )
    )
    results = [
        {k: str(row[k]) for k in ("attempt_id", "result_id", "team_id")}
        for row in result_rows
    ]
    summary = reconcile(frozen, [case["manifest"]], results)
    assert summary["consistent"] is True
    assert summary["counts"]["committed"] == 63
    assert sum(summary["counts"].values()) == 63
    assert summary["orphan_result_attempts"] == []
    assert summary["database_verified"] is False and summary["database_writes"] == 0
    assert (
        await case["admin"].fetchval(
            "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
        )
        == 3
    )
    blocks = await case["admin"].fetch(
        (
            "SELECT count(bm.member_id) n FROM "
            "ctt_staff_private.review_batch_blocks b JOIN "
            "ctt_staff_private.review_batch_block_members bm "
            "USING(block_id) GROUP BY b.block_id,b.created_at ORDER BY "
            "b.created_at"
        )
    )
    assert [b["n"] for b in blocks] == [5, 50, 8]
    assert len(results) == 63


async def manual_photo_after_first_block(consent_case, adapter, writer_errors):
    """A sixth source-bound photo proposal requires its own fresh parent UV."""
    import secrets

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.webauthn import (
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    from devnous.copa_telmex.staff_batch_source_service import analysis_identity
    from devnous.copa_telmex.staff_batch_store import BatchDenied
    from devnous.copa_telmex.staff_batch_workflow import _canonical_snapshot

    case = consent_case["case"]
    admin, store, workflow = case["admin"], case["store"], consent_case["workflow"]
    account, run = UUID(case["context"]["account_id"]), UUID(case["context"]["run_id"])

    async def existing_rows():
        return {
            table: await admin.fetchval(
                "SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id)::text "
                f"FROM public.{table} t"
            )
            for table in (
                "copa_telmex_teams",
                "copa_telmex_players",
                "copa_telmex_team_staff",
            )
        }

    before = await existing_rows()
    before_results = await admin.fetch(
        "SELECT * FROM ctt_staff_private.capture_results ORDER BY attempt_id"
    )
    frozen = await store.private_run(account, run)
    assert frozen["cohort_metrics"]["teams"] == {"numerator": 5, "denominator": 6}
    exception = next(m for m in frozen["members"] if m["status"] == "exception")
    for member in case["prepared_input"]["members"]:
        source = member["snapshot"]["source"]
        workflow.sources.cache.retain(
            analysis_identity(digest(case["profile"]), source),
            member["snapshot"]["analysis"],
        )
    details = await workflow.member(account, run, UUID(exception["member_id"]))
    photo_field = next(f for f in details["fields"] if f["field_id"] == "staff.1.photo")
    assert photo_field["kind"] == "photo" and photo_field["editable"] is True
    rectangle = photo_field["rectangle"]
    assert rectangle == dict(page_index=1, x=650, y=600, width=40, height=45)
    with pytest.raises(BatchDenied):
        await workflow.propose_corrections(
            account,
            run,
            UUID(exception["member_id"]),
            {
                "expected_version": frozen["version"],
                "edits": [{"field_id": "staff.1.role", "value": "auxiliar"}],
            },
        )
    await workflow.propose_corrections(
        account,
        run,
        UUID(exception["member_id"]),
        {
            "expected_version": frozen["version"],
            "edits": [
                {
                    "field_id": "staff.1.photo",
                    "value": {
                        "kind": "photo",
                        "presence": "PRESENT",
                        "rectangle": rectangle,
                    },
                }
            ],
        },
    )
    pending = await store.private_run(account, run)
    worker = uuid4()
    lease = await store.claim_analysis(worker)
    analyzed = await workflow.sources.analyze(pending)
    analyzed = {**pending["input_snapshot"], **analyzed}
    analyzed["members"] = [_canonical_snapshot(m) for m in analyzed["members"]]
    analyzed["input_hash"] = digest(
        {k: v for k, v in analyzed.items() if k != "input_hash"}
    )
    await store.complete_preparation(
        account, run, pending["version"], worker, lease["lease_epoch"], analyzed
    )
    prepared = await store.private_run(account, run)
    corrected = next(
        m for m in prepared["members"] if m["member_id"] == exception["member_id"]
    )
    assert corrected["status"] == "ready" and corrected["original_auto_ready"] is False
    assert prepared["cohort_metrics"] == frozen["cohort_metrics"]
    assert await existing_rows() == before
    assert (
        await admin.fetch(
            "SELECT * FROM ctt_staff_private.capture_results ORDER BY attempt_id"
        )
        == before_results
    )
    assert (await consent_case["worker_store"].claim(uuid4()))["status"] == "idle"
    # Source-replayed ready does not carry a parent or a human badge.
    review = json.loads(base64.b64decode(corrected["frozen_snapshot"]["review_bytes"]))
    assert all(p["verified_by_human"] is False for p in review["players"])
    sampled = await workflow.sample(
        account,
        run,
        {
            "expected_run_version": prepared["version"],
            "observations": [
                dict(
                    document_id=doc,
                    outcome=(
                        "matches"
                        if doc in frozen["cohort_metrics"]["automatic_document_ids"]
                        else "exception_confirmed"
                    ),
                    notes="Synthetic source and pending photo rectangle compared",
                )
                for doc in prepared["input_snapshot"]["sample_documents"]
            ],
        },
    )
    block = await workflow.prepare(
        account,
        run,
        {
            "expected_run_version": sampled["version"],
            "member_ids": [exception["member_id"]],
            "idempotency_key": str(uuid4()),
        },
    )
    assert block["member_count"] == 1
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
        )
        == 1
    )
    assert (
        await admin.fetchval("SELECT count(*) FROM ctt_staff_private.capture_results")
        == 5
    )
    challenge = await store.create_challenge(
        account,
        UUID(block["block_id"]),
        block["version"],
        uuid4(),
        secrets.token_bytes(32),
    )
    client = CollectedClientData.create(
        "webauthn.get",
        base64.b64decode(challenge["challenge"]),
        "https://sam.chat",
        cross_origin=False,
    )
    auth = AuthenticatorData.create(
        hashlib.sha256(b"sam.chat").digest(),
        AuthenticatorData.FLAG.UP
        | AuthenticatorData.FLAG.UV
        | AuthenticatorData.FLAG.BE
        | AuthenticatorData.FLAG.BS,
        0,
    )
    response = AuthenticationResponse(
        raw_id=case["credential"].credential_id,
        response=AuthenticatorAssertionResponse(
            client_data=client,
            authenticator_data=auth,
            signature=case["private"].sign(
                bytes(auth) + client.hash, ec.ECDSA(hashes.SHA256())
            ),
            user_handle=b"synthetic-handle",
        ),
    )
    proof = await consent_case["issuer"].issue_consent(
        UUID(challenge["challenge_id"]),
        canonical(json.loads(json.dumps(dict(response)))),
    )
    assert proof != consent_case["proof"]
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
        )
        == 2
    )
    job = await consent_case["worker_store"].claim(consent_case["worker_id"])
    assert job["status"] == "leased" and job["member_id"] == exception["member_id"]

    async def fresh_proof():
        return await consent_case["issuer"].issue_child(
            UUID(job["consent_id"]),
            UUID(job["member_id"]),
            consent_case["worker_id"],
            job["lease_epoch"],
        )

    result = await adapter.prepare_and_capture(job, proof_factory=fresh_proof)
    if writer_errors:
        raise AssertionError("manual photo writer failed") from writer_errors[0]
    assert result["status"] == "committed"
    expected_path = review["staff"][0]["row"]["photo_path"]
    assert expected_path is not None
    assert (
        await admin.fetchval(
            (
                "SELECT photo_path FROM public.copa_telmex_team_staff WHERE "
                "team_id=$1 AND staff_slot=1"
            ),
            UUID(result["team_id"]),
        )
        == expected_path
    )
    for table, rows in before.items():
        # Existing IDs/rows remain byte-for-byte unchanged; only the sixth team
        # and its exact player/staff rows may be appended.
        old = json.loads(rows)
        ids = [UUID(r["id"]) for r in old]
        assert (
            await admin.fetchval(
                "SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id)::text "
                f"FROM public.{table} t WHERE id=ANY($1::uuid[])",
                ids,
            )
            == rows
        )
    final = await store.private_run(account, run)
    assert final["cohort_metrics"] == frozen["cohort_metrics"]


async def postcommit_correction(consent_case, analyzed):
    ("Sixth source exception can change; five committed records stay " "exact.")
    from devnous.copa_telmex.staff_batch_source_service import analysis_identity
    from devnous.copa_telmex.staff_batch_store import BatchDenied
    from devnous.copa_telmex.staff_batch_workflow import _canonical_snapshot

    case, workflow = consent_case["case"], consent_case["workflow"]
    admin, store = case["admin"], case["store"]
    account, run = UUID(case["context"]["account_id"]), UUID(case["context"]["run_id"])
    for member in analyzed["members"]:
        workflow.sources.cache.retain(
            analysis_identity(
                case["source_context"]["profile_hash"], member["snapshot"]["source"]
            ),
            member["snapshot"]["analysis"],
        )

    async def canonical_rows():
        return {
            table: await admin.fetchval(
                "SELECT coalesce(jsonb_agg(to_jsonb(t) "
                "ORDER BY t.id),'[]'::jsonb)::text "
                f"FROM public.{table} t"
            )
            for table in (
                "copa_telmex_teams",
                "copa_telmex_players",
                "copa_telmex_team_staff",
            )
        }

    before = await canonical_rows()
    results = await admin.fetch(
        "SELECT * FROM ctt_staff_private.capture_results ORDER BY attempt_id"
    )
    frozen = await store.private_run(account, run)
    assert frozen["cohort_metrics"]["teams"] == {"numerator": 5, "denominator": 6}
    exception = next(m for m in frozen["members"] if m["status"] == "exception")
    proposed = await workflow.propose_corrections(
        account,
        run,
        UUID(exception["member_id"]),
        {
            "expected_version": frozen["version"],
            "edits": [{"field_id": "players.1.first_name", "value": "Synthetic1"}],
        },
    )
    pending = await store.private_run(account, run)
    worker = uuid4()
    lease = await store.claim_analysis(worker)
    reanalyzed = await workflow.sources.analyze(pending)
    reanalyzed = {**pending["input_snapshot"], **reanalyzed}
    reanalyzed["members"] = [_canonical_snapshot(m) for m in reanalyzed["members"]]
    reanalyzed["input_hash"] = digest(
        {k: v for k, v in reanalyzed.items() if k != "input_hash"}
    )
    tampered = copy.deepcopy(reanalyzed)
    committed = next(m for m in tampered["members"] if m["status"] == "committed")
    committed["payload_hash"] = "0" * 64
    with pytest.raises(BatchDenied):
        await store.complete_preparation(
            account, run, proposed["version"], worker, lease["lease_epoch"], tampered
        )
    await store.complete_preparation(
        account, run, proposed["version"], worker, lease["lease_epoch"], reanalyzed
    )
    after = await store.private_run(account, run)
    assert after["cohort_metrics"] == frozen["cohort_metrics"]
    assert sum(m["status"] == "committed" for m in after["members"]) == 5
    corrected = next(
        m for m in after["members"] if m["member_id"] == exception["member_id"]
    )
    assert corrected["status"] == "ready"
    assert corrected["original_auto_ready"] is False
    assert await canonical_rows() == before
    assert (
        await admin.fetch(
            "SELECT * FROM ctt_staff_private.capture_results ORDER BY attempt_id"
        )
        == results
    )
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM " "ctt_staff_private.review_batch_consumptions"
        )
        == 5
    )


@pytest.mark.parametrize(
    "mode",
    [
        "exception",
        "ready",
        "capture",
        "capture_rpc",
        "capture_supplied_photos",
        "capture_pagination",
        "capture_mixed",
        "capture_manual_photo",
        "capture_revoke_race",
        "capture_rollback",
        "capture_reprepare",
        "expiry",
    ],
)
def test_actual_source_run_analysis_lease_budget_and_exception_completion(
    full_pg, tmp_path, mode
):
    async def check():
        resources = []
        try:
            case = await operational_case(
                full_pg,
                tmp_path,
                resources,
                team_count=(
                    63
                    if mode == "capture_pagination"
                    else (
                        1
                        if mode in {"capture_supplied_photos", "capture_mixed"}
                        else (
                            6
                            if mode in {"capture_reprepare", "capture_manual_photo"}
                            else 5
                        )
                    )
                ),
                supplied_photos=mode in {"capture_supplied_photos", "capture_mixed"},
                manual_photo=mode == "capture_manual_photo",
                mixed=mode == "capture_mixed",
            )
            case["pagination"] = mode == "capture_pagination"
            case["use_issuer_rpc"] = mode in {
                "capture_rpc",
                "capture_revoke_race",
                "capture_supplied_photos",
                "capture_pagination",
                "capture_manual_photo",
            }
            admin, store, context = case["admin"], case["store"], case["context"]
            account, run = UUID(context["account_id"]), UUID(context["run_id"])
            created = await store.create_run(account, run, case["initial"])
            assert created["status"] == "analysis_pending"
            assert await store.create_run(account, run, case["initial"]) == created
            worker, other = uuid4(), uuid4()
            lease = await store.claim_analysis(worker)
            assert lease["status"] == "leased"
            assert (await store.claim_analysis(other))["status"] == "busy"
            await store.analysis_heartbeat(run, worker, lease["lease_epoch"])
            verifier = PostgresBatchReviewStore(
                case["pools"]["verifier"], backend_pin="acceptance"
            )
            reservation = uuid4()
            reserved = await verifier.budget_reserve(run, reservation, 80)
            assert reserved["maximum_units"] == 80
            retry = await verifier.budget_reserve(run, reservation, 80)
            assert retry["reservation_id"] == reserved["reservation_id"]
            assert retry["maximum_units"] == reserved["maximum_units"]
            from devnous.copa_telmex.staff_batch_store import BatchDenied

            with pytest.raises(BatchDenied):
                await verifier.budget_reserve(run, uuid4(), 21)
            # An ambiguous provider timeout leaves its original reservation held.
            assert (
                await admin.fetchval(
                    (
                        "SELECT actual_units FROM "
                        "ctt_staff_private.review_batch_budget_reservations WHERE "
                        "reservation_id=$1"
                    ),
                    reservation,
                )
                is None
            )
            await verifier.budget_reconcile(reservation, 60, "9" * 64)
            with pytest.raises(BatchDenied):
                await store.analysis_heartbeat(run, other, lease["lease_epoch"])
            analyzed = copy.deepcopy(case["initial"])
            if mode == "exception":
                for member in analyzed["members"]:
                    member.update(
                        status="exception",
                        issues=[{"code": "READER_DISAGREEMENT"}],
                        snapshot={"analysis": {"auto_ready": False}},
                    )
            else:
                from devnous.copa_telmex.staff_batch_source_adapter import (
                    PrivateBatchSourceProvider,
                    SourceCheckAdapterV2,
                )
                from devnous.copa_telmex.staff_batch_source_service import (
                    BatchCatalogSourceProvider,
                )

                adapter = SourceCheckAdapterV2(
                    PrivateBatchSourceProvider(case["cas"]),
                    max_source_bytes=4_000_000,
                    max_asset_bytes=4_000_000,
                    max_manifest_bytes=4_000_000,
                )
                analyzed["members"] = []
                for number, source in enumerate(case["source_context"]["documents"]):
                    ctx = {
                        **case["source_context"],
                        **context,
                        "capability": case["source_context"]["capabilities"][0],
                    }
                    source_index = (source["source_pages"][0]["pdf_page"] - 1) // 2
                    observation = copy.deepcopy(case["observations"][source_index])
                    observation["document_id"] = source["document_id"]
                    observation["source_physical_slots"] = [
                        s["slot_ref"] for s in case["profile"]["physical_slots"]
                    ]
                    observation["source_binding_hash"] = digest(source["source_pages"])
                    observation["verification_input"]["source_binding_hash"] = (
                        observation["source_binding_hash"]
                    )
                    observation["verification_input"]["proposed_fields"][
                        "team.name"
                    ] = observation["team"]["name"]
                    for reading in observation["verification_input"]["readings"]:
                        reading["fields"]["team.name"] = observation["team"]["name"]
                    with adapter.open_verified_snapshot(
                        BatchCatalogSourceProvider._source_scope(ctx, source)
                    ) as snapshot:
                        from devnous.copa_telmex.staff_batch_observation_inputs import (
                            derive_input_hashes,
                        )

                        if case["supplied_photos"]:
                            bind_supplied_photos(
                                case, observation, snapshot, source, tmp_path
                            )
                        assert set(
                            observation["verification_input"]["expected_fields"]
                        ).issubset(
                            case["profile"]["observation_views"][0]["fields"]
                        ), sorted(
                            set(observation["verification_input"]["expected_fields"])
                            - set(case["profile"]["observation_views"][0]["fields"])
                        )
                        derived = derive_input_hashes(
                            snapshot,
                            source["source_pages"],
                            case["profile"],
                            observation["verification_input"]["expected_fields"],
                        )
                        for reading, actual in zip(
                            observation["verification_input"]["readings"], derived
                        ):
                            reading.update(actual)
                        observation["readings"] = copy.deepcopy(
                            observation["verification_input"]["readings"]
                        )
                        if case["supplied_photos"]:
                            supplied_photo_negative_cases(
                                case, observation, snapshot, source
                            )
                        member = case["builder"].build_batch(
                            ctx, source, observation, snapshot
                        )
                        if case["supplied_photos"]:
                            evidence = json.loads(
                                base64.b64decode(member["snapshot"]["evidence_bytes"])
                            )
                            player_evidence = next(
                                m
                                for m in evidence["members"]
                                if m["kind"] == "PLAYER" and m["slot"] == 1
                            )
                            staff_evidence = next(
                                m
                                for m in evidence["members"]
                                if m["kind"] == "STAFF" and m["slot"] == 1
                            )
                            assert (
                                player_evidence["photo_evidence"]["mode"]
                                == "BATCH_PLAYER_SLOT"
                            )
                            assert (
                                staff_evidence["photo_evidence"]["mode"]
                                == "SOURCE_RECTANGLE"
                            )
                            assert (
                                "photo" not in player_evidence["fields"]
                                and "photo" not in staff_evidence["fields"]
                            )
                        snapshot.verify_unchanged()
                    member["snapshot"]["raw_snapshot_bytes"] = base64.b64encode(
                        canonical(member["snapshot"]["raw_snapshot"])
                    ).decode()
                    member["snapshot"]["profile_hash"] = digest(case["profile"])
                    member["snapshot"]["analysis"] = observation
                    member["snapshot"]["source"] = source
                    member["source_slices"] = case["manifest"]["documents"][
                        source_index
                    ]["source_slices"]
                    member["template_id"] = case["profile"]["template_id"]
                    member["category"] = "UNICA"
                    if (
                        mode in {"capture_reprepare", "capture_manual_photo"}
                        and source_index == 5
                    ):
                        from devnous.copa_telmex.staff_batch_corrections import (
                            editable_fields,
                        )

                        observation["auto_ready"] = False
                        observation["issues"] = [{"code": "READER_DISAGREEMENT"}]
                        observation["players"][0]["first_name"] = ""
                        observation["verification_input"]["proposed_fields"][
                            "players.1.first_name"
                        ] = ""
                        observation["verification_input"]["readings"][1]["fields"][
                            "players.1.first_name"
                        ] = ""
                        member.update(
                            status="exception",
                            auto_ready=False,
                            issues=observation["issues"],
                            snapshot={
                                "source": source,
                                "analysis": observation,
                                "editable_fields": editable_fields(
                                    observation,
                                    profile=case["profile"],
                                    source_pages=source["source_pages"],
                                ),
                            },
                            payload_hash=None,
                            scope_hash=None,
                            attempt_id=None,
                        )
                    if mode == "capture_manual_photo" and source_index == 5:
                        observation["players"][0]["first_name"] = case["observations"][
                            source_index
                        ]["players"][0]["first_name"]
                        observation["verification_input"]["proposed_fields"][
                            "players.1.first_name"
                        ] = observation["players"][0]["first_name"]
                        for reading in observation["verification_input"]["readings"]:
                            reading["fields"]["players.1.first_name"] = observation[
                                "players"
                            ][0]["first_name"]
                        observation["issues"] = [{"code": "PHOTO_UNCERTAIN"}]
                        member["issues"] = observation["issues"]
                        member["snapshot"]["analysis"] = observation
                        member["snapshot"]["editable_fields"] = editable_fields(
                            observation,
                            profile=case["profile"],
                            source_pages=source["source_pages"],
                        )
                    analyzed["members"].append(member)
            if case["supplied_photos"]:
                from devnous.copa_telmex.staff_batch_source_service import (
                    BatchCatalogSourceProvider,
                    PrivateBatchAnalysisCache,
                    analysis_identity,
                )
                from devnous.copa_telmex.staff_batch_workflow import _canonical_snapshot

                cache = PrivateBatchAnalysisCache(case["cas"])
                for member in analyzed["members"]:
                    source = member["snapshot"]["source"]
                    cache.retain(
                        analysis_identity(digest(case["profile"]), source),
                        member["snapshot"]["analysis"],
                    )
                provider = BatchCatalogSourceProvider(
                    store=store,
                    source_adapter=adapter,
                    profiles={digest(case["profile"]): case["profile"]},
                    builders={case["context"]["policy_hash"]: case["builder"]},
                    analysis_cache=cache,
                )
                retained_run = await store.private_run(account, run)
                native = await provider.analyze(retained_run)
                analyzed = {**case["initial"], **native}
                analyzed["members"] = [
                    _canonical_snapshot(m) for m in analyzed["members"]
                ]
            if mode != "exception":
                from datetime import datetime, timezone

                from devnous.tournaments.core.ctt_batch_qualification import (
                    calculate_metrics,
                    select_sample,
                )

                analyzed["documents"] = [
                    {
                        k: m[k]
                        for k in (
                            "document_id",
                            "source_slices",
                            "template_id",
                            "category",
                        )
                    }
                    for m in analyzed["members"]
                ]
                analyzed["metrics"] = calculate_metrics(
                    analyzed["members"],
                    source_pages=[
                        dict(pdf_sha256=p["pdf_sha256"], pdf_page=p["pdf_page"])
                        for m in analyzed["members"]
                        for p in m["source_pages"]
                    ],
                    cutoff=datetime.now(timezone.utc).isoformat(),
                    profile_hash=case["source_context"]["profile_hash"],
                    corpus_hash=analyzed["corpus_hash"],
                    input_versions={},
                )
                analyzed["sample_documents"] = list(
                    select_sample(
                        analyzed["documents"], corpus_hash=analyzed["corpus_hash"]
                    )
                )
                assert (
                    analyzed["sample_documents"] == case["initial"]["sample_documents"]
                )
            analyzed["input_hash"] = digest(
                {k: v for k, v in analyzed.items() if k != "input_hash"}
            )
            result = await store.complete_preparation(
                account, run, 1, worker, lease["lease_epoch"], analyzed
            )
            assert result["status"] == "prepared"
            view = await store.view(account, run)
            assert len(view["members"]) == min(50, case["team_count"])
            assert view["counts"]["ready"] == (
                0
                if mode == "exception"
                else case["team_count"]
                - (1 if mode in {"capture_reprepare", "capture_manual_photo"} else 0)
            )
            assert await admin.fetchval(
                "SELECT count(*) FROM public.copa_telmex_registration_review_drafts"
            ) == (
                0
                if mode == "exception"
                else case["team_count"]
                - (1 if mode in {"capture_reprepare", "capture_manual_photo"} else 0)
            ) + int(
                case["mixed"]
            )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_consents"
                )
                == 0
            )
            case["prepared_input"] = analyzed
            if mode != "exception":
                if mode == "ready":
                    forged = json.loads(
                        await admin.fetchval(
                            "SELECT to_jsonb(d)::text FROM "
                            "public.copa_telmex_registration_review_drafts d LIMIT 1"
                        )
                    )
                    forged["predecessor_draft_id"] = forged["id"]
                    forged["predecessor_content_hash"] = forged["content_hash"]
                    forged["draft_version"] += 1
                    forged["id"] = str(uuid4())
                    forged["mutation_operation_id"] = (
                        "batch:" + str(uuid4()) + ":" + str(uuid4())
                    )
                    public = await connect(case["database"], "devnous_user")
                    try:
                        await public.execute("SET ctt.batch_preparation = 'true'")
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await public.execute(
                                "INSERT INTO "
                                "public.copa_telmex_registration_review_drafts "
                                "SELECT (jsonb_populate_record(NULL::public."
                                "copa_telmex_registration_review_drafts,$1::jsonb)).*",
                                json.dumps(forged),
                            )
                    finally:
                        await public.close()
                consent_case = await qualify_and_consent(case, analyzed)
                if mode.startswith("capture"):
                    await canonical_captures(
                        consent_case,
                        tmp_path,
                        rpc=mode
                        in {
                            "capture_rpc",
                            "capture_revoke_race",
                            "capture_supplied_photos",
                            "capture_pagination",
                            "capture_manual_photo",
                        },
                        revoke_race=mode == "capture_revoke_race",
                        manual_photo=mode == "capture_manual_photo",
                        rollback=mode == "capture_rollback",
                    )
                    if mode == "capture_reprepare":
                        await postcommit_correction(consent_case, analyzed)
                    return
                from devnous.copa_telmex.staff_batch_authorization import (
                    BatchAuthorizationDenied,
                )

                if mode == "expiry":
                    job = consent_case["job"]
                    await admin.execute(
                        "UPDATE ctt_staff_private.review_batch_capabilities SET "
                        "valid_until=clock_timestamp()-interval '1 second'"
                    )
                    with pytest.raises(BatchAuthorizationDenied):
                        await consent_case["issuer"].issue_child(
                            UUID(job["consent_id"]),
                            UUID(job["member_id"]),
                            consent_case["worker_id"],
                            job["lease_epoch"],
                        )
                    body = json.loads(consent_case["child"])["body"]
                    async with case["pools"]["writer"].acquire() as writer:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await writer.fetchval(
                                "SELECT ctt_staff_private."
                                "batch_lock_child($1,$2,$3,$4)",
                                UUID(job["attempt_id"]),
                                UUID(body["authorization_id"]),
                                consent_case["worker_id"],
                                job["lease_epoch"],
                            )
                    assert (
                        await admin.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_teams"
                        )
                        == 0
                    )
                    return

                block_id = UUID(consent_case["block"]["block_id"])
                current = await store.private_block(account, block_id)
                paused = await store.transition(
                    account, block_id, current["version"], "pause"
                )
                with pytest.raises(BatchAuthorizationDenied):
                    job = consent_case["job"]
                    await consent_case["issuer"].issue_child(
                        UUID(job["consent_id"]),
                        UUID(job["member_id"]),
                        consent_case["worker_id"],
                        job["lease_epoch"],
                    )
                resumed = await store.transition(
                    account, block_id, paused["version"], "resume"
                )
                slot = await admin.fetchrow(
                    (
                        "SELECT lease_epoch FROM "
                        "ctt_staff_private.review_batch_dispatch_slot WHERE singleton"
                    )
                )
                renewed_worker = uuid4()
                recovered = await consent_case["worker_store"].recover_job(
                    renewed_worker, UUID(job["attempt_id"]), slot["lease_epoch"]
                )
                assert recovered["status"] == "leased"
                assert recovered["attempt_id"] == job["attempt_id"]
                renewed = await consent_case["issuer"].issue_child(
                    UUID(job["consent_id"]),
                    UUID(job["member_id"]),
                    renewed_worker,
                    recovered["lease_epoch"],
                )
                assert renewed != consent_case["child"]
                with pytest.raises(BatchAuthorizationDenied):
                    await consent_case["issuer"].issue_child(
                        UUID(job["consent_id"]),
                        UUID(job["member_id"]),
                        consent_case["worker_id"],
                        job["lease_epoch"],
                    )
                revoked = await store.transition(
                    account, block_id, resumed["version"], "revoke"
                )
                with pytest.raises(BatchDenied):
                    await store.transition(
                        account, block_id, revoked["version"], "resume"
                    )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())


async def seed_same_legacy_source(
    admin, pools, cas, original_root, manifest, observation, edition
):
    """Admit an authentic V1 source referencing the identical V2 physical pages."""
    from dataclasses import replace

    from devnous.copa_telmex.batch_admission import sha256_value
    from devnous.copa_telmex.staff_capture_edition import TrustedEditionBinding
    from devnous.copa_telmex.staff_source_admission import (
        admit_prepared_sources,
        prepare_source_admission,
        sign_source_admission,
    )

    spec = importlib.util.spec_from_file_location(
        "acceptance_legacy_source_fixture",
        ROOT / "tests/unit/test_ctt_staff_capture_source_check.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    legacy_manifest, original_scope, _, _ = fixture.sample()
    doc_id = digest(
        {"legacy_independent_source": manifest["documents"][0]["document_id"]}
    )
    extraction = copy.deepcopy(observation)
    extraction = {k: extraction[k] for k in ("team", "players", "staff")}
    for number, player in enumerate(extraction["players"], 1):
        player["slot"] = number
        player["name"] = player["first_name"] + " " + player["last_name"]
        player["slot_ref"] = (
            f'page:{player["source_page_index"]}:jugador_{player["slot"]}'
        )
    for number, (person, role) in enumerate(
        zip(extraction["staff"], ("director_tecnico", "auxiliar")), 1
    ):
        person["slot"] = number
        person.update(
            role=role,
            slot_ref=f'page:{person["source_page_index"]}:{role}',
            presence="present",
            evidence={},
        )
        person["staff_entry_id"] = sha256_value(
            {k: person[k] for k in ("role", "slot", "slot_ref", "evidence")}
        )
    legacy_manifest["edition"] = {
        k: edition[k] for k in ("tournament_id", "edition_year", "roster_slug")
    }
    legacy_manifest["documents"] = [
        dict(
            document_id=doc_id,
            pdf="consolidated.pdf",
            pdf_sha256=manifest["pdfs"][0]["sha256"],
            pages=[1, 2],
            extraction=extraction,
        )
    ]
    legacy_manifest.pop("manifest_sha256", None)
    binding = TrustedEditionBinding(
        edition["tenant_id"],
        UUID(edition["edition_id"]),
        UUID(edition["tournament_id"]),
        2026,
        edition["roster_slug"],
    )
    key = Ed25519PrivateKey.generate()
    limits = SourceAdmissionLimits(
        4_000_000, 4_000_000, 16_000_000, 4_000_000, 16_000_000
    )
    with prepare_source_admission(
        canonical(legacy_manifest), original_root, binding, limits
    ) as prepared:
        proof = sign_source_admission(
            prepared.plan_bytes,
            admission_id=uuid4(),
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 300,
            kid="legacy-source",
            private_key=key,
        )
        await admit_prepared_sources(
            prepared,
            proof,
            public_keys={
                "legacy-source": key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            },
            private_root=cas,
            pool=pools["verifier"],
        )
        plan = json.loads(prepared.plan_bytes)
        normalized = json.loads(prepared.manifest_bytes)
    sid = await admin.fetchval(
        "SELECT ctt_staff_private.review_document_session($1,$2)",
        UUID(edition["edition_id"]),
        doc_id,
    )
    pages = await admin.fetch(
        (
            "SELECT * FROM ctt_staff_private.review_source_pages_v2 "
            "WHERE document_id=$1 ORDER BY page_index"
        ),
        manifest["documents"][0]["document_id"],
    )
    scope = replace(
        original_scope,
        tenant_id=edition["tenant_id"],
        edition_id=UUID(edition["edition_id"]),
        tournament_slug=edition["roster_slug"],
        session_id=sid,
        draft_id=uuid4(),
        document_id=doc_id,
        manifest_sha256=plan["manifest_sha256"],
        pdf_sha256=manifest["pdfs"][0]["sha256"],
        source_pages=tuple(
            replace(
                old,
                asset_id=uuid4(),
                image_sha256="sha256:" + row["image_sha256"],
                pdf_page=row["pdf_page"],
            )
            for old, row in zip(original_scope.source_pages, pages)
        ),
    )
    from devnous.copa_telmex.draft_versioning import draft_content_hash

    raw_snapshot = dict(
        ocr_raw=None,
        extraction=extraction,
        validation={
            "incident_policy": {
                "player_results": [
                    dict(player_index=i, eligibility_status="CLEARED", incidents=[])
                    for i in range(1, 17)
                ]
            }
        },
        review_edits={},
        layout_regions={},
        overall_confidence=1.0,
        needs_review=False,
    )
    content_hash = draft_content_hash(raw_snapshot)
    await admin.execute(
        (
            "INSERT INTO public.copa_telmex_registration_review_sessions("
            "id,status,source,provider,tournament_slug,started_at) "
            "VALUES($1,'reviewing','synthetic','synthetic',$2,clock_times"
            "tamp())"
        ),
        sid,
        scope.tournament_slug,
    )
    await admin.execute(
        (
            "INSERT INTO public.copa_telmex_registration_review_drafts(id"
            ",session_id,extraction,validation,review_edits,layout_region"
            "s,overall_confidence,needs_review,draft_version,content_hash"
            ",mutation_type,mutation_operation_id,mutation_decision_id,mu"
            "tation_receipt_id,created_at) "
            "VALUES($1,$2,$3::json,$4::json,'{}','{}',1,false,1,$5,'synth"
            "etic_initial','synthetic-initial-full',$6,$6,clock_timestamp"
            "())"
        ),
        scope.draft_id,
        sid,
        json.dumps(extraction),
        json.dumps(raw_snapshot["validation"]),
        content_hash,
        "sha256:" + "b" * 64,
    )
    for page in pages:
        await admin.execute(
            (
                "INSERT INTO public.copa_telmex_registration_review_assets(id"
                ",session_id,page_index,image_path,sha256,width,height,create"
                "d_at) VALUES($1,$2,$3,$4,$5,$6,$7,clock_timestamp())"
            ),
            scope.source_pages[page["page_index"] - 1].asset_id,
            sid,
            page["page_index"],
            str(cas / "pages" / (page["image_sha256"] + ".png")),
            page["image_sha256"],
            page["width"],
            page["height"],
        )
    return dict(manifest=normalized, scope=scope, session_id=sid)


async def mixed_capture_race(
    consent_case,
    adapter,
    capture_client,
    old_gate,
    individual_key,
    gate_key,
    bus_key,
    publish,
    tmp_path,
    writer_errors,
):
    """Both UV-authorized domains compete on the very same immutable original."""
    from unittest.mock import patch

    import httpx
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.utils import websafe_decode
    from fido2.webauthn import (
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )
    from staff_capture_gate_v2 import StaffCaptureGateV2

    from devnous.copa_telmex.staff_capture_postgres import _CaptureTransaction
    from devnous.copa_telmex.staff_review_issuer_client import ReviewIssuerClient
    from devnous.copa_telmex.staff_review_runtime import (
        assemble_review_issuer,
        assemble_review_workflow,
    )

    case = consent_case["case"]
    admin = case["admin"]
    sid = case["legacy"]["session_id"]
    account = UUID(case["context"]["account_id"])
    actor = UUID(case["context"]["principal_id"])
    edition = UUID(case["context"]["edition_id"])
    await admin.execute(
        (
            "INSERT INTO ctt_staff_private.review_faculties(principal_id,"
            "tenant_id,edition_id,faculty,valid_from,valid_until) "
            "VALUES($1,'synthetic',$2,'capture_staff_v2',clock_timestamp("
            ")-interval '1minute',clock_timestamp()+interval '1hour')"
        ),
        actor,
        edition,
    )
    await admin.execute("CREATE ROLE ctt_mixed_proposals LOGIN NOINHERIT")
    await admin.execute(
        "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_mixed_proposals"
    )
    await admin.execute(
        (
            "INSERT INTO ctt_staff_private.review_services(service_role,b"
            "ackend_pin,not_before,not_after,active) "
            "VALUES('ctt_mixed_proposals','acceptance',clock_timestamp()-"
            "interval '1minute',clock_timestamp()+interval '1hour',true)"
        )
    )
    names = [
        "resolve_review_source_scope",
        "load_review_proposal_context",
        "register_review_proposal",
        "read_review_proposal",
        "create_proposal_challenge",
        "review_proposal_status",
        "project_review_capture_rows",
        "load_review_finality_gate_context",
        "register_proposal_capture_payload",
    ]
    for row in await admin.fetch(
        (
            "SELECT oid::regprocedure::text signature FROM pg_proc WHERE "
            "pronamespace='ctt_staff_private'::regnamespace AND "
            "proname=ANY($1::text[])"
        ),
        names,
    ):
        await admin.execute(
            "GRANT EXECUTE ON FUNCTION " + row["signature"] + " TO ctt_mixed_proposals"
        )
    await admin.execute(
        (
            "GRANT EXECUTE ON FUNCTION "
            "ctt_staff_private.record_assertion_proof(uuid,bytea) TO "
            "ctt_full_issuer"
        )
    )
    pool = await asyncpg.create_pool(
        host=case["database"][0],
        port=int(PORT),
        database=case["database"][1],
        user="ctt_mixed_proposals",
        min_size=1,
        max_size=2,
    )
    case["resources"].append(pool)
    enrollment_pool = await asyncpg.create_pool(
        host=case["database"][0],
        port=int(PORT),
        database=case["database"][1],
        user="ctt_mixed_proposals",
        min_size=1,
        max_size=2,
    )
    case["resources"].append(enrollment_pool)
    policy = copy.deepcopy(case["builder"]._policy)
    policy["phase_binding"]["manifest_sha256"] = case["legacy"]["manifest"][
        "manifest_sha256"
    ]
    policy.pop("policy_hash")
    policy["policy_hash"] = "sha256:" + digest(policy)

    def pub(key):
        return key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    gate = StaffCaptureGateV2(
        identity_key=b"i" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        signing_kid="gate",
        signing_key=gate_key,
        assertion_public_keys={"individual": pub(individual_key)},
        receipt_public_keys={"bus": pub(bus_key)},
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        publisher=publish,
        max_page_bytes=4_000_000,
    )
    issuer_app = assemble_review_issuer(
        issuer_pool=case["pools"]["issuer"],
        nonce_pool=case["pools"]["workflow"],
        signing_kid="individual",
        signing_key=individual_key,
        channel_key=b"h" * 32,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=issuer_app),
        base_url="http://127.0.0.1:9101",
        trust_env=False,
    ) as http:
        issuer = ReviewIssuerClient(
            url="http://127.0.0.1:9101", channel_key=b"h" * 32, http_client=http
        )
        v2_ready, v2_release = asyncio.Event(), asyncio.Event()
        import os

        from devnous.copa_telmex.staff_capture_transport import CaptureUnixClient

        class HeldCapture(CaptureUnixClient):
            async def execute(self, *args, **kwargs):
                v2_ready.set()
                await asyncio.wait_for(v2_release.wait(), 30)
                return await capture_client.execute(*args, **kwargs)

        workflow = assemble_review_workflow(
            proposal_pool=pool,
            enrollment_pool=enrollment_pool,
            backend_pin="acceptance",
            issuer_client=issuer,
            business_gate=gate,
            capture_client=HeldCapture(
                path=tmp_path / "capture.sock",
                key=b"c" * 32,
                server_uid=os.getuid(),
                timeout=60,
            ),
            private_source_root=case["cas"],
            private_photo_root=tmp_path / "photos",
            review_key=b"r" * 32,
            effect_key=b"e" * 32,
            gate_public_keys={"gate": pub(gate_key)},
            policy_artifact=policy,
            expected_policy_hash=policy["policy_hash"],
            max_source_bytes=4_000_000,
            max_asset_bytes=4_000_000,
            max_manifest_bytes=4_000_000,
        )
        view = await workflow.prepare(
            account, sid, None, {"confirm_present_staff": True}
        )
        assert view["ready_to_approve"] is True, view
        proposal = UUID(view["proposal_id"])
        challenge = await workflow.challenge(account, sid, proposal, True)
        client = CollectedClientData.create(
            "webauthn.get",
            websafe_decode(challenge["options"]["publicKey"]["challenge"]),
            "https://sam.chat",
            cross_origin=False,
        )
        auth = AuthenticatorData.create(
            hashlib.sha256(b"sam.chat").digest(),
            AuthenticatorData.FLAG.UP
            | AuthenticatorData.FLAG.UV
            | AuthenticatorData.FLAG.BE
            | AuthenticatorData.FLAG.BS,
            0,
        )
        assertion = AuthenticationResponse(
            raw_id=case["credential"].credential_id,
            response=AuthenticatorAssertionResponse(
                client_data=client,
                authenticator_data=auth,
                signature=case["private"].sign(
                    bytes(auth) + client.hash, ec.ECDSA(hashes.SHA256())
                ),
                user_handle=b"synthetic-handle",
            ),
        )
        attempt = UUID(challenge["attempt_id"])
        stored_proposal = await workflow._store.read_proposal(account, sid, proposal)
        v2_scope = json.loads(stored_proposal["scope_bytes"])
        v3_scope = json.loads(
            base64.b64decode(
                case["prepared_input"]["members"][0]["snapshot"]["scope_bytes"]
            )
        )
        assert v2_scope["session_id"] != v3_scope["session_id"]
        assert v2_scope["draft_id"] != v3_scope["draft_id"]
        assert v2_scope["team_id"] != v3_scope["team_id"]
        assert v2_scope["document_id"] != v3_scope["document_id"]
        assert v2_scope["edition_id"] == v3_scope["edition_id"]
        assert v2_scope["pdf_sha256"] == v3_scope["source_pages"][0]["pdf_sha256"]
        assert (
            [p["pdf_page"] for p in v2_scope["source_pages"]]
            == [p["pdf_page"] for p in v3_scope["source_pages"]]
            == [1, 2]
        )
        v2_task = asyncio.create_task(
            workflow.assert_and_capture(account, sid, attempt, dict(assertion))
        )
        v3_task = None
        entered, release = asyncio.Event(), asyncio.Event()
        original = _CaptureTransaction.capture_batch
        writer_pid = None

        async def hold(tx, *args, **kwargs):
            nonlocal writer_pid
            result = await original(tx, *args, **kwargs)
            writer_pid = tx._conn.get_server_pid()
            entered.set()
            await asyncio.wait_for(release.wait(), 30)
            return result

        try:
            await asyncio.wait_for(v2_ready.wait(), 15)
            # Both current signed attempts exist before either writer runs.
            admitted = await workflow._store.status(account, sid, attempt)
            assert admitted["is_current"] is True
            assert admitted["payload_admitted"] is True
            assert type(admitted["signed_review"]) is bytes
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_results"
                )
                == 0
            )
            with patch.object(_CaptureTransaction, "capture_batch", hold):
                v3_task = asyncio.create_task(
                    adapter.prepare_and_capture(
                        consent_case["job"], consent_case["child"]
                    )
                )
                await asyncio.wait_for(entered.wait(), 15)
                v2_release.set()
                deadline = time.monotonic() + 5
                blocked = None
                while time.monotonic() < deadline:
                    blocked = await admin.fetchrow(
                        (
                            "SELECT pid,pg_blocking_pids(pid) blockers FROM "
                            "pg_stat_activity WHERE datname=current_database() AND "
                            "usename='ctt_full_writer' AND pid<>$1 AND "
                            "wait_event_type='Lock'"
                        ),
                        writer_pid,
                    )
                    if blocked and writer_pid in blocked["blockers"]:
                        break
                    if v2_task.done():
                        break
                    await asyncio.sleep(0.01)
                assert (
                    blocked and writer_pid in blocked["blockers"]
                ), "individual writer did not compete for the held batch identity"
                release.set()
                result = await asyncio.wait_for(v3_task, 20)
                assert result["status"] == "committed"
                v2_result = await asyncio.wait_for(v2_task, 20)
                assert v2_result["state"] in {"uncertain", "stale", "committed"}
                recovered = await workflow.status(account, sid, attempt)
                assert recovered["state"] == "uncertain"
                assert len(writer_errors) == 1, writer_errors
                assert str(writer_errors[0]) == "CTT source page already captured"
                assert writer_errors[0].sqlstate == "55000"
                retry = await workflow.assert_and_capture(
                    account, sid, attempt, dict(assertion)
                )
                assert retry["state"] == "uncertain"
                assert len(writer_errors) == 2
                assert str(writer_errors[1]) == "CTT source page already captured"
                assert writer_errors[1].sqlstate == "55000"
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_results"
                )
                == 1
            )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 1
            )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_players")
                == case["player_count"]
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM public.copa_telmex_team_staff"
                )
                == 2
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_consumptions"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.capture_results "
                        "WHERE attempt_id=$1"
                    ),
                    attempt,
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "ctt_staff_private.review_challenge_consumptions c JOIN "
                        "ctt_staff_private.capture_attempt_anchors a "
                        "USING(challenge_id) WHERE a.attempt_id=$1"
                    ),
                    attempt,
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM public.copa_telmex_teams WHERE id=$1",
                    UUID(v2_scope["team_id"]),
                )
                == 0
            )
            for table in ("copa_telmex_players", "copa_telmex_team_staff"):
                assert (
                    await admin.fetchval(
                        f"SELECT count(*) FROM public.{table} WHERE team_id=$1",
                        UUID(v2_scope["team_id"]),
                    )
                    == 0
                )
        finally:
            release.set()
            v2_release.set()
            for task in (v2_task, v3_task):
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
