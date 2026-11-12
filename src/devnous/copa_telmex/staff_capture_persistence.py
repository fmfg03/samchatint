"""Exact provisional row materialization for the isolated trusted writer.

No endpoint or role is created. Current production remains closed until all
ownership, provenance, authority, source and transaction prerequisites hold.
The caller owns the transaction, nonce consumption, expiry guards and source
snapshot. Gate signatures must come from independently accredited keys.
"""

import json
import time
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, Float, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from .models import Player, Team, TeamStaff
from .staff_capture_authorization import verify_capture_authorization
from .staff_capture_executor import CaptureDenied


def _column_signature(column):
    kind = column.type.dialect_impl(postgresql.dialect())
    if isinstance(kind, DateTime):
        name = (
            "timestamp with time zone"
            if kind.timezone
            else "timestamp without time zone"
        )
    elif isinstance(kind, JSONB):
        name = "jsonb"
    elif isinstance(kind, JSON):
        name = "json"
    elif isinstance(column.type, Text):
        name = "text"
    elif isinstance(kind, String):
        name = "character varying"
    elif isinstance(kind, BigInteger):
        name = "bigint"
    elif isinstance(column.type, Float):
        name = "double precision"
    else:
        name = {UUID: "uuid", int: "integer", bool: "boolean", date: "date"}[
            kind.python_type
        ]
    modifier = kind.length + 4 if name == "character varying" and kind.length else -1
    return name, modifier, not column.nullable


async def _check_boundary(conn):
    if not conn.is_in_transaction():
        raise CaptureDenied("capture denied")
    if await conn.fetchval("SHOW max_prepared_transactions") != "0":
        raise CaptureDenied("capture denied")
    role = await conn.fetchrow(
        "SELECT current_user AS name, rolsuper, rolcreaterole, rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname=current_user"
    )
    if role["name"] == "devnous_user" or any(
        role[key] for key in ("rolsuper", "rolcreaterole", "rolbypassrls")
    ):
        raise CaptureDenied("capture denied")
    for model in (Team, Player, TeamStaff):
        table = "public." + model.__tablename__
        unsafe = await conn.fetchval(
            "SELECT pg_catalog.pg_has_role(current_user,c.relowner,'MEMBER') "
            "OR pg_catalog.pg_has_role('devnous_user',c.relowner,'MEMBER') "
            "OR pg_catalog.has_table_privilege('devnous_user',c.oid, "
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') "
            "OR pg_catalog.has_any_column_privilege('devnous_user',c.oid, "
            "'INSERT,UPDATE') FROM pg_catalog.pg_class c "
            "WHERE c.oid=$1::regclass",
            table,
        )
        if unsafe is not False:
            raise CaptureDenied("capture denied")
        unsafe_triggers = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
            "WHERE t.tgrelid=$1::regclass AND NOT t.tgisinternal AND ("
            "pg_catalog.pg_has_role('devnous_user',p.proowner,'MEMBER') "
            "OR NOT (coalesce(p.proconfig,ARRAY[]::text[]) "
            "&& ARRAY['search_path=pg_catalog',"
            "'search_path=pg_catalog, public']::text[])))",
            table,
        )
        if unsafe_triggers:
            raise CaptureDenied("capture denied")
        names = await conn.fetch(
            "SELECT attname, atttypid::regtype::text AS type_name, "
            "atttypmod,attnotnull FROM pg_catalog.pg_attribute "
            "WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped",
            table,
        )
        if {row["attname"] for row in names} != set(model.__table__.columns.keys()):
            raise CaptureDenied("capture denied")
        columns = {column.name: column for column in model.__table__.columns}
        for item in names:
            if (
                item["type_name"],
                item["atttypmod"],
                item["attnotnull"],
            ) != _column_signature(columns[item["attname"]]):
                raise CaptureDenied("capture denied")


def _value(value, column):
    if value is None:
        return None
    if isinstance(column.type, JSON):
        if type(value) is not dict:
            raise CaptureDenied("capture denied")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    kind = column.type.python_type
    if kind is UUID:
        return UUID(value)
    if kind is datetime:
        return datetime.fromisoformat(value)
    if kind is date:
        return date.fromisoformat(value)
    return value


