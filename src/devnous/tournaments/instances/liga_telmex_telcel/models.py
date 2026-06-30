"""
SQLAlchemy models for Liga Telmex Telcel baseball tournament.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, String, Float, JSON, BigInteger, Text, Integer, Numeric
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class BaseballTeam(Base):
    """Baseball team registration model for Liga Telmex Telcel."""
    __tablename__ = 'liga_telmex_telcel_teams'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), nullable=False, index=True)
    tournament_slug = Column(String(80), default='liga_telmex_telcel', index=True)
    gender = Column(String(10), default='varonil')  # varonil, femenil
    category = Column(String(20), nullable=False)  # "13 años varonil", "14 años varonil"
    league = Column(String(100))  # Liga de origen
    league_phone = Column(String(20))  # Teléfono de la liga
    league_address = Column(String(300))  # Domicilio de la liga
    representative_name = Column(String(200))  # Nombre del representante
    contact_email = Column(String(150))  # Email de contacto
    contact_phone = Column(String(20))  # Teléfono de contacto
    state = Column(String(50))  # Estado
    municipality = Column(String(100))  # Municipio

    # Tournament specific
    team_number = Column(Integer)  # Número de equipo en el torneo
    registration_stage = Column(String(50))  # Etapa de registro
    payment_status = Column(String(20), default='pending')  # pending, paid, waived
    payment_amount = Column(Numeric(10, 2))  # Monto pagado
    payment_date = Column(DateTime)  # Fecha de pago
    payment_reference = Column(String(100))  # Referencia de pago

    # OCR image
    roster_image_path = Column(String(500))  # Path to roster OCR image file

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    verification_date = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Telegram tracking
    telegram_chat_id = Column(BigInteger, index=True)
    telegram_user_id = Column(BigInteger)

    # Relationships
    players = relationship("BaseballPlayer", back_populates="team", cascade="all, delete-orphan")
    registrations = relationship("LigaOCRRegistration", back_populates="team")

    def __repr__(self):
        return f"<BaseballTeam(id={self.id}, name='{self.name}', category='{self.category}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': str(self.id),
            'name': self.name,
            'gender': self.gender,
            'category': self.category,
            'league': self.league,
            'representative_name': self.representative_name,
            'state': self.state,
            'municipality': self.municipality,
            'team_number': self.team_number,
            'payment_status': self.payment_status,
            'payment_amount': float(self.payment_amount) if self.payment_amount else None,
            'is_active': self.is_active,
            'is_verified': self.is_verified,
            'roster_image_path': self.roster_image_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class BaseballPlayer(Base):
    """Baseball player registration model for Liga Telmex Telcel."""
    __tablename__ = 'liga_telmex_telcel_players'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey('liga_telmex_telcel_teams.id'), nullable=False, index=True)

    # Player info
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    birth_date = Column(Date)
    curp = Column(String(18), unique=True, index=True)  # CURP is unique
    email = Column(String(150))
    phone = Column(String(20))  # Teléfono del jugador

    # Baseball specific
    jersey_number = Column(Integer)  # Número de uniforme
    position = Column(String(50))  # Posición principal
    batting_order = Column(Integer)  # Orden al bate
    throwing_hand = Column(String(10))  # Derecho, Izquierdo, Ambidiestro
    batting_hand = Column(String(10))  # Derecho, Izquierdo, Ambidiestro

    # Documentation
    photo_path = Column(String(500))  # Path to player photo file
    photo_data = Column(Text)  # Base64 encoded photo data (optional)
    photo_sha256 = Column(String(64), index=True)
    photo_ahash = Column(String(16), index=True)
    birth_certificate_path = Column(String(500))  # Acta de nacimiento
    curp_document_path = Column(String(500))  # Documento de CURP
    medical_certificate_path = Column(String(500))  # Certificado médico

    # CURP validation
    curp_valid = Column(Boolean, default=False)
    curp_validation_date = Column(DateTime)
    curp_validation_errors = Column(Text)  # JSON string with validation errors

    # Age verification
    age_verified = Column(Boolean, default=False)
    age_verification_date = Column(DateTime)
    age_category_compliant = Column(Boolean, default=True)  # Cumple con la categoría de edad

    # OCR metadata
    ocr_confidence = Column(Float)  # OCR confidence score
    needs_review = Column(Boolean, default=False)
    verified_by_human = Column(Boolean, default=False)
    verification_notes = Column(Text)
    roster_index = Column(Integer)  # 1-based order in roster

    # Status
    is_active = Column(Boolean, default=True)
    is_eligible = Column(Boolean, default=True)  # Elegible para jugar
    ineligibility_reason = Column(Text)  # Razón de inelegibilidad

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    team = relationship("BaseballTeam", back_populates="players")

    def __repr__(self):
        return f"<BaseballPlayer(id={self.id}, name='{self.first_name} {self.last_name}', team_id={self.team_id})>"

    @property
    def full_name(self):
        """Get full name."""
        return f"{self.first_name} {self.last_name}"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': str(self.id),
            'team_id': str(self.team_id),
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'birth_date': self.birth_date.isoformat() if self.birth_date else None,
            'curp': self.curp,
            'email': self.email,
            'phone': self.phone,
            'jersey_number': self.jersey_number,
            'position': self.position,
            'batting_order': self.batting_order,
            'throwing_hand': self.throwing_hand,
            'batting_hand': self.batting_hand,
            'photo_path': self.photo_path,
            'photo_sha256': self.photo_sha256,
            'photo_ahash': self.photo_ahash,
            'curp_valid': self.curp_valid,
            'age_verified': self.age_verified,
            'age_category_compliant': self.age_category_compliant,
            'ocr_confidence': self.ocr_confidence,
            'needs_review': self.needs_review,
            'verified_by_human': self.verified_by_human,
            'roster_index': self.roster_index,
            'is_active': self.is_active,
            'is_eligible': self.is_eligible,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class LigaOCRRegistration(Base):
    """OCR processing log and registration tracking for Liga Telmex Telcel."""
    __tablename__ = 'liga_telmex_telcel_ocr_registrations'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey('liga_telmex_telcel_teams.id'), index=True)

    # Telegram metadata
    telegram_chat_id = Column(BigInteger, nullable=False, index=True)
    telegram_user_id = Column(BigInteger)
    telegram_photo_file_id = Column(String(200))

    # OCR results
    ocr_result = Column(JSON)  # Full OCR result from Claude Vision
    ocr_provider = Column(String(50))  # claude_vision, openai_vision, local_ocr, etc.
    ocr_confidence = Column(Float)  # Overall OCR confidence

    # Validation results
    validation_result = Column(JSON)  # Validation result from MexicanNamesValidator
    needs_review = Column(Boolean, default=False)  # Whether registration needs human review

    # Review status
    review_completed = Column(Boolean, default=False)
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String(100))  # Admin who reviewed
    review_action = Column(String(50))  # confirmed, corrected, manual_entry, rejected

    # Processing metadata
    processing_time_ms = Column(Float)  # Processing time in milliseconds
    error_message = Column(Text)  # Error if processing failed

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    team = relationship("BaseballTeam", back_populates="registrations")

    def __repr__(self):
        return f"<LigaOCRRegistration(id={self.id}, team_id={self.team_id}, chat_id={self.telegram_chat_id})>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': str(self.id),
            'team_id': str(self.team_id),
            'telegram_chat_id': self.telegram_chat_id,
            'telegram_user_id': self.telegram_user_id,
            'ocr_provider': self.ocr_provider,
            'ocr_confidence': self.ocr_confidence,
            'needs_review': self.needs_review,
            'review_completed': self.review_completed,
            'review_action': self.review_action,
            'processing_time_ms': self.processing_time_ms,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class TournamentStage(Base):
    """Tournament stage tracking for Liga Telmex Telcel."""
    __tablename__ = 'liga_telmex_telcel_stages'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    stage_id = Column(String(50), unique=True, nullable=False)  # convenio, fase_colectiva, etc.
    name = Column(String(100), nullable=False)
    description = Column(Text)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(String(20), default='planned')  # planned, active, completed, cancelled
    location = Column(String(200))
    is_final = Column(Boolean, default=False)  # Si es la etapa final
    is_awards = Column(Boolean, default=False)  # Si es etapa de premios

    # Statistics
    teams_registered = Column(Integer, default=0)
    players_registered = Column(Integer, default=0)
    matches_played = Column(Integer, default=0)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<TournamentStage(id={self.id}, stage_id='{self.stage_id}', name='{self.name}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': str(self.id),
            'stage_id': self.stage_id,
            'name': self.name,
            'description': self.description,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'status': self.status,
            'location': self.location,
            'is_final': self.is_final,
            'is_awards': self.is_awards,
            'teams_registered': self.teams_registered,
            'players_registered': self.players_registered,
            'matches_played': self.matches_played,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Sponsorship(Base):
    """Sponsorship tracking for Liga Telmex Telcel."""
    __tablename__ = 'liga_telmex_telcel_sponsorships'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sponsor_name = Column(String(200), nullable=False)
    sponsor_type = Column(String(50))  # company, individual, organization
    contact_person = Column(String(200))
    contact_email = Column(String(150))
    contact_phone = Column(String(20))

    # Sponsorship details
    tier = Column(String(20))  # platinum, gold, silver, bronze
    amount = Column(Numeric(10, 2))
    currency = Column(String(3), default='MXN')
    benefits = Column(JSON)  # List of benefits provided

    # Payment
    payment_status = Column(String(20), default='pending')  # pending, paid, cancelled
    payment_date = Column(DateTime)
    payment_reference = Column(String(100))

    # Status
    is_active = Column(Boolean, default=True)
    contract_signed = Column(Boolean, default=False)
    contract_date = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Sponsorship(id={self.id}, sponsor_name='{self.sponsor_name}', tier='{self.tier}')>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': str(self.id),
            'sponsor_name': self.sponsor_name,
            'sponsor_type': self.sponsor_type,
            'contact_person': self.contact_person,
            'tier': self.tier,
            'amount': float(self.amount) if self.amount else None,
            'currency': self.currency,
            'payment_status': self.payment_status,
            'is_active': self.is_active,
            'contract_signed': self.contract_signed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }