"""Finalize an exact pending capture inside the caller's trusted transaction.

The immutable review inputs must be resolved through private SQL, independently
of the application principal. This module never enrolls a reviewer, admits real
inputs, starts a service, opens another capture connection or commits a part of
the effect. Receipt emission alone is not a database commit.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import time
from types import MappingProxyType
from typing import Any, NoReturn
from uuid import UUID, uuid4

from .models import Player, Team, TeamStaff
from .staff_capture_executor import CaptureDenied
from .staff_capture_finality_authorization import (
    verify_capture_finality_authorization,
    verify_pre_authorization,
)
from .staff_capture_finality_effect import (
    FinalityContext,
    freeze_finality_effect,
    verify_finality_bundle,
)
from .staff_capture_persistence import PendingCaptureWriter, _observed, _value


def _deny() -> NoReturn:
    raise CaptureDenied("capture denied") from None


@dataclass(frozen=True)
class FinalizedCaptureTokens:
    """Both verified authorizations; SQL still enforces their joint effect."""

    pending_authorization_hash: str
    finality_authorization_hash: str


def _blocking_incident_count(validation: object, roster_indices: list[int]) -> int:
    """Recount the existing incident flags from the locked immutable draft.

    A supplied count or absent incident inventory is not evidence of zero.
    This consumes the registration policy's existing per-player flags; it does
    not determine sports eligibility or replace the pinned gate policy.
    """
    if type(validation) is not dict or type(roster_indices) is not list:
        _deny()
    policy = validation.get("incident_policy")
    if type(policy) is not dict:
        _deny()
    results = policy.get("player_results")
    if type(results) is not list or len(results) != len(roster_indices):
        _deny()
    if any(
        type(index) is not int or not 1 <= index <= 35 for index in roster_indices
    ) or len(set(roster_indices)) != len(roster_indices):
        _deny()
    seen = set()
    count = 0
    for result in results:
        if type(result) is not dict:
            _deny()
        index = result.get("player_index")
        status = result.get("eligibility_status")
        incidents = result.get("incidents")
        if (
            type(index) is not int
            or index not in roster_indices
            or index in seen
            or type(status) is not str
            or status
            not in {
                "CLEARED",
                "INCIDENT_PENDING",
                "PENDING_NONBLOCKING",
                "PENDING_BLOCKING",
                "REJECTED",
            }
            or type(incidents) is not list
        ):
            _deny()
        seen.add(index)
        blocks = status in {"PENDING_BLOCKING", "REJECTED"}
        if status == "INCIDENT_PENDING":
            # The existing review policy uses one pending status and carries
            # the distinction in its explicit flags, not in the status name.
            if type(result.get("counts_toward_minimum")) is not bool or not incidents:
                _deny()
            blocks = not result["counts_toward_minimum"]
        for incident in incidents:
            if type(incident) is not dict:
                _deny()
            player = incident.get("blocks_player_eligibility")
            team = incident.get("blocks_team_registration")
            if type(player) is not bool or type(team) is not bool:
                _deny()
            if player or team:
                count += 1
                blocks = False  # The explicit incident already counted this row.
        if blocks:
            count += 1
    if seen != set(roster_indices):
        _deny()
    blockers = validation.get("blockers", [])
    if type(blockers) is not list:
        _deny()
    count += len(blockers)
    return count


def _private_json(raw: bytes) -> dict:
    """Accept bounded canonical private input without duplicate JSON keys."""

    def pairs(items):
        value = {}
        for name, child in items:
            if name in value:
                _deny()
            value[name] = child
        return value

    def constant(_):
        _deny()

    try:
        if type(raw) is not bytes or not 2 <= len(raw) <= 4 * 1024 * 1024:
            _deny()
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        if type(value) is not dict:
            _deny()
        if (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            != raw
        ):
            _deny()
        return value
    except Exception:
        _deny()


class FinalizingCaptureWriter:
    """Trusted service composition, never constructed from browser input."""

    def __init__(
        self,
        *,
        public_keys,
        receipt_public_keys,
        assertion_public_keys,
        review_key,
        effect_key,
        finality_service,
        database_identity,
        photo_store=None,
    ):
        if (
            not callable(getattr(finality_service, "finalize_rows", None))
            or not callable(
                getattr(finality_service, "authorize_finality_effect", None)
            )
            or type(database_identity) is not str
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", database_identity) is None
        ):
            _deny()
        for registry in (public_keys, receipt_public_keys, assertion_public_keys):
            if not registry or any(
                type(kid) is not str
                or not kid
                or type(key) is not bytes
                or len(key) != 32
                for kid, key in registry.items()
            ):
                _deny()
        self._keys = MappingProxyType(dict(public_keys))
        self._receipt_keys = MappingProxyType(dict(receipt_public_keys))
        self._assertion_keys = MappingProxyType(dict(assertion_public_keys))
        self._review_key, self._effect_key = review_key, effect_key
        self._service, self._database_identity = finality_service, database_identity
        self._photos = photo_store
        self._pending = PendingCaptureWriter(
            public_keys=self._keys,
            review_key=review_key,
            effect_key=effect_key,
            photo_store=photo_store,
        )

    async def _read_rows(self, conn, pending):
        result: dict[str, Any] = {"team": None, "players": [], "staff": []}
        for model, row in [(Team, pending["team"])]:
            observed = await conn.fetchrow(
                "SELECT * FROM public.copa_telmex_teams WHERE id=$1 FOR UPDATE",
                UUID(row["id"]),
            )
            if observed is None:
                _deny()
            result["team"] = _observed(observed, model)
        for row in pending["players"]:
            observed = await conn.fetchrow(
                "SELECT * FROM public.copa_telmex_players WHERE id=$1 FOR UPDATE",
                UUID(row["id"]),
            )
            if observed is None:
                _deny()
            result["players"].append(_observed(observed, Player))
        for slot in pending["staff"]:
            item = deepcopy(slot)
            if slot["row"] is not None:
                observed = await conn.fetchrow(
                    "SELECT * FROM public.copa_telmex_team_staff "
                    "WHERE id=$1 FOR UPDATE",
                    UUID(slot["row"]["id"]),
                )
                if observed is None:
                    _deny()
                item["row"] = _observed(observed, TeamStaff)
            result["staff"].append(item)
        return result

    async def persist_and_finalize(
        self, conn, *, source, signed_review, **authorization
    ):
        """Insert, observe, attest, activate and read back on this one connection."""
        try:
            if not conn.is_in_transaction() or type(signed_review) is not bytes:
                _deny()
            pending_token = await self._pending.persist(conn, **authorization)
            pending = _private_json(authorization["effect_bytes"])
            observed = await self._read_rows(conn, pending)
            if observed != {key: pending[key] for key in ("team", "players", "staff")}:
                _deny()
            attempt_id = authorization["attempt_id"]
            loaded = await conn.fetchrow(
                "SELECT * FROM ctt_staff_private.load_capture_finality_context($1)",
                attempt_id,
            )
            inputs = await conn.fetchrow(
                "SELECT * FROM ctt_staff_private.load_capture_finality_inputs($1)",
                attempt_id,
            )
            if (
                loaded is None
                or inputs is None
                or loaded["database_identity"] != self._database_identity
            ):
                _deny()
            validation = loaded["validation"]
            if type(validation) is str:
                validation = json.loads(validation)
            incidents = _blocking_incident_count(
                validation, [row["roster_index"] for row in pending["players"]]
            )
            if incidents != 0:
                _deny()
            preauthorization = _private_json(inputs["preauthorization_bytes"])
            evidence = _private_json(inputs["evidence_bytes"])
            from .staff_capture_effect import bind_review_evidence

            scope = authorization["scope"]
            if bind_review_evidence(
                inputs["evidence_bytes"], self._review_key
            ) != scope.field_evidence_binding or evidence.get("incident_state") != {
                "blocking_incident_count": incidents
            }:
                _deny()
            context = FinalityContext(
                database_identity=loaded["database_identity"],
                database_oid=int(loaded["database_oid"]),
                pg_top_xid=int(loaded["pg_top_xid"]),
                review_attempt_id=attempt_id,
                issued_at=loaded["issued_at_seconds"],
                expires_at=loaded["expires_at_seconds"],
                nonce=uuid4(),
                snapshot=source,
                blocking_incident_count=incidents,
                cas_succeeded=True,
            )
            context.verify(scope, attempt_id)
            verify_pre_authorization(
                preauthorization=preauthorization,
                scope=scope,
                attempt_id=attempt_id,
                pending_effect_bytes=authorization["effect_bytes"],
                receipt_public_keys=self._receipt_keys,
                signed_review=signed_review,
                assertion_public_keys=self._assertion_keys,
                now_seconds=int(time.time()),
            )
            bundle = await asyncio.to_thread(
                self._service.finalize_rows,
                protected_context=context,
                scope=scope,
                review_attempt_id=attempt_id,
                review_bytes=authorization["review_bytes"],
                pending_effect_bytes=authorization["effect_bytes"],
                signed_review=signed_review,
                preauthorization=preauthorization,
                observed_pending_rows=observed,
            )
            records = verify_finality_bundle(
                bundle=bundle,
                scope=scope,
                context=context,
                pending_effect_bytes=authorization["effect_bytes"],
                preauthorization=preauthorization,
                effect_key=self._effect_key,
                receipt_public_keys=self._receipt_keys,
                now_seconds=int(time.time()),
            )
            by_id = {item["row_id"]: item for item in records}
            active = deepcopy(observed)
            updated = datetime.fromtimestamp(context.issued_at, timezone.utc)
            for row in active["players"]:
                row.update(
                    governance_state="ACTIVE",
                    finality_receipt_id=by_id[row["id"]]["receipt"]["receipt_id"],
                    updated_at=updated.replace(tzinfo=None).isoformat(),
                )
                projected = await conn.fetchval(
                    "SELECT ctt_staff_private.project_capture_finality_player("
                    "$1::jsonb)",
                    json.dumps(row, ensure_ascii=False, allow_nan=False),
                )
                projection = (
                    json.loads(projected) if type(projected) is str else projected
                )
                if type(projection) is not dict or set(projection) != set(row):
                    _deny()
                # JSONB normalizes numeric/timestamp representation. Preserve
                # the reviewed material bytes and consume only SQL-derived
                # metadata; complete typed SQL guards and readback still apply.
                row["postcommit_revision"] = projection["postcommit_revision"]
                row["postcommit_snapshot_hash"] = projection["postcommit_snapshot_hash"]
            for slot in active["staff"]:
                if slot["row"] is not None:
                    row = slot["row"]
                    row.update(
                        governance_state="ACTIVE",
                        finality_receipt_id=by_id[row["id"]]["receipt"]["receipt_id"],
                        updated_at=updated.isoformat(),
                    )
            finality_effect = freeze_finality_effect(
                scope=scope,
                protected_context=context,
                pending_effect_bytes=authorization["effect_bytes"],
                observed_pending_rows=observed,
                verified_finalities=bundle,
                active_projection_db_derived=active,
                effect_key=self._effect_key,
                preauthorization=preauthorization,
                receipt_public_keys=self._receipt_keys,
            )
            envelope = await asyncio.to_thread(
                self._service.authorize_finality_effect,
                protected_context=context,
                scope=scope,
                review_attempt_id=attempt_id,
                nonce=context.nonce,
                review_bytes=authorization["review_bytes"],
                pending_effect_bytes=authorization["effect_bytes"],
                finality_effect_bytes=finality_effect,
                signed_review=signed_review,
                preauthorization=preauthorization,
                verified_finalities=bundle,
                issued_at=context.issued_at,
                expires_at=context.expires_at,
            )
            verification = dict(
                envelope=envelope,
                public_keys=self._keys,
                scope=scope,
                protected_context=context,
                attempt_id=attempt_id,
                nonce=context.nonce,
                review_bytes=authorization["review_bytes"],
                pending_effect_bytes=authorization["effect_bytes"],
                finality_effect_bytes=finality_effect,
                signed_review=signed_review,
                preauthorization=preauthorization,
                verified_finalities=bundle,
                review_key=self._review_key,
                effect_key=self._effect_key,
                receipt_public_keys=self._receipt_keys,
                assertion_public_keys=self._assertion_keys,
            )
            finality_token = verify_capture_finality_authorization(
                **verification, now_seconds=int(time.time())
            )
            # This call is outside the pending writer's savepoint: SQL binds
            # consumption, guard and finality proof to the actual top-level xid.
            await conn.execute(
                "SELECT ctt_staff_private.record_capture_finality($1,$2,$3)",
                attempt_id,
                finality_effect,
                envelope,
            )
            for model, rows in (
                (Player, active["players"]),
                (
                    TeamStaff,
                    [
                        slot["row"]
                        for slot in active["staff"]
                        if slot["row"] is not None
                    ],
                ),
            ):
                fields = ["governance_state", "finality_receipt_id", "updated_at"]
                if model is Player:
                    fields += ["postcommit_revision", "postcommit_snapshot_hash"]
                columns = model.__table__.columns
                assignment = ",".join(
                    '"' + name + '"=$' + str(index)
                    for index, name in enumerate(fields, 1)
                )
                statement = (
                    f"UPDATE public.{model.__tablename__} SET {assignment} "
                    f"WHERE id=${len(fields)+1} "
                    "AND governance_state='PENDING_FINALITY' "
                    "AND finality_receipt_id IS NULL RETURNING *"
                )
                for row in rows:
                    result = await conn.fetchrow(
                        statement,
                        *[_value(row[name], columns[name]) for name in fields],
                        UUID(row["id"]),
                    )
                    if result is None or _observed(result, model) != row:
                        _deny()
            if await self._read_rows(conn, pending) != active:
                _deny()
            source.verify_unchanged()
            finality_token = verify_capture_finality_authorization(
                **verification, now_seconds=int(time.time())
            )
            if self._photos is not None:
                self._photos.verify_effect_photos(active)
            return FinalizedCaptureTokens(pending_token, finality_token)
        except Exception:
            _deny()
