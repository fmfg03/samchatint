"""Private offline source preparation accounts for every original physical page."""

import copy
import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
import scripts.prepare_ctt_batch_sources as cli

from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    digest,
    source_refs,
    stable_document_id,
    validate_source_manifest,
)
from devnous.tournaments.core.ctt_batch_qualification import calculate_metrics


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    pdfs = []
    originals = {}
    for index, texts in enumerate(
        (
            ("unclassified original source", "synthetic team front slots1-8"),
            ("synthetic team back slots9-20",),
        ),
        1,
    ):
        document = fitz.open()
        for text in texts:
            page = document.new_page(width=612, height=792)
            page.insert_text((30, 50), text)
        raw = document.tobytes()
        document.close()
        path = root / f"Oaxaca Varonil 103 PT{index}.pdf"
        path.write_bytes(raw)
        path.chmod(0o644)
        originals[path] = (raw, path.stat().st_mode, path.stat().st_mtime_ns)
        pdfs.append(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "page_count": len(texts),
            }
        )
    profile = {
        "template_ids": ["letter-20"],
        "source_groups": {"state-parts": [pdf["sha256"] for pdf in pdfs]},
        "allowed_page_patterns": {
            "front": list(range(1, 9)),
            "back": list(range(9, 21)),
        },
    }
    observations = []
    for pdf, number, side in (
        (pdfs[0], 1, "unknown"),
        (pdfs[0], 2, "front"),
        (pdfs[1], 1, "back"),
    ):
        observations.append(
            {
                "pdf_sha256": pdf["sha256"],
                "pdf_page": number,
                "side": side,
                "template_id": "letter-20",
                "category": "varonil",
                "source_group": "state-parts",
                "team_key": "synthetic-team",
                "slots": profile["allowed_page_patterns"].get(side, []),
            }
        )
    edition = {
        "tenant_id": "synthetic",
        "edition_id": str(uuid4()),
        "tournament_id": str(uuid4()),
        "edition_year": 2026,
        "roster_slug": "ctt-synthetic",
        "phase": "national",
    }
    return SimpleNamespace(
        root=root,
        output=tmp_path / "prepared",
        edition=edition,
        observations=observations,
        profile=profile,
        pdfs=pdfs,
        originals=originals,
        limit=sum(len(v[0]) for v in originals.values()),
    )


def prepare(fixture, **changes):
    args = {
        "pdf_root": fixture.root,
        "output_root": fixture.output,
        "edition": fixture.edition,
        "observations": fixture.observations,
        "profile": fixture.profile,
        "max_total_bytes": fixture.limit,
    }
    return cli.prepare(**{**args, **changes})


def assert_originals_unchanged(fixture):
    for path, (raw, mode, mtime) in fixture.originals.items():
        assert path.read_bytes() == raw
        assert path.stat().st_mode == mode
        assert path.stat().st_mtime_ns == mtime


def test_real_offline_preparation_preserves_split_sources_and_all_pages(
    retained, monkeypatch
):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("offline source preparation attempted network access")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    report = prepare(retained)
    assert report["physical_pages"] == 3
    assert (
        report["documents"] == 1
    )  # A filename containing103 never establishes a team count.
    assert report["inventory"] == retained.pdfs
    assert report["observations_sha256"] == digest(retained.observations)
    assert report["profile_sha256"] == digest(retained.profile)
    assert (
        report["database_writes"]
        == report["provider_calls"]
        == report["teams_created"]
        == 0
    )
    assert report["requires_independent_source_admission"] is True
    classifications = report["segmentation"]["page_classifications"]
    assert len(classifications) == 3
    assert sum(p["classification"] == "document" for p in classifications) == 2
    assert sum(p["classification"] == "exception" for p in classifications) == 1
    entry = report["manifests"][0]
    manifest = validate_source_manifest(
        json.loads((retained.output / entry["filename"]).read_bytes())
    )
    slices = [
        {"pdf_sha256": retained.pdfs[0]["sha256"], "pages": [2]},
        {"pdf_sha256": retained.pdfs[1]["sha256"], "pages": [1]},
    ]
    assert manifest["edition"] == retained.edition
    assert manifest["manifest_sha256"] == entry["sha256"]
    assert manifest["documents"] == [
        {
            "document_id": stable_document_id(
                retained.edition["edition_id"], source_refs(slices)
            ),
            "source_slices": slices,
            "extraction": {
                "structural_observation": {
                    "template_id": "letter-20",
                    "category": "varonil",
                    "source_refs": [
                        {"pdf_sha256": sha, "pdf_page": number}
                        for sha, number in source_refs(slices)
                    ],
                    "physical_slots": [f"page:1:jugador_{slot}" for slot in range(1, 9)]
                    + [f"page:2:jugador_{slot}" for slot in range(9, 21)],
                }
            },
        }
    ]
    assert (retained.output / "preparation-report.json").read_bytes() == canonical(
        report
    )
    assert retained.output.stat().st_mode & 0o077 == 0
    for path in retained.output.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
    for source, (raw, _, _) in retained.originals.items():
        assert (retained.output / "originals" / source.name).read_bytes() == raw
    assert_originals_unchanged(retained)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign"])
