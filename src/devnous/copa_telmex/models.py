"""
SQLAlchemy models for Copa Telmex registration system.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Team(Base):
    """Team registration model."""

    __tablename__ = "copa_telmex_teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), nullable=False, index=True)
    # Allows multiple competitions/calendars in the same DB (e.g. futbol vs beisbol).
    tournament_slug = Column(String(80), index=True)
    gender = Column(String(10))  # varonil, femenil
    category = Column(String(20))  # U10, U12, U14, U16, U18, Open
    league = Column(String(100))
    league_phone = Column(String(20))  # Teléfono de la liga
    league_address = Column(String(300))  # Domicilio de la liga
    representative_name = Column(String(200))
    contact_email = Column(String(150))  # Email de contacto (representante/equipo)
    contact_phone = Column(String(20))  # Teléfono de contacto
    state = Column(String(50))
    municipality = Column(String(100))

    # OCR image
    roster_image_path = Column(String(500))  # Path to roster OCR image file

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    postcommit_revision = Column(Integer, nullable=False, default=1)
    postcommit_snapshot_hash = Column(
        String(71), nullable=False, default=lambda: "sha256:" + ("0" * 64)
    )

    # Telegram tracking
    telegram_chat_id = Column(BigInteger, index=True)
    telegram_user_id = Column(BigInteger)

    # Relationships
    players = relationship(
        "Player", back_populates="team", cascade="all, delete-orphan"
    )
    registrations = relationship("OCRRegistration", back_populates="team")

    def __repr__(self):
        return f"<Team(id={self.id}, name='{self.name}', category='{self.category}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "name": self.name,
            "gender": self.gender,
            "category": self.category,
            "league": self.league,
            "representative_name": self.representative_name,
            "state": self.state,
            "municipality": self.municipality,
            "roster_image_path": self.roster_image_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Player(Base):
    """Player registration model."""

    __tablename__ = "copa_telmex_players"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_teams.id"),
        nullable=False,
        index=True,
    )

    # Player info
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    birth_date = Column(Date)
    curp = Column(String(18), unique=True, index=True)  # CURP is unique
    email = Column(String(150))
    photo_path = Column(String(500))  # Path to player photo file
    photo_data = Column(Text)  # Base64 encoded photo data (optional)
    photo_sha256 = Column(String(64), index=True)
    photo_ahash = Column(String(16), index=True)

    # CURP validation
    curp_valid = Column(Boolean, default=False)
    curp_validation_date = Column(DateTime)
    curp_validation_errors = Column(Text)  # JSON string with validation errors

    # OCR metadata
    ocr_confidence = Column(Float)  # OCR confidence score
    needs_review = Column(Boolean, default=False)
    verified_by_human = Column(Boolean, default=False)
    verification_notes = Column(Text)
    roster_index = Column(Integer)  # 1-based order in roster (top-to-bottom, by rows)

    # REG-003: provisional rows are never operational until Zaubern finality.
    # LEGACY_ACTIVE preserves the status of rows created before this protocol.
    governance_state = Column(
        String(30), nullable=False, default="LEGACY_ACTIVE", index=True
    )
    governance_draft_id = Column(String(80), index=True)
    governance_draft_version = Column(Integer)
    governance_decision_id = Column(String(80), index=True)
    roster_draft_binding = Column(String(80), index=True)
    preauthorization_receipt_id = Column(String(80), index=True)
    finality_receipt_id = Column(String(80), index=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    postcommit_revision = Column(Integer, nullable=False, default=0)
    postcommit_snapshot_hash = Column(
        String(71), nullable=False, default=lambda: "sha256:" + ("0" * 64)
    )

    # Relationships
    team = relationship("Team", back_populates="players")

    def __repr__(self):
        return f"<Player(id={self.id}, name='{self.first_name} {self.last_name}', team_id={self.team_id})>"

    @property
    def full_name(self):
        """Get full name."""
        return f"{self.first_name} {self.last_name}"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "team_id": str(self.team_id),
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "curp": self.curp,
            "email": self.email,
            "photo_path": self.photo_path,
            "photo_sha256": self.photo_sha256,
            "photo_ahash": self.photo_ahash,
            "ocr_confidence": self.ocr_confidence,
            "needs_review": self.needs_review,
            "verified_by_human": self.verified_by_human,
            "roster_index": self.roster_index,
            "governance_state": self.governance_state,
            "governance_draft_id": self.governance_draft_id,
            "governance_draft_version": self.governance_draft_version,
            "governance_decision_id": self.governance_decision_id,
            "roster_draft_binding": self.roster_draft_binding,
            "preauthorization_receipt_id": self.preauthorization_receipt_id,
            "finality_receipt_id": self.finality_receipt_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OCRRegistration(Base):
    """OCR processing log and registration tracking."""

    __tablename__ = "copa_telmex_ocr_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("copa_telmex_teams.id"), index=True)

    # Telegram metadata
    telegram_chat_id = Column(BigInteger, nullable=False, index=True)
    telegram_user_id = Column(BigInteger)
    telegram_photo_file_id = Column(String(200))

    # OCR results
    ocr_result = Column(JSON)  # Full OCR result from Claude Vision
    validation_result = Column(JSON)  # Validation result from MexicanNamesValidator

    # Processing status
    needs_review = Column(Boolean, default=False)
    review_completed = Column(Boolean, default=False)
    reviewed_at = Column(DateTime)
    review_action = Column(String(50))  # 'confirmed', 'corrected', 'manual_entry'

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    processing_time_ms = Column(Float)  # Time taken to process OCR

    # Relationships
    team = relationship("Team", back_populates="registrations")

    def __repr__(self):
        return f"<OCRRegistration(id={self.id}, chat_id={self.telegram_chat_id}, needs_review={self.needs_review})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "team_id": str(self.team_id) if self.team_id else None,
            "telegram_chat_id": self.telegram_chat_id,
            "needs_review": self.needs_review,
            "review_completed": self.review_completed,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "processing_time_ms": self.processing_time_ms,
        }


class RegistrationReviewSession(Base):
    """Temporary pre-capture review session for roster OCR."""

    __tablename__ = "copa_telmex_registration_review_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    committed_team_id = Column(
        UUID(as_uuid=True), ForeignKey("copa_telmex_teams.id"), index=True
    )

    status = Column(String(30), nullable=False, default="uploaded", index=True)
    source = Column(String(30), nullable=False, default="web")
    provider = Column(String(50), nullable=False, default="local")
    tournament_slug = Column(String(80), index=True)

    telegram_chat_id = Column(BigInteger, index=True)
    telegram_user_id = Column(BigInteger)
    created_by_user_id = Column(String(80))

    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime)
    committed_at = Column(DateTime)

    assets = relationship(
        "RegistrationReviewAsset",
        back_populates="session",
        passive_deletes="all",
        order_by="RegistrationReviewAsset.page_index",
    )
    drafts = relationship(
        "RegistrationReviewDraft",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RegistrationReviewDraft.draft_version",
    )
    ocr_runs = relationship(
        "RegistrationOcrRun",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RegistrationOcrRun.created_at",
    )
    page_append_attempts = relationship(
        "RegistrationPageAppendAttempt",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RegistrationPageAppendAttempt.created_at",
    )
    human_field_edit_proposals = relationship(
        "RegistrationHumanFieldEditProposal",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RegistrationHumanFieldEditProposal.created_at",
    )
    committed_team = relationship("Team")

    @property
    def draft(self):
        """Return the latest immutable draft version loaded for this session."""
        return self.drafts[-1] if self.drafts else None

    def __repr__(self):
        return f"<RegistrationReviewSession(id={self.id}, status={self.status}, provider={self.provider})>"


class RegistrationReviewAsset(Base):
    """Uploaded image asset tied to a review session."""

    __tablename__ = "copa_telmex_registration_review_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        index=True,
    )

    page_index = Column(Integer, nullable=False, default=1)
    image_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), index=True)
    width = Column(Integer)
    height = Column(Integer)
    page_append_attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_page_append_attempts.id"),
        index=True,
    )
    admitted_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
        index=True,
    )
    source_base_draft_id = Column(UUID(as_uuid=True))
    source_base_content_hash = Column(String(71))
    source_ocr_run_ref = Column(String(120))
    admission_operation_id = Column(String(120))
    admission_decision_id = Column(String(71))
    admission_receipt_id = Column(String(120))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("RegistrationReviewSession", back_populates="assets")

    def __repr__(self):
        return f"<RegistrationReviewAsset(id={self.id}, session_id={self.session_id}, page={self.page_index})>"


class RegistrationReviewDraft(Base):
    """One immutable, append-only OCR draft version."""

    __tablename__ = "copa_telmex_registration_review_drafts"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "draft_version",
            name="uq_registration_review_draft_session_version",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        index=True,
    )

    ocr_raw = Column(JSON)
    extraction = Column(JSON)
    validation = Column(JSON)
    review_edits = Column(JSON)
    layout_regions = Column(JSON)
    overall_confidence = Column(Float, default=0.0)
    needs_review = Column(Boolean, default=True)
    # Monotonic compare-and-swap token for governance decisions.
    draft_version = Column(Integer, nullable=False, default=1)
    predecessor_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
        nullable=True,
        index=True,
    )
    predecessor_content_hash = Column(String(71))
    content_hash = Column(String(71), nullable=False)
    mutation_type = Column(String(80), nullable=False)
    mutation_actor_binding = Column(String(71))
    mutation_operation_id = Column(String(80), nullable=False, unique=True)
    mutation_decision_id = Column(String(71), nullable=False)
    mutation_receipt_id = Column(String(120), nullable=False)
    parent_decision_id = Column(String(71))
    parent_receipt_id = Column(String(120))
    page_manifest_hash = Column(String(71))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("RegistrationReviewSession", back_populates="drafts")

    def __repr__(self):
        return f"<RegistrationReviewDraft(id={self.id}, session_id={self.session_id}, needs_review={self.needs_review})>"


class RegistrationOcrRun(Base):
    """Immutable identity and output for one OCR reprocess execution."""

    __tablename__ = "copa_telmex_registration_ocr_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        index=True,
    )
    base_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
        nullable=False,
        index=True,
    )
    base_draft_version = Column(Integer, nullable=False)
    base_content_hash = Column(String(71), nullable=False)
    reprocess_request_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    operation_id = Column(String(71), nullable=False, unique=True)
    run_fingerprint = Column(String(71), nullable=False)
    pipeline_version = Column(String(80), nullable=False)
    provider = Column(String(50), nullable=False)
    model_identity = Column(JSON, nullable=False)
    prompt_config_hash = Column(String(71), nullable=False)
    input_page_bindings = Column(JSON, nullable=False)
    input_page_set_hash = Column(String(71), nullable=False)
    geometry_binding_hash = Column(String(71), nullable=False)
    previous_evidence_set_hash = Column(String(71), nullable=False)
    new_evidence_set_hash = Column(String(71), nullable=False)
    proposed_snapshot_hash = Column(String(71), nullable=False)
    field_diff_set_hash = Column(String(71), nullable=False)
    field_diff_count = Column(Integer, nullable=False)
    material_change_count = Column(Integer, nullable=False)
    proposed_extraction = Column(JSON, nullable=False)
    proposed_ocr_raw = Column(JSON, nullable=False)
    proposed_layout_regions = Column(JSON, nullable=False)
    proposed_validation = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("RegistrationReviewSession", back_populates="ocr_runs")
    field_diffs = relationship(
        "RegistrationOcrFieldDiff",
        back_populates="ocr_run",
        cascade="all, delete-orphan",
        order_by="RegistrationOcrFieldDiff.field_path",
    )
    decision = relationship(
        "RegistrationOcrReprocessDecision",
        back_populates="ocr_run",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RegistrationOcrFieldDiff(Base):
    """Immutable field-level comparison between the base draft and an OCR run."""

    __tablename__ = "copa_telmex_registration_ocr_field_diffs"
    __table_args__ = (
        UniqueConstraint(
            "ocr_run_id",
            "field_path",
            name="uq_registration_ocr_diff_run_field",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocr_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_ocr_runs.id"),
        nullable=False,
        index=True,
    )
    field_path = Column(String(160), nullable=False)
    player_slot = Column(Integer)
    source_page = Column(Integer)
    classification = Column(String(50), nullable=False)
    previous_value = Column(JSON)
    proposed_value = Column(JSON)
    previous_value_present = Column(Boolean, nullable=False)
    proposed_value_present = Column(Boolean, nullable=False)
    previous_value_binding = Column(String(71), nullable=False)
    proposed_value_binding = Column(String(71), nullable=False)
    previous_normalized_value_binding = Column(String(71), nullable=False)
    proposed_normalized_value_binding = Column(String(71), nullable=False)
    previous_evidence_binding = Column(String(71), nullable=False)
    new_evidence_binding = Column(String(71), nullable=False)
    evidence_binding_changed = Column(Boolean, nullable=False, default=False)
    requires_review = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    ocr_run = relationship("RegistrationOcrRun", back_populates="field_diffs")


class RegistrationOcrReprocessDecision(Base):
    """Receipt-bound deterministic adjudication for one immutable OCR run."""

    __tablename__ = "copa_telmex_registration_ocr_reprocess_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocr_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_ocr_runs.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    successor_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
    )
    decision_id = Column(String(71), nullable=False, unique=True)
    policy_hash = Column(String(71), nullable=False)
    decision = Column(String(50), nullable=False)
    reason_codes = Column(JSON, nullable=False)
    receipt_id = Column(String(120), nullable=False)
    event_hash = Column(String(71), nullable=False)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    ocr_run = relationship("RegistrationOcrRun", back_populates="decision")


class RegistrationPageAppendAttempt(Base):
    """Immutable proposed composition of existing and newly uploaded pages."""

    __tablename__ = "copa_telmex_registration_page_append_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        index=True,
    )
    page_append_request_id = Column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    base_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
        nullable=False,
        index=True,
    )
    base_draft_version = Column(Integer, nullable=False)
    base_content_hash = Column(String(71), nullable=False)
    declared_base_page_manifest_hash = Column(String(71))
    operation_id = Column(String(71), nullable=False, unique=True)
    append_ocr_run_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    pipeline_version = Column(String(80), nullable=False)
    provider = Column(String(50), nullable=False)
    model_identity = Column(JSON, nullable=False)
    prompt_config_hash = Column(String(71), nullable=False)
    existing_page_manifest = Column(JSON, nullable=False)
    existing_page_manifest_hash = Column(String(71), nullable=False)
    appended_page_manifest = Column(JSON, nullable=False)
    appended_page_manifest_hash = Column(String(71), nullable=False)
    proposed_page_manifest = Column(JSON, nullable=False)
    proposed_page_manifest_hash = Column(String(71), nullable=False)
    proposed_snapshot_hash = Column(String(71), nullable=False)
    base_player_set_hash = Column(String(71), nullable=False)
    incoming_player_set_hash = Column(String(71), nullable=False)
    proposed_player_set_hash = Column(String(71), nullable=False)
    incoming_extraction = Column(JSON, nullable=False)
    incoming_ocr_raw = Column(JSON, nullable=False)
    incoming_layout_regions = Column(JSON, nullable=False)
    proposed_extraction = Column(JSON, nullable=False)
    proposed_ocr_raw = Column(JSON, nullable=False)
    proposed_layout_regions = Column(JSON, nullable=False)
    proposed_validation = Column(JSON, nullable=False)
    staged_assets = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship(
        "RegistrationReviewSession", back_populates="page_append_attempts"
    )
    decision = relationship(
        "RegistrationPageAppendDecision",
        back_populates="attempt",
        uselist=False,
        cascade="all, delete-orphan",
    )


class RegistrationPageAppendDecision(Base):
    """Receipt-bound decision for one immutable page composition attempt."""

    __tablename__ = "copa_telmex_registration_page_append_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_append_attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_page_append_attempts.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    successor_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
    )
    decision_id = Column(String(71), nullable=False, unique=True)
    policy_hash = Column(String(71), nullable=False)
    decision = Column(String(60), nullable=False)
    reason_codes = Column(JSON, nullable=False)
    receipt_id = Column(String(120), nullable=False)
    event_hash = Column(String(71), nullable=False)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    attempt = relationship(
        "RegistrationPageAppendAttempt", back_populates="decision"
    )


class RegistrationHumanFieldEditProposal(Base):
    """Immutable exact successor proposed through human field review."""

    __tablename__ = "copa_telmex_registration_human_field_edit_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        index=True,
    )
    edit_request_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    base_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_drafts.id"),
        nullable=False,
        index=True,
    )
    base_draft_version = Column(Integer, nullable=False)
    base_draft_hash = Column(String(71), nullable=False)
    proposed_successor_draft_id = Column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    proposed_successor_hash = Column(String(71), nullable=False)
    operation_id = Column(String(71), nullable=False, unique=True)
    tournament_slug = Column(String(80), nullable=False)
    registration_subject_binding = Column(String(80), nullable=False)
    proposed_values = Column(JSON, nullable=False)
    resolutions = Column(JSON, nullable=False)
    field_resolution_set_hash = Column(String(71), nullable=False)
    required_blocking_diff_ids = Column(JSON, nullable=False)
    required_blocking_diff_set_hash = Column(String(71), nullable=False)
    approval_set_hash = Column(String(71), nullable=False)
    proposer_principal_id = Column(String(120), nullable=False)
    proposer_role = Column(String(60), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship(
        "RegistrationReviewSession", back_populates="human_field_edit_proposals"
    )
    approvals = relationship(
        "RegistrationHumanFieldApproval",
        back_populates="proposal",
        cascade="all, delete-orphan",
        order_by="RegistrationHumanFieldApproval.field_path",
    )
    decision = relationship(
        "RegistrationHumanFieldEditDecision",
        back_populates="proposal",
        cascade="all, delete-orphan",
        uselist=False,
    )
    execution = relationship(
        "RegistrationHumanFieldEditExecution",
        back_populates="proposal",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RegistrationHumanFieldApproval(Base):
    """Immutable field-bound human attestation; consumption is a separate row."""

    __tablename__ = "copa_telmex_registration_human_field_approvals"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "field_path",
            name="uq_registration_human_approval_proposal_field",
        ),
        UniqueConstraint(
            "nonce", name="uq_registration_human_approval_nonce"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_edit_proposals.id"),
        nullable=False,
        index=True,
    )
    nonce = Column(String(120), nullable=False)
    roster_entry_id = Column(UUID(as_uuid=True), index=True)
    player_slot = Column(Integer)
    field_path = Column(String(200), nullable=False)
    resolution_type = Column(String(60), nullable=False)
    evidence_class = Column(String(60), nullable=False)
    previous_value_binding = Column(String(80), nullable=False)
    previous_normalized_value_binding = Column(String(80), nullable=False)
    proposed_value_binding = Column(String(80), nullable=False)
    proposed_normalized_value_binding = Column(String(80), nullable=False)
    source_page_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_assets.id"),
    )
    source_page_hash = Column(String(71))
    normalized_page_hash = Column(String(71))
    coordinate_frame_hash = Column(String(71))
    crop_coordinates = Column(JSON)
    crop_hash = Column(String(71))
    ocr_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_ocr_runs.id"),
    )
    reprocess_decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_ocr_reprocess_decisions.id"),
    )
    field_diff_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_ocr_field_diffs.id"),
    )
    classification = Column(String(60))
    approver_principal_id = Column(String(120), nullable=False)
    approver_role = Column(String(60), nullable=False)
    role_assignment_id = Column(String(160), nullable=False)
    authorization_epoch = Column(String(160), nullable=False)
    authentication_method = Column(String(80), nullable=False)
    authentication_assurance_level = Column(Integer, nullable=False)
    auth_context_id = Column(String(160), nullable=False)
    issued_at = Column(DateTime, nullable=False)
    not_before = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    proposal = relationship(
        "RegistrationHumanFieldEditProposal", back_populates="approvals"
    )
    consumption = relationship(
        "RegistrationHumanFieldApprovalConsumption",
        back_populates="approval",
        uselist=False,
    )


class RegistrationHumanFieldEditDecision(Base):
    """Receipt-bound deterministic decision for one human edit proposal."""

    __tablename__ = "copa_telmex_registration_human_field_edit_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_edit_proposals.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    decision_id = Column(String(71), nullable=False, unique=True)
    policy_hash = Column(String(71), nullable=False)
    decision = Column(String(60), nullable=False)
    reason_codes = Column(JSON, nullable=False)
    receipt_id = Column(String(120), nullable=False)
    receipt_alg = Column(String(30), nullable=False)
    event_hash = Column(String(71), nullable=False)
    decision_document = Column(JSON, nullable=False)
    receipt_document = Column(JSON, nullable=False)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    proposal = relationship(
        "RegistrationHumanFieldEditProposal", back_populates="decision"
    )


class RegistrationHumanFieldEditExecution(Base):
    """Atomic binding between one authorized proposal and its REG-S02 successor."""

    __tablename__ = "copa_telmex_registration_human_field_edit_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_edit_proposals.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_edit_decisions.id"),
        nullable=False,
        unique=True,
    )
    successor_draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "copa_telmex_registration_review_drafts.id",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        unique=True,
        index=True,
    )
    successor_draft_version = Column(Integer, nullable=False)
    successor_hash = Column(String(71), nullable=False)
    parent_decision_id = Column(String(71), nullable=False)
    parent_receipt_id = Column(String(120), nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    proposal = relationship(
        "RegistrationHumanFieldEditProposal", back_populates="execution"
    )
    consumptions = relationship(
        "RegistrationHumanFieldApprovalConsumption",
        back_populates="execution",
        cascade="all, delete-orphan",
    )


class RegistrationHumanFieldApprovalConsumption(Base):
    """One-time immutable consumption of an approval by an exact execution."""

    __tablename__ = "copa_telmex_registration_human_field_approval_consumptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    approval_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_approvals.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_human_field_edit_executions.id"),
        nullable=False,
        index=True,
    )
    consumed_by_principal_id = Column(String(120), nullable=False)
    consumed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    consumed_by_draft_version = Column(Integer, nullable=False)
    consumed_by_successor_hash = Column(String(71), nullable=False)

    approval = relationship(
        "RegistrationHumanFieldApproval", back_populates="consumption"
    )
    execution = relationship(
        "RegistrationHumanFieldEditExecution", back_populates="consumptions"
    )


class RegistrationPostcommitMutationProposal(Base):
    """Immutable exact successor proposed for a committed Team or Player."""

    __tablename__ = "copa_telmex_registration_postcommit_mutation_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    mutation_request_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    entity_type = Column(String(20), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    team_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    mutation_type = Column(String(40), nullable=False)
    base_revision = Column(Integer, nullable=False)
    proposed_revision = Column(Integer, nullable=False)
    base_snapshot = Column(JSON, nullable=False)
    base_snapshot_hash = Column(String(71), nullable=False)
    proposed_snapshot = Column(JSON, nullable=False)
    proposed_snapshot_hash = Column(String(71), nullable=False)
    field_changes = Column(JSON, nullable=False)
    field_change_set_hash = Column(String(71), nullable=False)
    mutation_reason = Column(Text, nullable=False)
    mutation_reason_binding = Column(String(80), nullable=False)
    source_evidence_binding = Column(String(80), nullable=False)
    proposer_principal_id = Column(String(120), nullable=False)
    proposer_role = Column(String(60), nullable=False)
    role_assignment_id = Column(String(160), nullable=False)
    authorization_epoch = Column(String(160), nullable=False)
    authentication_method = Column(String(80), nullable=False)
    authentication_assurance_level = Column(Integer, nullable=False)
    auth_context_id = Column(String(160), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RegistrationPostcommitMutationDecision(Base):
    """Ed25519 receipt-bound preauthorization for one exact successor."""

    __tablename__ = "copa_telmex_registration_postcommit_mutation_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_postcommit_mutation_proposals.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    decision_id = Column(String(71), nullable=False, unique=True)
    policy_hash = Column(String(71), nullable=False)
    decision = Column(String(60), nullable=False)
    reason_codes = Column(JSON, nullable=False)
    receipt_id = Column(String(120), nullable=False)
    receipt_alg = Column(String(30), nullable=False)
    event_hash = Column(String(71), nullable=False)
    decision_document = Column(JSON, nullable=False)
    receipt_document = Column(JSON, nullable=False)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RegistrationPostcommitMutationExecution(Base):
    """Atomic projection and post-execution attestation for REG-S07."""

    __tablename__ = "copa_telmex_registration_postcommit_mutation_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_postcommit_mutation_proposals.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_postcommit_mutation_decisions.id"),
        nullable=False,
        unique=True,
    )
    database_transaction_id = Column(String(120), nullable=False, unique=True)
    attestation_id = Column(String(71), nullable=False, unique=True)
    attestation_hash = Column(String(71), nullable=False)
    finality_receipt_id = Column(String(120), nullable=False)
    finality_receipt_alg = Column(String(30), nullable=False)
    finality_event_document = Column(JSON, nullable=False)
    finality_receipt_document = Column(JSON, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RegistrationPostcommitEntityVersion(Base):
    """Append-only committed-state history for Team and Player projections."""

    __tablename__ = "copa_telmex_registration_postcommit_entity_versions"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "revision",
            name="uq_registration_postcommit_entity_revision",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_type = Column(String(20), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    team_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    snapshot_hash = Column(String(71), nullable=False)
    predecessor_snapshot_hash = Column(String(71))
    mutation_type = Column(String(40), nullable=False)
    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_postcommit_mutation_executions.id"),
        unique=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ValidationLog(Base):
    """Log of human validation actions."""

    __tablename__ = "copa_telmex_validation_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    registration_id = Column(
        UUID(as_uuid=True), ForeignKey("copa_telmex_ocr_registrations.id"), index=True
    )

    # What was validated
    field_name = Column(String(50))  # 'player_name', 'team_name', etc.
    original_value = Column(Text)
    corrected_value = Column(Text)
    validation_action = Column(String(50))  # 'accepted', 'corrected', 'suggestion_used'

    # Metadata
    validated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    telegram_chat_id = Column(BigInteger)

    def __repr__(self):
        return f"<ValidationLog(id={self.id}, field='{self.field_name}', action='{self.validation_action}')>"
