from pathlib import Path

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