def test_missing_duplicate_or_foreign_observation_cannot_omit_a_physical_page(
    retained, fault
):
    observed = copy.deepcopy(retained.observations)
    if fault == "missing":
        observed.pop()
    elif fault == "duplicate":
        observed.append(copy.deepcopy(observed[0]))
    else:
        observed[0]["pdf_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="every original physical page exactly once"):
        prepare(retained, observations=observed)
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert not (retained.output / "preparation-report.json").exists()
    assert_originals_unchanged(retained)


def test_ambiguous_cross_pdf_continuation_remains_exception_without_manifest(retained):
    profile = {**retained.profile, "source_groups": {}}
    observed = [{**item, "source_group": None} for item in retained.observations]
    report = prepare(retained, profile=profile, observations=observed)
    assert report["documents"] == 0
    assert report["manifests"] == []
    assert len(report["segmentation"]["page_classifications"]) == 3
    assert all(
        page["classification"] == "exception"
        for page in report["segmentation"]["page_classifications"]
    )
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(retained)


def test_manifest_retains_wholly_unknown_pdf_in_coverage_denominator(retained):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((30, 50), "unknown standalone source")
    raw = document.tobytes()
    document.close()
    path = retained.root / "Zacatecas desconocido.pdf"
    path.write_bytes(raw)
    path.chmod(0o644)
    retained.originals[path] = (raw, path.stat().st_mode, path.stat().st_mtime_ns)
    retained.limit += len(raw)
    unknown_sha = hashlib.sha256(raw).hexdigest()
    observed = [
        {
            "pdf_sha256": unknown_sha,
            "pdf_page": 1,
            "side": "unknown",
            "source_group": None,
            "slots": [],
        },
        *retained.observations,
    ]
    report = prepare(retained, observations=observed)
    assert report["physical_pages"] == 4 and report["documents"] == 1
    manifest = validate_source_manifest(
        json.loads((retained.output / report["manifests"][0]["filename"]).read_bytes())
    )
    assert len(manifest["pdfs"]) == 3
    assert unknown_sha in {pdf["sha256"] for pdf in manifest["pdfs"]}
    member = manifest["documents"][0]
    assert unknown_sha not in {part["pdf_sha256"] for part in member["source_slices"]}
    assert {
        "pdf_sha256": unknown_sha,
        "pdf_page": 1,
        "classification": "exception",
        "code": "UNKNOWN_PAGE",
    } in report["segmentation"]["page_classifications"]
    pages = [
        {"pdf_sha256": pdf["sha256"], "pdf_page": number}
        for pdf in manifest["pdfs"]
        for number in range(1, pdf["page_count"] + 1)
    ]
    metrics = calculate_metrics(
        [{**member, "auto_ready": True}],
        source_pages=pages,
        cutoff="2026-11-09T00:00:00+00:00",
        profile_hash=digest(retained.profile),
        corpus_hash=manifest["manifest_sha256"],
    )
    assert metrics["pages"] == {"numerator": 2, "denominator": 4}
    assert_originals_unchanged(retained)


@pytest.mark.parametrize("limit", [0, -1, True])
def test_missing_or_coerced_byte_limit_fails_before_output(retained, limit):
    with pytest.raises(ValueError, match="Explicit source byte limit"):
        prepare(retained, max_total_bytes=limit)
    assert not retained.output.exists()
    assert_originals_unchanged(retained)


def test_total_byte_limit_blocks_manifest_without_modifying_originals(retained):
    with pytest.raises(ValueError, match="Source byte limit exceeded"):
        prepare(retained, max_total_bytes=retained.limit - 1)
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(retained)


def test_source_symlink_is_not_followed(retained, tmp_path):
    source = next(iter(retained.originals))
    target = tmp_path / "outside.pdf"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)
    with pytest.raises(OSError):
        prepare(retained)
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert target.read_bytes() == retained.originals[source][0]


