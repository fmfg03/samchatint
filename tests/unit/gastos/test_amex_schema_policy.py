from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from devnous.gastos.amex_schema_policy import (
    AMEX_OWNER_MIGRATION_TABLES,
    runtime_managed_tables,
)


def test_startup_preserves_existing_tables_but_excludes_owner_amex_ddl():
    metadata = MetaData()
    ordinary = Table("documentos", metadata, Column("id", Integer, primary_key=True))
    for name in AMEX_OWNER_MIGRATION_TABLES:
        Table(name, metadata, Column("id", Integer, primary_key=True))
    assert runtime_managed_tables(metadata) == [ordinary]


def test_serving_entrypoint_uses_the_owner_migration_boundary():
    root = Path(__file__).resolve().parents[3]
    source = (root / "copa_telmex_dashboard.py").read_text()
    assert "tables=runtime_managed_tables(Base.metadata)" in source


# Owner-DDL compatibility is exercised against PostgreSQL by the integration
# health test. These catalog-only cases isolate incompatible partial installs.
def _compatible_catalog():
    from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
    from sqlalchemy.dialects import postgresql

    from devnous.gastos.amex_schema_policy import _APPROVED_CHECKS
    from devnous.gastos.models import Base

    columns, constraints = [], []
    for table in Base.metadata.tables.values():
        if table.name not in AMEX_OWNER_MIGRATION_TABLES:
            continue
        columns.extend(
            (
                table.name,
                column.name,
                str(column.type.compile(dialect=postgresql.dialect())),
                not column.nullable,
            )
            for column in table.columns
        )
        for constraint in table.constraints:
            names = [column.name for column in constraint.columns]
            target, namespace, target_columns, deletion = None, None, [], " "
            definition = ""
            if isinstance(constraint, ForeignKeyConstraint):
                kind = "f"
                elements = list(constraint.elements)
                target = elements[0].column.table.name
                namespace = "owner_schema"
                target_columns = [element.column.name for element in elements]
                deletion = "r"
            elif isinstance(constraint, PrimaryKeyConstraint):
                kind = "p"
            elif isinstance(constraint, UniqueConstraint):
                kind = "u"
            elif constraint.name in _APPROVED_CHECKS:
                kind = "c"
                definition = _APPROVED_CHECKS[constraint.name]
            else:
                continue
            constraints.append(
                (
                    table.name,
                    constraint.name,
                    kind,
                    True,
                    names,
                    target,
                    namespace,
                    target_columns,
                    deletion,
                    definition,
                )
            )
    triggers = [
        ("amex_source_evidence_guard", "amex_report_source_evidences"),
        ("amex_approval_proof_guard", "aprobaciones"),
        ("amex_consumption_guard", "amex_recognition_consumptions"),
        ("amex_representation_guard", "amex_recognition_representations"),
        ("amex_report_anchor_consumption", "amex_recognition_consumptions"),
        ("amex_report_anchor_representation", "amex_recognition_representations"),
        ("amex_bank_association_guard", "amex_report_bank_associations"),
        ("amex_bank_group_exact", "amex_report_bank_associations"),
        ("amex_bridged_expense_guard", "expense_reports"),
        ("amex_cut_history_guard", "amex_accounting_cuts"),
    ]
    return columns, constraints, triggers


async def _inspect_catalog(columns, constraints, triggers):
    from unittest.mock import AsyncMock, MagicMock

    from devnous.gastos.amex_schema_policy import inspect_approved_informe_schema

    session = AsyncMock()
    answers = []
    for rows in (columns, constraints, None, triggers):
        result = MagicMock()
        result.all.return_value = rows
        result.scalar_one.return_value = "owner_schema"
        answers.append(result)
    session.execute.side_effect = answers
    result = await inspect_approved_informe_schema(session)
    queries = [str(call.args[0]).lower() for call in session.execute.call_args_list]
    assert all(query.startswith("select ") for query in queries)
    assert all(" from amex_" not in query for query in queries)
    return result


