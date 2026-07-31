#!/usr/bin/env python3
"""
Webhook Service - Main Application

This service handles webhook notifications for all email server events:
- SMTP inbound/outbound email processing
- IMAP/POP3 user activity tracking
- Dovecot authentication and quota events
- Postfix delivery and bounce notifications
- Encrypted webhook delivery with failover
- Real-time event processing and queuing

The service is designed with modular architecture for scalability
and includes comprehensive security features for production environments.
"""

import base64
import email
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# Add shared directory to path
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root / "shared"))

from metrics import get_metrics
from services.cleanup_service import WebhookCleanupService

# Import modular webhook services
from services.notification_sender import notification_sender

from shared.logging_config import LogTimer, get_performance_logger
from shared.webhook_dispatcher import Events, dispatch_event

app = FastAPI(title="Webhooks Service")

# Initialize metrics
metrics = get_metrics()

# Configure service-specific logging
logger, log_performance = get_performance_logger("webhooks")

# Webhook queue for async processing
webhook_queue = queue.Queue(maxsize=10000)  # Prevent memory issues


class WebhookQueueManager:
    """
    Manages webhook queue processing with worker threads
    and integrates with modular notification services.
    """

    def __init__(self):
        """Initialize queue manager with configurable workers."""
        self.worker_count = int(os.getenv("WEBHOOK_WORKERS", "5"))
        self.workers = []
        self.running = True

        # Performance tracking
        self.processed_count = 0
        self.error_count = 0
        self.start_time = time.time()

        logger.info(f"Webhook queue manager initialized with {self.worker_count} workers")

    def start_workers(self):
        """Start worker threads for processing webhook queue."""
        for i in range(self.worker_count):
            worker = threading.Thread(
                target=self._webhook_worker, name=f"webhook-worker-{i}", daemon=True
            )
            worker.start()
            self.workers.append(worker)

        logger.info(f"Started {len(self.workers)} webhook workers")

    def _webhook_worker(self):
        """Background worker to process webhook queue."""
        worker_name = threading.current_thread().name
        logger.debug(f"Webhook worker {worker_name} started")

        while self.running:
            try:
                # Get event from queue with timeout
                event_data = webhook_queue.get(timeout=1)

                start_time = time.time()

                # Process webhook using notification sender
                with LogTimer(
                    log_performance, f"webhook_processing_{event_data.get('event', 'unknown')}"
                ):
                    result = notification_sender.send_webhook(
                        event_type=event_data.get("event", "unknown"),
                        payload=event_data.get("payload", {}),
                        include_eml=event_data.get("include_eml", True),
                        eml_content=event_data.get("eml_content"),
                    )

                processing_time = time.time() - start_time

                if result["success"]:
                    self.processed_count += 1
                    logger.debug(
                        f"Webhook processed successfully in {processing_time:.3f}s by {worker_name}"
                    )
                else:
                    self.error_count += 1
                    logger.warning(f"Webhook processing failed: {result}")

                webhook_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                self.error_count += 1
                logger.error(f"Webhook worker {worker_name} error: {e}")
                if "event_data" in locals():
                    webhook_queue.task_done()

    def queue_webhook(self, event_data: dict) -> bool:
        """
        Queue webhook for processing.

        Args:
            event_data: Webhook event data

        Returns:
            bool: True if queued successfully, False if queue is full
        """
        try:
            webhook_queue.put_nowait(event_data)
            return True
        except queue.Full:
            logger.error("Webhook queue is full, dropping event")
            return False

    def get_stats(self) -> dict:
        """Get webhook processing statistics."""
        uptime = time.time() - self.start_time
        total_processed = self.processed_count + self.error_count

        return {
            "queue_size": webhook_queue.qsize(),
            "worker_count": len(self.workers),
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "total_processed": total_processed,
            "success_rate": (self.processed_count / total_processed * 100)
            if total_processed > 0
            else 100.0,
            "uptime_seconds": uptime,
            "processing_rate": total_processed / (uptime / 60) if uptime > 60 else 0,
        }

    def stop_workers(self):
        """Stop all webhook workers."""
        self.running = False
        for worker in self.workers:
            worker.join(timeout=5)
        logger.info("Webhook workers stopped")


