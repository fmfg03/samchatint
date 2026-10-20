"""Owner-migrated immutable bank coverage and historical-credit settlements."""

from sqlalchemy import CheckConstraint, Column, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB

from devnous.copa_telmex.models import Base

from .amex_direct_models import identifier, reference, timestamp


class AmexDirectCompletionReceipt(Base):
    __tablename__ = "amex_direct_completion_receipts"
    id = identifier()
    import_id = reference("amex_statement_imports", unique=True)
    context_id = reference("amex_statement_direct_contexts")
    context_version = Column(Integer, nullable=False)
    source_version = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    actor_id = reference("empleados")
    snapshot_json = Column(JSONB, nullable=False)
    created_at = timestamp()


class AmexDirectPriorCoverage(Base):
    __tablename__ = "amex_direct_prior_coverages"
    __table_args__ = (
        CheckConstraint("amount > 0 AND currency = 'MXN'"),
        CheckConstraint("(cut_id IS NULL) <> (completion_receipt_id IS NULL)"),
    )
    id = identifier()
    import_id = reference("amex_statement_imports")
    direct_item_id = reference("amex_direct_items", unique=True)
    imported_expense_id = reference("expense_reports", unique=True)
    consumption_id = reference("amex_recognition_consumptions", unique=True)
    representation_id = reference("amex_recognition_representations")
    original_cut_id = reference("amex_accounting_cuts", nullable=True)
    original_direct_cut_id = reference("amex_direct_accounting_cuts", nullable=True)
    accounting_poliza_id = reference("accounting_polizas")
    card_account_id = reference("amex_card_accounts")
    context_id = reference("amex_statement_direct_contexts")
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    cut_id = reference("amex_direct_accounting_cuts", nullable=True)
    completion_receipt_id = reference("amex_direct_completion_receipts", nullable=True)
    source_hash = Column(String(64), nullable=False)
    proof_json = Column(JSONB, nullable=False)
    actor_id = reference("empleados")
    created_at = timestamp()


class AmexExpenseAccountSettlement(Base):
    __tablename__ = "amex_expense_account_settlements"
    __table_args__ = (CheckConstraint("amount > 0 AND currency = 'MXN'"),)
    id = identifier()
    import_id = reference("amex_statement_imports")
    direct_item_id = reference("amex_direct_items", unique=True)
    imported_expense_id = reference("expense_reports", unique=True)
    context_id = reference("amex_statement_direct_contexts")
    card_account_id = reference("amex_card_accounts")
    cut_id = reference("amex_direct_accounting_cuts")
    accounting_poliza_id = reference("accounting_polizas")
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    source_hash = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    actor_id = reference("empleados")
    reason = Column(String(1000), nullable=False)
    proof_json = Column(JSONB, nullable=False)
    created_at = timestamp()


class AmexExpenseAccountSettlementAllocation(Base):
    __tablename__ = "amex_expense_account_settlement_allocations"
    __table_args__ = (CheckConstraint("amount > 0"),)
    id = identifier()
    settlement_id = reference("amex_expense_account_settlements")
    origin_poliza_id = reference("accounting_polizas")
    origin_line_id = reference("accounting_poliza_lines")
    origin_expense_id = reference("expense_reports")
    origin_document_id = reference("documentos")
    origin_cuenta_gastos_id = reference("cuentas_de_gastos")
    origin_account_id = reference("cuentas_contables")
    origin_account_code = Column(String(100), nullable=False)
    destination_debit_line_id = reference("accounting_poliza_lines", unique=True)
    destination_credit_line_id = reference("accounting_poliza_lines", unique=True)
    amount = Column(Numeric(18, 2), nullable=False)
    proof_json = Column(JSONB, nullable=False)
    created_at = timestamp()
