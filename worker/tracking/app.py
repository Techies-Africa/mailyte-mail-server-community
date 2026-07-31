#!/usr/bin/env python3
"""
Email Tracking Service - Main Application

This service provides comprehensive email tracking functionality including:
- Open tracking via tracking pixels
- Click tracking via URL rewriting
- Real-time webhook notifications
- Rate limiting and security
- Multi-tenant support

The service is designed to be scalable, secure, and fully configurable
through environment variables managed by the config system.
"""

import sys
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from datetime import datetime

# Add shared directory and database models to path
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root / "shared"))

from shared.logging_config import get_service_logger, get_performance_logger, LogTimer
from database.models import Base

# Import our modular services
from config import config_manager
from services.tracking_service import TrackingService
from services.database_service import DatabaseService
from services.webhook_service import EnterpriseWebhookService as WebhookService
from services.rate_limiter import RateLimiter

# Import API routers
from api.tracking_api import tracking_api
from api.health_api import health_api
from api.stats_api import stats_api

# Configure service-specific logging
logger, log_performance = get_performance_logger("tracking")


def create_app():
    """
    Application factory for creating the FastAPI app with all services configured.

    This factory pattern allows for:
    - Proper service initialization order
    - Clean dependency injection
    - Easy testing with different configurations
    - Centralized error handling during startup

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(title="Tracking Service")

    with LogTimer(logger, "Email Tracking Service initialization"):
        # Initialize services
        logger.info("Initializing Email Tracking Service...")
        logger.info(f"Python path: {sys.path}")
        logger.info(f"Project root: {project_root}")

        # Validate configuration before proceeding
        try:
            if hasattr(config_manager, "validate_config"):
                config_manager.validate_config()
                logger.info("Configuration validation passed")
            else:
                logger.warning("Configuration validation skipped (validate_config not available)")
        except Exception as e:
            logger.warning(f"Configuration validation failed: {e}")

        # Initialize global services in proper order
        with LogTimer(logger, "Service initialization"):
            try:
                # Database service first (required by others)
                logger.info("Initializing database service...")
                app.database_service = DatabaseService()

                # Test database connectivity
                if not app.database_service.test_connection():
                    logger.warning("Database connection test failed - will retry at runtime")
                else:
                    logger.info("Database service initialized successfully")

                # Tracking service (core functionality)
                logger.info("Initializing tracking service...")
                app.tracking_service = TrackingService()
                logger.info("Tracking service initialized successfully")

                # Webhook service (for notifications)
                logger.info("Initializing webhook service...")
                app.webhook_service = WebhookService()
                logger.info("Webhook service initialized successfully")

                # Rate limiter (for protection)
                logger.info("Initializing rate limiter...")
                app.rate_limiter = RateLimiter()
                logger.info("Rate limiter initialized successfully")

            except Exception as e:
                logger.error(f"Service initialization failed: {e}")
                raise

        # Include API routers with proper URL prefixes
        logger.info("Registering API routers...")
        try:
            # Health endpoints (no prefix for easy monitoring)
            app.include_router(health_api)

            # Tracking endpoints (pixel and click tracking)
            app.include_router(tracking_api)

            # Stats and analytics API
            app.include_router(stats_api, prefix="/api")

            logger.info("API routers registered successfully")
        except Exception as e:
            logger.error(f"Router registration failed: {e}")
            raise

        # Log final configuration status
        config = config_manager.config
        logger.info("Email Tracking Service initialized successfully")
        logger.info(f"Configuration summary:")
        logger.info(f"  - Tracking enabled: {config.enabled}")
        logger.info(f"  - Open tracking: {config.open_tracking_enabled}")
        logger.info(f"  - Click tracking: {config.click_tracking_enabled}")
        logger.info(f"  - Webhook notifications: {config.webhook_enabled}")
        logger.info(f"  - Rate limiting: {config.rate_limiting_enabled}")
        logger.info(f"  - Async logging: {config.async_logging}")
        logger.info(f"  - Database URL: {config_manager.get_database_url()}")
        logger.info(f"  - Tracking base URL: {config_manager.get_tracking_url_base()}")

    return app


# Create the FastAPI application using factory pattern
app = create_app()


# Global exception handlers for better error reporting
@app.exception_handler(404)
async def not_found_error(request: Request, exc):
    """Handle 404 errors gracefully"""
    logger.warning(f"404 error: {exc}")
    return JSONResponse({"error": "Endpoint not found", "status": 404}, status_code=404)


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    """Handle 500 errors gracefully"""
    logger.error(f"500 error: {exc}")
    return JSONResponse({"error": "Internal server error", "status": 500}, status_code=500)


@app.exception_handler(Exception)
async def handle_exception(request: Request, exc: Exception):
    """Handle unexpected exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse({"error": "An unexpected error occurred", "status": 500}, status_code=500)


if __name__ == "__main__":
    """
    Main entry point for the tracking service.

    Starts the FastAPI application with uvicorn.
    The service runs on all interfaces (0.0.0.0) to be accessible within
    the Docker network and from external clients.

    The service listens on port 8086 to avoid conflicts with other services:
    - Port 8085: Health monitor
    - Port 8086: Tracking service (this service)
    - Port 8087: Available for future services
    """
    try:
        logger.info("Starting Email Tracking Service...")
        logger.info(f"Service will be available at: {config_manager.get_tracking_url_base()}")
        logger.info("Available endpoints:")
        logger.info("  - GET  /health - Service health check")
        logger.info("  - GET  /track/open/<tracking_id> - Email open tracking")
        logger.info("  - GET  /track/click/<tracking_id> - Email click tracking")
        logger.info("  - POST /api/tracking/inject - Inject tracking into email content")
        logger.info("  - GET  /api/tracking/stats/<email_id> - Get tracking statistics")
        logger.info("  - GET  /api/tracking/tenant/<tenant_id>/stats - Get tenant statistics")

        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=8086)
    except Exception as e:
        logger.error(f"Failed to start Email Tracking Service: {e}")
        sys.exit(1)