class WebhookProcessor:
    """Processes webhooks"""

    def __init__(self):
        self.webhook_urls = [""]  # Replace with actual urls

    def process_webhook(self, event_data):
        """Process the webhook event"""
        try:
            time.sleep(1)
            logger.info(f"Webhook processed: {event_data.get('event', 'unknown')}")
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")

    def _extract_organization_id(self, metadata):
        """Extract organization ID from metadata"""
        # Implement logic to extract organization ID from metadata
        # This is just a placeholder, replace with actual logic
        if metadata and "from" in metadata:
            from_address = metadata["from"]
            if "@" in from_address:
                domain = from_address.split("@")[1]
                # Simple example: use the domain as the organization ID
                return domain
        return "unknown"


# Initialize WebhookQueueManager
webhook_manager = WebhookQueueManager()
webhook_manager.start_workers()


def webhook_worker():
    """Background worker to process webhook queue"""
    processor = WebhookProcessor()

    while True:
        try:
            event_data = webhook_queue.get(timeout=1)
            processor.process_webhook(event_data)
            webhook_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"Webhook worker error: {e}")


# Start webhook worker thread
# webhook_thread = threading.Thread(target=webhook_worker, daemon=True)
# webhook_thread.start()


def extract_email_metadata(eml_content):
    """Extract metadata from EML content"""
    try:
        msg = email.message_from_string(eml_content)

        metadata = {
            "message_id": msg.get("Message-ID", ""),
            "subject": msg.get("Subject", ""),
            "from": msg.get("From", ""),
            "to": msg.get("To", ""),
            "cc": msg.get("Cc", ""),
            "bcc": msg.get("Bcc", ""),
            "date": msg.get("Date", ""),
            "reply_to": msg.get("Reply-To", ""),
            "return_path": msg.get("Return-Path", ""),
            "content_type": msg.get("Content-Type", ""),
            "encoding": msg.get("Content-Transfer-Encoding", ""),
            "size": len(eml_content),
            "has_attachments": False,
            "attachment_count": 0,
            "attachment_names": [],
        }

        # Check for attachments
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    metadata["has_attachments"] = True
                    metadata["attachment_count"] += 1
                    filename = part.get_filename()
                    if filename:
                        metadata["attachment_names"].append(filename)

        return metadata
    except Exception as e:
        logger.error(f"Failed to extract email metadata: {e}")
        return {}


def extract_attachments(eml_content):
    """Extract attachments from EML content"""
    attachments = []

    try:
        msg = email.message_from_string(eml_content)

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    filename = part.get_filename()
                    if filename:
                        content = part.get_payload(decode=True)
                        if content:
                            attachments.append(
                                {
                                    "filename": filename,
                                    "content_type": part.get_content_type(),
                                    "size": len(content),
                                    "content_base64": base64.b64encode(content).decode("utf-8"),
                                }
                            )
    except Exception as e:
        logger.error(f"Failed to extract attachments: {e}")

    return attachments


def track_email_storage_usage(webhook_payload, metadata, attachments):
    """Track storage usage for an email."""
    organization_id = webhook_payload.get("organization_id", "unknown")
    email_size = metadata.get("size", 0)

    total_attachment_size = sum(attachment.get("size", 0) for attachment in attachments)

    total_storage_used = email_size + total_attachment_size

    logger.info(
        f"Storage usage tracked for org {organization_id}: Email size: {email_size}, Attachments: {total_attachment_size}, Total: {total_storage_used}"
    )

    # Here, you would typically update a database or external service
    # to record the storage usage for the given organization.
    # Example:
    # db.update_storage_usage(organization_id, total_storage_used)
    pass


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    if hasattr(metrics, "record_request"):
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


