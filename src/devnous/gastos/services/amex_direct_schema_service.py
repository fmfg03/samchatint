"""Catalog-only admission of the owner-installed direct AMEX source contract."""

import hashlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ..amex_schema_policy import (
    AMEX_DIRECT_OWNER_MIGRATION_TABLES,
    _catalog_check,
    _catalog_type,
)


class DirectSchemaUnavailable(RuntimeError):
    """The source owner has not been installed with its complete integrity guards."""


async def require_amex_direct_schema(
    session: AsyncConnection | AsyncSession,
) -> None:
    """Fail closed without granting the application permission to install DDL."""
    result = await inspect_amex_direct_schema(session)
    if not result["ready"]:
        raise DirectSchemaUnavailable("direct_source_not_enabled")


async def inspect_amex_direct_schema(
    session: AsyncConnection | AsyncSession,
) -> dict[str, Any]:
    """Verify exact owner checks, keys and trigger bodies without business reads."""
    from ..models import Base
    from .amex_direct_schema_contract import (
        EXPECTED_CHECKS,
        EXPECTED_EFFECTIVE_VIEW_HASH,
        EXPECTED_FUNCTION_HASHES,
        EXPECTED_INDEXES,
        EXPECTED_KEYS,
        EXPECTED_SETTLEMENT_TEXT_VIEW_HASHES,
        EXPECTED_SETTLEMENT_VIEW_HASHES,
        EXPECTED_TRIGGERS,
    )

    names = sorted(
        AMEX_DIRECT_OWNER_MIGRATION_TABLES
        | {
            "amex_statement_imports",
            "amex_statement_import_items",
            "amex_recognition_consumptions",
        }
    )
    tables = {name: Base.metadata.tables[name] for name in names}
    rows = (
        await session.execute(
            text(
                "SELECT c.relname,a.attname, "
                "pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=ANY(:names) "
                "AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped"
            ),
            {"names": names},
        )
    ).all()
    found = {
        (name, column): (type_name, not_null)
        for name, column, type_name, not_null in rows
    }
    missing: dict[str, list[str]] = {}
    errors: list[str] = []
    dialect = postgresql.dialect()
    for name, table in tables.items():
        absent = []
        for column in table.columns:
            actual = found.get((name, column.name))
            if actual is None:
                absent.append(column.name)
                continue
            expected_type = str(column.type.compile(dialect=dialect))
            if _catalog_type(actual[0]) != _catalog_type(expected_type):
                errors.append(f"{name}.{column.name}: incompatible type")
            if actual[1] != (not column.nullable):
                errors.append(f"{name}.{column.name}: incompatible nullability")
        if absent:
            missing[name] = sorted(absent)

    rows = (
        await session.execute(
            text(
                "SELECT c.relname,k.conname,k.contype::text,k.convalidated, "
                "ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY "
                "AS key(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.conrelid "
                "AND a.attnum=key.attnum ORDER BY key.ord), r.relname,rn.nspname, "
                "ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY "
                "AS key(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.confrelid "
                "AND a.attnum=key.attnum ORDER BY key.ord), "
                "k.confdeltype::text,pg_get_constraintdef(k.oid) "
                "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_class r ON r.oid=k.confrelid "
                "LEFT JOIN pg_namespace rn ON rn.oid=r.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=ANY(:names)"
            ),
            {"names": names},
        )
    ).all()
    installed_keys: set[tuple[Any, ...]] = set()
    installed_checks = {}
    for (
        name,
        label,
        kind,
        valid,
        columns,
        target,
        namespace,
        target_columns,
        deletion,
        definition,
    ) in rows:
        if not valid:
            continue
        if kind == "f":
            installed_keys.add(
                (
                    name,
                    kind,
                    tuple(columns),
                    target,
                    tuple(target_columns),
                    deletion,
                    namespace,
                )
            )
        elif kind in {"p", "u"}:
            installed_keys.add((name, kind, tuple(columns)))
        elif kind == "c":
            installed_checks[(name, label)] = _catalog_check(definition)
    schema = (await session.execute(text("SELECT current_schema()"))).scalar_one()
    for key in EXPECTED_KEYS:
        expected = tuple(key)
        if expected[1] == "f":
            expected = (*expected, schema)
        if expected not in installed_keys:
            errors.append(f"{expected[0]}: missing compatible validated key")
    for key, definition in EXPECTED_CHECKS.items():
        if installed_checks.get(key) != _catalog_check(definition):
            errors.append(f"{key[0]}.{key[1]}: incompatible validated CHECK")

    triggers = (
        await session.execute(
            text(
                "SELECT t.tgname,c.relname,p.proname,t.tgtype::integer, "
                "t.tgdeferrable,t.tginitdeferred FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_proc p ON p.oid=t.tgfoid "
                "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
                "WHERE n.nspname=current_schema() AND pn.nspname=current_schema() "
                "AND NOT t.tgisinternal AND t.tgname=ANY(:names) "
                "AND t.tgenabled IN ('O','A')"
            ),
            {"names": sorted(EXPECTED_TRIGGERS)},
        )
    ).all()
    installed_triggers = {row[0]: tuple(row[1:]) for row in triggers}
    missing_triggers = sorted(
        name
        for name, shape in EXPECTED_TRIGGERS.items()
        if installed_triggers.get(name) != shape
    )
    functions = (
        await session.execute(
            text(
                "SELECT p.proname,p.prosrc FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "JOIN pg_language l ON l.oid=p.prolang "
                "WHERE n.nspname=current_schema() AND p.proname=ANY(:names) "
                "AND p.pronargs=0 AND p.prorettype='trigger'::regtype "
                "AND l.lanname='plpgsql'"
            ),
            {"names": sorted(EXPECTED_FUNCTION_HASHES)},
        )
    ).all()
    installed_functions = {name: body for name, body in functions}
    for name, digest in EXPECTED_FUNCTION_HASHES.items():
        body = installed_functions.get(name)
        if body is None or hashlib.sha256(body.encode()).hexdigest() != digest:
            errors.append(f"{name}: incompatible owner guard body")
    indexes = (
        await session.execute(
            text(
                "SELECT c.relname,ic.relname,i.indisunique,i.indisvalid, "
                "pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indrelid "
                "JOIN pg_class ic ON ic.oid=i.indexrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND ic.relname=ANY(:names)"
            ),
            {"names": sorted(EXPECTED_INDEXES)},
        )
    ).all()
    installed_indexes = {row[1]: row for row in indexes}
    for name, expected in EXPECTED_INDEXES.items():
        row = installed_indexes.get(name)
        # Namespace alone varies between isolated verification and production.
        if row is None or row[0] != expected[0] or not row[2] or not row[3]:
            errors.append(f"{name}: missing compatible unique index")
        elif _catalog_check(row[4].replace(schema + ".", "")) != _catalog_check(
            expected[1]
        ):
            errors.append(f"{name}: incompatible unique index predicate")
    view = (
        await session.execute(
            text(
                "SELECT pg_get_viewdef(c.oid,true) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE "
                "n.nspname=current_schema() "
                "AND c.relname='amex_direct_effective_sources' "
                "AND c.relkind='v'"
            )
        )
    ).scalar_one_or_none()
    if (
        view is None
        or hashlib.sha256(
            _catalog_check(view.replace(schema + ".", "")).encode()
        ).hexdigest()
        != EXPECTED_EFFECTIVE_VIEW_HASH
    ):
        errors.append(
            "amex_direct_effective_sources: incompatible owner view definition"
        )
    for name, expected_view_hash in EXPECTED_SETTLEMENT_VIEW_HASHES.items():
        definition = (
            await session.execute(
                text(
                    "SELECT pg_get_viewdef(c.oid,true) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    + (
                        "WHERE n.nspname=current_schema() AND c.relname=:name "
                        "AND c.relkind='v'"
                    )
                ),
                {"name": name},
            )
        ).scalar_one_or_none()
        if definition is None or hashlib.sha256(
            _catalog_check(definition.replace(schema + ".", "")).encode()
        ).hexdigest() not in {
            expected_view_hash,
            EXPECTED_SETTLEMENT_TEXT_VIEW_HASHES[name],
        }:
            errors.append(name + ": incompatible settlement view definition")
    result = {
        "ready": not missing and not missing_triggers and not errors,
        "missing_columns": missing,
        "missing_triggers": missing_triggers,
    }
    if errors:
        result["schema_errors"] = sorted(set(errors))
    return result
