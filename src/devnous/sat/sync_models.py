"""Persistent SAT scheduled sync state and download job tracking."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

from devnous.copa_telmex.models import Base


class SATSyncState(Base):
    """High-water cursor and quota state per RFC + direction."""

    __tablename__ = "sat_sync_state"
    __table_args__ = (
        UniqueConstraint("rfc", "direction", name="uq_sat_sync_state_rfc_direction"),
    )

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    rfc = Column(String(13), nullable=False, index=True)
    direction = Column(String(20), nullable=False, index=True)  # received | issued

    june_2026_backfill_completed_at = Column(DateTime, nullable=True)
    last_successful_sync_at = Column(DateTime, nullable=True)
    cursor_fecha_emision = Column(DateTime, nullable=True)
    cursor_fecha_timbrado = Column(DateTime, nullable=True)
    forward_floor_fecha_emision = Column(DateTime, nullable=True)

    quota_blocked_until = Column(DateTime, nullable=True)
    last_error_code = Column(Integer, nullable=True)
    last_error_message = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SATSyncRun(Base):
    """Audit log for scheduled/manual SAT sync orchestration runs."""

    __tablename__ = "sat_sync_runs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mode = Column(String(30), nullable=False, index=True)
    trigger_source = Column(String(30), nullable=False, index=True)  # cron | admin | api
    started_at = Column(DateTime, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, index=True)
    summary_message = Column(Text, nullable=True)
    results = Column(JSONB, nullable=True)
    http_status = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SATDownloadRequest(Base):
    """Persisted SAT solicitud lifecycle for reuse and quota-aware sync."""

    __tablename__ = "sat_download_requests"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    rfc = Column(String(13), nullable=False, index=True)
    direction = Column(String(20), nullable=False, index=True)
    sync_mode = Column(String(30), nullable=False, default="forward")

    fecha_inicial = Column(DateTime, nullable=False)
    fecha_final = Column(DateTime, nullable=False)
    solicitud_id = Column(String(100), nullable=True, index=True)
    estado_sat = Column(String(50), nullable=True, index=True)

    package_ids = Column(JSONB, nullable=True)
    packages_downloaded = Column(JSONB, nullable=True)
    packages_ingested = Column(JSONB, nullable=True)

    ingested_cfdis = Column(Integer, nullable=False, default=0)
    sat_num_cfdis = Column(Integer, nullable=True)
    linked_expenses = Column(Integer, nullable=False, default=0)
    linked_documentos = Column(Integer, nullable=False, default=0)

    last_verified_at = Column(DateTime, nullable=True)
    last_error_code = Column(Integer, nullable=True)
    last_error_message = Column(Text, nullable=True)
    is_complete = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SATPackageBlob(Base):
    """Cached SAT package ZIP bytes to avoid re-download (SAT 5008)."""

    __tablename__ = "sat_package_blobs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    package_id = Column(String(200), nullable=False, unique=True, index=True)
    rfc = Column(String(13), nullable=False, index=True)
    solicitud_id = Column(String(100), nullable=True, index=True)
    package_bytes = Column(Text, nullable=False)  # base64-encoded ZIP
    cod_estatus = Column(Integer, nullable=True)

    downloaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
