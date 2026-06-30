"""Runtime controls for canonical registration review intake and PII retention."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REGISTRATION_REVIEW_CANONICAL_INTAKE = True
LEGACY_OCR_DISABLED_MESSAGE = "Legacy OCR intake is disabled. Use registration-review canonical flow."


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def is_legacy_ocr_intake_enabled() -> bool:
    return _env_bool("LEGACY_OCR_INTAKE_ENABLED", False)


def ensure_legacy_ocr_intake_enabled() -> None:
    if not is_legacy_ocr_intake_enabled():
        raise RuntimeError(LEGACY_OCR_DISABLED_MESSAGE)


def get_review_asset_retention_days() -> int:
    return _env_int("REVIEW_ASSET_RETENTION_DAYS", 30)


def get_review_draft_retention_days() -> int:
    return _env_int("REVIEW_DRAFT_RETENTION_DAYS", 90)


def get_review_purge_dry_run() -> bool:
    return _env_bool("REVIEW_PURGE_DRY_RUN", True)


LEGACY_OCR_INTAKE_ENABLED = is_legacy_ocr_intake_enabled()
REVIEW_ASSET_RETENTION_DAYS = get_review_asset_retention_days()
REVIEW_DRAFT_RETENTION_DAYS = get_review_draft_retention_days()
REVIEW_PURGE_DRY_RUN = get_review_purge_dry_run()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _safe_relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def _safe_age_days(reference: Optional[datetime], now: datetime) -> Optional[int]:
    if reference is None:
        return None
    return max(int((now - reference).total_seconds() // 86400), 0)


def build_review_pii_path_inventory(
    *,
    photos_root: Path,
    review_uploads_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    review_dir = review_uploads_dir or (photos_root / "review_sessions")
    inventory_paths = [
        ("review_sessions", review_dir),
        ("review_previews", review_dir),
        ("players", photos_root / "players"),
        ("rosters", photos_root / "rosters"),
        ("crops", photos_root / "crops"),
        ("personas", photos_root / "personas"),
    ]
    inventory: List[Dict[str, Any]] = []
    for label, path in inventory_paths:
        entry: Dict[str, Any] = {
            "label": label,
            "path": path.as_posix(),
            "exists": path.exists(),
            "file_count": "unknown",
            "directory_count": "unknown",
        }
        if path.exists():
            try:
                files = 0
                dirs = 0
                for child in path.rglob("*"):
                    if child.is_file():
                        files += 1
                    elif child.is_dir():
                        dirs += 1
                entry["file_count"] = files
                entry["directory_count"] = dirs
            except Exception:
                entry["file_count"] = "unknown"
                entry["directory_count"] = "unknown"
        inventory.append(entry)
    return inventory


def plan_review_data_retention(
    *,
    photos_root: Path,
    review_uploads_dir: Path,
    session_summaries: Optional[Iterable[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    dry_run: Optional[bool] = None,
    apply_changes: bool = False,
) -> Dict[str, Any]:
    current_time = now or _utc_now()
    effective_dry_run = get_review_purge_dry_run() if dry_run is None else bool(dry_run)
    asset_cutoff = current_time - timedelta(days=get_review_asset_retention_days())
    draft_cutoff = current_time - timedelta(days=get_review_draft_retention_days())
    result: Dict[str, Any] = {
        "dry_run": effective_dry_run,
        "cutoff": {
            "assets_before": asset_cutoff.isoformat(),
            "drafts_before": draft_cutoff.isoformat(),
        },
        "candidate_files": [],
        "candidate_sessions": [],
        "errors": [],
    }

    candidate_dirs: List[Path] = []

    try:
        for session_dir in sorted(review_uploads_dir.iterdir()) if review_uploads_dir.exists() else []:
            if not session_dir.is_dir():
                continue
            try:
                modified_at = datetime.fromtimestamp(session_dir.stat().st_mtime, tz=timezone.utc)
            except OSError:
                modified_at = None
            if modified_at and modified_at <= asset_cutoff:
                candidate_dirs.append(session_dir)
                preview_dir = session_dir / "player_previews"
                if preview_dir.exists():
                    candidate_dirs.append(preview_dir)
        crops_dir = photos_root / "crops"
        if crops_dir.exists():
            modified_at = datetime.fromtimestamp(crops_dir.stat().st_mtime, tz=timezone.utc)
            if modified_at <= asset_cutoff:
                candidate_dirs.append(crops_dir)
        seen_paths = set()
        deduped_dirs: List[Path] = []
        for path in candidate_dirs:
            marker = str(path.resolve())
            if marker in seen_paths:
                continue
            seen_paths.add(marker)
            deduped_dirs.append(path)
        candidate_dirs = deduped_dirs
    except Exception as exc:
        result["errors"].append(f"review_sessions_scan_failed:{type(exc).__name__}")

    for path in candidate_dirs:
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            file_entry = {
                "path": _safe_relative_path(path, photos_root),
                "age_days": _safe_age_days(modified_at, current_time),
                "kind": "directory" if path.is_dir() else "file",
            }
            result["candidate_files"].append(file_entry)
            if apply_changes and not effective_dry_run and path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        except Exception as exc:
            result["errors"].append(f"candidate_file_failed:{path.name}:{type(exc).__name__}")

    for session in list(session_summaries or []):
        status = str(session.get("status") or "").strip().lower()
        updated_at = _coerce_datetime(session.get("updated_at")) or _coerce_datetime(session.get("started_at"))
        if updated_at is None or updated_at > draft_cutoff:
            continue
        if status == "committed":
            continue
        result["candidate_sessions"].append(
            {
                "session_id": str(session.get("session_id") or session.get("id") or ""),
                "status": status or "unknown",
                "age_days": _safe_age_days(updated_at, current_time),
                "has_assets": bool(session.get("has_assets", True)),
                "has_draft": bool(session.get("has_draft", True)),
            }
        )

    return result