def _observed(record, model):
    result = {}
    for column in model.__table__.columns:
        value = record[column.name]
        if value is not None:
            kind = column.type.python_type
            if kind in (datetime, date):
                value = value.isoformat()
            elif kind is UUID:
                value = str(value)
            elif isinstance(column.type, JSON):
                if type(value) is str:
                    value = json.loads(value)
                if type(value) is not dict:
                    raise CaptureDenied("capture denied")
        result[column.name] = value
    return result


async def _persist_pending_capture(conn, *, photo_store=None, **authorization):
    """Verify authorization and write/read back exact rows in a savepoint.

    Caller supplies the locked scope, attempt and nonce, both private payloads
    and pinned keys accepted by verify_capture_authorization. Caller cannot
    supply the verification clock. No finality transition is performed here.
    """
    try:
        if "now_seconds" in authorization:
            raise CaptureDenied("capture denied")
        await _check_boundary(conn)
        async with conn.transaction():
            token = verify_capture_authorization(
                **authorization, now_seconds=int(time.time())
            )
            effect = json.loads(authorization["effect_bytes"])
            photo_rows = effect["players"] + [
                item["row"] for item in effect["staff"] if item["row"] is not None
            ]
            has_photos = any(row["photo_path"] is not None for row in photo_rows)
            if has_photos:
                if photo_store is None:
                    raise CaptureDenied("capture denied")
                photo_store.verify_effect_photos(effect)
            rows = [(Team, effect["team"])]
            rows.extend((Player, row) for row in effect["players"])
            rows.extend(
                (TeamStaff, item["row"])
                for item in effect["staff"]
                if item["row"] is not None
            )
            for model, row in rows:
                columns = list(model.__table__.columns)
                names = ",".join('"' + c.name + '"' for c in columns)
                params = ",".join("$" + str(i) for i in range(1, len(columns) + 1))
                observed = await conn.fetchrow(
                    f"INSERT INTO public.{model.__tablename__} ({names}) "
                    f"VALUES ({params}) RETURNING *",
                    *[_value(row[c.name], c) for c in columns],
                )
                if _observed(observed, model) != row:
                    raise CaptureDenied("capture denied")
            for model, row in rows:
                observed = await conn.fetchrow(
                    f"SELECT * FROM public.{model.__tablename__} WHERE id=$1",
                    UUID(row["id"]),
                )
                if observed is None or _observed(observed, model) != row:
                    raise CaptureDenied("capture denied")
            verify_capture_authorization(**authorization, now_seconds=int(time.time()))
            if has_photos:
                photo_store.verify_effect_photos(effect)
            return token
    except Exception:
        raise CaptureDenied("capture denied") from None


class PendingCaptureWriter:
    """Internal writer with keys pinned by trusted service configuration.

    Never construct this from HTTP inputs. The service must independently
    resolve and lock scope/attempt/nonce and own the entire outer transaction.
    No operational service or source of trusted configuration exists here yet.
    """

    def __init__(
        self, *, public_keys, review_key: bytes, effect_key: bytes, photo_store=None
    ):
        from types import MappingProxyType

        if (
            type(review_key) is not bytes
            or len(review_key) < 32
            or type(effect_key) is not bytes
            or len(effect_key) < 32
        ):
            raise CaptureDenied("capture denied")
        self._public_keys = MappingProxyType(dict(public_keys))
        self._review_key = review_key
        self._effect_key = effect_key
        self._photo_store = photo_store

    async def persist(
        self,
        conn,
        *,
        envelope,
        scope,
        attempt_id,
        nonce,
        review_bytes,
        effect_bytes,
    ):
        return await _persist_pending_capture(
            conn,
            envelope=envelope,
            scope=scope,
            attempt_id=attempt_id,
            nonce=nonce,
            review_bytes=review_bytes,
            effect_bytes=effect_bytes,
            public_keys=self._public_keys,
            review_key=self._review_key,
            effect_key=self._effect_key,
            photo_store=self._photo_store,
        )


class BatchPendingCaptureWriter(PendingCaptureWriter):
    """Explicit V3 pending writer using the same exact row/boundary checks."""

    async def persist(self, conn, *, member_proof_bytes, **authorization):
        from .staff_batch_authorization import BatchReviewScope

        if type(authorization.get("scope")) is not BatchReviewScope:
            raise CaptureDenied("capture denied")
        return await _persist_pending_capture(
            conn,
            **authorization,
            member_proof_bytes=member_proof_bytes,
            public_keys=self._public_keys,
            review_key=self._review_key,
            effect_key=self._effect_key,
            photo_store=self._photo_store,
        )
