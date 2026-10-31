"""Freeze and bind the complete proposed effect; no write authority.

Every modeled column must be explicit; the database schema must be checked
independently. The final effect is built AFTER preauthorization references exist;
the initial review plan excludes those future references. Isolated integration
tests cover both stages with signed receipts. The operational review UI and
protected proposal service remain pending. This module never generates its own
preceding receipts or substitutes a final effect for the initial review binding.
The private bytes contain personal data and
must not enter logs, public receipts or URLs. Database-generated changes and
finality require separately verified effects; this format does not reinterpret
the existing 32-field proof for a single staff row.
"""

import hashlib
import hmac
import json
import re
from typing import Mapping

from sqlalchemy.dialects import postgresql
from uuid import UUID

from .models import Player, Team, TeamStaff
from .staff_capture_executor import CaptureDenied
from .staff_capture_player_slots import resolve_player_slots
from .staff_review_challenge import ReviewScope, canonical_scope_bytes


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _uuid(value):
    return type(value) is str and str(UUID(value)) == value


DATABASE_DERIVED_FIELDS = frozenset({"postcommit_revision", "postcommit_snapshot_hash"})

LATE_AUTHORITY_FIELDS = frozenset(
    {
        "governance_decision_id",
        "roster_draft_binding",
        "preauthorization_receipt_id",
        "finality_receipt_id",
    }
)


def _row(value, model, review_plan=False):
    if not isinstance(value, Mapping):
        raise ValueError("invalid row")
    columns = {column.name: column for column in model.__table__.columns}
    if set(value) != set(columns):
        raise ValueError("incomplete row")
    result = dict(value)
    for name, column in columns.items():
        item = result[name]
        if review_plan and name in (LATE_AUTHORITY_FIELDS | DATABASE_DERIVED_FIELDS):
            if item is not None:
                raise ValueError("authority must follow review")
            continue
        if item is None:
            if not column.nullable:
                raise ValueError("required value missing")
            continue
        expected = column.type.python_type
        if expected is UUID:
            if not _uuid(item):
                raise ValueError("invalid identifier")
        elif expected in (str, int, bool, float):
            if type(item) is not expected:
                raise ValueError("invalid value")
            if (
                expected is str
                and column.type.length
                and len(item) > column.type.length
            ):
                raise ValueError("value too long")
        elif expected is dict:
            if type(item) is not dict:
                raise ValueError("invalid evidence")
        else:
            from datetime import date, datetime, timedelta

            if expected is datetime:
                if (
                    type(item) is not str
                    or datetime.fromisoformat(item).isoformat() != item
                ):
                    raise ValueError("invalid timestamp")
                timestamp = datetime.fromisoformat(item)
                if bool(timestamp.utcoffset() is not None) != bool(
                    column.type.timezone
                ):
                    raise ValueError("timestamp timezone mismatch")
                if column.type.timezone and timestamp.utcoffset() != timedelta(0):
                    raise ValueError("UTC timestamp required")
            elif expected is date:
                if (
                    type(item) is not str
                    or date.fromisoformat(item).isoformat() != item
                ):
                    raise ValueError("invalid date")
            else:
                raise ValueError("unsupported column")
    if model is not Team and (
        result["governance_state"] != "PENDING_FINALITY"
        or result["finality_receipt_id"] is not None
    ):
        raise ValueError("invalid provisional state")
    return result


