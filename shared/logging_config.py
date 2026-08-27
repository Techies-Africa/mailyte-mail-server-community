#!/usr/bin/env python3
"""
Shared Logging Configuration

This module provides a standardized logging configuration for all services
in the Mailyte mail server. It ensures consistent log formatting,
proper file rotation, and appropriate log levels across all components.

Features:
- Structured logging with timestamps and service identification
- Automatic log rotation to prevent disk space issues
- Console and file output with different log levels
- Service-specific log files in organized directory structure
"""

import logging
import logging.handlers
import os
import re
from datetime import datetime
from pathlib import Path


class RedactingFilter(logging.Filter):
    """Defence-in-depth against secrets reaching logs (phase-07 H2).

    The real fix is never putting a secret in a log call in the first place
    -- this filter exists for whatever call site nobody's caught yet, current
    or future. Matches `key=value` / `key: value` / `key" : "value` shapes
    where the key looks like a credential, case-insensitively, and redacts
    only the value so the rest of the line stays useful for debugging.
    """

    # No leading \b: real secret names in this codebase are compound
    # (DB_PASSWORD, WEBHOOK_SECRET, ADMIN_TOKEN_SECRET) and `_` counts as a
    # word character, so `\bpassword\b` never matches the "PASSWORD" in
    # "DB_PASSWORD" at all -- caught by testing this filter directly rather
    # than trusting the pattern on sight. A trailing \b is kept: the
    # keyword still needs to end at a whole word, just not begin at one.
    _PATTERN = re.compile(
        r'(?i)(password|secret|token|api[_-]?key|private[_-]?key)\b("?\s*[:=]\s*"?)([^\s,"\'}]+)'
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # This codebase logs with f-strings (already-interpolated text), not
        # %-style logging.info("...%s", value) -- record.msg is the full
        # message by the time it reaches a filter, so matching against it
        # directly is sufficient; record.args is empty in practice here.
        if isinstance(record.msg, str) and self._PATTERN.search(record.msg):
            record.msg = self._PATTERN.sub(r"\1\2***REDACTED***", record.msg)
        return True


class ServiceLogger:
    """
    Centralized logging configuration for all mail server services.

    Creates service-specific log files in the logs directory with proper
    rotation and formatting.
    """

    def __init__(self, service_name: str, log_level: str = "INFO"):
        """
        Initialize logger for a specific service.

        Args:
            service_name: Name of the service (e.g., 'tracking', 'health_monitor')
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        self.service_name = service_name
        self.log_level = getattr(logging, log_level.upper())
        self.logger = logging.getLogger(service_name)

        # Prevent duplicate handlers if logger already configured
        if not self.logger.handlers:
            self._setup_logging()

    def _setup_logging(self):
        """Configure logging handlers and formatters.

        File logging is best-effort. Every service in this stack now runs as a
        non-root UID (H1), so a bind-mounted log directory created by root on
        the host is not writable by the container. This used to raise
        PermissionError straight out of the constructor -- and because several
        services build their logger at import time, the process exited before
        doing any work and the orchestrator restart-looped it forever.

        A mail server that cannot write a log file should still deliver mail.
        So the console handler is installed FIRST and unconditionally, the file
        handlers are added only if the directory is genuinely usable, and the
        failure is reported rather than raised.
        """

        self.logger.setLevel(self.log_level)

        detailed_formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)s | %(module)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        simple_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        perf_formatter = logging.Formatter(
            fmt="%(asctime)s | PERF | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console handler (INFO and above). Installed before anything that can
        # fail, so the "file logging disabled" warning below has somewhere to go.
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(simple_formatter)
        console_handler.addFilter(RedactingFilter())
        self.logger.addHandler(console_handler)

        self.perf_logger = logging.getLogger(f"{self.service_name}_performance")
        self.perf_logger.setLevel(logging.INFO)
        self.perf_logger.propagate = False
        self.perf_handler = None

        log_dir = Path(__file__).parent.parent / "logs" / self.service_name
        self.log_dir = log_dir
        self.file_logging_enabled = False

        try:
            log_dir.mkdir(parents=True, exist_ok=True)

            # mkdir(exist_ok=True) succeeds on a directory that already exists
            # and is read-only, so it does not answer the question we care
            # about. os.access does.
            if not os.access(log_dir, os.W_OK):
                raise PermissionError(f"{log_dir} is not writable by uid {os.getuid()}")

            main_handler = logging.handlers.RotatingFileHandler(
                filename=log_dir / f"{self.service_name}.log",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8",
            )
            main_handler.setLevel(self.log_level)
            main_handler.setFormatter(detailed_formatter)
            main_handler.addFilter(RedactingFilter())
            self.logger.addHandler(main_handler)

            error_handler = logging.handlers.RotatingFileHandler(
                filename=log_dir / f"{self.service_name}_errors.log",
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.WARNING)
            error_handler.setFormatter(detailed_formatter)
            error_handler.addFilter(RedactingFilter())
            self.logger.addHandler(error_handler)

            self.perf_handler = logging.handlers.RotatingFileHandler(
                filename=log_dir / f"{self.service_name}_performance.log",
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding="utf-8",
            )
            self.perf_handler.setLevel(logging.INFO)
            self.perf_handler.setFormatter(perf_formatter)
            self.perf_logger.addHandler(self.perf_handler)

            self.file_logging_enabled = True

        except (OSError, ValueError) as exc:
            # PermissionError is an OSError. Degrade to stdout only -- Docker
            # and journald capture it, so the logs are not lost, they are just
            # not on the volume.
            for handler in list(self.logger.handlers):
                if isinstance(handler, logging.handlers.RotatingFileHandler):
                    self.logger.removeHandler(handler)
                    handler.close()

            perf_console = logging.StreamHandler()
            perf_console.setLevel(logging.INFO)
            perf_console.setFormatter(perf_formatter)
            perf_console.addFilter(RedactingFilter())
            self.perf_logger.addHandler(perf_console)

            self.logger.warning(
                f"File logging disabled for {self.service_name}: {exc}. "
                f"Logging to stdout only. Fix by chowning the mounted log "
                f"directory to the container UID."
            )

        # Log startup message
        self.logger.info(f"{self.service_name} service started - logging configured")
        if self.file_logging_enabled:
            self.logger.info(f"Log files: {log_dir}")

    def get_logger(self):
        """Get the configured logger instance."""
        return self.logger

    def log_performance(self, message: str, duration: float = None, **kwargs):
        """
        Log performance metrics to dedicated performance log.

        Args:
            message: Performance message
            duration: Optional duration in seconds
            **kwargs: Additional metrics to log
        """
        perf_data = []
        if duration is not None:
            perf_data.append(f"duration={duration:.3f}s")

        for key, value in kwargs.items():
            perf_data.append(f"{key}={value}")

        perf_msg = message
        if perf_data:
            perf_msg += f" | {' | '.join(perf_data)}"

        self.perf_logger.info(perf_msg)

    def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration: float,
        user_agent: str = None,
        ip: str = None,
    ):
        """
        Log HTTP request details.

        Args:
            method: HTTP method
            path: Request path
            status_code: HTTP status code
            duration: Request duration in seconds
            user_agent: Optional user agent string
            ip: Optional client IP address
        """
        extras = []
        if ip:
            extras.append(f"ip={ip}")
        if user_agent:
            extras.append(f"user_agent={user_agent[:100]}")

        extra_str = f" | {' | '.join(extras)}" if extras else ""

        self.log_performance(f"HTTP {method} {path} -> {status_code}", duration=duration)


def get_service_logger(service_name: str, log_level: str = None) -> logging.Logger:
    """
    Get a configured logger for a service.

    Args:
        service_name: Name of the service
        log_level: Optional log level override

    Returns:
        Configured logger instance
    """
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    service_logger = ServiceLogger(service_name, log_level)
    return service_logger.get_logger()


def get_performance_logger(service_name: str) -> tuple:
    """
    Get both regular and performance loggers for a service.

    Args:
        service_name: Name of the service

    Returns:
        Tuple of (regular_logger, performance_logger_func)
    """
    service_logger = ServiceLogger(service_name)
    return service_logger.get_logger(), service_logger.log_performance


# Context manager for timing operations
class LogTimer:
    """Context manager for timing and logging operations."""

    def __init__(self, logger, operation_name: str, **kwargs):
        self.logger = logger
        self.operation_name = operation_name
        self.kwargs = kwargs
        self.start_time = None

    def __enter__(self):
        self.start_time = datetime.now()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()

        if hasattr(self.logger, "log_performance"):
            self.logger.log_performance(self.operation_name, duration=duration, **self.kwargs)
        elif callable(self.logger) and not hasattr(self.logger, "info"):
            # get_performance_logger()'s second return value is the bound
            # log_performance function itself -- exactly what every service
            # passes here. Before this branch existed, __exit__ fell through
            # to .info() on the function and raised AttributeError AFTER the
            # timed body succeeded, which the callers' outer try/except then
            # reported as the operation failing (rate_limiter fail-open on
            # every check was the visible symptom).
            self.logger(self.operation_name, duration=duration, **self.kwargs)
        else:
            # Fallback for regular loggers
            self.logger.info(f"{self.operation_name} completed in {duration:.3f}s")


import logging
from logging.handlers import RotatingFileHandler


def setup_logging(log_level=None):
    """
    Set up logging configuration for the application
    """
    try:
        # Create logs directory if it doesn't exist
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        # Get log level from environment or use default
        if log_level is None:
            log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Validate log level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level not in valid_levels:
            log_level = "INFO"

        # Configure logging
        handlers = [logging.StreamHandler()]

        # Add file handler if possible
        try:
            file_handler = RotatingFileHandler(
                log_dir / "application.log",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
            )
            handlers.append(file_handler)
        except Exception as e:
            print(f"Warning: Could not create file handler: {e}")

        for h in handlers:
            h.addFilter(RedactingFilter())

        logging.basicConfig(
            level=getattr(logging, log_level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=handlers,
            force=True,  # Override any existing configuration
        )

        # Get logger for this module
        logger = logging.getLogger(__name__)
        logger.info(f"Logging configured with level: {log_level}")

        return logger

    except Exception as e:
        # Fallback to basic logging if setup fails
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to setup logging properly: {e}")
        return logger