def test_existing_output_is_never_overwritten(retained):
    report = prepare(retained)
    original_files = {
        path: path.read_bytes() for path in retained.output.rglob("*") if path.is_file()
    }
    with pytest.raises(FileExistsError):
        prepare(retained)
    assert {
        path: path.read_bytes() for path in retained.output.rglob("*") if path.is_file()
    } == original_files
    assert (
        json.loads((retained.output / "preparation-report.json").read_bytes()) == report
    )
    assert_originals_unchanged(retained)


def test_edition_binding_is_validated_before_any_manifest_is_published(retained):
    edition = {**retained.edition, "tournament_id": "not-a-uuid"}
    with pytest.raises(ValueError):
        prepare(retained, edition=edition)
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(retained)


def test_main_uses_explicit_retained_inputs_and_prints_counts_only(
    retained, tmp_path, monkeypatch, capsys
):
    args = [
        "prepare_ctt_batch_sources.py",
        "--pdf-root",
        str(retained.root),
        "--output-root",
        str(retained.output),
        "--max-total-bytes",
        str(retained.limit),
    ]
    for name, value in (
        ("edition", retained.edition),
        ("observations", retained.observations),
        ("profile", retained.profile),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(canonical(value))
        args.extend(["--" + name, str(path)])
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "physical_pages": 3,
        "documents": 1,
        "provider_calls": 0,
        "teams_created": 0,
    }
    assert_originals_unchanged(retained)


