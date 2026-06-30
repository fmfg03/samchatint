from .core import SamChatSystem, SamChatConfig
from .agents import ProductOwnerAgent, ScrumMasterAgent, DeveloperAgent
from .base_agent import BaseAgent, Message, ProjectContext, LLMProvider
from .conversation_parser import ConversationParser
from .feature_flags import FeatureFlagManager, get_feature_manager

__version__ = "0.1.0"
__all__ = [
    # Core system
    "SamChatSystem",
    "SamChatConfig",

    # Agents
    "ProductOwnerAgent",
    "ScrumMasterAgent",
    "DeveloperAgent",
    "BaseAgent",

    # Data models
    "Message",
    "ProjectContext",
    "LLMProvider",

    # Utilities
    "ConversationParser",
    "FeatureFlagManager",
    "get_feature_manager"
]