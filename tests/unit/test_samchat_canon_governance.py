import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANON_PATHS = (
    "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md",
    "SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md",
    "SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md",
)
REGISTER_PATH = "docs/roadmap/samchat-convergence-register.md"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _normalized(relative_path: str) -> str:
    return " ".join(_read(relative_path).split())


def _registered_hashes() -> dict[str, str]:
    register = _read(REGISTER_PATH)
    return {
        path: digest
        for path, digest in re.findall(
            r"`(SAMCHAT_[A-Z_]+_2026-09-10\.md)` \| `([a-f0-9]{64})`",
            register,
        )
    }


def test_canon_sources_are_versioned_and_match_the_integrity_receipt() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", *CANON_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(tracked) == set(CANON_PATHS)

    registered_hashes = _registered_hashes()
    assert set(registered_hashes) == set(CANON_PATHS)
    for relative_path in CANON_PATHS:
        digest = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert registered_hashes[relative_path] == digest


def test_agents_requires_ordered_fail_closed_canon_bootstrap() -> None:
    instructions = _read("AGENTS.md")
    normalized = _normalized("AGENTS.md")
    positions = [instructions.index(path) for path in CANON_PATHS]
    assert positions == sorted(positions)
    assert "Before investigating, planning, editing, testing" in normalized
    assert "If any canon is missing, its hash differs" in normalized
    assert "Do not substitute memory, summaries, or assumptions" in normalized


def test_canon_governance_requires_evidence_and_human_review() -> None:
    register = _normalized(REGISTER_PATH)
    template = _read(".github/pull_request_template.md")

    for phrase in (
        "explicit, human-reviewed PR",
        "evidence, date, and reason",
        "Canon unchanged",
        "must never author or approve canon edits silently",
    ):
        assert phrase in register

    assert "Exactly one option must be selected" in template
    assert "Canon edits require explicit human review" in template
