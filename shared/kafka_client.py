#!/usr/bin/env python3
"""
Shared Kafka Producer/Consumer Utility

Provides reusable KafkaProducerClient and KafkaConsumerClient wrappers around
kafka-python for all Mailyte email server services.  Handles JSON
serialization/deserialization, automatic reconnection with configurable retries,
and graceful degradation when the Kafka cluster is unreachable (logs a warning
instead of crashing the calling service).

Environment variables
---------------------
KAFKA_BOOTSTRAP_SERVERS : str
    Comma-separated list of broker addresses (default: ``kafka:9092``).
KAFKA_GROUP_ID : str
    Default consumer group id (default: ``mailyte-default-group``).

Usage::

    from shared.kafka_client import get_kafka_producer, get_kafka_consumer, Topics

    # --- Producing ---
    producer = get_kafka_producer()
    if producer:
        producer.send(Topics.OUTBOUND_HIGH, {"to": "user@example.com", "body": "..."})

    # --- Consuming ---
    consumer = get_kafka_consumer(Topics.INBOUND, group_id="inbound-workers")
    if consumer:
        for message in consumer.consume():
            process(message)
        consumer.close()
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topic name constants
# ---------------------------------------------------------------------------


class Topics:
    """
    Canonical Kafka topic names used across all Mailyte services.

    Centralising topic strings here avoids typos and makes renaming trivial.
    """

    INBOUND: str = "mailyte.inbound"
    OUTBOUND_HIGH: str = "mailyte.outbound.high"
    OUTBOUND_NORMAL: str = "mailyte.outbound.normal"
    OUTBOUND_LOW: str = "mailyte.outbound.low"
    NOTIFICATIONS: str = "mailyte.notifications"
    ANALYTICS: str = "mailyte.analytics"
    BOUNCE_EVENTS: str = "mailyte.bounce-events"
    DEAD_LETTER: str = "mailyte.dead-letter"

    @classmethod
    def all(cls) -> list[str]:
        """Return every registered topic name."""
        return [
            cls.INBOUND,
            cls.OUTBOUND_HIGH,
            cls.OUTBOUND_NORMAL,
            cls.OUTBOUND_LOW,
            cls.NOTIFICATIONS,
            cls.ANALYTICS,
            cls.BOUNCE_EVENTS,
            cls.DEAD_LETTER,
        ]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class KafkaConfig:
    """
    Kafka connection settings, populated from environment variables.

    Works identically to the other ``*Config`` dataclasses in
    ``shared.config`` -- every value has a sensible default that can be
    overridden via environment variables.
    """

    bootstrap_servers: str = ""
    group_id: str = ""
    client_id: str = ""
    max_retries: int = 5
    retry_backoff_seconds: float = 2.0
    request_timeout_ms: int = 30000
    session_timeout_ms: int = 30000
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = True
    acks: str | int = "all"

    def __post_init__(self):
        self.bootstrap_servers = os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", self.bootstrap_servers or "kafka:9092"
        )
        self.group_id = os.getenv("KAFKA_GROUP_ID", self.group_id or "mailyte-default-group")
        self.client_id = os.getenv(
            "KAFKA_CLIENT_ID", self.client_id or f"mailyte-{uuid.uuid4().hex[:8]}"
        )
        self.max_retries = int(os.getenv("KAFKA_MAX_RETRIES", str(self.max_retries)))
        self.retry_backoff_seconds = float(
            os.getenv("KAFKA_RETRY_BACKOFF_SECONDS", str(self.retry_backoff_seconds))
        )
        self.request_timeout_ms = int(
            os.getenv("KAFKA_REQUEST_TIMEOUT_MS", str(self.request_timeout_ms))
        )
        self.auto_offset_reset = os.getenv("KAFKA_AUTO_OFFSET_RESET", self.auto_offset_reset)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _json_serializer(data: Any) -> bytes:
    """Serialize a Python object to UTF-8 encoded JSON bytes."""
    return json.dumps(data, default=str).encode("utf-8")


def _json_deserializer(raw: bytes | None) -> Any:
    """Deserialize UTF-8 JSON bytes back to a Python object."""
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Failed to deserialize Kafka message: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


class KafkaProducerClient:
    """
    Thin wrapper around ``kafka.KafkaProducer`` with automatic JSON
    serialization, retry logic, and graceful error handling.

    Example::

        producer = KafkaProducerClient()
        if producer.is_connected:
            producer.send(Topics.ANALYTICS, {"event": "open", "msg_id": "abc"})
            producer.flush()
    """

    def __init__(self, config: KafkaConfig | None = None):
        self._config = config or KafkaConfig()
        self._producer = None
        self._connected = False
        self._connect()

    # -- connection management -----------------------------------------------

    def _connect(self) -> None:
        """Attempt to create the underlying KafkaProducer with retries."""
        try:
            from kafka import KafkaProducer  # type: ignore[import-untyped]
        except ImportError:
            logger.error(
                "kafka-python is not installed. Install it with: pip install kafka-python-ng"
            )
            return

        for attempt in range(1, self._config.max_retries + 1):
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=self._config.bootstrap_servers.split(","),
                    client_id=self._config.client_id,
                    value_serializer=_json_serializer,
                    key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
                    acks=self._config.acks,
                    retries=3,
                    request_timeout_ms=self._config.request_timeout_ms,
                    max_block_ms=self._config.request_timeout_ms,
                )
                self._connected = True
                logger.info(
                    "Kafka producer connected to %s (attempt %d)",
                    self._config.bootstrap_servers,
                    attempt,
                )
                return
            except Exception as exc:
                logger.warning(
                    "Kafka producer connection attempt %d/%d failed: %s",
                    attempt,
                    self._config.max_retries,
                    exc,
                )
                if attempt < self._config.max_retries:
                    time.sleep(self._config.retry_backoff_seconds * attempt)

        logger.error(
            "Kafka producer could not connect after %d attempts. "
            "Messages will be dropped until the broker is reachable.",
            self._config.max_retries,
        )

    def _ensure_connection(self) -> bool:
        """Re-establish the connection if it was lost. Returns ``True`` when ready."""
        if self._connected and self._producer is not None:
            return True
        logger.info("Kafka producer reconnecting ...")
        self._connect()
        return self._connected

    # -- public API ----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """``True`` when the producer has an active broker connection."""
        return self._connected and self._producer is not None

    def send(
        self,
        topic: str,
        value: Any,
        key: str | None = None,
        headers: list[tuple] | None = None,
    ) -> bool:
        """
        Publish a message to *topic*.

        Parameters
        ----------
        topic:
            Kafka topic name (use a ``Topics.*`` constant).
        value:
            Payload -- will be JSON-serialized automatically.
        key:
            Optional partition key.
        headers:
            Optional list of ``(header_name, header_value_bytes)`` tuples.

        Returns
        -------
        bool
            ``True`` if the message was accepted by the producer buffer,
            ``False`` otherwise (e.g. broker unreachable).
        """
        if not self._ensure_connection():
            logger.warning(
                "Kafka producer unavailable -- dropping message for topic '%s'",
                topic,
            )
            return False

        try:
            future = self._producer.send(  # type: ignore[union-attr]
                topic,
                value=value,
                key=key,
                headers=headers,
            )
            # Block briefly to surface immediate errors (e.g. topic not found).
            future.get(timeout=10)
            return True
        except Exception as exc:
            logger.error("Failed to send message to '%s': %s", topic, exc)
            self._connected = False
            return False

    def send_async(
        self,
        topic: str,
        value: Any,
        key: str | None = None,
        headers: list[tuple] | None = None,
        on_success: Callable | None = None,
        on_error: Callable | None = None,
    ) -> bool:
        """
        Publish a message without blocking.

        Optional *on_success* / *on_error* callbacks are invoked by the
        kafka-python I/O thread when delivery confirmation arrives.

        Returns ``True`` if the message was enqueued successfully.
        """
        if not self._ensure_connection():
            logger.warning(
                "Kafka producer unavailable -- dropping async message for topic '%s'",
                topic,
            )
            return False

        try:
            future = self._producer.send(  # type: ignore[union-attr]
                topic,
                value=value,
                key=key,
                headers=headers,
            )
            if on_success:
                future.add_callback(on_success)
            if on_error:
                future.add_errback(on_error)
            return True
        except Exception as exc:
            logger.error("Failed to enqueue async message to '%s': %s", topic, exc)
            self._connected = False
            return False

    def flush(self, timeout: float | None = None) -> None:
        """Flush any buffered messages, blocking until complete."""
        if self._producer is not None:
            try:
                self._producer.flush(timeout=timeout)
            except Exception as exc:
                logger.error("Kafka producer flush failed: %s", exc)

    def close(self) -> None:
        """Flush pending messages and release resources."""
        if self._producer is not None:
            try:
                self._producer.flush(timeout=10)
                self._producer.close(timeout=10)
                logger.info("Kafka producer closed gracefully.")
            except Exception as exc:
                logger.warning("Error closing Kafka producer: %s", exc)
            finally:
                self._producer = None
                self._connected = False


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


class KafkaConsumerClient:
    """
    Thin wrapper around ``kafka.KafkaConsumer`` with automatic JSON
    deserialization, retry logic, and graceful error handling.

    Example::

        consumer = KafkaConsumerClient(Topics.INBOUND)
        for message in consumer.consume():
            handle(message)
        consumer.close()
    """

    def __init__(
        self,
        topics: str | list[str],
        group_id: str | None = None,
        config: KafkaConfig | None = None,
    ):
        self._config = config or KafkaConfig()
        self._topics = [topics] if isinstance(topics, str) else list(topics)
        self._group_id = group_id or self._config.group_id
        self._consumer = None
        self._connected = False
        self._connect()

    # -- connection management -----------------------------------------------

    def _connect(self) -> None:
        """Attempt to create the underlying KafkaConsumer with retries."""
        try:
            from kafka import KafkaConsumer  # type: ignore[import-untyped]
        except ImportError:
            logger.error(
                "kafka-python is not installed. Install it with: pip install kafka-python-ng"
            )
            return

        for attempt in range(1, self._config.max_retries + 1):
            try:
                self._consumer = KafkaConsumer(
                    *self._topics,
                    bootstrap_servers=self._config.bootstrap_servers.split(","),
                    client_id=self._config.client_id,
                    group_id=self._group_id,
                    value_deserializer=_json_deserializer,
                    key_deserializer=lambda k: k.decode("utf-8") if k else None,
                    auto_offset_reset=self._config.auto_offset_reset,
                    enable_auto_commit=self._config.enable_auto_commit,
                    request_timeout_ms=self._config.request_timeout_ms,
                    session_timeout_ms=self._config.session_timeout_ms,
                )
                self._connected = True
                logger.info(
                    "Kafka consumer connected to %s, topics=%s, group=%s (attempt %d)",
                    self._config.bootstrap_servers,
                    self._topics,
                    self._group_id,
                    attempt,
                )
                return
            except Exception as exc:
                logger.warning(
                    "Kafka consumer connection attempt %d/%d failed: %s",
                    attempt,
                    self._config.max_retries,
                    exc,
                )
                if attempt < self._config.max_retries:
                    time.sleep(self._config.retry_backoff_seconds * attempt)

        logger.error(
            "Kafka consumer could not connect after %d attempts. "
            "No messages will be consumed until the broker is reachable.",
            self._config.max_retries,
        )

    def _ensure_connection(self) -> bool:
        """Re-establish the connection if it was lost. Returns ``True`` when ready."""
        if self._connected and self._consumer is not None:
            return True
        logger.info("Kafka consumer reconnecting ...")
        self._connect()
        return self._connected

    # -- public API ----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """``True`` when the consumer has an active broker connection."""
        return self._connected and self._consumer is not None

    def consume(self, max_messages: int | None = None) -> Iterator[dict[str, Any]]:
        """
        Yield deserialized messages from the subscribed topics.

        Each yielded dict contains::

            {
                "topic":     str,
                "partition": int,
                "offset":    int,
                "key":       Optional[str],
                "value":     Any,          # already JSON-decoded
                "timestamp": int,          # milliseconds since epoch
                "headers":   list,
            }

        Parameters
        ----------
        max_messages:
            If set, stop iteration after this many messages.
        """
        if not self._ensure_connection():
            logger.warning("Kafka consumer unavailable -- cannot consume messages.")
            return

        count = 0
        try:
            for msg in self._consumer:  # type: ignore[union-attr]
                yield {
                    "topic": msg.topic,
                    "partition": msg.partition,
                    "offset": msg.offset,
                    "key": msg.key,
                    "value": msg.value,
                    "timestamp": msg.timestamp,
                    "headers": msg.headers,
                }
                count += 1
                if max_messages is not None and count >= max_messages:
                    break
        except Exception as exc:
            logger.error("Error while consuming messages: %s", exc)
            self._connected = False

    def poll(self, timeout_ms: int = 1000, max_records: int = 500) -> list[dict[str, Any]]:
        """
        Poll the broker for a batch of messages and return them as a list.

        Useful when you want bounded, non-blocking consumption instead of
        an infinite iterator.
        """
        if not self._ensure_connection():
            logger.warning("Kafka consumer unavailable -- poll returning empty list.")
            return []

        messages: list[dict[str, Any]] = []
        try:
            raw_batches = self._consumer.poll(  # type: ignore[union-attr]
                timeout_ms=timeout_ms,
                max_records=max_records,
            )
            for _tp, records in raw_batches.items():
                for msg in records:
                    messages.append(
                        {
                            "topic": msg.topic,
                            "partition": msg.partition,
                            "offset": msg.offset,
                            "key": msg.key,
                            "value": msg.value,
                            "timestamp": msg.timestamp,
                            "headers": msg.headers,
                        }
                    )
        except Exception as exc:
            logger.error("Error while polling messages: %s", exc)
            self._connected = False

        return messages

    def commit(self) -> None:
        """Manually commit the current offsets (when auto-commit is disabled)."""
        if self._consumer is not None:
            try:
                self._consumer.commit()
            except Exception as exc:
                logger.error("Kafka consumer commit failed: %s", exc)

    def close(self) -> None:
        """Cleanly unsubscribe and release resources."""
        if self._consumer is not None:
            try:
                self._consumer.close()
                logger.info("Kafka consumer closed gracefully.")
            except Exception as exc:
                logger.warning("Error closing Kafka consumer: %s", exc)
            finally:
                self._consumer = None
                self._connected = False


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_producer_instance: KafkaProducerClient | None = None
_consumer_instances: dict[str, KafkaConsumerClient] = {}


def get_kafka_producer(config: KafkaConfig | None = None) -> KafkaProducerClient | None:
    """
    Return a shared ``KafkaProducerClient`` singleton.

    Returns ``None`` if Kafka is unavailable so callers can degrade
    gracefully::

        producer = get_kafka_producer()
        if producer:
            producer.send(Topics.ANALYTICS, payload)
    """
    global _producer_instance

    if _producer_instance is None or not _producer_instance.is_connected:
        try:
            _producer_instance = KafkaProducerClient(config=config)
        except Exception as exc:
            logger.warning("Could not create Kafka producer: %s", exc)
            return None

    if not _producer_instance.is_connected:
        logger.warning("Kafka producer exists but is not connected -- returning None.")
        return None

    return _producer_instance


def get_kafka_consumer(
    topic: str | list[str],
    group_id: str | None = None,
    config: KafkaConfig | None = None,
) -> KafkaConsumerClient | None:
    """
    Return a ``KafkaConsumerClient`` for the given *topic* and *group_id*.

    A new consumer is created for each unique ``(topic, group_id)`` pair.
    Returns ``None`` when Kafka is unreachable.

    Example::

        consumer = get_kafka_consumer(Topics.INBOUND, group_id="inbound-workers")
        if consumer:
            for msg in consumer.consume():
                process(msg)
    """
    global _consumer_instances

    cache_key = f"{topic}::{group_id or 'default'}"

    existing = _consumer_instances.get(cache_key)
    if existing is not None and existing.is_connected:
        return existing

    try:
        consumer = KafkaConsumerClient(topics=topic, group_id=group_id, config=config)
    except Exception as exc:
        logger.warning("Could not create Kafka consumer: %s", exc)
        return None

    if not consumer.is_connected:
        logger.warning("Kafka consumer created but not connected -- returning None.")
        return None

    _consumer_instances[cache_key] = consumer
    return consumer


def close_all() -> None:
    """
    Flush the shared producer and close every cached consumer.

    Call this during application shutdown to release resources cleanly.
    """
    global _producer_instance, _consumer_instances

    if _producer_instance is not None:
        _producer_instance.close()
        _producer_instance = None

    for key, consumer in list(_consumer_instances.items()):
        consumer.close()
    _consumer_instances.clear()

    logger.info("All shared Kafka clients have been closed.")
