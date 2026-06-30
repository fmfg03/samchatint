"""
Database operations for Liga Telmex Telcel baseball tournament.
"""
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker as async_session_maker_func

from .models import (
    BaseballTeam, BaseballPlayer, LigaOCRRegistration,
    TournamentStage, Sponsorship, Base
)

logger = logging.getLogger(__name__)


class LigaTelmexTelcelDB:
    """Database operations for Liga Telmex Telcel."""

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
        category: str,
        telegram_chat_id: int,
        gender: str = "varonil",
        league: Optional[str] = None,
        representative_name: Optional[str] = None,
        state: Optional[str] = None,
        municipality: Optional[str] = None,
        contact_email: Optional[str] = None,
        contact_phone: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
        roster_image_path: Optional[str] = None
    ) -> BaseballTeam:
        """
        Create a new baseball team.

        Args:
            name: Team name
            category: Category ("13 años varonil" or "14 años varonil")
            telegram_chat_id: Telegram chat ID
            gender: varonil/femenil (default: varonil)
            league: League name
            representative_name: Representative's full name
            state: State
            municipality: Municipality
            contact_email: Contact email
            contact_phone: Contact phone
            telegram_user_id: Telegram user ID
            roster_image_path: Path to roster OCR image file

        Returns:
            Created BaseballTeam object
        """
        team = BaseballTeam(
            name=name,
            tournament_slug='liga_telmex_telcel',
            gender=gender,
            category=category,
            league=league,
            representative_name=representative_name,
            state=state,
            municipality=municipality,
            contact_email=contact_email,
            contact_phone=contact_phone,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
            roster_image_path=roster_image_path
        )

        self.session.add(team)
        await self.session.flush()

        logger.info(f"✅ Baseball team created: {team.name} (ID: {team.id})")
        return team

    async def get_team_by_id(self, team_id: UUID) -> Optional[BaseballTeam]:
        """Get team by ID."""
        result = await self.session.execute(
            select(BaseballTeam).where(BaseballTeam.id == team_id)
        )
        return result.scalar_one_or_none()

    async def get_team_by_name(
        self,
        name: str,
        category: Optional[str] = None,
        telegram_chat_id: Optional[int] = None
    ) -> Optional[BaseballTeam]:
        """
        Get team by name and optionally category and chat.

        Args:
            name: Team name
            category: Optional category filter
            telegram_chat_id: Optional chat ID filter

        Returns:
            BaseballTeam or None
        """
        query = select(BaseballTeam).where(BaseballTeam.name == name)
        if category:
            query = query.where(BaseballTeam.category == category)
        if telegram_chat_id is not None:
            query = query.where(BaseballTeam.telegram_chat_id == telegram_chat_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_teams_by_chat(self, telegram_chat_id: int) -> List[BaseballTeam]:
        """Get all teams registered by a Telegram chat."""
        result = await self.session.execute(
            select(BaseballTeam)
            .where(BaseballTeam.telegram_chat_id == telegram_chat_id)
            .order_by(desc(BaseballTeam.created_at))
        )
        return list(result.scalars().all())

    async def get_teams_by_category(self, category: str) -> List[BaseballTeam]:
        """Get all teams in a specific category."""
        result = await self.session.execute(
            select(BaseballTeam)
            .where(
                and_(
                    BaseballTeam.category == category,
                    BaseballTeam.is_active == True
                )
            )
            .order_by(BaseballTeam.name)
        )
        return list(result.scalars().all())

    async def update_team(self, team_id: UUID, **fields: Any) -> Optional[BaseballTeam]:
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
        phone: Optional[str] = None,
        jersey_number: Optional[int] = None,
        position: Optional[str] = None,
        batting_order: Optional[int] = None,
        throwing_hand: Optional[str] = None,
        batting_hand: Optional[str] = None,
        photo_path: Optional[str] = None,
        photo_data: Optional[str] = None,
        photo_sha256: Optional[str] = None,
        photo_ahash: Optional[str] = None,
        ocr_confidence: Optional[float] = None,
        needs_review: bool = False,
        verified_by_human: bool = False,
        verification_notes: Optional[str] = None,
        roster_index: Optional[int] = None,
    ) -> BaseballPlayer:
        """
        Create a new baseball player.

        Args:
            team_id: Team ID
            first_name: Player's first name(s)
            last_name: Player's last name(s)
            birth_date: Date of birth
            curp: CURP (Clave Única de Registro de Población)
            email: Email address
            phone: Phone number
            jersey_number: Jersey number
            position: Primary position
            batting_order: Batting order
            throwing_hand: Throwing hand (Derecho, Izquierdo, Ambidiestro)
            batting_hand: Batting hand (Derecho, Izquierdo, Ambidiestro)
            photo_path: Path to player photo file
            photo_data: Base64 encoded photo data
            photo_sha256: SHA-256 fingerprint
            photo_ahash: Average hash fingerprint
            ocr_confidence: OCR confidence score
            needs_review: Whether player needs human review
            verified_by_human: Whether player was verified by human
            verification_notes: Notes from verification
            roster_index: Order in roster

        Returns:
            Created BaseballPlayer object
        """
        player = BaseballPlayer(
            team_id=team_id,
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            curp=curp,
            email=email,
            phone=phone,
            jersey_number=jersey_number,
            position=position,
            batting_order=batting_order,
            throwing_hand=throwing_hand,
            batting_hand=batting_hand,
            photo_path=photo_path,
            photo_data=photo_data,
            photo_sha256=photo_sha256,
            photo_ahash=photo_ahash,
            ocr_confidence=ocr_confidence,
            needs_review=needs_review,
            verified_by_human=verified_by_human,
            verification_notes=verification_notes,
            roster_index=roster_index,
        )

        self.session.add(player)
        await self.session.flush()

        logger.info(f"✅ Baseball player created: {player.full_name} (ID: {player.id}, Team: {team_id})")
        return player

    async def get_player_by_id(self, player_id: UUID) -> Optional[BaseballPlayer]:
        """Get player by ID."""
        result = await self.session.execute(
            select(BaseballPlayer).where(BaseballPlayer.id == player_id)
        )
        return result.scalar_one_or_none()

    async def get_player_by_curp(self, curp: str) -> Optional[BaseballPlayer]:
        """Get player by CURP."""
        result = await self.session.execute(
            select(BaseballPlayer).where(BaseballPlayer.curp == curp)
        )
        return result.scalar_one_or_none()

    async def get_players_by_team(self, team_id: UUID) -> List[BaseballPlayer]:
        """Get all players in a team."""
        result = await self.session.execute(
            select(BaseballPlayer)
            .where(
                and_(
                    BaseballPlayer.team_id == team_id,
                    BaseballPlayer.is_active == True
                )
            )
            .order_by(BaseballPlayer.batting_order, BaseballPlayer.jersey_number)
        )
        return list(result.scalars().all())

    async def update_player(self, player_id: UUID, **fields: Any) -> Optional[BaseballPlayer]:
        """Update a player with the provided fields."""
        player = await self.session.get(BaseballPlayer, player_id)
        if not player:
            return None

        for key, value in fields.items():
            if not hasattr(player, key):
                continue
            setattr(player, key, value)

        await self.session.flush()
        logger.info(f"✅ Player updated: {player.full_name} (ID: {player.id})")
        return player

    async def get_players_needing_review(self, team_id: Optional[UUID] = None) -> List[BaseballPlayer]:
        """Get players that need human review."""
        query = select(BaseballPlayer).where(
            and_(
                BaseballPlayer.needs_review == True,
                BaseballPlayer.verified_by_human == False
            )
        )

        if team_id:
            query = query.where(BaseballPlayer.team_id == team_id)

        result = await self.session.execute(query.order_by(BaseballPlayer.created_at))
        return list(result.scalars().all())

    # ============================================================================
    # TOURNAMENT STAGE OPERATIONS
    # ============================================================================

    async def create_stage(
        self,
        stage_id: str,
        name: str,
        start_date: date,
        end_date: date,
        description: Optional[str] = None,
        location: Optional[str] = None,
        is_final: bool = False,
        is_awards: bool = False
    ) -> TournamentStage:
        """Create a tournament stage."""
        stage = TournamentStage(
            stage_id=stage_id,
            name=name,
            description=description,
            start_date=start_date,
            end_date=end_date,
            location=location,
            is_final=is_final,
            is_awards=is_awards
        )

        self.session.add(stage)
        await self.session.flush()

        logger.info(f"✅ Tournament stage created: {name} (ID: {stage.id})")
        return stage

    async def get_current_stage(self) -> Optional[TournamentStage]:
        """Get the current active tournament stage."""
        today = date.today()

        result = await self.session.execute(
            select(TournamentStage).where(
                and_(
                    TournamentStage.start_date <= today,
                    TournamentStage.end_date >= today,
                    TournamentStage.status == 'active'
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_all_stages(self) -> List[TournamentStage]:
        """Get all tournament stages ordered by date."""
        result = await self.session.execute(
            select(TournamentStage).order_by(TournamentStage.start_date)
        )
        return list(result.scalars().all())

    # ============================================================================
    # STATISTICS AND REPORTING
    # ============================================================================

    async def get_registration_stats(self) -> Dict[str, Any]:
        """Get registration statistics."""
        # Total teams
        teams_result = await self.session.execute(
            select(func.count(BaseballTeam.id)).where(BaseballTeam.is_active == True)
        )
        total_teams = teams_result.scalar()

        # Teams by category
        teams_13_result = await self.session.execute(
            select(func.count(BaseballTeam.id)).where(
                and_(
                    BaseballTeam.category == '13 años varonil',
                    BaseballTeam.is_active == True
                )
            )
        )
        teams_13 = teams_13_result.scalar()

        teams_14_result = await self.session.execute(
            select(func.count(BaseballTeam.id)).where(
                and_(
                    BaseballTeam.category == '14 años varonil',
                    BaseballTeam.is_active == True
                )
            )
        )
        teams_14 = teams_14_result.scalar()

        # Total players
        players_result = await self.session.execute(
            select(func.count(BaseballPlayer.id)).where(BaseballPlayer.is_active == True)
        )
        total_players = players_result.scalar()

        # Players needing review
        review_result = await self.session.execute(
            select(func.count(BaseballPlayer.id)).where(
                and_(
                    BaseballPlayer.needs_review == True,
                    BaseballPlayer.verified_by_human == False
                )
            )
        )
        players_needing_review = review_result.scalar()

        # Total OCR registrations
        ocr_result = await self.session.execute(select(func.count(LigaOCRRegistration.id)))
        total_ocr_registrations = ocr_result.scalar()

        # Average OCR confidence
        confidence_result = await self.session.execute(
            select(func.avg(BaseballPlayer.ocr_confidence)).where(
                and_(
                    BaseballPlayer.ocr_confidence.isnot(None),
                    BaseballPlayer.is_active == True
                )
            )
        )
        avg_confidence = confidence_result.scalar() or 0.0

        # Payment stats
        paid_teams_result = await self.session.execute(
            select(func.count(BaseballTeam.id)).where(
                and_(
                    BaseballTeam.payment_status == 'paid',
                    BaseballTeam.is_active == True
                )
            )
        )
        paid_teams = paid_teams_result.scalar()

        total_revenue_result = await self.session.execute(
            select(func.sum(BaseballTeam.payment_amount)).where(
                and_(
                    BaseballTeam.payment_status == 'paid',
                    BaseballTeam.is_active == True
                )
            )
        )
        total_revenue = total_revenue_result.scalar() or 0

        return {
            'total_teams': total_teams,
            'teams_13_years': teams_13,
            'teams_14_years': teams_14,
            'total_players': total_players,
            'players_needing_review': players_needing_review,
            'total_ocr_registrations': total_ocr_registrations,
            'average_ocr_confidence': float(avg_confidence),
            'review_rate': (players_needing_review / total_players * 100) if total_players > 0 else 0.0,
            'paid_teams': paid_teams,
            'pending_payments': total_teams - paid_teams,
            'total_revenue': float(total_revenue),
            'revenue_per_team': float(total_revenue / paid_teams) if paid_teams > 0 else 0.0
        }

    async def commit(self):
        """Commit the current transaction."""
        await self.session.commit()
        logger.info("✅ Database transaction committed")

    async def rollback(self):
        """Rollback the current transaction."""
        await self.session.rollback()
        logger.warning("⚠️ Database transaction rolled back")