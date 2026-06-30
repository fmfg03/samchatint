"""
SQLAlchemy models for Copa Telmex Expense Management System.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    Float,
    JSON,
    BigInteger,
    Text,
    Integer,
    Numeric,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, deferred, declared_attr

# Use same Base as Copa Telmex models
from devnous.copa_telmex.models import Base
from devnous.gastos.expense_receipt_column import (
    configure_expense_receipt_blob_column_from_db,
    resolved_receipt_blob_column_for_orm,
)

configure_expense_receipt_blob_column_from_db()


class ExpenseReport(Base):
    """
    Expense reports from users (with or without receipts).
    Linked to Copa Telmex teams and players.
    """

    __tablename__ = "expense_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # User information (Telegram)
    telegram_user_id = Column(BigInteger, index=True)
    telegram_chat_id = Column(BigInteger, index=True)
    usuario_nombre = Column(String(200))
    usuario_username = Column(String(100))

    # Expense details
    proyecto = Column(String(200), nullable=False)  # Project name
    concepto = Column(
        Text, nullable=False
    )  # Concepto (transport, food, etc.) - renamed from categoria
    sub_cuenta = Column(String(10))  # Sub-account code (e.g., "001", "002")
    gasto_cantidad = Column(Float, nullable=False)  # Amount
    categorias = Column(JSONB, nullable=True)
    edicion = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=False, default="MXN")
    budget_concept_id = Column(
        UUID(as_uuid=True),
        ForeignKey("budget_concepts.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Expense type
    tipo_gasto = Column(String(50), default="ticket")  # "ticket", "manual"
    numero_referencia = Column(String(100), unique=True, index=True)
    numero_factura = Column(Text, nullable=True)

    # File information (for receipts)
    archivo_nombre = Column(String(500))
    archivo_path = Column(String(500))

    @declared_attr
    def archivo_data(cls):
        # Physical column may be archivo_data (canonical) or legacy "Archivos" — see expense_receipt_column.
        return Column(resolved_receipt_blob_column_for_orm(), Text)

    # CFDI/Tocino integration
    nova_request_id = Column(String(200), index=True)
    estado_factura = Column(
        String(50)
    )  # "pendiente", "en_proceso", "completada", "error"
    link_xml = Column(String(500))
    link_pdf = Column(String(500))
    mensaje_error = Column(Text)
    cfdi_use = Column(String(10))  # e.g., "G03"

    # Sender information
    nombre_enviador = Column(String(200))  # Name of the person sending the ticket
    departamento = Column(
        String(50)
    )  # Department (Mercadotecnia, Operaciones, Finanzas)
    fase_torneo = Column(
        String(50)
    )  # Tournament phase (Colectiva, Estatal, Nacional, Viaje de Campeones, No Aplica)
    metodo_pago = Column(
        String(50)
    )  # Payment method (Efectivo, Tarjeta Personal, Tarjeta de Empresa)
    ultimos_4_digitos = Column(
        String(4)
    )  # Last 4 digits of card (if payment method is a card)
    iva = Column(
        Float, nullable=True
    )  # IVA amount (NULL means no IVA, numeric value means IVA amount)
    hospedaje_entidad_fiscal = Column(String(120), nullable=True)
    hospedaje_tasa_impuesto = Column(Float, nullable=True)
    hospedaje_impuesto_monto = Column(Float, nullable=True)
    hospedaje_impuesto_confirmado = Column(Boolean, nullable=False, default=False)

    # Reimbursement tracking
    estado_reembolso = Column(
        String(50), default="pendiente"
    )  # "pendiente", "aprobado", "pagado"
    fecha_reembolso = Column(DateTime)
    cuenta_contable_base = Column(
        String(200)
    )  # Accounting account base (from tournament)

    # Link to Copa Telmex team/tournament (optional)
    team_id = Column(
        UUID(as_uuid=True), ForeignKey("copa_telmex_teams.id"), nullable=True
    )

    # Link to empleados table (optional, nullable for backward compatibility)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=True, index=True
    )

    # Link to documentos table (optional, nullable for backward compatibility)
    documento_id = Column(
        UUID(as_uuid=True), ForeignKey("documentos.id"), nullable=True, index=True
    )

    # Link to Solicitud documento (explicit link for UI display)
    solicitud_documento_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documentos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Link to Informe documento (explicit link for UI display)
    informe_documento_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documentos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Link to cuenta contable (accounting classification)
    cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contra_cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    retencion_cuentas_json = Column(JSONB, nullable=True)
    cuenta_iva_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # CFDI UUID-based matching
    cfdi_uuid_manual = Column(
        Text, nullable=True, index=True
    )  # UUID provided by user or extracted from QR/link
    cfdi_report_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cfdi_reports.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Cuenta de Gastos fields (LEAP_SPEC_REFERENCIAS_CLEANUP_V2)
    referencia_base = Column(
        Text, nullable=True, index=True
    )  # Base reference linking expense to Cuenta de Gastos
    cuenta_gastos_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_de_gastos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Cancellation tracking
    estado_gasto = Column(
        String(50), nullable=False, default="activo", index=True
    )  # 'activo' or 'cancelado'
    cancelado_en = Column(DateTime(timezone=True), nullable=True)
    cancelado_por_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=True, index=True
    )
    motivo_cancelacion = Column(Text, nullable=True)

    # Data source (e.g. 'amex_batch', 'solicitud_terceros', 'solicitud_personal')
    origen = Column(String(50), nullable=True, index=True)
    pagado_con_amex_empresa = Column(Boolean, nullable=True, default=None, index=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    team = relationship("Team", foreign_keys=[team_id])
    invoices = relationship("InvoiceReport", back_populates="expense")
    empleado = relationship(
        "Empleado", foreign_keys=[empleado_id], back_populates="gastos", lazy="selectin"
    )
    # Note: documento relationship uses explicit foreign_keys to disambiguate from solicitud_documento, informe_documento, and gasto_generado
    documento = relationship(
        "Documento",
        foreign_keys=[documento_id],
        back_populates="gastos",
        lazy="selectin",
    )
    # Explicit relationships for Solicitud and Informe documentos
    solicitud_documento = relationship(
        "Documento", foreign_keys=[solicitud_documento_id], lazy="selectin"
    )
    informe_documento = relationship(
        "Documento", foreign_keys=[informe_documento_id], lazy="selectin"
    )
    cancelado_por = relationship(
        "Empleado", foreign_keys=[cancelado_por_id], lazy="selectin"
    )
    budget_concept = relationship(
        "BudgetConcept", foreign_keys=[budget_concept_id], lazy="selectin"
    )
    cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[cuenta_contable_id], lazy="selectin"
    )
    contra_cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[contra_cuenta_contable_id], lazy="selectin"
    )
    cuenta_iva = relationship(
        "CuentaContable", foreign_keys=[cuenta_iva_id], lazy="selectin"
    )
    # CFDI relationship - explicit foreign_keys to prevent mapper errors
    cfdi_report = relationship(
        "CFDIReport", foreign_keys=[cfdi_report_id], lazy="selectin"
    )
    # Cuenta de Gastos relationship
    cuenta_gastos = relationship(
        "CuentaDeGastos",
        foreign_keys=[cuenta_gastos_id],
        back_populates="gastos",
        lazy="selectin",
    )
    # Multi-file attachments (canonical 1:N); legacy receipt remains archivo_data for Tocino.
    adjuntos = relationship(
        "Adjunto",
        back_populates="gasto",
        foreign_keys="Adjunto.gasto_id",
        # Keep attachment loading explicit through schema-safe helpers.
        # Auto-selectin here issues full ORM loads against `adjuntos`, which
        # breaks on environments that have not yet applied the metadata-column
        # migration.
        lazy="select",
    )

    def __repr__(self):
        return f"<ExpenseReport(id={self.id}, proyecto='{self.proyecto}', cantidad={self.gasto_cantidad})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "telegram_user_id": self.telegram_user_id,
            "proyecto": self.proyecto,
            "concepto": self.concepto,
            "sub_cuenta": self.sub_cuenta,
            "gasto_cantidad": self.gasto_cantidad,
            "categorias": self.categorias,
            "edicion": self.edicion,
            "currency": self.currency or "MXN",
            "budget_concept_id": (
                str(self.budget_concept_id) if self.budget_concept_id else None
            ),
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "tipo_gasto": self.tipo_gasto,
            "numero_referencia": self.numero_referencia,
            "numero_factura": self.numero_factura,
            "estado_factura": self.estado_factura,
            "estado_reembolso": self.estado_reembolso,
            "cuenta_contable_base": self.cuenta_contable_base,
            "nombre_enviador": self.nombre_enviador,
            "departamento": self.departamento,
            "fase_torneo": self.fase_torneo,
            "metodo_pago": self.metodo_pago,
            "ultimos_4_digitos": self.ultimos_4_digitos,
            "iva": self.iva,
            "hospedaje_entidad_fiscal": self.hospedaje_entidad_fiscal,
            "hospedaje_tasa_impuesto": self.hospedaje_tasa_impuesto,
            "hospedaje_impuesto_monto": self.hospedaje_impuesto_monto,
            "hospedaje_impuesto_confirmado": self.hospedaje_impuesto_confirmado,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "referencia_base": self.referencia_base,
            "cuenta_gastos_id": (
                str(self.cuenta_gastos_id) if self.cuenta_gastos_id else None
            ),
            "origen": self.origen,
            "pagado_con_amex_empresa": self.pagado_con_amex_empresa,
            "contra_cuenta_contable_id": (
                str(self.contra_cuenta_contable_id)
                if self.contra_cuenta_contable_id
                else None
            ),
            "retencion_cuentas_json": self.retencion_cuentas_json,
            "cuenta_iva_id": str(self.cuenta_iva_id) if self.cuenta_iva_id else None,
        }


class InvoiceReport(Base):
    """
    Invoice/CFDI generation data from Tocino AI.
    Tracks the status of invoice generation for expense reports.
    """

    __tablename__ = "invoice_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    expense_id = Column(
        UUID(as_uuid=True), ForeignKey("expense_reports.id"), index=True
    )

    # Tocino AI information
    nova_request_id = Column(String(200), unique=True, index=True)
    tocino_request_id = Column(String(200))

    # Invoice status
    estado_factura = Column(
        String(50), default="pendiente"
    )  # "pendiente", "en_proceso", "completada", "error"

    # Download links
    link_xml = Column(String(500))
    link_pdf = Column(String(500))

    # Error handling
    mensaje_error = Column(Text)

    # Full webhook payload from Tocino (stores complete export data)
    webhook_payload = Column(JSONB)

    # Metadata
    fecha = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    expense = relationship("ExpenseReport", back_populates="invoices")

    def __repr__(self):
        return f"<InvoiceReport(id={self.id}, nova_request_id='{self.nova_request_id}', estado='{self.estado_factura}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "expense_id": str(self.expense_id),
            "nova_request_id": self.nova_request_id,
            "estado_factura": self.estado_factura,
            "link_xml": self.link_xml,
            "link_pdf": self.link_pdf,
            "mensaje_error": self.mensaje_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CFDIReport(Base):
    """
    Complete CFDI XML data extracted from invoice XML files.

    Separate table to store all CFDI fields for detailed analysis and reporting.
    Can be joined with invoice_reports and expense_reports via nova_request_id.
    """

    __tablename__ = "cfdi_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Join keys
    nova_request_id = Column(
        String(200), index=True
    )  # Links to invoice_reports and expense_reports
    numero_referencia = Column(String(100), index=True)  # Links to expense_reports

    # Comprobante fields
    version = Column(String(10))  # "4.0"
    serie = Column(String(50))  # "KKGH"
    folio = Column(String(50))  # "3827"
    fecha = Column(DateTime)  # "2025-11-03T19:54:52"
    sello = Column(Text)  # Long seal string
    forma_pago = Column(
        Text
    )  # "01" or "04 - Tarjeta de crédito" (widened from String(20) in v1.0.16)
    no_certificado = Column(String(50))  # "00001000000515628535"
    certificado = Column(Text)  # Long certificate string
    subtotal = Column(Float)  # 333.31
    descuento = Column(Float)  # 179.00
    moneda = Column(String(10))  # "MXN"
    tipo_cambio = Column(Float)  # Tipo de cambio (ej: 1.0 para MXN)
    total = Column(Float)  # 179.00
    tipo_de_comprobante = Column(String(10))  # "I"
    metodo_pago = Column(
        Text
    )  # "PUE" or "PUE - Pago en Una sola Exhibición" (widened from String(20) in v1.0.16)
    lugar_expedicion = Column(Text)  # "11000" (widened from String(20) in v1.0.16)
    exportacion = Column(String(10))  # "01"

    # Emisor fields
    emisor_rfc = Column(String(20))  # "KKM0304101S1"
    emisor_nombre = Column(String(500))  # "KRISPY KREME MEXICO"
    emisor_regimen_fiscal = Column(Text)  # "601" (widened from String(20) in v1.0.16)

    # Receptor fields
    receptor_rfc = Column(String(20))  # "RFC123456789"
    receptor_nombre = Column(String(500))  # "JUAN PEREZ GARCIA"
    receptor_uso_cfdi = Column(Text)  # "G03" (widened from String(10) in v1.0.16)
    receptor_domicilio_fiscal = Column(
        Text
    )  # "12345" (widened from String(20) in v1.0.16)
    receptor_regimen_fiscal = Column(Text)  # "626" (widened from String(20) in v1.0.16)

    # TimbreFiscalDigital fields
    timbre_version = Column(String(10))  # "1.1"
    cfdi_uuid = Column(
        String(100), unique=True, index=True
    )  # "C027C9F4-92CF-4190-BB89-3E76AB2ECA70"
    fecha_timbrado = Column(DateTime)  # "2025-11-03T19:54:56"
    rfc_prov_certif = Column(String(20))  # "SST060807KU0"
    sello_cfd = Column(Text)  # Long seal
    no_certificado_sat = Column(String(50))  # "00001000000711914678"
    sello_sat = Column(Text)  # Long SAT seal

    # Impuestos summary
    total_impuestos_trasladados = Column(Float)  # 24.69

    # Conceptos (stored as JSONB for flexibility - can have multiple)
    conceptos = Column(JSONB)

    # Descripción del concepto principal (primer concepto)
    descripcion_concepto_principal = Column(Text)  # "HONORARIOS"

    # Impuestos detallados (stored as JSONB - contiene traslados y retenciones)
    impuestos_detalle = Column(JSONB)

    # Raw XML (optional, for reference)
    xml_raw = Column(Text)

    # Processing metadata
    xml_parsed = Column(Boolean, default=False)
    parsed_at = Column(DateTime)

    # Data source tracking
    origen = Column(
        String(50), nullable=True, index=True
    )  # 'tocino' or 'csv' - tracks data source

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # Note: Optional backref to expenses linked via cfdi_report_id
    # Using explicit foreign_keys to prevent mapper errors
    # Note: Using lambda for forward reference since ExpenseReport is defined earlier in the file
    expenses = relationship(
        "ExpenseReport",
        foreign_keys=lambda: [ExpenseReport.cfdi_report_id],
        back_populates="cfdi_report",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<CFDIReport(id={self.id}, cfdi_uuid='{self.cfdi_uuid}', nova_request_id='{self.nova_request_id}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "nova_request_id": self.nova_request_id,
            "numero_referencia": self.numero_referencia,
            "cfdi_uuid": self.cfdi_uuid,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "emisor_rfc": self.emisor_rfc,
            "emisor_nombre": self.emisor_nombre,
            "receptor_rfc": self.receptor_rfc,
            "total": self.total,
            "tipo_cambio": self.tipo_cambio,
            "descripcion_concepto_principal": self.descripcion_concepto_principal,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AccountingImportRun(Base):
    """Audit trail for accounting source imports."""

    __tablename__ = "accounting_import_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_type = Column(
        String(50), nullable=False, index=True
    )  # balanza, rfc, coi, auxiliar, banco
    filename = Column(String(255), nullable=False)
    source_sha256 = Column(String(64), nullable=True, index=True)
    mode = Column(String(20), nullable=False, default="apply")  # dry_run, apply
    status = Column(
        String(20), nullable=False, default="completed"
    )  # completed, failed
    started_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    summary_json = Column(JSONB, nullable=True)
    error_text = Column(Text, nullable=True)

    started_by = relationship(
        "Empleado", foreign_keys=[started_by_empleado_id], lazy="selectin"
    )


class AccountingPoliza(Base):
    """Imported accounting journal header from COI workbook."""

    __tablename__ = "accounting_polizas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    import_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_import_runs.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    source_file = Column(String(255), nullable=False)
    source_sheet = Column(String(120), nullable=True)
    source_row_start = Column(Integer, nullable=True)
    tipo_poliza = Column(String(20), nullable=False, index=True)
    numero_poliza = Column(String(50), nullable=False, index=True)
    fecha_poliza = Column(DateTime, nullable=True, index=True)
    beneficiario_nombre = Column(String(500), nullable=True, index=True)
    concepto = Column(Text, nullable=False)
    concepto_resumen = Column(Text, nullable=True, index=True)
    line_count_declared = Column(Integer, nullable=True)
    line_count_actual = Column(Integer, nullable=True)
    cfdi_uuid = Column(String(100), nullable=True, index=True)
    cfdi_report_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cfdi_reports.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    origen = Column(String(50), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    import_run = relationship(
        "AccountingImportRun", foreign_keys=[import_run_id], lazy="selectin"
    )
    cfdi_report = relationship(
        "CFDIReport", foreign_keys=[cfdi_report_id], lazy="selectin"
    )
    lines = relationship(
        "AccountingPolizaLine",
        back_populates="poliza",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class AccountingPolizaLine(Base):
    """Imported accounting journal line from COI workbook."""

    __tablename__ = "accounting_poliza_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    poliza_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounting_polizas.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_no = Column(Integer, nullable=False)
    cuenta_codigo = Column(String(100), nullable=False, index=True)
    cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    concepto = Column(Text, nullable=True)
    movimiento_no = Column(String(20), nullable=True)
    debe = Column(Float, nullable=True)
    haber = Column(Float, nullable=True)
    raw_row_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    poliza = relationship("AccountingPoliza", back_populates="lines", lazy="selectin")
    cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[cuenta_contable_id], lazy="selectin"
    )


class AccountingClosePeriod(Base):
    """Monthly accounting close status for manual lock/cutoff control."""

    __tablename__ = "accounting_close_periods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    fiscal_year = Column(Integer, nullable=False, index=True)
    fiscal_month = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="open", index=True)
    notes = Column(Text, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reopened_at = Column(DateTime(timezone=True), nullable=True)
    reopened_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    closed_by = relationship(
        "Empleado", foreign_keys=[closed_by_empleado_id], lazy="selectin"
    )
    reopened_by = relationship(
        "Empleado", foreign_keys=[reopened_by_empleado_id], lazy="selectin"
    )


class AccountingAuditLog(Base):
    """Audit trail for manual accounting actions and period close events."""

    __tablename__ = "accounting_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    poliza_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounting_polizas.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    poliza_line_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_poliza_lines.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    close_period_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_close_periods.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    entity_type = Column(String(50), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    before_state = Column(JSONB, nullable=True)
    after_state = Column(JSONB, nullable=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    empleado = relationship("Empleado", foreign_keys=[empleado_id], lazy="selectin")
    poliza = relationship("AccountingPoliza", foreign_keys=[poliza_id], lazy="selectin")
    poliza_line = relationship(
        "AccountingPolizaLine", foreign_keys=[poliza_line_id], lazy="selectin"
    )
    close_period = relationship(
        "AccountingClosePeriod", foreign_keys=[close_period_id], lazy="selectin"
    )


class AccountingCloseChecklistItem(Base):
    """Checklist item tracked per monthly close period."""

    __tablename__ = "accounting_close_checklist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    close_period_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_close_periods.id", onupdate="CASCADE", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    task_code = Column(String(80), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    owner_role = Column(String(80), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    notes = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    close_period = relationship(
        "AccountingClosePeriod", foreign_keys=[close_period_id], lazy="selectin"
    )
    completed_by = relationship(
        "Empleado", foreign_keys=[completed_by_empleado_id], lazy="selectin"
    )


class AuxLedgerEntry(Base):
    """Imported auxiliary ledger entry from COI/Aux workbook."""

    __tablename__ = "aux_ledger_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    import_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_import_runs.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    source_file = Column(String(255), nullable=False)
    source_sheet = Column(String(120), nullable=True)
    source_row_number = Column(Integer, nullable=False)
    cuenta_codigo = Column(String(100), nullable=False, index=True)
    cuenta_nombre = Column(String(500), nullable=True)
    cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tipo_poliza = Column(String(20), nullable=True, index=True)
    numero_poliza = Column(String(50), nullable=True, index=True)
    fecha = Column(DateTime, nullable=True, index=True)
    concepto = Column(Text, nullable=True)
    saldo_inicial = Column(Float, nullable=True)
    debe = Column(Float, nullable=True)
    haber = Column(Float, nullable=True)
    saldo = Column(Float, nullable=True)
    cfdi_uuid = Column(String(100), nullable=True, index=True)
    related_poliza_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounting_polizas.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    raw_row_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    import_run = relationship(
        "AccountingImportRun", foreign_keys=[import_run_id], lazy="selectin"
    )
    cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[cuenta_contable_id], lazy="selectin"
    )
    related_poliza = relationship(
        "AccountingPoliza", foreign_keys=[related_poliza_id], lazy="selectin"
    )


class BankMovement(Base):
    """Imported bank statement movement."""

    __tablename__ = "bank_movements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    import_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounting_import_runs.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    source_file = Column(String(255), nullable=False)
    source_row_number = Column(Integer, nullable=False)
    cuenta_bancaria = Column(String(100), nullable=True, index=True)
    fecha = Column(DateTime, nullable=True, index=True)
    hora = Column(String(10), nullable=True)
    sucursal = Column(String(20), nullable=True)
    descripcion = Column(String(255), nullable=True)
    signo = Column(String(1), nullable=True, index=True)
    importe = Column(Float, nullable=True, index=True)
    saldo = Column(Float, nullable=True)
    referencia_bancaria = Column(String(100), nullable=True, index=True)
    concepto_banco = Column(Text, nullable=True)
    banco_participante = Column(String(255), nullable=True)
    clabe_beneficiario = Column(String(32), nullable=True, index=True)
    nombre_beneficiario = Column(String(255), nullable=True, index=True)
    cuenta_ordenante = Column(String(100), nullable=True)
    nombre_ordenante = Column(String(255), nullable=True)
    codigo_devolucion = Column(String(50), nullable=True)
    causa_devolucion = Column(String(255), nullable=True)
    rfc_beneficiario = Column(String(20), nullable=True, index=True)
    rfc_ordenante = Column(String(20), nullable=True, index=True)
    clave_rastreo = Column(String(120), nullable=True, index=True)
    descripcion_larga = Column(Text, nullable=True)
    proveedor_cliente_id = Column(
        UUID(as_uuid=True),
        ForeignKey("proveedores_clientes.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_aux_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("aux_ledger_entries.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    related_poliza_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounting_polizas.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_expense_id = Column(
        UUID(as_uuid=True),
        ForeignKey("expense_reports.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conciliacion_estado = Column(
        String(20), nullable=False, default="unmatched", index=True
    )
    raw_row_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    import_run = relationship(
        "AccountingImportRun", foreign_keys=[import_run_id], lazy="selectin"
    )
    proveedor_cliente = relationship(
        "ProveedorCliente", foreign_keys=[proveedor_cliente_id], lazy="selectin"
    )
    matched_aux_entry = relationship(
        "AuxLedgerEntry", foreign_keys=[matched_aux_entry_id], lazy="selectin"
    )
    related_poliza = relationship(
        "AccountingPoliza", foreign_keys=[related_poliza_id], lazy="selectin"
    )
    matched_expense = relationship(
        "ExpenseReport", foreign_keys=[matched_expense_id], lazy="selectin"
    )


class ReconciliationAuditLog(Base):
    """Audit log for manual reconciliation actions on bank movements."""

    __tablename__ = "reconciliation_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    bank_movement_id = Column(
        UUID(as_uuid=True),
        ForeignKey("bank_movements.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = Column(String(50), nullable=False, index=True)
    before_state = Column(JSONB, nullable=True)
    after_state = Column(JSONB, nullable=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    bank_movement = relationship(
        "BankMovement", foreign_keys=[bank_movement_id], lazy="selectin"
    )
    empleado = relationship("Empleado", foreign_keys=[empleado_id], lazy="selectin")


class Tournament(Base):
    """Tournament/Project configuration model."""

    __tablename__ = "tournaments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(200), nullable=False, unique=True, index=True)
    description = Column(String(500))
    active = Column(Boolean, default=True, nullable=False, index=True)
    display_order = Column(Integer, default=0)  # For ordering in lists
    cuenta_contable_relacionada = Column(
        String(200)
    )  # Accounting account related to this tournament
    etapas = Column(
        JSONB, nullable=True
    )  # Tournament phase names (e.g. ["Colectiva","Estatal","Nacional"]). NULL = use default list.
    categorias = Column(
        JSONB, nullable=True
    )  # Optional category labels (one per line in admin forms).
    form_visibility_areas = Column(
        JSONB, nullable=True
    )  # Departamentos (Finanzas, Operaciones, …); NULL/empty = todos.

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Tournament(id={self.id}, name='{self.name}', active={self.active})>"

    def to_dict(self):
        """Convert to dictionary."""
        etapas_val = getattr(self, "etapas", None)
        if etapas_val is not None and not isinstance(etapas_val, list):
            etapas_val = list(etapas_val) if etapas_val else None
        categorias_val = getattr(self, "categorias", None)
        if categorias_val is not None and not isinstance(categorias_val, list):
            categorias_val = list(categorias_val) if categorias_val else None
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "display_order": self.display_order,
            "cuenta_contable_relacionada": self.cuenta_contable_relacionada,
            "etapas": etapas_val,
            "categorias": categorias_val,
            "form_visibility_areas": getattr(self, "form_visibility_areas", None),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    # Relationship to concepto mappings
    concepto_mappings = relationship(
        "TournamentConceptoMapping",
        back_populates="tournament",
        cascade="all, delete-orphan",
    )


class TournamentOperationsLink(Base):
    """Optional link from a gastos project to an operations tournament record."""

    __tablename__ = "tournament_operations_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tournament_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tournaments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    operations_tournament_id = Column(String(64), nullable=False, index=True)
    operations_tournament_slug = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BudgetConcept(Base):
    """Canonical budget concept catalog scoped to a tournament/program."""

    __tablename__ = "budget_concepts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tournament_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tournaments.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tournament_code = Column(String(40), nullable=True, index=True)
    tournament_name = Column(String(200), nullable=False)
    concept_name = Column(String(200), nullable=False)
    concept_key = Column(String(200), nullable=False, index=True)
    active = Column(Boolean, default=True, nullable=False, index=True)
    source = Column(String(80), nullable=False, default="manual")
    metadata_json = Column("metadata", JSONB, nullable=False, default=dict)
    cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tournament = relationship("Tournament", foreign_keys=[tournament_id], lazy="selectin")
    cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[cuenta_contable_id], lazy="selectin"
    )
    created_by_empleado = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "tournament_id": str(self.tournament_id) if self.tournament_id else None,
            "tournament_code": self.tournament_code,
            "tournament_name": self.tournament_name,
            "concept_name": self.concept_name,
            "concept_key": self.concept_key,
            "active": bool(self.active),
            "source": self.source,
            "metadata": self.metadata_json or {},
            "cuenta_contable_id": (
                str(self.cuenta_contable_id) if self.cuenta_contable_id else None
            ),
            "created_by_empleado_id": (
                str(self.created_by_empleado_id)
                if self.created_by_empleado_id
                else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TournamentConceptoMapping(Base):
    """Tournament-specific concepto to sub-cuenta mapping."""

    __tablename__ = "tournament_concepto_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tournament_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tournaments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cuenta_contable = Column(
        String(50), nullable=False
    )  # Full account code (e.g., "5300-010-001")
    concepto = Column(
        String(200), nullable=False
    )  # Concepto name (e.g., "GASTOS DE VIAJE TRANSPORTE")
    telegram_display_text = Column(
        String(200)
    )  # Optional display text for Telegram buttons
    sub_cuenta = Column(String(10))  # Extracted from last 3 digits of cuenta_contable
    parent_account = Column(String(20))  # Extracted from first part (e.g., "5300-010")
    active = Column(Boolean, default=True, nullable=False, index=True)
    display_order = Column(Integer, default=0)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    tournament = relationship("Tournament", back_populates="concepto_mappings")

    def __repr__(self):
        return f"<TournamentConceptoMapping(id={self.id}, tournament_id={self.tournament_id}, concepto='{self.concepto}', sub_cuenta='{self.sub_cuenta}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "tournament_id": str(self.tournament_id),
            "cuenta_contable": self.cuenta_contable,
            "concepto": self.concepto,
            "telegram_display_text": self.telegram_display_text or self.concepto,
            "sub_cuenta": self.sub_cuenta,
            "parent_account": self.parent_account,
            "active": self.active,
            "display_order": self.display_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def parse_cuenta_contable(cuenta_contable: str) -> dict:
        """
        Parse cuenta_contable to extract sub_cuenta and parent_account.

        Args:
            cuenta_contable: Full account code (e.g., "5300-010-001")

        Returns:
            dict with 'sub_cuenta' and 'parent_account' keys
        """
        if not cuenta_contable:
            return {"sub_cuenta": None, "parent_account": None}

        # Split by hyphen
        parts = cuenta_contable.split("-")

        if len(parts) < 2:
            # Try to extract last 3 digits if no hyphens
            if len(cuenta_contable) >= 3:
                return {
                    "sub_cuenta": cuenta_contable[-3:],
                    "parent_account": cuenta_contable[:-3],
                }
            return {"sub_cuenta": None, "parent_account": None}

        # Last part is sub_cuenta (last 3 digits)
        sub_cuenta = parts[-1]
        # Everything before last hyphen is parent_account
        parent_account = "-".join(parts[:-1])

        return {"sub_cuenta": sub_cuenta, "parent_account": parent_account}


class RFCConfig(Base):
    """RFC configuration model for Tocino payload data."""

    __tablename__ = "rfc_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(
        String(200), nullable=False, unique=True, index=True
    )  # Display name for the RFC
    active = Column(Boolean, default=True, nullable=False, index=True)
    display_order = Column(Integer, default=0)  # For ordering in lists

    # RFC/Taxpayer information
    tax_id = Column(String(50), nullable=False)  # RFC identifier (e.g., "RFC123456789")
    taxpayer = Column(
        String(500), nullable=False
    )  # Full taxpayer name (e.g., "JUAN PEREZ GARCIA")
    taxpayer_name = Column(String(200))  # First name
    taxpayer_last_name = Column(String(200))  # Last name
    taxpayer_second_last_name = Column(String(200))  # Second last name

    # Address information
    street_address_1 = Column(String(500))
    ext_num = Column(String(50))  # External number
    int_num = Column(String(50))  # Internal number (optional)
    street_address_2 = Column(String(500))  # Additional address (optional)
    city = Column(String(200))
    state = Column(String(200))
    country = Column(String(200), default="México")
    postal_code = Column(String(50))

    # Fiscal information
    invoice_fiscal_regimen = Column(String(50))  # e.g., "626"

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<RFCConfig(id={self.id}, name='{self.name}', tax_id='{self.tax_id}', active={self.active})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "name": self.name,
            "active": self.active,
            "display_order": self.display_order,
            "tax_id": self.tax_id,
            "taxpayer": self.taxpayer,
            "taxpayer_name": self.taxpayer_name,
            "taxpayer_last_name": self.taxpayer_last_name,
            "taxpayer_second_last_name": self.taxpayer_second_last_name,
            "street_address_1": self.street_address_1,
            "ext_num": self.ext_num,
            "int_num": self.int_num,
            "street_address_2": self.street_address_2,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "postal_code": self.postal_code,
            "invoice_fiscal_regimen": self.invoice_fiscal_regimen,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Empleado(Base):
    """
    Employee model representing people who can submit expenses, get advances, and appear in approvals.
    """

    __tablename__ = "empleados"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre = Column(String(200), nullable=False)
    correo = Column(String(200), unique=True, index=True)
    telefono = Column(String(50), nullable=True)
    telegram_user_id = Column(BigInteger, nullable=True, index=True)
    departamento = Column(String(100), nullable=True)
    proyecto_predeterminado = Column(
        String(200), nullable=True
    )  # For now nullable string, may become FK later
    centro_costo_predeterminado = Column(
        String(200), nullable=True
    )  # For now nullable string, may become FK later
    rol = Column(
        String(50), nullable=False, default="empleado"
    )  # 'empleado', 'coordinador', 'finanzas', 'admin'
    activo = Column(Boolean, default=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=True)  # Password hash for web authentication
    aprobador_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=True, index=True
    )

    # Metadata
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    # Note: foreign_keys specified to disambiguate which FK to use
    # This is needed because ExpenseReport has two FKs to empleados (empleado_id and cancelado_por_id)
    # For reverse relationships, we use primaryjoin to specify which FK to use
    gastos = relationship(
        "ExpenseReport",
        primaryjoin="Empleado.id == ExpenseReport.empleado_id",
        foreign_keys=[ExpenseReport.empleado_id],
        back_populates="empleado",
    )
    documentos = relationship(
        "Documento",
        primaryjoin="Empleado.id == Documento.empleado_id",
        foreign_keys=lambda: [Documento.empleado_id],
        back_populates="empleado",
    )
    # Reverse relationship for personal solicitudes where this empleado is the beneficiary
    solicitudes_personales_recibidas = relationship(
        "Documento",
        primaryjoin="Empleado.id == Documento.beneficiario_empleado_id",
        foreign_keys=lambda: [Documento.beneficiario_empleado_id],
        lazy="selectin",
    )
    aprobador = relationship(
        "Empleado",
        remote_side=[id],
        back_populates="subordinados",
        lazy="selectin",
    )
    subordinados = relationship(
        "Empleado",
        back_populates="aprobador",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Empleado(id={self.id}, nombre='{self.nombre}', correo='{self.correo}', activo={self.activo})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "nombre": self.nombre,
            "correo": self.correo,
            "telefono": self.telefono,
            "telegram_user_id": (
                str(self.telegram_user_id) if self.telegram_user_id else None
            ),
            "departamento": self.departamento,
            "proyecto_predeterminado": self.proyecto_predeterminado,
            "centro_costo_predeterminado": self.centro_costo_predeterminado,
            "rol": self.rol,
            "activo": self.activo,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "actualizado_en": (
                self.actualizado_en.isoformat() if self.actualizado_en else None
            ),
        }


class CuentaContable(Base):
    """
    Chart of accounts - central accounting account definitions.
    """

    __tablename__ = "cuentas_contables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo = Column(String(100), nullable=False, unique=True, index=True)
    nombre = Column(String(500), nullable=False)
    tipo = Column(
        String(100), nullable=False
    )  # 'gasto', 'proveedor', 'anticipo', 'iva', 'retencion', 'banco', etc.
    activo = Column(Boolean, default=True, nullable=False, index=True)

    # Metadata
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self):
        return f"<CuentaContable(id={self.id}, codigo='{self.codigo}', nombre='{self.nombre}', tipo='{self.tipo}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "codigo": self.codigo,
            "nombre": self.nombre,
            "tipo": self.tipo,
            "activo": self.activo,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "actualizado_en": (
                self.actualizado_en.isoformat() if self.actualizado_en else None
            ),
        }


class CentroDeCosto(Base):
    """
    Cost center dimension - optional cost center for expense tracking.
    """

    __tablename__ = "centros_de_costo"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre = Column(String(200), nullable=False)
    codigo = Column(String(100), nullable=False, index=True)
    activo = Column(Boolean, default=True, nullable=False, index=True)

    # Metadata
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self):
        return f"<CentroDeCosto(id={self.id}, nombre='{self.nombre}', codigo='{self.codigo}', activo={self.activo})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "nombre": self.nombre,
            "codigo": self.codigo,
            "activo": self.activo,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "actualizado_en": (
                self.actualizado_en.isoformat() if self.actualizado_en else None
            ),
        }


class ProveedorCliente(Base):
    """
    Suppliers and clients model - tracks vendors and customers for SOLICITUD documents.

    IMPORTANT: cuenta_bancaria stores bank account numbers (CLABE/account number) as plain text.
    This is NOT related to cuentas_contables (accounting ledger codes).
    - cuenta_bancaria: Where money is sent (banking info)
    - cuenta_contable: How money is classified in accounting (NOT stored here)
    """

    __tablename__ = "proveedores_clientes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo = Column(
        String(50), nullable=False
    )  # 'proveedor', 'cliente', 'operadores_regionales'
    nombre = Column(Text, nullable=False)
    rfc = Column(Text, nullable=True)
    banco = Column(Text, nullable=True)
    cuenta_clabe = Column(Text, nullable=True)
    # Bank account number (NOT accounting code) - stored as plain text, no FK
    cuenta_bancaria = Column(Text, nullable=True, index=True)
    entidad_region = Column(Text, nullable=True)
    activo = Column(Boolean, default=True, nullable=False, index=True)

    # Metadata
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    documentos = relationship("Documento", back_populates="proveedor_cliente")

    def __repr__(self):
        return f"<ProveedorCliente(id={self.id}, tipo='{self.tipo}', nombre='{self.nombre}', activo={self.activo})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "tipo": self.tipo,
            "nombre": self.nombre,
            "rfc": self.rfc,
            "banco": self.banco,
            "cuenta_clabe": self.cuenta_clabe,
            "cuenta_bancaria": self.cuenta_bancaria,
            "entidad_region": self.entidad_region,
            "activo": self.activo,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "actualizado_en": (
                self.actualizado_en.isoformat() if self.actualizado_en else None
            ),
        }


class Documento(Base):
    """
    Document model for grouping expenses under a single reference number.
    Can be an INFORME (expense report) or SOLICITUD (request/advance request).
    """

    __tablename__ = "documentos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    tipo = Column(String(50), nullable=False)  # 'INFORME' or 'SOLICITUD'
    numero_referencia = Column(String(200), nullable=False, index=True)
    estado = Column(
        String(50), nullable=False, index=True
    )  # 'borrador', 'enviado', 'aprobado', 'pagado', 'cerrado'
    fecha_inicio = Column(DateTime(timezone=True), nullable=True)
    fecha_fin = Column(DateTime(timezone=True), nullable=True)
    monto_solicitado = Column(Numeric, nullable=True)
    monto_total = Column(Numeric, nullable=True)
    categorias = Column(JSONB, nullable=True)
    edicion = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=False, default="MXN")
    budget_concept_id = Column(
        UUID(as_uuid=True),
        ForeignKey("budget_concepts.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    torneo_id = Column(
        UUID(as_uuid=True), ForeignKey("tournaments.id"), nullable=True, index=True
    )
    fase = deferred(Column(String(200), nullable=True, index=True))
    proyecto_otro = Column(Text, nullable=True)
    proveedor_cliente_id = Column(
        UUID(as_uuid=True),
        ForeignKey("proveedores_clientes.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    beneficiario_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    enviado_en = Column(DateTime(timezone=True), nullable=True)
    aprobado_en = Column(DateTime(timezone=True), nullable=True)
    pagado_en = Column(DateTime(timezone=True), nullable=True)
    # SOLICITUD-specific payment fields
    fecha_pago = Column(Date, nullable=True)  # Payment date for SOLICITUD
    pago_urgente = Column(Boolean, default=False, nullable=False, index=True)
    concepto_pago = Column(
        Text, nullable=True
    )  # Payment concept (distinct from expense concept)
    numero_factura = Column(Text, nullable=True)  # Invoice number (optional)
    referencia_pago = Column(
        Text, nullable=True
    )  # Payment reference (bank reference, not business reference)
    metodo_pago = Column(Text, nullable=True)  # Payment method (e.g., "TRANSFERENCIA")
    # Track if expense was generated from this SOLICITUD
    gasto_generado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("expense_reports.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Cuenta de Gastos fields (LEAP_SPEC_REFERENCIAS_CLEANUP_V2)
    referencia_base = Column(
        Text, nullable=True, index=True
    )  # Base reference linking Solicitud + Informe pairs
    referencia_operaciones = Column(
        Text, nullable=True
    )  # Global ops counter (stringified integer), system-assigned; shared by informe + its transfer solicitudes
    cuenta_gastos_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_de_gastos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # CFDI capture at solicitud time (canonical UUID; linked to CFDIReport when available)
    cfdi_uuid_manual = Column(Text, nullable=True, index=True)
    cfdi_compartido_confirmado = Column(Boolean, default=False, nullable=False)
    cfdi_report_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cfdi_reports.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    # Note: empleado relationship uses explicit foreign_keys to disambiguate from beneficiario_empleado
    empleado = relationship(
        "Empleado", foreign_keys=[empleado_id], back_populates="documentos"
    )
    beneficiario_empleado = relationship(
        "Empleado",
        foreign_keys=[beneficiario_empleado_id],
        lazy="selectin",
        overlaps="solicitudes_personales_recibidas",
    )
    torneo = relationship("Tournament", foreign_keys=[torneo_id])
    proveedor_cliente = relationship(
        "ProveedorCliente", back_populates="documentos", lazy="selectin"
    )
    budget_concept = relationship(
        "BudgetConcept", foreign_keys=[budget_concept_id], lazy="selectin"
    )
    # Note: gastos relationship uses explicit foreign_keys to disambiguate from gasto_generado
    gastos = relationship(
        "ExpenseReport",
        primaryjoin="Documento.id == ExpenseReport.documento_id",
        foreign_keys=[ExpenseReport.documento_id],
        back_populates="documento",
    )
    # Note: gasto_generado relationship uses explicit foreign_keys to disambiguate from gastos
    gasto_generado = relationship(
        "ExpenseReport", foreign_keys=[gasto_generado_id], post_update=True
    )
    # Note: aprobaciones relationship is handled via queries filtering by tipo_entidad='documento' and entidad_id
    anticipos = relationship("Anticipo", back_populates="documento")
    reembolsos = relationship("Reembolso", back_populates="documento")
    adjuntos = relationship("Adjunto", back_populates="documento")
    # Cuenta de Gastos relationship
    cuenta_gastos = relationship(
        "CuentaDeGastos",
        foreign_keys=[cuenta_gastos_id],
        back_populates="documentos",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Documento(id={self.id}, tipo='{self.tipo}', numero_referencia='{self.numero_referencia}', estado='{self.estado}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "empleado_id": str(self.empleado_id),
            "tipo": self.tipo,
            "numero_referencia": self.numero_referencia,
            "estado": self.estado,
            "fecha_inicio": (
                self.fecha_inicio.isoformat() if self.fecha_inicio else None
            ),
            "fecha_fin": self.fecha_fin.isoformat() if self.fecha_fin else None,
            "monto_solicitado": (
                float(self.monto_solicitado) if self.monto_solicitado else None
            ),
            "monto_total": float(self.monto_total) if self.monto_total else None,
            "categorias": self.categorias,
            "edicion": self.edicion,
            "currency": self.currency or "MXN",
            "budget_concept_id": (
                str(self.budget_concept_id) if self.budget_concept_id else None
            ),
            "torneo_id": str(self.torneo_id) if self.torneo_id else None,
            "fase": self.fase,
            "proyecto_otro": self.proyecto_otro,
            "proveedor_cliente_id": (
                str(self.proveedor_cliente_id) if self.proveedor_cliente_id else None
            ),
            "beneficiario_empleado_id": (
                str(self.beneficiario_empleado_id)
                if self.beneficiario_empleado_id
                else None
            ),
            "notas": self.notas,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "enviado_en": self.enviado_en.isoformat() if self.enviado_en else None,
            "aprobado_en": self.aprobado_en.isoformat() if self.aprobado_en else None,
            "pagado_en": self.pagado_en.isoformat() if self.pagado_en else None,
            "fecha_pago": self.fecha_pago.isoformat() if self.fecha_pago else None,
            "pago_urgente": bool(self.pago_urgente),
            "concepto_pago": self.concepto_pago,
            "numero_factura": self.numero_factura,
            "referencia_pago": self.referencia_pago,
            "metodo_pago": self.metodo_pago,
            "gasto_generado_id": (
                str(self.gasto_generado_id) if self.gasto_generado_id else None
            ),
            "referencia_base": self.referencia_base,
            "referencia_operaciones": self.referencia_operaciones,
            "cuenta_gastos_id": (
                str(self.cuenta_gastos_id) if self.cuenta_gastos_id else None
            ),
            "cfdi_uuid_manual": self.cfdi_uuid_manual,
            "cfdi_compartido_confirmado": bool(self.cfdi_compartido_confirmado),
            "cfdi_report_id": str(self.cfdi_report_id) if self.cfdi_report_id else None,
        }


class Aprobacion(Base):
    """
    Approval tracking for documents or expenses.
    """

    __tablename__ = "aprobaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo_entidad = Column(
        String(50), nullable=False, index=True
    )  # 'documento' or 'gasto'
    entidad_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    aprobador_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    accion = Column(String(50), nullable=False)  # 'enviar', 'aprobar', 'rechazar'
    comentario = Column(Text, nullable=True)
    fecha = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    # Relationships
    aprobador = relationship("Empleado", foreign_keys=[aprobador_id])

    def __repr__(self):
        return f"<Aprobacion(id={self.id}, tipo_entidad='{self.tipo_entidad}', accion='{self.accion}', fecha='{self.fecha}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "tipo_entidad": self.tipo_entidad,
            "entidad_id": str(self.entidad_id),
            "aprobador_id": str(self.aprobador_id),
            "accion": self.accion,
            "comentario": self.comentario,
            "fecha": self.fecha.isoformat() if self.fecha else None,
        }


class Anticipo(Base):
    """
    Advance payment tracking - advances paid before a trip.
    """

    __tablename__ = "anticipos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    documento_id = Column(
        UUID(as_uuid=True), ForeignKey("documentos.id"), nullable=False, index=True
    )
    monto = Column(Numeric, nullable=False)
    moneda = Column(String(10), nullable=False)
    fecha_entrega = Column(DateTime(timezone=True), nullable=False)
    estado = Column(
        String(50), nullable=False, index=True
    )  # 'pendiente', 'aplicado', 'cerrado'
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
    documento = relationship("Documento", back_populates="anticipos")

    def __repr__(self):
        return f"<Anticipo(id={self.id}, empleado_id={self.empleado_id}, monto={self.monto}, estado='{self.estado}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "empleado_id": str(self.empleado_id),
            "documento_id": str(self.documento_id),
            "monto": float(self.monto) if self.monto else None,
            "moneda": self.moneda,
            "fecha_entrega": (
                self.fecha_entrega.isoformat() if self.fecha_entrega else None
            ),
            "estado": self.estado,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
        }


class Reembolso(Base):
    """
    Reimbursement / devolution tracking (saldar cuenta).

    v1.0.24 extends this table to model a direction-aware cuenta settlement:
    - tipo = 'reembolso' (company -> employee) or 'devolucion' (employee -> company).
    - cuenta_gastos_id scopes the settlement to a CuentaDeGastos (preferred over documento_id).
    - pagador_empleado_id is the actor who logged the settlement.
    - A mandatory comprobante is attached via the adjuntos table (reembolso_id FK).
    """

    __tablename__ = "reembolsos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    documento_id = Column(
        UUID(as_uuid=True), ForeignKey("documentos.id"), nullable=False, index=True
    )
    cuenta_gastos_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_de_gastos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pagador_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id"),
        nullable=True,
        index=True,
    )
    tipo = Column(String(20), nullable=False, default="reembolso")
    monto = Column(Numeric, nullable=False)
    moneda = Column(String(10), nullable=False)
    metodo_pago = Column(String(100), nullable=True)
    fecha_pago = Column(DateTime(timezone=True), nullable=True)
    referencia_pago = Column(Text, nullable=True)
    notas = Column(Text, nullable=True)
    estado = Column(String(50), nullable=False, index=True)
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    cancelado_en = Column(DateTime(timezone=True), nullable=True)
    cancelado_por_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=True
    )
    motivo_cancelacion = Column(Text, nullable=True)

    # Relationships
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
    pagador = relationship("Empleado", foreign_keys=[pagador_empleado_id])
    cancelado_por = relationship("Empleado", foreign_keys=[cancelado_por_id])
    documento = relationship("Documento", back_populates="reembolsos")
    cuenta_gastos = relationship(
        "CuentaDeGastos",
        foreign_keys=[cuenta_gastos_id],
        back_populates="reembolsos",
        lazy="selectin",
    )
    adjuntos = relationship(
        "Adjunto",
        back_populates="reembolso",
        foreign_keys="Adjunto.reembolso_id",
        lazy="select",
    )

    def __repr__(self):
        return (
            f"<Reembolso(id={self.id}, tipo='{self.tipo}', cuenta_gastos_id={self.cuenta_gastos_id}, "
            f"monto={self.monto}, estado='{self.estado}')>"
        )

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "empleado_id": str(self.empleado_id),
            "documento_id": str(self.documento_id),
            "cuenta_gastos_id": (
                str(self.cuenta_gastos_id) if self.cuenta_gastos_id else None
            ),
            "pagador_empleado_id": (
                str(self.pagador_empleado_id) if self.pagador_empleado_id else None
            ),
            "tipo": self.tipo,
            "monto": float(self.monto) if self.monto else None,
            "moneda": self.moneda,
            "metodo_pago": self.metodo_pago,
            "fecha_pago": self.fecha_pago.isoformat() if self.fecha_pago else None,
            "referencia_pago": self.referencia_pago,
            "notas": self.notas,
            "estado": self.estado,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "cancelado_en": (
                self.cancelado_en.isoformat() if self.cancelado_en else None
            ),
            "cancelado_por_id": (
                str(self.cancelado_por_id) if self.cancelado_por_id else None
            ),
            "motivo_cancelacion": self.motivo_cancelacion,
        }


class Adjunto(Base):
    """
    File attachments - allows multiple files per expense or document.
    """

    __tablename__ = "adjuntos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    gasto_id = Column(
        UUID(as_uuid=True), ForeignKey("expense_reports.id"), nullable=True
    )
    documento_id = Column(
        UUID(as_uuid=True), ForeignKey("documentos.id"), nullable=True
    )
    reembolso_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reembolsos.id", ondelete="CASCADE"),
        nullable=True,
    )
    ruta_archivo = Column(Text, nullable=False)
    tipo_archivo = Column(String(100), nullable=True)
    nombre_archivo = Column(String(500), nullable=True)
    mime_type = Column(String(200), nullable=True)
    categoria = Column(
        String(50), nullable=True
    )  # receipt, cfdi_pdf, cfdi_xml, supporting, comprobante_reembolso
    origen = Column(
        String(50), nullable=True
    )  # user_upload, tocino_webhook, legacy_backfill, document_upload
    subido_en = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    gasto = relationship(
        "ExpenseReport", back_populates="adjuntos", foreign_keys=[gasto_id]
    )
    documento = relationship("Documento", back_populates="adjuntos")
    reembolso = relationship(
        "Reembolso", back_populates="adjuntos", foreign_keys=[reembolso_id]
    )

    def __repr__(self):
        return f"<Adjunto(id={self.id}, gasto_id={self.gasto_id}, documento_id={self.documento_id}, ruta_archivo='{self.ruta_archivo}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "gasto_id": str(self.gasto_id) if self.gasto_id else None,
            "documento_id": str(self.documento_id) if self.documento_id else None,
            "reembolso_id": str(self.reembolso_id) if self.reembolso_id else None,
            "ruta_archivo": self.ruta_archivo,
            "tipo_archivo": self.tipo_archivo,
            "nombre_archivo": self.nombre_archivo,
            "mime_type": self.mime_type,
            "categoria": self.categoria,
            "origen": self.origen,
            "subido_en": self.subido_en.isoformat() if self.subido_en else None,
        }


class CuentaDeGastos(Base):
    """
    Cuenta de Gastos - represents a trip/reimbursement case/expense batch.

    This is the organizing unit for personal expenses. Each cuenta has a pair
    of documents (SOLICITUD_PERSONAL + INFORME) and multiple expenses linked
    via referencia_base.

    Classification: tipo_cuenta is 'local' | 'viaje' | 'nacional' | 'extranjero'.
    torneo_id is required for all informes (project/tournament context). fase
    (subproject / etapa) is optional; when set it must match Tournament.etapas.

    Third-party SOLICITUD documents do NOT use this; they remain document-centric.
    """

    __tablename__ = "cuentas_de_gastos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    referencia_base = Column(Text, nullable=False, index=True)
    nombre = Column(
        Text, nullable=True
    )  # Optional display name (Nombre de Cuenta de Gastos)
    estado = Column(Text, nullable=False, default="abierta")  # 'abierta' or 'cerrada'
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Classification (v1.0.19 columns; v1.0.22 rules: four tipos, torneo required, fase optional).
    # deferred() so these load on access; allows old DBs without columns to load base row first.
    tipo_cuenta = deferred(
        Column(String(20), nullable=False, default="local", index=True)
    )  # local | viaje | nacional | extranjero
    # DB enforces non-null via check_cuenta_informe_tipo_torneo_fase (v1.0.22+); keep ORM nullable=True
    # so incomplete migrations / deferred loads do not break row hydration.
    torneo_id = deferred(
        Column(
            UUID(as_uuid=True),
            ForeignKey("tournaments.id", onupdate="CASCADE", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )
    )
    fase = deferred(Column(String(200), nullable=True, index=True))
    categorias = Column(JSONB, nullable=True)
    edicion = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=False, default="MXN")

    # Relationships
    empleado = relationship("Empleado", foreign_keys=[empleado_id], lazy="selectin")
    torneo = relationship("Tournament", foreign_keys=[torneo_id], lazy="selectin")
    documentos = relationship(
        "Documento", back_populates="cuenta_gastos", lazy="selectin"
    )
    gastos = relationship(
        "ExpenseReport", back_populates="cuenta_gastos", lazy="selectin"
    )
    reembolsos = relationship(
        "Reembolso",
        back_populates="cuenta_gastos",
        foreign_keys="Reembolso.cuenta_gastos_id",
        lazy="select",
    )

    # Unique constraint: (empleado_id, referencia_base)
    __table_args__ = (
        CheckConstraint("estado IN ('abierta', 'cerrada')", name="check_cuenta_estado"),
    )

    def __repr__(self):
        return f"<CuentaDeGastos(id={self.id}, empleado_id={self.empleado_id}, referencia_base='{self.referencia_base}', estado='{self.estado}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "empleado_id": str(self.empleado_id),
            "referencia_base": self.referencia_base,
            "nombre": self.nombre,
            "estado": self.estado,
            "tipo_cuenta": self.tipo_cuenta,
            "torneo_id": str(self.torneo_id) if self.torneo_id else None,
            "fase": self.fase,
            "categorias": self.categorias,
            "edicion": self.edicion,
            "currency": self.currency or "MXN",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class AssistantConversation(Base):
    """Persistent assistant conversation (stored in gastos DB)."""

    __tablename__ = "assistant_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )
    title = Column(String(200), nullable=True)
    tournament_key = Column(
        String(50), nullable=True, index=True
    )  # e.g. 'copa_america'
    archived = Column(Boolean, default=False, nullable=False, index=True)
    # SQLAlchemy Declarative reserves the attribute name `metadata`.
    metadata_ = Column("metadata", JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    messages = relationship(
        "AssistantMessage", back_populates="conversation", cascade="all, delete-orphan"
    )
    runs = relationship(
        "AssistantRun", back_populates="conversation", cascade="all, delete-orphan"
    )
    artifacts = relationship(
        "AssistantArtifact", back_populates="conversation", cascade="all, delete-orphan"
    )


class AssistantMessage(Base):
    """Message within an assistant conversation."""

    __tablename__ = "assistant_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False)  # system|user|assistant|tool
    content = Column(Text, nullable=True)
    tool_name = Column(String(100), nullable=True, index=True)
    tool_payload = Column(JSONB, nullable=True)  # args or tool result

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    conversation = relationship("AssistantConversation", back_populates="messages")


class AssistantRun(Base):
    """Audit log for assistant runs (prompt/response/tools)."""

    __tablename__ = "assistant_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )

    status = Column(
        String(50), nullable=False, default="completed", index=True
    )  # completed|pending_confirmation|failed
    model = Column(String(100), nullable=True)

    user_message = Column(Text, nullable=True)
    assistant_message = Column(Text, nullable=True)
    tool_trace = Column(JSONB, nullable=True)

    pending_tool_name = Column(String(100), nullable=True)
    pending_tool_args = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    conversation = relationship("AssistantConversation", back_populates="runs")


class AssistantArtifact(Base):
    """A saved output produced by the assistant (requires admin confirmation for writes)."""

    __tablename__ = "assistant_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_empleado_id = Column(
        UUID(as_uuid=True), ForeignKey("empleados.id"), nullable=False, index=True
    )

    title = Column(String(200), nullable=False)
    artifact_type = Column(
        String(50), nullable=False, default="report_template", index=True
    )
    format = Column(String(20), nullable=False, default="markdown")  # markdown|csv|json
    content = Column(Text, nullable=False)
    # SQLAlchemy Declarative reserves the attribute name `metadata`.
    metadata_ = Column("metadata", JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )

    conversation = relationship("AssistantConversation", back_populates="artifacts")


class RegulatorySource(Base):
    """Authoritative legal/fiscal source tracked for payroll and regulatory calculations."""

    __tablename__ = "regulatory_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_key = Column(String(120), nullable=False, unique=True, index=True)
    source_type = Column(
        String(50), nullable=False, index=True
    )  # tax|labor|social_security|economic_indicator
    authority = Column(String(120), nullable=False)
    title = Column(String(500), nullable=False)
    url = Column(Text, nullable=False, unique=True)
    legal_reference = Column(String(255), nullable=True)
    verification_status = Column(
        String(40), nullable=False, default="official", index=True
    )
    published_at = Column(DateTime(timezone=True), nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=True, index=True)
    effective_to = Column(DateTime(timezone=True), nullable=True, index=True)
    summary_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class LaborRuleSnapshot(Base):
    """Normalized labor or economic rule snapshot with explicit validity range."""

    __tablename__ = "labor_rule_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regulatory_sources.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_key = Column(String(120), nullable=False, index=True)
    category = Column(
        String(60), nullable=False, index=True
    )  # lft|uma|salary_minimum|payroll_policy
    title = Column(String(255), nullable=False)
    legal_reference = Column(String(255), nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_to = Column(DateTime(timezone=True), nullable=True, index=True)
    numeric_value = Column(Numeric(18, 6), nullable=True)
    unit = Column(String(40), nullable=True)
    payload_json = Column(JSONB, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    source = relationship("RegulatorySource", foreign_keys=[source_id], lazy="selectin")


class TaxTableISR(Base):
    """ISR brackets with explicit periodicity and validity window."""

    __tablename__ = "tax_tables_isr"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regulatory_sources.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    regime_key = Column(
        String(80), nullable=False, default="sueldos_salarios", index=True
    )
    periodicity = Column(
        String(20), nullable=False, index=True
    )  # annual|daily|weekly|ten_day|biweekly|monthly
    effective_from = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_to = Column(DateTime(timezone=True), nullable=True, index=True)
    row_order = Column(Integer, nullable=False, default=1)
    lower_limit = Column(Numeric(18, 2), nullable=False)
    upper_limit = Column(Numeric(18, 2), nullable=True)
    fixed_fee = Column(Numeric(18, 2), nullable=False, default=0)
    marginal_rate = Column(Numeric(8, 4), nullable=False)  # percentage, e.g. 1.92
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    source = relationship("RegulatorySource", foreign_keys=[source_id], lazy="selectin")


class TaxTableSubsidioEmpleo(Base):
    """Subsidio al empleo vigente por rango temporal."""

    __tablename__ = "tax_tables_subsidio_empleo"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regulatory_sources.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    periodicity = Column(String(20), nullable=False, default="monthly", index=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_to = Column(DateTime(timezone=True), nullable=True, index=True)
    income_limit = Column(Numeric(18, 2), nullable=False)
    subsidy_amount = Column(Numeric(18, 2), nullable=False)
    subsidy_percent = Column(Numeric(8, 4), nullable=True)
    uma_value = Column(Numeric(18, 2), nullable=True)
    uma_periodicity = Column(String(20), nullable=True)  # daily|monthly|annual
    legal_reference = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    source = relationship("RegulatorySource", foreign_keys=[source_id], lazy="selectin")


class SocialSecurityTable(Base):
    """Social security and housing contribution parameters used by payroll."""

    __tablename__ = "social_security_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regulatory_sources.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    component_key = Column(String(120), nullable=False, index=True)
    component_name = Column(String(255), nullable=False)
    branch = Column(String(80), nullable=True, index=True)  # imss|infonavit
    calculation_mode = Column(
        String(30), nullable=False, default="fixed_rate"
    )  # fixed_rate|formula|progressive|variable
    base_type = Column(
        String(40), nullable=True
    )  # sdi|uma_daily|uma_monthly|salary_minimum
    employer_rate = Column(Numeric(10, 6), nullable=True)
    employee_rate = Column(Numeric(10, 6), nullable=True)
    fixed_amount = Column(Numeric(18, 4), nullable=True)
    min_uma = Column(Numeric(10, 4), nullable=True)
    max_uma = Column(Numeric(10, 4), nullable=True)
    legal_reference = Column(String(255), nullable=True)
    formula_json = Column(JSONB, nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_to = Column(DateTime(timezone=True), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    source = relationship("RegulatorySource", foreign_keys=[source_id], lazy="selectin")


class PayrollEmployee(Base):
    """Payroll-specific extension of an internal employee."""

    __tablename__ = "payroll_employees"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    employee_number = Column(String(80), nullable=True, unique=True, index=True)
    birth_date = Column(Date, nullable=True, index=True)
    birth_place = Column(String(120), nullable=True)
    gender = Column(String(20), nullable=True)
    curp = Column(String(18), nullable=True, index=True)
    rfc = Column(String(13), nullable=True, index=True)
    nss = Column(String(16), nullable=True, index=True)
    tax_regime = Column(String(160), nullable=True)
    personal_email = Column(String(160), nullable=True)
    work_email = Column(String(160), nullable=True)
    personal_postal_code = Column(String(10), nullable=True)
    hire_date = Column(Date, nullable=True, index=True)
    seniority_date = Column(Date, nullable=True)
    contract_start_date = Column(Date, nullable=True)
    contract_end_date = Column(Date, nullable=True)
    employment_state = Column(String(120), nullable=True)
    employee_type = Column(String(80), nullable=True)
    contract_type = Column(String(60), nullable=True)
    policy_name = Column(String(120), nullable=True)
    worker_type = Column(String(120), nullable=True)
    geographic_area = Column(String(60), nullable=True)
    schedule_scheme = Column(String(120), nullable=True)
    reduced_workweek_type = Column(String(120), nullable=True)
    worked_days_override = Column(Numeric(10, 4), nullable=True)
    employer_registration_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "payroll_employer_registrations.id", onupdate="CASCADE", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    payroll_frequency = Column(
        String(20), nullable=False, default="biweekly", index=True
    )
    salary_zone = Column(String(20), nullable=False, default="general")  # general|zlfn
    payment_method = Column(String(30), nullable=False, default="transfer")
    bank_name = Column(String(120), nullable=True)
    bank_account_last4 = Column(String(4), nullable=True)
    job_title = Column(String(120), nullable=True)
    department_name = Column(String(120), nullable=True)
    daily_salary = Column(Numeric(18, 2), nullable=True)
    integrated_daily_salary = Column(Numeric(18, 2), nullable=True)
    variable_salary = Column(Numeric(18, 2), nullable=True)
    work_risk_class = Column(String(20), nullable=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    empleado = relationship("Empleado", foreign_keys=[empleado_id], lazy="selectin")
    employer_registration = relationship(
        "PayrollEmployerRegistration",
        foreign_keys=[employer_registration_id],
        lazy="selectin",
    )
    compensation_profile = relationship(
        "PayrollEmployeeCompensationProfile",
        foreign_keys="PayrollEmployeeCompensationProfile.payroll_employee_id",
        back_populates="payroll_employee",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    payment_profile = relationship(
        "PayrollEmployeePaymentProfile",
        foreign_keys="PayrollEmployeePaymentProfile.payroll_employee_id",
        back_populates="payroll_employee",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    deduction_profile = relationship(
        "PayrollEmployeeDeductionProfile",
        foreign_keys="PayrollEmployeeDeductionProfile.payroll_employee_id",
        back_populates="payroll_employee",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    benefit_profile = relationship(
        "PayrollEmployeeBenefitProfile",
        foreign_keys="PayrollEmployeeBenefitProfile.payroll_employee_id",
        back_populates="payroll_employee",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    address_profile = relationship(
        "PayrollEmployeeAddressProfile",
        foreign_keys="PayrollEmployeeAddressProfile.payroll_employee_id",
        back_populates="payroll_employee",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PayrollEmployer(Base):
    """Employer legal entity / payroll source of truth for patronal settings."""

    __tablename__ = "payroll_employers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    employer_key = Column(String(80), nullable=False, unique=True, index=True)
    legal_name = Column(String(255), nullable=False)
    rfc = Column(String(13), nullable=True, unique=True, index=True)
    payroll_mode = Column(
        String(40), nullable=True
    )  # sueldos_salarios|asimilados|mixto
    active = Column(Boolean, nullable=False, default=True, index=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    registrations = relationship(
        "PayrollEmployerRegistration",
        foreign_keys="PayrollEmployerRegistration.payroll_employer_id",
        back_populates="employer",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class PayrollEmployerRegistration(Base):
    """Employer registration and its single patronal risk configuration."""

    __tablename__ = "payroll_employer_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employers.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    registration_code = Column(String(40), nullable=False, unique=True, index=True)
    branch_name = Column(String(160), nullable=True)
    risk_class = Column(String(20), nullable=True)  # I-V
    risk_premium = Column(Numeric(10, 6), nullable=True)
    effective_from = Column(Date, nullable=True, index=True)
    effective_to = Column(Date, nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    employer = relationship(
        "PayrollEmployer",
        foreign_keys=[payroll_employer_id],
        back_populates="registrations",
        lazy="selectin",
    )


class PayrollAccountMapping(Base):
    """Payroll accounting purpose to chart-of-accounts mapping."""

    __tablename__ = "payroll_account_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employers.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    purpose_key = Column(String(80), nullable=False, index=True)
    cuenta_contable_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_contables.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )
    active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text, nullable=True)
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employer = relationship(
        "PayrollEmployer", foreign_keys=[payroll_employer_id], lazy="selectin"
    )
    cuenta_contable = relationship(
        "CuentaContable", foreign_keys=[cuenta_contable_id], lazy="selectin"
    )
    created_by = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )


class PayrollEmployeeCompensationProfile(Base):
    """Normalized compensation and employment classification profile."""

    __tablename__ = "payroll_employee_compensation_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    compensation_regime = Column(
        String(80), nullable=True
    )  # sueldos_salarios|asimilado
    salary_type = Column(String(40), nullable=True)  # fijo|variable|mixto
    monthly_net_salary = Column(Numeric(18, 2), nullable=True)
    daily_salary = Column(Numeric(18, 2), nullable=True)
    integrated_daily_salary = Column(Numeric(18, 2), nullable=True)
    variable_salary = Column(Numeric(18, 2), nullable=True)
    severance_daily_salary = Column(Numeric(18, 2), nullable=True)
    work_risk_class = Column(String(20), nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee",
        foreign_keys=[payroll_employee_id],
        back_populates="compensation_profile",
        lazy="selectin",
    )


class PayrollEmployeePaymentProfile(Base):
    """Normalized payment and bank profile."""

    __tablename__ = "payroll_employee_payment_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    payment_method = Column(String(30), nullable=True)
    bank_name = Column(String(120), nullable=True)
    account_number = Column(String(32), nullable=True)
    clabe = Column(String(18), nullable=True)
    customer_number = Column(String(40), nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee",
        foreign_keys=[payroll_employee_id],
        back_populates="payment_profile",
        lazy="selectin",
    )


class PayrollEmployeeDeductionProfile(Base):
    """Normalized recurring deduction profile."""

    __tablename__ = "payroll_employee_deduction_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    deduction_name = Column(String(160), nullable=True)
    infonavit_discount_type = Column(String(80), nullable=True)
    infonavit_discount_value = Column(Numeric(18, 2), nullable=True)
    infonavit_notice_folio = Column(String(80), nullable=True)
    infonavit_credit_number = Column(String(80), nullable=True)
    infonavit_start_date = Column(Date, nullable=True)
    loan_balance = Column(Numeric(18, 2), nullable=True)
    monthly_deduction_amount = Column(Numeric(18, 2), nullable=True)
    payroll_deduction_name = Column(String(160), nullable=True)
    fonacot_credit_folio = Column(String(80), nullable=True)
    fonacot_discount_type = Column(String(80), nullable=True)
    fonacot_discount_value = Column(Numeric(18, 2), nullable=True)
    fonacot_start_date = Column(Date, nullable=True)
    alimony_percentage = Column(Numeric(10, 4), nullable=True)
    alimony_mode = Column(
        String(40), nullable=True
    )  # percent_net|percent_gross|fixed_amount
    alimony_fixed_amount = Column(Numeric(18, 2), nullable=True)
    alimony_case_number = Column(String(120), nullable=True)
    alimony_beneficiary_name = Column(String(160), nullable=True)
    alimony_beneficiary_bank = Column(String(120), nullable=True)
    alimony_beneficiary_account = Column(String(64), nullable=True)
    alimony_beneficiary_clabe = Column(String(18), nullable=True)
    alimony_effective_from = Column(Date, nullable=True)
    alimony_effective_to = Column(Date, nullable=True)
    alimony_apply_to_extraordinary = Column(Boolean, nullable=False, default=False)
    alimony_priority_order = Column(Integer, nullable=True)
    alimony_court_name = Column(String(160), nullable=True)
    alimony_office_reference = Column(String(160), nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee",
        foreign_keys=[payroll_employee_id],
        back_populates="deduction_profile",
        lazy="selectin",
    )


class PayrollEmployeeBenefitProfile(Base):
    """Normalized benefits profile including vacations, UMF and meal vouchers."""

    __tablename__ = "payroll_employee_benefit_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    vacation_balance = Column(Numeric(18, 2), nullable=True)
    umf = Column(String(40), nullable=True)
    voucher_provider = Column(String(120), nullable=True)
    voucher_account_number = Column(String(64), nullable=True)
    voucher_card_number = Column(String(64), nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee",
        foreign_keys=[payroll_employee_id],
        back_populates="benefit_profile",
        lazy="selectin",
    )


class PayrollEmployeeAddressProfile(Base):
    """Normalized tax address profile."""

    __tablename__ = "payroll_employee_address_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    street = Column(String(160), nullable=True)
    exterior_number = Column(String(40), nullable=True)
    interior_number = Column(String(40), nullable=True)
    neighborhood = Column(String(120), nullable=True)
    municipality = Column(String(120), nullable=True)
    state = Column(String(120), nullable=True)
    postal_code = Column(String(10), nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee",
        foreign_keys=[payroll_employee_id],
        back_populates="address_profile",
        lazy="selectin",
    )


class PayrollPeriod(Base):
    """Closed or open payroll period."""

    __tablename__ = "payroll_periods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    period_type = Column(
        String(20), nullable=False, index=True
    )  # weekly|biweekly|monthly
    fiscal_year = Column(Integer, nullable=False, index=True)
    period_no = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=False, index=True)
    payment_date = Column(Date, nullable=True, index=True)
    status = Column(
        String(30), nullable=False, default="draft", index=True
    )  # draft|open|calculated|posted|closed
    notes = Column(Text, nullable=True)
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    created_by = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )


class PayrollIncident(Base):
    """Prenómina incident captured before a payroll run."""

    __tablename__ = "payroll_incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_periods.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    incident_type = Column(
        String(40), nullable=False, index=True
    )  # perception|deduction|absence|vacation|bonus|loan
    incident_code = Column(String(60), nullable=True, index=True)
    quantity = Column(Numeric(18, 4), nullable=True)
    taxable_amount = Column(Numeric(18, 2), nullable=True)
    exempt_amount = Column(Numeric(18, 2), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="captured", index=True)
    payload_json = Column(JSONB, nullable=True)
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    payroll_employee = relationship(
        "PayrollEmployee", foreign_keys=[payroll_employee_id], lazy="selectin"
    )
    period = relationship("PayrollPeriod", foreign_keys=[period_id], lazy="selectin")
    created_by = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )


class PayrollRun(Base):
    """Calculated payroll run for a period."""

    __tablename__ = "payroll_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    period_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_periods.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_type = Column(String(30), nullable=False, default="ordinary", index=True)
    status = Column(
        String(30), nullable=False, default="draft", index=True
    )  # draft|calculated|posted|cancelled
    notes = Column(Text, nullable=True)
    source_snapshot_tag = Column(String(120), nullable=True, index=True)
    gross_total = Column(Numeric(18, 2), nullable=True)
    deductions_total = Column(Numeric(18, 2), nullable=True)
    employer_charges_total = Column(Numeric(18, 2), nullable=True)
    net_total = Column(Numeric(18, 2), nullable=True)
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    period = relationship("PayrollPeriod", foreign_keys=[period_id], lazy="selectin")
    created_by = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )


class PayrollRunLine(Base):
    """Calculated payroll line for one employee inside a payroll run."""

    __tablename__ = "payroll_run_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_runs.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payroll_employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_employees.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    days_paid = Column(Numeric(10, 4), nullable=True)
    taxable_total = Column(Numeric(18, 2), nullable=True)
    exempt_total = Column(Numeric(18, 2), nullable=True)
    deductions_total = Column(Numeric(18, 2), nullable=True)
    employer_charges_total = Column(Numeric(18, 2), nullable=True)
    isr_withheld = Column(Numeric(18, 2), nullable=True)
    subsidy_applied = Column(Numeric(18, 2), nullable=True)
    net_pay = Column(Numeric(18, 2), nullable=True)
    integrated_daily_salary_used = Column(Numeric(18, 2), nullable=True)
    perceptions_json = Column(JSONB, nullable=True)
    deductions_json = Column(JSONB, nullable=True)
    employer_charges_json = Column(JSONB, nullable=True)
    incidents_summary = Column(JSONB, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    run = relationship("PayrollRun", foreign_keys=[run_id], lazy="selectin")
    payroll_employee = relationship(
        "PayrollEmployee", foreign_keys=[payroll_employee_id], lazy="selectin"
    )


class PayrollConcept(Base):
    """Catalog of payroll concepts used by prenómina and payroll runs."""

    __tablename__ = "payroll_concepts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    concept_key = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(160), nullable=False)
    concept_type = Column(
        String(30), nullable=False, index=True
    )  # perception|deduction|employer_charge
    input_mode = Column(
        String(30), nullable=False, default="amount"
    )  # amount|days|hours|percent|flag
    tax_group = Column(String(40), nullable=True, index=True)
    affects_sbc = Column(Boolean, nullable=False, default=False, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    display_order = Column(Integer, nullable=False, default=100)
    aliases_json = Column(JSONB, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PayrollConceptRule(Base):
    """Effective-dated rule set for one payroll concept."""

    __tablename__ = "payroll_concept_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    concept_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_concepts.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regulatory_sources.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=True, index=True)
    taxable_mode = Column(
        String(40), nullable=False, default="fully_taxable"
    )  # fully_taxable|fully_exempt|split_formula
    exempt_formula_key = Column(String(80), nullable=True, index=True)
    taxable_formula_key = Column(String(80), nullable=True, index=True)
    sbc_mode = Column(
        String(40), nullable=False, default="ignore"
    )  # ignore|include_full|include_partial
    exempt_cap_multiplier = Column(Numeric(18, 4), nullable=True)
    exempt_cap_unit = Column(
        String(30), nullable=True
    )  # uma_daily|uma_monthly|salary_minimum_daily|calendar_days
    payload_json = Column(JSONB, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    concept = relationship("PayrollConcept", foreign_keys=[concept_id], lazy="selectin")
    source = relationship("RegulatorySource", foreign_keys=[source_id], lazy="selectin")


class PayrollSATCatalogEntry(Base):
    """Subset of SAT payroll catalog entries used to project CFDI nomina."""

    __tablename__ = "payroll_sat_catalog_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sat_group = Column(
        String(30), nullable=False, index=True
    )  # percepcion|deduccion|otro_pago
    code = Column(String(10), nullable=False, index=True)
    description = Column(String(200), nullable=False)
    official_source_url = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PayrollSATConceptMapping(Base):
    """Mapping from internal payroll concept keys to SAT payroll catalog codes."""

    __tablename__ = "payroll_sat_concept_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    concept_key = Column(String(80), nullable=False, index=True)
    sat_group = Column(
        String(30), nullable=False, index=True
    )  # percepcion|deduccion|otro_pago
    sat_code = Column(String(10), nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    mapping_basis = Column(String(40), nullable=False, default="default_seed")
    notes = Column(Text, nullable=True)
    created_by_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    created_by = relationship(
        "Empleado", foreign_keys=[created_by_empleado_id], lazy="selectin"
    )


# Allowed values kept as module-level constants so routes/tests can validate
# without round-tripping through the DB CHECK constraint.
SUPPORT_TICKET_CATEGORIES: tuple[str, ...] = (
    "bug",
    "duda",
    "solicitud",
    "acceso",
    "otro",
)
SUPPORT_TICKET_PRIORITIES: tuple[str, ...] = ("baja", "normal", "alta", "urgente")
SUPPORT_TICKET_STATUSES: tuple[str, ...] = (
    "abierto",
    "en_revision",
    "en_progreso",
    "resuelto",
    "cerrado",
)
SUPPORT_TICKET_OPEN_STATUSES: frozenset[str] = frozenset(
    {"abierto", "en_revision", "en_progreso"}
)


class SupportTicket(Base):
    """
    Support ticket submitted by an empleado.

    Tickets are intentionally simple: a subject, a description, a category,
    a priority and a workflow status. Superadmins triage and respond via the
    admin dashboard; the requester sees their own tickets and the response
    thread on /soporte.
    """

    __tablename__ = "support_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    requester_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asunto = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=False)
    categoria = Column(String(40), nullable=False, default="otro", index=True)
    prioridad = Column(String(20), nullable=False, default="normal", index=True)
    estado = Column(String(30), nullable=False, default="abierto", index=True)

    # Optional context the user can volunteer to speed up triage.
    page_url = Column(String(600), nullable=True)
    contact_email = Column(String(200), nullable=True)

    # Triage metadata maintained by superadmins.
    assigned_to_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resolution_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    requester = relationship(
        "Empleado",
        foreign_keys=[requester_empleado_id],
        lazy="selectin",
    )
    assignee = relationship(
        "Empleado",
        foreign_keys=[assigned_to_empleado_id],
        lazy="selectin",
    )
    comments = relationship(
        "SupportTicketComment",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportTicketComment.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "categoria IN ('bug','duda','solicitud','acceso','otro')",
            name="ck_support_tickets_categoria",
        ),
        CheckConstraint(
            "prioridad IN ('baja','normal','alta','urgente')",
            name="ck_support_tickets_prioridad",
        ),
        CheckConstraint(
            "estado IN ('abierto','en_revision','en_progreso','resuelto','cerrado')",
            name="ck_support_tickets_estado",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SupportTicket(id={self.id}, asunto='{self.asunto}', "
            f"estado='{self.estado}', prioridad='{self.prioridad}')>"
        )

    @property
    def is_open(self) -> bool:
        return (self.estado or "").lower() in SUPPORT_TICKET_OPEN_STATUSES

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "requester_empleado_id": str(self.requester_empleado_id),
            "asunto": self.asunto,
            "descripcion": self.descripcion,
            "categoria": self.categoria,
            "prioridad": self.prioridad,
            "estado": self.estado,
            "page_url": self.page_url,
            "contact_email": self.contact_email,
            "assigned_to_empleado_id": (
                str(self.assigned_to_empleado_id)
                if self.assigned_to_empleado_id
                else None
            ),
            "resolution_note": self.resolution_note,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SupportTicketComment(Base):
    """Comment thread on a support ticket. Visible to both requester and admin."""

    __tablename__ = "support_ticket_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_tickets.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 'requester' = comment from the user who opened the ticket.
    # 'staff'     = comment from a superadmin/finanzas/admin role.
    # 'system'    = automatic note (status change, assignment, etc.).
    author_role = Column(String(30), nullable=False, default="requester")
    body = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    ticket = relationship("SupportTicket", back_populates="comments")
    author = relationship(
        "Empleado",
        foreign_keys=[author_empleado_id],
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "author_role IN ('requester','staff','system')",
            name="ck_support_ticket_comments_role",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SupportTicketComment(id={self.id}, ticket_id={self.ticket_id}, "
            f"author_role='{self.author_role}')>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "author_empleado_id": (
                str(self.author_empleado_id) if self.author_empleado_id else None
            ),
            "author_role": self.author_role,
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TelegramNotificationOutbox(Base):
    """Delivery log for gastos/documentos Telegram notifications."""

    __tablename__ = "telegram_notification_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    notification_type = Column(String(64), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    documento_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documentos.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipient_empleado_id = Column(
        UUID(as_uuid=True),
        ForeignKey("empleados.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    telegram_chat_id = Column(BigInteger, nullable=True)
    header_text = Column(Text, nullable=True)
    body_preview = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)

    documento = relationship("Documento", foreign_keys=[documento_id], lazy="selectin")
    recipient_empleado = relationship(
        "Empleado", foreign_keys=[recipient_empleado_id], lazy="selectin"
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "notification_type": self.notification_type,
            "status": self.status,
            "documento_id": str(self.documento_id) if self.documento_id else None,
            "recipient_empleado_id": (
                str(self.recipient_empleado_id) if self.recipient_empleado_id else None
            ),
            "telegram_chat_id": self.telegram_chat_id,
            "header_text": self.header_text,
            "body_preview": self.body_preview,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "retry_count": self.retry_count,
            "next_retry_at": (
                self.next_retry_at.isoformat() if self.next_retry_at else None
            ),
        }
