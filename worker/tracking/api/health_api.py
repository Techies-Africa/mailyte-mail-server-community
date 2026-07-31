#!/usr/bin/env python3
"""
Email Tracking Service - Health Check API

This module provides comprehensive health monitoring endpoints:
- Basic service health checks
- Database connectivity verification
- Configuration validation
- Performance metrics

Used by monitoring systems and load balancers to ensure
service availability and proper functioning.
"""

import logging
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Create router for health check endpoints
health_api = APIRouter()


@health_api.get("/health")
async def health_check(request: Request):
    """
    Basic health check endpoint for the tracking service.

    This is the primary endpoint used by:
    - Load balancers for health checks
    - Monitoring systems for availability checks
    - Docker/Kubernetes health probes
    - Service discovery systems

    Returns:
        JSON: Basic health status
    """
    try:
        # Test database connectivity
        db_healthy = request.app.database_service.test_connection()

        # Check if tracking service is properly configured
        config_valid = (
            hasattr(request.app, "tracking_service") and request.app.tracking_service.config.enabled
        )

        # Determine overall health status
        is_healthy = db_healthy and config_valid

        health_data = {
            "status": "healthy" if is_healthy else "unhealthy",
            "service": "email-tracking",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "checks": {
                "database": "ok" if db_healthy else "failed",
                "configuration": "valid" if config_valid else "invalid",
            },
        }

        # Return appropriate HTTP status code
        status_code = 200 if is_healthy else 503

        if not is_healthy:
            logger.warning(f"Health check failed: database={db_healthy}, config={config_valid}")

        return JSONResponse(health_data, status_code=status_code)

    except Exception as e:
        logger.error(f"Health check error: {e}")
        return JSONResponse(
            {
                "status": "unhealthy",
                "service": "email-tracking",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@health_api.get("/health/detailed")
async def detailed_health_check(request: Request):
    """
    Detailed health check with comprehensive service status.

    Provides in-depth information about:
    - All service components status
    - Configuration summary
    - Database connection details
    - Performance indicators

    Returns:
        JSON: Detailed health and status information
    """
    try:
        health_data = {
            "status": "healthy",
            "service": "email-tracking",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": {},
            "configuration": {},
            "performance": {},
        }

        # Check database service
        try:
            db_healthy = request.app.database_service.test_connection()
            health_data["components"]["database"] = {
                "status": "ok" if db_healthy else "failed",
                "url": request.app.database_service.database_url.replace(
                    request.app.database_service.database_url.split("@")[0].split("://")[-1], "***"
                )
                if hasattr(request.app.database_service, "database_url")
                else "not_configured",
            }
            if not db_healthy:
                health_data["status"] = "unhealthy"
        except Exception as e:
            health_data["components"]["database"] = {"status": "error", "error": str(e)}
            health_data["status"] = "unhealthy"

        # Check tracking service
        try:
            if hasattr(request.app, "tracking_service"):
                config = request.app.tracking_service.config
                health_data["components"]["tracking"] = {"status": "ok"}
                health_data["configuration"] = {
                    "tracking_enabled": config.enabled,
                    "open_tracking": config.open_tracking_enabled,
                    "click_tracking": config.click_tracking_enabled,
                    "webhook_enabled": config.webhook_enabled,
                    "rate_limiting": config.rate_limiting_enabled,
                    "async_logging": config.async_logging,
                }
            else:
                health_data["components"]["tracking"] = {"status": "not_initialized"}
                health_data["status"] = "unhealthy"
        except Exception as e:
            health_data["components"]["tracking"] = {"status": "error", "error": str(e)}
            health_data["status"] = "unhealthy"

        # Check webhook service
        try:
            if hasattr(request.app, "webhook_service"):
                health_data["components"]["webhook"] = {"status": "ok"}
            else:
                health_data["components"]["webhook"] = {"status": "not_initialized"}
        except Exception as e:
            health_data["components"]["webhook"] = {"status": "error", "error": str(e)}

        # Check rate limiter
        try:
            if hasattr(request.app, "rate_limiter"):
                health_data["components"]["rate_limiter"] = {"status": "ok"}
            else:
                health_data["components"]["rate_limiter"] = {"status": "not_initialized"}
        except Exception as e:
            health_data["components"]["rate_limiter"] = {"status": "error", "error": str(e)}

        # Performance metrics (basic)
        health_data["performance"] = {
            "uptime_seconds": "not_implemented",  # Could track service start time
            "requests_processed": "not_implemented",  # Could use counters
            "last_database_check": datetime.utcnow().isoformat() + "Z",
        }

        status_code = 200 if health_data["status"] == "healthy" else 503
        return JSONResponse(health_data, status_code=status_code)

    except Exception as e:
        logger.error(f"Detailed health check error: {e}")
        return JSONResponse(
            {
                "status": "unhealthy",
                "service": "email-tracking",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@health_api.get("/ready")
async def readiness_check(request: Request):
    """
    Kubernetes-style readiness probe.

    Indicates whether the service is ready to handle requests.
    Used by Kubernetes and other orchestration systems.

    Returns:
        JSON: Readiness status
    """
    try:
        # Check if all required services are initialized and functional
        required_services = [
            "database_service",
            "tracking_service",
            "webhook_service",
            "rate_limiter",
        ]

        ready = True
        missing_services = []

        for service_name in required_services:
            if not hasattr(request.app, service_name):
                ready = False
                missing_services.append(service_name)

        # Additional check - database connectivity
        if ready and hasattr(request.app, "database_service"):
            if not request.app.database_service.test_connection():
                ready = False
                missing_services.append("database_connection")

        readiness_data = {
            "ready": ready,
            "service": "email-tracking",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        if missing_services:
            readiness_data["missing_services"] = missing_services

        status_code = 200 if ready else 503
        return JSONResponse(readiness_data, status_code=status_code)

    except Exception as e:
        logger.error(f"Readiness check error: {e}")
        return JSONResponse(
            {
                "ready": False,
                "service": "email-tracking",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )


@health_api.get("/live")
async def liveness_check():
    """
    Kubernetes-style liveness probe.

    Indicates whether the service is alive and should continue running.
    Used by Kubernetes to determine if a pod should be restarted.

    Returns:
        JSON: Liveness status
    """
    try:
        # Basic liveness check - if we can respond, we're alive
        # This is intentionally simple to avoid false positives

        liveness_data = {
            "alive": True,
            "service": "email-tracking",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        return JSONResponse(liveness_data, status_code=200)

    except Exception as e:
        logger.error(f"Liveness check error: {e}")
        return JSONResponse(
            {
                "alive": False,
                "service": "email-tracking",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            status_code=503,
        )
