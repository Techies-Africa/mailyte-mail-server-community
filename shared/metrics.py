#!/usr/bin/env python3
"""
Metrics stub for Community Edition.

Enterprise Edition provides full Prometheus metrics export.
This stub provides the same interface with no-op implementations.
"""


class _NoOpMetrics:
    """No-op metrics collector — CE does not ship Prometheus integration."""

    def __init__(self, service_name: str = ""):
        self.service_name = service_name

    def record_request(self, **kwargs):
        pass

    def get_prometheus_metrics(self) -> str:
        return "# Prometheus metrics available in Mailyte Enterprise Edition\n"

    def increment(self, name: str, value: int = 1, **kwargs):
        pass

    def gauge(self, name: str, value: float, **kwargs):
        pass

    def histogram(self, name: str, value: float, **kwargs):
        pass


def get_metrics(service_name: str = "") -> _NoOpMetrics:
    """Get a metrics collector instance (no-op in Community Edition)."""
    return _NoOpMetrics(service_name)
