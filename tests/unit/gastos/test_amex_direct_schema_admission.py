"""Metadata admission fails closed on weakened owner DDL without business reads."""

import hashlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from devnous.gastos.amex_schema_policy import (
    AMEX_DIRECT_OWNER_MIGRATION_TABLES,
    _catalog_check,
)
from devnous.gastos.models import Base
from devnous.gastos.services.amex_direct_schema_contract import (
    EXPECTED_CHECKS,
    EXPECTED_EFFECTIVE_VIEW_DEFINITION,
    EXPECTED_FUNCTION_HASHES,
    EXPECTED_INDEXES,
    EXPECTED_KEYS,
    EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS,
    EXPECTED_SETTLEMENT_TEXT_VIEW_HASHES,
    EXPECTED_SETTLEMENT_VIEW_DEFINITIONS,
    EXPECTED_SETTLEMENT_VIEW_HASHES,
    EXPECTED_TRIGGERS,
)
from devnous.gastos.services.amex_direct_schema_service import (
    inspect_amex_direct_schema,
)

ROOT = Path(__file__).resolve().parents[3]


def catalog():
    names = AMEX_DIRECT_OWNER_MIGRATION_TABLES | {
        "amex_statement_imports",
        "amex_statement_import_items",
        "amex_recognition_consumptions",
    }
    columns = [
        (
            name,
            column.name,
            str(column.type.compile(dialect=postgresql.dialect())),
            not column.nullable,
        )
        for name in names
        for column in Base.metadata.tables[name].columns
    ]
    constraints = []
    for key in EXPECTED_KEYS:
        name, kind, columns_key = key[:3]
        target, namespace, targets, deletion = None, None, [], " "
        if kind == "f":
            target, targets, deletion = key[3:]
            namespace = "owner_schema"
        constraints.append(
            (
                name,
                "key",
                kind,
                True,
                columns_key,
                target,
                namespace,
                targets,
                deletion,
                "",
            )
        )
    constraints.extend(
        (name, label, "c", True, [], None, None, [], " ", definition)
        for (name, label), definition in EXPECTED_CHECKS.items()
    )
    triggers = [(name, *shape) for name, shape in EXPECTED_TRIGGERS.items()]
    bodies = {}
    for file in [
        "20261001_amex_shared_recognition.sql",
        "20261013_amex_approved_informe_recognition.sql",
        "20261013_amex_statement_workflow.sql",
        "20261014_amex_sat_budget_direct.sql",
        "20261015_amex_direct_period_recovery.sql",
        "20261020_amex_expense_account_settlements.sql",
        "20261021_amex_direct_no_cfdi.sql",
    ]:
        source = (ROOT / "database/migrations" / file).read_text()
        bodies.update(
            re.findall(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z_]\w*)\(\)"
                r"\s+RETURNS\s+TRIGGER\b.*?\$\$(.*?)\$\$",
                source,
                re.I | re.S,
            )
        )
    functions = [(name, bodies[name]) for name in EXPECTED_FUNCTION_HASHES]
    indexes = [
        (
            table,
            name,
            True,
            True,
            definition.replace(" ON " + table, " ON owner_schema." + table),
        )
        for name, (table, definition) in EXPECTED_INDEXES.items()
    ]
    return columns, constraints, triggers, functions, indexes


