#!/usr/bin/env python3
"""
Rate Limiter Service - Main Application

This service provides dynamic organization and domain-based rate limiting for
inbound/outbound emails with minimal database operations, heavy webhook integration,
and real-time tracking using modular service architecture.

Key Features:
- Modular service architecture for maintainability
- Organization, domain, and mailbox-level rate limiting
- Redis-backed high-performance counters with database fallback
- Configurable rate limits from database with intelligent caching
- Webhook-driven quota reporting and alerting
- Production-grade performance optimization
- Comprehensive error handling and monitoring

The application is split into specialized services:
- ConfigService: Manages rate limit configurations and caching
- CacheService: Handles Redis operations and distributed counters
- DatabaseService: Manages database operations and fallback storage
- UsageService: Tracks usage statistics and quota consumption
- AlertService: Manages threshold monitoring and notifications
- WebhookService: Handles notification delivery and retry logic
"""

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# Add shared directory to path for logging
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root / "shared"))

from services.alert_service import RateLimitAlertService
from services.cache_service import RateLimitCacheService
from services.config_service import RateLimitConfigService
from services.database_service import RateLimitDatabaseService
from services.send_credit_service import SendCreditService
from services.suppression_check_service import SuppressionCheckService
from services.tripwire_service import TripwireService
from services.usage_service import RateLimitUsageService
from services.webhook_service import RateLimitWebhookService

# Import configuration and services
from config import config
from shared.logging_config import LogTimer, get_performance_logger
from shared.metrics import get_metrics
from shared.webhook_dispatcher import Events, dispatch_event

# Initialize FastAPI application
app = FastAPI(title="Rate Limiter Service")

