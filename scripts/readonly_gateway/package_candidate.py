"""Create a deterministic local review package; never installs or publishes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT.parent / "readonly-delivery-20261009"
OUTPUT.mkdir(exist_ok=True)
directories = [
    "src/samchat/readonly_gateway",
    "tests/unit/readonly_gateway",
    "scripts/readonly_gateway",
    "deployment/readonly_gateway",
]
files = sorted(
    path
    for directory in directories
    for path in (ROOT / directory).rglob("*")
    if path.is_file()
    and path.suffix
    in {
        ".py",
        ".json",
        ".mjs",
        ".patch",
        ".sql",
        ".service",
        ".timer",
        ".conf",
        ".fixture",
    }
)
files.append(ROOT / "docs/assistant/samchat-readonly-candidate.md")
files.append(ROOT / ".github/workflows/test.yml")
files.append(ROOT / "pyproject.toml")
files.append(ROOT / "docs/private-plugin/route-inventory.json")
files.append(ROOT / "docs/private-plugin/route-matrix.csv")
base = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
if base != "2cb5397769fa1f7532ec495a68a19cbc4a6778c7":
    raise SystemExit("Unexpected base; do not package")
# Intent-to-add makes new-file diff reviewable without a commit or publication.
subprocess.run(
    [
        "git",
        "-C",
        str(ROOT),
        "add",
        "--sparse",
        "--intent-to-add",
        "--",
        *[p.relative_to(ROOT).as_posix() for p in files],
    ],
    check=True,
)
subprocess.run(["git", "-C", str(ROOT), "diff", "--check"], check=True)
patch = subprocess.check_output(
    ["git", "-C", str(ROOT), "diff", "--binary", "--no-ext-diff"]
)
(OUTPUT / "samchat-readonly-candidate.patch").write_bytes(patch)
manifest = {
    "status": "local_candidate_not_committed_not_activated",
    "base_commit": base,
    "files": {
        p.relative_to(ROOT).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
        }
        for p in files
    },
    "patch_sha256": hashlib.sha256(patch).hexdigest(),
    "secrets_credentials_env_sessions_git_metadata": "excluded",
}
(OUTPUT / "MANIFEST.json").write_text(
    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
)
with zipfile.ZipFile(
    OUTPUT / "samchat-readonly-candidate.zip", "w", compression=zipfile.ZIP_DEFLATED
) as archive:
    for path in files:
        entry = zipfile.ZipInfo(
            path.relative_to(ROOT).as_posix(), date_time=(2026, 10, 8, 0, 0, 0)
        )
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, path.read_bytes())
print(
    json.dumps(
        {
            "output": str(OUTPUT),
            "files": len(files),
            "patch_sha256": manifest["patch_sha256"],
            "zip_sha256": hashlib.sha256(
                (OUTPUT / "samchat-readonly-candidate.zip").read_bytes()
            ).hexdigest(),
        },
        indent=2,
    )
)
