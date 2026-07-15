"""
Database operations for Copa Telmex registration system.
"""
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Team, Player, OCRRegistration, ValidationLog

logger = logging.getLogger(__name__)


class CopaTelmexDB:
    """Database operations for Copa Telmex."""

    def __init__(self, session: AsyncSession):
        """
        Initialize with database session.

        Args:
            session: SQLAlchemy async session
        """
        self.session = session

    # ============================================================================
    # TEAM OPERATIONS
    # ============================================================================

    async def create_team(
        self,
        name: str,
        telegram_chat_id: int,
        team_id: Optional[UUID] = None,
        tournament_slug: Optional[str] = None,
        gender: Optional[str] = None,
        category: Optional[str] = None,
        league: Optional[str] = None,
        representative_name: Optional[str] = None,
        state: Optional[str] = None,
        municipality: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
        roster_image_path: Optional[str] = None
    ) -> Team:
        """
        Create a new team.

        Args:
            name: Team name
            telegram_chat_id: Telegram chat ID
            gender: varonil/femenil
            category: U10, U12, etc.
            league: League name
            representative_name: Representative's full name
            state: State
            municipality: Municipality
            telegram_user_id: Telegram user ID
            roster_image_path: Path to roster OCR image file

        Returns:
            Created Team object
        """
        team_fields = dict(
            name=name,
            tournament_slug=tournament_slug,
            gender=gender,
            category=category,
            league=league,
            representative_name=representative_name,
            state=state,
            municipality=municipality,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
            roster_image_path=roster_image_path
        )
        if team_id is not None:
            team_fields["id"] = team_id
        team = Team(**team_fields)

        self.session.add(team)
        await self.session.flush()  # Get the ID without committing

        logger.info(f"✅ Team created: {team.name} (ID: {team.id})")
        return team

    async def get_team_by_id(self, team_id: UUID) -> Optional[Team]:
        """Get team by ID."""
        result = await self.session.execute(
            select(Team).where(Team.id == team_id)
        )
        return result.scalar_one_or_none()

    async def get_team_by_name(
        self,
        name: str,
        category: Optional[str] = None,
        telegram_chat_id: Optional[int] = None,
        tournament_slug: Optional[str] = None,
    ) -> Optional[Team]:
        """
        Get team by name and optionally category and chat.

        Args:
            name: Team name
            category: Optional category filter
            telegram_chat_id: Optional chat ID filter (recommended to avoid duplicates)

        Returns:
            Team or None
        """
        query = select(Team).where(Team.name == name)
        if category:
            query = query.where(Team.category == category)
        if telegram_chat_id is not None:
            query = query.where(Team.telegram_chat_id == telegram_chat_id)
        if tournament_slug:
            query = query.where(Team.tournament_slug == tournament_slug)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_teams_by_chat(self, telegram_chat_id: int) -> List[Team]:
        """Get all teams registered by a Telegram chat."""
        result = await self.session.execute(
            select(Team).where(Team.telegram_chat_id == telegram_chat_id).order_by(desc(Team.created_at))
        )
        return list(result.scalars().all())

    async def get_latest_team_by_chat(self, telegram_chat_id: int) -> Optional[Team]:
        """Get the most recent team for a chat."""
        result = await self.session.execute(
            select(Team)
            .where(Team.telegram_chat_id == telegram_chat_id)
            .order_by(desc(Team.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_team(self, team_id: UUID, **fields: Any) -> Optional[Team]:
        """Update a team with the provided fields."""
        team = await self.get_team_by_id(team_id)
        if not team:
            return None

        for key, value in fields.items():
            if not hasattr(team, key):
                continue
            setattr(team, key, value)

        await self.session.flush()
        logger.info(f"✅ Team updated: {team.name} (ID: {team.id})")
        return team

    # ============================================================================
    # PLAYER OPERATIONS
    # ============================================================================

    async def create_player(
        self,
        team_id: UUID,
        first_name: str,
        last_name: str,
        birth_date: Optional[date] = None,
        curp: Optional[str] = None,
        email: Optional[str] = None,
        photo_path: Optional[str] = None,
        photo_data: Optional[str] = None,
        photo_sha256: Optional[str] = None,
        photo_ahash: Optional[str] = None,
        ocr_confidence: Optional[float] = None,
        needs_review: bool = False,
        verified_by_human: bool = False,
        verification_notes: Optional[str] = None,
        roster_index: Optional[int] = None,
        governance_state: str = "LEGACY_ACTIVE",
        governance_draft_id: Optional[str] = None,
        governance_draft_version: Optional[int] = None,
        governance_decision_id: Optional[str] = None,
        roster_draft_binding: Optional[str] = None,
        preauthorization_receipt_id: Optional[str] = None,
    ) -> Player:
        """
        Create a new player.

        Args:
            team_id: Team ID
            first_name: Player's first name(s)
            last_name: Player's last name(s)
            birth_date: Date of birth
            curp: CURP (Clave Única de Registro de Población)
            email: Email address
            photo_path: Path to player photo file
            photo_data: Base64 encoded photo data (optional)
            photo_sha256: SHA-256 fingerprint of extracted player photo
            photo_ahash: Average hash fingerprint of extracted player photo
            ocr_confidence: OCR confidence score
            needs_review: Whether player needs human review
            verified_by_human: Whether player was verified by human
            verification_notes: Notes from verification

        Returns:
            Created Player object
        """
        player = Player(
            team_id=team_id,
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            curp=curp,
            email=email,
            photo_path=photo_path,
            photo_data=photo_data,
            photo_sha256=photo_sha256,
            photo_ahash=photo_ahash,
            ocr_confidence=ocr_confidence,
            needs_review=needs_review,
            verified_by_human=verified_by_human,
            verification_notes=verification_notes,
            roster_index=roster_index,
            governance_state=governance_state,
            governance_draft_id=governance_draft_id,
            governance_draft_version=governance_draft_version,
            governance_decision_id=governance_decision_id,
            roster_draft_binding=roster_draft_binding,
            preauthorization_receipt_id=preauthorization_receipt_id,
        )

        self.session.add(player)
        await self.session.flush()

        logger.info(f"✅ Player created: {player.full_name} (ID: {player.id}, Team: {team_id})")
        return player

    async def get_player_by_id(self, player_id: UUID) -> Optional[Player]:
        """Get player by ID."""
        result = await self.session.execute(
            select(Player).where(Player.id == player_id)
        )
        return result.scalar_one_or_none()

    async def get_player_by_curp(self, curp: str) -> Optional[Player]:
        """Get player by CURP."""
        result = await self.session.execute(
            select(Player).where(Player.curp == curp)
        )
        return result.scalar_one_or_none()

    async def get_player_by_team_and_identity(
        self,
        team_id: UUID,
        first_name: str,
        last_name: str,
        birth_date: Optional[date] = None,
    ) -> Optional[Player]:
        """Best-effort dedupe for players without CURP."""
        query = select(Player).where(
            and_(
                Player.team_id == team_id,
                Player.first_name == first_name,
                Player.last_name == last_name,
            )
        )
        if birth_date is not None:
            query = query.where(Player.birth_date == birth_date)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_players_by_team(
        self, team_id: UUID, *, include_provisional: bool = False
    ) -> List[Player]:
        """Get operational players; provisional governance rows are excluded."""
        query = select(Player).where(Player.team_id == team_id)
        if not include_provisional:
            query = query.where(Player.governance_state.in_(("ACTIVE", "LEGACY_ACTIVE")))
        result = await self.session.execute(
            query.order_by(Player.created_at)
        )
        return list(result.scalars().all())

    async def update_player(self, player_id: UUID, **fields: Any) -> Optional[Player]:
        """Update a player with the provided fields."""
        player = await self.session.get(Player, player_id)
        if not player:
            return None

        for key, value in fields.items():
            if not hasattr(player, key):
                continue
            setattr(player, key, value)

        await self.session.flush()
        logger.info(f"✅ Player updated: {player.full_name} (ID: {player.id})")
        return player

    async def delete_player(self, player_id: UUID) -> bool:
        """Delete a player by ID."""
        player = await self.session.get(Player, player_id)
        if not player:
            return False
        await self.session.delete(player)
        await self.session.flush()
        logger.info(f"🗑️ Player deleted: {player_id}")
        return True

    async def get_players_needing_review(self, team_id: Optional[UUID] = None) -> List[Player]:
        """
        Get players that need human review.

        Args:
            team_id: Optional team ID filter

        Returns:
            List of players needing review
        """
        query = select(Player).where(
            and_(
                Player.needs_review == True,
                Player.verified_by_human == False
            )
        )

        if team_id:
            query = query.where(Player.team_id == team_id)

        result = await self.session.execute(query.order_by(Player.created_at))
        return list(result.scalars().all())

    # ============================================================================
    # OCR REGISTRATION OPERATIONS
    # ============================================================================

    async def create_ocr_registration(
        self,
        telegram_chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Dict[str, Any],
        team_id: Optional[UUID] = None,
        telegram_user_id: Optional[int] = None,
        telegram_photo_file_id: Optional[str] = None,
        processing_time_ms: Optional[float] = None
    ) -> OCRRegistration:
        """
        Create OCR registration log.

        Args:
            telegram_chat_id: Telegram chat ID
            ocr_result: Full OCR result from Claude Vision
            validation_result: Validation result from MexicanNamesValidator
            team_id: Optional team ID if already created
            telegram_user_id: Telegram user ID
            telegram_photo_file_id: Telegram photo file ID
            processing_time_ms: Processing time in milliseconds

        Returns:
            Created OCRRegistration object
        """
        needs_review = validation_result.get('needs_human_review', False)

        registration = OCRRegistration(
            team_id=team_id,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
            telegram_photo_file_id=telegram_photo_file_id,
            ocr_result=ocr_result,
            validation_result=validation_result,
            needs_review=needs_review,
            processing_time_ms=processing_time_ms
        )

        self.session.add(registration)
        await self.session.flush()

        logger.info(f"✅ OCR registration created: {registration.id} (needs_review: {needs_review})")
        return registration

    async def mark_registration_reviewed(
        self,
        registration_id: UUID,
        review_action: str,
        team_id: Optional[UUID] = None
    ):
        """
        Mark registration as reviewed.

        Args:
            registration_id: Registration ID
            review_action: Action taken (e.g., 'confirmed', 'corrected', 'manual_entry')
            team_id: Team ID if created during review
        """
        registration = await self.session.get(OCRRegistration, registration_id)
        if registration:
            registration.review_completed = True
            registration.reviewed_at = datetime.utcnow()
            registration.review_action = review_action
            if team_id:
                registration.team_id = team_id

            logger.info(f"✅ Registration {registration_id} marked as reviewed: {review_action}")

    async def get_registrations_by_chat(
        self,
        telegram_chat_id: int,
        limit: int = 100
    ) -> List[OCRRegistration]:
        """Get recent registrations by chat ID."""
        result = await self.session.execute(
            select(OCRRegistration)
            .where(OCRRegistration.telegram_chat_id == telegram_chat_id)
            .order_by(desc(OCRRegistration.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_registrations_needing_review(self, limit: int = 100) -> List[OCRRegistration]:
        """Get registrations needing review."""
        result = await self.session.execute(
            select(OCRRegistration)
            .where(
                and_(
                    OCRRegistration.needs_review == True,
                    OCRRegistration.review_completed == False
                )
            )
            .order_by(OCRRegistration.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    # ============================================================================
    # VALIDATION LOG OPERATIONS
    # ============================================================================

    async def log_validation(
        self,
        registration_id: UUID,
        field_name: str,
        original_value: str,
        corrected_value: str,
        validation_action: str,
        telegram_chat_id: int
    ) -> ValidationLog:
        """
        Log a human validation action.

        Args:
            registration_id: OCR registration ID
            field_name: Field that was validated
            original_value: Original OCR value
            corrected_value: Corrected value
            validation_action: Action taken
            telegram_chat_id: Telegram chat ID

        Returns:
            Created ValidationLog object
        """
        log = ValidationLog(
            registration_id=registration_id,
            field_name=field_name,
            original_value=original_value,
            corrected_value=corrected_value,
            validation_action=validation_action,
            telegram_chat_id=telegram_chat_id
        )

        self.session.add(log)
        await self.session.flush()

        logger.info(f"✅ Validation logged: {field_name} ({validation_action})")
        return log

    # ============================================================================
    # STATISTICS AND REPORTING
    # ============================================================================

    async def get_registration_stats(self) -> Dict[str, Any]:
        """Get registration statistics."""
        from sqlalchemy import func

        # Total teams
        teams_result = await self.session.execute(select(func.count(Team.id)))
        total_teams = teams_result.scalar()

        # Total players
        players_result = await self.session.execute(select(func.count(Player.id)))
        total_players = players_result.scalar()

        # Players needing review
        review_result = await self.session.execute(
            select(func.count(Player.id)).where(
                and_(
                    Player.needs_review == True,
                    Player.verified_by_human == False
                )
            )
        )
        players_needing_review = review_result.scalar()

        # Total OCR registrations
        ocr_result = await self.session.execute(select(func.count(OCRRegistration.id)))
        total_ocr_registrations = ocr_result.scalar()

        # Average OCR confidence
        confidence_result = await self.session.execute(
            select(func.avg(Player.ocr_confidence)).where(Player.ocr_confidence.isnot(None))
        )
        avg_confidence = confidence_result.scalar() or 0.0

        return {
            'total_teams': total_teams,
            'total_players': total_players,
            'players_needing_review': players_needing_review,
            'total_ocr_registrations': total_ocr_registrations,
            'average_ocr_confidence': float(avg_confidence),
            'review_rate': (players_needing_review / total_players * 100) if total_players > 0 else 0.0
        }

    async def commit(self):
        """Commit the current transaction."""
        await self.session.commit()
        logger.info("✅ Database transaction committed")

    async def rollback(self):
        """Rollback the current transaction."""
        await self.session.rollback()
        logger.warning("⚠️ Database transaction rolled back")
