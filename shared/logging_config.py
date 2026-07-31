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
from datetime import datetime
from pathlib import Path


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
        """Configure logging handlers and formatters."""

        # Create logs directory structure
        log_dir = Path(__file__).parent.parent / "logs" / self.service_name
        log_dir.mkdir(parents=True, exist_ok=True)

        # Set logger level
        self.logger.setLevel(self.log_level)

        # Create formatters
        detailed_formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)s | %(module)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        simple_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console handler (INFO and above)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(simple_formatter)
        self.logger.addHandler(console_handler)

        # Main log file handler (all messages)
        main_log_file = log_dir / f"{self.service_name}.log"
        main_handler = logging.handlers.RotatingFileHandler(
            filename=main_log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        main_handler.setLevel(self.log_level)
        main_handler.setFormatter(detailed_formatter)
        self.logger.addHandler(main_handler)

        # Error log file handler (WARNING and above)
        error_log_file = log_dir / f"{self.service_name}_errors.log"
        error_handler = logging.handlers.RotatingFileHandler(
            filename=error_log_file,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(detailed_formatter)
        self.logger.addHandler(error_handler)

        # Performance log file handler (for timing and metrics)
        perf_log_file = log_dir / f"{self.service_name}_performance.log"
        self.perf_handler = logging.handlers.RotatingFileHandler(
            filename=perf_log_file,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        self.perf_handler.setLevel(logging.INFO)
        perf_formatter = logging.Formatter(
            fmt="%(asctime)s | PERF | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.perf_handler.setFormatter(perf_formatter)

        # Create performance logger
        self.perf_logger = logging.getLogger(f"{self.service_name}_performance")
        self.perf_logger.setLevel(logging.INFO)
        self.perf_logger.addHandler(self.perf_handler)
        self.perf_logger.propagate = False

        # Log startup message
        self.logger.info(f"{self.service_name} service started - logging configured")
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
