"""
Routes for expense management system.
"""

from .webhook_handler import router as webhook_router
from .admin_routes import router as admin_router
from .user_routes import router as user_router
from .auth_routes import router as auth_router
from .support_routes import router as support_router

__all__ = [
    'webhook_router',
    'admin_router',
    'user_router',
    'auth_router',
    'support_router',
]

