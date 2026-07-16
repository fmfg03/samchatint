"""
Operations Module - Manages tournament operations.

Handles:
- Team and player registration (with OCR)
- Match scheduling
- Venue management
- Logistics
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import statistics
import subprocess
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from devnous.copa_telmex.draft_versioning import (
    append_draft_version,
    build_successor_values,
)
from devnous.copa_telmex.page_composition_governance import (
    admitted_asset_rows,
    build_gate_request as build_page_composition_gate_request,
    build_page_append_attempt,
    decision_row as build_page_composition_decision_row,
    existing_page_manifest,
    parent_authorization as page_composition_parent_authorization,
    proposed_page_manifest,
    sha256_binding as page_composition_sha256_binding,
    staged_page_manifest,
)
from devnous.copa_telmex.registration_governance import (
    RegistrationGovernanceClient,
    RegistrationGovernanceDenied,
)
from devnous.tournaments.core.intelligence_program import (
    EntityFinanceRecord,
    EntityOperationsRecord,
    NationalFinanceRecord,
    NationalMarketingRecord,
    NationalOperationsRecord,
    TournamentIntelligenceWorkspace,
)
from devnous.validation import validate_name_field, validate_team_name
from devnous.validation.hard_validator import ValidationStatus

from .local_ocr_runner import LocalOCRRunner
from .ocr_integrity import (
    average_hash_hex,
    canonicalize_mexican_state,
    compute_sha256_hex,
    crop_player_photo,
    describe_integrity_reasons,
    evaluate_player_identity_integrity,
    hashes_look_duplicate,
    image_has_photo_like_content,
    normalize_ctt_template_image,
    slugify_filename,
)

logger = logging.getLogger(__name__)


TEAM_FIELDS_WITH_SAFE_SUPABASE_SYNC = {
    "name",
    "representative_name",
    "contact_email",
    "league",
    "state",
    "municipality",
    "gender",
    "category",
}

PLAYER_FIELDS_WITH_SAFE_SUPABASE_SYNC = {
    "full_name",
    "birth_date",
    "curp",
    "email",
}

REGS08_GOVERNED_REVIEW_UNAVAILABLE = "REGS08_GOVERNED_REVIEW_UNAVAILABLE"


class OperationsModule:
    """Operations management for tournaments"""

    def __init__(
        self,
        tournament_id: str,
        config: Dict[str, Any],
        db=None,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        self.tournament_id = tournament_id
        # Support both legacy config (top-level) and new layout under "modules".
        self.config = config.get("operations", {}) or config.get("modules", {}).get("operations", {})
        self.telegram_config = config.get("telegram", {}) or config.get("modules", {}).get("telegram", {})
        self.db = db

        # In-memory storage
        self.teams = []
        self.players = []
        self.matches = []
        self.venues = []

        # OCR configuration
        self.ocr_enabled = self.config.get('ocr_enabled', False)
        # Supported:
        # - claude_vision: legacy single-player JSON prompt to Anthropic
        # - claude_structured: Anthropic OCRAgent tool_use schema (team + players)
        # - openai_vision: OpenAI-only (team + players)
        # - local_only: local Moondream/TrOCR subprocess only
        # - local_first: prefer local OCR and fallback to remote when quality is low
        # - compare_anthropic_openai: run both and let admin choose
        self.ocr_provider = self.config.get("ocr_provider", "claude_structured")

        self.admin_chat_ids = set(self.telegram_config.get("admin_chat_ids") or [])

        self.anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        self.openai_key = openai_key or os.getenv("OPENAI_API_KEY")

        # Initialize OCR components if enabled
        self.claude = None  # legacy client (single player)
        self.ocr_agent = None  # structured Anthropic extractor (team + players)
        self.validator = None
        self.pending_verifications: Dict[int, Dict[str, Any]] = {}
        self.pending_saves: Dict[int, Dict[str, Any]] = {}
        self.pending_edits: Dict[int, Dict[str, Any]] = {}
        self.pending_player_onboarding: Dict[int, Dict[str, Any]] = {}
        self.pending_back_photos: Dict[int, Dict[str, Any]] = {}  # chat_id -> {team_id, provider}
        self.photos_base_dir = Path(os.getenv("PHOTOS_DIR", "/root/samchat/photos"))
        self.photo_duplicate_distance = max(
            0,
            int(os.getenv("OCR_PLAYER_PHOTO_HASH_DISTANCE", "4")),
        )
        self.local_ocr_min_confidence = max(
            0.0,
            min(1.0, float(os.getenv("LOCAL_OCR_MIN_CONFIDENCE", "0.55"))),
        )
        self.local_ocr_min_players = max(1, int(os.getenv("LOCAL_OCR_MIN_PLAYERS", "2")))
        self.local_ocr_timeout_seconds = max(
            5.0,
            float(os.getenv("LOCAL_OCR_TIMEOUT_SECONDS", "180")),
        )
        self.local_ocr_runner = LocalOCRRunner(
            repo_root=Path(__file__).resolve().parents[4],
            timeout_seconds=self.local_ocr_timeout_seconds,
        )
        ai_root = self.config.get("ai_workspace_root") or os.getenv(
            "TOURNAMENT_AI_WORKSPACE_ROOT", "reports/tournaments_ai"
        )
        self.ai_workspace = TournamentIntelligenceWorkspace(root_dir=ai_root)

        if self.ocr_enabled:
            if self.anthropic_key:
                import anthropic

                from devnous.agents.ocr_agent import OCRAgent
                from devnous.validation import MexicanNamesValidator

                self.claude = anthropic.Anthropic(api_key=self.anthropic_key)
                self.ocr_agent = OCRAgent(anthropic_api_key=self.anthropic_key)
                self.validator = MexicanNamesValidator(min_confidence=0.80)
                logger.info(f"✅ OCR enabled (Anthropic) provider={self.ocr_provider}")
            if self.openai_key:
                logger.info(f"✅ OCR enabled (OpenAI) provider={self.ocr_provider}")
            if not self.anthropic_key and not self.openai_key:
                logger.warning("⚠️ OCR enabled but missing ANTHROPIC_API_KEY and OPENAI_API_KEY")
        else:
            logger.info("📭 OCR disabled")

        logger.info(f"🏃 Operations module initialized for {tournament_id}")

    def _generic_retry_error(self, summary: str) -> str:
        return f"❌ {summary}\nIntenta de nuevo."

    def _generic_db_error(self) -> str:
        return "No pude guardar en la base de datos. Reintenta o contacta a un admin."

    def _normalize_openai_registration_payload(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(raw or {})
        team = dict(payload.get("team") or {})
        if not (team.get("name") or "").strip():
            team["name"] = "Unknown Team"
        team.setdefault("confidence", 0.0)
        payload["team"] = team

        manager = payload.get("manager")
        if isinstance(manager, dict) and not (manager.get("name") or "").strip():
            payload["manager"] = None

        players = []
        for player in list(payload.get("players") or []):
            if not isinstance(player, dict):
                continue
            name = (player.get("name") or "").strip()
            first_name = (player.get("first_name") or "").strip()
            paternal = (player.get("paternal_surname") or "").strip()
            maternal = (player.get("maternal_surname") or "").strip()
            curp = (player.get("curp") or "").strip()
            birth_date = (player.get("birth_date") or "").strip()
            if not any([name, first_name, paternal, maternal, curp, birth_date]):
                continue
            if len(name) < 3:
                joined = " ".join(part for part in [first_name, paternal, maternal] if part).strip()
                if len(joined) >= 3:
                    player["name"] = joined
                else:
                    player["name"] = None
                    player["needs_review"] = True
            players.append(player)
        payload["players"] = players
        payload.setdefault("overall_confidence", 0.0)
        return payload

    def _validate_required_team_header(self, extraction) -> Tuple[bool, str]:
        team_name = (getattr(extraction.team, "name", None) or "").strip()
        state_value = (getattr(extraction.team, "state", None) or "").strip()
        municipality_value = (getattr(extraction.team, "municipality", None) or "").strip()
        canonical_state = canonicalize_mexican_state(state_value) if state_value else None
        if canonical_state:
            extraction.team.state = canonical_state
            state_value = canonical_state

        missing_labels: List[str] = []
        if not team_name or team_name.lower() in {"unknown team", "error"}:
            missing_labels.append("nombre del equipo")
        if not state_value:
            missing_labels.append("estado")
        if not municipality_value:
            missing_labels.append("municipio")

        suspicious_labels: List[str] = []
        if team_name:
            team_validation = validate_team_name(team_name)
            if team_validation.status != ValidationStatus.ACCEPT:
                suspicious_labels.append("nombre del equipo")
        if state_value:
            if not canonical_state:
                suspicious_labels.append("estado")
            else:
                state_validation = validate_name_field(state_value)
                if state_validation.status == ValidationStatus.HUMAN:
                    suspicious_labels.append("estado")
        if municipality_value:
            municipality_validation = validate_name_field(municipality_value)
            if municipality_validation.status != ValidationStatus.ACCEPT:
                suspicious_labels.append("municipio")

        problems: List[str] = []
        if missing_labels:
            problems.append("faltan: " + ", ".join(missing_labels))
        if suspicious_labels:
            problems.append("sospechosos: " + ", ".join(sorted(set(suspicious_labels))))

        if problems:
            return (
                False,
                "❌ *Cedula rechazada*\n\n"
                "No puedo guardar esta cedula porque el encabezado no cumple el minimo obligatorio.\n\n"
                f"{chr(10).join('• ' + problem for problem in problems)}\n\n"
                "Campos obligatorios: *equipo, estado y municipio*.\n"
                "Sube una foto mas clara y completa del frente de la cedula.",
            )

        return True, ""

    def _preferred_remote_ocr_provider(self) -> Optional[str]:
        if self.anthropic_key and self.ocr_agent:
            return "anthropic"
        if self.openai_key:
            return "openai"
        return None

    def _local_extraction_quality(self, extraction) -> Tuple[bool, str]:
        if extraction is None:
            return False, "sin extraccion local"

        header_ok, header_error = self._validate_required_team_header(extraction)
        if not header_ok:
            return False, header_error

        players = list(getattr(extraction, "players", None) or [])
        overall_confidence = float(getattr(extraction, "overall_confidence", 0.0) or 0.0)
        review_count = sum(1 for player in players if getattr(player, "needs_review", False))

        if len(players) < self.local_ocr_min_players:
            return False, f"solo detecte {len(players)} jugador(es)"
        if overall_confidence < self.local_ocr_min_confidence:
            return False, f"confianza local {overall_confidence:.2f} < {self.local_ocr_min_confidence:.2f}"
        if players and review_count == len(players):
            return False, "todos los jugadores quedaron en revision"

        return True, f"confianza={overall_confidence:.2f}, jugadores={len(players)}, revision={review_count}"

    def _decode_image_b64(self, image_b64: Optional[str]) -> Optional[Image.Image]:
        if not image_b64:
            return None
        try:
            raw = base64.b64decode(image_b64)
            image = Image.open(io.BytesIO(raw))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            return image
        except Exception:
            logger.warning("Could not decode pending OCR image for integrity checks", exc_info=True)
            return None

    def _load_pending_image(self, chat_id: int) -> Optional[Image.Image]:
        pending = self.pending_saves.get(chat_id) or {}
        return self._decode_image_b64(pending.get("image_b64"))

    def _web_review_enabled(self) -> bool:
        return bool(self.db) and (os.getenv("OCR_ENABLE_WEB_REVIEW", "1").strip().lower() not in {"0", "false", "no", "off"})

    def _telegram_auto_web_review_enabled(self) -> bool:
        configured = self.config.get("telegram_auto_web_review")
        if configured is not None:
            return bool(configured) and self._web_review_enabled()
        env_value = os.getenv("OCR_TELEGRAM_AUTO_WEB_REVIEW", "1").strip().lower()
        return self._web_review_enabled() and env_value not in {"0", "false", "no", "off"}

    def _telegram_review_max_pages(self) -> int:
        configured = self.config.get("telegram_review_max_pages")
        if configured is None:
            configured = os.getenv("OCR_TELEGRAM_REVIEW_MAX_PAGES", "3")
        try:
            return max(1, min(5, int(configured)))
        except (TypeError, ValueError):
            return 3

    def _review_workspace_url(self, session_id: Any) -> str:
        base_url = (os.getenv("APP_URL") or "https://sam.chat").rstrip("/")
        return f"{base_url}/registration-review/{session_id}"

    def _build_web_review_validation(self, extraction) -> Dict[str, Any]:
        issues: List[Dict[str, str]] = []
        header_ok, _ = self._validate_required_team_header(extraction)
        players = list(getattr(extraction, "players", None) or [])
        review_players = sum(1 for player in players if bool(getattr(player, "needs_review", False)))
        low_conf_players = sum(1 for player in players if float(getattr(player, "confidence", 0.0) or 0.0) < 0.7)

        if not header_ok:
            issues.append({"level": "error", "message": "Falta revisar el encabezado del equipo."})
        if not players:
            issues.append({"level": "error", "message": "No se detectaron jugadores en la cédula."})
        if review_players:
            issues.append({"level": "warning", "message": f"{review_players} jugador(es) marcados para revisión."})
        if low_conf_players:
            issues.append({"level": "warning", "message": f"{low_conf_players} jugador(es) con confianza OCR baja."})

        player_rows = []
        for idx, player in enumerate(players, 1):
            row_issues: List[str] = []
            if not (getattr(player, "name", None) or "").strip():
                row_issues.append("Nombre vacío")
            if not (getattr(player, "birth_date", None) or "").strip():
                row_issues.append("Falta fecha")
            curp = (getattr(player, "curp", None) or "").strip()
            if not curp:
                row_issues.append("Falta CURP")
            elif len(curp) != 18:
                row_issues.append("CURP incompleto")
            if bool(getattr(player, "needs_review", False)):
                row_issues.append("Marcado para revisión")
            if float(getattr(player, "confidence", 0.0) or 0.0) < 0.7:
                row_issues.append("Confianza baja")
            player_rows.append(
                {
                    "index": idx,
                    "confidence": float(getattr(player, "confidence", 0.0) or 0.0),
                    "needs_review": bool(getattr(player, "needs_review", False)),
                    "issues": row_issues,
                }
            )

        return {
            "needs_review": any(item["level"] == "error" for item in issues) or review_players > 0,
            "needs_human_review": any(item["level"] == "error" for item in issues) or review_players > 0,
            "issue_count": len(issues),
            "player_count": len(players),
            "review_player_count": review_players,
            "low_confidence_player_count": low_conf_players,
            "issues": issues,
            "player_rows": player_rows,
        }

    def _build_web_review_layout(self, extraction) -> Dict[str, Any]:
        overlays: List[Dict[str, Any]] = []
        player_page_map: Dict[str, int] = {}
        for idx, player in enumerate(list(getattr(extraction, "players", None) or []), 1):
            player_page_map[str(idx)] = 1
            photo_region = getattr(player, "photo_region", None)
            if not photo_region:
                continue
            overlays.append(
                {
                    "label": f"J{idx} foto",
                    "kind": "photo",
                    "player_index": idx,
                    "page_index": 1,
                    "x": int(getattr(photo_region, "x", 0) or 0),
                    "y": int(getattr(photo_region, "y", 0) or 0),
                    "width": int(getattr(photo_region, "width", 0) or 0),
                    "height": int(getattr(photo_region, "height", 0) or 0),
                }
            )
        return {"pages": {"1": overlays}, "player_page_map": player_page_map}

    def _merge_review_team_fields(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        for key in ("name", "category", "gender", "league", "municipality", "state"):
            current = (merged.get(key) or "").strip() if isinstance(merged.get(key), str) else merged.get(key)
            new_value = (incoming.get(key) or "").strip() if isinstance(incoming.get(key), str) else incoming.get(key)
            if key == "name" and current and str(current).lower() == "unknown team":
                current = None
            if not current and new_value:
                merged[key] = new_value
        merged["confidence"] = max(float(base.get("confidence") or 0.0), float(incoming.get("confidence") or 0.0))
        return merged

    def _merge_review_manager_fields(self, base: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not base and not incoming:
            return None
        merged = dict(base or {})
        for key in ("name", "role", "phone", "email"):
            current = (merged.get(key) or "").strip() if isinstance(merged.get(key), str) else merged.get(key)
            new_value = (incoming or {}).get(key)
            if isinstance(new_value, str):
                new_value = new_value.strip()
            if not current and new_value:
                merged[key] = new_value
        merged["confidence"] = max(float((base or {}).get("confidence") or 0.0), float((incoming or {}).get("confidence") or 0.0))
        return merged if any(merged.get(k) for k in ("name", "phone", "email")) else None

    @staticmethod
    def _extend_player_page_map(
        existing: Optional[Dict[str, int]],
        *,
        total_players: int,
        appended_page_index: int,
    ) -> Dict[str, int]:
        player_page_map = dict(existing or {})
        for index in range(1, total_players + 1):
            player_page_map.setdefault(
                str(index),
                1 if index <= 8 else appended_page_index,
            )
        return player_page_map

    def _build_review_validation_from_payload(self, extraction_payload: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[Dict[str, str]] = []
        players = list(extraction_payload.get("players") or [])
        team = extraction_payload.get("team") or {}
        team_name = (team.get("name") or "").strip()
        if not team_name or team_name.lower() == "unknown team":
            issues.append({"level": "error", "message": "Falta revisar el encabezado del equipo."})
        if not players:
            issues.append({"level": "error", "message": "No se detectaron jugadores en la cédula."})

        review_players = 0
        low_conf_players = 0
        player_rows = []
        for idx, player in enumerate(players, 1):
            row_issues: List[str] = []
            confidence = float(player.get("confidence") or 0.0)
            if not (player.get("name") or "").strip():
                row_issues.append("Nombre vacío")
            if not (player.get("birth_date") or "").strip():
                row_issues.append("Falta fecha")
            curp = (player.get("curp") or "").strip()
            if not curp:
                row_issues.append("Falta CURP")
            elif len(curp) != 18:
                row_issues.append("CURP incompleto")
            if bool(player.get("needs_review")):
                row_issues.append("Marcado para revisión")
                review_players += 1
            if confidence < 0.7:
                row_issues.append("Confianza baja")
                low_conf_players += 1
            player_rows.append(
                {
                    "index": idx,
                    "confidence": confidence,
                    "needs_review": bool(player.get("needs_review")),
                    "issues": row_issues,
                }
            )
        if review_players:
            issues.append({"level": "warning", "message": f"{review_players} jugador(es) marcados para revisión."})
        if low_conf_players:
            issues.append({"level": "warning", "message": f"{low_conf_players} jugador(es) con confianza OCR baja."})

        return {
            "needs_review": any(item["level"] == "error" for item in issues) or review_players > 0,
            "needs_human_review": any(item["level"] == "error" for item in issues) or review_players > 0,
            "issue_count": len(issues),
            "player_count": len(players),
            "review_player_count": review_players,
            "low_confidence_player_count": low_conf_players,
            "issues": issues,
            "player_rows": player_rows,
        }

    async def _append_back_photo_to_review_session(
        self,
        *,
        review_session_id: str,
        optimized_bytes: bytes,
        extraction,
        raw_payload: Optional[Dict[str, Any]],
        provider: str,
    ) -> Tuple[bool, str]:
        if not self.db:
            return False, "No hay conexión a BD para actualizar la revisión web."
        try:
            from devnous.copa_telmex.models import (
                RegistrationPageAppendAttempt,
                RegistrationReviewAsset,
                RegistrationReviewDraft,
                RegistrationReviewSession,
            )

            async with self.db() as session:
                review_session = await session.get(RegistrationReviewSession, UUID(review_session_id))
                if not review_session:
                    return False, "La sesión web de revisión ya no existe."

                assets_result = await session.execute(
                    select(RegistrationReviewAsset)
                    .where(RegistrationReviewAsset.session_id == review_session.id)
                    .order_by(RegistrationReviewAsset.page_index.asc())
                )
                assets = list(assets_result.scalars().all())
                next_page_index = (assets[-1].page_index if assets else 0) + 1
                image_hash = compute_sha256_hex(optimized_bytes)
                page_append_request_id = uuid5(
                    NAMESPACE_URL,
                    (
                        f"samchat:page-append:{review_session.id}:"
                        f"{image_hash}"
                    ),
                )
                review_dir = (
                    self.photos_base_dir
                    / "review_sessions"
                    / str(review_session.id)
                    / f"append-{page_append_request_id}"
                )
                review_dir.mkdir(parents=True, exist_ok=True)
                image_path = review_dir / f"page-{next_page_index:02d}.jpg"
                image_path.write_bytes(optimized_bytes)

                image = Image.open(io.BytesIO(optimized_bytes))
                stored_assets = [
                    {
                        "page_index": next_page_index,
                        "image_path": str(image_path),
                        "sha256": image_hash,
                        "width": int(image.width),
                        "height": int(image.height),
                    }
                ]

                draft_result = await session.execute(
                    select(RegistrationReviewDraft)
                    .where(RegistrationReviewDraft.session_id == review_session.id)
                    .order_by(RegistrationReviewDraft.draft_version.desc())
                    .limit(1)
                )
                draft = draft_result.scalar_one_or_none()
                if draft is None:
                    return False, "La sesión web no tiene draft para actualizar."

                page_append_request_id = uuid5(
                    NAMESPACE_URL,
                    (
                        f"samchat:page-append:{review_session.id}:"
                        f"{draft.content_hash}:{image_hash}"
                    ),
                )
                prior_result = await session.execute(
                    select(RegistrationPageAppendAttempt)
                    .options(
                        selectinload(RegistrationPageAppendAttempt.decision)
                    )
                    .where(
                        RegistrationPageAppendAttempt.page_append_request_id
                        == page_append_request_id
                    )
                )
                prior_attempt = prior_result.scalar_one_or_none()
                if prior_attempt is not None and prior_attempt.decision is not None:
                    accepted = (
                        prior_attempt.decision.decision
                        == "ACCEPT_NON_CONFLICTING_PAGE_APPEND"
                    )
                    return (
                        accepted,
                        self._review_workspace_url(review_session_id)
                        if accepted
                        else "La composición de páginas requiere revisión web.",
                    )

                base_extraction = dict(draft.review_edits or draft.extraction or {})
                base_players = list(base_extraction.get("players") or [])
                incoming_payload = extraction.model_dump(mode="json")
                merged_payload = None
                combined_raw_payload = None
                if self.openai_key and provider == "openai":
                    try:
                        image_b64_values: List[str] = []
                        for asset in assets:
                            existing_bytes = Path(asset.image_path).read_bytes()
                            image_b64_values.append(base64.b64encode(existing_bytes).decode("utf-8"))
                        image_b64_values.append(base64.b64encode(optimized_bytes).decode("utf-8"))
                        from devnous.agents.ocr_schemas import (
                            RegistrationFormExtraction,
                        )

                        combined_raw_payload = self._normalize_openai_registration_payload(
                            await self._call_openai_vision_multi(image_b64_values)
                        )
                        combined_extraction = RegistrationFormExtraction.model_validate(combined_raw_payload)
                        merged_payload = combined_extraction.model_dump(mode="json")
                        logger.info(
                            "✅ Multi-page OpenAI registration extraction completed: players=%s confidence=%s",
                            len(merged_payload.get("players") or []),
                            merged_payload.get("overall_confidence"),
                        )
                    except Exception:
                        logger.warning(
                            "Multi-page OpenAI extraction failed; falling back to page merge",
                            exc_info=True,
                        )

                if merged_payload is None:
                    merged_payload = dict(base_extraction)
                    merged_payload["team"] = self._merge_review_team_fields(
                        base_extraction.get("team") or {},
                        incoming_payload.get("team") or {},
                    )
                    merged_payload["manager"] = self._merge_review_manager_fields(
                        base_extraction.get("manager"),
                        incoming_payload.get("manager"),
                    )
                    merged_payload["players"] = base_players + list(incoming_payload.get("players") or [])
                    merged_payload["overall_confidence"] = max(
                        float(base_extraction.get("overall_confidence") or 0.0),
                        float(incoming_payload.get("overall_confidence") or 0.0),
                    )

                layout_regions = dict(draft.layout_regions or {"pages": {}, "player_page_map": {}})
                pages = dict(layout_regions.get("pages") or {})
                total_players = len(list(merged_payload.get("players") or []))
                player_page_map = self._extend_player_page_map(
                    layout_regions.get("player_page_map"),
                    total_players=total_players,
                    appended_page_index=next_page_index,
                )
                pages.setdefault(str(next_page_index), [])
                layout_regions["pages"] = pages
                layout_regions["player_page_map"] = player_page_map

                validation = self._build_review_validation_from_payload(merged_payload)
                page_append_request_id = uuid5(
                    NAMESPACE_URL,
                    (
                        f"samchat:page-append:{review_session.id}:"
                        f"{draft.content_hash}:{image_hash}"
                    ),
                )
                proposed_raw = {
                    "provider": review_session.provider,
                    "page_count": next_page_index,
                    "pages": list((draft.ocr_raw or {}).get("pages") or [])
                    + [
                        {
                            "page_index": next_page_index,
                            "raw": raw_payload,
                            "combined_raw": combined_raw_payload,
                            "player_count": len(
                                incoming_payload.get("players") or []
                            ),
                            "side": "back",
                        }
                    ],
                }
                current_manifest = existing_page_manifest(
                    session_id=review_session.id,
                    base_draft=draft,
                    assets=assets,
                )
                (
                    append_ocr_run_id,
                    operation_id,
                    staged_assets,
                    appended_manifest,
                ) = staged_page_manifest(
                    session_id=review_session.id,
                    base_draft=draft,
                    page_append_request_id=page_append_request_id,
                    stored_assets=stored_assets,
                )
                composed_manifest = proposed_page_manifest(
                    current_manifest, appended_manifest
                )
                proposed_values = build_successor_values(
                    draft,
                    extraction=merged_payload,
                    review_edits=merged_payload,
                    ocr_raw=proposed_raw,
                    layout_regions=layout_regions,
                    page_manifest_hash=page_composition_sha256_binding(
                        composed_manifest
                    ),
                    overall_confidence=float(
                        merged_payload.get("overall_confidence") or 0.0
                    ),
                    validation=validation,
                    needs_review=bool(validation.get("needs_review")),
                )
                incoming_layout = {
                    "pages": {str(next_page_index): []},
                    "player_page_map": {
                        str(slot): next_page_index
                        for slot in range(
                            1, len(incoming_payload.get("players") or []) + 1
                        )
                    },
                }
                attempt = build_page_append_attempt(
                    session_id=review_session.id,
                    page_append_request_id=page_append_request_id,
                    base_draft=draft,
                    provider=provider,
                    prompt_config_hash=page_composition_sha256_binding(
                        {
                            "provider": provider,
                            "pipeline": "telegram-back-page-v1",
                        }
                    ),
                    append_ocr_run_id=append_ocr_run_id,
                    operation_id=operation_id,
                    existing_manifest=current_manifest,
                    appended_manifest=appended_manifest,
                    staged_assets=staged_assets,
                    incoming_extraction=incoming_payload,
                    incoming_ocr_raw=raw_payload or {},
                    incoming_layout_regions=incoming_layout,
                    proposed_values=proposed_values,
                )
                successor_draft_id = uuid4()
                gate_response = await RegistrationGovernanceClient.from_environment().adjudicate_page_composition(
                    build_page_composition_gate_request(
                        tenant_id=os.getenv("ZAUBERN_TENANT_ID", "samchat-prod"),
                        tournament_slug=review_session.tournament_slug,
                        attempt=attempt,
                        current_draft=draft,
                        base_extraction=base_extraction,
                        successor_draft_id=successor_draft_id,
                    )
                )
                event = gate_response.get("page_composition_decision") or {}
                receipt = gate_response.get("page_composition_receipt") or {}
                if (
                    receipt.get("verified") is not True
                    or not event.get("decision_id")
                    or not receipt.get("receipt_id")
                ):
                    raise RegistrationGovernanceDenied(
                        "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
                        "Zaubern returned an incomplete page composition adjudication",
                    )
                session.add(attempt)
                if gate_response.get("successor_authorized") is True:
                    successor = await append_draft_version(
                        session,
                        review_session,
                        mutation_type="pages_appended",
                        actor_id=review_session.telegram_user_id,
                        expected_draft=draft,
                        operation_id=attempt.operation_id,
                        new_draft_id=successor_draft_id,
                        parent_authorization=page_composition_parent_authorization(
                            gate_response
                        ),
                        extraction=attempt.proposed_extraction,
                        review_edits=attempt.proposed_extraction,
                        ocr_raw=attempt.proposed_ocr_raw,
                        layout_regions=attempt.proposed_layout_regions,
                        page_manifest_hash=attempt.proposed_page_manifest_hash,
                        overall_confidence=float(
                            (attempt.proposed_extraction or {}).get(
                                "overall_confidence"
                            )
                            or 0.0
                        ),
                        validation=attempt.proposed_validation,
                        needs_review=bool(
                            (attempt.proposed_validation or {}).get(
                                "needs_review"
                            )
                        ),
                    )
                    if successor.content_hash != attempt.proposed_snapshot_hash:
                        raise RegistrationGovernanceDenied(
                            "PAGE_COMPOSITION_SUCCESSOR_HASH_MISMATCH",
                            "REG-S02 successor does not match the adjudicated composition",
                        )
                    for asset in admitted_asset_rows(
                        attempt=attempt,
                        successor_draft_id=successor_draft_id,
                        response=gate_response,
                    ):
                        session.add(asset)
                session.add(
                    build_page_composition_decision_row(
                        attempt=attempt,
                        successor_draft_id=successor_draft_id,
                        response=gate_response,
                    )
                )
                review_session.status = "ready"

                await session.commit()

                if gate_response.get("successor_authorized") is not True:
                    return (
                        False,
                        "La composición de páginas requiere revisión web.",
                    )

            return True, self._review_workspace_url(review_session_id)
        except Exception as exc:
            logger.error("Failed to append back photo to review session: %s", exc, exc_info=True)
            return False, f"No pude agregar la vuelta a la sesión web: {exc}"

    async def _create_web_review_session(
        self,
        *,
        chat_id: int,
        user_id: Optional[int],
        provider: str,
        extraction: Any,
        raw_payload: Any,
        image: Image.Image,
        tournament_slug: Optional[str] = None,
        tournament_selected: Optional[str] = None,
        category_guess: Optional[str] = None,
        category_selected: Optional[str] = None,
        expect_back_photo: bool = True,
    ) -> Tuple[bool, str]:
        if not self.db:
            return False, "No hay conexión de base de datos para crear la sesión web."
        if extraction is None or image is None:
            return False, "No encuentro la extracción o imagen OCR para enviarla a revisión web."
        try:
            from devnous.agents.ocr_schemas import RegistrationFormExtraction
            from devnous.copa_telmex.models import (
                RegistrationReviewAsset,
                RegistrationReviewDraft,
                RegistrationReviewSession,
            )

            extraction = RegistrationFormExtraction.model_validate(extraction)
            if category_selected:
                extraction.team.category = str(category_selected)
            elif category_guess and not getattr(extraction.team, "category", None):
                extraction.team.category = str(category_guess)
            extraction_payload = extraction.model_dump(mode="json")
            validation = self._build_web_review_validation(extraction)
            layout_regions = self._build_web_review_layout(extraction)

            review_session_id: Optional[str] = None
            async with self.db() as session:
                review_session = RegistrationReviewSession(
                    status="ready",
                    source="telegram",
                    provider=provider,
                    tournament_slug=(tournament_selected or tournament_slug or None),
                    telegram_chat_id=chat_id,
                    telegram_user_id=user_id,
                )
                session.add(review_session)
                await session.flush()

                review_dir = self.photos_base_dir / "review_sessions" / str(review_session.id)
                review_dir.mkdir(parents=True, exist_ok=True)
                image_path = review_dir / "telegram-front.jpg"
                image.convert("RGB").save(image_path, format="JPEG", quality=95)

                asset = RegistrationReviewAsset(
                    session_id=review_session.id,
                    page_index=1,
                    image_path=str(image_path),
                    sha256=compute_sha256_hex(image_path.read_bytes()),
                    width=int(image.width),
                    height=int(image.height),
                )
                session.add(asset)
                initial_draft = await append_draft_version(
                    session,
                    review_session,
                    mutation_type="telegram_upload_created",
                    actor_id=user_id,
                    ocr_raw=raw_payload
                    if isinstance(raw_payload, dict)
                    else {"raw": raw_payload},
                    extraction=extraction_payload,
                    validation=validation,
                    review_edits=extraction_payload,
                    layout_regions=layout_regions,
                    overall_confidence=float(
                        getattr(extraction, "overall_confidence", 0.0) or 0.0
                    ),
                    needs_review=bool(validation.get("needs_review")),
                )
                asset.admitted_draft_id = initial_draft.id
                asset.source_base_draft_id = initial_draft.id
                asset.source_base_content_hash = initial_draft.content_hash
                asset.source_ocr_run_ref = f"initial:{initial_draft.id}"
                asset.admission_operation_id = (
                    initial_draft.mutation_operation_id
                )
                asset.admission_decision_id = (
                    initial_draft.mutation_decision_id
                )
                asset.admission_receipt_id = (
                    initial_draft.mutation_receipt_id
                )
                await session.commit()
                review_session_id = str(review_session.id)

            if review_session_id and expect_back_photo:
                self.pending_back_photos[chat_id] = {
                    "review_session_id": review_session_id,
                    "provider": provider,
                    "page_count": 1,
                    "max_pages": self._telegram_review_max_pages(),
                }
            return True, self._review_workspace_url(review_session.id)
        except Exception as exc:
            logger.error("Failed to create web review session from pending OCR: %s", exc, exc_info=True)
            return False, f"No pude crear la sesión web de revisión: {exc}"

    async def _create_web_review_session_from_pending(
        self,
        chat_id: int,
        provider: str,
        *,
        expect_back_photo: bool = True,
    ) -> Tuple[bool, str]:
        pending = self.pending_saves.get(chat_id) or {}
        extraction_dict = pending.get(f"{provider}_extraction")
        image = self._load_pending_image(chat_id)
        if not extraction_dict or image is None:
            return False, "No encuentro la imagen OCR pendiente para enviarla a revisión web."
        return await self._create_web_review_session(
            chat_id=chat_id,
            user_id=pending.get("user_id"),
            provider=provider,
            extraction=extraction_dict,
            raw_payload=pending.get(provider),
            image=image,
            tournament_slug=pending.get("tournament_slug"),
            tournament_selected=pending.get("tournament_selected"),
            category_guess=pending.get("category_guess"),
            category_selected=pending.get("category_selected"),
            expect_back_photo=expect_back_photo,
        )

    async def _stage_pending_registration_review(
        self,
        chat_id: int,
        provider: str,
    ) -> Tuple[bool, str]:
        """Route a legacy Telegram confirmation into governed precapture only."""
        if not self._web_review_enabled():
            return (
                False,
                f"{REGS08_GOVERNED_REVIEW_UNAVAILABLE}: "
                "la precaptura gobernada no está disponible; no se creó ningún equipo o jugador.",
            )
        ok, result = await self._create_web_review_session_from_pending(
            chat_id,
            provider,
            expect_back_photo=True,
        )
        if not ok:
            return (
                False,
                f"{REGS08_GOVERNED_REVIEW_UNAVAILABLE}: {result}",
            )
        return True, result

    def _team_storage_key(self, team_id: Any) -> str:
        return str(team_id)

    def _save_roster_image(self, *, team_id: Any, image: Image.Image, side: str) -> str:
        photos_dir = self.photos_base_dir / "rosters" / self._team_storage_key(team_id)
        photos_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        filepath = photos_dir / f"{side}_{timestamp}.jpg"
        image.save(filepath, format="JPEG", quality=95)
        return str(filepath)

    def _load_existing_photo_fingerprints(self, players: List[Any]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for player in players:
            sha256_value = getattr(player, "photo_sha256", None)
            ahash_value = getattr(player, "photo_ahash", None)
            photo_path = getattr(player, "photo_path", None)

            if (not sha256_value or not ahash_value) and photo_path:
                path = Path(photo_path)
                if path.exists():
                    try:
                        raw = path.read_bytes()
                        image = Image.open(io.BytesIO(raw))
                        sha256_value = sha256_value or compute_sha256_hex(raw)
                        ahash_value = ahash_value or average_hash_hex(image)
                        if not getattr(player, "photo_sha256", None):
                            player.photo_sha256 = sha256_value
                        if not getattr(player, "photo_ahash", None):
                            player.photo_ahash = ahash_value
                    except Exception:
                        logger.warning("Could not hydrate existing player photo fingerprint: %s", photo_path, exc_info=True)

            if sha256_value or ahash_value:
                records.append(
                    {
                        "player_id": str(getattr(player, "id", "")),
                        "player_name": getattr(player, "full_name", None) or "Jugador existente",
                        "photo_sha256": sha256_value,
                        "photo_ahash": ahash_value,
                    }
                )
        return records

    def _build_player_photo_artifacts(
        self,
        *,
        team_id: Any,
        extraction: Any,
        image: Optional[Image.Image],
        side: str,
        existing_photo_records: List[Dict[str, Any]],
    ) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, int]]:
        artifacts: Dict[int, Dict[str, Any]] = {}
        stats = {
            "photo_saved_count": 0,
            "photo_duplicate_count": 0,
            "photo_unclear_count": 0,
        }
        if image is None:
            return artifacts, stats

        photos_dir = self.photos_base_dir / "players" / self._team_storage_key(team_id)
        photos_dir.mkdir(parents=True, exist_ok=True)

        players = list(getattr(extraction, "players", []) or [])
        normalized_image, normalization = normalize_ctt_template_image(image)
        for idx, player in enumerate(players, 1):
            try:
                crop = crop_player_photo(
                    image=normalized_image,
                    photo_region=getattr(player, "photo_region", None),
                    player_index=idx - 1,
                    total_players=len(players),
                    side=side,
                )
                reasons: List[str] = []
                if not image_has_photo_like_content(crop):
                    reasons.append("foto_no_detectada_con_claridad")
                    stats["photo_unclear_count"] += 1

                buffer = io.BytesIO()
                crop.save(buffer, format="JPEG", quality=90)
                raw = buffer.getvalue()
                sha256_value = compute_sha256_hex(raw)
                ahash_value = average_hash_hex(crop)
                filename = (
                    f"{side}_{idx:02d}_{slugify_filename(getattr(player, 'name', None), fallback='jugador')}"
                    f"_{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.jpg"
                )
                filepath = photos_dir / filename
                filepath.write_bytes(raw)
                artifacts[idx] = {
                    "photo_path": str(filepath),
                    "photo_sha256": sha256_value,
                    "photo_ahash": ahash_value,
                    "reasons": reasons,
                    "photo_normalization": normalization,
                }
                stats["photo_saved_count"] += 1
            except Exception:
                logger.warning("Could not extract player photo for idx=%s", idx, exc_info=True)

        indexes = sorted(artifacts.keys())
        for pos, left_idx in enumerate(indexes):
            left = artifacts[left_idx]
            for right_idx in indexes[pos + 1:]:
                right = artifacts[right_idx]
                if hashes_look_duplicate(
                    sha256_left=left.get("photo_sha256"),
                    sha256_right=right.get("photo_sha256"),
                    ahash_left=left.get("photo_ahash"),
                    ahash_right=right.get("photo_ahash"),
                    max_distance=self.photo_duplicate_distance,
                ):
                    left["reasons"].append("foto_repetida_en_misma_cedula")
                    right["reasons"].append("foto_repetida_en_misma_cedula")
                    stats["photo_duplicate_count"] += 1

        for idx in indexes:
            artifact = artifacts[idx]
            for existing in existing_photo_records:
                if hashes_look_duplicate(
                    sha256_left=artifact.get("photo_sha256"),
                    sha256_right=existing.get("photo_sha256"),
                    ahash_left=artifact.get("photo_ahash"),
                    ahash_right=existing.get("photo_ahash"),
                    max_distance=self.photo_duplicate_distance,
                ):
                    artifact["reasons"].append("foto_repetida_contra_jugador_existente")
                    artifact["duplicate_of"] = existing.get("player_name")
                    stats["photo_duplicate_count"] += 1
                    break

        for artifact in artifacts.values():
            artifact["reasons"] = sorted(set(artifact.get("reasons") or []))

        return artifacts, stats

    def _prepare_extraction_integrity(
        self,
        *,
        team_id: Any,
        extraction: Any,
        image: Optional[Image.Image],
        side: str,
        existing_players: List[Any],
    ) -> Dict[str, Any]:
        existing_photo_records = self._load_existing_photo_fingerprints(existing_players)
        photo_artifacts, photo_stats = self._build_player_photo_artifacts(
            team_id=team_id,
            extraction=extraction,
            image=image,
            side=side,
            existing_photo_records=existing_photo_records,
        )

        flagged_names = 0
        flagged_photos = 0
        integrity_notes: Dict[int, List[str]] = {}
        players = list(getattr(extraction, "players", []) or [])
        for idx, player in enumerate(players, 1):
            reasons: List[str] = []
            name_result = evaluate_player_identity_integrity(
                getattr(player, "name", None),
                birth_date=getattr(player, "birth_date", None),
                curp=getattr(player, "curp", None),
                confidence=getattr(player, "confidence", None),
                validator=self.validator,
            )
            reasons.extend(name_result.reasons)
            if name_result.reasons:
                flagged_names += 1

            photo_result = photo_artifacts.get(idx) or {}
            photo_reasons = list(photo_result.get("reasons") or [])
            reasons.extend(photo_reasons)
            if photo_reasons:
                flagged_photos += 1

            if reasons:
                player.needs_review = True
                integrity_notes[idx] = sorted(set(reasons))

        return {
            "photo_artifacts": photo_artifacts,
            "integrity_notes": integrity_notes,
            "flagged_name_count": flagged_names,
            "flagged_photo_count": flagged_photos,
            "photo_stats": photo_stats,
        }

    async def handle(self, message):
        """Handle operations messages"""
        text = message.text.lower()
        chat_id = message.chat_id

        # If we're waiting for a manual category selection for a pending OCR save, consume next text.
        pending_save = self.pending_saves.get(chat_id)
        if pending_save and pending_save.get("awaiting_manual_category") and not message.text.strip().startswith("/"):
            cat = message.text.strip()
            pending_save["awaiting_manual_category"] = False
            pending_save["category_selected"] = cat
            pending_save["category_confidence"] = 1.0
            self.pending_saves[chat_id] = pending_save
            provider = (pending_save.get("selected_provider") or "").strip().lower()
            tournament_selected = (pending_save.get("tournament_selected") or "").strip()
            if provider in ("anthropic", "openai", "local") and tournament_selected:
                extraction_dict = pending_save.get(f"{provider}_extraction")
                if extraction_dict:
                    try:
                        from devnous.agents.ocr_schemas import (
                            RegistrationFormExtraction,
                        )

                        extraction = RegistrationFormExtraction.model_validate(extraction_dict)
                        extraction.team.category = cat
                        ok, msg = await self._stage_pending_registration_review(
                            chat_id,
                            provider,
                        )
                        if ok:
                            self.pending_saves.pop(chat_id, None)
                            return (
                                f"✅ Categoría confirmada: *{cat}*.\n"
                                f"✅ Precaptura gobernada creada ({provider}).\n{msg}\n"
                                "Revísala en la plataforma antes del commit final."
                            )
                        return f"✅ Categoría confirmada: *{cat}*.\n⛔ No se creó la precaptura.\n{msg}"
                    except Exception as e:
                        logger.error(f"manual category review staging failed: {e}", exc_info=True)
                        return (
                            f"✅ Categoría confirmada: *{cat}*.\n"
                            "⛔ No pude crear la precaptura gobernada; no se escribió estado final."
                        )
            return (
                f"✅ Categoría guardada para este registro: *{cat}*.\n"
                "Ahora solicita nuevamente crear la precaptura."
            )

        # If we're waiting for a manual edit value, consume the next text message.
        if chat_id in self.pending_edits and not message.photo:
            state = self.pending_edits[chat_id]
            if state.get("waiting_value"):
                return await self._apply_pending_edit(chat_id, message.user_id, message.text)

        # AI workspace (commands or conversational) should run before generic
        # correction handlers, otherwise "actualiza ..." is treated as OCR correction.
        if not message.photo:
            ai_response = await self._handle_ai_workspace_commands(message)
            if ai_response is not None:
                return ai_response

        # Conversational write actions (e.g., "dar de alta jugador ...").
        if not message.photo and not message.text.strip().startswith("/"):
            action_resp = await self._handle_conversational_actions(
                chat_id=chat_id,
                user_id=message.user_id,
                text=message.text,
            )
            if action_resp is not None:
                return action_resp

        # Freeform corrections: allow natural language updates for any captured field.
        if not message.photo and not message.text.strip().startswith("/"):
            corrected = await self._apply_freeform_corrections(
                chat_id=chat_id,
                user_id=message.user_id,
                text=message.text,
            )
            if corrected is not None:
                return corrected

        # Conversational queries (read-only): e.g. "lista todos los jugadores del equipo"
        if not message.photo and not message.text.strip().startswith("/"):
            conversational = await self._handle_conversational_query(chat_id=chat_id, text=message.text)
            if conversational is not None:
                return conversational

        # Check if this is an OCR registration (photo message)
        if message.photo and self.ocr_enabled:
            return await self.process_ocr_registration(message)
        elif 'registro_ocr' in text and message.photo and self.ocr_enabled:
            return await self.process_ocr_registration(message)
        elif text in ("/corregir", "corregir", "editar", "/editar") or "corregir" in text:
            return await self._start_corrections(chat_id)
        elif 'registrar equipo' in text:
            return await self.register_team(message)
        elif 'ver equipos' in text:
            return await self.list_teams()
        elif 'programar partido' in text:
            return await self.schedule_match(message)
        elif 'calendario' in text:
            return await self.show_calendar()
        else:
            return self.get_operations_help()

    async def _handle_ai_workspace_commands(self, message) -> Optional[str]:
        """Handle AI workspace commands for entity/national folders and reports."""
        original = (message.text or "").strip()
        lowered = original.lower()
        tournament_slug = self._get_ai_tournament_slug()

        # Conversational aliases (no slash commands required)
        if lowered.startswith("ayuda ai") or lowered.startswith("help ai"):
            return self._get_ai_help_message()
        if "inicializa" in lowered and "workspace" in lowered:
            guessed = self._extract_after_keywords(
                original,
                ("para torneo", "torneo", "para"),
            )
            if guessed:
                tournament_slug = self._slugify_local(guessed)
            paths = self.ai_workspace.bootstrap_tournament(tournament_slug)
            return (
                "✅ Workspace AI inicializado\n"
                f"🏷️ Torneo: {tournament_slug}\n"
                f"📁 Base: {paths['tournament_dir']}"
            )
        if "crear entidad" in lowered or "crea entidad" in lowered or "carpeta entidad" in lowered:
            entity_name = (
                self._extract_after_keywords(original, ("entidad",))
                .replace("carpeta", "")
                .replace("crear", "")
                .replace("crea", "")
                .strip()
            )
            if entity_name:
                self.ai_workspace.bootstrap_tournament(tournament_slug)
                result = self.ai_workspace.upsert_entity(
                    tournament_slug=tournament_slug,
                    entity_ops=EntityOperationsRecord(entity_name=entity_name),
                    entity_fin=EntityFinanceRecord(entity_name=entity_name),
                )
                return (
                    "✅ Entidad creada/actualizada\n"
                    f"🏷️ {entity_name}\n"
                    f"📁 {result['entity_dir']}"
                )
        parsed_batch = self._parse_batch_natural_updates(original)
        if parsed_batch:
            if parsed_batch["scope"] == "entity":
                return self._apply_entity_batch_updates(
                    tournament_slug=tournament_slug,
                    entity_name=parsed_batch["entity_name"],
                    updates=parsed_batch["updates"],
                )
            if parsed_batch["scope"] == "national":
                return self._apply_national_batch_updates(
                    tournament_slug=tournament_slug,
                    updates=parsed_batch["updates"],
                )
        parsed_multi_entity_batch = self._parse_multi_entity_batch_updates(original)
        if parsed_multi_entity_batch:
            return self._apply_multi_entity_batch_updates(
                tournament_slug=tournament_slug,
                batch=parsed_multi_entity_batch,
            )
        parsed_entity_update = self._parse_conversational_entity_update(original)
        if parsed_entity_update:
            return self._set_entity_ai_field(
                tournament_slug=tournament_slug,
                entity_name=parsed_entity_update["entity_name"],
                domain=parsed_entity_update["domain"],
                field_name=parsed_entity_update["field_name"],
                raw_value=parsed_entity_update["raw_value"],
            )
        parsed_entity_natural = self._parse_natural_entity_update(original)
        if parsed_entity_natural:
            return self._set_entity_ai_field(
                tournament_slug=tournament_slug,
                entity_name=parsed_entity_natural["entity_name"],
                domain=parsed_entity_natural["domain"],
                field_name=parsed_entity_natural["field_name"],
                raw_value=parsed_entity_natural["raw_value"],
            )
        parsed_national_update = self._parse_conversational_national_update(original)
        if parsed_national_update:
            return self._set_national_ai_field(
                tournament_slug=tournament_slug,
                domain=parsed_national_update["domain"],
                field_name=parsed_national_update["field_name"],
                raw_value=parsed_national_update["raw_value"],
            )
        parsed_national_natural = self._parse_natural_national_update(original)
        if parsed_national_natural:
            return self._set_national_ai_field(
                tournament_slug=tournament_slug,
                domain=parsed_national_natural["domain"],
                field_name=parsed_national_natural["field_name"],
                raw_value=parsed_national_natural["raw_value"],
            )
        if "genera reporte entidad" in lowered or "generar reporte entidad" in lowered:
            entity_name = self._extract_after_keywords(original, ("entidad",)).strip()
            if entity_name:
                self.ai_workspace.bootstrap_tournament(tournament_slug)
                payload = self._load_entity_payload(tournament_slug, entity_name)
                result = self.ai_workspace.upsert_entity(
                    tournament_slug=tournament_slug,
                    entity_ops=self._entity_ops_record_from_payload(entity_name, payload.get("ops", {})),
                    entity_fin=self._entity_fin_record_from_payload(entity_name, payload.get("fin", {})),
                )
                return (
                    "✅ Reporte de entidad regenerado\n"
                    f"🏷️ {entity_name}\n"
                    f"📝 {result['operations_report']}\n"
                    f"💰 {result['finance_report']}"
                )
        if "genera reporte nacional" in lowered or "generar reporte nacional" in lowered:
            self.ai_workspace.bootstrap_tournament(tournament_slug)
            payload = self._load_national_payload(tournament_slug)
            self.ai_workspace.upsert_national_phase(
                tournament_slug=tournament_slug,
                operations=self._national_ops_record_from_payload(payload.get("ops", {})),
                finance=self._national_fin_record_from_payload(payload.get("fin", {})),
                marketing=self._national_mkt_record_from_payload(payload.get("mkt", {})),
            )
            national_dir = self.ai_workspace.root / self._slugify_local(tournament_slug) / "national"
            return (
                "✅ Reportes nacionales regenerados\n"
                f"📁 {national_dir}"
            )

        if lowered in {"/ai", "/ai_help", "ai"}:
            return self._get_ai_help_message()

        if lowered.startswith("/ai_init"):
            parts = original.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                tournament_slug = self._slugify_local(parts[1].strip())
            paths = self.ai_workspace.bootstrap_tournament(tournament_slug)
            return (
                "✅ Workspace AI inicializado\n"
                f"🏷️ Torneo: {tournament_slug}\n"
                f"📁 Base: {paths['tournament_dir']}\n"
                f"📂 Entidades: {paths['entities_dir']}\n"
                f"📂 Nacional: {paths['national_dir']}"
            )

        if lowered.startswith("/ai_entidad ") or lowered.startswith("crear carpeta entidad "):
            entity_name = original.split(" ", 1)[1].replace("carpeta entidad", "").strip()
            if not entity_name:
                return "⚠️ Uso: /ai_entidad <Nombre de la entidad>"
            self.ai_workspace.bootstrap_tournament(tournament_slug)
            result = self.ai_workspace.upsert_entity(
                tournament_slug=tournament_slug,
                entity_ops=EntityOperationsRecord(entity_name=entity_name),
                entity_fin=EntityFinanceRecord(entity_name=entity_name),
            )
            return (
                "✅ Entidad creada/actualizada\n"
                f"🏷️ {entity_name}\n"
                f"📁 {result['entity_dir']}\n"
                f"📝 {result['operations_report']}\n"
                f"💰 {result['finance_report']}"
            )

        if lowered.startswith("/ai_nacional"):
            self.ai_workspace.bootstrap_tournament(tournament_slug)
            result = self.ai_workspace.upsert_national_phase(
                tournament_slug=tournament_slug,
                operations=NationalOperationsRecord(),
                finance=NationalFinanceRecord(),
                marketing=NationalMarketingRecord(),
            )
            return (
                "✅ Carpeta/reporte nacional actualizados\n"
                f"📁 {result['national_dir']}\n"
                f"🧭 Operaciones: {result['operations_json']}\n"
                f"💰 Finanzas: {result['finance_json']}\n"
                f"📣 Mercadotecnia: {result['marketing_json']}"
            )

        if lowered.startswith("/ai_set_entidad "):
            # /ai_set_entidad entidad|ops|campo|valor  (ops|fin)
            raw = original[len("/ai_set_entidad ") :].strip()
            parts = [p.strip() for p in raw.split("|", 3)]
            if len(parts) != 4:
                return "⚠️ Uso: /ai_set_entidad entidad|ops|campo|valor"
            entity_name, domain, field_name, raw_value = parts
            return self._set_entity_ai_field(
                tournament_slug=tournament_slug,
                entity_name=entity_name,
                domain=domain,
                field_name=field_name,
                raw_value=raw_value,
            )

        if lowered.startswith("/ai_set_nacional "):
            # /ai_set_nacional ops|campo|valor (ops|fin|mkt)
            raw = original[len("/ai_set_nacional ") :].strip()
            parts = [p.strip() for p in raw.split("|", 2)]
            if len(parts) != 3:
                return "⚠️ Uso: /ai_set_nacional ops|campo|valor"
            domain, field_name, raw_value = parts
            return self._set_national_ai_field(
                tournament_slug=tournament_slug,
                domain=domain,
                field_name=field_name,
                raw_value=raw_value,
            )

        if lowered.startswith("/ai_reporte_entidad "):
            entity_name = original[len("/ai_reporte_entidad ") :].strip()
            if not entity_name:
                return "⚠️ Uso: /ai_reporte_entidad <Entidad>"
            self.ai_workspace.bootstrap_tournament(tournament_slug)
            payload = self._load_entity_payload(tournament_slug, entity_name)
            result = self.ai_workspace.upsert_entity(
                tournament_slug=tournament_slug,
                entity_ops=self._entity_ops_record_from_payload(entity_name, payload.get("ops", {})),
                entity_fin=self._entity_fin_record_from_payload(entity_name, payload.get("fin", {})),
            )
            return (
                "✅ Reporte de entidad regenerado\n"
                f"🏷️ {entity_name}\n"
                f"📝 {result['operations_report']}\n"
                f"💰 {result['finance_report']}"
            )

        if lowered.startswith("/ai_reporte_nacional"):
            self.ai_workspace.bootstrap_tournament(tournament_slug)
            payload = self._load_national_payload(tournament_slug)
            self.ai_workspace.upsert_national_phase(
                tournament_slug=tournament_slug,
                operations=self._national_ops_record_from_payload(payload.get("ops", {})),
                finance=self._national_fin_record_from_payload(payload.get("fin", {})),
                marketing=self._national_mkt_record_from_payload(payload.get("mkt", {})),
            )
            national_dir = self.ai_workspace.root / self._slugify_local(tournament_slug) / "national"
            return (
                "✅ Reportes nacionales regenerados\n"
                f"📁 {national_dir}\n"
                f"🧭 {national_dir / 'nacional_operaciones_reporte.md'}\n"
                f"💰 {national_dir / 'nacional_finanzas_reporte.md'}\n"
                f"📣 {national_dir / 'nacional_mercadotecnia_reporte.md'}"
            )

        return None

    def _get_ai_help_message(self) -> str:
        return (
            "🤖 *AI Workspace Torneos*\n\n"
            "Modo conversacional:\n"
            "• inicializa workspace para torneo copa telmex 2026\n"
            "• crea entidad Estado de Mexico\n"
            "• actualiza entidad Estado de Mexico en ops campo ps_owner_name a Martin Zarate\n"
            "• actualiza nacional en fin campo insurance_costs a [{\"concepto\":\"seguro\",\"monto\":10000}]\n"
            "• genera reporte entidad Estado de Mexico\n"
            "• genera reporte nacional\n\n"
            "Comandos:\n"
            "• /ai_init [torneo]\n"
            "• /ai_entidad <Entidad>\n"
            "• /ai_nacional\n"
            "• /ai_set_entidad entidad|ops|campo|valor\n"
            "• /ai_set_nacional ops|campo|valor\n"
            "• /ai_reporte_entidad <Entidad>\n"
            "• /ai_reporte_nacional\n\n"
            "Notas:\n"
            "• Dominio entidad: ops o fin\n"
            "• Dominio nacional: ops, fin o mkt\n"
            "• `valor` puede ser texto, numero, true/false, o JSON ([...], {...])"
        )

    def _extract_after_keywords(self, text: str, keywords: Tuple[str, ...]) -> str:
        lower = text.lower()
        for kw in keywords:
            pos = lower.find(kw)
            if pos >= 0:
                return text[pos + len(kw) :].strip(" :,-")
        return ""

    def _parse_conversational_entity_update(self, text: str) -> Optional[Dict[str, str]]:
        """
        Parse:
        'actualiza entidad <Entidad> en <ops|fin> campo <campo> a <valor>'
        """
        match = re.search(
            r"actualiza\s+entidad\s+(.+?)\s+en\s+(ops|fin)\s+campo\s+([a-zA-Z0-9_]+)\s+a\s+(.+)$",
            text.strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return {
            "entity_name": match.group(1).strip(),
            "domain": match.group(2).strip().lower(),
            "field_name": match.group(3).strip(),
            "raw_value": match.group(4).strip(),
        }

    def _parse_conversational_national_update(self, text: str) -> Optional[Dict[str, str]]:
        """
        Parse:
        'actualiza nacional en <ops|fin|mkt> campo <campo> a <valor>'
        """
        match = re.search(
            r"actualiza\s+nacional\s+en\s+(ops|fin|mkt|marketing)\s+campo\s+([a-zA-Z0-9_]+)\s+a\s+(.+)$",
            text.strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return {
            "domain": match.group(1).strip().lower(),
            "field_name": match.group(2).strip(),
            "raw_value": match.group(3).strip(),
        }

    def _extract_rhs_value(self, text: str) -> Optional[str]:
        for token in (" es ", " fue ", " son ", " será ", ":", "="):
            if token in text:
                value = text.split(token, 1)[1].strip()
                if value:
                    return value
        return None

    def _extract_entity_name_from_text(self, text: str) -> Optional[str]:
        patterns = [
            r"(?:entidad|estado)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s+(?:es|fue|son|sera|será|tiene|con|en)\b",
            r"de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s+(?:es|fue|son|sera|será|tiene|con|en)\b",
            r"(?:entidad|estado)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})$",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                candidate = m.group(1).strip(" .,:;")
                candidate = re.sub(r"\b(ops|fin|mkt|marketing|campo)\b.*$", "", candidate, flags=re.IGNORECASE).strip()
                if candidate:
                    return candidate
        return None

    def _parse_natural_entity_update(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        tl = t.lower()
        if "nacional" in tl:
            return None

        # Explicit natural patterns: "pon a X como <campo> de <entidad>"
        explicit_patterns = [
            (
                r"^\s*pon(?:\s+a)?\s+(.+?)\s+como\s+(?:responsable\s*ps|encargad[oa]\s*ps)\s+de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s*$",
                "ops",
                "ps_owner_name",
                "value_first",
            ),
            (
                r"^\s*pon(?:\s+a)?\s+(.+?)\s+como\s+(?:encargad[oa]|responsable\s+de\s+la\s+entidad)\s+de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s*$",
                "ops",
                "entity_contact_name",
                "value_first",
            ),
            (
                r"^\s*pon(?:\s+el)?\s+telefon[oa]?\s+de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s+(?:a|en)\s+(.+?)\s*$",
                "ops",
                "entity_contact_phone",
                "entity_first",
            ),
            (
                r"^\s*pon(?:\s+el)?\s+(?:correo|email)\s+de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\s+(?:a|en)\s+(.+?)\s*$",
                "ops",
                "entity_contact_email",
                "entity_first",
            ),
        ]
        for pattern, domain, field_name, order in explicit_patterns:
            m = re.match(pattern, t, flags=re.IGNORECASE)
            if not m:
                continue
            if order == "value_first":
                value = m.group(1).strip(" .,:;")
                entity_name = m.group(2).strip(" .,:;")
            else:
                entity_name = m.group(1).strip(" .,:;")
                value = m.group(2).strip(" .,:;")
            return {
                "entity_name": entity_name,
                "domain": domain,
                "field_name": field_name,
                "raw_value": value,
            }

        entity_name = self._extract_entity_name_from_text(t)
        if not entity_name:
            return None

        # Prefer "pon a X como ..." style, fallback to rhs extraction.
        value = None
        m = re.search(r"^\s*pon(?:\s+a)?\s+(.+?)\s+como\b", t, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip(" .,:;")
        if value is None:
            value = self._extract_rhs_value(t)
        if value is None:
            return None

        if "responsable ps" in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "ps_owner_name",
                "raw_value": value,
            }
        if ("encargad" in tl or "responsable entidad" in tl) and "telefon" not in tl and "correo" not in tl and "email" not in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "entity_contact_name",
                "raw_value": value,
            }
        if "telefon" in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "entity_contact_phone",
                "raw_value": value,
            }
        if "correo" in tl or "email" in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "entity_contact_email",
                "raw_value": value,
            }
        if "fecha de nacimiento" in tl and "pareja" not in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "entity_contact_birthdate",
                "raw_value": value,
            }
        if "pareja" in tl and ("nombre" in tl or "se llama" in tl):
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "partner_name",
                "raw_value": value,
            }
        if "pareja" in tl and "nacimiento" in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "partner_birthdate",
                "raw_value": value,
            }
        if "fase estatal" in tl:
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "state_phase_description",
                "raw_value": value,
            }
        if "uniformes" in tl and ("entrega" in tl or "fecha" in tl or "lugar" in tl):
            return {
                "entity_name": entity_name,
                "domain": "ops",
                "field_name": "uniform_delivery_date_place",
                "raw_value": value,
            }
        if any(k in tl for k in ("uniformes", "balones", "equipamiento", "utileria", "utilería")) and any(k in tl for k in ("costo", "monto", "importe")):
            return {
                "entity_name": entity_name,
                "domain": "fin",
                "field_name": "equipment_costs",
                "raw_value": [{"descripcion": "equipamiento/utileria", "valor": self._parse_scalar_or_json(value)}],
            }
        return None

    def _parse_natural_national_update(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        tl = t.lower()
        if "nacional" not in tl:
            return None

        # Explicit natural patterns
        m = re.match(
            r"^\s*pon(?:\s+a)?\s+(.+?)\s+como\s+costo\s+de\s+seguros\s+(?:en\s+)?nacional\s*$",
            t,
            flags=re.IGNORECASE,
        )
        if m:
            value = self._parse_scalar_or_json(m.group(1).strip())
            return {
                "domain": "fin",
                "field_name": "insurance_costs",
                "raw_value": [{"descripcion": "seguros", "valor": value}],
            }
        m = re.match(
            r"^\s*pon(?:\s+a)?\s+(.+?)\s+como\s+unidad\s+deportiva\s+(?:en\s+)?nacional\s*$",
            t,
            flags=re.IGNORECASE,
        )
        if m:
            return {"domain": "ops", "field_name": "sports_facility", "raw_value": m.group(1).strip()}
        m = re.match(
            r"^\s*(?:pon|agrega)(?:\s+a)?\s+(.+?)\s+como\s+visitante(?:s)?\s+(?:del?\s+)?patrocinador\s+(?:en\s+)?nacional\s*$",
            t,
            flags=re.IGNORECASE,
        )
        if m:
            return {
                "domain": "mkt",
                "field_name": "sponsor_visitors",
                "raw_value": [{"nombre": m.group(1).strip()}],
            }

        value = self._extract_rhs_value(t)
        if value is None:
            return None

        if "costo" in tl and "seguros" in tl:
            return {
                "domain": "fin",
                "field_name": "insurance_costs",
                "raw_value": [{"descripcion": "seguros", "valor": self._parse_scalar_or_json(value)}],
            }
        if "unidad deportiva" in tl:
            return {"domain": "ops", "field_name": "sports_facility", "raw_value": value}
        if "servicio" in tl and "medic" in tl:
            return {"domain": "ops", "field_name": "medical_services_description", "raw_value": value}
        if "proveedor" in tl and ("activacion" in tl or "activación" in tl):
            return {
                "domain": "mkt",
                "field_name": "onsite_brand_activation_providers",
                "raw_value": [{"nombre": value}],
            }
        if "visitante" in tl and "patrocinador" in tl:
            return {
                "domain": "mkt",
                "field_name": "sponsor_visitors",
                "raw_value": [{"nombre": value}],
            }
        if "actividad" in tl and "resultado" in tl:
            return {
                "domain": "mkt",
                "field_name": "activities_and_results",
                "raw_value": [{"actividad": value}],
            }
        return None

    def _split_batch_clauses(self, text: str) -> List[str]:
        raw = re.split(r"\s+y\s+|;", text, flags=re.IGNORECASE)
        return [c.strip(" ,.") for c in raw if c.strip(" ,.")]

    def _parse_entity_clause_with_context(self, entity_name: str, clause: str) -> Optional[Dict[str, Any]]:
        c = clause.strip()
        cl = c.lower()
        value = self._extract_rhs_value(c)
        if value is None:
            return None

        if "responsable ps" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "ps_owner_name", "raw_value": value}
        if "correo" in cl or "email" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "entity_contact_email", "raw_value": value}
        if "telefon" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "entity_contact_phone", "raw_value": value}
        if ("encargad" in cl or "responsable entidad" in cl) and "telefon" not in cl and "correo" not in cl and "email" not in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "entity_contact_name", "raw_value": value}
        if "fecha de nacimiento" in cl and "pareja" not in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "entity_contact_birthdate", "raw_value": value}
        if "pareja" in cl and "nacimiento" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "partner_birthdate", "raw_value": value}
        if "pareja" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "partner_name", "raw_value": value}
        if "fase estatal" in cl:
            return {"entity_name": entity_name, "domain": "ops", "field_name": "state_phase_description", "raw_value": value}
        if "uniformes" in cl and ("entrega" in cl or "fecha" in cl or "lugar" in cl):
            return {"entity_name": entity_name, "domain": "ops", "field_name": "uniform_delivery_date_place", "raw_value": value}
        if any(k in cl for k in ("uniformes", "balones", "equipamiento", "utileria", "utilería")) and any(k in cl for k in ("costo", "monto", "importe")):
            return {
                "entity_name": entity_name,
                "domain": "fin",
                "field_name": "equipment_costs",
                "raw_value": [{"descripcion": "equipamiento/utileria", "valor": self._parse_scalar_or_json(value)}],
            }
        return None

    def _parse_national_clause(self, clause: str) -> Optional[Dict[str, Any]]:
        c = clause.strip()
        cl = c.lower()
        if "nacional" not in cl:
            c = f"{c} nacional"
        return self._parse_natural_national_update(c)

    def _parse_batch_natural_updates(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        tl = t.lower()
        if re.search(r"\s+y\s+en\s+[A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80}\s+", t, flags=re.IGNORECASE):
            # Multi-entity batch is handled by a dedicated parser.
            return None
        has_batch_delimiter = (" y " in tl) or (";" in tl)
        if not has_batch_delimiter:
            return None

        clauses = self._split_batch_clauses(t)
        if len(clauses) < 2:
            return None

        if "nacional" in tl:
            updates: List[Dict[str, Any]] = []
            for clause in clauses:
                parsed = self._parse_national_clause(clause)
                if parsed:
                    updates.append(parsed)
            if len(updates) >= 2:
                return {"scope": "national", "updates": updates}
            return None

        entity_name = self._extract_entity_name_from_text(t)
        if not entity_name:
            m = re.search(r"\ben\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,80})\b", t, flags=re.IGNORECASE)
            if m:
                entity_name = m.group(1).strip(" .,:;")
        if not entity_name:
            return None

        updates = []
        for clause in clauses:
            parsed = self._parse_entity_clause_with_context(entity_name, clause)
            if parsed:
                updates.append(parsed)
        if len(updates) >= 2:
            return {"scope": "entity", "entity_name": entity_name, "updates": updates}
        return None

    def _parse_multi_entity_batch_updates(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """
        Parse inputs like:
        "en Jalisco responsable PS es Laura y correo es a@x.com y en Puebla responsable PS es Mario y correo es b@x.com"
        """
        t = (text or "").strip()
        tl = t.lower()
        if "nacional" in tl:
            return None
        if tl.count(" en ") < 1:
            return None

        segments = re.split(r"\s+y\s+en\s+", t, flags=re.IGNORECASE)
        if len(segments) < 2:
            return None

        parsed: List[Dict[str, Any]] = []
        start_keywords = (
            "responsable",
            "correo",
            "email",
            "telefon",
            "encargad",
            "fase",
            "uniformes",
            "costo",
            "monto",
            "importe",
            "fecha",
            "pareja",
        )

        for raw_seg in segments:
            seg = raw_seg.strip()
            seg = re.sub(r"^\s*en\s+", "", seg, flags=re.IGNORECASE).strip()
            if not seg:
                continue

            lower_seg = seg.lower()
            split_idx = -1
            for kw in start_keywords:
                idx = lower_seg.find(kw)
                if idx > 0 and (split_idx == -1 or idx < split_idx):
                    split_idx = idx
            if split_idx <= 0:
                continue

            entity_name = seg[:split_idx].strip(" .,:;")
            body = seg[split_idx:].strip(" .,:;")
            if not entity_name or not body:
                continue

            clauses = self._split_batch_clauses(body)
            updates: List[Dict[str, Any]] = []
            for clause in clauses:
                upd = self._parse_entity_clause_with_context(entity_name, clause)
                if upd:
                    updates.append(upd)
            if updates:
                parsed.append({"entity_name": entity_name, "updates": updates})

        return parsed if len(parsed) >= 2 else None

    def _slugify_local(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower())
        return slug.strip("-") or "sin-nombre"

    def _get_ai_tournament_slug(self) -> str:
        return self._slugify_local(self.tournament_id or "torneo")

    def _parse_scalar_or_json(self, raw_value: str) -> Any:
        value = (raw_value or "").strip()
        if not value:
            return ""
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        if value.lower() == "null":
            return None
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        if (value.startswith("{") and value.endswith("}")) or (
            value.startswith("[") and value.endswith("]")
        ):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _load_json_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_entity_payload(self, tournament_slug: str, entity_name: str) -> Dict[str, Dict[str, Any]]:
        base = self.ai_workspace.root / self._slugify_local(tournament_slug) / "entities" / self._slugify_local(entity_name)
        return {
            "ops": self._load_json_file(base / "operations.json"),
            "fin": self._load_json_file(base / "finance.json"),
        }

    def _load_national_payload(self, tournament_slug: str) -> Dict[str, Dict[str, Any]]:
        base = self.ai_workspace.root / self._slugify_local(tournament_slug) / "national"
        return {
            "ops": self._load_json_file(base / "operations.json"),
            "fin": self._load_json_file(base / "finance.json"),
            "mkt": self._load_json_file(base / "marketing.json"),
        }

    def _filter_dataclass_fields(self, payload: Dict[str, Any], record_cls) -> Dict[str, Any]:
        allowed = {f.name for f in fields(record_cls)}
        return {k: v for k, v in payload.items() if k in allowed}

    def _entity_ops_record_from_payload(self, entity_name: str, payload: Dict[str, Any]) -> EntityOperationsRecord:
        filtered = self._filter_dataclass_fields(payload, EntityOperationsRecord)
        filtered["entity_name"] = entity_name
        return EntityOperationsRecord(**filtered)

    def _entity_fin_record_from_payload(self, entity_name: str, payload: Dict[str, Any]) -> EntityFinanceRecord:
        filtered = self._filter_dataclass_fields(payload, EntityFinanceRecord)
        filtered["entity_name"] = entity_name
        return EntityFinanceRecord(**filtered)

    def _national_ops_record_from_payload(self, payload: Dict[str, Any]) -> NationalOperationsRecord:
        return NationalOperationsRecord(**self._filter_dataclass_fields(payload, NationalOperationsRecord))

    def _national_fin_record_from_payload(self, payload: Dict[str, Any]) -> NationalFinanceRecord:
        return NationalFinanceRecord(**self._filter_dataclass_fields(payload, NationalFinanceRecord))

    def _national_mkt_record_from_payload(self, payload: Dict[str, Any]) -> NationalMarketingRecord:
        return NationalMarketingRecord(**self._filter_dataclass_fields(payload, NationalMarketingRecord))

    def _set_entity_ai_field(
        self,
        tournament_slug: str,
        entity_name: str,
        domain: str,
        field_name: str,
        raw_value: Any,
    ) -> str:
        domain_clean = domain.strip().lower()
        payload = self._load_entity_payload(tournament_slug, entity_name)
        value = self._parse_scalar_or_json(raw_value) if isinstance(raw_value, str) else raw_value

        if domain_clean == "ops":
            payload["ops"][field_name] = value
        elif domain_clean == "fin":
            payload["fin"][field_name] = value
        else:
            return "⚠️ Dominio invalido. Usa `ops` o `fin`."

        result = self.ai_workspace.upsert_entity(
            tournament_slug=tournament_slug,
            entity_ops=self._entity_ops_record_from_payload(entity_name, payload["ops"]),
            entity_fin=self._entity_fin_record_from_payload(entity_name, payload["fin"]),
        )
        return (
            "✅ Campo de entidad actualizado\n"
            f"🏷️ Entidad: {entity_name}\n"
            f"📚 Dominio: {domain_clean}\n"
            f"🔧 Campo: {field_name}\n"
            f"📝 Reporte ops: {result['operations_report']}\n"
            f"💰 Reporte fin: {result['finance_report']}"
        )

    def _apply_entity_batch_updates(
        self,
        tournament_slug: str,
        entity_name: str,
        updates: List[Dict[str, Any]],
    ) -> str:
        payload = self._load_entity_payload(tournament_slug, entity_name)
        applied = []
        for upd in updates:
            domain_clean = upd["domain"].strip().lower()
            field_name = upd["field_name"]
            value = upd["raw_value"]
            if domain_clean == "ops":
                payload["ops"][field_name] = value
                applied.append(f"ops.{field_name}")
            elif domain_clean == "fin":
                payload["fin"][field_name] = value
                applied.append(f"fin.{field_name}")

        result = self.ai_workspace.upsert_entity(
            tournament_slug=tournament_slug,
            entity_ops=self._entity_ops_record_from_payload(entity_name, payload["ops"]),
            entity_fin=self._entity_fin_record_from_payload(entity_name, payload["fin"]),
        )
        return (
            "✅ Lote de entidad actualizado\n"
            f"🏷️ Entidad: {entity_name}\n"
            f"🔧 Campos: {', '.join(applied)}\n"
            f"📝 {result['operations_report']}\n"
            f"💰 {result['finance_report']}"
        )

    def _set_national_ai_field(
        self,
        tournament_slug: str,
        domain: str,
        field_name: str,
        raw_value: Any,
    ) -> str:
        domain_clean = domain.strip().lower()
        payload = self._load_national_payload(tournament_slug)
        value = self._parse_scalar_or_json(raw_value) if isinstance(raw_value, str) else raw_value

        if domain_clean == "ops":
            payload["ops"][field_name] = value
        elif domain_clean == "fin":
            payload["fin"][field_name] = value
        elif domain_clean in {"mkt", "marketing"}:
            payload["mkt"][field_name] = value
        else:
            return "⚠️ Dominio invalido. Usa `ops`, `fin` o `mkt`."

        result = self.ai_workspace.upsert_national_phase(
            tournament_slug=tournament_slug,
            operations=self._national_ops_record_from_payload(payload["ops"]),
            finance=self._national_fin_record_from_payload(payload["fin"]),
            marketing=self._national_mkt_record_from_payload(payload["mkt"]),
        )
        return (
            "✅ Campo nacional actualizado\n"
            f"📚 Dominio: {domain_clean}\n"
            f"🔧 Campo: {field_name}\n"
            f"📁 {result['national_dir']}"
        )

    def _apply_national_batch_updates(
        self,
        tournament_slug: str,
        updates: List[Dict[str, Any]],
    ) -> str:
        payload = self._load_national_payload(tournament_slug)
        applied = []
        for upd in updates:
            domain_clean = upd["domain"].strip().lower()
            field_name = upd["field_name"]
            value = upd["raw_value"]
            if domain_clean == "ops":
                payload["ops"][field_name] = value
                applied.append(f"ops.{field_name}")
            elif domain_clean == "fin":
                payload["fin"][field_name] = value
                applied.append(f"fin.{field_name}")
            elif domain_clean in {"mkt", "marketing"}:
                payload["mkt"][field_name] = value
                applied.append(f"mkt.{field_name}")

        result = self.ai_workspace.upsert_national_phase(
            tournament_slug=tournament_slug,
            operations=self._national_ops_record_from_payload(payload["ops"]),
            finance=self._national_fin_record_from_payload(payload["fin"]),
            marketing=self._national_mkt_record_from_payload(payload["mkt"]),
        )
        return (
            "✅ Lote nacional actualizado\n"
            f"🔧 Campos: {', '.join(applied)}\n"
            f"📁 {result['national_dir']}"
        )

    def _apply_multi_entity_batch_updates(
        self,
        tournament_slug: str,
        batch: List[Dict[str, Any]],
    ) -> str:
        summaries: List[str] = []
        for item in batch:
            entity_name = item["entity_name"]
            updates = item["updates"]
            result = self._apply_entity_batch_updates(
                tournament_slug=tournament_slug,
                entity_name=entity_name,
                updates=updates,
            )
            first_line = result.splitlines()[0] if result else "✅"
            summaries.append(f"• {entity_name}: {first_line.replace('✅ ', '')}")

        return "✅ Lote multi-entidad aplicado\n" + "\n".join(summaries)

    async def _handle_conversational_query(self, chat_id: int, text: str) -> Optional[str]:
        """Handle natural-language read queries for team/player data."""
        t = (text or "").strip()
        tl = t.lower()
        if not t:
            return None

        wants_list_players = (
            ("jugador" in tl or "jugadores" in tl)
            and any(k in tl for k in ["lista", "listar", "muestra", "muéstrame", "dame", "ver", "quienes", "quiénes"])
        )
        wants_count_players = ("cuantos" in tl or "cuántos" in tl) and ("jugador" in tl or "jugadores" in tl)

        if not wants_list_players and not wants_count_players:
            return None

        team_hint = self._extract_team_hint_from_text(t)
        pending = self.pending_saves.get(chat_id) or {}
        tournament_slug_hint = (
            (pending.get("tournament_selected") or pending.get("tournament_slug") or "").strip() or None
        )
        if team_hint:
            try:
                from samchat.tournaments_v2.adapters import (
                    infer_tournament_key_from_slug,
                    team_roster_query_v2,
                )

                roster = await team_roster_query_v2(
                    tournament_key=infer_tournament_key_from_slug(tournament_slug_hint),
                    tournament_slug=tournament_slug_hint,
                    team_name=team_hint,
                    limit=100,
                )
                team_name_value = ((roster.get("team") or {}).get("team_name") or team_hint).strip()
                players = roster.get("players") or []

                if wants_count_players:
                    return f"👥 Equipo *{team_name_value}*: {len(players)} jugadores registrados."

                if not players:
                    return f"📭 El equipo *{team_name_value}* no tiene jugadores registrados."

                lines = [f"👥 *Jugadores de {team_name_value}* ({len(players)}):"]
                for idx, p in enumerate(players, 1):
                    n = p.get("jersey_number") or idx
                    lines.append(f"{n}. {p.get('nombre') or 'Jugador sin nombre'}")
                return "\n".join(lines)
            except Exception:
                logger.warning("Supabase conversational roster lookup failed; falling back to legacy chat DB", exc_info=True)

        if not self.db:
            return "❌ No hay conexion a BD para consultar jugadores."

        from devnous.copa_telmex.database import CopaTelmexDB

        try:
            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                teams = await copa_db.get_teams_by_chat(chat_id)
                if not teams:
                    return "📭 No encuentro equipos registrados en este chat."

                team = None
                if team_hint:
                    for tm in teams:
                        if team_hint in (tm.name or "").lower():
                            team = tm
                            break
                if not team:
                    team = teams[0]  # latest by created_at desc in get_teams_by_chat

                players = await copa_db.get_players_by_team(team.id)
                players_sorted = sorted(
                    players,
                    key=lambda p: (
                        (p.roster_index is None),
                        (p.roster_index or 10**9),
                        p.created_at,
                    ),
                )

                if wants_count_players:
                    return f"👥 Equipo *{team.name}*: {len(players_sorted)} jugadores registrados."

                if not players_sorted:
                    return f"📭 El equipo *{team.name}* no tiene jugadores registrados."

                lines = [f"👥 *Jugadores de {team.name}* ({len(players_sorted)}):"]
                for idx, p in enumerate(players_sorted, 1):
                    n = p.roster_index or idx
                    lines.append(f"{n}. {p.full_name}")
                return "\n".join(lines)
        except Exception as e:
            logger.error(f"❌ conversational query failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude consultar los jugadores")

    def _extract_team_hint_from_text(self, text: str) -> Optional[str]:
        import re

        t = (text or "").strip()
        m = re.search(r"(?:equipo|del equipo|de equipo)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ0-9 ._-]{3,80})", t, flags=re.IGNORECASE)
        if not m:
            return None
        hint = m.group(1).strip().lower()
        # Remove trailing punctuation/questions.
        return hint.rstrip("?.!,;: ")

    async def _handle_conversational_actions(self, chat_id: int, user_id: int, text: str) -> Optional[str]:
        """
        Handle natural-language write actions.
        Currently supports: add/register a player manually to an existing team.
        """
        t = (text or "").strip()
        tl = t.lower()
        if not t:
            return None

        # Continue an in-progress onboarding flow.
        if chat_id in self.pending_player_onboarding:
            return await self._continue_player_onboarding(chat_id=chat_id, user_id=user_id, text=t)

        add_markers = [
            "dar de alta",
            "agregar jugador",
            "añadir jugador",
            "anadir jugador",
            "inscribir jugador",
            "registrar jugador",
            "no viene en la cedula",
            "no viene en la cédula",
        ]
        if not any(m in tl for m in add_markers):
            return None

        if not self.db:
            return "❌ No hay conexion a BD para dar de alta jugadores."

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                teams = await copa_db.get_teams_by_chat(chat_id)
                if not teams:
                    return "📭 No encuentro equipos registrados en este chat."

                team_hint = self._extract_team_hint_from_text(t)
                team = None
                if team_hint:
                    for tm in teams:
                        if team_hint in (tm.name or "").lower():
                            team = tm
                            break
                if not team:
                    team = teams[0]

                parsed = self._parse_manual_player_payload(t)
                full_name = parsed.get("full_name")
                birth_date = self._parse_birth_date(parsed.get("birth_date") or "") if parsed.get("birth_date") else None

                if not full_name or birth_date is None:
                    self.pending_player_onboarding[chat_id] = {
                        "team_id": str(team.id),
                        "team_name": team.name,
                    }
                    return (
                        f"✅ Claro. Vamos a dar de alta un jugador en *{team.name}*.\n\n"
                        "Enviame en un solo mensaje:\n"
                        "`Nombre completo, fecha nacimiento DD/MM/YYYY, CURP(opcional), email(opcional)`\n\n"
                        "Ejemplo:\n"
                        "`Brian Rodriguez, 23/03/2007, ROLB070323HDFXXX09, brian@mail.com`"
                    )

                created_msg = await self._create_manual_player(
                    chat_id=chat_id,
                    team_id=str(team.id),
                    full_name=full_name,
                    birth_date=birth_date,
                    curp=parsed.get("curp"),
                    email=parsed.get("email"),
                )
                return created_msg

        except Exception as e:
            logger.error(f"❌ conversational add-player failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude dar de alta al jugador")

    async def _continue_player_onboarding(self, chat_id: int, user_id: int, text: str) -> str:
        """Continue manual player onboarding started from conversational action."""
        state = self.pending_player_onboarding.get(chat_id) or {}
        team_id = state.get("team_id")
        team_name = state.get("team_name") or "equipo"
        if not team_id:
            self.pending_player_onboarding.pop(chat_id, None)
            return "⚠️ Se perdio el contexto del equipo. Escribe de nuevo la solicitud."

        parsed = self._parse_manual_player_payload(text)
        full_name = parsed.get("full_name")
        birth_date = self._parse_birth_date(parsed.get("birth_date") or "") if parsed.get("birth_date") else None
        if not full_name or birth_date is None:
            return (
                f"⚠️ Aun me falta informacion para *{team_name}*.\n"
                "Formato esperado: `Nombre completo, DD/MM/YYYY, CURP(opcional), email(opcional)`"
            )

        self.pending_player_onboarding.pop(chat_id, None)
        return await self._create_manual_player(
            chat_id=chat_id,
            team_id=str(team_id),
            full_name=full_name,
            birth_date=birth_date,
            curp=parsed.get("curp"),
            email=parsed.get("email"),
        )

    def _parse_manual_player_payload(self, text: str) -> Dict[str, Optional[str]]:
        """Best-effort parser for manual player create payload from one sentence."""
        t = (text or "").strip()

        # Split by comma first (most reliable in chat).
        parts = [p.strip() for p in t.split(",") if p.strip()]

        # Detect date token.
        date_token = None
        for p in parts:
            if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", p):
                m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", p)
                if m:
                    date_token = m.group(1).replace("-", "/")
                    break
        if not date_token:
            m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", t)
            if m:
                date_token = m.group(1).replace("-", "/")

        # Detect email + CURP anywhere.
        email = None
        m = re.search(r"\b([^\s,;]+@[^\s,;]+\.[^\s,;]+)\b", t, flags=re.IGNORECASE)
        if m:
            email = m.group(1).strip()

        curp = None
        m = re.search(r"\b([A-Za-z]{4}\d{6}[HMhm][A-Za-z]{5}[A-Za-z0-9]\d)\b", t)
        if m:
            curp = m.group(1).upper()

        # Name heuristic: text before first detected date token/comma, cleanup intent words.
        name_candidate = parts[0] if parts else t
        # Remove common intent preamble.
        name_candidate = re.sub(
            r"(?i).*(dar de alta|agregar|añadir|anadir|inscribir|registrar)\s+(a\s+)?(un\s+)?jugador(\s+a[l]?\s+equipo)?\s*",
            "",
            name_candidate,
        ).strip(" :.-")
        name_candidate = re.sub(r"(?i)^en\s+los\s+", "", name_candidate).strip()

        # If first part still does not look like a name, try explicit "se llama ..."
        if len(name_candidate.split()) < 2:
            m = re.search(r"(?i)se llama\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{4,100})", t)
            if m:
                name_candidate = m.group(1).strip()

        return {
            "full_name": name_candidate if len(name_candidate.split()) >= 2 else None,
            "birth_date": date_token,
            "curp": curp,
            "email": email,
        }

    async def _create_manual_player(
        self,
        chat_id: int,
        team_id: str,
        full_name: str,
        birth_date: date,
        curp: Optional[str],
        email: Optional[str],
    ) -> str:
        """Create a player manually with Supabase-first write plus local mirror."""
        from uuid import UUID

        from devnous.copa_telmex.database import CopaTelmexDB

        async with self.db() as session:
            copa_db = CopaTelmexDB(session)
            team_uuid = UUID(team_id)
            team = await copa_db.get_team_by_id(team_uuid)
            if not team:
                return "❌ No encuentro el equipo destino."

            players = await copa_db.get_players_by_team(team_uuid)
            max_idx = max([p.roster_index or 0 for p in players] or [0])

            parts = full_name.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else "X"
            tournament_slug_hint = (getattr(team, "tournament_slug", None) or "").strip() or None
            category_name_hint = (getattr(team, "category", None) or "").strip() or None
            pending = self.pending_saves.get(chat_id) or {}
            if not tournament_slug_hint:
                tournament_slug_hint = (
                    (pending.get("tournament_selected") or pending.get("tournament_slug") or "").strip() or None
                )
            if not category_name_hint:
                category_name_hint = (
                    (pending.get("category_selected") or pending.get("category_name") or "").strip() or None
                )

            supabase_result = None
            supabase_error: Optional[Exception] = None
            if tournament_slug_hint and category_name_hint:
                try:
                    from samchat.tournaments_v2.adapters import (
                        append_players_to_team_v2,
                        infer_tournament_key_from_slug,
                    )

                    supabase_result = await append_players_to_team_v2(
                        tournament_key=infer_tournament_key_from_slug(tournament_slug_hint),
                        tournament_slug=tournament_slug_hint,
                        category_name=category_name_hint,
                        team_name=team.name,
                        players=[
                            {
                                "first_name": first_name,
                                "last_name": last_name,
                                "birth_date": birth_date.isoformat(),
                                "curp": (curp or None),
                                "parent_email": (email or None),
                            }
                        ],
                    )
                    if int(supabase_result.get("players_created") or 0) == 0 and int(
                        supabase_result.get("players_skipped") or 0
                    ) > 0:
                        return f"⚠️ Ese jugador ya existe en *{team.name}*."
                except Exception as exc:
                    supabase_error = exc
                    logger.warning("Supabase manual player create failed; using local mirror only", exc_info=True)

            # Local mirror / compatibility layer.
            existing = await copa_db.get_player_by_team_and_identity(
                team_id=team_uuid,
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date,
            )
            if existing:
                if supabase_result and int(supabase_result.get("players_created") or 0) > 0:
                    return (
                        f"✅ Jugador dado de alta en *{team.name}*.\n"
                        f"• {existing.roster_index or '?'}. {existing.full_name}\n"
                        f"• Nacimiento: {birth_date.strftime('%d/%m/%Y')}\n"
                        "• Supabase: sincronizado\n"
                        "• BD local: ya existia"
                    )
                return f"⚠️ Ese jugador ya existe en *{team.name}* ({existing.full_name})."

            p = await copa_db.create_player(
                team_id=team_uuid,
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date,
                curp=(curp or None),
                email=(email or None),
                ocr_confidence=None,
                needs_review=False,
                verified_by_human=True,
                verification_notes="Alta manual via chat",
                roster_index=max_idx + 1,
            )
            await copa_db.commit()
            lines = [
                f"✅ Jugador dado de alta en *{team.name}*.\n"
                f"• {p.roster_index}. {p.full_name}",
                f"• Nacimiento: {birth_date.strftime('%d/%m/%Y')}",
            ]
            if supabase_result and int(supabase_result.get("players_created") or 0) > 0:
                lines.append("• Supabase: sincronizado")
            elif supabase_result and int(supabase_result.get("players_skipped") or 0) > 0:
                lines.append("• Supabase: jugador ya existente")
            elif tournament_slug_hint and category_name_hint and supabase_error:
                lines.append("• Supabase: pendiente de sincronizar")
            return "\n".join(lines)

    async def _apply_freeform_corrections(self, chat_id: int, user_id: int, text: str) -> Optional[str]:
        """
        Attempt to parse and apply corrections from natural language.

        If the message looks like a correction but is ambiguous, returns an
        instruction to use `corregir`.
        """
        t = (text or "").strip()
        t_lower = t.lower()

        # Quick heuristics: only engage when it smells like a correction.
        correction_markers = [
            "se llama",
            "es ",
            "correo",
            "email",
            "curp",
            "municipio",
            "estado",
            "rama",
            "género",
            "genero",
            "categoria",
            "categoría",
            "liga",
            "representante",
            "jugador",
            ":",
        ]
        if not any(m in t_lower for m in correction_markers):
            return None

        return (
            "Las correcciones de equipos ya no se aplican desde Telegram. "
            "Usa el dashboard: cada cambio requiere motivo, version, decision "
            "Zaubern y recibo de finalidad."
        )

        if not self.db:
            return "❌ No hay conexion a BD para corregir datos."

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                team = await copa_db.get_latest_team_by_chat(chat_id)
                if not team:
                    return "📭 No encuentro un equipo reciente para corregir en este chat."

                players = await copa_db.get_players_by_team(team.id)
                regs = await copa_db.get_registrations_by_chat(chat_id, limit=10)
                reg_id = None
                for r in regs:
                    if r.team_id and r.team_id == team.id:
                        reg_id = r.id
                        break

                applied: List[str] = []
                supabase_player_sync = None

                # 1) Team/representative fields
                team_updates = self._parse_team_updates(t)
                supabase_sync_result = None
                if team_updates:
                    original_map = {k: getattr(team, k, None) for k in team_updates.keys()}
                    await copa_db.update_team(team.id, **team_updates)
                    supabase_sync_result = await self._sync_team_updates_to_supabase(team=team, updates=team_updates)
                    for k, v in team_updates.items():
                        applied.append(f"✅ {self._label_for_team_field(k)}: *{v}*")
                        if reg_id:
                            await copa_db.log_validation(
                                registration_id=reg_id,
                                field_name=f"team_{k}",
                                original_value=str(original_map.get(k) or ""),
                                corrected_value=str(v),
                                validation_action="corrected",
                                telegram_chat_id=chat_id,
                            )

                # 2) Player fields (requires player identity)
                player_change = self._parse_player_updates(t)
                if player_change:
                    target_name = player_change["target_name"]
                    updates = player_change["updates"]

                    matches = self._resolve_player_ref(players, target_name)
                    if len(matches) != 1:
                        return (
                            "⚠️ No pude identificar un jugador unico.\n\n"
                            "Usa `corregir` y selecciona el jugador, o escribe el nombre completo exactamente."
                        )

                    player = matches[0]
                    original_player = {
                        "first_name": player.first_name,
                        "last_name": player.last_name,
                        "email": player.email,
                        "curp": player.curp,
                        "birth_date": player.birth_date.isoformat() if player.birth_date else "",
                    }

                    # Transform schema-level updates to model fields.
                    model_updates: Dict[str, Any] = {}
                    for k, v in updates.items():
                        if k == "full_name":
                            parts = v.split()
                            if len(parts) < 2:
                                return "⚠️ Escribe nombre y apellidos (ej: Juan Garcia Lopez)."
                            model_updates["first_name"] = parts[0]
                            model_updates["last_name"] = " ".join(parts[1:])
                        elif k == "birth_date":
                            parsed = self._parse_birth_date(v)
                            if parsed is None:
                                return "⚠️ Fecha invalida. Usa DD/MM/YYYY (ej: 23/03/2007)."
                            model_updates["birth_date"] = parsed
                        else:
                            model_updates[k] = v

                    await copa_db.update_player(player.id, **model_updates)
                    supabase_player_sync = await self._sync_player_updates_to_supabase(
                        team=team,
                        player=player,
                        updates=updates,
                        original_player=original_player,
                    )
                    for k, v in updates.items():
                        applied.append(f"✅ Jugador *{player.full_name}* {self._label_for_player_field(k)}: *{v}*")
                        if reg_id:
                            await copa_db.log_validation(
                                registration_id=reg_id,
                                field_name=f"player_{k}",
                                original_value=str(original_player.get(k) or ""),
                                corrected_value=str(v),
                                validation_action="corrected",
                                telegram_chat_id=chat_id,
                            )

                if not applied:
                    # It looked like a correction, but we couldn't parse it confidently.
                    return (
                        "⚠️ Entendi que quieres corregir datos, pero el mensaje es ambiguo.\n\n"
                        "Usa `corregir` para abrir el menu de campos."
                    )

                if reg_id:
                    await copa_db.mark_registration_reviewed(reg_id, "corrected", team_id=team.id)

                await copa_db.commit()

            if player_change:
                if supabase_player_sync:
                    local_only = supabase_player_sync.get("local_only_fields") or []
                    if local_only:
                        applied.append(
                            "ℹ️ Supabase parcial en jugador; solo local en: " + ", ".join(sorted(local_only))
                        )
                    else:
                        applied.append("✅ Jugador sincronizado en Supabase")
                else:
                    applied.append("ℹ️ Correccion de jugador guardada localmente; sync Supabase pendiente")
            if team_updates:
                if supabase_sync_result:
                    local_only = supabase_sync_result.get("local_only_fields") or []
                    if local_only:
                        applied.append(
                            "ℹ️ Supabase parcial; solo local en: " + ", ".join(sorted(local_only))
                        )
                    else:
                        applied.append("✅ Supabase sincronizado")
                else:
                    applied.append("ℹ️ Correccion guardada localmente; sync Supabase pendiente")
            return "\n".join(applied)

        except Exception as e:
            logger.error(f"❌ freeform_corrections failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude aplicar la corrección")

    def _parse_team_updates(self, text: str) -> Dict[str, Any]:
        """Parse team/representative field updates from text."""
        import re

        t = (text or "").strip()
        tl = t.lower()
        out: Dict[str, Any] = {}

        # Team name
        m = re.search(r"(?:nombre\s+del\s+equipo|equipo)\s*(?:se\s+llama|es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m and "jugador" not in tl:
            out["name"] = m.group(1).strip()

        # Representative name
        m = re.search(r"(?:representante|representate|reprsentate)\s*(?:se\s+llama|es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            out["representative_name"] = m.group(1).strip()

        # Email (team/representative contact)
        m = re.search(r"(?:correo|email)\s+(?:del\s+equipo|del\s+representante|de\s+contacto)?\s*(?:es|:)\s*([^\s,;]+@[^\s,;]+)", t, flags=re.IGNORECASE)
        if m:
            out["contact_email"] = m.group(1).strip()

        # League
        m = re.search(r"(?:liga)\s*(?:es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m and "jugador" not in tl:
            out["league"] = m.group(1).strip()

        # Municipality / State
        m = re.search(r"(?:municipio)\s*(?:es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            out["municipality"] = m.group(1).strip()
        m = re.search(r"(?:estado)\s*(?:es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            out["state"] = m.group(1).strip()

        # Gender/branch
        m = re.search(r"(?:rama|género|genero)\s*(?:es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            out["gender"] = self._normalize_gender(m.group(1).strip())

        # Category
        m = re.search(r"(?:categor[ií]a|categoria)\s*(?:es|:)\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            out["category"] = m.group(1).strip()

        return {k: v for k, v in out.items() if v}

    def _parse_player_updates(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse player corrections. Returns {target_name, updates} or None."""
        import re

        t = (text or "").strip()
        tl = t.lower()
        if "jugador" not in tl and "curp de" not in tl and "correo de" not in tl and "email de" not in tl:
            return None

        target = None

        # Numeric reference: "jugador 1 ..."
        m = re.search(r"(?:jugador)\s+(\d{1,2})\b", t, flags=re.IGNORECASE)
        if m:
            target = m.group(1).strip()

        m = re.search(r"(?:jugador)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){1,5})", t, flags=re.IGNORECASE)
        if not target and m:
            target = m.group(1).strip()

        m = re.search(r"(?:curp|correo|email)\s+de\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){1,5})", t, flags=re.IGNORECASE)
        if not target and m:
            target = m.group(1).strip()

        if not target:
            return None

        updates: Dict[str, Any] = {}

        # Full name change: "jugador X se llama Y" or "jugador X ahora se llama Y"
        m = re.search(r"(?:jugador)\s+[^\n]+?\s+(?:ahora\s+)?se\s+llama\s+(.+)$", t, flags=re.IGNORECASE)
        if m:
            updates["full_name"] = m.group(1).strip()

        # First name / last names
        m = re.search(r"(?:nombre)\s*(?:es|:)\s*([A-Za-zÁÉÍÓÚÑáéíóúñ ]+)$", t, flags=re.IGNORECASE)
        if m and "apell" in tl:
            # When both name+apellidos are provided, handle via full_name downstream if needed.
            pass

        m = re.search(r"(?:nombre\s+completo)\s*(?:es|:)\s*(.+)$", t, flags=re.IGNORECASE)
        if m:
            updates["full_name"] = m.group(1).strip()

        # Email
        m = re.search(r"(?:correo|email)\s*(?:es|:)\s*([^\s,;]+@[^\s,;]+)", t, flags=re.IGNORECASE)
        if m:
            updates["email"] = m.group(1).strip()

        # CURP
        m = re.search(r"(?:curp)\s*(?:es|:)\s*([A-Za-z0-9]{10,20})", t, flags=re.IGNORECASE)
        if m:
            updates["curp"] = m.group(1).strip().upper()

        # Birth date
        m = re.search(r"(?:nacimiento|fecha\s+de\s+nacimiento)\s*(?:es|:)\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", t, flags=re.IGNORECASE)
        if m:
            updates["birth_date"] = m.group(1).strip().replace("-", "/")

        if not updates:
            return None

        return {"target_name": target, "updates": updates}

    def _resolve_player_ref(self, players: List[Any], ref: str) -> List[Any]:
        """
        Resolve a player reference.

        Supports:
        - "1", "2", ... (roster_index order)
        - full/partial name match
        """
        r = (ref or "").strip()
        if not r:
            return []

        # Numeric reference: roster slot
        if r.isdigit():
            n = int(r)
            ordered = sorted(
                players,
                key=lambda p: (
                    (p.roster_index is None),
                    (p.roster_index or 10**9),
                    p.created_at,
                    str(p.id),
                ),
            )
            if 1 <= n <= len(ordered):
                return [ordered[n - 1]]
            return []

        q = " ".join(r.lower().split())
        matches = []
        for p in players:
            full = " ".join(f"{p.first_name} {p.last_name}".strip().lower().split())
            if full == q:
                return [p]
            if q in full:
                matches.append(p)
        return matches

    def _normalize_gender(self, s: str) -> str:
        v = (s or "").strip().lower()
        if "fem" in v:
            return "femenil"
        if "mix" in v:
            return "mixto"
        if "var" in v or "masc" in v:
            return "varonil"
        return s.strip()

    def _parse_birth_date(self, s: str) -> Optional[date]:
        try:
            parts = (s or "").strip().replace("-", "/").split("/")
            if len(parts) != 3:
                return None
            dd, mm, yy = int(parts[0]), int(parts[1]), int(parts[2])
            if yy < 100:
                yy = 2000 + yy if yy < 50 else 1900 + yy
            return date(yy, mm, dd)
        except Exception:
            return None

    def _label_for_team_field(self, field: str) -> str:
        return {
            "name": "Nombre del equipo",
            "representative_name": "Representante",
            "contact_email": "Correo de contacto",
            "municipality": "Municipio",
            "state": "Estado",
            "gender": "Rama",
            "category": "Categoria",
            "league": "Liga",
        }.get(field, field)

    def _label_for_player_field(self, field: str) -> str:
        return {
            "full_name": "nombre",
            "birth_date": "nacimiento",
            "curp": "CURP",
            "email": "correo",
        }.get(field, field)

    async def _sync_team_updates_to_supabase(
        self,
        *,
        team: Any,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        requested_updates = {k: v for k, v in dict(updates or {}).items() if k in TEAM_FIELDS_WITH_SAFE_SUPABASE_SYNC}
        if not requested_updates:
            return None
        tournament_slug_hint = (getattr(team, "tournament_slug", None) or "").strip() or None
        if not tournament_slug_hint:
            return None
        try:
            from samchat.tournaments_v2.adapters import (
                infer_tournament_key_from_slug,
                update_team_fields_v2,
                update_team_registration_v2,
            )
            tournament_key = infer_tournament_key_from_slug(tournament_slug_hint)
            simple_updates = {
                k: v
                for k, v in requested_updates.items()
                if k not in {"gender", "category"}
            }
            structural_category = requested_updates.get("category")
            structural_branch = requested_updates.get("gender")

            combined_applied: List[str] = []
            combined_local_only: List[str] = []
            combined_payload: Dict[str, Any] = {
                "created": False,
                "dry_run": False,
                "source": "supabase_tournaments_v2",
                "tournament": None,
                "team": None,
                "requested_fields": sorted(requested_updates.keys()),
            }

            if simple_updates:
                simple_result = await update_team_fields_v2(
                    tournament_key=tournament_key,
                    tournament_slug=tournament_slug_hint,
                    team_name=getattr(team, "name", None),
                    updates=simple_updates,
                )
                combined_payload["tournament"] = simple_result.get("tournament")
                combined_payload["team"] = simple_result.get("team")
                combined_applied.extend(simple_result.get("applied_fields") or [])
                combined_local_only.extend(simple_result.get("local_only_fields") or [])
                if simple_result.get("manager"):
                    combined_payload["manager"] = simple_result.get("manager")

            if structural_category or structural_branch:
                structural_result = await update_team_registration_v2(
                    tournament_key=tournament_key,
                    tournament_slug=tournament_slug_hint,
                    team_name=getattr(team, "name", None),
                    current_category_name=getattr(team, "category", None),
                    target_category_name=structural_category,
                    target_branch=structural_branch,
                )
                combined_payload["tournament"] = combined_payload.get("tournament") or structural_result.get("tournament")
                combined_payload["team"] = combined_payload.get("team") or structural_result.get("team")
                combined_payload["registration"] = structural_result.get("registration")
                combined_applied.extend(structural_result.get("applied_fields") or [])
                combined_local_only.extend(structural_result.get("local_only_fields") or [])

            combined_payload["created"] = bool(combined_applied)
            combined_payload["applied_fields"] = sorted(set(combined_applied))
            combined_payload["local_only_fields"] = sorted(set(combined_local_only))
            return combined_payload
        except Exception:
            logger.warning("Supabase team update sync failed; keeping local correction only", exc_info=True)
            return None

    async def _sync_player_updates_to_supabase(
        self,
        *,
        team: Any,
        player: Any,
        updates: Dict[str, Any],
        original_player: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        safe_updates = {k: v for k, v in dict(updates or {}).items() if k in PLAYER_FIELDS_WITH_SAFE_SUPABASE_SYNC}
        if not safe_updates:
            return None
        tournament_slug_hint = (getattr(team, "tournament_slug", None) or "").strip() or None
        category_name_hint = (getattr(team, "category", None) or "").strip() or None
        if not tournament_slug_hint or not category_name_hint:
            return None
        try:
            from samchat.tournaments_v2.adapters import (
                infer_tournament_key_from_slug,
                update_player_fields_v2,
            )

            birth_date_original = original_player.get("birth_date")
            if isinstance(birth_date_original, date):
                birth_date_match = birth_date_original.isoformat()
            else:
                birth_date_match = str(birth_date_original or "")

            return await update_player_fields_v2(
                tournament_key=infer_tournament_key_from_slug(tournament_slug_hint),
                tournament_slug=tournament_slug_hint,
                team_name=getattr(team, "name", None),
                category_name=category_name_hint,
                match_curp=original_player.get("curp"),
                match_first_name=original_player.get("first_name"),
                match_last_name=original_player.get("last_name"),
                match_birth_date=birth_date_match,
                updates=safe_updates,
            )
        except Exception:
            logger.warning("Supabase player update sync failed; keeping local correction only", exc_info=True)
            return None

    def _try_extract_representative_name(self, text: str) -> Optional[str]:
        """Extract representative name from a free-text correction message."""
        import re

        t = (text or "").strip()
        # Common patterns in Spanish
        patterns = [
            r"(?:representante|representate|reprsentate)\s*(?:se\s+llama|es)\s+(.+)$",
            r"(?:representante|representate|reprsentate)\s*:\s*(.+)$",
        ]
        for pat in patterns:
            m = re.search(pat, t, flags=re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                if len(name) >= 3:
                    return name
        return None

    async def _quick_update_latest_team_field(
        self,
        chat_id: int,
        user_id: int,
        field: str,
        new_value: str,
        label: str,
    ) -> str:
        """Update the latest team for this chat, for simple corrections."""
        return (
            "Esta correccion fue denegada. Usa el dashboard para crear "
            "un sucesor REG-S07 con evidencia y recibos."
        )

        if not self.db:
            return "❌ No hay conexion a BD para corregir datos."

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                team = await copa_db.get_latest_team_by_chat(chat_id)
                if not team:
                    return "📭 No encuentro un equipo reciente para corregir en este chat."

                original = getattr(team, field, None)
                await copa_db.update_team(team.id, **{field: new_value})
                supabase_sync_result = await self._sync_team_updates_to_supabase(
                    team=team,
                    updates={field: new_value},
                )

                # Attach to latest registration for audit if possible
                regs = await copa_db.get_registrations_by_chat(chat_id, limit=5)
                reg_id = None
                for r in regs:
                    if r.team_id and r.team_id == team.id:
                        reg_id = r.id
                        break
                if reg_id:
                    await copa_db.log_validation(
                        registration_id=reg_id,
                        field_name=f"team_{field}",
                        original_value=str(original) if original is not None else "",
                        corrected_value=new_value,
                        validation_action="corrected",
                        telegram_chat_id=chat_id,
                    )
                    await copa_db.mark_registration_reviewed(reg_id, "corrected", team_id=team.id)

                await copa_db.commit()

            if supabase_sync_result:
                local_only = supabase_sync_result.get("local_only_fields") or []
                if local_only:
                    return (
                        f"✅ Actualizado {label}: *{new_value}*\n"
                        "ℹ️ Supabase parcial; solo local en: " + ", ".join(sorted(local_only))
                    )
                return f"✅ Actualizado {label}: *{new_value}*\n✅ Supabase sincronizado."
            return f"✅ Actualizado {label}: *{new_value}*\nℹ️ Sync Supabase pendiente."

        except Exception as e:
            logger.error(f"❌ quick_update_latest_team_field failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude aplicar la corrección")

    async def _start_corrections(self, chat_id: int):
        """Show correction menu for the latest team in this chat."""
        return (
            "Las correcciones post-captura se realizan en el dashboard. "
            "Telegram no tiene autoridad REG-S07 para modificar equipos o jugadores."
        )

        if not self.db:
            return "❌ No hay conexion a BD para corregir datos."

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                team = await copa_db.get_latest_team_by_chat(chat_id)
                if not team:
                    return "📭 No encuentro un equipo reciente para corregir en este chat."

                players = await copa_db.get_players_by_team(team.id)

            category_value = team.category or "-"
            state_value = team.state or "-"
            municipality_value = team.municipality or "-"
            players_count = len(players)
            tournament_slug_hint = (getattr(team, "tournament_slug", None) or "").strip() or None
            if tournament_slug_hint and getattr(team, "name", None):
                try:
                    from samchat.tournaments_v2.adapters import (
                        infer_tournament_key_from_slug,
                        team_summary_query_v2,
                    )

                    summary = await team_summary_query_v2(
                        tournament_key=infer_tournament_key_from_slug(tournament_slug_hint),
                        tournament_slug=tournament_slug_hint,
                        team_name=team.name,
                    )
                    category_value = ((summary.get("category") or {}).get("name") or category_value)
                    state_value = ((summary.get("team") or {}).get("state") or state_value)
                    players_count = int(summary.get("players_count") or players_count)
                except Exception:
                    logger.warning("Supabase team summary failed in correction menu; using legacy values", exc_info=True)

            text = (
                "✏️ *Correcciones*\n\n"
                f"⚽ Equipo: *{team.name}*\n"
                f"🏷️ Categoria: {category_value}\n"
                f"📍 Estado/Municipio: {state_value}/{municipality_value}\n"
                f"👥 Jugadores: {players_count}\n\n"
                "¿Que quieres corregir?"
            )

            keyboard = {
                "inline_keyboard": [
                    [{"text": "⚽ Nombre del equipo", "callback_data": f"edit_team:name:{team.id}"}],
                    [{"text": "👤 Representante", "callback_data": f"edit_team:representative_name:{team.id}"}],
                    [{"text": "✉️ Correo de contacto", "callback_data": f"edit_team:contact_email:{team.id}"}],
                    [{"text": "🏟️ Liga", "callback_data": f"edit_team:league:{team.id}"}],
                    [{"text": "🚻 Rama", "callback_data": f"edit_team:gender:{team.id}"}],
                    [{"text": "🏷️ Categoria del equipo", "callback_data": f"edit_team:category:{team.id}"}],
                    [{"text": "📍 Estado", "callback_data": f"edit_team:state:{team.id}"}],
                    [{"text": "📍 Municipio", "callback_data": f"edit_team:municipality:{team.id}"}],
                    [{"text": "👤 Corregir jugador", "callback_data": f"edit_player_menu:{team.id}"}],
                    [{"text": "❌ Cancelar", "callback_data": "edit_cancel"}],
                ]
            }
            return {"text": text, "reply_markup": keyboard}

        except Exception as e:
            logger.error(f"❌ start_corrections failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude abrir el menú de correcciones")

    async def _apply_pending_edit(self, chat_id: int, user_id: int, new_value: str):
        """Apply the pending edit for this chat using the provided text as the new value."""
        self.pending_edits.pop(chat_id, None)
        return (
            "Esta correccion fue denegada. Usa el dashboard para crear "
            "un sucesor REG-S07 con evidencia y recibos."
        )

        state = self.pending_edits.get(chat_id) or {}
        entity = state.get("entity")
        field = state.get("field")
        entity_id = state.get("id")
        registration_id = state.get("registration_id")

        if not entity or not field or not entity_id:
            self.pending_edits.pop(chat_id, None)
            return "⚠️ No hay edicion pendiente. Escribe `corregir` para iniciar."

        if not self.db:
            return "❌ No hay conexion a BD."

        new_value = (new_value or "").strip()
        if not new_value:
            return "⚠️ Valor vacio. Escribe el valor correcto."

        try:
            from uuid import UUID

            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                supabase_player_sync = None

                if entity == "team":
                    team = await copa_db.get_team_by_id(UUID(entity_id))
                    if not team:
                        self.pending_edits.pop(chat_id, None)
                        return "❌ Equipo no encontrado."

                    original = getattr(team, field, None)
                    updated = await copa_db.update_team(UUID(entity_id), **{field: new_value})
                    if not updated:
                        self.pending_edits.pop(chat_id, None)
                        return "❌ No se pudo actualizar el equipo."
                    supabase_sync_result = await self._sync_team_updates_to_supabase(
                        team=team,
                        updates={field: new_value},
                    )

                    # Log validation
                    if registration_id:
                        await copa_db.log_validation(
                            registration_id=UUID(registration_id),
                            field_name=f"team_{field}",
                            original_value=str(original) if original is not None else "",
                            corrected_value=new_value,
                            validation_action="corrected",
                            telegram_chat_id=chat_id,
                        )
                        await copa_db.mark_registration_reviewed(UUID(registration_id), "corrected", team_id=updated.id)

                elif entity == "player":
                    player = await copa_db.session.get(
                        __import__("devnous.copa_telmex.models", fromlist=["Player"]).Player,
                        UUID(entity_id),
                    )
                    if not player:
                        self.pending_edits.pop(chat_id, None)
                        return "❌ Jugador no encontrado."

                    original = getattr(player, field, None)
                    update_fields: Dict[str, Any] = {}
                    if field == "full_name":
                        parts = new_value.split()
                        if len(parts) < 2:
                            return "⚠️ Escribe nombre y apellidos (ej: Juan Garcia Lopez)."
                        update_fields["first_name"] = parts[0]
                        update_fields["last_name"] = " ".join(parts[1:])
                    elif field == "birth_date":
                        try:
                            dd, mm, yy = new_value.split("/")
                            update_fields["birth_date"] = date(int(yy), int(mm), int(dd))
                        except Exception:
                            return "⚠️ Formato invalido. Usa DD/MM/YYYY (ej: 23/03/2007)."
                    else:
                        update_fields[field] = new_value

                    updated = await copa_db.update_player(UUID(entity_id), **update_fields)
                    if not updated:
                        self.pending_edits.pop(chat_id, None)
                        return "❌ No se pudo actualizar el jugador."
                    team = await copa_db.get_team_by_id(player.team_id)
                    original_player_payload = {
                        "first_name": player.first_name,
                        "last_name": player.last_name,
                        "email": player.email,
                        "curp": player.curp,
                        "birth_date": player.birth_date,
                    }
                    supabase_player_sync = await self._sync_player_updates_to_supabase(
                        team=team,
                        player=player,
                        updates={field: new_value},
                        original_player=original_player_payload,
                    )

                    if registration_id:
                        await copa_db.log_validation(
                            registration_id=UUID(registration_id),
                            field_name=f"player_{field}",
                            original_value=str(original) if original is not None else "",
                            corrected_value=new_value,
                            validation_action="corrected",
                            telegram_chat_id=chat_id,
                        )
                        await copa_db.mark_registration_reviewed(UUID(registration_id), "corrected", team_id=updated.team_id)

                await copa_db.commit()

            self.pending_edits.pop(chat_id, None)
            if entity == "team":
                if supabase_sync_result:
                    local_only = supabase_sync_result.get("local_only_fields") or []
                    if local_only:
                        return (
                            "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\n"
                            "ℹ️ Supabase parcial; solo local en: " + ", ".join(sorted(local_only))
                        )
                    return "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\n✅ Supabase sincronizado."
                return "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\nℹ️ Sync Supabase pendiente."
            if entity == "player":
                if supabase_player_sync:
                    local_only = supabase_player_sync.get("local_only_fields") or []
                    if local_only:
                        return (
                            "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\n"
                            "ℹ️ Supabase parcial en jugador; solo local en: " + ", ".join(sorted(local_only))
                        )
                    return "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\n✅ Jugador sincronizado en Supabase."
                return "✅ Correccion aplicada. Escribe `corregir` para hacer otra.\nℹ️ Sync Supabase de jugador pendiente."
            return "✅ Correccion aplicada. Escribe `corregir` para hacer otra."

        except Exception as e:
            logger.error(f"❌ apply_pending_edit failed: {e}", exc_info=True)
            return self._generic_retry_error("No pude aplicar la corrección")

    async def register_team(self, message) -> str:
        """Register a team (with OCR if photo provided)"""
        team = {
            'id': len(self.teams) + 1,
            'name': message.data.get('team_name', 'Team'),
            'category': message.data.get('category', 'General'),
            'registered_at': datetime.now(),
            'players_count': 0
        }

        self.teams.append(team)

        return f"""✅ *Equipo Registrado*

Nombre: {team['name']}
Categoría: {team['category']}
Fecha: {team['registered_at'].strftime('%Y-%m-%d')}

Total equipos: {len(self.teams)}
"""

    async def list_teams(self) -> str:
        """List registered teams"""
        if not self.teams:
            return "📭 No hay equipos registrados"

        lines = ["🏆 *Equipos Registrados*\n"]
        for team in self.teams:
            lines.append(f"• {team['name']} ({team['category']}) - {team['players_count']} jugadores")

        return "\n".join(lines)

    async def schedule_match(self, message) -> str:
        """Schedule a match"""
        match = {
            'id': len(self.matches) + 1,
            'team_a': message.data.get('team_a', 'Team A'),
            'team_b': message.data.get('team_b', 'Team B'),
            'date': message.data.get('date', datetime.now()),
            'venue': message.data.get('venue', 'TBD'),
            'status': 'scheduled'
        }

        self.matches.append(match)

        return f"""✅ *Partido Programado*

{match['team_a']} vs {match['team_b']}
Fecha: {match['date']}
Cancha: {match['venue']}

Total partidos: {len(self.matches)}
"""

    async def show_calendar(self) -> str:
        """Show match calendar"""
        if not self.matches:
            return "📭 No hay partidos programados"

        lines = ["📅 *Calendario de Partidos*\n"]
        for match in self.matches[-10:]:
            lines.append(f"• {match['team_a']} vs {match['team_b']} - {match['date']}")

        return "\n".join(lines)

    async def get_metrics(self) -> Dict[str, Any]:
        """Get operations metrics"""
        return {
            'teams_registered': len(self.teams),
            'players_registered': sum(t.get('players_count', 0) for t in self.teams),
            'matches_scheduled': len(self.matches),
            'matches_completed': sum(1 for m in self.matches if m['status'] == 'completed'),
            'venues_booked': len(self.venues)
        }

    # ==================== OCR FUNCTIONALITY ====================

    async def process_ocr_registration(self, message):
        """Process registration OCR from a photo (team + players)."""
        if not self.ocr_enabled:
            return "❌ OCR no está habilitado para este torneo"

        try:
            chat_id = message.chat_id
            photo_bytes = message.photo

            # Process image
            image = Image.open(io.BytesIO(photo_bytes))
            logger.info(f"🖼️  Image: {image.size}, {image.mode}")

            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGB')

            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            img_byte_arr.seek(0)
            optimized_bytes = img_byte_arr.getvalue()

            image_b64 = base64.b64encode(optimized_bytes).decode('utf-8')

            # If we just saved the front side, treat the next photo as the back side.
            if chat_id in self.pending_back_photos:
                pending = self.pending_back_photos[chat_id]
                team_id = pending.get("team_id")
                provider = (pending.get("provider") or "anthropic").strip().lower()
                return await self._process_back_photo(
                    chat_id=chat_id,
                    user_id=message.user_id,
                    team_id=team_id,
                    optimized_bytes=optimized_bytes,
                    image_b64=image_b64,
                    provider=provider,
                )

            provider = (self.ocr_provider or "").strip().lower()

            # Legacy single-player OCR flow (kept for backwards compatibility).
            if provider == "claude_vision":
                if not self.claude or not self.validator:
                    return "❌ OCR (Anthropic) no esta configurado (falta ANTHROPIC_API_KEY)."
                return await self._legacy_single_player_ocr(chat_id, image_b64)

            if provider in ("claude_structured", "anthropic"):
                return await self._ocr_single_provider(
                    chat_id=chat_id,
                    user_id=message.user_id,
                    optimized_bytes=optimized_bytes,
                    image_b64=image_b64,
                    provider="anthropic",
                )

            if provider in ("openai_vision", "openai"):
                return await self._ocr_single_provider(
                    chat_id=chat_id,
                    user_id=message.user_id,
                    optimized_bytes=optimized_bytes,
                    image_b64=image_b64,
                    provider="openai",
                )

            if provider in ("local", "local_only"):
                return await self._ocr_single_provider(
                    chat_id=chat_id,
                    user_id=message.user_id,
                    optimized_bytes=optimized_bytes,
                    image_b64=image_b64,
                    provider="local",
                )

            if provider in ("local_first", "hybrid_local_remote"):
                return await self._ocr_local_first(
                    chat_id=chat_id,
                    user_id=message.user_id,
                    optimized_bytes=optimized_bytes,
                    image_b64=image_b64,
                )

            # Default: compare both
            return await self._ocr_compare(
                chat_id=chat_id,
                user_id=message.user_id,
                optimized_bytes=optimized_bytes,
                image_b64=image_b64,
            )

        except Exception as e:
            logger.error(f"❌ OCR Error: {e}", exc_info=True)
            return self._generic_retry_error("No pude procesar la imagen")

    async def _process_back_photo(
        self,
        chat_id: int,
        user_id: int,
        team_id: Optional[str],
        optimized_bytes: bytes,
        image_b64: str,
        provider: str,
    ):
        """Process back-side photo and append players to an existing team."""
        if not team_id:
            pending = self.pending_back_photos.get(chat_id) or {}
            review_session_id = pending.get("review_session_id")
            if not review_session_id:
                self.pending_back_photos.pop(chat_id, None)
                return "⚠️ No tengo referencia del equipo para la vuelta. Escribe `corregir` o vuelve a registrar."

        extraction, raw = await self._extract_registration_form(provider, optimized_bytes, image_b64)
        if extraction is None:
            return f"❌ OCR fallo en la vuelta con {provider}."

        pending = self.pending_back_photos.get(chat_id) or {}
        review_session_id = pending.get("review_session_id")
        if review_session_id:
            ok, result = await self._append_back_photo_to_review_session(
                review_session_id=str(review_session_id),
                optimized_bytes=optimized_bytes,
                extraction=extraction,
                raw_payload=raw,
                provider=provider,
            )
            if ok:
                page_count = int(pending.get("page_count") or 1) + 1
                max_pages = int(pending.get("max_pages") or self._telegram_review_max_pages())
                if page_count >= max_pages:
                    self.pending_back_photos.pop(chat_id, None)
                    next_step = (
                        f"Ya tengo {page_count} página(s), cierro este equipo para revisión. "
                        "La siguiente foto iniciará otra precaptura."
                    )
                else:
                    pending["page_count"] = page_count
                    pending["max_pages"] = max_pages
                    self.pending_back_photos[chat_id] = pending
                    next_step = (
                        f"Esta sesión lleva {page_count} página(s). "
                        "Puedes enviar otra página del mismo equipo o presionar 'No hay más páginas'."
                    )
                return (
                    "✅ *Página agregada a la revisión web*\n\n"
                    f"{next_step}\n\n"
                    f"Revisión: {result}"
                )
            return f"❌ No pude agregar la vuelta a la revisión web.\n{result}"

        ok, msg = await self._append_players_to_team(
            chat_id=chat_id,
            user_id=user_id,
            team_id=team_id,
            extraction=extraction,
            provider=provider,
            raw_payload=raw,
            source_image=Image.open(io.BytesIO(optimized_bytes)),
        )
        if ok:
            self.pending_back_photos.pop(chat_id, None)
            return f"✅ *Vuelta procesada*\n\n{msg}\n\nEscribe `corregir` si quieres ajustar algo."
        return f"❌ Error guardando vuelta.\n{msg}"

    async def _legacy_single_player_ocr(self, chat_id: int, image_b64: str):
        """Legacy flow: extract single player fields with Claude JSON prompt."""
        logger.info("🤖 Calling Claude Vision API (legacy single-player)...")
        loop = asyncio.get_event_loop()
        ocr_result = await loop.run_in_executor(None, self._call_claude_vision, image_b64)

        player_name = ocr_result.get("player_name", "")
        ocr_confidence = ocr_result.get("confidence", 0.0)
        logger.info(f"🔍 OCR Result: player_name='{player_name}', confidence={ocr_confidence}")

        if not player_name:
            return (
                "⚠️  *No se detecto nombre del jugador*\n\n"
                "Por favor verifica que:\n"
                "• El nombre este visible\n"
                "• La foto tenga buena iluminacion\n"
                "• El texto sea legible"
            )

        validation_result = self.validator.validate_full_name(player_name, confidence=ocr_confidence)
        if validation_result.get("needs_human_review"):
            return await self._request_human_verification(chat_id, player_name, validation_result, ocr_result)

        return await self._send_final_confirmation(chat_id, ocr_result, validation_result)

    async def _ocr_single_provider(
        self,
        chat_id: int,
        user_id: int,
        optimized_bytes: bytes,
        image_b64: str,
        provider: str,
    ):
        extraction, raw = await self._extract_registration_form(provider, optimized_bytes, image_b64)
        if extraction is None:
            return f"❌ OCR fallo con {provider} (revisa keys/logs)."

        tournament_slug, tournament_conf, tournament_reason = await self._infer_tournament_slug(
            optimized_bytes=optimized_bytes,
            image_b64=image_b64,
        )
        category_guess, category_conf, category_reason = self._infer_category_from_birthdates(
            extraction=extraction,
            tournament_slug=tournament_slug,
        )

        header_ok, header_error = self._validate_required_team_header(extraction)
        if not header_ok:
            return header_error

        summary = self._format_extraction_summary(extraction, title=f"Resultado ({provider})")
        summary = self._append_inference_summary(
            summary=summary,
            tournament_slug=tournament_slug,
            tournament_confidence=tournament_conf,
            tournament_reason=tournament_reason,
            category_guess=category_guess,
            category_confidence=category_conf,
            category_reason=category_reason,
        )
        if chat_id not in self.admin_chat_ids:
            return summary + "\n\n⚠️ Este chat no tiene permisos para crear una precaptura."

        if self._telegram_auto_web_review_enabled():
            ok, result = await self._create_web_review_session(
                chat_id=chat_id,
                user_id=user_id,
                provider=provider,
                extraction=extraction,
                raw_payload=raw,
                image=Image.open(io.BytesIO(optimized_bytes)),
                tournament_slug=tournament_slug,
                category_guess=category_guess,
                expect_back_photo=True,
            )
            if ok:
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🧾 Abrir precaptura", "url": result}],
                        [{"text": "📋 Ver bandeja", "url": f"{(os.getenv('APP_URL') or 'https://sam.chat').rstrip('/')}/registration-review"}],
                        [{"text": "✅ No hay más páginas", "callback_data": "back_done"}],
                    ]
                }
                return {
                    "text": (
                        "✅ Cédula recibida y enviada a precaptura web.\n"
                        "Si este equipo tiene más páginas, envíalas ahora y se anexarán a la misma revisión.\n"
                        "Si la siguiente foto ya es otro equipo, primero presiona 'No hay más páginas'.\n\n"
                        f"Proveedor: {provider}\n"
                        f"Equipo detectado: {getattr(extraction.team, 'name', 'Sin nombre')}\n"
                        f"Jugadores detectados: {len(extraction.players or [])}\n\n"
                        "Valídala en la plataforma antes de capturar a la base final."
                    ),
                    "reply_markup": keyboard,
                }
            logger.warning("Automatic web review failed; falling back to manual OCR confirmation: %s", result)

        self.pending_saves[chat_id] = {
            "created_at": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "image_b64": image_b64,
            "tournament_slug": tournament_slug,
            "tournament_selected": None,
            "tournament_confidence": tournament_conf,
            "tournament_reason": tournament_reason,
            "category_guess": category_guess,
            "category_confidence": category_conf,
            "category_reason": category_reason,
            "anthropic": raw if provider == "anthropic" else None,
            "openai": raw if provider == "openai" else None,
            "local": raw if provider == "local" else None,
            "anthropic_extraction": extraction.model_dump() if provider == "anthropic" else None,
            "openai_extraction": extraction.model_dump() if provider == "openai" else None,
            "local_extraction": extraction.model_dump() if provider == "local" else None,
        }

        keyboard = {
            "inline_keyboard": [
                [{"text": f"🧾 Crear precaptura ({provider})", "callback_data": f"stage_ocr:{provider}"}],
                [{"text": "❌ Cancelar", "callback_data": "stage_ocr:cancel"}],
            ]
        }
        return {
            "text": (
                summary
                + "\n\nLa finalización directa por Telegram está retirada. "
                "Crea una precaptura y aprueba el commit en la plataforma."
            ),
            "reply_markup": keyboard,
        }

    async def _ocr_local_first(
        self,
        chat_id: int,
        user_id: int,
        optimized_bytes: bytes,
        image_b64: str,
    ):
        local_extraction, local_raw = await self._extract_registration_form(
            "local",
            optimized_bytes,
            image_b64,
        )

        local_ok, local_reason = self._local_extraction_quality(local_extraction)
        if local_extraction is not None and local_ok:
            response = await self._ocr_single_provider(
                chat_id=chat_id,
                user_id=user_id,
                optimized_bytes=optimized_bytes,
                image_b64=image_b64,
                provider="local",
            )
            if isinstance(response, dict):
                response["text"] = (
                    "🧠 OCR local aceptado.\n"
                    f"ℹ️ Calidad: {local_reason}\n\n"
                    f"{response['text']}"
                )
            elif isinstance(response, str):
                response = f"🧠 OCR local aceptado.\nℹ️ Calidad: {local_reason}\n\n{response}"
            return response

        fallback_provider = self._preferred_remote_ocr_provider()
        if fallback_provider:
            response = await self._ocr_single_provider(
                chat_id=chat_id,
                user_id=user_id,
                optimized_bytes=optimized_bytes,
                image_b64=image_b64,
                provider=fallback_provider,
            )
            prefix = (
                "🧠 OCR local no alcanzo el umbral; usando fallback remoto.\n"
                f"ℹ️ Motivo local: {local_reason}\n\n"
            )
            if isinstance(response, dict):
                response["text"] = prefix + response["text"]
            elif isinstance(response, str):
                response = prefix + response
            return response

        if local_extraction is not None:
            response = await self._ocr_single_provider(
                chat_id=chat_id,
                user_id=user_id,
                optimized_bytes=optimized_bytes,
                image_b64=image_b64,
                provider="local",
            )
            prefix = (
                "🧠 OCR local disponible sin fallback remoto.\n"
                f"ℹ️ Calidad local: {local_reason}\n\n"
            )
            if isinstance(response, dict):
                response["text"] = prefix + response["text"]
            elif isinstance(response, str):
                response = prefix + response
            return response

        return (
            "❌ OCR local no disponible y tampoco hay proveedor remoto listo.\n"
            f"Detalle local: {local_raw or local_reason}"
        )

    async def _ocr_compare(
        self,
        chat_id: int,
        user_id: int,
        optimized_bytes: bytes,
        image_b64: str,
    ):
        anthropic_extraction, anthropic_raw = await self._extract_registration_form(
            "anthropic", optimized_bytes, image_b64
        )
        openai_extraction, openai_raw = await self._extract_registration_form(
            "openai", optimized_bytes, image_b64
        )

        tournament_slug, tournament_conf, tournament_reason = await self._infer_tournament_slug(
            optimized_bytes=optimized_bytes,
            image_b64=image_b64,
        )
        # Prefer a category guess based on the extraction that produced more player birth dates.
        best_for_category = anthropic_extraction or openai_extraction
        if anthropic_extraction and openai_extraction:
            def _birth_count(ex):
                c = 0
                for p in (ex.players or []):
                    if getattr(p, "birth_date", None):
                        c += 1
                return c
            best_for_category = anthropic_extraction if _birth_count(anthropic_extraction) >= _birth_count(openai_extraction) else openai_extraction
        category_guess, category_conf, category_reason = (None, 0.0, "Sin datos de nacimiento")
        if best_for_category is not None:
            category_guess, category_conf, category_reason = self._infer_category_from_birthdates(
                extraction=best_for_category,
                tournament_slug=tournament_slug,
            )

        blocks: List[str] = []
        if anthropic_extraction is not None:
            anthropic_header_ok, anthropic_header_error = self._validate_required_team_header(anthropic_extraction)
            if not anthropic_header_ok:
                anthropic_extraction = None
                anthropic_raw = {
                    "error": "missing_required_team_header",
                    "message": anthropic_header_error,
                }
                blocks.append(anthropic_header_error)
            else:
                s = self._format_extraction_summary(anthropic_extraction, title="Anthropic")
                blocks.append(
                    self._append_inference_summary(
                        summary=s,
                        tournament_slug=tournament_slug,
                        tournament_confidence=tournament_conf,
                        tournament_reason=tournament_reason,
                        category_guess=category_guess,
                        category_confidence=category_conf,
                        category_reason=category_reason,
                    )
                )
        else:
            blocks.append("❌ *Anthropic*: fallo (falta `ANTHROPIC_API_KEY` o error API).")

        blocks.append("\n" + ("-" * 24) + "\n")

        if openai_extraction is not None:
            openai_header_ok, openai_header_error = self._validate_required_team_header(openai_extraction)
            if not openai_header_ok:
                openai_extraction = None
                openai_raw = {
                    "error": "missing_required_team_header",
                    "message": openai_header_error,
                }
                blocks.append(openai_header_error)
            else:
                s = self._format_extraction_summary(openai_extraction, title="OpenAI")
                blocks.append(
                    self._append_inference_summary(
                        summary=s,
                        tournament_slug=tournament_slug,
                        tournament_confidence=tournament_conf,
                        tournament_reason=tournament_reason,
                        category_guess=category_guess,
                        category_confidence=category_conf,
                        category_reason=category_reason,
                    )
                )
        else:
            blocks.append("❌ *OpenAI*: fallo (falta `OPENAI_API_KEY` o error API).")

        text = "\n".join(blocks)

        if anthropic_extraction is None and openai_extraction is None:
            return text

        if chat_id not in self.admin_chat_ids:
            return text + "\n\n⚠️ Este chat no tiene permisos para crear una precaptura."

        if self._telegram_auto_web_review_enabled():
            auto_provider = "openai" if openai_extraction is not None else "anthropic"
            auto_extraction = openai_extraction if openai_extraction is not None else anthropic_extraction
            auto_raw = openai_raw if openai_extraction is not None else anthropic_raw
            ok, result = await self._create_web_review_session(
                chat_id=chat_id,
                user_id=user_id,
                provider=auto_provider,
                extraction=auto_extraction,
                raw_payload=auto_raw,
                image=Image.open(io.BytesIO(optimized_bytes)),
                tournament_slug=tournament_slug,
                category_guess=category_guess,
                expect_back_photo=True,
            )
            if ok:
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🧾 Abrir precaptura", "url": result}],
                        [{"text": "📋 Ver bandeja", "url": f"{(os.getenv('APP_URL') or 'https://sam.chat').rstrip('/')}/registration-review"}],
                        [{"text": "✅ No hay más páginas", "callback_data": "back_done"}],
                    ]
                }
                return {
                    "text": (
                        "✅ Cédula recibida y enviada a precaptura web.\n"
                        "Si este equipo tiene más páginas, envíalas ahora y se anexarán a la misma revisión.\n"
                        "Si la siguiente foto ya es otro equipo, primero presiona 'No hay más páginas'.\n\n"
                        f"Proveedor elegido: {auto_provider}\n"
                        f"Equipo detectado: {getattr(auto_extraction.team, 'name', 'Sin nombre')}\n"
                        f"Jugadores detectados: {len(auto_extraction.players or [])}\n\n"
                        "Valídala en la plataforma antes de capturar a la base final."
                    ),
                    "reply_markup": keyboard,
                }
            logger.warning("Automatic web review failed; falling back to compare confirmation: %s", result)

        self.pending_saves[chat_id] = {
            "created_at": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "image_b64": image_b64,
            "tournament_slug": tournament_slug,
            "tournament_selected": None,
            "tournament_confidence": tournament_conf,
            "tournament_reason": tournament_reason,
            "category_guess": category_guess,
            "category_confidence": category_conf,
            "category_reason": category_reason,
            "anthropic": anthropic_raw,
            "openai": openai_raw,
            "anthropic_extraction": anthropic_extraction.model_dump() if anthropic_extraction else None,
            "openai_extraction": openai_extraction.model_dump() if openai_extraction else None,
        }

        keyboard = {
            "inline_keyboard": [
                *(
                    [[{"text": "🧾 Crear precaptura Anthropic", "callback_data": "stage_ocr:anthropic"}]]
                    if anthropic_extraction is not None
                    else []
                ),
                *(
                    [[{"text": "🧾 Crear precaptura OpenAI", "callback_data": "stage_ocr:openai"}]]
                    if openai_extraction is not None
                    else []
                ),
                [{"text": "❌ Cancelar", "callback_data": "stage_ocr:cancel"}],
            ]
        }
        return {
            "text": (
                text
                + "\n\nElige qué resultado enviar a precaptura. "
                "Telegram no puede crear el equipo o los jugadores finales."
            ),
            "reply_markup": keyboard,
        }

    async def _extract_registration_form(
        self,
        provider: str,
        optimized_bytes: bytes,
        image_b64: str,
    ):
        """Return (RegistrationFormExtraction | None, raw_dict | None)."""
        provider = (provider or "").strip().lower()
        try:
            if provider == "local":
                from devnous.agents.ocr_schemas import RegistrationFormExtraction

                extraction_dict, raw = await self.local_ocr_runner.extract_registration_form_from_bytes_async(
                    optimized_bytes,
                )
                if not extraction_dict:
                    return None, raw
                extraction = RegistrationFormExtraction.model_validate(extraction_dict)
                return extraction, raw or extraction_dict

            if provider == "anthropic":
                if not self.ocr_agent:
                    return None, None
                extraction = await self.ocr_agent.extract_registration_form_structured(optimized_bytes)
                return extraction, extraction.model_dump()

            if provider == "openai":
                if not self.openai_key:
                    return None, None
                from devnous.agents.ocr_schemas import RegistrationFormExtraction

                raw = await self._call_openai_vision(image_b64)
                raw = self._normalize_openai_registration_payload(raw)
                extraction = RegistrationFormExtraction.model_validate(raw)
                return extraction, raw

            return None, None
        except Exception as e:
            logger.error(f"❌ extract_registration_form failed ({provider}): {e}", exc_info=True)
            return None, None

    def _openai_model_name(self) -> str:
        return os.getenv("OPENAI_OCR_MODEL", "gpt-4.1-mini")

    async def _call_openai_vision(self, image_b64: str) -> Dict[str, Any]:
        """Call OpenAI Chat Completions Vision and return parsed JSON dict."""
        import aiohttp

        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY missing")

        model = self._openai_model_name()
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }

        prompt = self._openai_ctt_prompt(page_count=1)
        image_content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": self._openai_image_url_from_b64(image_b64)},
            },
        ]
        montage_url = self._build_ctt_openai_montage_url([image_b64])
        if montage_url:
            image_content.append(
                {
                    "type": "text",
                    "text": "Imagen adicional: montaje de casillas recortadas y etiquetadas por plantilla.",
                }
            )
            image_content.append({"type": "image_url", "image_url": {"url": montage_url}})

        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": image_content,
                }
            ],
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"OpenAI HTTP {resp.status}: {body[:500]}")
                data = json.loads(body)

        content = data["choices"][0]["message"]["content"]
        return self._extract_json_object(content)

    def _openai_ctt_prompt(self, *, page_count: int = 1) -> str:
        page_context = (
            "Analiza esta imagen de una CEDULA DE INSCRIPCION Copa Telmex Telcel 2026."
            if page_count <= 1
            else (
                "Analiza estas imagenes de una CEDULA DE INSCRIPCION Copa Telmex Telcel 2026. "
                f"Son {page_count} paginas del mismo expediente; extrae el equipo completo."
            )
        )
        return (
            f"{page_context}\n"
            "El formato tiene encabezado de equipo arriba y tarjetas fijas de participantes. "
            "La hoja 1 puede contener Director Tecnico/Auxiliar y Jugadores 1-8; "
            "la hoja 2 puede contener Jugadores 9-20 sin repetir encabezado.\n\n"
            "Puedes recibir una imagen adicional con casillas recortadas y etiquetas como "
            "'jugador_9 nacimiento'. Usa ese montaje para leer texto fino y la hoja completa para contexto.\n\n"
            "Devuelve SOLO un objeto JSON (sin markdown) con esta estructura:\n"
            "{\n"
            '  \"team\": {\"name\": string, \"category\": string|null, \"gender\": string|null, \"league\": string|null, '
            '\"municipality\": string|null, \"state\": string|null, \"confidence\": number},\n'
            '  \"manager\": null | {\"name\": string, \"role\": string|null, \"phone\": string|null, \"email\": string|null, '
            '\"confidence\": number},\n'
            '  \"players\": [{\"name\": string, \"first_name\": string|null, \"paternal_surname\": string|null, '
            '\"maternal_surname\": string|null, \"birth_date\": string|null, \"curp\": string|null, \"jersey_number\": string|null, '
            '\"position\": string|null, \"photo_region\": null, \"confidence\": number, \"needs_review\": boolean}],\n'
            '  \"overall_confidence\": number,\n'
            '  \"notes\": string|null\n'
            "}\n\n"
            "Reglas:\n"
            "- Extrae por casilla del formulario, no como texto corrido.\n"
            "- Mantén el orden de jugadoras/jugadores del formulario.\n"
            "- No inventes jugadores; las casillas vacias no cuentan.\n"
            "- No inventes CURP; si no esta completa y clara, usa null.\n"
            "- No deduzcas nombres desde fotografias.\n"
            "- photo_region debe ser null; el sistema recorta fotos con plantilla fija.\n"
            "- Usa contexto mexicano para desempatar manuscritos ambiguos.\n"
            "- Si una palabra parece municipio/estado mexicano mal reconocido, usa la alternativa visible mas probable y baja confidence.\n"
            "- Ejemplo: Tacambaro/Tacámbaro es preferible a lecturas inexistentes como Tarumba si la escritura lo permite.\n"
            "- Fechas en DD/MM/YYYY. Si el formulario usa año de dos digitos, normaliza a 20YY cuando sea coherente; si dudas, needs_review=true.\n"
            "- Manuscrito dudoso debe tener confidence <=0.80 y needs_review=true.\n"
            "- Marca needs_review=true cuando una fecha este cortada, ilegible o parcialmente fuera de cuadro.\n"
            "- En notes, lista las dudas relevantes y cualquier foto atipica que requiera revision.\n"
        )

    def _load_ctt_layout(self) -> Dict[str, Any]:
        path = Path(__file__).resolve().parents[4] / "config" / "layout_ctt_2026.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not load CTT layout from %s", path, exc_info=True)
            return {}

    def _ctt_field_crop(self, image: Image.Image, field: Dict[str, Any], *, margin_px: int = 14) -> Image.Image:
        width, height = image.size
        left = int(float(field.get("x") or 0.0) * width) - margin_px
        top = int(float(field.get("y") or 0.0) * height) - margin_px
        right = int((float(field.get("x") or 0.0) + float(field.get("w") or 0.0)) * width) + margin_px
        bottom = int((float(field.get("y") or 0.0) + float(field.get("h") or 0.0)) * height) + margin_px
        left, top, right, bottom = max(0, left), max(0, top), min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            return Image.new("RGB", (40, 24), "white")
        crop = image.crop((left, top, right, bottom))
        scale = 2.2 if crop.height < 110 else 1.6
        return crop.resize(
            (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
            Image.Resampling.BICUBIC,
        )

    def _openai_image_url_from_b64(self, image_b64: str) -> str:
        image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

    def _build_ctt_openai_montage_url(self, image_b64_values: List[str]) -> Optional[str]:
        layout = self._load_ctt_layout()
        if not layout:
            return None

        rows: List[Tuple[str, Image.Image]] = []
        for asset_position, image_b64 in enumerate(image_b64_values):
            page_side = "front" if asset_position == 0 else "back"
            page_layout = ((layout.get("pages") or {}).get(page_side) or {})
            image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            normalized, _metadata = normalize_ctt_template_image(image)

            if page_side == "front":
                for field_name, field in (page_layout.get("header_fields") or {}).items():
                    if isinstance(field, dict):
                        rows.append((f"P{asset_position + 1} header {field_name}", self._ctt_field_crop(normalized, field)))

            cards = page_layout.get("cards") or {}
            for card_name, fields_by_name in cards.items():
                if not isinstance(fields_by_name, dict):
                    continue
                if page_side == "front" and card_name in {"director_tecnico", "auxiliar"}:
                    label_prefix = f"P1 {card_name}"
                elif str(card_name).startswith("jugador_"):
                    label_prefix = f"P{asset_position + 1} {card_name}"
                else:
                    continue
                for field_name in ("nombre", "apellidos", "nacimiento", "curp"):
                    field = fields_by_name.get(field_name)
                    if isinstance(field, dict):
                        rows.append((f"{label_prefix} {field_name}", self._ctt_field_crop(normalized, field)))

        if not rows:
            return None

        label_width = 360
        row_gap = 10
        row_height = max(72, max(crop.height for _, crop in rows) + 12)
        crop_width = max(crop.width for _, crop in rows)
        canvas_width = min(2400, max(1200, label_width + crop_width + 60))
        canvas_height = max(200, (row_height + row_gap) * len(rows) + 30)
        montage = Image.new("RGB", (canvas_width, canvas_height), "white")
        draw = ImageDraw.Draw(montage)
        y = 16
        for label, crop in rows:
            draw.text((18, y + 18), label, fill="black")
            montage.paste(crop, (label_width, y))
            draw.rectangle((label_width, y, min(canvas_width - 20, label_width + crop.width), y + crop.height), outline="gray")
            y += row_height + row_gap

        montage.thumbnail((2400, 6000), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        montage.save(buffer, format="JPEG", quality=86, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

    async def _call_openai_vision_multi(self, image_b64_values: List[str]) -> Dict[str, Any]:
        """Call OpenAI Vision with all pages from one registration expediente."""
        import aiohttp

        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY missing")
        if not image_b64_values:
            raise ValueError("No images provided")

        content: List[Dict[str, Any]] = [
            {"type": "text", "text": self._openai_ctt_prompt(page_count=len(image_b64_values))}
        ]
        for image_b64 in image_b64_values:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._openai_image_url_from_b64(image_b64)},
                }
            )
        montage_url = self._build_ctt_openai_montage_url(image_b64_values)
        if montage_url:
            content.append(
                {
                    "type": "text",
                    "text": "Imagen adicional: montaje de casillas recortadas y etiquetadas por plantilla.",
                }
            )
            content.append({"type": "image_url", "image_url": {"url": montage_url}})

        payload = {
            "model": self._openai_model_name(),
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"OpenAI HTTP {resp.status}: {body[:500]}")
                data = json.loads(body)
        return self._extract_json_object(data["choices"][0]["message"]["content"])

    def _extract_json_object(self, text: str) -> Dict[str, Any]:
        t = (text or "").strip()
        if t.startswith("```"):
            parts = t.split("```")
            t = parts[1] if len(parts) > 1 else t
            t = t.lstrip("json").strip()

        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in response")
        return json.loads(t[start : end + 1])

    def _format_extraction_summary(self, extraction, title: str) -> str:
        team = extraction.team
        manager = getattr(extraction, "manager", None)
        players = extraction.players or []

        lines = [f"📋 *{title}*"]
        lines.append(f"⚽ Equipo: *{team.name}*")
        if getattr(team, "category", None):
            lines.append(f"🏷️ Categoria: {team.category}")
        if getattr(team, "gender", None):
            lines.append(f"🚻 Genero: {team.gender}")
        loc = " / ".join([x for x in [getattr(team, 'municipality', None), getattr(team, 'state', None)] if x])
        if loc:
            lines.append(f"📍 Lugar: {loc}")
        lines.append(f"📊 Confianza equipo: {getattr(team, 'confidence', 0.0):.2f}")

        if manager and getattr(manager, "name", None):
            lines.append(f"👤 Responsable: {manager.name}")

        lines.append(f"👥 Jugadores detectados: *{len(players)}*")
        for idx, p in enumerate(players[:8], 1):
            nm = getattr(p, "name", "N/A")
            conf = getattr(p, "confidence", 0.0)
            nr = getattr(p, "needs_review", False)
            tag = " (rev)" if nr else ""
            lines.append(f"{idx}. {nm}{tag} [{conf:.2f}]")

        needs_review_count = sum(1 for p in players if getattr(p, "needs_review", False))
        if needs_review_count:
            lines.append(f"⚠️ Requieren revision: {needs_review_count}")
        lines.append(f"⭐ Confianza global: {getattr(extraction, 'overall_confidence', 0.0):.2f}")

        return "\n".join(lines)

    def _append_inference_summary(
        self,
        summary: str,
        tournament_slug: str,
        tournament_confidence: float,
        tournament_reason: str,
        category_guess: Optional[str],
        category_confidence: float,
        category_reason: str,
    ) -> str:
        lines = [summary]
        if tournament_slug:
            lines.append(
                f"🏆 Torneo sugerido: `{tournament_slug}` ({tournament_confidence:.2f})"
                + (f" [{tournament_reason}]" if tournament_reason else "")
            )
        if category_guess:
            lines.append(
                f"🧠 Categoria sugerida: `{category_guess}` ({category_confidence:.2f})"
                + (f" [{category_reason}]" if category_reason else "")
            )
        return "\n".join(lines)

    def _ref_year_from_tournament_slug(self, tournament_slug: Optional[str]) -> int:
        slug = (tournament_slug or "").strip().lower()
        m = re.search(r"(20\\d{2})", slug)
        if m:
            return int(m.group(1))
        return datetime.utcnow().year

    def _infer_category_from_birthdates(
        self,
        extraction,
        tournament_slug: Optional[str],
    ) -> Tuple[Optional[str], float, str]:
        """
        Infer category from the majority of player birth dates.

        Returns:
            (category, confidence, reason)
        """
        players = extraction.players or []
        bds: List[date] = []
        for p in players:
            bd = getattr(p, "birth_date", None)
            if not bd:
                continue
            d = self._parse_birth_date(str(bd))
            if d:
                bds.append(d)

        if not bds:
            return None, 0.0, "Sin fechas de nacimiento detectables"

        ref_year = self._ref_year_from_tournament_slug(tournament_slug)
        ages = [max(0, ref_year - d.year) for d in bds]
        if not ages:
            return None, 0.0, "Sin edades calculables"

        med = int(statistics.median(ages))
        spread = statistics.pstdev(ages) if len(ages) > 1 else 0.0
        coverage = len(bds) / max(1, len(players))

        slug = (tournament_slug or "").lower()
        if "beisbol" in slug or slug == "liga-telmex-2026":
            if 9 <= med <= 10:
                return "9-10", min(1.0, 0.65 + 0.25 * coverage + (0.10 if spread <= 1.0 else 0.0)), f"mediana edad={med}"
            if 11 <= med <= 12:
                return "11-12", min(1.0, 0.65 + 0.25 * coverage + (0.10 if spread <= 1.0 else 0.0)), f"mediana edad={med}"
            return None, 0.35, f"edad fuera de rango (mediana={med})"

        # Telmex futbol categories are not by year label; infer Juvenil vs Varonil (and Femenil by gender).
        if "futbol" in slug or "telmex-futbol" in slug or slug.startswith("copa-telmex-"):
            gender = self._normalize_gender(getattr(extraction.team, "gender", "") or "")
            if gender == "femenil":
                conf = min(1.0, 0.60 + 0.30 * coverage + (0.10 if spread <= 1.0 else 0.0))
                return "Femenil", conf, "genero=femenil"
            # Juvenil: majority under/around 18
            if med <= 18:
                conf = min(1.0, 0.60 + 0.30 * coverage + (0.10 if spread <= 1.0 else 0.0))
                return "Juvenil", conf, f"mediana edad={med}"
            conf = min(1.0, 0.55 + 0.30 * coverage + (0.10 if spread <= 1.0 else 0.0))
            return "Varonil", conf, f"mediana edad={med}"

        # Default: generic age group label if it helps (kept for non-telmex tournaments).
        if 6 <= med <= 25:
            conf = min(1.0, 0.55 + 0.30 * coverage + (0.10 if spread <= 1.0 else 0.0))
            return f"Sub-{med}", conf, f"mediana edad={med}"

        return None, 0.25, f"edad atipica (mediana={med})"

    def _category_options_for_tournament(
        self,
        *,
        tournament_slug: Optional[str],
        suggested: Optional[str] = None,
    ) -> List[str]:
        """Return category options constrained by tournament."""
        slug = (tournament_slug or "").strip().lower()
        options: List[str] = []

        if slug in {"copa-telmex-2025", "copa-telmex-2026"}:
            options = ["Varonil", "Femenil", "Juvenil"]
        elif slug == "liga-telmex-2026":
            options = ["9-10", "11-12"]
        elif slug == "copa-club-america":
            options = ["2015", "2016", "2017", "2018", "2019", "2020", "Sub-11", "Varonil 2014", "Femenil 2014"]
        elif slug == "homeless-world-cup":
            options = ["Libre", "Open"]
        else:
            options = ["Sub-13", "Sub-14", "Sub-15", "Sub-16", "Sub-17", "Sub-18", "Libre", "Open", "9-10", "11-12"]

        if suggested:
            s = suggested.strip()
            if s and s not in options:
                options = [s] + options
        return options

    async def _sync_ocr_to_supabase(
        self,
        *,
        chat_id: int,
        tournament_slug: str,
        category_name: str,
        extraction,
        side: str,
    ) -> Tuple[bool, str]:
        """
        Best-effort mirror of a saved OCR extraction to Supabase:
        teams + registrations + players.
        """
        try:
            try:
                from samchat.tournaments_v2.adapters import (
                    append_players_to_team_v2,
                    register_team_from_roster_v2,
                )

                tournament_key = "copa_telmex"
                slug_norm = (tournament_slug or "").strip().lower()
                if "beis" in slug_norm:
                    tournament_key = "beisbol"
                elif "america" in slug_norm:
                    tournament_key = "copa_america"

                team_name = (extraction.team.name or "").strip() or "Unknown Team"
                resp = (extraction.responsables[0] if getattr(extraction, "responsables", None) else None)
                representative_name = (getattr(resp, "name", None) or "No especificado").strip() or "No especificado"
                representative_email = (getattr(resp, "email", None) or "no-email@sam.chat").strip() or "no-email@sam.chat"
                representative_phone = re.sub(r"\D", "", (getattr(resp, "phone", None) or "")) or "0000000000"
                representative_phone = representative_phone[:10].rjust(10, "0")

                roster = []
                skipped_review = 0
                for idx, p in enumerate(extraction.players or [], 1):
                    if bool(getattr(p, "needs_review", False)):
                        skipped_review += 1
                        continue
                    birth_date = None
                    bd = getattr(p, "birth_date", None)
                    if bd:
                        birth_date = self._parse_birth_date(str(bd))
                    roster.append(
                        {
                            "name": getattr(p, "name", None),
                            "first_name": getattr(p, "first_name", None),
                            "last_name": " ".join(
                                x
                                for x in [
                                    (getattr(p, "paternal_surname", None) or "").strip(),
                                    (getattr(p, "maternal_surname", None) or "").strip(),
                                ]
                                if x
                            ).strip()
                            or None,
                            "paternal_surname": getattr(p, "paternal_surname", None),
                            "maternal_surname": getattr(p, "maternal_surname", None),
                            "birth_date": birth_date.isoformat() if birth_date else None,
                            "curp": (getattr(p, "curp", None) or "").strip().upper() or None,
                            "parent_name": representative_name,
                            "parent_email": representative_email,
                            "parent_phone": representative_phone,
                            "jersey_number": idx,
                        }
                    )

                if side == "back":
                    result = await append_players_to_team_v2(
                        tournament_key=tournament_key,
                        tournament_slug=tournament_slug,
                        category_name=category_name,
                        team_name=team_name,
                        representative_name=representative_name,
                        representative_email=representative_email,
                        representative_phone=representative_phone,
                        players=roster,
                    )
                else:
                    result = await register_team_from_roster_v2(
                        tournament_key=tournament_key,
                        tournament_slug=tournament_slug,
                        category_name=category_name,
                        team_name=team_name,
                        municipality=(getattr(extraction.team, "municipality", None) or "").strip() or None,
                        state=(getattr(extraction.team, "state", None) or "No especificado").strip() or "No especificado",
                        phone_number=representative_phone,
                        payment_status="pending",
                        notes=f"Importado via Telegram OCR ({side}). chat_id={chat_id}",
                        representative_name=representative_name,
                        representative_email=representative_email,
                        representative_phone=representative_phone,
                        players=roster,
                    )
                return (
                    True,
                    "Supabase OK (tournaments_v2): "
                    f"team_id={(result.get('team') or {}).get('id')} "
                    f"registration_id={(result.get('registration') or {}).get('id')} "
                    f"players_created={result.get('players_created', 0)} "
                    f"skipped={result.get('players_skipped', 0)} "
                    f"review_skipped={skipped_review}"
                )
            except Exception:
                logger.warning("tournaments_v2 OCR sync failed; falling back to legacy Supabase sync", exc_info=True)

            from devnous.tournaments.core.supabase_sync import (
                SupabaseAdminClient,
                load_supabase_config_from_env,
            )

            cfg = load_supabase_config_from_env()
            if not cfg:
                return False, "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY no configurados en el servidor."

            admin = SupabaseAdminClient(cfg, cache_dir="data")
            import_user_id = await admin.ensure_import_user()

            tournament_id = await admin.get_tournament_id_by_slug(tournament_slug)
            category_id = await admin.get_category_id(tournament_id, category_name)

            team_name = (extraction.team.name or "").strip() or "Unknown Team"
            team = await admin.find_team(tournament_id=tournament_id, user_id=import_user_id, team_name=team_name)
            if not team:
                # Required fields in this schema: user_id, team_name, state, phone_number.
                state = (getattr(extraction.team, "state", None) or "No especificado").strip() or "No especificado"
                resp = (extraction.responsables[0] if getattr(extraction, "responsables", None) else None)
                phone = None
                if resp:
                    phone = getattr(resp, "phone", None)
                phone = (phone or "0000000000").strip()
                # Ensure phone is digits; schema is text but UI expects digits.
                phone_digits = re.sub(r"\\D", "", phone) or "0000000000"

                team_payload = {
                    "user_id": import_user_id,
                    "team_name": team_name,
                    "academy_name": getattr(extraction.team, "league", None),
                    "state": state,
                    "country": "Mexico",
                    "phone_country_code": "+52",
                    "phone_number": phone_digits[:10].rjust(10, "0"),
                    "status": "pending",
                    "tournament_id": tournament_id,
                }
                team = await admin.create_team(team_payload)

            reg = await admin.upsert_registration(
                {
                    "team_id": team["id"],
                    "category_id": category_id,
                    "payment_status": "pending",
                    "notes": f"Importado via Telegram OCR ({side}). chat_id={chat_id}",
                }
            )

            resp = (extraction.responsables[0] if getattr(extraction, "responsables", None) else None)
            parent_name = (getattr(resp, "name", None) or "No especificado").strip() or "No especificado"
            parent_email = (getattr(resp, "email", None) or "no-email@sam.chat").strip() or "no-email@sam.chat"
            parent_phone = re.sub(r"\\D", "", (getattr(resp, "phone", None) or "")) or "0000000000"
            parent_phone = parent_phone[:10].rjust(10, "0")

            created = 0
            payloads = []
            skipped_review = 0
            for p in (extraction.players or []):
                if bool(getattr(p, "needs_review", False)):
                    skipped_review += 1
                    continue
                bd = getattr(p, "birth_date", None)
                birth_date = self._parse_birth_date(str(bd)) if bd else None
                if not birth_date:
                    # Supabase players.birth_date is NOT NULL; skip and let admin fix later.
                    continue

                curp = (getattr(p, "curp", None) or "").strip().upper() or None
                if curp:
                    existing = await admin.get_player_by_curp(curp)
                    if existing:
                        continue

                first_name = (getattr(p, "first_name", None) or "").strip()
                last_name = " ".join(
                    x
                    for x in [
                        (getattr(p, "paternal_surname", None) or "").strip(),
                        (getattr(p, "maternal_surname", None) or "").strip(),
                    ]
                    if x
                ).strip()
                if not first_name or not last_name:
                    full = (getattr(p, "name", None) or "").strip()
                    parts = full.split()
                    if parts:
                        first_name = first_name or parts[0]
                        last_name = last_name or (" ".join(parts[1:]) if len(parts) > 1 else "X")
                if not last_name:
                    last_name = "X"

                payloads.append(
                    {
                        "registration_id": reg["id"],
                        "first_name": first_name or "N/A",
                        "last_name": last_name,
                        "birth_date": birth_date.isoformat(),
                        "parent_name": parent_name,
                        "parent_email": parent_email,
                        "parent_phone": parent_phone,
                        "curp": curp,
                        "paternal_surname": (getattr(p, "paternal_surname", None) or None),
                        "maternal_surname": (getattr(p, "maternal_surname", None) or None),
                        "documents_complete": False,
                        "documents_verified": False,
                    }
                )

            created = await admin.insert_players(payloads)
            skipped = (len(extraction.players or []) - created)
            return True, (
                f"Supabase OK: team_id={team['id']} registration_id={reg['id']} "
                f"players_created={created} skipped={skipped} review_skipped={skipped_review}"
            )
        except Exception as e:
            return False, f"Supabase sync error: {e}"

    async def _infer_tournament_slug(
        self,
        optimized_bytes: bytes,
        image_b64: str,
    ) -> Tuple[str, float, str]:
        """
        Infer tournament based on the logo/labels in the upper-left region.

        Returns:
            (tournament_slug|'unknown', confidence, reason)
        """
        try:
            im = Image.open(io.BytesIO(optimized_bytes)).convert("RGB")
            w, h = im.size
            crop = im.crop((0, 0, int(w * 0.35), int(h * 0.25)))
            # First pass: local OCR keywords (fast, no network)
            text = self._tesseract_ocr_text(crop)
            text_l = text.lower()
            if "beisbol" in text_l or "béisbol" in text_l:
                return "liga-telmex-2026", 0.90, "OCR: beisbol"
            if "futbol" in text_l or "fútbol" in text_l:
                return "copa-telmex-2025", 0.80, "OCR: futbol"

            # Fallback: OpenAI vision logo classification (more robust for pure logos).
            if self.openai_key:
                buf = io.BytesIO()
                crop.save(buf, format="JPEG", quality=80, optimize=True)
                crop_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                slug, conf, reason = await self._call_openai_logo_classifier(crop_b64)
                return slug, conf, reason
        except Exception as e:
            logger.warning(f"tournament inference failed: {e}")

        return "unknown", 0.0, "Sin senales"

    def _tesseract_ocr_text(self, image: Image.Image) -> str:
        """
        Run local tesseract OCR on an image crop (best effort).
        """
        try:
            tmp = Path("/tmp/telmex_logo_crop.jpg")
            image.save(tmp, format="JPEG", quality=85)
            # Use spa+eng because some logos contain EN labels.
            out = subprocess.check_output(
                ["tesseract", str(tmp), "stdout", "-l", "spa+eng", "--psm", "6"],
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                text=True,
            )
            return (out or "").strip()
        except Exception:
            return ""

    async def _call_openai_logo_classifier(self, image_b64: str) -> Tuple[str, float, str]:
        import aiohttp

        model = os.getenv("OPENAI_OCR_MODEL", "gpt-4.1-mini")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        prompt = (
            "Clasifica este logo/encabezado en el torneo correcto.\n"
            "Opciones:\n"
            "- copa-telmex-2025\n"
            "- copa-telmex-2026\n"
            "- liga-telmex-2026\n"
            "- copa-club-america\n"
            "- homeless-world-cup\n"
            "- unknown\n\n"
            "Devuelve SOLO JSON:\n"
            "{\"tournament_slug\":\"...\",\"confidence\":0.0,\"reason\":\"...\"}\n"
            "Si no estas seguro, usa unknown y confidence <= 0.6."
        )
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 120,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ],
                }
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        try:
            parsed = json.loads(content)
            slug = str(parsed.get("tournament_slug") or "unknown").strip()
            conf = float(parsed.get("confidence") or 0.0)
            reason = str(parsed.get("reason") or "").strip()
            if slug not in {"telmex-futbol-2025", "telmex-beisbol-2025-2026", "unknown"}:
                return "unknown", 0.0, "OpenAI: invalid slug"
            conf = max(0.0, min(1.0, conf))
            return slug, conf, reason or "OpenAI"
        except Exception:
            return "unknown", 0.0, "OpenAI: parse fail"

    def _call_claude_vision(self, image_b64: str) -> Dict[str, Any]:
        """Call Claude Vision API (blocking operation)"""
        try:
            message = self.claude.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Extrae la siguiente información del formulario de registro:\n\n"
                                    "1. **Nombre del Jugador (player_name)**: DEBE incluir nombre Y apellido(s)\n"
                                    "2. **Equipo/Club (team_club)**: Nombre del equipo deportivo\n"
                                    "3. **Fecha de nacimiento** (dd/mm/yyyy)\n"
                                    "4. **Categoría** (U10/U12/U14/U16/U18/Open)\n"
                                    "5. **Nombre del padre/tutor**\n"
                                    "6. **Teléfono del tutor**\n\n"
                                    "Si algún campo no es visible, usa 'no visible'.\n\n"
                                    "Responde SOLO en formato JSON:\n"
                                    "{\n"
                                    '  "player_name": "nombre Y apellido",\n'
                                    '  "birth_date": "dd/mm/yyyy o no visible",\n'
                                    '  "category": "categoría o no visible",\n'
                                    '  "parent_name": "nombre o no visible",\n'
                                    '  "parent_phone": "teléfono o no visible",\n'
                                    '  "team_club": "equipo o no visible",\n'
                                    '  "confidence": 0.0-1.0\n'
                                    "}"
                                )
                            }
                        ],
                    }
                ],
            )

            # Extract text from response
            response_text = message.content[0].text.strip()

            # Clean JSON
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            # Extract JSON object
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                json_str = response_text[start_idx:end_idx + 1]
                result = json.loads(json_str)
                return result

            return {'player_name': '', 'confidence': 0.0, 'error': 'Could not parse response'}

        except Exception as e:
            logger.error(f"❌ Claude Vision error: {e}", exc_info=True)
            return {'player_name': '', 'confidence': 0.0, 'error': 'ocr_processing_failed'}

    async def _request_human_verification(
        self,
        chat_id: int,
        detected_name: str,
        validation_result: Dict[str, Any],
        ocr_result: Dict[str, Any]
    ):
        """Request human verification with inline keyboard"""

        # Build inline keyboard
        keyboard = {"inline_keyboard": []}

        # Get suggestions
        parts = validation_result.get('parts', {})
        all_suggestions = []

        if isinstance(parts, dict):
            first_name_suggestions = parts.get('first_name', {}).get('suggestions', [])
            surname_suggestions = []
            for surname_result in parts.get('surnames', []):
                surname_suggestions.extend(surname_result.get('suggestions', []))

            # Reconstruct full name suggestions
            if first_name_suggestions:
                name_parts = detected_name.split()
                for suggestion in first_name_suggestions[:2]:
                    suggested_full = f"{suggestion} {' '.join(name_parts[1:])}"
                    all_suggestions.append(suggested_full)

            if surname_suggestions and len(detected_name.split()) > 1:
                name_parts = detected_name.split()
                for suggestion in surname_suggestions[:2]:
                    suggested_full = f"{name_parts[0]} {suggestion}"
                    all_suggestions.append(suggested_full)

        # Build message
        message_text = f"❓ *Verificación Necesaria*\n\nDetectado: *{detected_name}*\n\n"

        if all_suggestions:
            message_text += "💡 ¿Es correcto?\n"
        else:
            message_text += "⚠️ Nombre no encontrado\n"

        # Add suggestion buttons
        for i, suggestion in enumerate(all_suggestions[:2]):
            keyboard["inline_keyboard"].append([{
                "text": f"✅ {suggestion}",
                "callback_data": f"confirm_{i}_{suggestion}"
            }])

        # Add "use detected" button
        keyboard["inline_keyboard"].append([{
            "text": f"👍 {detected_name}",
            "callback_data": f"use_detected_{detected_name}"
        }])

        # Add "write manually" button
        keyboard["inline_keyboard"].append([{
            "text": "✏️ Corregir",
            "callback_data": "write_manually"
        }])

        # Store pending verification
        self.pending_verifications[chat_id] = {
            'detected_name': detected_name,
            'suggestions': all_suggestions,
            'ocr_result': ocr_result,
            'validation_result': validation_result
        }

        return {
            'text': message_text,
            'reply_markup': keyboard
        }

    async def handle_callback_query(self, callback_query: Dict[str, Any], telegram_adapter):
        """Handle inline keyboard button press"""
        callback_id = callback_query['id']
        chat_id = callback_query['message']['chat']['id']
        data = callback_query['data']

        logger.info(f"📱 Callback: {data}")

        try:
            if data == "back_done":
                self.pending_back_photos.pop(chat_id, None)
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                await telegram_adapter.send_message(chat_id, "✅ Entendido. No se requiere vuelta.")
                return

            if data == "noop":
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                if chat_id in self.pending_back_photos:
                    await telegram_adapter.send_message(
                        chat_id,
                        "⏳ Sigo esperando la foto de la *vuelta*.\n"
                        "En cuanto la envies, la proceso automaticamente.",
                    )
                else:
                    await telegram_adapter.send_message(
                        chat_id,
                        "✅ La vuelta ya fue procesada.\n"
                        "Si quieres corregir algo escribe `corregir`.",
                    )
                return

            if data == "edit_cancel":
                self.pending_edits.pop(chat_id, None)
                await telegram_adapter.answer_callback_query(callback_id, "Cancelado")
                await telegram_adapter.send_message(chat_id, "❌ Correccion cancelada.")
                return

            if data.startswith("edit_team:"):
                # edit_team:<field>:<team_uuid>
                parts = data.split(":", 2)
                if len(parts) != 3:
                    await telegram_adapter.answer_callback_query(callback_id, "Invalido")
                    return
                field = parts[1]
                team_id = parts[2]

                registration_id = None
                if self.db:
                    from devnous.copa_telmex.database import CopaTelmexDB
                    async with self.db() as session:
                        copa_db = CopaTelmexDB(session)
                        regs = await copa_db.get_registrations_by_chat(chat_id, limit=5)
                        for r in regs:
                            if r.team_id and str(r.team_id) == team_id:
                                registration_id = str(r.id)
                                break

                self.pending_edits[chat_id] = {
                    "waiting_value": True,
                    "entity": "team",
                    "field": field,
                    "id": team_id,
                    "registration_id": registration_id,
                }
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                await telegram_adapter.send_message(chat_id, f"✏️ Escribe el nuevo valor para *{field}* del equipo:")
                return

            if data.startswith("edit_player_menu:"):
                team_id = data.split(":", 1)[1]
                if not self.db:
                    await telegram_adapter.send_message(chat_id, "❌ No hay conexion a BD.")
                    return
                try:
                    from uuid import UUID as PlayerMenuUUID

                    from devnous.copa_telmex.database import CopaTelmexDB

                    async with self.db() as session:
                        copa_db = CopaTelmexDB(session)
                        players = await copa_db.get_players_by_team(
                            PlayerMenuUUID(team_id)
                        )

                    if not players:
                        await telegram_adapter.send_message(chat_id, "📭 No hay jugadores para ese equipo.")
                        return

                    kb = {"inline_keyboard": []}
                    # Show by roster_index (top-to-bottom) when available.
                    players_sorted = sorted(
                        players,
                        key=lambda p: (
                            (p.roster_index is None),
                            (p.roster_index or 10**9),
                            p.created_at,
                            str(p.id),
                        ),
                    )
                    for p in players_sorted[:20]:
                        prefix = f"{p.roster_index}. " if p.roster_index else ""
                        kb["inline_keyboard"].append(
                            [{"text": f"✏️ {prefix}{p.full_name}", "callback_data": f"edit_player:{p.id}"}]
                        )
                    kb["inline_keyboard"].append([{"text": "⬅️ Volver", "callback_data": "edit_cancel"}])
                    await telegram_adapter.answer_callback_query(callback_id, "OK")
                    await telegram_adapter.send_message(chat_id, "👤 Elige jugador a corregir:", reply_markup=kb)
                    return
                except Exception as e:
                    logger.error(f"edit_player_menu failed: {e}", exc_info=True)
                    await telegram_adapter.send_message(
                        chat_id,
                        self._generic_retry_error("No pude abrir la lista de jugadores"),
                    )
                    return

            if data.startswith("edit_player:"):
                player_id = data.split(":", 1)[1]

                # Offer field options
                kb = {
                    "inline_keyboard": [
                        [{"text": "👤 Nombre completo", "callback_data": f"edit_player_field:full_name:{player_id}"}],
                        [{"text": "📅 Fecha nacimiento (DD/MM/YYYY)", "callback_data": f"edit_player_field:birth_date:{player_id}"}],
                        [{"text": "🆔 CURP", "callback_data": f"edit_player_field:curp:{player_id}"}],
                        [{"text": "✉️ Email", "callback_data": f"edit_player_field:email:{player_id}"}],
                        [{"text": "❌ Cancelar", "callback_data": "edit_cancel"}],
                    ]
                }
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                await telegram_adapter.send_message(chat_id, "¿Que campo quieres corregir?", reply_markup=kb)
                return

            if data.startswith("edit_player_field:"):
                # edit_player_field:<field>:<player_uuid>
                parts = data.split(":", 2)
                if len(parts) != 3:
                    await telegram_adapter.answer_callback_query(callback_id, "Invalido")
                    return
                field = parts[1]
                player_id = parts[2]

                registration_id = None
                if self.db:
                    from uuid import UUID as PlayerFieldUUID

                    from devnous.copa_telmex.database import CopaTelmexDB

                    async with self.db() as session:
                        copa_db = CopaTelmexDB(session)
                        player = await copa_db.session.get(
                            __import__("devnous.copa_telmex.models", fromlist=["Player"]).Player,
                            PlayerFieldUUID(player_id),
                        )
                        if player:
                            regs = await copa_db.get_registrations_by_chat(chat_id, limit=10)
                            for r in regs:
                                if r.team_id and str(r.team_id) == str(player.team_id):
                                    registration_id = str(r.id)
                                    break

                self.pending_edits[chat_id] = {
                    "waiting_value": True,
                    "entity": "player",
                    "field": field,
                    "id": player_id,
                    "registration_id": registration_id,
                }
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                await telegram_adapter.send_message(chat_id, f"✏️ Escribe el nuevo valor para *{field}* del jugador:")
                return

            if data.startswith(("stage_ocr:", "save_ocr:")):
                action = data.split(":", 1)[1].strip().lower()

                if action == "cancel":
                    self.pending_saves.pop(chat_id, None)
                    await telegram_adapter.answer_callback_query(callback_id, "Cancelado")
                    await telegram_adapter.send_message(chat_id, "❌ Guardado cancelado.")
                    return

                if chat_id not in self.admin_chat_ids:
                    await telegram_adapter.answer_callback_query(callback_id, "Sin permisos")
                    await telegram_adapter.send_message(chat_id, "⚠️ Este chat no tiene permisos para crear una precaptura.")
                    return

                pending = self.pending_saves.get(chat_id)
                if not pending:
                    await telegram_adapter.answer_callback_query(callback_id, "Expirado")
                    await telegram_adapter.send_message(chat_id, "⚠️ No hay OCR pendiente (vuelve a enviar la foto).")
                    return

                if action not in ("anthropic", "openai", "local"):
                    await telegram_adapter.answer_callback_query(callback_id, "Accion invalida")
                    return

                # Ensure we know tournament + category before saving (ask user if uncertain).
                pending = self.pending_saves.get(chat_id) or {}
                pending["selected_provider"] = action
                self.pending_saves[chat_id] = pending

                # Tournament selection is mandatory before save.
                t_selected = (pending.get("tournament_selected") or "").strip() or None
                if not t_selected:
                    t_guess = (pending.get("tournament_slug") or "").strip() or None
                    kb = {
                        "inline_keyboard": [
                            *(
                                [[{"text": f"✅ Usar sugerido: {t_guess}", "callback_data": f"set_tournament:{t_guess}"}]]
                                if t_guess and t_guess != "unknown"
                                else []
                            ),
                            [{"text": "⚾ Liga Telmex Telcel 2026", "callback_data": "set_tournament:liga-telmex-2026"}],
                            [{"text": "⚾ Infantil Béisbol 2026", "callback_data": "set_tournament:liga-telmex-2026"}],
                            [{"text": "❌ Cancelar", "callback_data": "stage_ocr:cancel"}],
                        ]
                    }
                    await telegram_adapter.answer_callback_query(callback_id, "OK")
                    await telegram_adapter.send_message(
                        chat_id,
                        "¿A que torneo corresponde esta cedula?",
                        reply_markup=kb,
                    )
                    return

                # Category selection is mandatory before save.
                c_selected = (pending.get("category_selected") or "").strip() or None
                c_guess = (pending.get("category_guess") or "").strip() or None
                if not c_selected:
                    opts = []
                    if c_guess:
                        opts.append([{"text": f"✅ Usar sugerida: {c_guess}", "callback_data": f"set_category:{c_guess}"}])
                    t_slug_for_opts = (pending.get("tournament_selected") or pending.get("tournament_slug") or "").strip() or None
                    for c in self._category_options_for_tournament(
                        tournament_slug=t_slug_for_opts,
                        suggested=c_guess,
                    ):
                        if c_guess and c == c_guess:
                            continue
                        opts.append([{"text": c, "callback_data": f"set_category:{c}"}])
                    opts.append([{"text": "✏️ Escribir categoria", "callback_data": "set_category_manual"}])
                    opts.append([{"text": "❌ Cancelar", "callback_data": "stage_ocr:cancel"}])
                    kb = {"inline_keyboard": opts[:12]}  # keep keyboard size bounded
                    await telegram_adapter.answer_callback_query(callback_id, "OK")
                    await telegram_adapter.send_message(
                        chat_id,
                        "¿Que categoria corresponde para este equipo?",
                        reply_markup=kb,
                    )
                    return

                extraction_dict = pending.get(f"{action}_extraction")
                if not extraction_dict:
                    await telegram_adapter.answer_callback_query(callback_id, "No disponible")
                    await telegram_adapter.send_message(chat_id, f"⚠️ No hay resultado de {action} para guardar.")
                    return

                from devnous.agents.ocr_schemas import RegistrationFormExtraction

                extraction = RegistrationFormExtraction.model_validate(extraction_dict)
                # Apply confirmed category (or fallback to guess for backwards compatibility).
                final_category = (pending.get("category_selected") or pending.get("category_guess") or getattr(extraction.team, "category", None))
                if final_category:
                    extraction.team.category = str(final_category)
                await telegram_adapter.answer_callback_query(callback_id, "Creando precaptura...")
                ok, msg = await self._stage_pending_registration_review(
                    chat_id,
                    action,
                )
                if ok:
                    self.pending_saves.pop(chat_id, None)
                    await telegram_adapter.send_message(
                        chat_id,
                        f"✅ Precaptura gobernada creada ({action}).\n{msg}\n"
                        "Revísala en la plataforma antes del commit final.",
                    )
                    # Provide an explicit "no back side" option.
                    if chat_id in self.pending_back_photos:
                        kb = {
                            "inline_keyboard": [
                                [{"text": "📸 Ya envie la vuelta", "callback_data": "noop"}],
                                [{"text": "✅ No hay vuelta", "callback_data": "back_done"}],
                            ]
                        }
                        await telegram_adapter.send_message(
                            chat_id,
                            "Cuando estes listo, envia la foto de la *vuelta*.\n"
                            "Si no existe vuelta, presiona 'No hay vuelta'.",
                            reply_markup=kb,
                        )
                else:
                    await telegram_adapter.send_message(chat_id, f"⛔ No se creó estado final ni precaptura.\n{msg}")
                return

            if data.startswith("web_review:"):
                action = data.split(":", 1)[1].strip().lower()
                if action not in ("anthropic", "openai", "local"):
                    await telegram_adapter.answer_callback_query(callback_id, "Accion invalida")
                    return
                if chat_id not in self.admin_chat_ids:
                    await telegram_adapter.answer_callback_query(callback_id, "Sin permisos")
                    await telegram_adapter.send_message(chat_id, "⚠️ Este chat no tiene permisos para enviar a revisión web.")
                    return
                pending = self.pending_saves.get(chat_id)
                if not pending:
                    await telegram_adapter.answer_callback_query(callback_id, "Expirado")
                    await telegram_adapter.send_message(chat_id, "⚠️ No hay OCR pendiente para enviar a revisión web.")
                    return

                await telegram_adapter.answer_callback_query(callback_id, "Creando revisión...")
                ok, result = await self._create_web_review_session_from_pending(chat_id, action)
                if ok:
                    self.pending_saves.pop(chat_id, None)
                    keyboard = {
                        "inline_keyboard": [
                            [{"text": "🧾 Abrir revisión web", "url": result}],
                        ]
                    }
                    await telegram_adapter.send_message(
                        chat_id,
                        "✅ Envié la cédula a la bandeja web de precaptura.\n"
                        "Revísala ahí y aprueba el commit cuando esté lista.\n"
                        "Si también tienes la vuelta, envíala ahora en este chat y la agregaré a la misma sesión.",
                        reply_markup=keyboard,
                    )
                else:
                    await telegram_adapter.send_message(chat_id, f"❌ {result}")
                return

            if data.startswith("set_tournament:"):
                slug = data.split(":", 1)[1].strip()
                pending = self.pending_saves.get(chat_id) or {}
                pending["tournament_slug"] = slug
                pending["tournament_selected"] = slug
                pending["tournament_confidence"] = 1.0
                # Recompute category suggestion using selected tournament year/rules (best effort).
                provider = (pending.get("selected_provider") or "").strip().lower()
                if provider in ("anthropic", "openai", "local"):
                    extraction_dict = pending.get(f"{provider}_extraction")
                    if extraction_dict:
                        try:
                            from devnous.agents.ocr_schemas import (
                                RegistrationFormExtraction,
                            )

                            ex = RegistrationFormExtraction.model_validate(extraction_dict)
                            cat_g, cat_c, cat_r = self._infer_category_from_birthdates(extraction=ex, tournament_slug=slug)
                            pending["category_guess"] = cat_g
                            pending["category_confidence"] = cat_c
                            pending["category_reason"] = cat_r
                        except Exception:
                            pass
                self.pending_saves[chat_id] = pending
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                # After tournament selection, category confirmation is mandatory.
                c_guess = (pending.get("category_selected") or pending.get("category_guess") or "").strip() or None
                c_selected = (pending.get("category_selected") or "").strip() or None
                if not c_selected:
                    opts = []
                    if pending.get("category_guess"):
                        opts.append([{"text": f"✅ Usar sugerida: {pending['category_guess']}", "callback_data": f"set_category:{pending['category_guess']}"}])
                    for c in self._category_options_for_tournament(
                        tournament_slug=slug,
                        suggested=pending.get("category_guess"),
                    ):
                        if pending.get("category_guess") and c == pending.get("category_guess"):
                            continue
                        opts.append([{"text": c, "callback_data": f"set_category:{c}"}])
                    opts.append([{"text": "✏️ Escribir categoria", "callback_data": "set_category_manual"}])
                    opts.append([{"text": "❌ Cancelar", "callback_data": "stage_ocr:cancel"}])
                    kb = {"inline_keyboard": opts[:12]}
                    await telegram_adapter.send_message(
                        chat_id,
                        f"✅ Torneo seleccionado: *{slug}*\n\n¿Que categoria es?",
                        reply_markup=kb,
                    )
                    return

                # If provider + category are already known, finalize save.
                provider = (pending.get("selected_provider") or "").strip().lower()
                if provider in ("anthropic", "openai", "local"):
                    extraction_dict = pending.get(f"{provider}_extraction")
                    if extraction_dict:
                        from devnous.agents.ocr_schemas import (
                            RegistrationFormExtraction,
                        )

                        extraction = RegistrationFormExtraction.model_validate(extraction_dict)
                        final_cat = (pending.get("category_selected") or pending.get("category_guess") or getattr(extraction.team, "category", None))
                        if final_cat:
                            extraction.team.category = str(final_cat)
                        ok, msg = await self._stage_pending_registration_review(
                            chat_id,
                            provider,
                        )
                        if ok:
                            self.pending_saves.pop(chat_id, None)
                            await telegram_adapter.send_message(
                                chat_id,
                                f"✅ Precaptura gobernada creada ({provider}).\n{msg}\n"
                                "Revísala en la plataforma antes del commit final.",
                            )
                            if chat_id in self.pending_back_photos:
                                kb = {
                                    "inline_keyboard": [
                                        [{"text": "📸 Ya envie la vuelta", "callback_data": "noop"}],
                                        [{"text": "✅ No hay vuelta", "callback_data": "back_done"}],
                                    ]
                                }
                                await telegram_adapter.send_message(
                                    chat_id,
                                    "Cuando estes listo, envia la foto de la *vuelta*.\n"
                                    "Si no existe vuelta, presiona 'No hay vuelta'.",
                                    reply_markup=kb,
                                )
                        else:
                            await telegram_adapter.send_message(
                                chat_id,
                                f"⛔ No se creó estado final ni precaptura.\n{msg}",
                            )
                        return

                await telegram_adapter.send_message(chat_id, f"✅ Torneo seleccionado: *{slug}*")
                return

            if data == "set_category_manual":
                pending = self.pending_saves.get(chat_id) or {}
                pending["awaiting_manual_category"] = True
                self.pending_saves[chat_id] = pending
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                await telegram_adapter.send_message(chat_id, "✏️ Escribe la categoria (ej. Sub-15, 9-10):")
                return

            if data.startswith("set_category:"):
                cat = data.split(":", 1)[1].strip()
                pending = self.pending_saves.get(chat_id) or {}
                pending["category_selected"] = cat
                pending["category_confidence"] = 1.0
                self.pending_saves[chat_id] = pending
                await telegram_adapter.answer_callback_query(callback_id, "OK")
                # If provider is already chosen, finalize save immediately.
                provider = (pending.get("selected_provider") or "").strip().lower()
                if provider in ("anthropic", "openai", "local"):
                    extraction_dict = pending.get(f"{provider}_extraction")
                    if not extraction_dict:
                        await telegram_adapter.send_message(chat_id, "⚠️ No encuentro el OCR para guardar. Vuelve a enviar la foto.")
                        return
                    from devnous.agents.ocr_schemas import RegistrationFormExtraction

                    extraction = RegistrationFormExtraction.model_validate(extraction_dict)
                    extraction.team.category = cat
                    ok, msg = await self._stage_pending_registration_review(
                        chat_id,
                        provider,
                    )
                    if ok:
                        self.pending_saves.pop(chat_id, None)
                        await telegram_adapter.send_message(
                            chat_id,
                            f"✅ Precaptura gobernada creada ({provider}).\n{msg}\n"
                            "Revísala en la plataforma antes del commit final.",
                        )
                        if chat_id in self.pending_back_photos:
                            kb = {
                                "inline_keyboard": [
                                    [{"text": "📸 Ya envie la vuelta", "callback_data": "noop"}],
                                    [{"text": "✅ No hay vuelta", "callback_data": "back_done"}],
                                ]
                            }
                            await telegram_adapter.send_message(
                                chat_id,
                                "Cuando estes listo, envia la foto de la *vuelta*.\n"
                                "Si no existe vuelta, presiona 'No hay vuelta'.",
                                reply_markup=kb,
                            )
                    else:
                        await telegram_adapter.send_message(
                            chat_id,
                            f"⛔ No se creó estado final ni precaptura.\n{msg}",
                        )
                    return

                await telegram_adapter.send_message(chat_id, f"✅ Categoria seleccionada: *{cat}*")
                return

            if data.startswith("confirm_"):
                # User selected a suggestion
                parts = data.split("_", 2)
                if len(parts) >= 3:
                    selected_name = parts[2]

                    await telegram_adapter.answer_callback_query(
                        callback_id,
                        f"✅ Confirmado: {selected_name}"
                    )

                    if chat_id in self.pending_verifications:
                        ocr_result = self.pending_verifications[chat_id]['ocr_result']
                        validation_result = self.pending_verifications[chat_id].get('validation_result')
                        ocr_result['player_name'] = selected_name
                        ocr_result['human_verified'] = True

                        response = await self._send_final_confirmation(chat_id, ocr_result, validation_result)
                        await telegram_adapter.send_message(chat_id, response)

                        del self.pending_verifications[chat_id]

            elif data.startswith("use_detected_"):
                detected_name = data.replace("use_detected_", "")

                await telegram_adapter.answer_callback_query(
                    callback_id,
                    f"✅ Usando: {detected_name}"
                )

                if chat_id in self.pending_verifications:
                    ocr_result = self.pending_verifications[chat_id]['ocr_result']
                    validation_result = self.pending_verifications[chat_id].get('validation_result')
                    ocr_result['player_name'] = detected_name
                    ocr_result['human_verified'] = True

                    response = await self._send_final_confirmation(chat_id, ocr_result, validation_result)
                    await telegram_adapter.send_message(chat_id, response)

                    del self.pending_verifications[chat_id]

            elif data == "write_manually":
                await telegram_adapter.answer_callback_query(
                    callback_id,
                    "✏️ Escribe el nombre correcto"
                )

                await telegram_adapter.send_message(
                    chat_id,
                    "✏️ *Escribe el nombre completo del jugador:*\n\nEjemplo: Juan García López\n\n📝 Escríbelo exactamente como debe aparecer."
                )

                if chat_id in self.pending_verifications:
                    self.pending_verifications[chat_id]['waiting_manual'] = True

        except Exception as e:
            logger.error(f"❌ Callback error: {e}", exc_info=True)
            await telegram_adapter.answer_callback_query(
                callback_id,
                "❌ Error interno"
            )

    async def _append_players_to_team(
        self,
        chat_id: int,
        user_id: Optional[int],
        team_id: str,
        extraction,
        provider: str,
        raw_payload: Optional[Dict[str, Any]],
        source_image: Optional[Image.Image] = None,
    ):
        """Append players from an extraction to an existing team."""
        if not self.db:
            return False, "No hay conexion a BD (db_session no configurado)."

        try:
            from uuid import UUID

            from devnous.copa_telmex.database import CopaTelmexDB

            async with self.db() as session:
                copa_db = CopaTelmexDB(session)
                team_uuid = UUID(team_id)
                team = await copa_db.get_team_by_id(team_uuid)
                if not team:
                    return False, "Equipo no encontrado."

                existing_players = await copa_db.get_players_by_team(team_uuid)
                max_idx = max([p.roster_index or 0 for p in existing_players] or [0])
                integrity = self._prepare_extraction_integrity(
                    team_id=team_uuid,
                    extraction=extraction,
                    image=source_image,
                    side="back",
                    existing_players=existing_players,
                )
                integrity_notes = integrity["integrity_notes"]
                photo_artifacts = integrity["photo_artifacts"]

                created_players = 0
                skipped_players = 0
                review_players = 0

                for offset, p in enumerate(extraction.players or [], 1):
                    full_name = (getattr(p, "name", None) or "").strip()
                    if not full_name:
                        continue

                    first_name = (getattr(p, "first_name", None) or "").strip()
                    last_name = " ".join(
                        x
                        for x in [
                            (getattr(p, "paternal_surname", None) or "").strip(),
                            (getattr(p, "maternal_surname", None) or "").strip(),
                        ]
                        if x
                    ).strip()
                    if not first_name or not last_name:
                        parts = full_name.split()
                        if parts:
                            first_name = first_name or parts[0]
                            last_name = last_name or (" ".join(parts[1:]) if len(parts) > 1 else "")

                    birth_date = None
                    bd = getattr(p, "birth_date", None)
                    if bd:
                        birth_date = self._parse_birth_date(bd)

                    curp = (getattr(p, "curp", None) or "").strip() or None
                    existing = None
                    if curp:
                        existing = await copa_db.get_player_by_curp(curp)
                    if not existing:
                        existing = await copa_db.get_player_by_team_and_identity(
                            team_id=team_uuid,
                            first_name=first_name,
                            last_name=last_name,
                            birth_date=birth_date,
                        )
                    if existing:
                        skipped_players += 1
                        continue

                    idx = offset
                    review_reasons = list(integrity_notes.get(idx) or [])
                    photo_artifact = photo_artifacts.get(idx) or {}
                    needs_review = bool(getattr(p, "needs_review", False) or review_reasons)
                    if needs_review:
                        review_players += 1

                    await copa_db.create_player(
                        team_id=team_uuid,
                        first_name=first_name,
                        last_name=last_name,
                        birth_date=birth_date,
                        curp=curp,
                        photo_path=photo_artifact.get("photo_path"),
                        photo_sha256=photo_artifact.get("photo_sha256"),
                        photo_ahash=photo_artifact.get("photo_ahash"),
                        ocr_confidence=getattr(p, "confidence", None),
                        needs_review=needs_review,
                        verified_by_human=False,
                        verification_notes=describe_integrity_reasons(review_reasons) if review_reasons else None,
                        roster_index=max_idx + offset,
                    )
                    created_players += 1

                regs = await copa_db.get_registrations_by_chat(chat_id, limit=10)
                reg_id = None
                for r in regs:
                    if r.team_id and r.team_id == team_uuid:
                        reg_id = r.id
                        break

                await copa_db.create_ocr_registration(
                    telegram_chat_id=chat_id,
                    telegram_user_id=user_id,
                    team_id=team_uuid,
                    ocr_result={
                        "provider": provider,
                        "extraction": extraction.model_dump(),
                        "raw": raw_payload,
                        "side": "back",
                    },
                    validation_result={
                        "provider": provider,
                        "side": "back",
                        "overall_confidence": float(getattr(extraction, "overall_confidence", 0.0) or 0.0),
                    },
                )

                if reg_id:
                    await copa_db.mark_registration_reviewed(reg_id, "corrected", team_id=team_uuid)

                await copa_db.commit()

            sync_note = ""
            tournament_slug = (getattr(team, "tournament_slug", None) or "").strip()
            category_name = (getattr(team, "category", None) or "").strip()
            if tournament_slug and category_name:
                ok_sync, msg_sync = await self._sync_ocr_to_supabase(
                    chat_id=chat_id,
                    tournament_slug=tournament_slug,
                    category_name=category_name,
                    extraction=extraction,
                    side="back",
                )
                sync_note = f"\n\n{'✅' if ok_sync else '⚠️'} {msg_sync}"

            return (
                True,
                f"Equipo: {team.name}\n"
                f"Jugadores agregados: {created_players}\n"
                f"Duplicados/ya existentes: {skipped_players}\n"
                f"Jugadores en revision: {review_players}\n"
                f"Nombres sospechosos: {integrity['flagged_name_count']}\n"
                f"Fotos sospechosas/repetidas: {integrity['flagged_photo_count']}"
                f"{sync_note}",
            )
        except Exception as e:
            logger.error(f"❌ append_players_to_team failed: {e}", exc_info=True)
            return False, self._generic_db_error()

    async def _save_to_database(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Save registration data to database"""
        if not self.db:
            logger.warning("⚠️  No database connection")
            return False

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            # self.db is expected to be an async_sessionmaker
            async with self.db() as session:
                copa_db = CopaTelmexDB(session)

                # Extract team info
                team_name = ocr_result.get('team_club', 'Unknown Team')
                if team_name == 'no visible':
                    team_name = 'Unknown Team'

                # Get or create team
                teams_in_chat = await copa_db.get_teams_by_chat(chat_id)
                team = None
                for t in teams_in_chat:
                    if t.name.lower() == team_name.lower():
                        team = t
                        break

                if not team:
                    logger.info(f"📝 Creating new team: {team_name}")
                    team = await copa_db.create_team(
                        name=team_name,
                        telegram_chat_id=chat_id,
                        category=ocr_result.get('category') if ocr_result.get('category') != 'no visible' else None
                    )
                else:
                    logger.info(f"✅ Found existing team: {team_name} (ID: {team.id})")

                # Parse player name
                player_name = ocr_result.get('player_name', '')
                if not player_name or player_name == 'no visible':
                    logger.warning("⚠️  No player name to save")
                    return False

                # Split name
                name_parts = player_name.split()
                if len(name_parts) < 2:
                    first_name = name_parts[0] if name_parts else ''
                    last_name = ''
                else:
                    first_name = name_parts[0]
                    last_name = ' '.join(name_parts[1:])

                # Parse birth date
                birth_date = None
                birth_date_str = ocr_result.get('birth_date')
                if birth_date_str and birth_date_str != 'no visible':
                    try:
                        if '/' in birth_date_str:
                            parts = birth_date_str.split('/')
                            if len(parts) == 3:
                                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                                if year < 100:
                                    year = 2000 + year if year < 50 else 1900 + year
                                birth_date = date(year, month, day)
                        elif '-' in birth_date_str:
                            parts = birth_date_str.split('-')
                            if len(parts) == 3:
                                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                                if year < 100:
                                    year = 2000 + year if year < 50 else 1900 + year
                                birth_date = date(year, month, day)
                    except (ValueError, IndexError) as e:
                        logger.warning(f"⚠️  Could not parse birth date: {birth_date_str}: {e}")

                # Create player
                logger.info(f"📝 Creating player: {player_name}")

                needs_review = validation_result.get('needs_human_review', False) if validation_result else False
                human_verified = ocr_result.get('human_verified', False)
                confidence = ocr_result.get('confidence', 0.0)

                player = await copa_db.create_player(
                    team_id=team.id,
                    first_name=first_name,
                    last_name=last_name,
                    birth_date=birth_date,
                    ocr_confidence=confidence,
                    needs_review=needs_review,
                    verified_by_human=human_verified,
                    verification_notes='Manually entered' if ocr_result.get('manually_entered') else None
                )

                # Create OCR registration log
                logger.info("📝 Creating OCR registration log")
                registration = await copa_db.create_ocr_registration(
                    telegram_chat_id=chat_id,
                    ocr_result=ocr_result,
                    validation_result=validation_result or {},
                    team_id=team.id
                )

                # Commit
                await copa_db.commit()

                logger.info(
                    f"✅ Saved to database: Team={team.id}, Player={player.id}, Registration={registration.id}"
                )
                return True

        except Exception as e:
            logger.error(f"❌ Database save error: {e}", exc_info=True)
            return False

    async def _send_final_confirmation(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """Send final confirmation with all extracted data"""
        # Save to database first
        db_saved = await self._save_to_database(chat_id, ocr_result, validation_result)

        player_name = ocr_result.get('player_name', 'N/A')
        confidence = ocr_result.get('confidence', 0.0)
        human_verified = ocr_result.get('human_verified', False)
        manually_entered = ocr_result.get('manually_entered', False)

        response = "✅ *Registro Completado*\n\n"

        response += f"👤 *Jugador:* {player_name}\n"

        if manually_entered:
            response += "✏️ *Verificado manualmente*\n"
        elif human_verified:
            response += "👍 *Verificado por humano*\n"
        else:
            response += f"📊 *Confianza:* {confidence * 100:.0f}%\n"

        response += "\n"

        # Add other extracted fields
        other_fields = [
            ('birth_date', '📅 Fecha de nacimiento'),
            ('category', '🏆 Categoría'),
            ('parent_name', '👨‍👩‍👧 Padre/Tutor'),
            ('parent_phone', '📞 Teléfono del tutor'),
            ('team_club', '⚽ Equipo/Club')
        ]

        for field, label in other_fields:
            value = ocr_result.get(field)
            if value and value != 'no visible':
                response += f"{label}: {value}\n"

        response += "\n"

        if db_saved:
            response += "✨ *Datos guardados en base de datos*\n"
            response += "📊 Registro ID guardado exitosamente"
        else:
            response += "⚠️  *Datos no guardados en BD*\n"
            response += "Se mostrará confirmación visual solamente"

        return response

    # ==================== END OCR FUNCTIONALITY ====================

    def get_operations_help(self) -> str:
        """Get help message"""
        ocr_help = ""
        if self.ocr_enabled:
            ocr_help = "• 📸 Enviar foto de formulario (OCR automático)\n"

        return f"""🏃 *Módulo de Operaciones*

*Comandos:*
{ocr_help}• Registrar equipo
• Ver equipos
• Programar partido
• Ver calendario
• /ai_help (workspace de reportes AI)
"""

    async def cleanup(self):
        """Cleanup"""
        logger.info(f"🔌 Operations module cleanup for {self.tournament_id}")
