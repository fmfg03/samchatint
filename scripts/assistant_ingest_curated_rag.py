#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable, List

from samchat.assistant.rag import LocalRAGStore


CURATED_RAG_PATHS: tuple[str, ...] = (
    "docs/assistant/product-canon.md",
    "docs/assistant/context-corpus.md",
    "docs/assistant/owner-ai-needs.md",
    "docs/assistant/rqf-assistant-009-quality-roadmap.md",
    "docs/operations/OPERATIONS_REFERENCE.md",
    "docs/operations/ORCHESTRATION_INTEGRATION_GUIDE.md",
    "docs/operations/REG003_REGISTRATION_GOVERNANCE.md",
    "docs/operations/REGS13_PERSISTENCE_AUTHORITY.md",
    "docs/security/SECURITY_CONFIGURATION_REFERENCE.md",
    "docs/sprints/rqf-056-beneficiary-ops-ledger.md",
    "docs/sprints/rqf-057b-telegram-notifications-audit.md",
    "docs/sprints/rqf-057c-regional-operator-beneficiaries.md",
    "docs/sprints/rqf-057d-provider-search.md",
    "docs/sprints/rqf-057e-no-deducibles-rule.md",
    "docs/sprints/rqf-057f-sat-massive-download.md",
    "docs/sprints/rqf-057g-authorization-strategy-matrix.md",
    "docs/sprints/rqf-057g2-authorization-profile-board.md",
    "docs/sprints/rqf-057g3-authorization-evidence.md",
    "docs/sprints/rqf-057g4-authorization-evidence-detail.md",
    "docs/sprints/rqf-057g5-authorization-route-preview.md",
    "docs/sprints/rqf-057g6-authorization-pre-send-preview.md",
    "docs/sprints/rqf-057g7-authorization-soft-warning.md",
    "docs/sprints/rqf-057g8-authorization-warnings-dashboard.md",
    "docs/sprints/rqf-057g9-authorization-enforcement-readiness.md",
    "docs/sprints/rqf-058-sprint-close-candidate.md",
    "artifacts/rqf-samchat-assistant-007d-health-stable-readonly-runtime-soak.md",
    "artifacts/rqf-samchat-assistant-008-readonly-canary.md",
)

DENYLIST_PARTS: tuple[str, ...] = (
    ".env",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "site-packages",
    "uploads",
    "downloads",
    "private",
    "secrets",
)

DENYLIST_SUFFIXES: tuple[str, ...] = (
    ".xls",
    ".xlsx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".zip",
    ".sqlite",
    ".db",
)

DENYLIST_NAME_PARTS: tuple[str, ...] = (
    "evaluation-set",
    "eval-set",
)


def _is_safe_curated_path(path: Path, *, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    lowered_parts = {part.lower() for part in resolved.parts}
    if resolved.is_dir():
        return False
    if any(part in lowered_parts for part in DENYLIST_PARTS):
        return False
    lowered_name = resolved.name.lower()
    if any(part in lowered_name for part in DENYLIST_NAME_PARTS):
        return False
    if resolved.suffix.lower() in DENYLIST_SUFFIXES:
        return False
    return True


def curated_paths(
    *, root: Path, requested: Iterable[str] = CURATED_RAG_PATHS
) -> List[str]:
    safe_paths: List[str] = []
    for item in requested:
        candidate = (
            (root / item).resolve()
            if not Path(item).is_absolute()
            else Path(item).resolve()
        )
        if not candidate.exists():
            continue
        if not _is_safe_curated_path(candidate, root=root):
            continue
        safe_paths.append(str(candidate))
    return safe_paths


def _default_root() -> Path:
    return Path(
        os.getenv("ASSISTANT_RAG_BASE_DIR") or Path(__file__).resolve().parents[1]
    ).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest the curated SamChat assistant knowledge corpus into LocalRAGStore."
    )
    parser.add_argument(
        "--root",
        default=str(_default_root()),
        help="Repository root used to resolve curated paths.",
    )
    parser.add_argument(
        "--index-path",
        default=os.getenv("ASSISTANT_RAG_INDEX_PATH"),
        help="Optional explicit RAG index path. Defaults to LocalRAGStore behavior.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset the index before ingesting the curated corpus.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=200,
        help="Maximum files to ingest from curated directories.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the curated paths without writing the index.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = curated_paths(root=root)
    payload = {
        "root": str(root),
        "paths": paths,
        "path_count": len(paths),
        "reset": bool(args.reset),
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if paths else 2

    if not paths:
        print(
            json.dumps(
                {**payload, "error": "no curated paths found"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if args.index_path:
        store = LocalRAGStore(index_path=args.index_path)
    else:
        store = LocalRAGStore()
    result = store.ingest(
        paths=paths,
        reset=bool(args.reset),
        max_files=max(1, int(args.max_files)),
    )
    print(json.dumps({**payload, "ingest": result}, ensure_ascii=False, indent=2))
    if result.get("indexed_chunks", 0) <= 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
