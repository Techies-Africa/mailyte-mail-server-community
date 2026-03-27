"""
API Routes Package — Community Edition
Exports all module routers for inclusion in the FastAPI application
"""

from .organizations import router as organizations_router
from .domains import router as domains_router
from .mailboxes import router as mailboxes_router
from .aliases import router as aliases_router
from .filters import router as filters_router
from .tracking import router as tracking_router
from .webhooks import router as webhooks_router
from .rate_limiter import router as rate_limiter_router
from .ssl import router as ssl_router
