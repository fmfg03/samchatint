"""Metadata-only admission for the separate owner-run statement schema."""

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ..amex_schema_policy import (
    AMEX_STATEMENT_OWNER_MIGRATION_TABLES,
    _catalog_check,
    _catalog_type,
)

_APPROVED_CHECKS = {
    "ck_amex_statement_period": "CHECK ((period_end >= period_start))",
    "ck_amex_statement_counts": "CHECK (((((workflow_mode)::text = 'legacy'::text) AND "
    "(imported_count > 0) AND (skipped_count >= 0) AND "
    "(credit_count = 0) AND (unknown_count = 0) AND "
    "(total_rows = (imported_count + skipped_count))) OR "
    "(((workflow_mode)::text = 'direct'::text) AND "
    "(total_rows > 0) AND (imported_count >= 0) AND "
    "(skipped_count >= 0) AND (credit_count >= 0) AND "
    "(unknown_count >= 0) AND (total_rows = (((imported_count "
    "+ skipped_count) + credit_count) + unknown_count)))))",
    "ck_amex_statement_hash": "CHECK ((length((file_sha256)::text) = 64))",
    "ck_amex_statement_row_number": "CHECK ((row_number >= 2))",
    "ck_amex_statement_item_status": "CHECK (((((status)::text = 'imported'::text) AND "
    "(imported_expense_id IS NOT NULL) AND (reason_code "
    "IS NULL)) OR (((status)::text = 'skipped'::text) "
    "AND (imported_expense_id IS NULL) AND (reason_code "
    "IS NOT NULL)) OR (((workflow_mode)::text = "
    "'direct'::text) AND ((status)::text = "
    "'credit'::text) AND (imported_expense_id IS NULL)) "
    "OR (((workflow_mode)::text = 'direct'::text) AND "
    "((status)::text = 'unknown'::text) AND "
    "(imported_expense_id IS NULL) AND (reason_code IS "
    "NOT NULL))))",
    "ck_amex_statement_link_hash": "CHECK ((length((request_payload_hash)::text) = 64))",
}


_APPROVED_FUNCTION_HASHES = {
    "amex_statement_immutable": (
        "37a9f7994695c830288a8d9ac1223802" "d4166be6d317feeee6145b9b10055012"
    ),
    "amex_statement_item_owner": (
        "6f331f3aa95bbf32a3fdfe7a994669b8" "424d2d50eda499f786b5706d39560b05"
    ),
    "amex_statement_counts": (
        "61908a151671fd663d09bb2a6bacfad0" "e6aee84a80835c8c0a86e173cc9dd4ee"
    ),
    "amex_statement_expense_owner": (
        "99ddc5bcd7d09652095cb0b297336d93" "8ce353fe6790931ca1f9d464ad0267a2"
    ),
}


