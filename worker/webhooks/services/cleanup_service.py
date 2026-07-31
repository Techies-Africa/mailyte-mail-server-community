#!/usr/bin/env python3
"""
Webhook Delivery Cleanup Service

This service manages the periodic cleanup of webhook delivery logs based on
configurable retention policies. It can auto-delete successful deliveries
while keeping failed ones for retry, and provides flexible cleanup schedules.

Features:
- Configurable cleanup intervals (hourly scheduling)
- Separate retention policies for successful vs failed deliveries
- Batch processing for efficient cleanup
- Detailed logging and metrics
- Safe cleanup with transaction rollback on errors
"""

import logging

# Import models
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

project_root = Path(__file__).parent.parent.parent.parent
sys.path.append(str(project_root))

from database.models import WebhookDeliveryLog, WebhookDeliveryStatus

logger = logging.getLogger(__name__)


class WebhookCleanupService:
    """
    Service for cleaning up webhook delivery logs based on configurable policies.

    This service runs as a background thread and periodically cleans up old
    webhook delivery records based on their status and age. It provides
    flexibility to keep failed deliveries longer for retry purposes.
    """

    def __init__(self, db_url: str, config: dict[str, Any]):
        """
        Initialize the webhook cleanup service.

        Args:
            db_url: Database connection URL
            config: Cleanup configuration dictionary
        """
        self.db_url = db_url
        self.config = config

        # Database setup
        self.engine = create_engine(db_url, pool_size=5, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Service state
        self.running = False
        self.cleanup_thread = None

        # Performance tracking
        self.total_cleanups = 0
        self.total_records_deleted = 0
        self.successful_records_deleted = 0
        self.failed_records_deleted = 0
        self.last_cleanup_time = None
        self.last_cleanup_duration = 0

        logger.info(f"WebhookCleanupService initialized with config: {config}")

    def start(self):
        """Start the cleanup service background thread."""
        if self.running:
            logger.warning("Cleanup service is already running")
            return

        if not self.config.get("enabled", True):
            logger.info("Cleanup service is disabled in configuration")
            return

        self.running = True
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_worker, name="webhook-cleanup-worker", daemon=True
        )
        self.cleanup_thread.start()
        logger.info("Webhook cleanup service started")

    def stop(self):
        """Stop the cleanup service."""
        if not self.running:
            return

        self.running = False
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=30)

        logger.info("Webhook cleanup service stopped")

    def _cleanup_worker(self):
        """Background worker that performs periodic cleanup."""
        cleanup_interval_hours = self.config.get("cleanup_interval_hours", 4)
        cleanup_interval_seconds = cleanup_interval_hours * 3600

        logger.info(f"Cleanup worker started, interval: {cleanup_interval_hours} hours")

        while self.running:
            try:
                # Wait for the next cleanup cycle
                for _ in range(cleanup_interval_seconds):
                    if not self.running:
                        break
                    time.sleep(1)

                if not self.running:
                    break

                # Perform cleanup
                self._perform_cleanup()

            except Exception as e:
                logger.error(f"Error in cleanup worker: {e}", exc_info=True)
                # Sleep for a shorter interval on error to retry sooner
                time.sleep(300)  # 5 minutes

    def _perform_cleanup(self):
        """
        Perform the actual cleanup of webhook delivery logs.

        This method handles both successful and failed deliveries with
        different retention policies.
        """
        start_time = time.time()
        session = self.SessionLocal()

        try:
            logger.info("Starting webhook delivery log cleanup")

            # Cleanup successful deliveries if auto-delete is enabled
            successful_deleted = 0
            if self.config.get("auto_delete_successful", True):
                successful_deleted = self._cleanup_successful_deliveries(session)

            # Cleanup old failed deliveries
            failed_deleted = 0
            if self.config.get("cleanup_failed_enabled", True):
                failed_deleted = self._cleanup_failed_deliveries(session)

            # Commit all changes
            session.commit()

            # Update statistics
            self.total_cleanups += 1
            self.total_records_deleted += successful_deleted + failed_deleted
            self.successful_records_deleted += successful_deleted
            self.failed_records_deleted += failed_deleted
            self.last_cleanup_time = datetime.utcnow()
            self.last_cleanup_duration = time.time() - start_time

            logger.info(
                f"Cleanup completed: {successful_deleted} successful, "
                f"{failed_deleted} failed deliveries deleted in "
                f"{self.last_cleanup_duration:.2f}s"
            )

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error during cleanup: {e}")
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Unexpected error during cleanup: {e}")
            raise
        finally:
            session.close()

    def _cleanup_successful_deliveries(self, session) -> int:
        """
        Clean up successful webhook deliveries based on retention policy.

        Args:
            session: Database session

        Returns:
            int: Number of records deleted
        """
        retention_hours = self.config.get("successful_retention_hours", 24)
        batch_size = self.config.get("cleanup_batch_size", 1000)

        cutoff_time = datetime.utcnow() - timedelta(hours=retention_hours)

        logger.debug(f"Cleaning up successful deliveries older than {cutoff_time}")

        total_deleted = 0

        while True:
            # Find batch of successful deliveries to delete
            records_to_delete = (
                session.query(WebhookDeliveryLog.id)
                .filter(
                    WebhookDeliveryLog.delivery_status == WebhookDeliveryStatus.DELIVERED,
                    WebhookDeliveryLog.delivered_at < cutoff_time,
                    WebhookDeliveryLog.auto_cleanup_enabled == True,
                )
                .limit(batch_size)
                .all()
            )

            if not records_to_delete:
                break

            # Delete the batch
            record_ids = [record.id for record in records_to_delete]
            deleted_count = (
                session.query(WebhookDeliveryLog)
                .filter(WebhookDeliveryLog.id.in_(record_ids))
                .delete(synchronize_session=False)
            )

            total_deleted += deleted_count
            session.commit()  # Commit each batch to avoid long-running transactions

            logger.debug(f"Deleted batch of {deleted_count} successful deliveries")

            # Break if we deleted fewer records than the batch size
            if deleted_count < batch_size:
                break

        return total_deleted

    def _cleanup_failed_deliveries(self, session) -> int:
        """
        Clean up old failed webhook deliveries that have exceeded retry attempts.

        Args:
            session: Database session

        Returns:
            int: Number of records deleted
        """
        retention_hours = self.config.get("failed_retention_hours", 72)
        batch_size = self.config.get("cleanup_batch_size", 1000)

        cutoff_time = datetime.utcnow() - timedelta(hours=retention_hours)

        logger.debug(f"Cleaning up failed deliveries older than {cutoff_time}")

        total_deleted = 0

        while True:
            # Find batch of old failed deliveries to delete
            records_to_delete = (
                session.query(WebhookDeliveryLog.id)
                .filter(
                    WebhookDeliveryLog.delivery_status.in_(
                        [WebhookDeliveryStatus.FAILED, WebhookDeliveryStatus.ABANDONED]
                    ),
                    WebhookDeliveryLog.created_at < cutoff_time,
                    WebhookDeliveryLog.auto_cleanup_enabled == True,
                )
                .limit(batch_size)
                .all()
            )

            if not records_to_delete:
                break

            # Delete the batch
            record_ids = [record.id for record in records_to_delete]
            deleted_count = (
                session.query(WebhookDeliveryLog)
                .filter(WebhookDeliveryLog.id.in_(record_ids))
                .delete(synchronize_session=False)
            )

            total_deleted += deleted_count
            session.commit()  # Commit each batch

            logger.debug(f"Deleted batch of {deleted_count} failed deliveries")

            if deleted_count < batch_size:
                break

        return total_deleted

    def cleanup_now(self) -> dict[str, Any]:
        """
        Perform immediate cleanup and return results.

        Returns:
            Dict with cleanup results
        """
        logger.info("Performing immediate webhook cleanup")

        try:
            self._perform_cleanup()
            return {
                "success": True,
                "message": "Cleanup completed successfully",
                "successful_deleted": self.successful_records_deleted,
                "failed_deleted": self.failed_records_deleted,
                "duration_seconds": self.last_cleanup_duration,
            }
        except Exception as e:
            logger.error(f"Immediate cleanup failed: {e}")
            return {"success": False, "message": f"Cleanup failed: {str(e)}", "error": str(e)}

    def get_cleanup_stats(self) -> dict[str, Any]:
        """
        Get cleanup service statistics.

        Returns:
            Dict containing service statistics
        """
        return {
            "service": "webhook_cleanup",
            "running": self.running,
            "configuration": {
                "cleanup_interval_hours": self.config.get("cleanup_interval_hours", 4),
                "successful_retention_hours": self.config.get("successful_retention_hours", 24),
                "failed_retention_hours": self.config.get("failed_retention_hours", 72),
                "auto_delete_successful": self.config.get("auto_delete_successful", True),
                "cleanup_batch_size": self.config.get("cleanup_batch_size", 1000),
            },
            "statistics": {
                "total_cleanups": self.total_cleanups,
                "total_records_deleted": self.total_records_deleted,
                "successful_records_deleted": self.successful_records_deleted,
                "failed_records_deleted": self.failed_records_deleted,
                "last_cleanup_time": self.last_cleanup_time.isoformat()
                if self.last_cleanup_time
                else None,
                "last_cleanup_duration_seconds": self.last_cleanup_duration,
            },
        }

    def update_config(self, new_config: dict[str, Any]):
        """
        Update cleanup configuration at runtime.

        Args:
            new_config: New configuration dictionary
        """
        old_config = self.config.copy()
        self.config.update(new_config)

        logger.info(f"Cleanup configuration updated from {old_config} to {self.config}")

        # If cleanup was disabled and is now enabled, start the service
        if not old_config.get("enabled", True) and new_config.get("enabled", True):
            self.start()
        # If cleanup was enabled and is now disabled, stop the service
        elif old_config.get("enabled", True) and not new_config.get("enabled", True):
            self.stop()


def create_cleanup_service(db_url: str, config: dict[str, Any]) -> WebhookCleanupService:
    """
    Factory function to create a webhook cleanup service.

    Args:
        db_url: Database connection URL
        config: Cleanup configuration

    Returns:
        WebhookCleanupService instance
    """
    return WebhookCleanupService(db_url, config)
