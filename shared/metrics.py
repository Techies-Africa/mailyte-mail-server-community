#!/usr/bin/env python3
"""
Metrics stub for Community Edition.

Enterprise Edition exports full Prometheus metrics. CE ships no Prometheus, so
this module answers the same calls and does nothing with them.

THE INTERFACE IS IMPLEMENTED BY __getattr__, NOT BY ENUMERATION -- deliberately.
The previous stub hand-listed five methods and claimed to "provide the same
interface", but the names did not even match EE's: it defined increment/gauge/
histogram where PrometheusMetrics defines increment_counter/set_gauge/
observe_histogram, and it omitted set_info, record_database_operation,
record_webhook_delivery and time_function entirely. Any CE service reaching one
of those raised AttributeError at runtime -- worker/webhooks/app.py does exactly
that, and it is a service CE ships.

A hand-listed stub drifts silently every time EE's metrics class grows a method.
This one cannot: unknown attributes resolve to a no-op callable, so the failure
mode is "a metric is not recorded", which is what CE promises, rather than "the
webhooks service crashes".
"""

from typing import Any, Callable


class _NoOpMetrics:
    """No-op metrics collector -- CE does not ship Prometheus integration."""

    def __init__(self, service_name: str = ""):
        self.service_name = service_name

    def get_prometheus_metrics(self) -> str:
        # Explicit because callers use the RETURN VALUE -- the /metrics
        # endpoint serves it as a body. A generic no-op returning None would
        # surface as a 500 rather than an honest empty scrape.
        return "# Prometheus metrics available in Mailyte Enterprise Edition\n"

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Accept any recording call PrometheusMetrics defines, and drop it.

        Only reached for attributes not defined above, so the explicit members
        keep their behaviour. Names beginning with an underscore are refused so
        that copy/pickle protocol probes and `hasattr(m, "_lock")` style checks
        still fail honestly instead of returning a function.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        _noop.__name__ = name
        return _noop


def get_metrics(service_name: str = "") -> _NoOpMetrics:
    """Get a metrics collector instance (no-op in Community Edition)."""
    return _NoOpMetrics(service_name)


def time_function(metric_name: str = None, service_name: str = None):
    """Pass-through form of EE's timing decorator.

    Absent from the previous CE stub altogether, so `@time_function(...)` on any
    ported code would have failed at import time -- before a single request.
    """

    def decorator(func):
        return func

    return decorator
