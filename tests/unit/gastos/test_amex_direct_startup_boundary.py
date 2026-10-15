"""The deployed entrypoint must not create direct-source accounting tables."""

from devnous.gastos.amex_schema_policy import (
    AMEX_DIRECT_OWNER_MIGRATION_TABLES,
    runtime_managed_tables,
)
from devnous.gastos.models import Base


def test_real_direct_tables_are_registered_but_excluded_from_startup():
    registered = set(Base.metadata.tables)
    assert AMEX_DIRECT_OWNER_MIGRATION_TABLES <= registered
    runtime = {table.name for table in runtime_managed_tables(Base.metadata)}
    assert runtime.isdisjoint(AMEX_DIRECT_OWNER_MIGRATION_TABLES)
    assert {"expense_reports", "documentos", "accounting_polizas"} <= runtime
