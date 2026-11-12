"""Materialize review plans from privately checked batch observations.

This is preparation, not approval. The independently signed verifier receipt,
sample activation, genuine parent UV and final SQL checks remain mandatory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import replace
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

from .draft_versioning import draft_content_hash
from .models import Player, Team, TeamStaff
from .registration_review_policy import (
    _build_registration_incident_policy,
    _parse_birth_date,
)
from .staff_batch_authorization import (
    BatchReviewScope,
    BatchSourcePage,
    batch_scope_hash,
    canonical_batch_scope_bytes,
)
from .staff_batch_contracts import BatchContractError, canonical, digest
from .staff_batch_store import BatchDenied
from .staff_batch_verification import normalize_observation
from .staff_capture_effect import (
    bind_review_evidence,
    bind_review_plan,
    freeze_review_batch,
)
from .staff_photo_store import derive_rectangular_review_photo
from .staff_review_plan_builder import ReviewPlanBuilder, _materialize, _policy_date


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _sample_fields(analysis):
    labels = {
        "name": "Equipo",
        "category": "Categoría",
        "gender": "Rama",
        "state": "Estado",
        "municipality": "Municipio",
        "league": "Liga",
        "league_phone": "Teléfono de la liga",
        "league_address": "Domicilio de la liga",
        "representative_name": "Representante",
        "contact_email": "Correo",
        "contact_phone": "Teléfono",
        "first_name": "Nombre",
        "last_name": "Apellidos",
        "birth_date": "Fecha de nacimiento",
        "curp": "CURP",
        "presence": "Presencia",
        "photo_sha256": "Fotografía",
        "role": "Puesto",
    }
    result = []
    for key, value in analysis["verification_input"]["proposed_fields"].items():
        parts = key.split(".")
        label = labels.get(parts[-1], parts[-1])
        if parts[0] in {"players", "staff"}:
            person = (
                f"Jugador {parts[1]}"
                if parts[0] == "players"
                else ("DT" if parts[1] == "1" else "Auxiliar")
            )
            label = f"{person} · {label}"
        elif parts[0] == "slots":
            label = f"Casilla {parts[2]}, página {parts[1]} · Presencia"
        if parts[-1] == "photo_sha256":
            value = "Incluida; contrasta la fuente" if value else "No proporcionada"
        elif value in ("PRESENT", "ABSENT"):
            value = "Presente" if value == "PRESENT" else "Ausente"
        elif parts[-1] == "role":
            value = {
                "director_tecnico": "Director técnico",
                "auxiliar": "Auxiliar",
            }.get(value, value)
        result.append({"field_id": key, "label": label, "value": value})
    return result


def semantic_fields(analysis):
    """Exact field IDs shared by the reader, private verifier and writer plan."""
    allowed_team = {
        "name",
        "category",
        "gender",
        "state",
        "municipality",
        "league",
        "league_phone",
        "league_address",
        "representative_name",
        "contact_email",
        "contact_phone",
    }
    if set(analysis["team"]) - allowed_team:
        raise BatchContractError("BATCH_UNOBSERVED_TEAM_FIELD")
    result = {
        f"team.{name}": analysis["team"].get(name)
        for name in sorted(set(analysis["team"]) | {"name", "category", "gender"})
    }
    for kind in ("players", "staff"):
        for index, person in enumerate(analysis[kind], 1):
            prefix = f"{kind}.{index}."
            for name in ("first_name", "last_name", "birth_date", "curp", "presence"):
                result[prefix + name] = person.get(name)
            result[prefix + "photo_sha256"] = (person.get("photo") or {}).get("sha256")
            if kind == "staff":
                result[prefix + "role"] = person.get("role")
    return result


class BatchPlanBuilder(ReviewPlanBuilder):
    """Reuse deterministic geometry/policy checks with explicit V3 provenance."""

    def build_batch(self, context, source, analysis, snapshot):
        if analysis.get("auto_ready") is not True or analysis.get("issues"):
            raise BatchContractError("BATCH_MEMBER_REQUIRES_REVIEW")
        manifest = source["manifest"]
        if manifest["manifest_sha256"] != context["manifest_sha256"]:
            raise BatchContractError("BATCH_SOURCE_CHANGED")
        if self._phase_binding and any(
            str(context[key]) != str(self._phase_binding[key])
            for key in ("tenant_id", "edition_id", "manifest_sha256")
        ):
            raise BatchContractError("BATCH_POLICY_SCOPE_INVALID")
        if self._policy_hash.removeprefix("sha256:") != context["policy_hash"]:
            raise BatchContractError("BATCH_POLICY_SCOPE_INVALID")
        players, staff = analysis.get("players"), analysis.get("staff")
        if (
            type(players) is not list
            or not 1 <= len(players) <= self._policy["maximum_roster_size"]
            or type(staff) is not list
            or len(staff) != 2
        ):
            raise BatchContractError("BATCH_ROSTER_INVALID")
        present_players = [p for p in players if p.get("presence") == "PRESENT"]
        if (
            any(p.get("presence") not in {"PRESENT", "ABSENT"} for p in players)
            or not self._policy["minimum_roster_size"]
            <= len(present_players)
            <= self._policy["maximum_roster_size"]
        ):
            raise BatchContractError("BATCH_MEMBER_REQUIRES_REVIEW")
        team = dict(analysis["team"])
        if not team.get("name") or not team.get("category") or not team.get("gender"):
            raise BatchContractError("BATCH_TEAM_INVALID")
        expected = semantic_fields(analysis)
        verification = analysis.get("verification_input", {})
        proposed = verification.get("proposed_fields", {})
        try:
            fields_match = (
                set(verification.get("expected_fields", [])) == set(proposed)
                and set(expected).issubset(proposed)
                and all(
                    re.fullmatch(
                        r"slots\.[1-4]\.([1-9]|[12][0-9]|3[0-5])\.presence", key
                    )
                    for key in set(proposed) - set(expected)
                )
                and all(
                    normalize_observation(proposed[k], field=k)
                    == normalize_observation(v, field=k)
                    for k, v in expected.items()
                )
            )
        except BatchDenied:
            fields_match = False
        if not fields_match:
            raise BatchContractError("BATCH_FIELD_PLAN_MISMATCH")
        operation = uuid5(
            NAMESPACE_URL,
            f"ctt-batch-capture:{context['tenant_id']}:"
            f"{context['edition_id']}:{source['document_id']}",
        )
        run = UUID(str(context["run_id"]))
        member = uuid5(run, source["document_id"])
        session = UUID(
            str(
                source.get("session_id")
                or source.get("legacy_session_id")
                or uuid5(operation, "session")
            )
        )
        page_manifest_hash = digest(source["source_pages"])
        # These are real draft snapshot fields, matching the canonical hash
        # function. No human-review flag or probabilistic score is manufactured.
        raw_snapshot = {
            "ocr_raw": {
                "schema": "ctt.batch.observations.v1",
                "readings": analysis["readings"],
            },
            "extraction": {"team": team, "players": players, "staff": staff},
            "validation": {
                "source_binding_hash": analysis["source_binding_hash"],
                "issues": [],
            },
            "review_edits": {
                "source": "automatic_batch",
                "human_reviewed": False,
                "pending_human_edits": analysis.get("human_edits", []),
            },
            "layout_regions": analysis.get("layout_regions", {}),
            "page_manifest_hash": "sha256:" + page_manifest_hash,
            "overall_confidence": 0.0,
            "needs_review": False,
        }
        content_hash = draft_content_hash(raw_snapshot).removeprefix("sha256:")
        same = source.get("expected_content_hash") == "sha256:" + content_hash
        draft = (
            UUID(str(source["expected_draft_id"]))
            if same and source.get("expected_draft_id")
            else uuid5(session, "batch-draft:" + content_hash)
        )
        version = (
            int(source.get("expected_draft_version") or 0)
            if same
            else int(source.get("expected_draft_version") or 0) + 1
        )
        pages = tuple(
            BatchSourcePage(**{**p, "asset_id": UUID(str(p["asset_id"]))})
            for p in source["source_pages"]
        )
        authority = (
            "hmac-sha256:"
            + hmac.new(
                self._key,
                b"ctt.batch.registry-authority.v1\0" + canonical(context["capability"]),
                hashlib.sha256,
            ).hexdigest()
        )
        scope = BatchReviewScope(
            tenant_id=context["tenant_id"],
            tournament_slug=manifest["edition"]["roster_slug"],
            edition_id=UUID(str(context["edition_id"])),
            session_id=session,
            draft_id=draft,
            draft_version=version,
            draft_content_hash=content_hash,
            team_id=uuid5(operation, "team"),
            manifest_sha256=manifest["manifest_sha256"],
            document_id=source["document_id"],
            source_pages=pages,
            actor_id=UUID(str(context["principal_id"])),
            actor_role=context["capability"]["actor_role"],
            authority_binding=authority,
            staff_and_changes_binding=bind_review_plan(b"inspection-only", self._key),
            field_evidence_binding=bind_review_evidence(b"inspection-only", self._key),
            policy_hash=context["policy_hash"],
            operation_id=operation,
            run_id=run,
            member_id=member,
        )
        team_row = _materialize(
            Team,
            {**team, "id": scope.team_id, "tournament_slug": scope.tournament_slug},
        )
        row_players: list[Player] = []
        row_staff, evidence_members, photo_artifacts = [], [], {}
        page_map = {p.page_index: p for p in pages}
        for kind, people in (("PLAYER", players), ("STAFF", staff)):
            for index, person in enumerate(people, 1):
                presence = person.get("presence")
                if kind == "PLAYER" and presence == "ABSENT":
                    # Keep the source-indexed placeholder in the draft and
                    # verifier inventory; it is not a canonical player row.
                    if (
                        any(
                            person.get(name) not in (None, "")
                            for name in (
                                "first_name",
                                "last_name",
                                "birth_date",
                                "curp",
                            )
                        )
                        or person.get("photo") is not None
                        or person.get("photo_evidence") is not None
                    ):
                        raise BatchContractError("BATCH_ABSENCE_HAS_DATA")
                    continue
                canonical_slot = len(row_players) + 1 if kind == "PLAYER" else index
                if kind == "STAFF" and person.get("role") != (
                    "director_tecnico" if index == 1 else "auxiliar"
                ):
                    raise BatchContractError("BATCH_STAFF_SOURCE_INVALID")
                if presence not in {"PRESENT", "ABSENT"} or (
                    presence == "ABSENT"
                    and (index in self._policy.get("required_staff_slots", []))
                ):
                    raise BatchContractError("BATCH_MEMBER_REQUIRES_REVIEW")
                page = page_map[person["source_page_index"]]
                geometry_page = SimpleNamespace(
                    page_index=page.page_index,
                    asset_id=page.asset_id,
                    image_sha256="sha256:" + page.image_sha256,
                )
                row_id = (
                    uuid5(draft, f"{kind}:{index}") if presence == "PRESENT" else None
                )
                evidence = {
                    "kind": kind,
                    "row_id": str(row_id) if row_id else None,
                    "slot": canonical_slot,
                    "presence": presence,
                    "source_page_index": page.page_index,
                    "fields": {},
                    "absence_geometry": None,
                }
                if presence == "ABSENT":
                    evidence["absence_geometry"] = self._geometry(
                        person.get("absence_box"), geometry_page, snapshot
                    )
                    row_staff.append(
                        dict(
                            slot=index, role=person["role"], presence=presence, row=None
                        )
                    )
                    evidence_members.append(evidence)
                    continue
                full_name = " ".join(
                    [person.get("first_name", ""), person.get("last_name", "")]
                ).strip()
                values = dict(
                    first_name=person["first_name"],
                    last_name=person["last_name"],
                    curp=person.get("curp"),
                    birth_date=_parse_birth_date(person["birth_date"]),
                    id=row_id,
                    team_id=scope.team_id,
                    governance_state="PENDING_FINALITY",
                    governance_draft_id=str(draft),
                    governance_draft_version=version,
                )
                if not full_name or values["birth_date"] is None:
                    raise BatchContractError("BATCH_PERSON_INVALID")
                for name, value in (
                    ("full_name", full_name),
                    ("birth_date", person.get("birth_date")),
                    ("curp", person.get("curp")),
                ):
                    if (
                        name == "curp"
                        and value in (None, "")
                        and kind in self._optional_curp_kinds
                    ):
                        continue
                    if not value:
                        raise BatchContractError("BATCH_PERSON_INVALID")
                    evidence["fields"][name] = self._geometry(
                        person.get("boxes", {}).get(name), geometry_page, snapshot
                    )
                photo = None
                photo_info = person.get("photo")
                if photo_info is not None:
                    photo_evidence = person["photo_evidence"]
                    if (
                        photo_evidence["mode"] == "BATCH_PHYSICAL_SLOT"
                        and kind == "PLAYER"
                    ):
                        photo = snapshot.derive_player_photo(person["slot_ref"])
                    elif photo_evidence["mode"] == "SOURCE_RECTANGLE":
                        photo = derive_rectangular_review_photo(
                            snapshot.page_assets[page.asset_id],
                            photo_evidence["geometry"],
                            max_page_bytes=self._max_page_bytes,
                        )
                    else:
                        raise BatchContractError("BATCH_PHOTO_SOURCE_INVALID")
                    if any(
                        photo_info.get(k) != getattr(photo, k)
                        for k in ("photo_path", "sha256", "ahash")
                    ):
                        raise BatchContractError("BATCH_PHOTO_SOURCE_INVALID")
                    self._photos.stage(photo)
                    values["photo_path"] = photo.photo_path
                    # Photo evidence is a separate source claim in the gate
                    # contract; identity fields remain name/date/CURP only.
                    evidence["photo_evidence"] = (
                        {"mode": "BATCH_PLAYER_SLOT", "slot_ref": person["slot_ref"]}
                        if photo_evidence["mode"] == "BATCH_PHYSICAL_SLOT"
                        else photo_evidence
                    )
                elif kind in self._policy.get("required_photo_kinds", []):
                    raise BatchContractError("BATCH_PHOTO_REQUIRED")
                if kind == "PLAYER":
                    values.update(
                        roster_index=canonical_slot,
                        ocr_confidence=None,
                        needs_review=False,
                        verified_by_human=False,
                    )
                    if photo:
                        values.update(
                            photo_sha256=photo.sha256, photo_ahash=photo.ahash
                        )
                        photo_artifacts[canonical_slot] = {
                            "photo_sha256": photo.sha256,
                            "photo_ahash": photo.ahash,
                        }
                    row_players.append(_materialize(Player, values))
                else:
                    values.update(
                        staff_slot=index,
                        role=person["role"],
                        evidence={"verification_origin": "automatic_batch"},
                    )
                    row_staff.append(
                        dict(
                            slot=index,
                            role=person["role"],
                            presence=presence,
                            row=_materialize(TeamStaff, values),
                        )
                    )
                evidence_members.append(evidence)
        incident_policy = _build_registration_incident_policy(
            {
                "players": [
                    {
                        "name": " ".join([p["first_name"], p["last_name"]]).strip(),
                        "birth_date": _policy_date(p["birth_date"]),
                        "curp": p.get("curp"),
                        "confidence": 0.0,
                        "needs_review": False,
                    }
                    for p in present_players
                ]
            },
            minimum_roster=self._policy["minimum_roster_size"],
            photo_artifacts=photo_artifacts,
            existing_photo_records=context.get("existing_photo_records", []),
        )
        blocking = sum(
            1
            for p in incident_policy["player_results"]
            for issue in p["incidents"]
            if issue["blocks_player_eligibility"] or issue["blocks_team_registration"]
        )
        if blocking:
            raise BatchContractError("BATCH_BLOCKING_INCIDENT")
        # Preserve nonblocking LOW_CONFIDENCE warnings (score unavailable).
        # Source checks and signed qualification resolve review necessity in
        # this mode; no row receives a fabricated probability or human badge.
        validation = {"blockers": [], "incident_policy": incident_policy}
        evidence_bytes = canonical(
            {
                "schema": "ctt.capture.identity-evidence.v1",
                "members": evidence_members,
                "incident_state": {"blocking_incident_count": blocking},
            }
        )
        scope = replace(
            scope,
            field_evidence_binding=bind_review_evidence(evidence_bytes, self._key),
        )
        review = freeze_review_batch(
            scope=scope,
            team=team_row,
            players=row_players,
            staff=row_staff,
            player_source_refs=[p["slot_ref"] for p in present_players],
        )
        scope = replace(
            scope, staff_and_changes_binding=bind_review_plan(review, self._key)
        )
        # The review excludes its own HMAC, so this must remain byte-identical.
        if (
            freeze_review_batch(
                scope=scope,
                team=team_row,
                players=row_players,
                staff=row_staff,
                player_source_refs=[p["slot_ref"] for p in present_players],
            )
            != review
        ):
            raise BatchContractError("BATCH_PLAN_CYCLE")
        scope_bytes = canonical_batch_scope_bytes(scope)
        payload_hash = hashlib.sha256(review).hexdigest()
        attempt = uuid5(operation, batch_scope_hash(scope) + ":" + payload_hash)
        return {
            "member_id": str(member),
            "document_id": source["document_id"],
            "manifest_sha256": context["manifest_sha256"],
            "policy_hash": context["policy_hash"],
            "source_payload_hash": source["source_payload_hash"],
            "payload_hash": payload_hash,
            "scope_hash": batch_scope_hash(scope),
            "attempt_id": str(attempt),
            "session_id": str(session),
            "status": "ready",
            "auto_ready": not bool(analysis.get("human_edits")),
            "verification_origin": (
                "human_corrected" if analysis.get("human_edits") else "automatic_batch"
            ),
            "issues": [],
            "label": team_row["name"],
            "source_pages": source["source_pages"],
            "snapshot": {
                "sample_fields": _sample_fields(analysis),
                "scope_bytes": _b64(scope_bytes),
                "review_bytes": _b64(review),
                "evidence_bytes": _b64(evidence_bytes),
                "validation_bytes": _b64(canonical(validation)),
                "raw_snapshot": raw_snapshot,
                "session_id": str(session),
                "draft_id": str(draft),
                "draft_version": version,
                "expected_draft_id": source.get("expected_draft_id"),
                "expected_draft_version": source.get("expected_draft_version", 0),
                "expected_content_hash": source.get("expected_content_hash"),
                "draft_content_hash": content_hash,
                "verification_input": analysis.get("verification_input", {}),
                "readings": analysis["readings"],
                "source_binding_hash": analysis["source_binding_hash"],
                "human_edits": analysis.get("human_edits", []),
                "human_edits_hash": digest(analysis.get("human_edits", [])),
            },
        }