@app.post("/webhook/email/inbound")
async def handle_inbound_email(request: Request):
    """Handle inbound email webhook"""
    try:
        data = await request.json()
        eml_content = data.get("eml_content", "")

        if not eml_content:
            return JSONResponse({"error": "No EML content provided"}, status_code=400)

        # Extract metadata and attachments
        metadata = extract_email_metadata(eml_content)
        attachments = extract_attachments(eml_content)

        # Create webhook payload
        webhook_payload = {
            "event": "email.smtp.inbound",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": {
                "direction": "inbound",
                "protocol": data.get("protocol", "smtp"),
                "server": data.get("server", "unknown"),
                "metadata": metadata,
                "delivery_info": {
                    "recipient": data.get("recipient", ""),
                    "sender": data.get("sender", ""),
                    "helo": data.get("helo", ""),
                    "client_ip": data.get("client_ip", ""),
                    "client_hostname": data.get("client_hostname", ""),
                    "delivery_status": "received",
                },
                "security": {
                    "tls_used": data.get("tls_used", False),
                    "auth_method": data.get("auth_method", ""),
                    "spf_result": data.get("spf_result", ""),
                    "dkim_result": data.get("dkim_result", ""),
                    "dmarc_result": data.get("dmarc_result", ""),
                    "spam_score": data.get("spam_score", 0),
                },
            },
            "attachments": attachments,
            "eml_file": {
                "content_base64": base64.b64encode(eml_content.encode("utf-8")).decode("utf-8"),
                "size": len(eml_content),
            },
        }

        # Add organization_id extraction
        webhook_payload["organization_id"] = WebhookProcessor()._extract_organization_id(metadata)

        # Track storage usage for the processed email
        track_email_storage_usage(webhook_payload, metadata, attachments)

        # Queue webhook for processing (legacy)
        webhook_manager.queue_webhook(webhook_payload)

        # Dispatch via centralized webhook dispatcher
        dispatch_event(
            Events.EMAIL_INBOUND,
            data={
                "message_id": metadata.get("message_id", ""),
                "from": metadata.get("from", ""),
                "to": data.get("recipient", ""),
                "subject": metadata.get("subject", ""),
                "size": metadata.get("size", 0),
                "has_attachments": metadata.get("has_attachments", False),
                "spam_score": data.get("spam_score", 0),
                "tls_used": data.get("tls_used", False),
                "spf_result": data.get("spf_result", ""),
                "dkim_result": data.get("dkim_result", ""),
                "dmarc_result": data.get("dmarc_result", ""),
            },
            source_service="postfix",
        )

        logger.info(f"Inbound email webhook queued: {metadata.get('message_id', 'unknown')}")

        return {"status": "success", "message": "Webhook queued"}

    except Exception as e:
        logger.error(f"Inbound email webhook error: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.post("/webhook/email/outbound")
async def handle_outbound_email(request: Request):
    """Handle outbound email webhook"""
    try:
        data = await request.json()
        eml_content = data.get("eml_content", "")

        if not eml_content:
            return JSONResponse({"error": "No EML content provided"}, status_code=400)

        # Extract metadata and attachments
        metadata = extract_email_metadata(eml_content)
        attachments = extract_attachments(eml_content)

        # Create webhook payload
        webhook_payload = {
            "event": "email.smtp.outbound",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": {
                "direction": "outbound",
                "protocol": data.get("protocol", "smtp"),
                "server": data.get("server", "unknown"),
                "metadata": metadata,
                "delivery_info": {
                    "sender": data.get("sender", ""),
                    "recipients": data.get("recipients", []),
                    "relay_host": data.get("relay_host", ""),
                    "queue_id": data.get("queue_id", ""),
                    "delivery_status": data.get("delivery_status", "queued"),
                    "delivery_delay": data.get("delivery_delay", 0),
                    "dsn_status": data.get("dsn_status", ""),
                },
                "security": {
                    "tls_used": data.get("tls_used", False),
                    "auth_user": data.get("auth_user", ""),
                    "encryption_protocol": data.get("encryption_protocol", ""),
                },
            },
            "attachments": attachments,
            "eml_file": {
                "content_base64": base64.b64encode(eml_content.encode("utf-8")).decode("utf-8"),
                "size": len(eml_content),
            },
        }

        # Add organization_id extraction
        webhook_payload["organization_id"] = WebhookProcessor()._extract_organization_id(metadata)

        # Track storage usage for the processed email
        track_email_storage_usage(webhook_payload, metadata, attachments)

        # Queue webhook for processing (legacy)
        webhook_manager.queue_webhook(webhook_payload)

        # Dispatch via centralized webhook dispatcher
        dispatch_event(
            Events.EMAIL_OUTBOUND,
            data={
                "message_id": metadata.get("message_id", ""),
                "from": metadata.get("from", ""),
                "to": data.get("recipients", []),
                "subject": metadata.get("subject", ""),
                "size": metadata.get("size", 0),
                "queue_id": data.get("queue_id", ""),
                "delivery_status": data.get("delivery_status", "queued"),
                "tls_used": data.get("tls_used", False),
            },
            source_service="postfix",
        )

        logger.info(f"Outbound email webhook queued: {metadata.get('message_id', 'unknown')}")

        return {"status": "success", "message": "Webhook queued"}

    except Exception as e:
        logger.error(f"Outbound email webhook error: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.post("/webhook/email/imap")
async def handle_imap_event(request: Request):
    """Handle IMAP events (read, delete, move, etc.)"""
    try:
        data = await request.json()

        # Extract metadata for organization ID
        metadata = {"user": data.get("user", "")}

        webhook_payload = {
            "event": f"email.imap.{data.get('action', 'unknown')}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": {
                "direction": "imap",
                "protocol": "imap",
                "action": data.get("action", ""),  # read, delete, move, flag, etc.
                "user": data.get("user", ""),
                "mailbox": data.get("mailbox", ""),
                "message_uid": data.get("message_uid", ""),
                "client_info": {
                    "ip_address": data.get("client_ip", ""),
                    "user_agent": data.get("user_agent", ""),
                    "connection_id": data.get("connection_id", ""),
                },
                "message_info": {
                    "message_id": data.get("message_id", ""),
                    "subject": data.get("subject", ""),
                    "flags": data.get("flags", []),
                    "size": data.get("size", 0),
                },
            },
        }

        # Add organization_id extraction
        webhook_payload["organization_id"] = WebhookProcessor()._extract_organization_id(metadata)

        # Queue webhook for processing (legacy)
        webhook_manager.queue_webhook(webhook_payload)

        # Dispatch via centralized webhook dispatcher
        action = data.get("action", "unknown")
        event_map = {
            "read": Events.EMAIL_READ,
            "delete": Events.EMAIL_DELETED,
            "move": Events.EMAIL_MOVED,
            "copy": Events.EMAIL_COPIED,
            "flag": Events.EMAIL_FLAGGED,
            "unflag": Events.EMAIL_UNFLAGGED,
        }
        event_type = event_map.get(action, f"email.imap.{action}")

        dispatch_event(
            event_type,
            data={
                "user": data.get("user", ""),
                "mailbox": data.get("mailbox", ""),
                "message_uid": data.get("message_uid", ""),
                "message_id": data.get("message_id", ""),
                "action": action,
                "flags": data.get("flags", []),
            },
            source_service="dovecot",
        )

        logger.info(
            f"IMAP webhook queued: {data.get('action', 'unknown')} by {data.get('user', 'unknown')}"
        )

        return {"status": "success", "message": "Webhook queued"}

    except Exception as e:
        logger.error(f"IMAP webhook error: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.post("/webhook/email/pop3")
async def handle_pop3_event(request: Request):
    """Handle POP3 events"""
    try:
        data = await request.json()

        # Extract metadata for organization ID
        metadata = {"user": data.get("user", "")}

        webhook_payload = {
            "event": f"email.pop3.{data.get('action', 'unknown')}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": {
                "direction": "pop3",
                "protocol": "pop3",
                "action": data.get("action", ""),  # login, download, delete, etc.
                "user": data.get("user", ""),
                "client_info": {
                    "ip_address": data.get("client_ip", ""),
                    "connection_id": data.get("connection_id", ""),
                },
                "session_info": {
                    "messages_downloaded": data.get("messages_downloaded", 0),
                    "bytes_downloaded": data.get("bytes_downloaded", 0),
                    "session_duration": data.get("session_duration", 0),
                },
            },
        }

        # Add organization_id extraction
        webhook_payload["organization_id"] = WebhookProcessor()._extract_organization_id(metadata)

        # Queue webhook for processing (legacy)
        webhook_manager.queue_webhook(webhook_payload)

        # Dispatch via centralized webhook dispatcher
        action = data.get("action", "unknown")
        event_map = {
            "login": Events.POP3_SESSION_START,
            "download": Events.POP3_MESSAGES_DOWNLOADED,
            "logout": Events.POP3_SESSION_END,
        }
        event_type = event_map.get(action, f"email.pop3.{action}")

        dispatch_event(
            event_type,
            data={
                "user": data.get("user", ""),
                "action": action,
                "messages_downloaded": data.get("messages_downloaded", 0),
                "bytes_downloaded": data.get("bytes_downloaded", 0),
                "session_duration": data.get("session_duration", 0),
            },
            source_service="dovecot",
        )

        logger.info(
            f"POP3 webhook queued: {data.get('action', 'unknown')} by {data.get('user', 'unknown')}"
        )

        return {"status": "success", "message": "Webhook queued"}

    except Exception as e:
        logger.error(f"POP3 webhook error: {e}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.get("/webhook/status")
async def webhook_status():
    """Get webhook service status"""
    return {
        "status": "active",
        "queue_size": webhook_queue.qsize(),
        "webhook_urls_configured": len(WebhookProcessor().webhook_urls),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/webhook/test")
async def test_webhook(request: Request):
    """Test webhook functionality"""
    data = (
        await request.json() if request.headers.get("content-type") == "application/json" else None
    )

    test_payload = {
        "event": "test.webhook",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "payload": {"message": "This is a test webhook", "test_data": data or {}},
    }

    webhook_queue.put(test_payload)

    # Dispatch via centralized webhook dispatcher
    dispatch_event(
        Events.WEBHOOK_TEST,
        data=data or {"message": "Test webhook"},
        source_service="webhooks",
    )

    return {"status": "success", "message": "Test webhook queued"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "webhook", "timestamp": datetime.utcnow().isoformat()}


@app.get("/cleanup/stats")
async def cleanup_stats():
    """Get webhook cleanup service statistics"""
    try:
        stats = cleanup_service.get_cleanup_stats()
        return JSONResponse(stats, status_code=200)
    except Exception as e:
        logger.error(f"Error getting cleanup stats: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/cleanup/now")
async def cleanup_now():
    """Trigger immediate webhook cleanup"""
    try:
        result = cleanup_service.cleanup_now()
        return JSONResponse(result, status_code=200 if result["success"] else 500)
    except Exception as e:
        logger.error(f"Error performing immediate cleanup: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/cleanup/config")
async def get_cleanup_config():
    """Get current cleanup configuration"""
    return JSONResponse(cleanup_service.config, status_code=200)


@app.put("/cleanup/config")
async def update_cleanup_config(request: Request):
    """Update cleanup configuration"""
    try:
        new_config = await request.json()
        if not new_config:
            return JSONResponse({"error": "Invalid JSON data"}, status_code=400)

        cleanup_service.update_config(new_config)
        return JSONResponse(
            {"message": "Configuration updated successfully", "new_config": cleanup_service.config},
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Error updating cleanup config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    # Load environment variables

    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        db_host = os.getenv("DB_HOST", "mysql")
        db_name = os.getenv("DB_NAME", "mailserver")
        db_user = os.getenv("DB_USER", "mailuser")
        db_password = os.getenv("DB_PASSWORD", "mailpassword")
        DATABASE_URL = f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}"

    # Services are already initialized at import time

    # Initialize cleanup service with configuration
    cleanup_config = {
        "enabled": os.getenv("WEBHOOK_CLEANUP_ENABLED", "true").lower() == "true",
        "cleanup_interval_hours": int(os.getenv("WEBHOOK_CLEANUP_SCHEDULE_HOURS", "4")),
        "successful_retention_hours": int(os.getenv("WEBHOOK_SUCCESSFUL_RETENTION_HOURS", "24")),
        "failed_retention_hours": int(os.getenv("WEBHOOK_FAILED_RETENTION_HOURS", "72")),
        "auto_delete_successful": os.getenv("WEBHOOK_AUTO_DELETE_SUCCESSFUL", "true").lower()
        == "true",
        "cleanup_batch_size": int(os.getenv("WEBHOOK_CLEANUP_BATCH_SIZE", "1000")),
    }

    cleanup_service = WebhookCleanupService(DATABASE_URL, cleanup_config)
    cleanup_service.start()

    logger.info("✅ Webhook service initialized successfully")
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