# Initialize metrics
metrics = get_metrics("rate_limiter")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    metrics.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration=duration,
    )
    return response


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint"""
    metrics_data = metrics.get_prometheus_metrics()
    return PlainTextResponse(metrics_data)


# Configure service-specific logging
logger, log_performance = get_performance_logger("rate_limiter")


class EnterpriseRateLimiter:
    """
    Main rate limiter orchestrator that coordinates all services.

    This class provides the main interface for rate limiting operations
    while delegating specific functionality to specialized services.
    It maintains service instances and handles cross-service coordination.
    """

    def __init__(self):
        """Initialize the rate limiter with all required services"""
        logger.info("Initializing Rate Limiter...")

        # Initialize all services
        self.config_service = RateLimitConfigService()
        self.cache_service = RateLimitCacheService()
        self.database_service = RateLimitDatabaseService()
        self.suppression_check_service = SuppressionCheckService(
            database_service=self.database_service
        )
        # App-sending pool and prepaid credits. Off unless
        # SEND_CREDIT_ENFORCEMENT is set -- see the service docstring.
        self.send_credit_service = SendCreditService(
            redis_client=self.cache_service.redis_client,
            config_service=self.config_service,
        )
        # Bounce-rate tripwire. Scanning is off unless TRIPWIRE_ENABLED, but
        # an existing pause key is always enforced -- see the service
        # docstring for why those are two separate decisions.
        self.tripwire_service = TripwireService(
            redis_client=self.cache_service.redis_client,
            config_service=self.config_service,
        )
        self.usage_service = RateLimitUsageService(
            cache_service=self.cache_service, database_service=self.database_service
        )
        self.alert_service = RateLimitAlertService(
            config_service=self.config_service, usage_service=self.usage_service
        )
        self.webhook_service = RateLimitWebhookService()

        # Start background services
        self._start_background_services()

        logger.info("Rate Limiter initialized successfully")

    def _start_background_services(self):
        """Start background monitoring and maintenance services"""
        try:
            # Start alert monitoring thread
            alert_thread = threading.Thread(
                target=self.alert_service.start_monitoring,
                name="rate_limit_alert_monitor",
                daemon=True,
            )
            alert_thread.start()
            logger.info("Alert monitoring service started")

            # Start cleanup thread for old usage data
            cleanup_thread = threading.Thread(
                target=self._run_cleanup_service, name="rate_limit_cleanup", daemon=True
            )
            cleanup_thread.start()
            logger.info("Cleanup service started")

            # Start the tripwire evaluator only when scanning is switched
            # on; enforcement of already-set pause keys needs no thread.
            if self.tripwire_service.enabled:
                tripwire_thread = threading.Thread(
                    target=self._run_tripwire_service, name="rate_limit_tripwire", daemon=True
                )
                tripwire_thread.start()
                logger.info("Tripwire evaluation service started")

        except Exception as e:
            logger.error(f"Failed to start background services: {e}")

    def _run_cleanup_service(self):
        """Background service to clean up old usage data"""
        cleanup_interval = config.cleanup_interval

        while True:
            try:
                time.sleep(cleanup_interval)

                # Clean up old usage records
                self.database_service.cleanup_old_usage_data(
                    retention_days=config.cleanup_retention_days
                )

                # Clean up old alert records
                self.alert_service.cleanup_old_alerts(retention_days=config.cleanup_retention_days)

                logger.info("Cleanup service completed successfully")

            except Exception as e:
                logger.error(f"Cleanup service error: {e}")
                time.sleep(60)  # Wait 1 minute on error

    def _run_tripwire_service(self):
        """Background service that evaluates the bounce-rate tripwire"""
        while True:
            try:
                time.sleep(60)
                self.tripwire_service.evaluate()

            except Exception as e:
                # evaluate() swallows its own errors; this catch only exists
                # so the thread can never die and silently stop scanning.
                logger.error(f"Tripwire service error: {e}")
                time.sleep(60)  # Wait 1 minute on error

    def check_rate_limit(self, email: str, direction: str) -> tuple[bool, str, dict[str, Any]]:
        """
        Check if email can be sent/received within rate limits.

        This method performs hierarchical rate limiting checks:
        1. Organization-level limits
        2. Domain-level limits
        3. Mailbox-level limits

        Args:
            email: Email address to check
            direction: 'inbound' or 'outbound'

        Returns:
            Tuple of (allowed, message, details)
        """
        try:
            with LogTimer(log_performance, f"rate_limit_check_{direction}"):
                # An identity with no '@' is an SMTP API key's SASL username
                # (00-PRD-smtp-api-keys K2) -- these used to fall through the
                # '@' guard in the policy bridge and bypass rate limiting
                # entirely. Resolve it to its org/domain so the same
                # hierarchical checks apply, plus a per-key rule on top.
                credential = None
                if "@" not in email:
                    try:
                        credential = self.config_service.get_smtp_credential(email)
                    except Exception as e:
                        # Infrastructure error: fail open, same contract as
                        # every other error path in this method.
                        logger.error(f"SMTP credential lookup failed for {email}: {e}")
                        return True, f"credential lookup failed: {e}", {"error": str(e)}
                    if credential is None:
                        # An AUTHENTICATED identity we cannot resolve is not
                        # an infrastructure error -- fail closed (PRD S5
                        # decision 7). Unauthenticated mail never reaches
                        # this branch: it carries a sender address with '@'.
                        return (
                            False,
                            "Unknown SMTP credential identity",
                            {"email": email, "direction": direction},
                        )
                    organization_id, domain = credential["organization_id"], credential["domain"]
                else:
                    # Extract organization and domain from email
                    organization_id, domain = self._extract_organization_and_domain(email)

                # Tripwire pause: enforced on key presence alone (so it holds
                # even with TRIPWIRE_ENABLED off), outbound only -- pausing a
                # sender must never refuse mail ADDRESSED to them, the exact
                # mistake that broke all inbound on 2026-08-22. A broken
                # Redis fails open inside is_paused.
                if direction == "outbound":
                    pause_reason = self.tripwire_service.is_paused(organization_id)
                    if pause_reason:
                        return (
                            False,
                            f"sending paused: {pause_reason}",
                            {
                                "email": email,
                                "organization_id": organization_id,
                                "domain": domain,
                                "direction": direction,
                                "checks": {"tripwire": {"paused": True, "reason": pause_reason}},
                                "timestamp": datetime.now().isoformat(),
                            },
                        )

                    # App-sending pool and prepaid credits. /policy carries its
                    # own copy of this gate, but the live SMTP path consults
                    # /check_rate_limit (USE_POLICY_ENDPOINT defaults false),
                    # which lands here -- without this, SEND_CREDIT_ENFORCEMENT
                    # could never bite. Key-authenticated senders only (a
                    # resolved credential, i.e. an application): a person
                    # sending from their own mailbox is governed by their
                    # per-mailbox daily limit and must never be stopped because
                    # the company's API allowance ran out. check() allows when
                    # enforcement is off and on every error path, so this adds
                    # no new failure mode to the mail path.
                    if credential is not None:
                        credit_ok, credit_reason = self.send_credit_service.check(organization_id)
                        if not credit_ok:
                            logger.warning(
                                f"Send credits exhausted for org {organization_id} ({email}): "
                                f"{credit_reason}"
                            )
                            return (
                                False,
                                f"Sending credits exhausted: {credit_reason}",
                                {
                                    "email": email,
                                    "organization_id": organization_id,
                                    "domain": domain,
                                    "direction": direction,
                                    "checks": {
                                        "send_credits": {
                                            "allowed": False,
                                            "reason": credit_reason,
                                        }
                                    },
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )

                # Prepare response details
                details = {
                    "email": email,
                    "organization_id": organization_id,
                    "domain": domain,
                    "direction": direction,
                    "checks": {},
                    "timestamp": datetime.now().isoformat(),
                }

                # Perform hierarchical rate limit checks
                if credential is not None:
                    checks = [
                        ("organization", organization_id),
                        ("domain", domain),
                        ("smtp_credential", email),
                    ]
                else:
                    checks = [
                        ("organization", organization_id),
                        ("domain", domain),
                    ]
                    # The per-sender mailbox rule (mailbox_inbound_second
                    # defaults to 1/sec) exists to stop a compromised mailbox
                    # WE host from spewing. An inbound message's sender is
                    # usually external, and get_organization_for_domain falls
                    # back to the bare domain string when we don't host it --
                    # so applying our tenant-mailbox burst limit to that
                    # sender 450'd legitimate mail: Microsoft delivering the
                    # SNDS confirmation and a status update in the same
                    # second tripped "Per-second limit (1) exceeded"
                    # (2026-09-06). Any big sender (Google, Microsoft, DMARC
                    # report aggregators) bursts like that. Rate-limit a
                    # sender as a mailbox only when it is actually one of
                    # ours: outbound (it authenticated to send), or a sender
                    # whose domain resolved to a real hosted organization.
                    hosted_sender = organization_id != domain
                    if direction == "outbound" or hosted_sender:
                        checks.append(("mailbox", email))

                for entity_type, identifier in checks:
                    rule_override = None
                    if entity_type == "smtp_credential":
                        rule_override = self._credential_rule(credential, direction)
                    allowed, message, check_details = self._check_single_entity_limit(
                        entity_type, identifier, direction, rule_override
                    )

                    details["checks"][entity_type] = check_details

                    if not allowed:
                        logger.warning(
                            f"Rate limit exceeded for {entity_type} {identifier}: {message}",
                            extra={
                                "entity_type": entity_type,
                                "identifier": identifier,
                                "direction": direction,
                                "email": email,
                            },
                        )
                        return (
                            False,
                            f"{entity_type.title()} rate limit exceeded: {message}",
                            details,
                        )

                logger.debug(f"Rate limit check passed for {email} ({direction})")
                return True, "Rate limit check passed", details

        except Exception as e:
            logger.error(f"Rate limit check error for {email}: {e}")
            # Fail open - allow the request if there's an error
            return True, f"Rate limit check failed: {str(e)}", {"error": str(e)}

    def _extract_organization_and_domain(self, email: str) -> tuple[str, str]:
        """
        Extract organization and domain from email address.

        Args:
            email: Email address to parse

        Returns:
            Tuple of (organization_id, domain)
        """
        if "@" not in email:
            return "unknown", "unknown"

        local_part, domain = email.split("@", 1)

        # Get organization ID for the domain
        organization_id = self.config_service.get_organization_for_domain(domain)

        return organization_id, domain

    def check_recipient_suppression(self, sender_identity: str, recipient: str) -> tuple[bool, str]:
        """Is `recipient` on the sending org's suppression list?

        sender_identity is the same value check_rate_limit keys on: the SASL
        username (mailbox address, or an SMTP API key's '@'-less username)
        for authenticated submissions. The org is resolved exactly the way
        the rate-limit path resolves it, so both features always agree on
        who is sending.

        Returns (suppressed, reject_message). FAIL OPEN on every error and
        every unresolvable identity -- unlike the rate-limit path's
        fail-closed rule for unknown credentials, an outage or gap in
        suppression data must never stop mail (it only re-opens the
        pre-enforcement gap for its duration, which is the acceptable
        failure mode).
        """
        try:
            if "@" not in sender_identity:
                credential = self.config_service.get_smtp_credential(sender_identity)
                if not credential:
                    return False, ""
                organization_id = credential["organization_id"]
            else:
                organization_id, _ = self._extract_organization_and_domain(sender_identity)

            # 'unknown' is _extract_organization_and_domain's can't-parse
            # sentinel; a domain-name fallback org simply has no suppression
            # rows, so it needs no special case.
            if not organization_id or organization_id == "unknown":
                return False, ""

            suppressed, suppression_type = self.suppression_check_service.is_suppressed(
                organization_id, recipient
            )
            if suppressed:
                label = f" ({suppression_type})" if suppression_type else ""
                return True, (
                    f"5.7.1 Recipient address <{recipient}> is on your organization's "
                    f"suppression list{label}. Remove the suppression via the API "
                    f"before sending to this address again."
                )
            return False, ""

        except Exception as e:
            logger.error(f"Suppression check error for {sender_identity} -> {recipient}: {e}")
            return False, ""  # fail open

    def _credential_rule(self, credential: dict, direction: str):
        """Per-key rule built from the smtp_credentials row itself rather
        than the rate_limit_rules table -- key limits live with the key
        (managed by the console/API), NULL means inherit, i.e. no per-key
        bound on that window (0 = unlimited in RateLimitRule semantics)."""
        from services.config_service import RateLimitRule

        return RateLimitRule(
            entity_type="smtp_credential",
            identifier=credential["username"],
            direction=direction,
            hourly_limit=int(credential.get("hourly_limit") or 0),
            daily_limit=int(credential.get("daily_limit") or 0),
        )

    def _check_single_entity_limit(
        self, entity_type: str, identifier: str, direction: str, rule_override=None
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Check rate limit for a single entity (organization/domain/mailbox/
        smtp_credential).

        Args:
            entity_type: 'organization', 'domain', 'mailbox' or 'smtp_credential'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            rule_override: Skip the rate_limit_rules lookup and use this rule
                (per-key limits come from the smtp_credentials row)

        Returns:
            Tuple of (allowed, message, details)
        """
        try:
            # Get rate limit configuration
            config_rule = rule_override or self.config_service.get_rate_limit_rule(
                entity_type, identifier, direction
            )

            # Get current usage statistics
            usage_stats = self.usage_service.get_current_usage(entity_type, identifier, direction)

            # Prepare check details
            check_details = {
                "entity_type": entity_type,
                "identifier": identifier,
                "direction": direction,
                "config": {
                    "hourly_limit": config_rule.hourly_limit,
                    "daily_limit": config_rule.daily_limit,
                    "monthly_limit": config_rule.monthly_limit,
                    "burst_limit": config_rule.burst_limit,
                    "active": config_rule.active,
                },
                "usage": usage_stats,
                "percentages": {},
            }

            # Check if rate limiting is active for this entity
            if not config_rule.active:
                logger.debug(f"Rate limiting disabled for {entity_type} {identifier}")
                return True, "Rate limiting disabled", check_details

            # Calculate usage percentages only for limits that are set
            percentages = {}

            if config_rule.second_limit > 0:
                percentages["second"] = (
                    usage_stats["second_count"] / config_rule.second_limit
                ) * 100

            if config_rule.minute_limit > 0:
                percentages["minute"] = (
                    usage_stats["minute_count"] / config_rule.minute_limit
                ) * 100

            if config_rule.hourly_limit > 0:
                percentages["hourly"] = (
                    usage_stats["hourly_count"] / config_rule.hourly_limit
                ) * 100

            if config_rule.daily_limit > 0:
                percentages["daily"] = (usage_stats["daily_count"] / config_rule.daily_limit) * 100

            if config_rule.monthly_limit > 0:
                percentages["monthly"] = (
                    usage_stats["monthly_count"] / config_rule.monthly_limit
                ) * 100

            check_details["percentages"] = percentages

            # Check against limits (prioritize based on value availability - spam prevention first)
            # Only check limits that are actually set (> 0)

            if (
                config_rule.second_limit > 0
                and usage_stats["second_count"] >= config_rule.second_limit
            ):
                dispatch_event(
                    Events.RATE_LIMIT_EXCEEDED,
                    data={
                        "entity_type": entity_type,
                        "identifier": identifier,
                        "direction": direction,
                        "window": "second",
                        "current_count": usage_stats["second_count"],
                        "limit": config_rule.second_limit,
                        "message": "Per-second limit exceeded - anti-spam protection",
                    },
                    org_id=identifier if entity_type == "organization" else None,
                    domain=identifier if entity_type == "domain" else None,
                    source_service="rate_limiter",
                )
                return (
                    False,
                    f"Per-second limit ({config_rule.second_limit}) exceeded - anti-spam protection",
                    check_details,
                )

            if (
                config_rule.minute_limit > 0
                and usage_stats["minute_count"] >= config_rule.minute_limit
            ):
                dispatch_event(
                    Events.RATE_LIMIT_EXCEEDED,
                    data={
                        "entity_type": entity_type,
                        "identifier": identifier,
                        "direction": direction,
                        "window": "minute",
                        "current_count": usage_stats["minute_count"],
                        "limit": config_rule.minute_limit,
                        "message": "Per-minute limit exceeded - burst protection",
                    },
                    org_id=identifier if entity_type == "organization" else None,
                    domain=identifier if entity_type == "domain" else None,
                    source_service="rate_limiter",
                )
                return (
                    False,
                    f"Per-minute limit ({config_rule.minute_limit}) exceeded - burst protection",
                    check_details,
                )

            if (
                config_rule.hourly_limit > 0
                and usage_stats["hourly_count"] >= config_rule.hourly_limit
            ):
                dispatch_event(
                    Events.RATE_LIMIT_EXCEEDED,
                    data={
                        "entity_type": entity_type,
                        "identifier": identifier,
                        "direction": direction,
                        "window": "hourly",
                        "current_count": usage_stats["hourly_count"],
                        "limit": config_rule.hourly_limit,
                        "message": "Hourly limit exceeded",
                    },
                    org_id=identifier if entity_type == "organization" else None,
                    domain=identifier if entity_type == "domain" else None,
                    source_service="rate_limiter",
                )
                return False, f"Hourly limit ({config_rule.hourly_limit}) exceeded", check_details

            if (
                config_rule.daily_limit > 0
                and usage_stats["daily_count"] >= config_rule.daily_limit
            ):
                dispatch_event(
                    Events.RATE_LIMIT_EXCEEDED,
                    data={
                        "entity_type": entity_type,
                        "identifier": identifier,
                        "direction": direction,
                        "window": "daily",
                        "current_count": usage_stats["daily_count"],
                        "limit": config_rule.daily_limit,
                        "message": "Daily limit exceeded",
                    },
                    org_id=identifier if entity_type == "organization" else None,
                    domain=identifier if entity_type == "domain" else None,
                    source_service="rate_limiter",
                )
                return False, f"Daily limit ({config_rule.daily_limit}) exceeded", check_details

            if (
                config_rule.monthly_limit > 0
                and usage_stats["monthly_count"] >= config_rule.monthly_limit
            ):
                dispatch_event(
                    Events.RATE_LIMIT_EXCEEDED,
                    data={
                        "entity_type": entity_type,
                        "identifier": identifier,
                        "direction": direction,
                        "window": "monthly",
                        "current_count": usage_stats["monthly_count"],
                        "limit": config_rule.monthly_limit,
                        "message": "Monthly limit exceeded",
                    },
                    org_id=identifier if entity_type == "organization" else None,
                    domain=identifier if entity_type == "domain" else None,
                    source_service="rate_limiter",
                )
                return False, f"Monthly limit ({config_rule.monthly_limit}) exceeded", check_details

            # Check for threshold warnings (approaching limits but not yet exceeded)
            for window_name, count_key, limit_val in [
                ("hourly", "hourly_count", config_rule.hourly_limit),
                ("daily", "daily_count", config_rule.daily_limit),
                ("monthly", "monthly_count", config_rule.monthly_limit),
            ]:
                if limit_val > 0:
                    pct = (usage_stats[count_key] / limit_val) * 100
                    if pct >= config_rule.warning_threshold:
                        dispatch_event(
                            Events.RATE_LIMIT_THRESHOLD,
                            data={
                                "entity_type": entity_type,
                                "identifier": identifier,
                                "direction": direction,
                                "window": window_name,
                                "current_count": usage_stats[count_key],
                                "limit": limit_val,
                                "usage_percentage": round(pct, 2),
                                "threshold_level": "critical"
                                if pct >= config_rule.critical_threshold
                                else "warning",
                            },
                            org_id=identifier if entity_type == "organization" else None,
                            domain=identifier if entity_type == "domain" else None,
                            source_service="rate_limiter",
                        )

            return True, "Within limits", check_details

        except Exception as e:
            logger.error(f"Error checking limits for {entity_type} {identifier}: {e}")
            # Fail open on errors
            return True, f"Check failed: {str(e)}", {"error": str(e)}

    def increment_usage(self, email: str, direction: str, amount: int = 1) -> bool:
        """
        Increment usage counters after successful email processing.

        Args:
            email: Email address
            direction: 'inbound' or 'outbound'
            amount: Number of emails to increment (default: 1)

        Returns:
            bool: True if increment was successful
        """
        try:
            with LogTimer(log_performance, f"usage_increment_{direction}"):
                # Same identity resolution as check_rate_limit: an SMTP API
                # key counts against its org, its domain, and itself.
                if "@" not in email:
                    credential = self.config_service.get_smtp_credential(email)
                    if credential is None:
                        logger.warning(f"increment_usage: unknown SMTP credential {email}")
                        return False
                    organization_id, domain = (
                        credential["organization_id"],
                        credential["domain"],
                    )
                    entities = [
                        ("organization", organization_id),
                        ("domain", domain),
                        ("smtp_credential", email),
                    ]
                else:
                    # Extract organization and domain
                    organization_id, domain = self._extract_organization_and_domain(email)

                    # Increment usage for all levels
                    entities = [
                        ("organization", organization_id),
                        ("domain", domain),
                        ("mailbox", email),
                    ]

                success = True
                for entity_type, identifier in entities:
                    if not self.usage_service.increment_usage(
                        entity_type, identifier, direction, amount
                    ):
                        success = False
                        logger.warning(f"Failed to increment usage for {entity_type} {identifier}")

                # Trigger alert checks after usage increment
                if success:
                    self.alert_service.check_thresholds(organization_id, domain, email, direction)

                logger.debug(f"Usage incremented for {email} ({direction}) by {amount}")
                return success

        except Exception as e:
            logger.error(f"Error incrementing usage for {email}: {e}")
            return False

    def get_usage_statistics(
        self, entity_type: str, identifier: str, direction: str
    ) -> dict[str, Any]:
        """
        Get comprehensive usage statistics for an entity.

        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'

        Returns:
            Dict containing usage statistics and limits
        """
        try:
            # Get configuration and usage
            config_rule = self.config_service.get_rate_limit_rule(
                entity_type, identifier, direction
            )
            usage_stats = self.usage_service.get_current_usage(entity_type, identifier, direction)

            # Calculate percentages and remaining quotas
            percentages = {}
            remaining = {}

            if config_rule.hourly_limit > 0:
                percentages["hourly"] = (
                    usage_stats["hourly_count"] / config_rule.hourly_limit
                ) * 100
                remaining["hourly"] = max(0, config_rule.hourly_limit - usage_stats["hourly_count"])

            if config_rule.daily_limit > 0:
                percentages["daily"] = (usage_stats["daily_count"] / config_rule.daily_limit) * 100
                remaining["daily"] = max(0, config_rule.daily_limit - usage_stats["daily_count"])

            if config_rule.monthly_limit > 0:
                percentages["monthly"] = (
                    usage_stats["monthly_count"] / config_rule.monthly_limit
                ) * 100
                remaining["monthly"] = max(
                    0, config_rule.monthly_limit - usage_stats["monthly_count"]
                )

            return {
                "entity_type": entity_type,
                "identifier": identifier,
                "direction": direction,
                "config": {
                    "hourly_limit": config_rule.hourly_limit,
                    "daily_limit": config_rule.daily_limit,
                    "monthly_limit": config_rule.monthly_limit,
                    "burst_limit": config_rule.burst_limit,
                    "active": config_rule.active,
                    "warning_threshold": config_rule.warning_threshold,
                    "critical_threshold": config_rule.critical_threshold,
                },
                "usage": usage_stats,
                "percentages": percentages,
                "remaining": remaining,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error getting usage statistics: {e}")
            return {"error": str(e)}

    def get_service_health(self) -> dict[str, Any]:
        """
        Get health status of all rate limiter services.

        Returns:
            Dict containing health status of all services
        """
        return {
            "service": "rate_limiter",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "config_service": self.config_service.get_config_stats(),
                "cache_service": self.cache_service.get_cache_stats(),
                "database_service": self.database_service.get_database_stats(),
                "usage_service": self.usage_service.get_usage_stats(),
                "alert_service": self.alert_service.get_alert_stats(),
                "webhook_service": self.webhook_service.get_webhook_stats(),
            },
        }


# Initialize global rate limiter instance
rate_limiter = EnterpriseRateLimiter()

# === API Endpoints ===


@app.post("/check_rate_limit")
async def check_rate_limit(request: Request):
    """
    Check if sender can send/receive email within rate limits.

    Request body:
    {
        "email": "user@example.com",
        "direction": "outbound"  // or "inbound"
    }

    Response:
    {
        "allowed": true,
        "message": "Rate limit check passed",
        "email": "user@example.com",
        "direction": "outbound",
        "details": {...}
    }
    """
    try:
        data = await request.json()

        # Validate required fields
        email = data.get("email", data.get("sender", ""))
        direction = data.get("direction", "outbound")

        if not email:
            return JSONResponse(
                {
                    "allowed": False,
                    "message": "No email address specified",
                    "error": "missing_email",
                },
                status_code=400,
            )

        if direction not in ["inbound", "outbound"]:
            return JSONResponse(
                {
                    "allowed": False,
                    "message": "Invalid direction, must be inbound or outbound",
                    "error": "invalid_direction",
                },
                status_code=400,
            )

        # Outbound recipient suppression -- the enforcement point for the
        # suppression list (docs/features/deliverability.md). Checked before
        # the rate limit so a suppressed recipient gets the permanent 5.7.1
        # immediately (not a 4.x defer first) and never consumes quota.
        # Outbound only: suppressions mean "our tenant must not mail this
        # address", never "block inbound mail". The Postfix policy protocol
        # carries `recipient` at the DATA stage only when the message has
        # exactly one recipient, so the bridge sends it when it exists;
        # multi-recipient messages are logged and pass unchecked -- an
        # honest, visible gap rather than a silent half-enforcement.
        recipient = str(data.get("recipient") or "").strip()
        if direction == "outbound":
            if recipient:
                suppressed, sup_message = rate_limiter.check_recipient_suppression(email, recipient)
                if suppressed:
                    logger.warning(
                        f"Suppressed recipient rejected: {recipient} (sender identity {email})"
                    )
                    return JSONResponse(
                        {
                            "allowed": False,
                            "suppressed": True,
                            "message": sup_message,
                            "email": email,
                            "recipient": recipient,
                            "direction": direction,
                        },
                        status_code=403,
                    )
            else:
                try:
                    recipient_count = int(data.get("recipient_count") or 0)
                except (TypeError, ValueError):
                    recipient_count = 0
                if recipient_count > 1:
                    logger.info(
                        f"Suppression check skipped: multi-recipient message from {email} "
                        f"({recipient_count} recipients; DATA-stage policy carries no "
                        f"per-recipient addresses)"
                    )

        # Perform rate limit check
        allowed, message, details = rate_limiter.check_rate_limit(email, direction)

        # The Postfix policy bridge sends record=true so that each allowed
        # message actually counts against the window -- without this, no
        # producer incremented usage anywhere and every limit compared
        # against zero (found during 00-PRD-smtp-api-keys K2). Other callers
        # (dashboards, pre-flight checks) omit it and stay read-only.
        if allowed and data.get("record"):
            # Count the allowed message against the app-sending pool and, past
            # the allowance, the credit balance -- mirroring /policy's
            # post-allow record. Outbound key-authenticated senders only (an
            # identity without '@' is an SMTP credential); the org id comes
            # from the check's details, and record() is fail-open, so a
            # missing id or a Redis blip never blocks a message already
            # judged allowed.
            if direction == "outbound" and "@" not in email:
                rate_limiter.send_credit_service.record(details.get("organization_id"))
            rate_limiter.increment_usage(email, direction)

        response = {
            "allowed": allowed,
            "message": message,
            "email": email,
            "direction": direction,
            "details": details,
        }

        status_code = 200 if allowed else 429  # 429 Too Many Requests
        return JSONResponse(response, status_code=status_code)

    except Exception as e:
        logger.error(f"Rate limit check API error: {e}")
        return JSONResponse(
            {"allowed": False, "message": "Rate limit check failed", "error": "internal_error"},
            status_code=500,
        )


@app.post("/increment_usage")
async def increment_usage(request: Request):
    """
    Increment usage counters after successful email processing.

    Request body:
    {
        "email": "user@example.com",
        "direction": "outbound",
        "amount": 1
    }
    """
    try:
        data = await request.json()

        email = data.get("email", data.get("sender", ""))
        direction = data.get("direction", "outbound")
        amount = data.get("amount", 1)

        if not email:
            return JSONResponse(
                {"success": False, "message": "No email address specified"}, status_code=400
            )

        if direction not in ["inbound", "outbound"]:
            return JSONResponse({"success": False, "message": "Invalid direction"}, status_code=400)

        if not isinstance(amount, int) or amount <= 0:
            return JSONResponse(
                {"success": False, "message": "Invalid amount, must be positive integer"},
                status_code=400,
            )

        success = rate_limiter.increment_usage(email, direction, amount)

        return {"success": success, "email": email, "direction": direction, "amount": amount}

    except Exception as e:
        logger.error(f"Usage increment API error: {e}")
        return JSONResponse(
            {"success": False, "message": "Usage increment failed"}, status_code=500
        )


@app.get("/get_usage/{entity_type}/{identifier}")
async def get_usage(entity_type: str, identifier: str, direction: str = "outbound"):
    """
    Get usage statistics for organization/domain/mailbox.

    Path parameters:
    - entity_type: organization, domain, or mailbox
    - identifier: organization ID, domain name, or email address

    Query parameters:
    - direction: inbound or outbound (default: outbound)
    """
    try:
        if entity_type not in ["organization", "domain", "mailbox"]:
            return JSONResponse({"error": "Invalid entity type"}, status_code=400)

        if direction not in ["inbound", "outbound"]:
            return JSONResponse({"error": "Invalid direction"}, status_code=400)

        stats = rate_limiter.get_usage_statistics(entity_type, identifier, direction)

        return {"success": True, "data": stats}

    except Exception as e:
        logger.error(f"Get usage API error: {e}")
        return JSONResponse({"error": "Failed to get usage statistics"}, status_code=500)


@app.post("/set_limits")
async def set_limits(request: Request):
    """
    Set rate limits for organization/domain/mailbox.

    Request body:
    {
        "type": "organization",
        "identifier": "example.com",
        "direction": "outbound",
        "hourly_limit": 1000,
        "daily_limit": 10000,
        "monthly_limit": 100000,
        "burst_limit": 200,
        "active": true,
        "warning_threshold": 80,
        "critical_threshold": 95,
        "description": "Custom limits for organization"
    }
    """
    try:
        data = await request.json()

        # Validate required fields
        required_fields = ["type", "identifier", "direction"]
        for field in required_fields:
            if field not in data:
                return JSONResponse({"error": f"Missing required field: {field}"}, status_code=400)

        if data["type"] not in ["organization", "domain", "mailbox"]:
            return JSONResponse({"error": "Invalid entity type"}, status_code=400)

        if data["direction"] not in ["inbound", "outbound"]:
            return JSONResponse({"error": "Invalid direction"}, status_code=400)

        # Create rate limit rule with flexible time windows
        from services.config_service import RateLimitRule

        rule = RateLimitRule(
            entity_type=data["type"],
            identifier=data["identifier"],
            direction=data["direction"],
            second_limit=data.get("second_limit", 0),  # 0 means no limit
            minute_limit=data.get("minute_limit", 0),
            hourly_limit=data.get("hourly_limit", 0),
            daily_limit=data.get("daily_limit", 0),
            monthly_limit=data.get("monthly_limit", 0),
            burst_limit=data.get("burst_limit", 0),
            active=data.get("active", True),
            priority=data.get("priority", 1),
            warning_threshold=data.get("warning_threshold", 80),
            critical_threshold=data.get("critical_threshold", 95),
            description=data.get("description"),
            created_by=data.get("created_by", "api"),
        )

        success = rate_limiter.config_service.create_rate_limit_rule(rule)

        if success:
            dispatch_event(
                Events.RATE_LIMIT_RESET,
                data={
                    "entity_type": data["type"],
                    "identifier": data["identifier"],
                    "direction": data["direction"],
                    "hourly_limit": data.get("hourly_limit", 0),
                    "daily_limit": data.get("daily_limit", 0),
                    "monthly_limit": data.get("monthly_limit", 0),
                    "active": data.get("active", True),
                    "updated_by": data.get("created_by", "api"),
                },
                source_service="rate_limiter",
            )
            return {"success": True, "message": "Rate limits updated successfully"}
        else:
            return JSONResponse(
                {"success": False, "message": "Failed to update rate limits"}, status_code=500
            )

    except Exception as e:
        logger.error(f"Set limits API error: {e}")
        return JSONResponse({"error": "Failed to set rate limits"}, status_code=500)


@app.post("/reset_counters")
async def reset_counters(request: Request):
    """
    Reset usage counters for organization/domain/mailbox.

    Request body:
    {
        "type": "domain",
        "identifier": "example.com",
        "direction": "outbound"
    }
    """
    try:
        data = await request.json()

        entity_type = data.get("type")
        identifier = data.get("identifier")
        direction = data.get("direction", "outbound")

        if entity_type not in ["organization", "domain", "mailbox"]:
            return JSONResponse(
                {"success": False, "message": "Invalid entity type"}, status_code=400
            )

        if not identifier:
            return JSONResponse(
                {"success": False, "message": "Missing identifier"}, status_code=400
            )

        if direction not in ["inbound", "outbound"]:
            return JSONResponse({"success": False, "message": "Invalid direction"}, status_code=400)

        success = rate_limiter.cache_service.reset_counters(entity_type, identifier, direction)

        if success:
            dispatch_event(
                Events.RATE_LIMIT_RESET,
                data={"entity_type": entity_type, "identifier": identifier, "direction": direction},
                source_service="rate_limiter",
            )
            return {
                "success": True,
                "message": "Counters reset successfully",
                "entity_type": entity_type,
                "identifier": identifier,
                "direction": direction,
            }

        return JSONResponse(
            {"success": False, "message": "Failed to reset counters"}, status_code=500
        )

    except Exception as e:
        logger.error(f"Reset counters API error: {e}")
        return JSONResponse(
            {"success": False, "message": "Failed to reset counters"}, status_code=500
        )


@app.post("/policy")
async def postfix_policy(request: Request):
    """
    Postfix policy delegation endpoint.

    Postfix sends key=value pairs (one per line, blank line terminates)
    as the request body. We parse them, check the rate limiter, and
    return the appropriate policy action as plain text.

    Responses follow the Postfix policy protocol:
      action=DUNNO                                     -> allow (let other restrictions decide)
      action=DEFER_IF_PERMIT 4.7.1 Rate limit exceeded -> soft reject
    """
    try:
        # Parse the Postfix policy request body (plain text key=value pairs)
        body = await request.body()
        body_text = body.decode("utf-8", errors="replace")

        attrs = {}
        for line in body_text.splitlines():
            line = line.strip()
            if not line:
                break
            if "=" in line:
                key, value = line.split("=", 1)
                attrs[key.strip()] = value.strip()

        # Extract the sender identity — prefer sasl_username for authenticated users
        sender = attrs.get("sender", "")
        sasl_username = attrs.get("sasl_username", "")
        recipient = attrs.get("recipient", "")

        email = sasl_username if sasl_username else sender
        # An authenticated identity is always checked -- an SMTP API key's
        # SASL username has no '@' and used to slip through this guard,
        # exempting every key from rate limiting (00-PRD-smtp-api-keys 4.5).
        # Only an unauthenticated message with no sender passes through.
        if not email or (not sasl_username and "@" not in email):
            # Cannot determine sender; pass through
            return PlainTextResponse("action=DUNNO\n\n")

        # Determine direction based on protocol_state / context
        # If the sender is authenticated (sasl_username present), this is outbound.
        # Otherwise treat as inbound.
        direction = "outbound" if sasl_username else "inbound"

        # Outbound recipient suppression -- same check, ordering and
        # reasoning as /check_rate_limit above (this endpoint is the
        # alternative wire format of the same policy consultation). REJECT
        # is a permanent 554 5.7.1 delivered to the sender in-session:
        # refused loudly, never dropped.
        if direction == "outbound" and recipient:
            suppressed, sup_message = rate_limiter.check_recipient_suppression(email, recipient)
            if suppressed:
                logger.warning(
                    f"Policy: suppressed recipient rejected: {recipient} (sender identity {email})"
                )
                return PlainTextResponse(f"action=REJECT {sup_message}\n\n")

        # Check the rate limiter using the existing internal method
        allowed, message, details = rate_limiter.check_rate_limit(email, direction)

        if allowed:
            # App-sending pool and prepaid credits, consulted HERE and nowhere
            # else -- this is the single consultation point, and adding a
            # second one double-counts every message (2026-08-27 outage).
            #
            # Key-authenticated senders only: a SASL username without '@' is
            # an SMTP credential, i.e. an application. A person sending from
            # their own mailbox is governed by their per-mailbox daily limit
            # and must never be stopped because the company's API allowance
            # ran out.
            if direction == "outbound" and sasl_username and "@" not in sasl_username:
                organization_id = details.get("organization_id")
                credit_ok, credit_reason = rate_limiter.send_credit_service.check(organization_id)

                if not credit_ok:
                    logger.warning(
                        f"Policy: out of sending credits for org {organization_id} ({email}): "
                        f"{credit_reason}"
                    )
                    # DEFER, not REJECT: the customer topping up should let the
                    # queued mail through rather than having bounced it.
                    return PlainTextResponse(
                        "action=DEFER_IF_PERMIT 4.7.1 Sending credits exhausted. "
                        "Top up to resume sending.\n\n"
                    )

                rate_limiter.send_credit_service.record(organization_id)

            # This endpoint IS the mail path (one query per RCPT), so an
            # allowed message must count against the window here -- usage
            # had no other producer.
            rate_limiter.increment_usage(email, direction)
            return PlainTextResponse("action=DUNNO\n\n")
        else:
            logger.warning(
                f"Policy: rate limit exceeded for {email} ({direction}): {message}",
                extra={"sender": sender, "sasl_username": sasl_username, "recipient": recipient},
            )
            return PlainTextResponse(
                f"action=DEFER_IF_PERMIT 4.7.1 Rate limit exceeded for {email}. Try again later.\n\n"
            )

    except Exception as e:
        logger.error(f"Policy endpoint error: {e}")
        # Fail open — do not block mail on internal errors
        return PlainTextResponse("action=DUNNO\n\n")


@app.post("/send_credits/{organization_id}")
async def set_send_credits(organization_id: str, request: Request):
    """Push an organization's credit balance and app-sending allowance down.

    Laravel owns both numbers -- it is the ledger of record -- and this is the
    enforced copy the send path can read fast enough to consult per message.
    On any disagreement Laravel wins; this is a cache with teeth, not a
    second source of truth.
    """
    try:
        data = await request.json()

        if "credit_balance" in data:
            rate_limiter.send_credit_service.set_balance(
                organization_id, int(data["credit_balance"])
            )
        if "app_send_monthly" in data:
            rate_limiter.send_credit_service.set_pool_allowance(
                organization_id, int(data["app_send_monthly"])
            )
        if "dedicated_ip" in data:
            rate_limiter.send_credit_service.set_dedicated_ip(
                organization_id, bool(data["dedicated_ip"])
            )

        return {
            "success": True,
            "message": "Send credit state updated",
            "data": rate_limiter.send_credit_service.snapshot(organization_id),
        }
    except Exception as e:
        logger.error(f"Failed to set send credits for {organization_id}: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/send_credits/{organization_id}")
async def get_send_credits(organization_id: str):
    """What enforcement currently believes, for reconciliation against the
    Laravel ledger."""
    return {
        "success": True,
        "message": "Send credit state retrieved",
        "data": rate_limiter.send_credit_service.snapshot(organization_id),
    }


@app.get("/tripwire/{organization_id}")
async def get_tripwire(organization_id: str):
    """Pause state and current window numbers for one organization --
    what an operator needs to judge a pause before deciding to lift it."""
    return {
        "success": True,
        "message": "Tripwire state retrieved",
        "data": rate_limiter.tripwire_service.snapshot(organization_id),
    }


@app.delete("/tripwire/{organization_id}")
async def release_tripwire(organization_id: str):
    """Manually lift a tripwire pause.

    An override of the pause TTL, not of the thresholds: if the window is
    still bad the next evaluation pass trips again within a minute, so
    releasing without fixing the underlying list buys exactly that long.
    """
    try:
        released = rate_limiter.tripwire_service.release(organization_id)

        return {
            "success": True,
            "message": "Tripwire pause released"
            if released
            else "Organization was not paused",
            "data": rate_limiter.tripwire_service.snapshot(organization_id),
        }
    except Exception as e:
        logger.error(f"Failed to release tripwire for {organization_id}: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/health")
async def health_check():
    """Health check endpoint with detailed service status"""
    try:
        health_data = rate_limiter.get_service_health()
        return health_data
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return JSONResponse(
            {"service": "rate_limiter", "status": "unhealthy", "error": str(e)}, status_code=500
        )


@app.get("/stats")
async def get_stats():
    """Get comprehensive rate limiter statistics"""
    try:
        return {
            "service": "rate_limiter",
            "version": "2.0.0",
            "config": {
                "service_name": config.service_name,
                "cache_ttl": config.cache.rate_limit_cache_ttl,
                "alert_thresholds": {
                    "warning": config.alerts.warning_threshold,
                    "critical": config.alerts.critical_threshold,
                },
            },
            "health": rate_limiter.get_service_health(),
        }
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return JSONResponse({"error": "Failed to get statistics"}, status_code=500)


# === Application Entry Point ===

if __name__ == "__main__":
    try:
        import uvicorn

        logger.info(f"Starting Rate Limiter on {config.service_host}:{config.service_port}")
        uvicorn.run(app, host=config.service_host, port=config.service_port)
    except Exception as e:
        logger.error(f"Failed to start rate limiter service: {e}")
        sys.exit(1)
