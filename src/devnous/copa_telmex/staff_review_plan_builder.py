"""Prepare private, source-bound proposals; never issue capture authority.

Incomplete observations have a separate, non-executable wire. Geometry and
photos are recomputed from the retained source; browser rectangles are proposals.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from io import BytesIO
from uuid import UUID, uuid4, uuid5

from PIL import Image

from .models import Player, Team, TeamStaff
from .registration_review_policy import (
    _birth_date_has_two_digit_year,
    _build_registration_incident_policy,
    _parse_birth_date,
    _split_player_name,
)
from .staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    bind_review_evidence,
    bind_review_plan,
    freeze_review_plan,
)
from .staff_capture_executor import CaptureDenied
from .staff_capture_geometry import verify_staff_geometry
from .staff_capture_player_slots import resolve_player_slots
from .staff_photo_store import (
    derive_batch_review_photo,
    derive_rectangular_review_photo,
)
from .staff_review_challenge import ReviewScope, SourcePage, canonical_scope_bytes


_FIELDS = ("full_name", "birth_date", "curp")
_TEAM = (
    "name",
    "state",
    "municipality",
    "gender",
    "category",
    "league",
    "representative_name",
    "contact_phone",
    "contact_email",
)
_MEMBER = (
    "first_name",
    "last_name",
    "birth_date",
    "curp",
    "source_page_index",
    "boxes",
    "photo_box",
    "presence",
    "presence_confirmed",
    "absence_box",
)


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _policy_date(value):
    # The existing form policy expects day/month/year. Native date inputs use
    # ISO; adapt that transport format before applying the unchanged policy.
    try:
        parsed = date.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y") if parsed.isoformat() == value else value
    except (ValueError, TypeError):
        return value


def _deny():
    raise CaptureDenied("private review proposal denied") from None


@dataclass(frozen=True, repr=False)
class PreparedProposal:
    scope: ReviewScope
    review_bytes: bytes
    evidence_bytes: bytes
    preview_bytes: bytes
    validation_bytes: bytes
    capture_nonce: UUID


def _materialize(model, values):
    entity = model(**values)
    result = {}
    for column in model.__table__.columns:
        value = getattr(entity, column.name)
        if column.name in LATE_AUTHORITY_FIELDS | DATABASE_DERIVED_FIELDS:
            value = None
        elif value is None and column.default is not None:
            default = column.default
            value = default.arg(None) if default.is_callable else default.arg
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, UUID):
            value = str(value)
        result[column.name] = value
    return result


class ReviewPlanBuilder:
    """Derive a reviewable plan from protected context and verified sources."""

    def __init__(
        self, *, review_key, policy_artifact, expected_policy_hash, photo_store, gate
    ):
        minimum = policy_artifact.get("minimum_roster_size")
        maximum = policy_artifact.get("maximum_roster_size")
        if (
            type(review_key) is not bytes
            or len(review_key) < 32
            or type(minimum) is not int
            or type(maximum) is not int
            or not 16 <= minimum <= maximum <= 35
            or policy_artifact.get("policy_hash") != expected_policy_hash
            or _sha(
                _canonical(
                    {k: v for k, v in policy_artifact.items() if k != "policy_hash"}
                )
            )
            != expected_policy_hash
            or getattr(gate, "_policy_hash", None) != expected_policy_hash
        ):
            raise ValueError("pinned operational review policy required")
        self._key = review_key
        self._policy = deepcopy(policy_artifact)
        self._policy_hash = expected_policy_hash
        self._photos = photo_store
        self._max_page_bytes = gate._max_page_bytes

    def initial_scope(self, context, proposal_id):
        """Build inspection scope solely from protected registry/source state."""
        try:
            if type(proposal_id) is not UUID:
                _deny()
            binding = {
                key: context[key]
                for key in (
                    "backend_pin",
                    "account_id",
                    "principal_id",
                    "actor_role",
                    "account_version",
                    "principal_version",
                    "faculty_version",
                    "credential_version",
                    "credential_id_hex",
                    "faculty_valid_until_seconds",
                )
            }
            authority = (
                "hmac-sha256:"
                + hmac.new(
                    self._key,
                    b"ctt.review.registry-authority.v1\0" + _canonical(binding),
                    hashlib.sha256,
                ).hexdigest()
            )
            draft, session = context["draft"], context["session"]
            return ReviewScope(
                tenant_id=context["tenant_id"],
                tournament_slug=context["roster_slug"],
                edition_id=UUID(context["edition_id"]),
                session_id=UUID(session["id"]),
                draft_id=UUID(draft["id"]),
                draft_version=draft["draft_version"],
                draft_content_hash=draft["content_hash"],
                team_id=uuid5(proposal_id, "team"),
                manifest_sha256=context["manifest_sha256"],
                document_id=context["document_id"],
                pdf_sha256=context["pdf_sha256"],
                source_pages=tuple(
                    SourcePage(
                        page_index=p["page_index"],
                        asset_id=UUID(p["asset_id"]),
                        image_sha256=p["image_sha256"],
                        pdf_page=p["pdf_page"],
                    )
                    for p in context["source_pages"]
                ),
                actor_id=UUID(context["principal_id"]),
                actor_role=context["actor_role"],
                authority_binding=authority,
                staff_and_changes_binding=bind_review_plan(
                    b"inspection-only", self._key
                ),
                field_evidence_binding=bind_review_evidence(
                    b"inspection-only", self._key
                ),
                decision_hash=None,
                review_receipt_id=None,
                policy_hash=self._policy_hash,
                operation_id=_sha(b"ctt.review.operation.v1\0" + proposal_id.bytes),
            )
        except Exception:
            _deny()

    def _geometry(self, box, page, snapshot, *, minimum_pixels=None):
        if (
            type(box) is not dict
            or set(box) != {"page_index", "x", "y", "width", "height"}
            or type(box["page_index"]) is not int
            or box["page_index"] != page.page_index
        ):
            raise ValueError("source rectangle missing")
        coords = {key: box[key] for key in ("x", "y", "width", "height")}
        if any(type(v) is not int for v in coords.values()):
            raise ValueError("integer source rectangle required")
        raw = snapshot.page_assets[page.asset_id]
        digest = _sha(raw)
        x, y, width, height = (coords[k] for k in ("x", "y", "width", "height"))
        if min(x, y) < 0 or min(width, height) < 1:
            raise ValueError("source rectangle bounds")
        with Image.open(BytesIO(raw)) as image:
            if (
                image.width > 8000
                or image.height > 8000
                or image.width * image.height > 25_000_000
                or x + width > image.width
                or y + height > image.height
            ):
                raise ValueError("source rectangle bounds")
            crop = image.convert("RGB").crop((x, y, x + width, y + height))
            crop_hash = _sha(
                _canonical({"mode": "RGB", "size": list(crop.size)}) + crop.tobytes()
            )
        geometry = {
            "original_image_hash": digest,
            "normalized_image_hash": digest,
            "coordinate_frame_image_hash": digest,
            "coordinate_frame": "ORIGINAL",
            "normalization_version": "samchat-identity-normalization-v1",
            "normalization_transform_hash": _sha(
                _canonical(
                    {
                        "operation": "identity",
                        "source_hash": digest,
                        "version": "samchat-identity-normalization-v1",
                    }
                )
            ),
            "field_coordinates": coords,
            "crop_derivation_version": "samchat-rgb-field-crop-v1",
            "crop_hash": crop_hash,
            "effective_pixels": width * height,
        }
        if not verify_staff_geometry(
            {"source_page_hash": page.image_sha256},
            geometry,
            raw,
            max_page_bytes=self._max_page_bytes,
        ) or width * height < (
            self._policy["minimum_effective_pixels"]
            if minimum_pixels is None
            else minimum_pixels
        ):
            raise ValueError("insufficient source geometry")
        return geometry

    @staticmethod
    def _initial_member(raw, slot, page_index):
        first, last = _split_player_name(raw.get("name") or raw.get("full_name") or "")
        birth = raw.get("birth_date") or ""
        parsed = (
            None
            if _birth_date_has_two_digit_year(_policy_date(birth))
            else _parse_birth_date(birth)
        )
        return {
            "slot": slot,
            "first_name": (raw.get("first_name") or first),
            "last_name": (raw.get("last_name") or last),
            "birth_date": parsed.isoformat() if parsed else birth,
            "curp": (raw.get("curp") or "").strip().upper(),
            "source_page_index": page_index,
            "boxes": {},
        }

    def build(self, context, changes, snapshot, proposal_id):
        """Prepare exact bytes, or a blocked observation without executable rows."""
        try:
            return self._build(context, changes, snapshot, proposal_id)
        except CaptureDenied:
            raise
        except Exception:
            _deny()

    def _build(self, context, changes, snapshot, proposal_id):
        scope = self.initial_scope(context, proposal_id)
        snapshot.verify_unchanged()
        if snapshot.scope_bytes != canonical_scope_bytes(scope):
            _deny()
        manifest = json.loads(snapshot.manifest_json)
        documents = manifest["documents"]
        document = next(d for d in documents if d["document_id"] == scope.document_id)
        source = document["extraction"]
        candidate = (
            context["draft"].get("review_edits") or context["draft"]["extraction"]
        )
        previous = context.get("previous_preview")
        blockers = []

        def block(code, message):
            item = {"code": code, "message": message}
            if item not in blockers:
                blockers.append(item)

        if type(changes) is not dict or set(changes) - {"team", "players", "staff"}:
            _deny()
        physical = source.get("players") or []
        refs = [p.get("slot_ref") for p in physical]
        slots = None
        try:
            slots = resolve_player_slots(physical, scope)
        except CaptureDenied:
            block(
                "PLAYER_SOURCE_MISSING", "Falta la referencia física de algún jugador."
            )
        if len(candidate.get("players") or []) != len(physical):
            block(
                "PLAYER_SOURCE_MISMATCH",
                "El draft no coincide con los espacios del PDF.",
            )
        players = []
        for index, raw in enumerate(candidate.get("players") or [], 1):
            page_index = 0
            if slots and index <= len(slots):
                page_index = next(
                    p.page_index
                    for p in scope.source_pages
                    if p.asset_id == slots[index - 1].asset_id
                )
            players.append(self._initial_member(raw, index, page_index))
        staff = []
        for slot, role in enumerate(("director_tecnico", "auxiliar"), 1):
            staff_raw: dict = next(
                (s for s in candidate.get("staff", []) if s.get("slot") == slot), {}
            )
            member = self._initial_member(
                staff_raw, slot, staff_raw.get("source_page_index", 0)
            )
            member.update(role=role, presence="UNCERTAIN", presence_confirmed=False)
            staff.append(member)
        team = {key: (candidate.get("team") or {}).get(key) or "" for key in _TEAM}
        if previous:
            team = deepcopy(previous["team"])
            players, staff = deepcopy(previous["players"]), deepcopy(previous["staff"])
        if "team" in changes:
            if type(changes["team"]) is not dict or set(changes["team"]) - set(_TEAM):
                _deny()
            team.update(changes["team"])
        for kind, members in (("players", players), ("staff", staff)):
            if kind not in changes:
                continue
            updates = changes[kind]
            if (
                type(updates) is not list
                or len(updates) != len(members)
                or any(
                    type(u) is not dict or type(u.get("slot")) is not int
                    for u in updates
                )
                or [u.get("slot") for u in updates] != [m["slot"] for m in members]
            ):
                _deny()
            for member, update in zip(members, updates):
                if type(update) is not dict or set(update) - set(_MEMBER) - {"slot"}:
                    _deny()
                member.update(deepcopy(update))
        if not team.get("name", "").strip():
            block("TEAM_NAME_MISSING", "Falta el nombre del equipo.")
        if (
            not self._policy["minimum_roster_size"]
            <= len(players)
            <= self._policy["maximum_roster_size"]
        ):
            block(
                "ROSTER_SIZE",
                "La plantilla no cumple el mínimo o la capacidad autorizada.",
            )
        if len(players) != len(physical):
            block(
                "PLAYER_SOURCE_MISMATCH",
                "La propuesta no coincide con los espacios del PDF.",
            )
        pages = {p.page_index: p for p in scope.source_pages}
        evidence_members, prepared_photos, photo_artifacts = [], {}, {}
        for kind, members in (("PLAYER", players), ("STAFF", staff)):
            for index, member in enumerate(members, 1):
                member.pop("photo_preview", None)
                presence = "PRESENT" if kind == "PLAYER" else member.get("presence")
                page_index = member.get("source_page_index")
                page = pages.get(page_index) if type(page_index) is int else None
                if kind == "PLAYER" and slots and index <= len(slots):
                    if not page or page.asset_id != slots[index - 1].asset_id:
                        block(
                            "PLAYER_SOURCE_MISMATCH",
                            "La página debe coincidir con el espacio físico.",
                        )
                row_id = (
                    str(uuid5(proposal_id, f"{kind}:{index}"))
                    if presence == "PRESENT"
                    else None
                )
                evidence = dict(
                    kind=kind,
                    row_id=row_id,
                    slot=index,
                    presence=presence,
                    source_page_index=member.get("source_page_index"),
                    fields={},
                    absence_geometry=None,
                )
                if kind == "STAFF" and member.get("presence_confirmed") is not True:
                    block(
                        "STAFF_CONFIRMATION_REQUIRED",
                        "Confirma presencia o ausencia del cuerpo técnico.",
                    )
                try:
                    if page is None:
                        raise ValueError("page missing")
                    if presence == "ABSENT" and kind == "STAFF":
                        evidence["absence_geometry"] = self._geometry(
                            member.get("absence_box"), page, snapshot
                        )
                    elif presence == "PRESENT":
                        for field in _FIELDS:
                            value = (
                                " ".join(
                                    [
                                        member.get("first_name", ""),
                                        member.get("last_name", ""),
                                    ]
                                ).strip()
                                if field == "full_name"
                                else member.get(field, "")
                            )
                            if not value:
                                raise ValueError("field missing")
                            if field == "birth_date" and (
                                _birth_date_has_two_digit_year(_policy_date(value))
                                or not _parse_birth_date(value)
                            ):
                                raise ValueError("date missing")
                            if field == "curp" and (
                                len(value) != 18 or not value.isalnum()
                            ):
                                raise ValueError("curp missing")
                            evidence["fields"][field] = self._geometry(
                                member.get("boxes", {}).get(field), page, snapshot
                            )
                    else:
                        raise ValueError("presence unknown")
                except (ValueError, KeyError, TypeError):
                    block(
                        "FIELD_EVIDENCE_REQUIRED",
                        "Completa los campos y sus recortes en la página original.",
                    )
                if (
                    kind == "STAFF"
                    and index in self._policy.get("required_staff_slots", [])
                    and presence != "PRESENT"
                ):
                    block(
                        "STAFF_REQUIRED",
                        "La política requiere este integrante del cuerpo técnico.",
                    )
                if presence == "PRESENT":
                    try:
                        if page is None:
                            raise ValueError("photo page missing")
                        if member.get("photo_box"):
                            geometry = self._geometry(
                                member["photo_box"], page, snapshot, minimum_pixels=1
                            )
                            photo = derive_rectangular_review_photo(
                                snapshot.page_assets[page.asset_id],
                                geometry,
                                max_page_bytes=self._max_page_bytes,
                            )
                            photo_evidence = {
                                "mode": "SOURCE_RECTANGLE",
                                "geometry": geometry,
                            }
                        elif kind == "PLAYER" and index <= len(refs):
                            photo = derive_batch_review_photo(snapshot, refs[index - 1])
                            photo_evidence = {
                                "mode": "BATCH_PLAYER_SLOT",
                                "slot_ref": refs[index - 1],
                            }
                        else:
                            photo = None
                        if photo:
                            self._photos.stage(photo)
                            prepared_photos[(kind, index)] = photo
                            evidence["photo_evidence"] = photo_evidence
                            member["photo_preview"] = (
                                "data:image/jpeg;base64,"
                                + base64.b64encode(photo.data).decode()
                            )
                            if kind == "PLAYER":
                                photo_artifacts[index] = dict(
                                    photo_path=photo.photo_path,
                                    photo_sha256=photo.sha256,
                                    photo_ahash=photo.ahash,
                                )
                        elif kind in self._policy.get("required_photo_kinds", []):
                            block(
                                "PHOTO_REQUIRED",
                                "Falta una fotografía derivada del PDF.",
                            )
                    except (CaptureDenied, ValueError, KeyError, TypeError):
                        if member.get("photo_box") or kind in self._policy.get(
                            "required_photo_kinds", []
                        ):
                            block(
                                "PHOTO_SOURCE_INVALID",
                                "Revisa el recorte de fotografía en la fuente.",
                            )
                evidence_members.append(evidence)
        policy_players = []
        raw_players = candidate.get("players") or []
        for index, p in enumerate(players):
            raw = raw_players[index] if index < len(raw_players) else {}
            policy_players.append(
                dict(
                    name=" ".join([p["first_name"], p["last_name"]]).strip(),
                    birth_date=_policy_date(p["birth_date"]),
                    curp=p["curp"],
                    confidence=raw.get("confidence", 0.0),
                    needs_review=raw.get("needs_review", False),
                )
            )
        incident_policy = _build_registration_incident_policy(
            {"players": policy_players},
            minimum_roster=self._policy["minimum_roster_size"],
            photo_artifacts=photo_artifacts,
            existing_photo_records=context["existing_photo_records"],
        )
        count = sum(
            1
            for result in incident_policy["player_results"]
            for incident in result["incidents"]
            if incident["blocks_player_eligibility"]
            or incident["blocks_team_registration"]
        )
        if count:
            block(
                "BLOCKING_INCIDENT", "Resuelve los incidentes que bloquean el registro."
            )
        evidence = {
            "schema": "ctt.capture.identity-evidence.v1",
            "members": evidence_members,
            "incident_state": {"blocking_incident_count": count},
        }
        evidence_bytes = _canonical(evidence)
        scope = replace(
            scope,
            field_evidence_binding=bind_review_evidence(evidence_bytes, self._key),
        )
        validation = {"blockers": blockers, "incident_policy": incident_policy}
        if not blockers:
            team_row = _materialize(
                Team,
                dict(
                    team,
                    id=scope.team_id,
                    tournament_slug=scope.tournament_slug,
                    telegram_chat_id=context["session"].get("telegram_chat_id"),
                    telegram_user_id=context["session"].get("telegram_user_id"),
                ),
            )
            rows, staff_rows = [], []
            for kind, members in (("PLAYER", players), ("STAFF", staff)):
                for index, member in enumerate(members, 1):
                    presence = "PRESENT" if kind == "PLAYER" else member["presence"]
                    row = None
                    if presence == "PRESENT":
                        values = {
                            key: member[key]
                            for key in ("first_name", "last_name", "curp")
                        }
                        values.update(
                            birth_date=_parse_birth_date(member["birth_date"]),
                            id=uuid5(proposal_id, f"{kind}:{index}"),
                            team_id=scope.team_id,
                            governance_state="PENDING_FINALITY",
                            governance_draft_id=str(scope.draft_id),
                            governance_draft_version=scope.draft_version,
                        )
                        photo = prepared_photos.get((kind, index))
                        if photo:
                            values["photo_path"] = photo.photo_path
                        if kind == "PLAYER":
                            values.update(
                                roster_index=index,
                                ocr_confidence=float(
                                    raw_players[index - 1].get("confidence") or 0.0
                                ),
                                needs_review=bool(
                                    incident_policy["player_results"][index - 1][
                                        "incidents"
                                    ]
                                ),
                                verified_by_human=True,
                            )
                            if photo:
                                values.update(
                                    photo_sha256=photo.sha256, photo_ahash=photo.ahash
                                )
                        else:
                            values.update(
                                staff_slot=index, role=member["role"], evidence={}
                            )
                        row = _materialize(
                            Player if kind == "PLAYER" else TeamStaff, values
                        )
                    if kind == "PLAYER":
                        rows.append(row)
                    else:
                        staff_rows.append(
                            dict(
                                slot=index,
                                role=member["role"],
                                presence=presence,
                                row=row,
                            )
                        )
            try:
                review_bytes = freeze_review_plan(
                    scope=scope,
                    team=team_row,
                    players=rows,
                    staff=staff_rows,
                    player_source_refs=refs,
                )
            except CaptureDenied:
                block(
                    "MATERIAL_VALUES_INVALID",
                    "Revisa los valores y longitudes de la propuesta.",
                )
        if blockers:
            review_context = json.loads(canonical_scope_bytes(scope))
            review_context.pop("staff_and_changes_binding")
            review_bytes = _canonical(
                {
                    "schema": "ctt.review.incomplete-proposal.v1",
                    "review_context": review_context,
                    "team": {
                        "id": str(scope.team_id),
                        "tournament_slug": scope.tournament_slug,
                    },
                    "observations": {"team": team, "players": players, "staff": staff},
                }
            )
        scope = replace(
            scope, staff_and_changes_binding=bind_review_plan(review_bytes, self._key)
        )
        snapshot.verify_unchanged()
        preview = dict(
            team=team,
            players=players,
            staff=staff,
            pages=[
                {key: p[key] for key in ("page_index", "pdf_page", "width", "height")}
                for p in context["source_pages"]
            ],
            blockers=blockers,
            ready_to_approve=not blockers,
        )
        return PreparedProposal(
            scope,
            review_bytes,
            evidence_bytes,
            _canonical(preview),
            _canonical(validation),
            uuid4(),
        )
