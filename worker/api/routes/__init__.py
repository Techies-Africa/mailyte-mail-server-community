"""
API Routes Package
Exports all module routers for inclusion in the FastAPI application
"""

from .aliases import router as aliases_router
from .analytics import router as analytics_router
from .domains import router as domains_router
from .mailboxes import router as mailboxes_router
from .monitoring import router as monitoring_router
from .queue import router as queue_router
from .rate_limiter import router as rate_limiter_router
from .ssl import router as ssl_router
from .tracking import router as tracking_router
from .webhooks import router as webhooks_router
