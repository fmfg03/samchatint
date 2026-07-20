"""
Database operations for Copa Telmex registration system.
"""
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import (
    and_,
    desc,
    event,
    inspect as sqlalchemy_inspect,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Team, Player, OCRRegistration, ValidationLog
from .persistence_authority import (
    PersistenceAuthorityDenied,
)

logger = logging.getLogger(__name__)

_SESSION_GUARD_KEY = "copa_telmex_persistence_authority_guard"


class _SessionPersistenceAuthorityGuard:
    """Guard Team/Player flushes made through a CopaTelmexDB-owned session."""

    def __init__(self, session: AsyncSession):
        self.token = object()
        self.capability: Optional[Any] = None
        self.sync_session = getattr(session, "sync_session", None)
        if self.sync_session is not None:
            event.listen(self.sync_session, "before_flush", self._before_flush)
            event.listen(self.sync_session, "after_commit", self._after_transaction)
            event.listen(self.sync_session, "after_rollback", self._after_transaction)

    def bind(self, capability: Any) -> None:
        if self.capability is not None and self.capability is not capability:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_ALREADY_BOUND",
                "session already has a different persistence capability",
            )
        capability.bind(self.token)
        self.capability = capability

    def require(self) -> Any:
        if self.capability is None:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_REQUIRED",
                "Team and Player mutations require transaction-scoped governance authority",
            )
        return self.capability

    @staticmethod
    def _changed_column_fields(entity: Any) -> Set[str]:
        state = sqlalchemy_inspect(entity)
        return {
            attribute.key
            for attribute in state.mapper.column_attrs
            if state.attrs[attribute.key].history.has_changes()
        }

    def _before_flush(self, session, _flush_context, _instances) -> None:
        protected = [
            entity
            for collection in (session.new, session.dirty, session.deleted)
            for entity in collection
            if isinstance(entity, (Team, Player))
        ]
        if not protected:
            return
        capability = self.require()
        for entity in session.deleted:
            if isinstance(entity, (Team, Player)):
                capability.deny_delete(entity, token=self.token)
        for entity in session.new:
            if isinstance(entity, Team):
                capability.authorize_team_create(entity, token=self.token)
            elif isinstance(entity, Player):
                capability.authorize_player_create(entity, token=self.token)
        for entity in session.dirty:
            if entity in session.new or entity in session.deleted:
                continue
            fields = self._changed_column_fields(entity)
            if not fields:
                continue
            if isinstance(entity, Team):
                capability.authorize_team_update(entity, fields, token=self.token)
            elif isinstance(entity, Player):
                capability.authorize_player_update(entity, fields, token=self.token)

    def _after_transaction(self, _session) -> None:
        self.invalidate()

    def invalidate(self) -> None:
        if self.capability is not None:
            self.capability.invalidate()
            self.capability = None


class CopaTelmexDB:
    """Database operations for Copa Telmex."""

    def __init__(self, session: AsyncSession):
        """
        Initialize with database session.

        Args:
            session: SQLAlchemy async session
        """
        self.session = session
        sync_session = getattr(session, "sync_session", None)
        if sync_session is not None:
            guard = sync_session.info.get(_SESSION_GUARD_KEY)
            if guard is None:
                guard = _SessionPersistenceAuthorityGuard(session)
                sync_session.info[_SESSION_GUARD_KEY] = guard
            self._persistence_guard = guard
        else:
            self._persistence_guard = _SessionPersistenceAuthorityGuard(session)

    def bind_persistence_authority(
        self, capability: Any
    ) -> None:
        """Bind one preauthorization capability to this database transaction."""
        self._persistence_guard.bind(capability)

    def bind_postcommit_authority(self, capability: Any) -> None:
        """Bind one REG-S07 double-receipt capability to this transaction."""
        self._persistence_guard.bind(capability)

    def record_player_finality(
        self, player: Player, finality_result: Dict[str, Any]
    ) -> str:
        """Bind one post-execution receipt before activating a Player."""
        return self._persistence_guard.require().record_player_finality(
            player, finality_result, token=self._persistence_guard.token
        )

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
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
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
            contact_phone=contact_phone,
            contact_email=contact_email,
            state=state,
            municipality=municipality,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
            roster_image_path=roster_image_path
        )
        if team_id is not None:
            team_fields["id"] = team_id
        team = Team(**team_fields)

        self._persistence_guard.require().authorize_team_create(
            team, token=self._persistence_guard.token
        )
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
        authority = self._persistence_guard.require()
        team = await self.get_team_by_id(team_id)
        if not team:
            return None

        authority.authorize_team_update(
            team, fields.keys(), token=self._persistence_guard.token
        )
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

        self._persistence_guard.require().authorize_player_create(
            player, token=self._persistence_guard.token
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

    async def get_player_photo_fingerprints_by_tournament(
        self,
        tournament_slug: str,
    ) -> List[Dict[str, Any]]:
        """Return committed player photo fingerprints for one tournament."""
        slug = (tournament_slug or "").strip()
        if not slug:
            return []

        result = await self.session.execute(
            select(Player, Team)
            .join(Team, Player.team_id == Team.id)
            .where(
                Team.tournament_slug == slug,
                Player.governance_state.in_(("ACTIVE", "LEGACY_ACTIVE")),
            )
        )
        records: List[Dict[str, Any]] = []
        for player, team in result.all():
            if not player.photo_sha256 and not player.photo_ahash:
                continue
            records.append(
                {
                    "player_ref": f"player-{player.id}",
                    "player_id": str(player.id),
                    "player_name": player.full_name,
                    "team_ref": f"team-{team.id}",
                    "team_id": str(team.id),
                    "team_name": team.name,
                    "tournament_slug": team.tournament_slug,
                    "photo_sha256": player.photo_sha256,
                    "photo_ahash": player.photo_ahash,
                }
            )
        return records

    async def update_player(self, player_id: UUID, **fields: Any) -> Optional[Player]:
        """Update a player with the provided fields."""
        authority = self._persistence_guard.require()
        player = await self.session.get(Player, player_id)
        if not player:
            return None

        authority.authorize_player_update(
            player, fields.keys(), token=self._persistence_guard.token
        )
        for key, value in fields.items():
            if not hasattr(player, key):
                continue
            setattr(player, key, value)

        await self.session.flush()
        logger.info(f"✅ Player updated: {player.full_name} (ID: {player.id})")
        return player

    async def delete_player(self, player_id: UUID) -> bool:
        """Delete a player by ID."""
        authority = self._persistence_guard.require()
        player = await self.session.get(Player, player_id)
        if not player:
            return False
        authority.deny_delete(player, token=self._persistence_guard.token)
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
        try:
            await self.session.commit()
            logger.info("✅ Database transaction committed")
        finally:
            self._persistence_guard.invalidate()

    async def rollback(self):
        """Rollback the current transaction."""
        try:
            await self.session.rollback()
            logger.warning("⚠️ Database transaction rolled back")
        finally:
            self._persistence_guard.invalidate()
