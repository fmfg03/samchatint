#!/usr/bin/env python3
"""Prepare private inventories or V2 manifests from retained observations.

The explicit --inventory-only mode retains exact original-page evidence before
grouping; the default mode continues to require retained observations.
Retained structural envelopes require their exact --source-inventory and full
structural --profile; raw observations and provenance stay private in the package.
Neither mode admits sources. No provider calls, database connections, signatures
or capture authority.
Original PDFs are read only; private copies and explicit exceptions are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
from pathlib import Path
from typing import Any

import fitz

from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    canonical_uuid,
    digest,
    strict_loads,
    validate_source_manifest,
)
from devnous.tournaments.core.ctt_batch_segmentation import segment_pages


def _write(path, value):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def _retained_binding(edition, observations, profile, inventory):
    """Bind an exact private observation envelope, without admitting its claims."""
    from devnous.copa_telmex.staff_batch_inventory import validate_inventory
    from devnous.tournaments.core.ctt_batch_structural_extraction import (
        validate_structural_profile,
    )

    if (
        type(observations) is not dict
        or set(observations)
        != {
            "schema",
            "preparation_id",
            "inventory_sha256",
            "profile_hash",
            "pages",
        }
        or observations["schema"] != "ctt.batch.structural-observations.v1"
    ):
        raise ValueError("Exact retained structural observations required")
    canonical_uuid(observations["preparation_id"])
    inventory = validate_inventory(inventory)
    validate_structural_profile(profile)
    if (
        type(edition) is not dict
        or any(edition.get(k) != v for k, v in inventory["edition"].items())
        or observations["inventory_sha256"] != digest(inventory)
        or observations["profile_hash"] != digest(profile)
        or type(observations["pages"]) is not list
        or len(observations["pages"]) != len(inventory["pages"])
    ):
        raise ValueError("Retained preparation binding mismatch")
    for observation, page in zip(observations["pages"], inventory["pages"]):
        if (
            type(observation) is not dict
            or type(observation.get("pdf_page")) is not int
            or any(
                observation.get(k) != page[k]
                for k in ("pdf_sha256", "pdf_page", "render_sha256")
            )
        ):
            raise ValueError("Retained page census mismatch")
    provenance = {
        k: observations[k]
        for k in ("preparation_id", "inventory_sha256", "profile_hash")
    }
    provenance["observations_sha256"] = digest(observations)
    return inventory, provenance


def _verify_retained_originals(private_root, pdfs, inventory):
    """Recheck the final copied bytes before publishing any candidate package."""
    expected = inventory["pdfs"]
    actual = [{k: p[k] for k in ("filename", "sha256", "page_count")} for p in expected]
    if pdfs != actual:
        raise ValueError("Retained original census mismatch")
    for pdf in expected:
        fd = os.open(private_root / pdf["filename"], os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != pdf["bytes"]:
                raise ValueError("Retained original bytes mismatch")
            hasher, total = hashlib.sha256(), 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > pdf["bytes"]:
                    raise ValueError("Retained original bytes mismatch")
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
            if (
                total != pdf["bytes"]
                or hasher.hexdigest() != pdf["sha256"]
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise ValueError("Retained original bytes mismatch")


def prepare(
    *,
    pdf_root,
    output_root,
    edition,
    observations,
    profile,
    max_total_bytes,
    inventory=None,
):
    """Inventory every PDF/page and conservatively segment exact references."""
    if type(max_total_bytes) is not int or max_total_bytes <= 0:
        raise ValueError("Explicit source byte limit required")
    envelope, source_inventory, provenance = None, None, None
    segmentation_profile = profile
    if type(observations) is dict:
        source_inventory, provenance = _retained_binding(
            edition, observations, profile, inventory
        )
        envelope = strict_loads(canonical(observations))
        observations = envelope["pages"]
        segmentation_profile = profile["segmentation"]
    elif inventory is not None:
        raise ValueError("Source inventory requires a retained observation envelope")
    sources = sorted(Path(pdf_root).iterdir(), key=lambda path: path.name)
    sources = [p for p in sources if p.suffix.lower() == ".pdf"]
    if not 1 <= len(sources) <= 16 or len({p.name for p in sources}) != len(sources):
        raise ValueError("Expected one to sixteen original PDF files")
    if source_inventory is not None:
        expected_names = [p["filename"] for p in source_inventory["pdfs"]]
        if {p.name for p in sources} != set(expected_names):
            raise ValueError("Retained original census mismatch")
        sources = [Path(pdf_root) / name for name in expected_names]
    output_root = Path(output_root)
    output_root.mkdir(mode=0o700)
    private_root = output_root / "originals"
    private_root.mkdir(mode=0o700)
    pdfs: list[dict[str, Any]] = []
    total = 0
    pages: set[tuple[str, int]] = set()
    for source in sources:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as original:
            before = os.fstat(original.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
                raise ValueError("Original must be a regular nonempty PDF")
            total += before.st_size
            if total > max_total_bytes:
                raise ValueError("Source byte limit exceeded")
            target = private_root / source.name
            out_fd = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            hasher = hashlib.sha256()
            copied = 0
            with os.fdopen(out_fd, "wb") as copied_stream:
                while chunk := original.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > before.st_size:
                        raise ValueError("Original changed while copying")
                    hasher.update(chunk)
                    copied_stream.write(chunk)
                copied_stream.flush()
                os.fsync(copied_stream.fileno())
            after = os.fstat(original.fileno())
            if copied != before.st_size or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Original changed while copying")
        with fitz.open(target) as document:
            if (
                not document.is_pdf
                or document.is_encrypted
                or not 1 <= len(document) <= 1200
            ):
                raise ValueError("Unsupported source PDF")
            pdf: dict[str, Any] = {
                "filename": source.name,
                "sha256": hasher.hexdigest(),
                "page_count": len(document),
            }
        if pdf["sha256"] in {p["sha256"] for p in pdfs}:
            raise ValueError("Duplicate original PDF bytes require explicit resolution")
        pdfs.append(pdf)
        pages.update((pdf["sha256"], n) for n in range(1, pdf["page_count"] + 1))
    if len(pages) > 1200 or type(observations) is not list:
        raise ValueError("Source page limit exceeded")
    observed = [(p.get("pdf_sha256"), p.get("pdf_page")) for p in observations]
    if len(observed) != len(set(observed)) or set(observed) != pages:
        raise ValueError(
            "Observations must account for every original physical page exactly once"
        )
    if source_inventory is not None:
        _verify_retained_originals(private_root, pdfs, source_inventory)
        if {p.name for p in Path(pdf_root).iterdir() if p.suffix.lower() == ".pdf"} != {
            p["filename"] for p in source_inventory["pdfs"]
        }:
            raise ValueError("Retained original census mismatch")
    segmented = segment_pages(
        observations, edition_id=edition["edition_id"], profile=segmentation_profile
    )
    manifests: list[dict[str, Any]] = []
    documents = segmented["documents"]
    for offset in range(0, len(documents), 300):
        selected = documents[offset : offset + 300]
        manifest = validate_source_manifest(
            {
                "schema_version": "ctt.registration.source-manifest.v2",
                "edition": edition,
                # Even a wholly unclassified PDF remains in the page census;
                # omitting it would inflate the operational scaling ratio.
                "pdfs": pdfs,
                "documents": [
                    {
                        "document_id": d["document_id"],
                        "source_slices": d["source_slices"],
                        "extraction": {
                            "structural_observation": {
                                "template_id": d["template_id"],
                                "category": d["category"],
                                "source_refs": d["page_refs"],
                                "physical_slots": [
                                    f"page:{index}:jugador_{slot}"
                                    for index, ref in enumerate(d["page_refs"], 1)
                                    for observation in observations
                                    if (
                                        observation["pdf_sha256"],
                                        observation["pdf_page"],
                                    )
                                    == (ref["pdf_sha256"], ref["pdf_page"])
                                    for slot in observation["slots"]
                                ],
                            },
                            **(
                                {
                                    "preparation_provenance": provenance,
                                    "retained_structural_observations": [
                                        observation
                                        for ref in d["page_refs"]
                                        for observation in observations
                                        if (
                                            observation["pdf_sha256"],
                                            observation["pdf_page"],
                                        )
                                        == (ref["pdf_sha256"], ref["pdf_page"])
                                    ],
                                }
                                if provenance is not None
                                else {}
                            ),
                        },
                    }
                    for d in selected
                ],
            }
        )
        name = f"source-manifest-{len(manifests) + 1:03d}.json"
        _write(output_root / name, manifest)
        manifests.append(
            {
                "filename": name,
                "sha256": manifest["manifest_sha256"],
                "documents": len(selected),
            }
        )
    report = {
        "schema": "ctt.batch.source-preparation.v1",
        "inventory": pdfs,
        "physical_pages": len(pages),
        "documents": len(documents),
        "observations_sha256": digest(
            envelope if envelope is not None else observations
        ),
        "profile_sha256": digest(profile),
        "manifests": manifests,
        "segmentation": segmented,
        "database_writes": 0,
        "provider_calls": 0,
        "teams_created": 0,
        "requires_independent_source_admission": True,
    }
    if envelope is not None:
        report["preparation_provenance"] = provenance
        _write(output_root / "source-observations.json", envelope)
        _write(output_root / "source-inventory.json", source_inventory)
        _write(output_root / "structural-profile.json", profile)
    _write(output_root / "preparation-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pdf-root", "output-root", "edition"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-total-bytes", type=int, required=True)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--render-profile", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument(
        "--source-inventory",
        type=Path,
        help="Exact original-page inventory for a structural envelope",
    )
    args = parser.parse_args()
    if args.inventory_only:
        if (
            args.render_profile is None
            or args.observations
            or args.profile
            or args.source_inventory
        ):
            parser.error(
                "--inventory-only requires --render-profile and no "
                "observations/profile/source-inventory"
            )
        from devnous.copa_telmex.staff_batch_inventory import prepare_inventory

        edition = strict_loads(args.edition.read_bytes())
        inventory = prepare_inventory(
            pdf_root=args.pdf_root,
            output_root=args.output_root,
            edition={k: edition[k] for k in ("tenant_id", "edition_id", "roster_slug")},
            render_profile=strict_loads(args.render_profile.read_bytes()),
            max_total_bytes=args.max_total_bytes,
        )
        print(
            canonical(
                dict(
                    physical_pages=len(inventory["pages"]),
                    pdfs=len(inventory["pdfs"]),
                    inventory_sha256=digest(inventory),
                    provider_calls=0,
                    teams_created=0,
                )
            ).decode()
        )
        return
    if args.observations is None or args.profile is None or args.render_profile:
        parser.error("retained preparation requires --observations and --profile")
    report = prepare(
        pdf_root=args.pdf_root,
        output_root=args.output_root,
        edition=strict_loads(args.edition.read_bytes()),
        observations=strict_loads(args.observations.read_bytes()),
        profile=strict_loads(args.profile.read_bytes()),
        max_total_bytes=args.max_total_bytes,
        inventory=(
            strict_loads(args.source_inventory.read_bytes())
            if args.source_inventory is not None
            else None
        ),
    )
    print(
        canonical(
            {
                k: report[k]
                for k in (
                    "physical_pages",
                    "documents",
                    "provider_calls",
                    "teams_created",
                )
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
