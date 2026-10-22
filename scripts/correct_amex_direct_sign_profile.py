"""Preview/apply the reviewed AMEX statement sign correction with explicit identity.

No default actor, database credentials, implicit confirmation or generic targets.
Apply requires the saved preview manifest; it never prints bank file contents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from devnous.gastos.services.amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    digest,
)
from devnous.gastos.services.amex_direct_sign_correction_service import (
    confirm_direct_sign_correction,
    preview_direct_sign_correction,
)

TARGET_IMPORT_ID = UUID("43119aac-2cbe-4875-ad88-9133c187221c")
TARGET_COUNTS = {"debit": 14, "credit": 2, "unknown": 0, "skipped": 0}
TARGET_TOTALS = {"debit": "8747.10", "credit": "17357.40"}


def validate_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    """Bind the reviewed file plan to the exact operation before opening a DB."""
    expected = {
        "import_id": str(TARGET_IMPORT_ID),
        "actor_id": str(args.actor_id),
        "profile": "debit_positive",
        "original_profile": "debit_negative",
        "reason": args.reason.strip(),
        "expected_source_version": args.source_version,
        "expected_count": 16,
        "effective_counts": TARGET_COUNTS,
        "effective_totals": TARGET_TOTALS,
        "preview_hash": args.preview_hash,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise DirectConflict("sign_manifest_mismatch")
    if not isinstance(manifest.get("rows"), list) or len(manifest["rows"]) != 16:
        raise DirectConflict("sign_manifest_mismatch")
    preimage = {key: value for key, value in manifest.items() if key != "preview_hash"}
    if digest(preimage) != args.preview_hash:
        raise DirectConflict("sign_manifest_mismatch")


async def run(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    """Use one explicit transaction; unsuccessful operations never commit."""
    if args.import_id != TARGET_IMPORT_ID or args.profile != "debit_positive":
        raise DirectConflict("sign_maintenance_target_denied")
    if args.command == "apply":
        if not all(
            (args.confirmed, args.preview_hash, args.idempotency_key, args.manifest)
        ):
            raise DirectConflict("sign_confirmation_required")
        validate_manifest(json.loads(Path(args.manifest).read_text()), args)
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(
            engine, expire_on_commit=False, autoflush=False
        ) as session:
            async with session.begin():
                if args.command == "preview":
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                operation = dict(
                    actor_id=args.actor_id,
                    import_id=args.import_id,
                    profile=args.profile,
                    reason=args.reason,
                    expected_source_version=args.source_version,
                )
                if args.command == "apply":
                    result = await confirm_direct_sign_correction(
                        session,
                        **operation,
                        confirmed=True,
                        preview_hash=args.preview_hash,
                        idempotency_key=args.idempotency_key,
                    )
                else:
                    result = await preview_direct_sign_correction(session, **operation)
                if result.status not in {"preview", "corrected", "exists"}:
                    raise DirectConflict(result.reason or "sign_correction_blocked")
                if result.status == "preview":
                    # Preview must itself match the specifically approved repair.
                    args.preview_hash = result.progress["preview_hash"]
                    validate_manifest(result.progress, args)
                return {
                    "status": result.status,
                    "source_version": result.source_version,
                    "manifest": result.progress,
                }
    finally:
        await engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preview", "apply"), nargs="?", default="preview"
    )
    parser.add_argument("--import-id", type=UUID, required=True)
    parser.add_argument("--actor-id", type=UUID, required=True)
    parser.add_argument("--profile", choices=("debit_positive",), required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--source-version", type=int, required=True)
    parser.add_argument(
        "--database-url-env", default="AMEX_SIGN_CORRECTION_DATABASE_URL"
    )
    parser.add_argument("--preview-hash")
    parser.add_argument("--idempotency-key")
    parser.add_argument(
        "--manifest", help="Private JSON containing the preview manifest object"
    )
    parser.add_argument("--confirmed", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        print(
            json.dumps(
                {"status": "blocked", "reason": "explicit_database_url_required"}
            )
        )
        return 1
    try:
        result = asyncio.run(run(args, database_url))
    except (DirectAccessError, DirectConflict) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 1
    except SQLAlchemyError:
        print(
            json.dumps(
                {"status": "blocked", "reason": "sign_database_operation_failed"}
            )
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "blocked", "reason": "sign_manifest_invalid"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
