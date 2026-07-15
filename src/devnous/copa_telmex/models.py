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
        cascade="all, delete-orphan",
        order_by="RegistrationReviewAsset.page_index",
    )
    draft = relationship(
        "RegistrationReviewDraft",
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
    )
    committed_team = relationship("Team")

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("RegistrationReviewSession", back_populates="assets")

    def __repr__(self):
        return f"<RegistrationReviewAsset(id={self.id}, session_id={self.session_id}, page={self.page_index})>"


class RegistrationReviewDraft(Base):
    """Current OCR draft and operator edits for a review session."""

    __tablename__ = "copa_telmex_registration_review_drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copa_telmex_registration_review_sessions.id"),
        nullable=False,
        unique=True,
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("RegistrationReviewSession", back_populates="draft")

    def __repr__(self):
        return f"<RegistrationReviewDraft(id={self.id}, session_id={self.session_id}, needs_review={self.needs_review})>"


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