def _freeze_capture_effect(
    *,
    scope: ReviewScope,
    team: Mapping,
    players: list[Mapping],
    player_source_refs: list[str],
    staff: list[Mapping],
    review_plan: bool,
) -> bytes:
    """Freeze exact rows and physical source mapping from protected review.

    Approval, provenance, policy, current draft and receipts must be verified
    independently. Returned bytes are private and cannot authorize SQL alone.
    """
    try:
        if type(scope) is not ReviewScope or len(players) != len(player_source_refs):
            raise ValueError("scope mismatch")
        team_row = _row(team, Team, review_plan)
        if (
            team_row["id"] != str(scope.team_id)
            or team_row["tournament_slug"] != scope.tournament_slug
        ):
            raise ValueError("team mismatch")
        player_rows = [_row(row, Player, review_plan) for row in players]
        slots = resolve_player_slots(
            [{"slot_ref": ref} for ref in player_source_refs], scope
        )
        all_ids = [team_row["id"]]
        for row, source in zip(player_rows, slots):
            if (
                row["team_id"] != team_row["id"]
                or row["roster_index"] != source.roster_index
            ):
                raise ValueError("player mismatch")
            if (
                row["governance_draft_id"] != str(scope.draft_id)
                or row["governance_draft_version"] != scope.draft_version
            ):
                raise ValueError("draft mismatch")
            all_ids.append(row["id"])
        if type(staff) is not list or len(staff) != 2:
            raise ValueError("staff incomplete")
        staff_rows = []
        for slot, item in enumerate(staff, 1):
            role = "director_tecnico" if slot == 1 else "auxiliar"
            if (
                set(item) != {"slot", "role", "presence", "row"}
                or type(item["slot"]) is not int
                or item["slot"] != slot
                or item["role"] != role
            ):
                raise ValueError("staff mismatch")
            row = None
            if item["presence"] == "PRESENT":
                row = _row(item["row"], TeamStaff, review_plan)
                if (
                    row["team_id"] != team_row["id"]
                    or row["staff_slot"] != slot
                    or row["role"] != role
                ):
                    raise ValueError("staff row mismatch")
                if (
                    row["governance_draft_id"] != str(scope.draft_id)
                    or row["governance_draft_version"] != scope.draft_version
                ):
                    raise ValueError("draft mismatch")
                all_ids.append(row["id"])
            elif item["presence"] != "ABSENT" or item["row"] is not None:
                raise ValueError("staff presence ambiguous")
            staff_rows.append({**dict(item), "row": row})
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("duplicate identifier")
        schema = {
            model.__tablename__: [
                [
                    c.name,
                    str(c.type.compile(dialect=postgresql.dialect())),
                    c.nullable,
                ]
                for c in model.__table__.columns
            ]
            for model in (Team, Player, TeamStaff)
        }
        if review_plan:
            for field in DATABASE_DERIVED_FIELDS:
                del team_row[field]
            for player in player_rows:
                for field in DATABASE_DERIVED_FIELDS:
                    del player[field]
            for row in player_rows + [
                item["row"] for item in staff_rows if item["row"] is not None
            ]:
                for field in LATE_AUTHORITY_FIELDS:
                    del row[field]
        context = json.loads(canonical_scope_bytes(scope))
        # These values are created from or after the effect. Excluding them
        # avoids a circular commitment; the final signed review binds both.
        for field in (
            "staff_and_changes_binding",
            "decision_hash",
            "review_receipt_id",
        ):
            context.pop(field, None)
        if scope.decision_hash is not None:
            # Preserve the existing v1 wire. New pre-receipt scopes bind
            # the preassigned operation within the reviewed plan itself.
            del context["operation_id"]
        return _canonical(
            {
                "schema": (
                    "ctt.capture.review-plan.v1"
                    if review_plan
                    else "ctt.capture.effect.v1"
                ),
                "review_context": context,
                "column_schema_hash": "sha256:"
                + hashlib.sha256(_canonical(schema)).hexdigest(),
                "team": team_row,
                "players": player_rows,
                "staff": staff_rows,
                "player_sources": [
                    {
                        "player_id": row["id"],
                        "roster_index": slot.roster_index,
                        "source_slot": slot.source_slot,
                        "pdf_page": slot.pdf_page,
                        "asset_id": str(slot.asset_id),
                    }
                    for row, slot in zip(player_rows, slots)
                ],
            }
        )
    except Exception:
        raise CaptureDenied("capture denied") from None


def bind_capture_effect(private_bytes: bytes, key: bytes) -> str:
    """Bind a post-preauthorization effect, not the initial review plan.

    The trusted caller must supply validated bytes and an isolated key.
    This does not verify earlier receipts or authorize any write.
    """
    if type(private_bytes) is not bytes or type(key) is not bytes or len(key) < 32:
        raise CaptureDenied("capture denied")
    return (
        "hmac-sha256:"
        + hmac.new(
            key, b"ctt.capture.effect.v1\x00" + private_bytes, hashlib.sha256
        ).hexdigest()
    )


def freeze_capture_effect(**kwargs) -> bytes:
    """Freeze final rows after separately verified preauthorization."""
    return _freeze_capture_effect(**kwargs, review_plan=False)


def freeze_review_plan(**kwargs) -> bytes:
    """Freeze reviewed data without later decision or receipt references."""
    return _freeze_capture_effect(**kwargs, review_plan=True)


