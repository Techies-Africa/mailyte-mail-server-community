"""
Per-request correlation ids (phase-04 task 4.7).

A contextvar rather than thread-local because this app is asyncio/uvicorn --
each request runs in its own Task, and contextvars propagate correctly
across `await` within a Task without leaking between concurrent requests.
"""

import contextvars
import logging

_correlation_id_var = contextvars.ContextVar("correlation_id", default="-")


def generate_correlation_id():
    try:
        from shared.ulid_utils import generate_ulid

        return f"req_{generate_ulid()}"
    except ImportError:
        import uuid

        return f"req_{uuid.uuid4().hex}"


def set_correlation_id(correlation_id):
    _correlation_id_var.set(correlation_id)


def get_correlation_id():
    return _correlation_id_var.get()


class CorrelationIdLogFilter(logging.Filter):
    """Attach the active request's correlation id to every log record."""

    def filter(self, record):
        record.correlation_id = _correlation_id_var.get()
        return True
