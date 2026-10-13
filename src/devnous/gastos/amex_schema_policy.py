"""Keep owner-installed AMEX accounting schema outside runtime startup DDL."""

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

OWNER_MIGRATION_TABLES = AMEX_OWNER_MIGRATION_TABLES | CTT_OWNER_MIGRATION_TABLES


def runtime_managed_tables(metadata: MetaData) -> list[Table]:
    """Preserve existing startup management while excluding owner-run AMEX DDL."""
    return [
        table
        for table in metadata.sorted_tables
        if table.name not in OWNER_MIGRATION_TABLES
    ]


async def inspect_approved_informe_schema(
    session: AsyncConnection | AsyncSession,
) -> dict[str, Any]:
    """Read allowlisted catalog metadata only; never provision or read rows."""
    from sqlalchemy import text

    required = {
        "amex_recognition_consumptions": {
            "source_kind",
            "binding_mode",
            "source_report_expense_id",
        },
        "amex_accounting_reviews": {"source_evidence_id"},
        "amex_report_source_evidences": {
            "id",
            "informe_id",
            "kind",
            "approval_id",
            "source_key",
            "snapshot_json",
            "decisions_json",
            "request_payload_hash",
        },
        "amex_report_bank_associations": {
            "statement_consumption_id",
            "report_consumption_id",
            "actor_id",
        },
    }
    rows = (
        await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(:names)"
            ),
            {"names": sorted(required)},
        )
    ).all()
    found: dict[str, set[str]] = {}
    for name, column in rows:
        found.setdefault(name, set()).add(column)
    missing = {
        name: sorted(columns - found.get(name, set()))
        for name, columns in required.items()
        if columns - found.get(name, set())
    }
    triggers = {
        "amex_source_evidence_guard",
        "amex_approval_proof_guard",
        "amex_consumption_guard",
        "amex_representation_guard",
        "amex_report_anchor_consumption",
        "amex_report_anchor_representation",
        "amex_bank_association_guard",
        "amex_bank_group_exact",
        "amex_bridged_expense_guard",
        "amex_cut_history_guard",
    }
    installed = set(
        (
            await session.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND NOT t.tgisinternal "
                    "AND t.tgname=ANY(:names) AND t.tgenabled IN ('O','A')"
                ),
                {"names": sorted(triggers)},
            )
        )
        .scalars()
        .all()
    )
    missing_triggers = sorted(triggers - installed)
    return {
        "ready": not missing and not missing_triggers,
        "missing_columns": missing,
        "missing_triggers": missing_triggers,
    }