@pytest.mark.asyncio
async def test_complete_catalog_is_ready_without_business_reads():
    assert await _inspect_catalog(*_compatible_catalog()) == {
        "ready": True,
        "missing_columns": {},
        "missing_triggers": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,column,type_name,not_null",
    [
        ("amex_recognition_consumptions", "imported_expense_id", "UUID", True),
        ("amex_recognition_consumptions", "source_kind", "VARCHAR(19)", True),
        ("amex_report_source_evidences", "snapshot_json", "JSON", True),
        ("amex_report_source_evidences", "version", "VARCHAR(64)", True),
        ("amex_report_source_evidences", "created_at", "TIMESTAMP", True),
        ("amex_report_source_evidences", "actor_id", "UUID", False),
        (
            "amex_report_bank_associations",
            "created_at",
            "TIMESTAMP WITH TIME ZONE",
            False,
        ),
    ],
)
async def test_partial_type_or_nullability_upgrade_is_not_ready(
    table, column, type_name, not_null
):
    columns, constraints, triggers = _compatible_catalog()
    columns = [
        (name, field, type_name, not_null) if (name, field) == (table, column) else row
        for row in columns
        for name, field, _, _ in [row]
    ]
    result = await _inspect_catalog(columns, constraints, triggers)
    assert not result["ready"]
    assert any(f"{table}.{column}" in error for error in result["schema_errors"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column", ["operational_evidence_id", "actor_id", "created_at", "version", "reason"]
)
async def test_missing_runtime_evidence_column_is_not_ready(column):
    columns, constraints, triggers = _compatible_catalog()
    columns = [
        row for row in columns if row[:2] != ("amex_report_source_evidences", column)
    ]
    result = await _inspect_catalog(columns, constraints, triggers)
    assert not result["ready"]
    assert column in result["missing_columns"]["amex_report_source_evidences"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "not_valid",
        "weaker",
        "unique",
        "fk_delete",
        "fk_schema",
        "literal_case",
    ],
)
async def test_missing_or_weakened_anchor_constraint_is_not_ready(damage):
    columns, constraints, triggers = _compatible_catalog()
    for i, row in enumerate(constraints):
        (
            name,
            label,
            kind,
            valid,
            fields,
            target,
            namespace,
            target_fields,
            deletion,
            definition,
        ) = row
        if damage in {"missing", "not_valid", "weaker", "literal_case"}:
            selected = label == "ck_amex_consumption_source_anchor"
        elif damage == "unique":
            selected = (
                name == "amex_recognition_consumptions"
                and kind == "u"
                and fields == ["source_report_expense_id"]
            )
        else:
            selected = (
                name == "amex_recognition_consumptions"
                and kind == "f"
                and fields == ["source_report_expense_id"]
            )
        if not selected:
            continue
        if damage in {"missing", "unique"}:
            del constraints[i]
        elif damage == "not_valid":
            constraints[i] = (*row[:3], False, *row[4:])
        elif damage == "weaker":
            constraints[i] = (*row[:-1], "CHECK (true)")
        elif damage == "literal_case":
            constraints[i] = (*row[:-1], row[-1].replace("'report'", "'REPORT'"))
        elif damage == "fk_delete":
            constraints[i] = (*row[:8], "a", row[9])
        else:
            constraints[i] = (*row[:6], "different_schema", *row[7:])
        break
    else:
        pytest.fail("Expected catalog witness not found")
    result = await _inspect_catalog(columns, constraints, triggers)
    assert not result["ready"]
    assert result["schema_errors"]


@pytest.mark.asyncio
async def test_same_trigger_name_on_wrong_table_is_not_ready():
    columns, constraints, triggers = _compatible_catalog()
    triggers[0] = ("amex_source_evidence_guard", "expense_reports")
    result = await _inspect_catalog(columns, constraints, triggers)
    assert not result["ready"]
    assert result["missing_triggers"] == ["amex_source_evidence_guard"]
