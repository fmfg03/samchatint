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


def runtime_managed_tables(metadata: MetaData) -> list[Table]:
    """Preserve existing startup management while excluding owner-run AMEX DDL."""
    return [
        table
        for table in metadata.sorted_tables
        if table.name not in AMEX_OWNER_MIGRATION_TABLES
    ]