async def inspect(parts, settlement_views=None):
    columns, constraints, triggers, functions, indexes = parts
    session = AsyncMock()
    results = []
    helper_source = (
        ROOT / "database/migrations/20261021_amex_direct_no_cfdi.sql"
    ).read_text()
    helpers = re.findall(
        (
            "CREATE FUNCTION (amex_direct_no_cfdi_\\w+"
            ")\\(item_uuid uuid\\) RETURNS (?:text|void"
            ") LANGUAGE (?:sql STABLE|plpgsql) AS \\$\\"
            "$(.*?)\\$\\$"
        ),
        helper_source,
        re.S,
    )
    for rows in [columns, constraints, None, triggers, functions, helpers, indexes]:
        result = MagicMock()
        result.all.return_value = rows
        result.scalar_one.return_value = "owner_schema"
        results.append(result)
    view_result = MagicMock()
    view_result.scalar_one_or_none.return_value = EXPECTED_EFFECTIVE_VIEW_DEFINITION
    results.append(view_result)
    for definition in (
        EXPECTED_SETTLEMENT_VIEW_DEFINITIONS
        if settlement_views is None
        else settlement_views
    ).values():
        settlement_view = MagicMock()
        settlement_view.scalar_one_or_none.return_value = definition
        results.append(settlement_view)
    session.execute.side_effect = results
    answer = await inspect_amex_direct_schema(session)
    queries = [str(call.args[0]).lower() for call in session.execute.call_args_list]
    assert all(query.startswith("select ") for query in queries)
    assert all(" from amex_" not in query for query in queries)
    return answer


@pytest.mark.asyncio
async def test_complete_exact_catalog_requires_no_business_data():
    assert await inspect(catalog()) == {
        "ready": True,
        "missing_columns": {},
        "missing_triggers": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "missing_column",
        "column_type",
        "nullability",
        "key_missing",
        "key_unvalidated",
        "fk_delete",
        "fk_namespace",
        "check_weakened",
        "check_literal_case",
        "trigger_disabled",
        "trigger_deferral",
        "function_body",
        "function_missing",
        "index_missing",
        "index_not_unique",
        "index_invalid",
        "index_predicate",
    ],
)
async def test_metadata_corruption_never_admits_financial_source(damage):
    columns, constraints, triggers, functions, indexes = catalog()
    if damage.startswith("column") or damage in {"missing_column", "nullability"}:
        original = columns[0]
        if damage == "missing_column":
            columns.pop(0)
        elif damage == "column_type":
            columns[0] = (*original[:2], "text", original[3])
        else:
            columns[0] = (*original[:3], not original[3])
    elif damage.startswith("key") or damage.startswith("fk"):
        index = next(i for i, row in enumerate(constraints) if row[2] == "f")
        row = list(constraints[index])
        if damage == "key_missing":
            constraints.pop(index)
        else:
            if damage == "key_unvalidated":
                row[3] = False
            elif damage == "fk_delete":
                row[8] = "c"
            else:
                row[6] = "other_schema"
            constraints[index] = tuple(row)
    elif damage.startswith("check"):
        index = next(
            i for i, row in enumerate(constraints) if row[2] == "c" and "MXN" in row[9]
        )
        row = list(constraints[index])
        row[9] = (
            "CHECK (true)"
            if damage == "check_weakened"
            else row[9].replace("MXN", "mxn")
        )
        constraints[index] = tuple(row)
    elif damage.startswith("trigger"):
        if damage == "trigger_disabled":
            triggers.pop(0)
        else:
            row = triggers[0]
            triggers[0] = (*row[:4], not row[4], row[5])
    elif damage.startswith("function"):
        if damage == "function_missing":
            functions.pop(0)
        else:
            functions[0] = (functions[0][0], "BEGIN RETURN NEW; END")
    elif damage.startswith("index"):
        if damage == "index_missing":
            indexes.pop(0)
        else:
            row = list(indexes[0])
            if damage == "index_not_unique":
                row[2] = False
            elif damage == "index_invalid":
                row[3] = False
            else:
                row[4] += " WHERE true"
            indexes[0] = tuple(row)
    result = await inspect((columns, constraints, triggers, functions, indexes))
    assert result["ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_runtime_admission_never_installs_missing_owner_schema(
    monkeypatch, ready
):
    from devnous.gastos.services import amex_direct_schema_service as service

    probe = AsyncMock(return_value={"ready": ready})
    monkeypatch.setattr(service, "inspect_amex_direct_schema", probe)
    session = AsyncMock()
    if ready:
        await service.require_amex_direct_schema(session)
    else:
        with pytest.raises(
            service.DirectSchemaUnavailable, match="direct_source_not_enabled"
        ):
            await service.require_amex_direct_schema(session)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,name",
    [
        ("trigger", "amex_approval_proof_guard"),
        ("trigger", "amex_statement_link_guard"),
        ("function", "amex_guard_source_evidence"),
        ("function", "amex_statement_immutable"),
    ],
)
async def test_legacy_owner_guards_remain_required_after_settlement(kind, name):
    """New DDL cannot erase guards whose names predate the direct prefix."""
    parts = list(catalog())
    index = 2 if kind == "trigger" else 3
    assert any(row[0] == name for row in parts[index])
    parts[index] = [row for row in parts[index] if row[0] != name]
    assert (await inspect(tuple(parts)))["ready"] is False


