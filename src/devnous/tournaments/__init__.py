"""
DevNous Tournaments - Multi-tenant tournament management system.

This package provides a modular architecture for managing multiple tournaments,
each with their own finance, operations, and marketing modules.
"""

from .core.tournament_bot import TournamentBot
from .core.finance_module import FinanceModule
from .core.operations_module import OperationsModule
from .core.marketing_module import MarketingModule
from .core.intelligence_program import TournamentIntelligenceWorkspace
from .central.master_bot import MasterTournamentBot

__all__ = [
    'TournamentBot',
    'FinanceModule',
    'OperationsModule',
    'MarketingModule',
    'TournamentIntelligenceWorkspace',
    'MasterTournamentBot',
]
