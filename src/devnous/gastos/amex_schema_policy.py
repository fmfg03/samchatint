"""Keep owner-installed AMEX accounting schema outside runtime startup DDL."""

from sqlalchemy import MetaData, Table

AMEX_OWNER_MIGRATION_TABLES = frozenset(
    {
        "amex_recognition_activation",
        "amex_recognition_consumptions",
        "amex_recognition_representations",
        "amex_accounting_reviews",
        "amex_accounting_cuts",
    }
)

CTT_OWNER_MIGRATION_TABLES = frozenset(
    {
        "copa_telmex_registration_batches",
        "copa_telmex_registration_batch_documents",
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