@pytest.mark.parametrize("form", ["varchar", "text"])
def test_exact_complete_settlement_view_hash_variants(form):
    definitions = (
        EXPECTED_SETTLEMENT_VIEW_DEFINITIONS
        if form == "varchar"
        else EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS
    )
    hashes = (
        EXPECTED_SETTLEMENT_VIEW_HASHES
        if form == "varchar"
        else EXPECTED_SETTLEMENT_TEXT_VIEW_HASHES
    )
    assert definitions.keys() == EXPECTED_SETTLEMENT_VIEW_DEFINITIONS.keys()
    for name, definition in definitions.items():
        assert (
            hashlib.sha256(_catalog_check(definition).encode()).hexdigest()
            == hashes[name]
        )
        if form == "text":
            expected = (
                EXPECTED_SETTLEMENT_VIEW_DEFINITIONS[name]
                .replace("d.tipo::text", "d.tipo")
                .replace("d.estado::text", "d.estado")
                .replace("e.estado_gasto::text", "e.estado_gasto")
            )
            assert _catalog_check(definition) == _catalog_check(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["varchar", "text"])
async def test_exact_text_and_varchar_settlement_views_admit_owner_schema(form):
    definitions = (
        EXPECTED_SETTLEMENT_VIEW_DEFINITIONS
        if form == "varchar"
        else EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS
    )
    result = await inspect(catalog(), definitions)
    assert result["ready"], result


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["varchar", "text"])
@pytest.mark.parametrize("name", sorted(EXPECTED_SETTLEMENT_VIEW_DEFINITIONS))
@pytest.mark.parametrize("damage", ["approval", "amount", "identity"])
async def test_each_view_variant_rejects_changed_financial_predicates(
    form, name, damage
):
    definitions = dict(
        EXPECTED_SETTLEMENT_VIEW_DEFINITIONS
        if form == "varchar"
        else EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS
    )
    original = definitions[name]
    if damage == "approval":
        changed = original.replace("'aprobado'::text", "'pendiente'::text")
    elif damage == "identity":
        changed = original.replace(
            "cf.id = e.cfdi_report_id", "cf.id <> e.cfdi_report_id"
        )
    elif name == "amex_direct_prior_sources":
        changed = original.replace(
            "l.haber::numeric = rc.amount", "l.haber::numeric >= rc.amount"
        )
    else:
        changed = original.replace(
            "l.haber > 0::double precision", "l.haber >= 0::double precision"
        )
    assert changed != original
    definitions[name] = changed
    result = await inspect(catalog(), definitions)
    assert not result["ready"]
    assert name + ": incompatible settlement view definition" in result["schema_errors"]


@pytest.mark.asyncio
async def test_text_variant_does_not_strip_other_identity_casts():
    definitions = dict(EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS)
    name = "amex_expense_account_credit_sources"
    original = definitions[name]
    definitions[name] = original.replace("p.origen::text", "p.origen")
    assert definitions[name] != original
    assert not (await inspect(catalog(), definitions))["ready"]