def test_inventory_only_main_is_explicit_offline_and_prints_counts_only(
    retained, tmp_path, monkeypatch, capsys
):
    from devnous.copa_telmex.staff_batch_inventory import RENDER_VERSION

    edition_path, render_path = tmp_path / "edition.json", tmp_path / "render.json"
    edition_path.write_bytes(canonical(retained.edition))
    render_path.write_bytes(
        canonical(
            dict(
                version=RENDER_VERSION,
                dpi=72,
                color="RGB",
                max_page_pixels=25_000_000,
                max_render_bytes=32 * 1024**2,
            )
        )
    )
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_ctt_batch_sources.py",
            "--inventory-only",
            "--pdf-root",
            str(retained.root),
            "--output-root",
            str(retained.output),
            "--edition",
            str(edition_path),
            "--render-profile",
            str(render_path),
            "--max-total-bytes",
            str(retained.limit),
        ],
    )
    cli.main()
    response = json.loads(capsys.readouterr().out)
    assert response == dict(
        physical_pages=3,
        pdfs=2,
        provider_calls=0,
        teams_created=0,
        inventory_sha256=digest(
            json.loads((retained.output / "source-inventory.json").read_bytes())
        ),
    )
    inventory = json.loads((retained.output / "source-inventory.json").read_bytes())
    assert inventory["edition"] == {
        key: retained.edition[key] for key in ("tenant_id", "edition_id", "roster_slug")
    }
    assert not list(retained.output.glob("source-manifest-*.json"))
    assert not (retained.output / "preparation-report.json").exists()
    assert_originals_unchanged(retained)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--inventory-only"],
        [
            "--inventory-only",
            "--render-profile",
            "render.json",
            "--observations",
            "obs.json",
        ],
        [
            "--inventory-only",
            "--render-profile",
            "render.json",
            "--profile",
            "profile.json",
        ],
        ["--render-profile", "render.json"],
        [],
    ],
)
def test_main_rejects_ambiguous_or_missing_mode_inputs_before_reading_files(
    retained, monkeypatch, arguments
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_ctt_batch_sources.py",
            "--pdf-root",
            str(retained.root),
            "--output-root",
            str(retained.output),
            "--edition",
            "unread-edition.json",
            "--max-total-bytes",
            str(retained.limit),
            *arguments,
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not retained.output.exists()


@pytest.fixture
def retained_envelope(retained, tmp_path):
    """Packaging fixture; source qualification belongs to analyzer acceptance."""
    from devnous.copa_telmex.staff_batch_inventory import (
        RENDER_VERSION,
        prepare_inventory,
    )
    from devnous.tournaments.core.ctt_batch_structural_extraction import PIPELINE

    inventory = prepare_inventory(
        pdf_root=retained.root,
        output_root=tmp_path / "retained-inventory",
        edition={
            k: retained.edition[k] for k in ("tenant_id", "edition_id", "roster_slug")
        },
        render_profile=dict(
            version=RENDER_VERSION,
            dpi=72,
            color="RGB",
            max_page_pixels=25_000_000,
            max_render_bytes=32 * 1024**2,
        ),
        max_total_bytes=retained.limit,
    )
    quote = dict(
        revision="synthetic",
        model_revision="synthetic-2026-01-01",
        currency="USD",
        unit="micro_usd",
        input_per_million_micro_usd=1_000_000,
        cached_input_per_million_micro_usd=100_000,
        output_per_million_micro_usd=2_000_000,
        model_input_token_limit=10,
    )
    region = dict(x=0.1, y=0.1, width=0.1, height=0.1)
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
            batch_size=2,
            max_output_tokens=256,
            max_request_bytes=1_000_000,
            max_analysis_bytes=1_000_000,
        ),
        templates=[
            dict(
                id="letter-20",
                title_text="SYNTHETIC",
                sides={
                    side: dict(
                        slot_regions={str(n): copy.deepcopy(region) for n in numbers},
                        probes=[
                            dict(
                                rectangle=copy.deepcopy(region), min_ink=0.1, max_ink=1
                            )
                        ]
                        * 2,
                        header_regions=(
                            {
                                k: copy.deepcopy(region)
                                for k in ("title", "category", "team")
                            }
                            if side == "front"
                            else {}
                        ),
                    )
                    for side, numbers in retained.profile[
                        "allowed_page_patterns"
                    ].items()
                },
            )
        ],
        segmentation=copy.deepcopy(retained.profile),
    )
    by_ref = {(p["pdf_sha256"], p["pdf_page"]): p for p in inventory["pages"]}
    pages = [
        {
            **copy.deepcopy(p),
            "render_sha256": by_ref[(p["pdf_sha256"], p["pdf_page"])]["render_sha256"],
            "raw_observation": {
                "literal": "unchanged retained input",
                "category": None,
            },
            "header_evidence": {"category": {"value": "explicit source label"}},
        }
        for p in retained.observations
    ]
    envelope = dict(
        schema="ctt.batch.structural-observations.v1",
        preparation_id=str(uuid4()),
        inventory_sha256=digest(inventory),
        profile_hash=digest(profile),
        pages=pages,
    )
    return SimpleNamespace(
        source=retained, inventory=inventory, profile=profile, envelope=envelope
    )


def prepare_envelope(case, **changes):
    source = case.source
    args = dict(
        pdf_root=source.root,
        output_root=source.output,
        edition=source.edition,
        observations=case.envelope,
        profile=case.profile,
        max_total_bytes=source.limit,
        inventory=case.inventory,
    )
    return cli.prepare(**{**args, **changes})