def bind_review_plan(private_bytes: bytes, key: bytes) -> str:
    """Bind the earlier review plan with a separate cryptographic domain."""
    if type(private_bytes) is not bytes or type(key) is not bytes or len(key) < 32:
        raise CaptureDenied("capture denied")
    return (
        "hmac-sha256:"
        + hmac.new(
            key,
            b"ctt.capture.review-plan.v1\x00" + private_bytes,
            hashlib.sha256,
        ).hexdigest()
    )


def bind_review_evidence(private_bytes: bytes, key: bytes) -> str:
    """Bind private field/crop evidence before freezing the review plan."""
    if (
        type(private_bytes) is not bytes
        or not private_bytes
        or type(key) is not bytes
        or len(key) < 32
    ):
        raise CaptureDenied("capture denied")
    return (
        "hmac-sha256:"
        + hmac.new(
            key, b"ctt.capture.review-evidence.v1\x00" + private_bytes, hashlib.sha256
        ).hexdigest()
    )


def check_effect_follows_review(
    *,
    review_bytes: bytes,
    effect_bytes: bytes,
    scope: ReviewScope,
    review_key: bytes,
    authority_metadata: Mapping,
) -> None:
    """Require unchanged reviewed data plus exact verified authority metadata.

    The gate must authenticate authority_metadata from signed receipts first.
    This structural comparison cannot establish authenticity from a mapping.
    The final authorization must bind both commitments and all scope IDs.
    """
    try:
        if not hmac.compare_digest(
            bind_review_plan(review_bytes, review_key),
            scope.staff_and_changes_binding,
        ):
            raise ValueError("review mismatch")
        plan = json.loads(review_bytes)
        final = json.loads(effect_bytes)
        if _canonical(plan) != review_bytes or _canonical(final) != effect_bytes:
            raise ValueError("noncanonical effect")
        if (
            plan["schema"] != "ctt.capture.review-plan.v1"
            or final["schema"] != "ctt.capture.effect.v1"
        ):
            raise ValueError("invalid stage")
        context = json.loads(canonical_scope_bytes(scope))
        for field in (
            "staff_and_changes_binding",
            "decision_hash",
            "review_receipt_id",
        ):
            context.pop(field, None)
        if scope.decision_hash is not None:
            del context["operation_id"]
        if plan["review_context"] != context:
            raise ValueError("review scope changed")
        rebuilt = freeze_capture_effect(
            scope=scope,
            team=final["team"],
            players=final["players"],
            staff=final["staff"],
            player_source_refs=[
                f"page:{item['pdf_page']}:jugador_{item['source_slot']}"
                for item in final["player_sources"]
            ],
        )
        if rebuilt != effect_bytes:
            raise ValueError("invalid final effect")
        rows = final["players"] + [
            item["row"] for item in final["staff"] if item["row"] is not None
        ]
        if set(authority_metadata) != {row["id"] for row in rows}:
            raise ValueError("authority mismatch")
        for row in rows:
            metadata = authority_metadata[row["id"]]
            if (
                set(metadata) != LATE_AUTHORITY_FIELDS
                or metadata["finality_receipt_id"] is not None
            ):
                raise ValueError("authority mismatch")
            for field, prefix in (
                ("governance_decision_id", "sha256:"),
                ("roster_draft_binding", "hmac-sha256:"),
                ("preauthorization_receipt_id", "sha256:"),
            ):
                value = metadata[field]
                if (
                    type(value) is not str
                    or re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", value) is None
                ):
                    raise ValueError("authority missing or malformed")
            if any(row[field] != metadata[field] for field in LATE_AUTHORITY_FIELDS):
                raise ValueError("authority mismatch")
            for field in LATE_AUTHORITY_FIELDS:
                del row[field]
        for entity, entity_rows, revision in (
            ("TEAM", [final["team"]], 1),
            ("PLAYER", final["players"], 0),
        ):
            for row in entity_rows:
                if (
                    row["postcommit_revision"] != revision
                    or re.fullmatch(
                        r"sha256:[0-9a-f]{64}", row["postcommit_snapshot_hash"]
                    )
                    is None
                ):
                    raise ValueError("invalid database-derived metadata")
                for field in DATABASE_DERIVED_FIELDS:
                    del row[field]
        final["schema"] = plan["schema"]
        if _canonical(final) != review_bytes:
            raise ValueError("reviewed values changed")
    except Exception:
        raise CaptureDenied("capture denied") from None
