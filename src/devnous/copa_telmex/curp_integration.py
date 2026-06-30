"""
CURP Integration for Copa Telmex

Integrates CURP validation with player registration and OCR extraction.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.validation import get_curp_validator
from devnous.copa_telmex.models import Player

logger = logging.getLogger(__name__)


class CURPIntegrationHandler:
    """
    Handles CURP validation integration with Copa Telmex system.

    Features:
    - Validate CURP for players
    - Store validation results
    - Check CURP against player data
    - Generate reports
    """

    def __init__(self):
        """Initialize CURP integration handler."""
        self.curp_validator = get_curp_validator()
        self.logger = logging.getLogger(__name__)

    async def validate_player_curp(
        self,
        session: AsyncSession,
        player_id: UUID,
        curp: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate CURP for a player.

        Args:
            session: Database session
            player_id: Player ID
            curp: Optional CURP (if not provided, uses player's CURP)

        Returns:
            Validation result dictionary
        """

        # Get player
        query = select(Player).where(Player.id == player_id)
        result = await session.execute(query)
        player = result.scalar_one_or_none()

        if not player:
            return {
                "status": "error",
                "message": "❌ Jugador no encontrado",
                "valid": False
            }

        # Use provided CURP or player's CURP
        curp_to_validate = curp or player.curp

        if not curp_to_validate:
            return {
                "status": "error",
                "message": "❌ No hay CURP para validar",
                "valid": False
            }

        # Validate CURP against player data
        validation = self.curp_validator.validar_contra_datos(
            curp=curp_to_validate,
            nombre=player.first_name,
            apellido_paterno=player.last_name,
            apellido_materno=None,  # Not in model yet
            fecha_nacimiento=player.birth_date,
            sexo=None  # Not in model yet
        )

        # Update player record
        player.curp = curp_to_validate
        player.curp_valid = validation['valid']
        player.curp_validation_date = datetime.utcnow()

        if not validation['valid'] or validation.get('mismatches'):
            errors = validation['errors'] + validation.get('mismatches', [])
            player.curp_validation_errors = json.dumps(errors, ensure_ascii=False)
        else:
            player.curp_validation_errors = None

        await session.commit()
        await session.refresh(player)

        # Format response
        if validation['valid']:
            data = validation['data']
            edad = self.curp_validator.calcular_edad(curp_to_validate)

            message = (
                f"✅ CURP válido\n\n"
                f"📋 CURP: {curp_to_validate}\n"
                f"👤 Jugador: {player.first_name} {player.last_name}\n"
                f"📅 Fecha nacimiento: {data.fecha_nacimiento.strftime('%Y-%m-%d')}\n"
                f"🎂 Edad: {edad} años\n"
                f"⚧️ Sexo: {data.sexo}\n"
                f"📍 Estado: {data.estado_nombre}\n"
            )

            # Check for mismatches
            if validation.get('mismatches'):
                message += "\n⚠️  **Inconsistencias detectadas:**\n"
                for mismatch in validation['mismatches']:
                    message += f"  • {mismatch}\n"

            return {
                "status": "success",
                "message": message,
                "valid": True,
                "data": data,
                "player": player
            }
        else:
            message = (
                f"❌ CURP inválido\n\n"
                f"📋 CURP: {curp_to_validate}\n"
                f"👤 Jugador: {player.first_name} {player.last_name}\n\n"
                f"**Errores:**\n"
            )

            for error in validation['errors']:
                message += f"  • {error}\n"

            return {
                "status": "error",
                "message": message,
                "valid": False,
                "errors": validation['errors'],
                "player": player
            }

    async def validate_curp_standalone(
        self,
        curp: str
    ) -> Dict[str, Any]:
        """
        Validate CURP without player data (standalone validation).

        Args:
            curp: CURP string

        Returns:
            Validation result dictionary
        """

        validation = self.curp_validator.validate(curp)

        if validation['valid']:
            data = validation['data']
            edad = self.curp_validator.calcular_edad(curp)

            message = (
                f"✅ CURP válido\n\n"
                f"📋 CURP: {curp}\n"
                f"📅 Fecha nacimiento: {data.fecha_nacimiento.strftime('%Y-%m-%d')}\n"
                f"🎂 Edad: {edad} años\n"
                f"⚧️ Sexo: {data.sexo}\n"
                f"📍 Estado: {data.estado_nombre}\n"
            )

            return {
                "status": "success",
                "message": message,
                "valid": True,
                "data": data
            }
        else:
            message = (
                f"❌ CURP inválido\n\n"
                f"📋 CURP: {curp}\n\n"
                f"**Errores:**\n"
            )

            for error in validation['errors']:
                message += f"  • {error}\n"

            return {
                "status": "error",
                "message": message,
                "valid": False,
                "errors": validation['errors']
            }

    async def get_players_without_curp(
        self,
        session: AsyncSession,
        team_id: Optional[UUID] = None
    ) -> List[Player]:
        """
        Get players without CURP.

        Args:
            session: Database session
            team_id: Optional team ID filter

        Returns:
            List of players without CURP
        """

        query = select(Player).where(
            (Player.curp == None) | (Player.curp == '')
        )

        if team_id:
            query = query.where(Player.team_id == team_id)

        result = await session.execute(query)
        return result.scalars().all()

    async def get_players_with_invalid_curp(
        self,
        session: AsyncSession,
        team_id: Optional[UUID] = None
    ) -> List[Player]:
        """
        Get players with invalid CURP.

        Args:
            session: Database session
            team_id: Optional team ID filter

        Returns:
            List of players with invalid CURP
        """

        query = select(Player).where(
            Player.curp.isnot(None),
            Player.curp_valid == False
        )

        if team_id:
            query = query.where(Player.team_id == team_id)

        result = await session.execute(query)
        return result.scalars().all()

    async def bulk_validate_team_curps(
        self,
        session: AsyncSession,
        team_id: UUID
    ) -> Dict[str, Any]:
        """
        Validate CURPs for all players in a team.

        Args:
            session: Database session
            team_id: Team ID

        Returns:
            Summary of validation results
        """

        # Get all players in team
        query = select(Player).where(Player.team_id == team_id)
        result = await session.execute(query)
        players = result.scalars().all()

        if not players:
            return {
                "status": "error",
                "message": "❌ No se encontraron jugadores en el equipo",
                "validated": 0,
                "valid": 0,
                "invalid": 0,
                "missing": 0
            }

        validated = 0
        valid = 0
        invalid = 0
        missing = 0

        for player in players:
            if not player.curp:
                missing += 1
                continue

            # Validate
            validation_result = await self.validate_player_curp(
                session=session,
                player_id=player.id
            )

            validated += 1
            if validation_result['valid']:
                valid += 1
            else:
                invalid += 1

        message = (
            f"✅ Validación masiva completada\n\n"
            f"👥 Total jugadores: {len(players)}\n"
            f"✔️ Válidos: {valid}\n"
            f"❌ Inválidos: {invalid}\n"
            f"⚠️ Sin CURP: {missing}\n"
        )

        return {
            "status": "success",
            "message": message,
            "total": len(players),
            "validated": validated,
            "valid": valid,
            "invalid": invalid,
            "missing": missing
        }


# Singleton instance
_curp_integration_handler = None

def get_curp_integration_handler() -> CURPIntegrationHandler:
    """Get singleton CURP integration handler instance."""
    global _curp_integration_handler
    if _curp_integration_handler is None:
        _curp_integration_handler = CURPIntegrationHandler()
    return _curp_integration_handler
