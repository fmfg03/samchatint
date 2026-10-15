"""Owner-migrated evidence for direct AMEX; never provisioned by runtime."""

from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from devnous.copa_telmex.models import Base


def identifier():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid4)


def reference(table, nullable=False, unique=False):
    return Column(
        UUID(as_uuid=True),
        ForeignKey(table + ".id", ondelete="RESTRICT"),
        nullable=nullable,
        unique=unique,
    )


def timestamp():
    return Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AmexStatementSourceFile(Base):
    __tablename__ = "amex_statement_source_files"
    __table_args__ = (
        CheckConstraint("byte_size > 0 AND byte_size <= 10485760"),
        CheckConstraint("byte_size = octet_length(content)"),
        CheckConstraint("length(file_sha256) = 64"),
    )
    id = identifier()
    import_id = reference("amex_statement_imports", unique=True)
    content = Column(LargeBinary, nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False)
    filename = Column(String(200), nullable=False)
    mime_type = Column(String(100), nullable=False)
    actor_id = reference("empleados")
    created_at = timestamp()


class AmexStatementDirectContext(Base):
    __tablename__ = "amex_statement_direct_contexts"
    __table_args__ = (
        UniqueConstraint("import_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("currency = 'MXN'"),
    )
    id = identifier()
    import_id = reference("amex_statement_imports")
    version = Column(Integer, nullable=False)
    entity_code = Column(String(40), nullable=False)
    company_code = Column(String(20), nullable=False)
    receiver_rfc = Column(String(13), nullable=False)
    currency = Column(String(3), nullable=False)
    profile = Column(String(30), nullable=True)
    config_hash = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    actor_id = reference("empleados")
    reason = Column(Text, nullable=True)
    created_at = timestamp()


class AmexStatementDirectState(Base):
    __tablename__ = "amex_statement_direct_state"
    import_id = reference("amex_statement_imports")
    import_id.primary_key = True
    context_id = reference("amex_statement_direct_contexts", nullable=True)
    context_version = Column(Integer, nullable=True)
    source_version = Column(Integer, nullable=False, default=1)
    progress_json = Column(JSONB, nullable=True)
    updated_at = timestamp()


class AmexMerchantIdentityAlias(Base):
    __tablename__ = "amex_merchant_identity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_code", "card_account_id", "descriptor", "version"),
        CheckConstraint("version > 0"),
    )
    id = identifier()
    entity_code = Column(String(40), nullable=False)
    card_account_id = reference("amex_card_accounts")
    descriptor = Column(Text, nullable=False)
    issuer_rfc = Column(String(13), nullable=False)
    version = Column(Integer, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    actor_id = reference("empleados")
    reason = Column(Text, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_at = timestamp()


class AmexCFDIStatusReceipt(Base):
    __tablename__ = "amex_cfdi_status_receipts"
    id = identifier()
    cfdi_id = reference("cfdi_reports")
    fiscal_uuid = Column(String(36), nullable=False)
    issuer_rfc = Column(String(13), nullable=False)
    receiver_rfc = Column(String(13), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    xml_hash = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    observed_status = Column(String(30), nullable=False)
    response_json = Column(JSONB, nullable=False)
    response_hash = Column(String(64), nullable=False)
    actor_id = reference("empleados")
    observed_at = timestamp()


class AmexDirectItem(Base):
    __tablename__ = "amex_direct_items"
    __table_args__ = (
        CheckConstraint("source_version > 0"),
        CheckConstraint("movement_kind IN ('debit','credit','unknown','skipped')"),
        CheckConstraint(
            "(movement_kind='debit' AND imported_expense_id IS NOT NULL) OR "
            "(movement_kind!='debit' AND imported_expense_id IS NULL)"
        ),
    )
    id = identifier()
    import_id = reference("amex_statement_imports")
    import_item_id = reference("amex_statement_import_items", unique=True)
    imported_expense_id = reference("expense_reports", nullable=True, unique=True)
    movement_kind = Column(String(20), nullable=False)
    raw_signed_amount = Column(Numeric(18, 2), nullable=True)
    raw_amount = Column(Text, nullable=True)
    amount_column = Column(String(100), nullable=True)
    profile = Column(String(30), nullable=True)
    profile_version = Column(String(30), nullable=False, default="amex-direct-csv-v1")
    source_json = Column(JSONB, nullable=False)
    context_id = reference("amex_statement_direct_contexts", nullable=True)
    context_version = Column(Integer, nullable=True)
    source_version = Column(Integer, nullable=False, default=1)
    source_hash = Column(String(64), nullable=False)
    match_status = Column(String(30), nullable=False, default="pending")
    match_reason = Column(String(80), nullable=True)
    match_proof = Column(JSONB, nullable=True)
    cfdi_id = reference("cfdi_reports", nullable=True)
    status_receipt_id = reference("amex_cfdi_status_receipts", nullable=True)
    merchant_alias_id = reference("amex_merchant_identity_aliases", nullable=True)
    declared_uuid = Column(String(36), nullable=True)
    issuer_rfc = Column(String(13), nullable=True)
    tournament_id = reference("tournaments", nullable=True)
    project_code = Column(String(100), nullable=True)
    edition_year = Column(Integer, nullable=True)
    budget_concept_id = reference("budget_concepts", nullable=True)
    # These are SQL-owned budget tables; the owner migration adds real FKs.
    budget_line_id = Column(UUID(as_uuid=True), nullable=True)
    budget_version_id = Column(UUID(as_uuid=True), nullable=True)
    budget_assignment_id = Column(UUID(as_uuid=True), nullable=True)
    budget_status = Column(String(30), nullable=False, default="unclassified")
    accounting_status = Column(String(30), nullable=False, default="pending")
    credit_excluded = Column(Boolean, nullable=False, default=False)
    updated_at = timestamp()


class AmexDirectEvent(Base):
    __tablename__ = "amex_direct_events"
    id = identifier()
    item_id = reference("amex_direct_items")
    action = Column(String(40), nullable=False)
    actor_id = reference("empleados")
    source_version = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    payload_json = Column(JSONB, nullable=False)
    created_at = timestamp()


class AmexDirectAccountingCut(Base):
    __tablename__ = "amex_direct_accounting_cuts"
    id = identifier()
    import_id = reference("amex_statement_imports", unique=True)
    context_id = reference("amex_statement_direct_contexts")
    context_version = Column(Integer, nullable=False)
    source_version = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    accounting_poliza_id = reference("accounting_polizas", unique=True)
    accounting_date = Column(Date, nullable=False)
    actor_id = reference("empleados")
    snapshot_json = Column(JSONB, nullable=False)
    created_at = timestamp()


class AmexDirectCutItem(Base):
    __tablename__ = "amex_direct_cut_items"
    __table_args__ = (UniqueConstraint("cut_id", "direct_item_id"),)
    id = identifier()
    cut_id = reference("amex_direct_accounting_cuts")
    direct_item_id = reference("amex_direct_items", unique=True)
    import_item_id = reference("amex_statement_import_items")
    imported_expense_id = reference("expense_reports", nullable=True)
    consumption_id = reference("amex_recognition_consumptions", nullable=True)
    accounting_poliza_id = reference("accounting_polizas", nullable=True)
    coverage_kind = Column(String(30), nullable=False)
    source_hash = Column(String(64), nullable=False)
    snapshot_json = Column(JSONB, nullable=False)
