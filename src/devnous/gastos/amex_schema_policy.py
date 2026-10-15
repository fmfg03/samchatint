"""Keep owner-installed AMEX accounting schema outside runtime startup DDL."""

import re
from typing import Any

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

AMEX_OWNER_MIGRATION_TABLES = frozenset(
    {
        "amex_recognition_activation",
        "amex_recognition_consumptions",
        "amex_recognition_representations",
        "amex_accounting_reviews",
        "amex_accounting_cuts",
        "amex_report_source_evidences",
        "amex_report_bank_associations",
    }
)

CTT_OWNER_MIGRATION_TABLES = frozenset(
    {
        "copa_telmex_registration_batches",
        "copa_telmex_registration_batch_documents",
        "copa_telmex_registration_batch_uploads",
        "copa_telmex_registration_batch_upload_files",
        "copa_telmex_team_staff",
    }
)

AMEX_STATEMENT_OWNER_MIGRATION_TABLES = frozenset(
    {
        "amex_statement_imports",
        "amex_statement_import_items",
        "amex_statement_cut_links",
    }
)

AMEX_DIRECT_OWNER_MIGRATION_TABLES = frozenset(
    {
        "amex_statement_source_files",
        "amex_statement_direct_contexts",
        "amex_statement_direct_state",
        "amex_merchant_identity_aliases",
        "amex_cfdi_status_receipts",
        "amex_direct_items",
        "amex_direct_events",
        "amex_direct_accounting_cuts",
        "amex_direct_cut_items",
    }
)

OWNER_MIGRATION_TABLES = (
    AMEX_OWNER_MIGRATION_TABLES
    | CTT_OWNER_MIGRATION_TABLES
    | AMEX_STATEMENT_OWNER_MIGRATION_TABLES
    | AMEX_DIRECT_OWNER_MIGRATION_TABLES
)


def runtime_managed_tables(metadata: MetaData) -> list[Table]:
    """Preserve existing startup management while excluding owner-run AMEX DDL."""
    return [
        table
        for table in metadata.sorted_tables
        if table.name not in OWNER_MIGRATION_TABLES
    ]


def _catalog_type(value: str) -> str:
    """Compare PostgreSQL catalog and SQLAlchemy spellings without losing sizes."""
    return "".join(value.lower().replace("character varying", "varchar").split())


def _catalog_check(value: str) -> str:
    """Ignore layout only; keep literal case, whitespace, and boolean grouping."""
    return "".join(re.findall(r"'(?:''|[^'])*'|[^\s]", value))


# Exact owner-DDL checks as PostgreSQL renders them. Preserve parentheses and
# boolean operators: an identically named weaker CHECK must fail admission.
_APPROVED_CHECKS = {
    "ck_amex_consumption_source_anchor": "CHECK (((((source_kind)::text = "
    "'statement'::text) AND (imported_expense_id IS "
    "NOT NULL) AND (source_report_expense_id IS "
    "NULL) AND ((binding_mode)::text = ANY "
    "((ARRAY['legacy'::character varying, "
    "'report_bridge'::character varying, "
    "'direct'::character varying])::text[]))) OR "
    "(((source_kind)::text = 'report'::text) AND "
    "(imported_expense_id IS NULL) AND "
    "(source_report_expense_id IS NOT NULL) AND "
    "((binding_mode)::text = 'report'::text))))",
    "ck_amex_bridge_never_posted": "CHECK ((((binding_mode)::text <> "
    "'report_bridge'::text) OR (accounting_poliza_id IS "
    "NULL)))",
    "ck_amex_source_evidence_origin": "CHECK (((((kind)::text = "
    "'operational_approval'::text) AND (approval_id IS "
    "NOT NULL) AND (decisions_json IS NULL) AND "
    "(request_payload_hash IS NULL)) OR (((kind)::text "
    "= 'finance_confirmation'::text) AND (approval_id "
    "IS NULL) AND (decisions_json IS NOT NULL) AND "
    "(request_payload_hash IS NOT NULL))))",
}


async def inspect_approved_informe_schema(
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

    from .models import Base

    tables = {
        table.name: table
        for table in Base.metadata.tables.values()
        if table.name in AMEX_OWNER_MIGRATION_TABLES
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
            {"names": sorted(AMEX_OWNER_MIGRATION_TABLES)},
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
            {"names": sorted(AMEX_OWNER_MIGRATION_TABLES)},
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

    triggers = {
        "amex_source_evidence_guard": "amex_report_source_evidences",
        "amex_approval_proof_guard": "aprobaciones",
        "amex_consumption_guard": "amex_recognition_consumptions",
        "amex_representation_guard": "amex_recognition_representations",
        "amex_report_anchor_consumption": "amex_recognition_consumptions",
        "amex_report_anchor_representation": "amex_recognition_representations",
        "amex_bank_association_guard": "amex_report_bank_associations",
        "amex_bank_group_exact": "amex_report_bank_associations",
        "amex_bridged_expense_guard": "expense_reports",
        "amex_cut_history_guard": "amex_accounting_cuts",
    }
    installed_triggers = set(
        (
            await session.execute(
                text(
                    "SELECT t.tgname,c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND NOT t.tgisinternal "
                    "AND t.tgname=ANY(:names) AND t.tgenabled IN ('O','A')"
                ),
                {"names": sorted(triggers)},
            )
        ).all()
    )
    missing_triggers = sorted(
        name
        for name, table in triggers.items()
        if (name, table) not in installed_triggers
    )
    result = {
        "ready": not missing and not missing_triggers and not errors,
        "missing_columns": missing,
        "missing_triggers": missing_triggers,
    }
    if errors:
        result["schema_errors"] = sorted(errors)
    return result