async def inspect_amex_statement_schema(
    session: AsyncConnection | AsyncSession,
) -> dict[str, Any]:
    """Admit compatible owner DDL using catalogs only, with no business reads."""
    from sqlalchemy import (
        ForeignKeyConstraint,
        PrimaryKeyConstraint,
        UniqueConstraint,
        text,
    )
    from sqlalchemy.dialects import postgresql

    from ..models import Base

    tables = {
        table.name: table
        for table in Base.metadata.tables.values()
        if table.name in AMEX_STATEMENT_OWNER_MIGRATION_TABLES
    }
    rows = (
        await session.execute(
            text(
                "SELECT c.relname, a.attname, "
                "pg_catalog.format_type(a.atttypid,a.atttypmod), a.attnotnull "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=ANY(:names) "
                "AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped"
            ),
            {"names": sorted(AMEX_STATEMENT_OWNER_MIGRATION_TABLES)},
        )
    ).all()
    found = {
        (name, column): (type_name, not_null)
        for name, column, type_name, not_null in rows
    }
    missing = {}
    errors = []
    dialect = postgresql.dialect()
    for name, table in tables.items():
        absent = []
        for column in table.columns:
            actual = found.get((name, column.name))
            if actual is None:
                absent.append(column.name)
                continue
            expected_type = _catalog_type(str(column.type.compile(dialect=dialect)))
            if _catalog_type(actual[0]) != expected_type:
                errors.append(f"{name}.{column.name}: incompatible type")
            if actual[1] != (not column.nullable):
                errors.append(f"{name}.{column.name}: incompatible nullability")
        if absent:
            missing[name] = sorted(absent)

    constraints = (
        await session.execute(
            text(
                "SELECT c.relname,k.conname,k.contype::text,k.convalidated, "
                "ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY "
                "AS key(attnum,ord) JOIN pg_attribute a "
                "ON a.attrelid=k.conrelid AND a.attnum=key.attnum ORDER BY key.ord), "
                "r.relname, rn.nspname, "
                "ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY "
                "AS key(attnum,ord) JOIN pg_attribute a "
                "ON a.attrelid=k.confrelid AND a.attnum=key.attnum ORDER BY key.ord), "
                "k.confdeltype::text, pg_get_constraintdef(k.oid) "
                "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_class r ON r.oid=k.confrelid "
                "LEFT JOIN pg_namespace rn ON rn.oid=r.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=ANY(:names)"
            ),
            {"names": sorted(AMEX_STATEMENT_OWNER_MIGRATION_TABLES)},
        )
    ).all()
    # Match key definitions rather than generated names: fresh owner migrations
    # and pre-existing SQLAlchemy installations use different unique/FK names.
    installed: set[tuple[Any, ...]] = set()
    installed_checks = {}
    for (
        name,
        label,
        kind,
        validated,
        columns,
        target,
        namespace,
        target_columns,
        deletion,
        definition,
    ) in constraints:
        if not validated:
            continue
        if kind == "f":
            installed.add(
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
        else:
            installed.add((name, kind, tuple(columns)))
        if kind == "c":
            installed_checks[(name, label)] = _catalog_check(definition)
    current_schema = (
        await session.execute(text("SELECT current_schema()"))
    ).scalar_one()
    expected: tuple[Any, ...]
    for name, table in tables.items():
        for constraint in table.constraints:
            columns = tuple(column.name for column in constraint.columns)
            if isinstance(constraint, ForeignKeyConstraint):
                elements = list(constraint.elements)
                expected = (
                    name,
                    "f",
                    columns,
                    elements[0].column.table.name,
                    tuple(element.column.name for element in elements),
                    "r",
                    current_schema,
                )
            elif isinstance(constraint, PrimaryKeyConstraint):
                expected = (name, "p", columns)
            elif isinstance(constraint, UniqueConstraint):
                expected = (name, "u", columns)
            else:
                if constraint.name in _APPROVED_CHECKS:
                    actual_check = installed_checks.get((name, constraint.name))
                    if actual_check != _catalog_check(
                        _APPROVED_CHECKS[constraint.name]
                    ):
                        errors.append(
                            f"{name}.{constraint.name}: "
                            "missing or incompatible validated CHECK"
                        )
                continue
            if expected not in installed:
                errors.append(
                    f"{name}: missing compatible validated {expected[1]} "
                    f"key ({','.join(columns)})"
                )

    # Admit the actual function/event/deferral shape, not merely a trigger name.
    triggers = {
        "amex_statement_import_guard": (
            "amex_statement_imports",
            "amex_statement_immutable",
            27,
            False,
            False,
        ),
        "amex_statement_item_guard": (
            "amex_statement_import_items",
            "amex_statement_immutable",
            27,
            False,
            False,
        ),
        "amex_statement_link_guard": (
            "amex_statement_cut_links",
            "amex_statement_immutable",
            27,
            False,
            False,
        ),
        "amex_statement_item_owner_guard": (
            "amex_statement_import_items",
            "amex_statement_item_owner",
            7,
            False,
            False,
        ),
        "amex_statement_counts_guard": (
            "amex_statement_imports",
            "amex_statement_counts",
            5,
            True,
            True,
        ),
        "amex_statement_item_counts_guard": (
            "amex_statement_import_items",
            "amex_statement_counts",
            5,
            True,
            True,
        ),
        "amex_statement_expense_owner_guard": (
            "expense_reports",
            "amex_statement_expense_owner",
            19,
            False,
            False,
        ),
    }
    installed_triggers = {
        row[0]: tuple(row[1:])
        for row in (
            await session.execute(
                text(
                    "SELECT t.tgname,c.relname,p.proname,t.tgtype::integer, "
                    "t.tgdeferrable,t.tginitdeferred FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_proc p ON p.oid=t.tgfoid "
                    "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
                    "WHERE n.nspname=current_schema() "
                    "AND pn.nspname=current_schema() "
                    "AND NOT t.tgisinternal AND t.tgname=ANY(:names) "
                    "AND t.tgenabled IN ('O','A')"
                ),
                {"names": sorted(triggers)},
            )
        ).all()
    }
    missing_triggers = sorted(
        name
        for name, shape in triggers.items()
        if installed_triggers.get(name) != shape
    )
    installed_functions: dict[str, str] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                text(
                    "SELECT p.proname,p.prosrc FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "JOIN pg_language l ON l.oid=p.prolang "
                    "WHERE n.nspname=current_schema() "
                    "AND p.proname=ANY(:names) AND p.pronargs=0 "
                    "AND p.prorettype='trigger'::regtype "
                    "AND l.lanname='plpgsql'"
                ),
                {"names": sorted(_APPROVED_FUNCTION_HASHES)},
            )
        ).all()
    }
    for name, expected_hash in _APPROVED_FUNCTION_HASHES.items():
        body = installed_functions.get(name)
        if body is None or hashlib.sha256(body.encode()).hexdigest() != expected_hash:
            errors.append(f"{name}: missing or incompatible immutable guard body")
    result = {
        "ready": not missing and not missing_triggers and not errors,
        "missing_columns": missing,
        "missing_triggers": missing_triggers,
    }
    if errors:
        result["schema_errors"] = sorted(errors)
    return result
