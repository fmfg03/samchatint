"""Export a coverage.py database as bounded, checksummed job outputs."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    source = parser.parse_args().database

    with source.open("rb") as database:
        maximum_bytes = 16 * 1024 * 1024
        data = database.read(maximum_bytes + 1)
    if not 0 < len(data) <= maximum_bytes:
        raise SystemExit("Coverage database size is outside the supported range")
    if not data.startswith(b"SQLite format 3\0"):
        raise SystemExit("Expected a SQLite coverage database")
    payload = base64.b64encode(gzip.compress(data, mtime=0)).decode("ascii")
    # Leave room below Linux's 128 KiB limit for a single environment entry.
    if len(payload) > 120_000:
        raise SystemExit("Compressed coverage exceeds the job output limit")
    checksum = hashlib.sha256(data).hexdigest()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="ascii") as output:
        output.write(f"coverage_b64={payload}\ncoverage_sha256={checksum}\n")
    print(f"Prepared {len(payload)} characters of coverage with SHA-256 {checksum}")


if __name__ == "__main__":
    main()