def test_retained_envelope_packages_exact_provenance_and_all_raw_pages(
    retained_envelope, monkeypatch
):
    case = retained_envelope
    monkeypatch.setattr(
        socket, "socket", lambda *a, **k: pytest.fail("offline network")
    )
    report = prepare_envelope(case)
    source = case.source
    provenance = {
        k: case.envelope[k]
        for k in ("preparation_id", "inventory_sha256", "profile_hash")
    }
    provenance["observations_sha256"] = digest(case.envelope)
    assert report["documents"] == 1 and report["physical_pages"] == 3
    assert report["preparation_provenance"] == provenance
    assert report["observations_sha256"] == digest(case.envelope)
    assert report["profile_sha256"] == digest(case.profile)
    manifest = validate_source_manifest(
        json.loads((source.output / report["manifests"][0]["filename"]).read_bytes())
    )
    extraction = manifest["documents"][0]["extraction"]
    assert set(extraction) == {
        "structural_observation",
        "preparation_provenance",
        "retained_structural_observations",
    }
    assert set(extraction["structural_observation"]) == {
        "template_id",
        "category",
        "source_refs",
        "physical_slots",
    }
    assert extraction["preparation_provenance"] == provenance
    assert extraction["retained_structural_observations"] == case.envelope["pages"][1:]
    for name, value in (
        ("source-observations.json", case.envelope),
        ("source-inventory.json", case.inventory),
        ("structural-profile.json", case.profile),
    ):
        path = source.output / name
        assert path.read_bytes() == canonical(value)
        assert path.stat().st_mode & 0o077 == 0
    assert (
        json.loads((source.output / "source-observations.json").read_bytes())["pages"][
            0
        ]["side"]
        == "unknown"
    )
    assert (
        report["database_writes"]
        == report["provider_calls"]
        == report["teams_created"]
        == 0
    )
    assert report["requires_independent_source_admission"] is True
    assert_originals_unchanged(source)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_inventory",
        "inventory_hash",
        "profile_hash",
        "edition",
        "uuid",
        "envelope_field",
        "schema",
        "missing",
        "duplicate",
        "order",
        "foreign",
        "render_hash",
        "boolean_page",
        "profile_schema",
        "profile_quote",
        "bad_inventory",
    ],
)
def test_envelope_binding_mismatch_rejects_before_output(retained_envelope, fault):
    case = retained_envelope
    changes = {}
    if fault == "missing_inventory":
        changes["inventory"] = None
    elif fault == "inventory_hash":
        case.envelope["inventory_sha256"] = "0" * 64
    elif fault == "profile_hash":
        case.envelope["profile_hash"] = "0" * 64
    elif fault == "edition":
        changes["edition"] = {**case.source.edition, "tenant_id": "other"}
    elif fault == "uuid":
        case.envelope["preparation_id"] = "invalid"
    elif fault == "envelope_field":
        case.envelope["human_reviewed"] = True
    elif fault == "schema":
        case.envelope["schema"] = "ctt.registration.source-manifest.v2"
    elif fault == "missing":
        case.envelope["pages"].pop()
    elif fault == "duplicate":
        case.envelope["pages"][1] = copy.deepcopy(case.envelope["pages"][0])
    elif fault == "order":
        case.envelope["pages"].reverse()
    elif fault == "foreign":
        case.envelope["pages"][0]["pdf_sha256"] = "0" * 64
    elif fault == "render_hash":
        case.envelope["pages"][0]["render_sha256"] = "0" * 64
    elif fault == "boolean_page":
        case.envelope["pages"][0]["pdf_page"] = True
    elif fault == "profile_schema":
        case.profile["schema"] = "unconfigured"
    elif fault == "profile_quote":
        case.profile["provider"].pop("pricing")
    else:
        case.inventory["teams"] = []
    with pytest.raises(ValueError):
        prepare_envelope(case, **changes)
    assert not case.source.output.exists()
    assert_originals_unchanged(case.source)


@pytest.mark.parametrize(
    "fault", ["missing", "extra", "renamed", "changed_bytes", "changed_page_count"]
)
def test_actual_originals_must_match_retained_census_before_packaging(
    retained_envelope, fault
):
    case = retained_envelope
    source = case.source
    path = next(iter(source.originals))
    if fault == "missing":
        path.unlink()
    elif fault == "extra":
        (source.root / "extra.pdf").write_bytes(path.read_bytes())
    elif fault == "renamed":
        path.rename(source.root / "renamed.pdf")
    elif fault == "changed_bytes":
        path.write_bytes(path.read_bytes()[:-1] + b"X")
    else:
        with fitz.open(path) as document:
            document.new_page()
            raw = document.tobytes()
        path.write_bytes(raw)
    with pytest.raises(ValueError):
        prepare_envelope(case, max_total_bytes=source.limit * 2)
    assert not list(source.output.glob("source-manifest-*.json"))
    assert not (source.output / "preparation-report.json").exists()


