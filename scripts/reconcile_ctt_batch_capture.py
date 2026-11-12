#!/usr/bin/env python3
"""Check completeness of private run and canonical-result exports, offline.

This reports consistency of supplied exports, not current database truth or
human acceptance. It cannot create, approve or retry a capture.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    digest,
    strict_loads,
    validate_source_manifest,
)


def reconcile(run, manifests, results):
    admitted = [validate_source_manifest(m) for m in manifests]
    if {m["edition"]["edition_id"] for m in admitted} != {run["edition_id"]}:
        raise ValueError("Edition mismatch")
    hashes = {m["manifest_sha256"] for m in admitted}
    if hashes != set(run["manifest_hashes"]):
        raise ValueError("Manifest inventory mismatch")
    expected = {d["document_id"] for m in admitted for d in m["documents"]}
    expected_pairs = {
        (m["manifest_sha256"], d["document_id"])
        for m in admitted
        for d in m["documents"]
    }
    members = run["members"]
    if type(members) is not list or type(results) is not list:
        raise ValueError("Complete private exports required")
    if (
        len({m["document_id"] for m in members}) != len(members)
        or {m["document_id"] for m in members} != expected
    ):
        raise ValueError("Incomplete or duplicate member export")
    by_attempt = {}
    result_ids, team_ids = set(), set()
    for result in results:
        for name in ("attempt_id", "result_id", "team_id"):
            UUID(result[name])
        if (
            result["attempt_id"] in by_attempt
            or result["result_id"] in result_ids
            or result["team_id"] in team_ids
        ):
            raise ValueError("Duplicate canonical result identity")
        by_attempt[result["attempt_id"]] = result
        result_ids.add(result["result_id"])
        team_ids.add(result["team_id"])
    categories = {
        "committed": [],
        "already_captured": [],
        "exception": [],
        "failed": [],
        "uncertain": [],
        "pending": [],
        "inconsistent": [],
    }
    used = set()
    for member in members:
        document = member["document_id"]
        if (member.get("manifest_sha256"), document) not in expected_pairs:
            raise ValueError("Member manifest mismatch")
        result = by_attempt.get(member.get("attempt_id"))
        if result:
            used.add(result["attempt_id"])
        if member["status"] == "committed":
            categories["committed" if result else "inconsistent"].append(document)
        elif result:
            categories["inconsistent"].append(document)
        elif member["status"] in {
            "already_captured",
            "exception",
            "failed",
            "uncertain",
        }:
            categories[member["status"]].append(document)
        else:
            categories["pending"].append(document)
    orphan_results = sorted(set(by_attempt) - used)
    return {
        "schema": "ctt.batch.export-reconciliation.v1",
        "run_id": run["run_id"],
        "edition_id": run["edition_id"],
        "run_export_sha256": digest(run),
        "results_export_sha256": digest(results),
        "manifest_hashes": sorted(hashes),
        "counts": {k: len(v) for k, v in categories.items()},
        "documents": categories,
        "orphan_result_attempts": orphan_results,
        "consistent": not categories["inconsistent"] and not orphan_results,
        "consistency_only": True,
        "database_verified": False,
        "database_writes": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-export", type=Path, required=True)
    parser.add_argument("--results-export", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = reconcile(
        strict_loads(args.run_export.read_bytes()),
        [strict_loads(p.read_bytes()) for p in args.manifest],
        strict_loads(args.results_export.read_bytes()),
    )
    # Private output directory must already be prepared by the operator.
    if args.output.parent.stat().st_mode & 0o077:
        raise ValueError("Private output directory required")
    fd = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(report))
    print(
        canonical(
            {"counts": report["counts"], "consistent": report["consistent"]}
        ).decode()
    )
    return 0 if report["consistent"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
