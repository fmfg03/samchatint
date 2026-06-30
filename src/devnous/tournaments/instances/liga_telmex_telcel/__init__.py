"""
Liga Telmex Telcel - Tournament Instance

Liga Telmex Telcel 2026 - Torneo nacional de béisbol
Categories: 13 años varonil, 14 años varonil
Stages: Convenio, Fase Colectiva, Fase Estatal, Fase Nacional (19-26 Sep), Viaje de Campeones
"""

from .bot import LigaTelmexTelcelBot, create_liga_telmex_telcel_bot
from .database import LigaTelmexTelcelDB
from .models import (
    Base,
    BaseballTeam,
    BaseballPlayer,
    LigaOCRRegistration,
    TournamentStage,
    Sponsorship
)

__all__ = [
    'LigaTelmexTelcelBot',
    'create_liga_telmex_telcel_bot',
    'LigaTelmexTelcelDB',
    'Base',
    'BaseballTeam',
    'BaseballPlayer',
    'LigaOCRRegistration',
    'TournamentStage',
    'Sponsorship'
]