def test_final_copy_bytes_are_rechecked_before_publishing_candidates(
    retained_envelope, monkeypatch
):
    case = retained_envelope
    source = case.source
    original_open = cli.fitz.open
    opened = []

    def open_pdf(path, *args, **kwargs):
        if getattr(path, "parent", None) == source.output / "originals":
            opened.append(path)
            if len(opened) == 2:
                first = opened[0]
                first.write_bytes(first.read_bytes()[:-1] + b"X")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(cli.fitz, "open", open_pdf)
    with pytest.raises(ValueError, match="Retained original bytes mismatch"):
        prepare_envelope(case)
    assert not list(source.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(source)


def test_unknown_only_envelope_retains_census_without_invented_documents(
    retained_envelope,
):
    case = retained_envelope
    for p in case.envelope["pages"]:
        p.update(side="unknown", slots=[], reason="RETAINED_UNRESOLVED")
    report = prepare_envelope(case)
    assert report["documents"] == 0 and report["manifests"] == []
    assert len(report["segmentation"]["page_classifications"]) == 3
    assert all(
        p["classification"] == "exception"
        for p in report["segmentation"]["page_classifications"]
    )
    assert (case.source.output / "source-observations.json").read_bytes() == canonical(
        case.envelope
    )


def test_inventory_order_is_preserved_independently_of_filename_order(
    retained_envelope,
):
    case = retained_envelope
    case.inventory["pdfs"].reverse()
    order = {p["sha256"]: i for i, p in enumerate(case.inventory["pdfs"])}
    case.inventory["pages"].sort(key=lambda p: (order[p["pdf_sha256"]], p["pdf_page"]))
    case.envelope["pages"].sort(key=lambda p: (order[p["pdf_sha256"]], p["pdf_page"]))
    case.envelope["inventory_sha256"] = digest(case.inventory)
    for p in case.envelope["pages"]:
        p.update(side="unknown", slots=[])
    report = prepare_envelope(case)
    assert [p["filename"] for p in report["inventory"]] == [
        p["filename"] for p in case.inventory["pdfs"]
    ]
    assert report["documents"] == 0


def test_list_observations_cannot_silently_ignore_supplied_inventory(retained_envelope):
    case = retained_envelope
    with pytest.raises(ValueError, match="requires a retained observation envelope"):
        prepare_envelope(
            case, observations=case.source.observations, profile=case.source.profile
        )
    assert not case.source.output.exists()


def test_main_consumes_retained_envelope_without_manual_json_edits(
    retained_envelope, tmp_path, monkeypatch, capsys
):
    case = retained_envelope
    source = case.source
    args = [
        "prepare_ctt_batch_sources.py",
        "--pdf-root",
        str(source.root),
        "--output-root",
        str(source.output),
        "--max-total-bytes",
        str(source.limit),
    ]
    for name, value in (
        ("edition", source.edition),
        ("observations", case.envelope),
        ("profile", case.profile),
        ("source-inventory", case.inventory),
    ):
        path = tmp_path / (name + ".json")
        path.write_bytes(canonical(value))
        args.extend(["--" + name, str(path)])
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    assert json.loads(capsys.readouterr().out) == dict(
        physical_pages=3, documents=1, provider_calls=0, teams_created=0
    )
    assert (source.output / "source-observations.json").read_bytes() == canonical(
        case.envelope
    )


def test_declared_inventory_byte_count_must_match_final_originals(retained_envelope):
    case = retained_envelope
    case.inventory["pdfs"][0]["bytes"] += 1
    case.envelope["inventory_sha256"] = digest(case.inventory)
    with pytest.raises(ValueError, match="Retained original bytes mismatch"):
        prepare_envelope(case)
    assert not list(case.source.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(case.source)


def test_late_original_pdf_cannot_escape_the_retained_census(
    retained_envelope, monkeypatch
):
    case = retained_envelope
    source = case.source
    original_open = cli.fitz.open
    opened = []

    def open_pdf(path, *args, **kwargs):
        if getattr(path, "parent", None) == source.output / "originals":
            opened.append(path)
            if len(opened) == 2:
                (source.root / "late-extra.pdf").write_bytes(b"late")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(cli.fitz, "open", open_pdf)
    with pytest.raises(ValueError, match="Retained original census mismatch"):
        prepare_envelope(case)
    assert not list(source.output.glob("source-manifest-*.json"))
    assert_originals_unchanged(source)
