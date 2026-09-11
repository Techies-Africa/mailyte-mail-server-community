#!/usr/bin/env python3
"""
Postfix Email Tracking Injection Script

This script integrates with Postfix as a content filter to automatically inject
tracking pixels and rewrite links in outgoing HTML emails. It implements the
industry-standard email tracking workflow used by services like Mailgun.

WORKFLOW:
1. Postfix receives email on submission port (587) with authentication
2. Email is passed to this script via the content_filter mechanism
3. Script extracts email metadata (sender, recipients, message-id, etc.)
4. For HTML emails, script calls the tracking service API to:
   - Inject a 1x1 pixel tracking image for open tracking
   - Rewrite all links to go through click tracking service
5. Modified email is returned to Postfix for final delivery
6. Original email is delivered if any errors occur (fail-safe operation)

TECHNICAL IMPLEMENTATION:
- Reads email from stdin (Postfix pipe interface)
- Parses email using Python's email library
- Makes HTTP requests to tracking service for content modification
- Outputs modified email to stdout for Postfix delivery
- Maintains detailed logging for debugging and monitoring

SECURITY FEATURES:
- HMAC-signed tracking tokens prevent tampering
- Database lookup for proper tenant/domain association
- Graceful fallback to original email on any errors
- Input validation and sanitization
- Timeout protection for external service calls

PERFORMANCE OPTIMIZATIONS:
- Connection pooling for database queries
- Efficient email parsing with minimal memory usage
- Fast-fail for non-HTML emails
- Configurable timeout limits
- Multi-process support via Postfix maxproc setting

Author: Mail Server System
Created: 2024
License: MIT
"""

import atexit
import email
import email.policy
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import requests


# Postfix's pipe(8) daemon (this script's invoker -- see master.cf's
# tracking-filter service) hands the external command it execs a
# hardcoded minimal environment (LANG, MAIL_CONFIG, PATH, LC_CTYPE),
# regardless of main.cf's import_environment setting -- confirmed live by
# dumping os.environ as Postfix actually invoked this script. Real values
# come from entrypoint.sh's runtime.env instead (written from the
# container's actual environment at startup, before Postfix ever execs
# this). setdefault, not overwrite: never clobber a real env var if one
# somehow is present.
def _load_runtime_env(path="/etc/postfix/runtime.env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                # Skip blank values -- entrypoint.sh writes every listed
                # name even when the underlying env var was never set
                # (e.g. optional timeouts), and os.getenv(name, default)
                # only falls back to default when the key is MISSING, not
                # when it's present-but-empty. Setting "" here would
                # break int(os.getenv("...TIMEOUT", "5"))-style calls.
                if value:
                    os.environ.setdefault(key, value)
    except OSError:
        pass


_load_runtime_env()

# Configure comprehensive logging for debugging and monitoring.
#
# The stderr handler is WARNING-and-above on purpose. Postfix runs this
# script as a pipe(8) command and captures whatever it writes; at INFO level
# that is ~40 lines per message, and pipe(8) stops reading once it has
# enough for a bounce report. The writes then hit a closed pipe, CPython
# fails to flush stderr while shutting down, and the interpreter exits 120 --
# which Postfix reports as "Command died with status 120" and bounces the
# mail *after* it has already been reinjected and delivered. The sender got
# a bounce for a message the recipient received.
#
# Full INFO detail still goes to the file below, which is where anyone
# debugging this should look.
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d] %(message)s",
    handlers=[
        # Log to file for permanent record
        logging.FileHandler("/var/log/postfix_tracking.log"),
        # Only real problems reach Postfix -- see above.
        _stderr_handler,
    ],
)
logger = logging.getLogger("postfix_tracking")


def _detach_stderr_at_exit() -> None:
    """Point stderr at /dev/null before the interpreter shuts down.

    Belt-and-braces for the exit-120 problem above: even a handful of
    WARNING lines can race a pipe reader that has already gone away, and the
    failure mode is a bounced message rather than a lost log line. Once the
    work is done there is nothing left worth writing, so the final flush is
    made harmless rather than fatal.
    """
    try:
        sys.stderr.flush()
    except Exception:
        pass

    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
    except Exception:
        pass


atexit.register(_detach_stderr_at_exit)


# Per-message tracking opt-out. worker/api/routes/mailbox.py sets this header
# on person-to-person mailbox sends (default: every mailbox send, unless the
# deployment sets MAILBOX_SEND_TRACKING=true). "off" skips pixel and link
# injection for that one message; everything else about processing -- the
# X-Mailyte-ID stamp, body capture, archiving, delivery pacing -- still runs.
# The header is stripped before re-injection so it never reaches a recipient.
TRACKING_OPT_OUT_HEADER = "X-Mailyte-Tracking"

# The header line, folded continuation lines included, inside a raw header
# block. Used only on the fallback paths that re-inject ORIGINAL bytes.
_OPT_OUT_HEADER_LINE_RE = re.compile(
    rb"(?im)^X-Mailyte-Tracking:[^\r\n]*(?:\r?\n[ \t][^\r\n]*)*\r?\n"
)


def _strip_opt_out_header(raw: bytes) -> bytes:
    """Remove the opt-out header from raw message bytes.

    The normal path strips it off the parsed message; this covers the
    fallback paths that re-inject the original bytes (parse failure, any
    unexpected error). Fail open by contract: any problem returns the bytes
    untouched, because a leaked internal header is harmless next to a lost
    message.
    """
    try:
        head, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            head, sep, body = raw.partition(b"\n\n")
        if not sep:
            return raw
        return _OPT_OUT_HEADER_LINE_RE.sub(b"", head + sep) + body
    except Exception:
        return raw


def _insert_list_unsubscribe_raw(raw: bytes, unsubscribe_url: str) -> bytes:
    """Insert the two unsubscribe header lines into serialized message bytes.

    Raw-bytes surgery (same technique as _strip_opt_out_header) because
    setting these on the message object and letting the policy folder
    serialize them RFC-2047-encodes the long URL token. Prepending to the
    header block is legal and keeps this independent of every other header.
    Fail open: any problem returns the bytes untouched.
    """
    try:
        head, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            head, sep, body = raw.partition(b"\n\n")
        if not sep:
            return raw
        # Defense in depth: never double up if a header slipped through.
        if b"list-unsubscribe" in head.lower():
            return raw
        eol = b"\r\n" if b"\r\n" in head else b"\n"
        lines = (
            b"List-Unsubscribe: <" + unsubscribe_url.encode("ascii") + b">" + eol
            + b"List-Unsubscribe-Post: List-Unsubscribe=One-Click" + eol
        )
        return lines + head + sep + body
    except Exception as insert_error:
        logger.info(f"List-Unsubscribe raw insertion failed (headers unchanged): {insert_error}")
        return raw


def _insert_stream_header_raw(raw: bytes) -> bytes:
    """Insert `X-Mailyte-Stream: marketing` into serialized message bytes.

    Same raw-bytes surgery as _insert_list_unsubscribe_raw, for the same
    reason (header-block prepend, independent of the policy folder). The
    cleanup_stream FILTER behind the 10026 reinjection listener routes any
    message carrying this header out the marketing egress IP -- this is how a
    marketing-flagged SMTP credential's mail rides the marketing lane without
    the submitting tool knowing anything about our headers.

    Defense in depth: never doubles up if the sender already set the header.
    Fail open returns the bytes untouched -- the message then rides the
    default (transactional) lane, which is always deliverable.
    """
    try:
        head, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            head, sep, body = raw.partition(b"\n\n")
        if not sep:
            return raw
        if b"x-mailyte-stream" in head.lower():
            return raw
        eol = b"\r\n" if b"\r\n" in head else b"\n"
        return b"X-Mailyte-Stream: marketing" + eol + head + sep + body
    except Exception as insert_error:
        logger.info(f"Stream header raw insertion failed (headers unchanged): {insert_error}")
        return raw


def _set_list_unsubscribe_headers(
    message: email.message.EmailMessage, unsubscribe_url: str | None, tracking_id: str | None = None
) -> bool:
    """Decide whether RFC 8058 one-click unsubscribe headers apply.

    Called only after tracking was actually applied (never on the opt-out or
    no-HTML paths). Adds nothing when the tracking service sent no
    unsubscribe_url (an older tracking image omits it -- skip silently), and
    touches NOTHING when the message as authored already carries a
    List-Unsubscribe header (case-insensitive, per the email.message API):
    the sender's own unsubscribe machinery wins, -Post header included.
    Fail open by contract: any error logs, restores the headers to exactly
    what they were, and reports False -- delivery never depends on this.

    Returns True only when both headers were injected.
    """
    try:
        if not unsubscribe_url:
            logger.debug("No unsubscribe_url in tracking response; List-Unsubscribe skipped")
            return False
        if message.get("List-Unsubscribe") is not None:
            logger.debug(
                "Message already carries List-Unsubscribe; passing headers through untouched"
            )
            return False
    except Exception as check_error:
        logger.info(f"List-Unsubscribe pre-check failed (headers left unchanged): {check_error}")
        return False

    # Eligible. The headers are NOT set on the message object: the email
    # package's folder RFC-2047-encodes an unbreakable token longer than the
    # policy line limit -- a ~110-char URL comes out as =?utf-8?q?...?=, which
    # no mail client parses back into a working unsubscribe link (caught by
    # the phase-02 end-to-end smoke). The caller inserts the header lines into
    # the serialized bytes instead (_insert_list_unsubscribe_raw), where no
    # folding machinery runs.
    logger.info(f"List-Unsubscribe eligible (tracking_id={tracking_id or 'N/A'})")
    return True


class PostfixTrackingInjector:
    """
    Postfix content filter for automatic email tracking injection.

    This class implements the core functionality for integrating Postfix
    with an external tracking service. It handles email parsing, metadata
    extraction, tracking injection, and error recovery.

    The class is designed to be robust and fail-safe - if any error occurs
    during tracking injection, the original email is delivered unchanged
    to ensure mail delivery reliability.
    """

    def __init__(self):
        """
        Initialize the tracking injector with configuration from environment.

        Configuration is loaded from environment variables to support
        Docker containerization and different deployment environments.
        """
        # Core tracking service configuration
        self.tracking_service_url = os.getenv("TRACKING_SERVICE_URL", "http://tracking:8086")
        self.tracking_enabled = os.getenv("TRACKING_ENABLED", "true").lower() == "true"

        # Message-body capture for the Email Logs detail view. Bodies are the
        # most sensitive thing this system stores, so: opt-outable, size-capped,
        # and given an expiry that log_ingestor enforces. Attachments are never
        # stored (see _extract_body_parts).
        self.body_capture_enabled = os.getenv("BODY_CAPTURE_ENABLED", "true").lower() == "true"
        self.body_capture_max_bytes = int(os.getenv("BODY_CAPTURE_MAX_BYTES", "262144"))
        self.body_retention_days = int(os.getenv("BODY_RETENTION_DAYS", "30"))

        # Delivery optimizer (ISP throttling / IP warmup / reputation) configuration.
        # If the optimizer can't be reached, we pace down by a conservative fixed
        # delay rather than sending at full, unthrottled speed -- see
        # _check_delivery_timing's docstring for why.
        self.delivery_optimizer_url = os.getenv("DELIVERY_OPTIMIZER_URL", "http://localhost:8088")
        self.delivery_optimizer_timeout = int(os.getenv("DELIVERY_OPTIMIZER_TIMEOUT", "5"))
        self.delivery_optimizer_fallback_delay = float(
            os.getenv("DELIVERY_OPTIMIZER_FALLBACK_DELAY", "2")
        )

        # Default identifiers for tenant/domain mapping. tenant_id and
        # organization_id are the same concept in this file (the domains
        # table's organization_id column) -- aliased so get_tenant_domain_ids'
        # fallback branches (self.default_tenant_id) have a real attribute to
        # read instead of raising AttributeError on every lookup miss.
        self.default_organization_id = os.getenv("DEFAULT_ORGANIZATION_ID", "default")
        self.default_tenant_id = self.default_organization_id
        self.default_domain_id = os.getenv("DEFAULT_DOMAIN_ID", "default")

        # Database configuration for domain/tenant lookup
        # This allows proper multi-tenant tracking data separation
        self.db_host = os.getenv("DB_HOST", "mysql")
        self.db_port = int(os.getenv("DB_PORT", 3306))
        self.db_name = os.getenv("DB_NAME", "mailserver")
        self.db_user = os.getenv("DB_USER", "root")
        self.db_password = os.getenv("DB_PASSWORD", "password")

        # Performance optimization features
        self._connection_pool = None  # Future: implement connection pooling
        self._tenant_cache = {}  # Simple in-memory cache for tenant lookups
        self._credential_cache = {}  # Same-pattern cache for smtp_credentials.tracking_enabled
        self._cache_ttl = 300  # Cache entries valid for 5 minutes

        # SASL username from master.cf's -a ${sasl_username}, set by main().
        # None on unauthenticated port-25 and reinjected mail -- pipe(8)
        # removes the empty argument entirely, so _parse_cli_args may never
        # see it. Initialized here so process_email can read it even when
        # this class is driven outside main() (tests, ad-hoc invocation).
        self.cli_sasl_username = None

        # Sidecar metadata from the last successful tracking-inject response
        # (unsubscribe_url, tracking_id). Reset per call by
        # inject_tracking_for_recipient; read by process_message_content for
        # the List-Unsubscribe header decision.
        self._last_tracking_meta = {}

        # Timeout configuration for external service calls
        self.api_timeout = int(os.getenv("TRACKING_API_TIMEOUT", "10"))

        # Continuous mail archive (DR-2). This script already holds the full
        # parsed MIME of every outbound message, which makes it the only place
        # on the send path that can hand the archiver a complete RFC 2822 copy
        # without re-reading it from anywhere.
        self.archive_enabled = os.getenv("ARCHIVE_OUTBOUND_ENABLED", "true").lower() == "true"
        self.archive_service_url = os.getenv("ARCHIVE_SERVICE_URL", "http://archiver:8083").rstrip(
            "/"
        )
        # Much shorter than api_timeout. A slow archiver must not add ten
        # seconds to every delivery; the archive is a durability guarantee, not
        # a delivery dependency, and a message we fail to archive is one we
        # still deliver.
        self.archive_timeout = float(os.getenv("ARCHIVE_TIMEOUT", "3"))
        self.db_timeout = int(os.getenv("DB_TIMEOUT", "5"))

        # Log initialization with current configuration
        logger.info("Tracking injector initialized:")
        logger.info(f"  - Tracking enabled: {self.tracking_enabled}")
        logger.info(f"  - Service URL: {self.tracking_service_url}")
        logger.info(f"  - Database: {self.db_host}:{self.db_port}/{self.db_name}")
        logger.info(f"  - API timeout: {self.api_timeout}s")

    def _setup_signal_handlers(self):
        """
        Set up signal handlers for graceful shutdown.

        This ensures that the process can be cleanly terminated by Postfix
        without leaving emails in an inconsistent state.
        """

        def signal_handler(signum, frame):
            logger.warning(f"Received signal {signum}, shutting down gracefully")
            sys.exit(1)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def extract_email_metadata(self, message: email.message.EmailMessage) -> dict[str, Any] | None:
        """
        Extract comprehensive metadata from email message for tracking.

        This method parses the email headers and extracts all information
        needed for proper tracking injection, including sender, recipients,
        message identification, and domain information.

        Args:
            message: Parsed email.message.EmailMessage object

        Returns:
            dict: Comprehensive metadata dictionary with all tracking info
            None: If metadata extraction fails (email will pass through unchanged)
        """
        try:
            # Extract sender information
            sender = self._clean_email_address(message.get("From", ""))
            if not sender:
                logger.warning("No valid sender found in email")
                return None

            # Extract all recipient types (To, Cc, Bcc)
            recipients = []
            for header in ["To", "Cc", "Bcc"]:
                header_value = message.get(header, "")
                if header_value:
                    # Handle comma-separated recipient lists
                    header_recipients = [
                        self._clean_email_address(addr.strip()) for addr in header_value.split(",")
                    ]
                    recipients.extend([addr for addr in header_recipients if addr])

            if not recipients:
                logger.warning("No valid recipients found in email")
                return None

            # Generate or extract unique email identifier
            message_id = message.get("Message-ID", "").strip("<>")
            if not message_id:
                # Generate deterministic message ID if not present
                timestamp = str(int(datetime.now().timestamp()))
                sender_hash = hashlib.md5(sender.encode()).hexdigest()[:8]
                hostname = os.getenv("HOSTNAME", "mail.local")
                message_id = f"{timestamp}.{sender_hash}@{hostname}"
                logger.debug(f"Generated message ID: {message_id}")

            # Extract domain information for tenant resolution
            sender_domain = sender.split("@")[-1] if "@" in sender else self.default_domain_id

            # Build comprehensive metadata dictionary
            metadata = {
                "email_id": message_id,
                "sender": sender,
                "recipients": recipients,
                "recipient_count": len(recipients),
                "subject": message.get("Subject", ""),
                "sender_domain": sender_domain,
                "date": message.get("Date", ""),
                "content_type": message.get_content_type(),
                "has_attachments": self._check_attachments(message),
                "email_size": len(str(message)),
                "priority": message.get("X-Priority", "normal"),
                "user_agent": message.get("User-Agent", ""),
                "x_mailer": message.get("X-Mailer", ""),
            }

            logger.debug(f"Extracted email metadata: {json.dumps(metadata, indent=2)}")
            return metadata

        except Exception as e:
            logger.error(f"Error extracting email metadata: {e}", exc_info=True)
            return None

    def _clean_email_address(self, address: str) -> str:
        """
        Clean and validate email address from header.

        Email headers can contain display names and formatting that needs
        to be cleaned to extract just the email address.

        Args:
            address: Raw email address from header

        Returns:
            str: Clean email address or empty string if invalid
        """
        if not address:
            return ""

        # Remove display name and extract email from "Name <email>" format
        import email.utils

        try:
            parsed = email.utils.parseaddr(address)
            email_addr = parsed[1].lower().strip()

            # Basic email validation
            if "@" in email_addr and "." in email_addr.split("@")[1]:
                return email_addr
        except Exception:
            pass

        return ""

    def _check_attachments(self, message: email.message.EmailMessage) -> bool:
        """
        Check if email has attachments.

        Args:
            message: Email message object

        Returns:
            bool: True if email has attachments
        """
        if not message.is_multipart():
            return False

        for part in message.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" in disposition.lower():
                return True

        return False

    def get_tenant_domain_ids(self, sender_domain: str, sender_email: str) -> tuple[str, str]:
        """
        Resolve tenant and domain IDs from database based on sender information.

        This method implements a hierarchical lookup strategy:
        1. Check cache for recent lookups (performance optimization)
        2. Query database for domain configuration
        3. Fallback to user-specific lookup
        4. Use default values if no configuration found

        The tenant/domain system allows proper multi-tenant isolation
        of tracking data and configuration.

        Args:
            sender_domain: Domain part of sender email address
            sender_email: Full sender email address

        Returns:
            tuple: (tenant_id, domain_id) for tracking data association
        """
        cache_key = f"{sender_domain}:{sender_email}"
        current_time = time.time()

        # Check cache first for performance
        if cache_key in self._tenant_cache:
            cached_entry = self._tenant_cache[cache_key]
            if current_time - cached_entry["timestamp"] < self._cache_ttl:
                logger.debug(f"Using cached tenant/domain for {sender_email}")
                return cached_entry["tenant_id"], cached_entry["domain_id"]
            else:
                # Remove expired cache entry
                del self._tenant_cache[cache_key]

        try:
            # Import PyMySQL with proper error handling
            try:
                import pymysql
            except ImportError:
                logger.error("PyMySQL not available, using default organization/domain IDs")
                return self.default_organization_id, self.default_domain_id

            # Establish database connection with timeout
            connection = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset="utf8mb4",
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout,
            )

            try:
                with connection.cursor() as cursor:
                    # Primary lookup: domain configuration table
                    # `domains` has no tracking_enabled column and never has --
                    # no migration in database/ defines one. Selecting it made
                    # every lookup fail with
                    #   (1054, "Unknown column 'tracking_enabled' in 'field list'")
                    # so no message was ever attributed to its real tenant or
                    # domain; they all silently fell back to the "default"
                    # identifiers below. Whether tracking runs at all is a
                    # deployment-wide setting (TRACKING_ENABLED), which
                    # self.tracking_enabled already carries.
                    cursor.execute(
                        """
                        SELECT id, organization_id
                        FROM domains
                        WHERE domain = %s AND active = 1
                    """,
                        (sender_domain,),
                    )
                    domain_result = cursor.fetchone()

                    if domain_result:
                        domain_id = str(domain_result[0])
                        tenant_id = domain_result[1] or self.default_tenant_id
                        tracking_enabled = self.tracking_enabled

                        logger.debug(
                            f"Found domain config: domain_id={domain_id}, "
                            f"tenant_id={tenant_id}, tracking_enabled={tracking_enabled}"
                        )
                    else:
                        # Secondary lookup: the sending mailbox itself.
                        #
                        # This queried `users` on u.domain_id / u.active and
                        # joined d.tenant_id -- three columns that do not
                        # exist (users is the operator/admin table: id,
                        # organization_id, email, password_hash, role,
                        # is_active). A sender address is a mailbox, so the
                        # lookup belongs on email_accounts, which really does
                        # carry both domain_id and organization_id.
                        cursor.execute(
                            """
                            SELECT domain_id, organization_id
                            FROM email_accounts
                            WHERE email = %s AND status = 'active'
                        """,
                            (sender_email,),
                        )
                        user_result = cursor.fetchone()

                        if user_result and user_result[0]:
                            domain_id = str(user_result[0])
                            tenant_id = user_result[1] or self.default_tenant_id
                            logger.debug(
                                f"Found user config: domain_id={domain_id}, tenant_id={tenant_id}"
                            )
                        else:
                            # Use default values
                            domain_id = self.default_domain_id
                            tenant_id = self.default_tenant_id
                            logger.debug(
                                f"Using defaults: domain_id={domain_id}, tenant_id={tenant_id}"
                            )

            finally:
                connection.close()

            # Cache the result for future lookups
            self._tenant_cache[cache_key] = {
                "tenant_id": tenant_id,
                "domain_id": domain_id,
                "timestamp": current_time,
            }

            logger.debug(
                f"Resolved tenant_id: {tenant_id}, domain_id: {domain_id} for {sender_email}"
            )
            return tenant_id, domain_id

        except Exception as e:
            logger.error(f"Database error getting tenant/domain IDs: {e}")
            # Always fall back to defaults to ensure email delivery
            return self.default_tenant_id, self.default_domain_id

    def get_credential_tracking_enabled(self, sasl_username: str) -> bool:
        """
        Whether the authenticated SMTP credential wants tracking applied.

        Reads smtp_credentials.tracking_enabled (BOOLEAN NOT NULL DEFAULT 1,
        alembic 0026) for the ${sasl_username} Postfix authenticated, with the
        same in-process cache pattern and TTL as get_tenant_domain_ids.

        Fail open by contract, in every branch: a missing row means the
        username is not an SMTP API credential (mailbox logins authenticate
        on the same ports) so there is no toggle to honour, and an
        unreachable database or any other error must neither strip tracking
        from everyone nor ever block mail -- both read as "enabled".

        Args:
            sasl_username: The SASL login Postfix authenticated (never empty
                here; the caller gates on its presence)

        Returns:
            bool: False only when a row exists and its toggle is off
        """
        cache_key = sasl_username
        current_time = time.time()

        # Check cache first for performance
        if cache_key in self._credential_cache:
            cached_entry = self._credential_cache[cache_key]
            if current_time - cached_entry["timestamp"] < self._cache_ttl:
                logger.debug(f"Using cached credential tracking flag for {sasl_username}")
                return cached_entry["tracking_enabled"]
            else:
                # Remove expired cache entry
                del self._credential_cache[cache_key]

        try:
            # Import PyMySQL with proper error handling
            try:
                import pymysql
            except ImportError:
                logger.error("PyMySQL not available, treating credential tracking as enabled")
                return True

            # Establish database connection with timeout
            connection = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset="utf8mb4",
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout,
            )

            try:
                with connection.cursor() as cursor:
                    # `stream` arrives with alembic 0027; a pre-migration
                    # database answers 1054 for the two-column form, so fall
                    # back to the original query with stream defaulted.
                    try:
                        cursor.execute(
                            """
                            SELECT tracking_enabled, stream
                            FROM smtp_credentials
                            WHERE username = %s
                        """,
                            (sasl_username,),
                        )
                        row = cursor.fetchone()
                        stream = "transactional" if row is None else (row[1] or "transactional")
                    except Exception:
                        cursor.execute(
                            """
                            SELECT tracking_enabled
                            FROM smtp_credentials
                            WHERE username = %s
                        """,
                            (sasl_username,),
                        )
                        row = cursor.fetchone()
                        stream = "transactional"
            finally:
                connection.close()

            # Missing row => not an SMTP API credential => enabled (see above)
            tracking_enabled = True if row is None else bool(row[0])

            # Cache the result for future lookups
            self._credential_cache[cache_key] = {
                "tracking_enabled": tracking_enabled,
                "stream": stream,
                "timestamp": current_time,
            }

            logger.debug(
                f"Resolved credential tracking_enabled={tracking_enabled} "
                f"stream={stream} for {sasl_username}"
            )
            return tracking_enabled

        except Exception as e:
            logger.error(f"Database error reading credential tracking flag: {e}")
            # Fail open: a broken lookup must not strip tracking or block mail
            return True

    def get_credential_stream(self, sasl_username: str) -> str:
        """
        The egress stream the authenticated credential is flagged for
        ('transactional' | 'marketing', smtp_credentials.stream).

        Piggybacks on get_credential_tracking_enabled's cache -- one DB
        round-trip resolves both flags. Fails SAFE (not open) by contract:
        any lookup problem reads as 'transactional', because the default lane
        always delivers while a wrong marketing reroute can bounce hosted
        recipients ("loops back to myself").
        """
        try:
            entry = self._credential_cache.get(sasl_username)
            if entry is None or time.time() - entry["timestamp"] >= self._cache_ttl:
                self.get_credential_tracking_enabled(sasl_username)
                entry = self._credential_cache.get(sasl_username)
            return (entry or {}).get("stream", "transactional")
        except Exception as e:
            logger.error(f"Credential stream lookup failed (defaulting transactional): {e}")
            return "transactional"

    def is_domain_hosted_here(self, domain: str) -> bool:
        """
        Whether a recipient domain is hosted on this platform (`domains`
        table, any tenant). Used to withhold the marketing stream header for
        internal mail: the cleanup_stream FILTER is per-message and overrides
        local delivery, so a marketing reroute of a hosted recipient relays
        to our own MX and bounces 5.4.6 (verified live 2026-09-08).

        Fails CLOSED (treat as hosted) -- withholding the header is always
        deliverable; adding it wrongly is not.
        """
        key = f"hosted:{domain.lower()}"
        current_time = time.time()
        cached = self._credential_cache.get(key)
        if cached and current_time - cached["timestamp"] < self._cache_ttl:
            return cached["hosted"]

        try:
            import pymysql

            connection = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset="utf8mb4",
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout,
            )
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM domains WHERE domain = %s LIMIT 1",
                        (domain.lower(),),
                    )
                    hosted = cursor.fetchone() is not None
            finally:
                connection.close()

            self._credential_cache[key] = {"hosted": hosted, "timestamp": current_time}
            return hosted
        except Exception as e:
            logger.error(f"Hosted-domain lookup failed for {domain} (treating as hosted): {e}")
            return True

    def inject_tracking_for_recipient(
        self, html_content: str, email_id: str, recipient: str, organization_id: str, domain_id: str
    ) -> str:
        """
        Inject tracking for a specific recipient using the tracking service API.

        This method calls the external tracking service to:
        1. Inject a 1x1 pixel tracking image for open tracking
        2. Rewrite all links to go through click tracking redirects
        3. Generate HMAC-signed tracking tokens for security

        The tracking service handles the complex logic of HTML parsing
        and modification while this script focuses on integration.

        Args:
            html_content: Original HTML content of the email
            email_id: Unique identifier for this email
            recipient: Email address of the recipient
            tenant_id: Tenant identifier for data isolation
            domain_id: Domain identifier for configuration

        Returns:
            str: Modified HTML content with tracking injected, or original if failed
        """
        # Reset per-call response metadata; populated only on a successful
        # inject, so a previous part's values can never leak into this one's
        # List-Unsubscribe decision.
        self._last_tracking_meta = {}

        try:
            # Prepare API payload with all tracking parameters
            payload = {
                "html_content": html_content,
                "email_id": email_id,
                "recipient": recipient,
                "tenant_id": organization_id,
                "domain_id": domain_id,
                "enable_open_tracking": True,
                "enable_click_tracking": True,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "source": "postfix_injector",
            }

            # Make API call to tracking service with timeout protection
            logger.debug(f"Calling tracking service for recipient: {recipient}")
            response = requests.post(
                f"{self.tracking_service_url}/api/tracking/inject",
                json=payload,
                timeout=self.api_timeout,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Postfix-Tracking-Injector/1.0",
                },
            )

            # Process successful response
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    tracking_stats = result.get("tracking_injected", {})
                    logger.info(f"Tracking injected successfully for {recipient}:")
                    logger.info(f"  - Open tracking: {tracking_stats.get('open_tracking', False)}")
                    logger.info(
                        f"  - Click tracking: {tracking_stats.get('click_tracking', False)}"
                    )
                    logger.info(f"  - Links rewritten: {tracking_stats.get('links_rewritten', 0)}")
                    logger.info(f"  - Tracking ID: {result.get('tracking_id', 'N/A')}")

                    # Sidecar metadata for the caller's List-Unsubscribe
                    # decision. Defensive .get(): an older tracking image
                    # sends neither key, and absent unsubscribe_url simply
                    # skips the header injection upstream.
                    self._last_tracking_meta = {
                        "unsubscribe_url": result.get("unsubscribe_url"),
                        "tracking_id": result.get("tracking_id"),
                    }

                    return result["modified_content"]
                else:
                    error_msg = result.get("error", "Unknown error")
                    logger.warning(f"Tracking injection failed for {recipient}: {error_msg}")
            else:
                logger.error(
                    f"Tracking service HTTP error for {recipient}: "
                    f"{response.status_code} - {response.text[:200]}"
                )

        except requests.exceptions.Timeout:
            logger.error(f"Tracking service timeout for {recipient} (>{self.api_timeout}s)")
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to tracking service for {recipient}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error calling tracking service for {recipient}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error injecting tracking for {recipient}: {e}", exc_info=True)

        # Always return original content if tracking injection fails
        # This ensures email delivery reliability over tracking functionality
        logger.info(f"Returning original content for {recipient} due to tracking failure")
        return html_content

    def process_message_content(
        self, message: email.message.EmailMessage, metadata: dict[str, Any]
    ) -> email.message.EmailMessage:
        """
        Process email message content to inject tracking for all recipients.

        This method handles both multipart and single-part emails, identifying
        HTML content and processing it for tracking injection. It supports
        multiple recipients by processing each one individually.

        For multipart emails, only HTML parts are modified while preserving
        all other parts (text, attachments, etc.) unchanged.

        Args:
            message: Original email message object
            metadata: Extracted email metadata

        Returns:
            email.message.EmailMessage: Modified message with tracking injected
        """
        try:
            email_id = metadata["email_id"]
            sender_domain = metadata["sender_domain"]
            sender_email = metadata["sender"]
            recipients = metadata["recipients"]

            logger.info(f"Processing message content for {len(recipients)} recipients")

            # Get tenant and domain IDs for this sender
            tenant_id, domain_id = self.get_tenant_domain_ids(sender_domain, sender_email)
            organization_id = tenant_id  # Using tenant_id as organization_id for now

            # Track processing statistics
            processed_parts = 0
            modified_parts = 0

            if message.is_multipart():
                # Handle multipart messages (most common for HTML emails)
                logger.debug("Processing multipart message")

                for part in message.walk():
                    if part.get_content_type() == "text/html":
                        processed_parts += 1
                        logger.debug(f"Found HTML part {processed_parts}")

                        try:
                            html_content = part.get_content()
                            if not html_content or len(html_content.strip()) == 0:
                                logger.debug("Skipping empty HTML part")
                                continue

                            # Process tracking for the first recipient
                            # Note: For multiple recipients, ideally each should get
                            # a separate email with their own tracking. This is a
                            # simplified implementation for single-recipient tracking.
                            primary_recipient = recipients[0] if recipients else None
                            if primary_recipient:
                                modified_content = self.inject_tracking_for_recipient(
                                    html_content,
                                    email_id,
                                    primary_recipient,
                                    organization_id,
                                    domain_id,
                                )

                                # Only update if content was actually modified
                                if modified_content != html_content:
                                    part.set_content(modified_content, subtype="html")
                                    modified_parts += 1
                                    logger.debug(f"Modified HTML part {processed_parts}")

                                # For now, only process the first HTML part
                                break
                            else:
                                logger.warning("No recipients found for multipart HTML processing")

                        except Exception as e:
                            logger.error(f"Error processing HTML part {processed_parts}: {e}")
                            continue

            else:
                # Handle single-part HTML messages
                if message.get_content_type() == "text/html":
                    processed_parts += 1
                    logger.debug("Processing single-part HTML message")

                    try:
                        html_content = message.get_content()
                        if html_content and len(html_content.strip()) > 0:
                            # Process for primary recipient
                            primary_recipient = recipients[0] if recipients else None
                            if primary_recipient:
                                modified_content = self.inject_tracking_for_recipient(
                                    html_content,
                                    email_id,
                                    primary_recipient,
                                    organization_id,
                                    domain_id,
                                )

                                if modified_content != html_content:
                                    message.set_content(modified_content, subtype="html")
                                    modified_parts += 1
                                    logger.debug("Modified single-part HTML content")
                            else:
                                logger.warning(
                                    "No recipients found for single-part HTML processing"
                                )
                        else:
                            logger.debug("Skipping empty HTML content")
                    except Exception as e:
                        logger.error(f"Error processing single-part HTML: {e}")

            # RFC 8058 one-click unsubscribe headers. Only when tracking was
            # ACTUALLY applied (modified_parts > 0 -- a failed or no-op inject
            # never gets headers; the opt-out and no-HTML paths return before
            # this method is even called) and the successful inject response
            # carried an unsubscribe_url (_last_tracking_meta; an older
            # tracking image sends none, and the helper then skips silently).
            # A message already carrying its own List-Unsubscribe passes
            # through untouched, -Post header included. Fail open: any error
            # logs and the message keeps the headers it had.
            try:
                self._pending_unsubscribe_url = None
                if modified_parts:
                    meta = getattr(self, "_last_tracking_meta", {}) or {}
                    if _set_list_unsubscribe_headers(
                        message, meta.get("unsubscribe_url"), meta.get("tracking_id")
                    ):
                        # Applied to the serialized BYTES in process_email, not
                        # to the message object -- see _insert_list_unsubscribe_raw.
                        self._pending_unsubscribe_url = meta.get("unsubscribe_url")
            except Exception as unsub_error:
                logger.info(f"List-Unsubscribe wiring failed (headers unchanged): {unsub_error}")

            # Log processing summary
            logger.info("Content processing completed:")
            logger.info(f"  - HTML parts found: {processed_parts}")
            logger.info(f"  - Parts modified: {modified_parts}")
            logger.info(f"  - Email ID: {email_id}")
            logger.info(f"  - Tenant: {tenant_id}, Domain: {domain_id}")

            return message

        except Exception as e:
            logger.error(f"Critical error processing message content: {e}", exc_info=True)
            # Return original message to ensure delivery
            return message

    def process_email(self):
        """
        Main processing function implementing the Postfix content filter interface.

        This is the entry point for the Postfix pipe transport. It:
        1. Reads the complete email from stdin (Postfix provides this)
        2. Parses and validates the email structure
        3. Extracts metadata needed for tracking
        4. Processes HTML content for tracking injection
        5. Outputs the modified email to stdout (Postfix receives this)
        6. Handles all errors gracefully to ensure email delivery

        The function implements the standard Postfix content filter protocol
        and maintains compatibility with Postfix's expectations.
        """
        # Set up signal handling for graceful shutdown
        self._setup_signal_handlers()

        # Store original email for error recovery
        raw_email = None

        try:
            logger.info("=" * 60)
            logger.info("Starting email processing")

            # Read complete email from stdin (Postfix pipe interface)
            logger.debug("Reading email from stdin...")
            raw_email = sys.stdin.buffer.read()

            if not raw_email:
                logger.error("No email content received from stdin")
                sys.exit(75)  # EX_TEMPFAIL - tells Postfix to retry later

            email_size = len(raw_email)
            logger.info(f"Received email: {email_size} bytes")

            # Consult the delivery optimizer (ISP throttling / IP warmup /
            # outbound-abuse detection) before doing any further work. Uses
            # cli_sender/cli_recipients (Postfix's own argv, set in main())
            # rather than parsed metadata, so this gate runs for every
            # message regardless of tracking-enabled, HTML, or parse state
            # -- those are all decided further down and must not bypass it.
            sender_domain = (
                self.cli_sender.split("@", 1)[-1].lower() if "@" in self.cli_sender else ""
            )
            organization_id, _domain_id = self.get_tenant_domain_ids(sender_domain, self.cli_sender)
            recipient_domains = sorted(
                {r.split("@", 1)[-1].lower() for r in self.cli_recipients if "@" in r}
            )

            for recipient_domain in recipient_domains:
                if not self._check_delivery_timing(recipient_domain, organization_id):
                    logger.warning(
                        f"Delivery optimizer deferred sending to {recipient_domain} "
                        f"(org={organization_id}); tempfailing so Postfix retries the "
                        f"whole batch later"
                    )
                    sys.exit(75)  # EX_TEMPFAIL - tells Postfix to retry later

            for recipient_domain in recipient_domains:
                self._record_delivery_attempt(recipient_domain, organization_id)

            # Parse email using Python's robust email parser
            try:
                message = email.message_from_bytes(raw_email, policy=email.policy.default)
            except Exception as e:
                logger.error(f"Failed to parse email: {e}")
                # Pass through unparseable emails unchanged
                self._reinject_email(raw_email, {"sender": "", "recipients": sys.argv[1:]})
                return

            # Stamp every email with a unique Mailyte ID (ULID)
            # This ID ties the email across all systems: tracking, webhooks,
            # message trace, analytics, and IMAP delivery
            mailyte_id = self._generate_mailyte_id()
            if "X-Mailyte-ID" not in message:
                message["X-Mailyte-ID"] = mailyte_id
                logger.info(f"Stamped X-Mailyte-ID: {mailyte_id}")
            else:
                mailyte_id = message["X-Mailyte-ID"]
                logger.info(f"Existing X-Mailyte-ID: {mailyte_id}")

            # Per-message opt-out from the mailbox send path. Read, then
            # stripped IMMEDIATELY, so no later re-injection of `message` can
            # carry it to a recipient. INFO only, never stderr -- see the
            # logging note at the top of this file.
            tracking_opted_out = False
            try:
                opt_out_value = (message.get(TRACKING_OPT_OUT_HEADER) or "").strip().lower()
                if opt_out_value:
                    tracking_opted_out = opt_out_value == "off"
                    del message[TRACKING_OPT_OUT_HEADER]
                    logger.info(f"{TRACKING_OPT_OUT_HEADER}: {opt_out_value} (header stripped)")
            except Exception as opt_out_error:
                logger.info(f"Opt-out check failed (treated as no opt-out): {opt_out_error}")

            # Check if tracking is globally enabled
            # Note: even with tracking disabled, the X-Mailyte-ID header is still stamped
            if not self.tracking_enabled:
                logger.info("Tracking disabled globally, passing email with Mailyte ID only")
                self._reinject_email(
                    message.as_bytes(policy=email.policy.SMTP),
                    {"sender": "", "recipients": sys.argv[1:]},
                )
                return

            # Per-credential tracking toggle (smtp_credentials.tracking_enabled).
            # Feeds the SAME decision as the X-Mailyte-Tracking opt-out above,
            # so a disabled credential behaves identically: no pixel, no
            # rewritten links, no List-Unsubscribe -- while the X-Mailyte-ID
            # stamp, body capture, archiving, and delivery pacing all still
            # run. Only consulted for authenticated submissions: pipe(8)
            # removes the -a argument entirely when ${sasl_username} is empty
            # (port 25, reinjection), so cli_sasl_username is None there and
            # the toggle cannot apply. Skipped when the message already opted
            # out -- no point paying a DB round-trip for a decided outcome.
            # The lookup itself fails open (any DB problem reads as enabled),
            # so this can never strip tracking by accident or block mail.
            sasl_username = getattr(self, "cli_sasl_username", None)
            if not tracking_opted_out and sasl_username:
                if not self.get_credential_tracking_enabled(sasl_username):
                    tracking_opted_out = True
                    logger.info(
                        f"Credential {sasl_username} has tracking disabled; "
                        "treating as per-message opt-out"
                    )

            # Extract and validate email metadata
            logger.debug("Extracting email metadata...")
            metadata = self.extract_email_metadata(message)
            if not metadata:
                logger.warning("Failed to extract valid metadata, passing through unchanged")
                self._reinject_email(
                    message.as_bytes(policy=email.policy.SMTP),
                    {"sender": "", "recipients": sys.argv[1:]},
                )
                return

            # Attach Mailyte ID to metadata for downstream use (webhooks, tracking, logs)
            metadata["mailyte_id"] = mailyte_id

            # Per-credential egress stream (smtp_credentials.stream): a
            # marketing-flagged credential's mail rides the marketing IP with
            # no cooperation from the submitting tool -- the header is
            # inserted at reinjection (_reinject_email) so every exit path
            # (plain-text, opt-out, full injection) carries it. Withheld when
            # the sender already chose a stream, and when ANY recipient is
            # hosted here (the FILTER is per-message and would loop-bounce
            # local delivery). Every lookup failure defaults to the
            # transactional lane, which always delivers.
            self._pending_stream_marketing = False
            try:
                if (
                    sasl_username
                    and message.get("X-Mailyte-Stream") is None
                    and self.get_credential_stream(sasl_username) == "marketing"
                ):
                    recipient_domains = {
                        r.rsplit("@", 1)[1].lower()
                        for r in metadata.get("recipients", [])
                        if "@" in r
                    }
                    if recipient_domains and not any(
                        self.is_domain_hosted_here(d) for d in recipient_domains
                    ):
                        self._pending_stream_marketing = True
                        logger.info(
                            f"Credential {sasl_username} is marketing-stream; "
                            "message will egress via the marketing transport"
                        )
            except Exception as stream_error:
                logger.error(f"Stream flag resolution failed (transactional lane): {stream_error}")

            # Log email processing details
            logger.info(f"Processing email: {metadata['email_id']} (mailyte_id={mailyte_id})")
            logger.info(f"  - From: {metadata['sender']}")
            logger.info(f"  - To: {len(metadata['recipients'])} recipients")
            logger.info(f"  - Subject: {metadata['subject'][:50]}...")
            logger.info(f"  - Content-Type: {metadata['content_type']}")
            logger.info(f"  - Has attachments: {metadata['has_attachments']}")

            # Capture the rendered body for Email Logs. Placed here, before the
            # HTML/no-HTML branch below, so a plain-text-only message is stored
            # too -- that branch returns early and would otherwise skip it.
            # Fails silently by contract; mail flow does not depend on it.
            metadata["organization_id"] = organization_id
            self._store_email_body(message, metadata)

            # Archive the raw message. Placed alongside body capture and before
            # the HTML branch for the same reason: that branch returns early,
            # and a plain-text message must be archived exactly like an HTML
            # one. Archives the message AS AUTHORED, before tracking injection
            # -- a restored message should be what the sender wrote, not a copy
            # carrying rewritten links and a tracking pixel.
            self._archive_message(raw_email, metadata)

            # Only process emails with HTML content, and honour the
            # per-message opt-out: a mailbox holder's person-to-person mail is
            # delivered exactly as written -- no pixel, no rewritten links.
            has_html = self._check_html_content(message)
            if not has_html or tracking_opted_out:
                logger.info(
                    "Passing email through with Mailyte ID only "
                    f"(html={has_html}, tracking_opted_out={tracking_opted_out})"
                )
                self._reinject_email(message.as_bytes(policy=email.policy.SMTP), metadata)
                return

            logger.info("HTML content detected, proceeding with tracking injection")

            # Process message for tracking injection
            logger.debug("Starting tracking injection process...")
            modified_message = self.process_message_content(message, metadata)

            # Reinject modified email back into Postfix via the post-tracking
            # listener (127.0.0.1:10026) which delivers without re-filtering
            try:
                modified_email = modified_message.as_bytes(policy=email.policy.SMTP)
                pending_unsub = getattr(self, "_pending_unsubscribe_url", None)
                if pending_unsub:
                    modified_email = _insert_list_unsubscribe_raw(modified_email, pending_unsub)
                output_size = len(modified_email)

                logger.info("Email processing completed successfully:")
                logger.info(f"  - Original size: {email_size} bytes")
                logger.info(f"  - Modified size: {output_size} bytes")
                logger.info(f"  - Size change: {output_size - email_size:+d} bytes")

                self._reinject_email(modified_email, metadata)

            except Exception as e:
                logger.error(f"Failed to reinject modified email: {e}, trying original")
                self._reinject_email(raw_email, metadata)

            logger.info("Email processing pipeline completed")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Critical error in email processing pipeline: {e}", exc_info=True)

            # Always attempt to deliver the original email
            try:
                if raw_email:
                    logger.info("Delivering original email due to processing error")
                    self._reinject_email(
                        raw_email,
                        metadata if metadata else {"sender": "", "recipients": sys.argv[1:]},
                    )
                else:
                    logger.error("No original email available for fallback delivery")
                    sys.exit(75)  # EX_TEMPFAIL
            except Exception as fallback_error:
                logger.error(f"Failed to deliver original email: {fallback_error}")
                sys.exit(75)  # EX_TEMPFAIL

    def _generate_mailyte_id(self) -> str:
        """
        Generate a ULID for the X-Mailyte-ID header.

        Uses python-ulid if available, falls back to a timestamp + random
        hex string that's still unique and sortable.
        """
        try:
            from ulid import ULID

            return str(ULID())
        except ImportError:
            # Fallback: timestamp-based unique ID (not a true ULID but
            # globally unique and sortable — works without extra packages)
            import uuid

            ts = int(time.time() * 1000)
            rand = uuid.uuid4().hex[:16]
            return f"ML{ts:013d}{rand}".upper()[:26]

    def _reinject_email(self, email_bytes: bytes, metadata: dict):
        """
        Reinject email into Postfix via SMTP to the post-tracking listener
        on 127.0.0.1:10026, which delivers WITHOUT re-filtering.

        This avoids the infinite loop that happens when using sendmail
        (which re-enters the default transport with content_filter).
        """
        import smtplib

        # Belt and braces for the opt-out header: the normal path strips it
        # from the parsed message, but the fallback paths hand this function
        # the ORIGINAL raw bytes, which may still carry it. Never let it
        # leave the server; _strip_opt_out_header fails open.
        email_bytes = _strip_opt_out_header(email_bytes)

        # Per-credential marketing stream, applied here so every exit path
        # (pass-through, no-HTML, full injection, even the failure fallback)
        # carries it. cleanup_stream on 10026 routes the header to the
        # marketing egress. The flag is per-message state set in process().
        if getattr(self, "_pending_stream_marketing", False):
            email_bytes = _insert_stream_header_raw(email_bytes)

        sender = metadata.get("sender", "") or getattr(self, "cli_sender", "") or ""

        # Deliver to the ENVELOPE recipients Postfix handed us (${recipient}
        # in master.cf), never to the ones parsed out of the headers.
        #
        # metadata["recipients"] is built from the To/Cc/Bcc *headers*, and a
        # Bcc header does not survive to the wire -- the sending client strips
        # it, which is what makes a blind copy blind. So header-derived
        # recipients are exactly To+Cc, and reinjecting to those silently
        # dropped every Bcc recipient while Cc worked fine. Worse, when
        # Postfix invokes this filter once per recipient, each invocation
        # would re-send to the whole To+Cc list.
        #
        # The envelope is the only authority on who a message is for; headers
        # are display metadata. metadata["recipients"] stays as the fallback
        # for the case where argv carried nothing.
        recipients = getattr(self, "cli_recipients", []) or metadata.get("recipients", []) or []

        if not recipients:
            logger.error("No recipients for reinjection — cannot deliver")
            sys.exit(75)

        logger.info(f"Reinjecting to 127.0.0.1:10026: sender={sender}, recipients={recipients}")

        try:
            smtp = smtplib.SMTP("127.0.0.1", 10026, timeout=30)
            smtp.sendmail(sender, recipients, email_bytes)
            smtp.quit()
            logger.info("Email reinjected successfully via 10026")
        except Exception as e:
            logger.error(f"SMTP reinjection to 10026 failed: {e}")
            sys.exit(75)  # EX_TEMPFAIL

    def _check_html_content(self, message: email.message.EmailMessage) -> bool:
        """
        Check if email contains HTML content worth processing.

        Args:
            message: Email message to check

        Returns:
            bool: True if email has HTML content that can be processed
        """
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    content = part.get_content()
                    if content and len(content.strip()) > 0:
                        return True
        else:
            if message.get_content_type() == "text/html":
                content = message.get_content()
                if content and len(content.strip()) > 0:
                    return True

        return False

    def _get_organization_from_database(self, domain: str) -> str:
        """
        Query database for organization ID associated with domain.

        In our architecture, Organization > Domain maintains a one-to-one relationship.
        We query the domains table to find the organization_id for the given domain.

        Args:
            domain: Domain name to look up

        Returns:
            str: Organization ID or domain name as fallback
        """
        # Use the existing database connection
        try:
            # Import PyMySQL with proper error handling
            try:
                import pymysql
            except ImportError:
                logger.error("PyMySQL not available, using default organization/domain IDs")
                return self.default_organization_id

            # Establish database connection with timeout
            db_connection = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset="utf8mb4",
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout,
            )
            try:
                cursor = db_connection.cursor()

                # Query for domain's organization_id - the domains table should contain organization_id
                # If organization_id column doesn't exist, we use the domain itself as organization
                query = """
                    SELECT COALESCE(organization_id, domain) as org_id
                    FROM domains 
                    WHERE domain = %s AND active = 1
                    LIMIT 1
                """

                cursor.execute(query, (domain,))
                result = cursor.fetchone()

                cursor.close()

                if result:
                    organization_id = result[0]
                    logger.debug(f"Found organization {organization_id} for domain {domain}")
                    return organization_id

            finally:
                db_connection.close()
        except Exception as e:
            logger.error(f"Failed to get organization for domain {domain}: {e}")

        # Fallback to using domain as organization ID
        logger.debug(f"Using domain {domain} as organization ID (fallback)")
        return domain

    def _check_delivery_timing(self, recipient_domain, organization_id):
        """Check if email should be sent now using the delivery optimizer.

        Mirrors how Postfix itself treats an unreachable milter
        (milter_default_action=tempfail, not accept): an unreachable/failing
        rate-limiter must never be silently treated as "no limits apply".
        We still don't hold the whole message hostage on one dependency
        blip -- that would turn a transient optimizer outage into a
        platform-wide outbound-mail outage, a worse failure mode -- so on
        error we pace down by a fixed conservative delay and send, instead
        of sending immediately at full, unthrottled speed.
        """
        try:
            response = requests.post(
                f"{self.delivery_optimizer_url}/check",
                json={"recipient_domain": recipient_domain, "organization_id": organization_id},
                timeout=self.delivery_optimizer_timeout,
            )
        except Exception as e:
            logger.error(
                f"Delivery optimizer unreachable for {recipient_domain} "
                f"(org={organization_id}): {e}. Pacing down "
                f"{self.delivery_optimizer_fallback_delay}s instead of sending unthrottled."
            )
            time.sleep(self.delivery_optimizer_fallback_delay)
            return True

        if response.status_code != 200:
            logger.error(
                f"Delivery optimizer check failed for {recipient_domain} "
                f"(org={organization_id}): HTTP {response.status_code}. Pacing down "
                f"{self.delivery_optimizer_fallback_delay}s instead of sending unthrottled."
            )
            time.sleep(self.delivery_optimizer_fallback_delay)
            return True

        result = response.json()
        should_send = result.get("send_now", True)

        # Real field is delay_ms (see delivery_optimizer's /check response), not
        # delay_seconds -- this previously never fired because of that mismatch.
        delay_ms = result.get("delay_ms")
        if should_send and delay_ms:
            time.sleep(delay_ms / 1000)

        return should_send

    def _record_delivery_attempt(self, recipient_domain, organization_id):
        """Record delivery attempt with optimizer"""
        try:
            requests.post(
                f"{self.delivery_optimizer_url}/record",
                json={"recipient_domain": recipient_domain, "organization_id": organization_id},
                timeout=self.delivery_optimizer_timeout,
            )
        except Exception as e:
            logger.error(f"Error recording delivery: {e}")

    def _extract_body_parts(self, message: email.message.EmailMessage) -> dict[str, str | None]:
        """
        Pull the rendered text/plain and text/html parts out of a message.

        Attachments are deliberately excluded -- both real attachments and
        inline parts (cid: images). This mirrors what Mailgun/Postmark/Brevo
        expose for a stored message, and keeps email_bodies from quietly
        becoming a second copy of every file anyone has ever mailed. A part is
        skipped whenever it declares any Content-Disposition with a filename,
        or an inline disposition, regardless of its content type.

        Returns {"html": ..., "text": ...}; either may be None.
        """
        html_parts: list[str] = []
        text_parts: list[str] = []

        try:
            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                disposition = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disposition or "inline" in disposition:
                    continue
                if part.get_filename():
                    continue

                ctype = part.get_content_type()
                if ctype not in ("text/plain", "text/html"):
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="replace")
                except Exception as exc:  # one unreadable part must not lose the rest
                    logger.warning(f"Could not decode {ctype} part: {exc}")
                    continue

                (html_parts if ctype == "text/html" else text_parts).append(decoded)
        except Exception as exc:
            logger.error(f"Body extraction failed: {exc}")
            return {"html": None, "text": None}

        def joined(parts: list[str]) -> str | None:
            if not parts:
                return None
            body = "\n".join(parts)
            if len(body) > self.body_capture_max_bytes:
                body = body[: self.body_capture_max_bytes] + "\n<!-- truncated by mailyte -->"
            return body

        return {"html": joined(html_parts), "text": joined(text_parts)}

    def _archive_message(self, raw_email: bytes, metadata: dict) -> None:
        """Hand the raw message to the archiver for durable S3 storage.

        Contract, from plans/06-operations/00-PRD-disaster-recovery.md §4 L2:
        every accepted message reaches the archiver within seconds, and a
        producer failure NEVER blocks or bounces mail. So every path here
        returns None, every exception is swallowed, and the timeout is short.

        This runs inside a Postfix pipe(8) transport. Two things follow from
        that and neither is optional:

        * Nothing may reach stderr. Postfix captures it, and at volume the
          interpreter can fail to flush stderr during shutdown and exit 120 --
          which Postfix reads as a delivery failure and bounces a message it
          has ALREADY delivered. That exact bug shipped once here; the module
          logger writes WARNING+ to stderr, so failures below are logged at
          INFO and go only to the log file.
        * It must be fast. A stalled archiver would otherwise add its timeout
          to every single delivery.
        """
        if not self.archive_enabled:
            return

        try:
            recipients = metadata.get("recipients") or []
            payload = {
                "message_id": metadata.get("email_id") or metadata.get("mailyte_id") or "",
                "organization_id": metadata.get("organization_id") or "unknown",
                "sender": (metadata.get("sender") or "")[:255],
                # email_archive is keyed (message_id, recipient); the first
                # recipient identifies the archived copy, and a fan-out to many
                # recipients is still one stored object, not one per envelope.
                "recipient": (recipients[0] if recipients else "")[:255],
                "subject": (metadata.get("subject") or "")[:998],
                "content": raw_email.decode("utf-8", errors="replace"),
            }
            if not payload["message_id"]:
                logger.info("Archive skipped: message has no usable id")
                return

            response = requests.post(
                f"{self.archive_service_url}/archive",
                json=payload,
                timeout=self.archive_timeout,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Postfix-Tracking-Injector/1.0",
                },
            )
            if response.status_code == 200:
                logger.info(
                    "Archived %s (%s)",
                    payload["message_id"],
                    response.json().get("storage_type", "?"),
                )
            else:
                # INFO, not ERROR: ERROR reaches stderr. See the docstring.
                logger.info(
                    "Archive rejected %s: HTTP %s %s",
                    payload["message_id"],
                    response.status_code,
                    response.text[:200],
                )
        except Exception as exc:  # noqa: BLE001 - mail flow must not depend on this
            logger.info("Archive call failed (mail unaffected): %s", exc)

    def _store_email_body(self, message: email.message.EmailMessage, metadata: dict) -> None:
        """
        Persist the rendered body so the Email Logs detail view can show it.

        This runs inside the Postfix pipe transport, so it is written to fail
        silently: a storage problem must never delay or block a delivery. Every
        path returns None and the caller ignores the result.

        Captured BEFORE tracking injection, so what is stored is the message as
        authored rather than one with rewritten links and a tracking pixel --
        that is what is useful to read back, and it also means a message with no
        HTML (which returns early, before injection) is captured on the same
        code path as one with.
        """
        if not self.body_capture_enabled:
            return

        try:
            body = self._extract_body_parts(message)
            if body["html"] is None and body["text"] is None:
                return  # nothing renderable; don't write an empty row

            try:
                import pymysql
            except ImportError:
                logger.warning("PyMySQL unavailable, skipping body capture")
                return

            expires_at = datetime.now() + timedelta(days=self.body_retention_days)
            row_id = (
                hashlib.sha256(
                    f"{metadata.get('email_id', '')}|{metadata.get('sender', '')}".encode()
                )
                .hexdigest()[:26]
                .upper()
            )

            conn = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name,
                charset="utf8mb4",
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout,
            )
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO email_bodies
                        (id, message_id, organization_id, sender, subject,
                         html, text, has_attachments, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        html = VALUES(html),
                        text = VALUES(text),
                        subject = VALUES(subject),
                        expires_at = VALUES(expires_at)
                    """,
                    (
                        row_id,
                        metadata.get("email_id", "")[:255],
                        metadata.get("organization_id"),
                        metadata.get("sender", "")[:255],
                        metadata.get("subject", ""),
                        body["html"],
                        body["text"],
                        1 if metadata.get("has_attachments") else 0,
                        expires_at,
                    ),
                )
                conn.commit()
                cursor.close()
                logger.info(
                    "Captured body for %s (html=%s, text=%s)",
                    metadata.get("email_id"),
                    len(body["html"]) if body["html"] else 0,
                    len(body["text"]) if body["text"] else 0,
                )
            finally:
                conn.close()
        except Exception as exc:
            # Never propagate -- this is bookkeeping attached to a delivery.
            logger.error(f"Body capture failed (mail unaffected): {exc}")

    def _send_webhook_notification(self, event_data):
        """Send webhook notification asynchronously"""
        try:
            webhook_url = os.getenv("TRACKING_WEBHOOK_URL")
            if not webhook_url:
                return

            webhook_secret = os.getenv("WEBHOOK_SECRET", "")

            payload = json.dumps(event_data)
            signature = self._generate_webhook_signature(payload, webhook_secret)

            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "User-Agent": "EmailTracker/1.0",
            }

            timeout = int(os.getenv("TRACKING_WEBHOOK_TIMEOUT", "10"))

            response = requests.post(webhook_url, data=payload, headers=headers, timeout=timeout)

            if response.status_code == 200:
                logger.debug("Webhook sent successfully for tracking event")
            else:
                logger.warning(f"Webhook failed with status: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")


def _parse_cli_args(args: list[str]) -> tuple[str, str | None, list[str]]:
    """Parse the pipe(8) argv: -f ${sender} [-a ${sasl_username}] -- rcpt...

    master.cf passes `-f ${sender} -a ${sasl_username} -- ${recipient}`, but
    pipe(8) REMOVES the ${sasl_username} argument entirely when the message
    was not SASL-authenticated (port 25, reinjected mail), so argv can
    arrive as `-f sender -a -- rcpt` or without -a at all. The token after
    -a is therefore the username only when it exists, is non-empty, is not
    `--`, and does not itself look like a flag; anything else means "no
    username" (None), never a parse failure -- swallowing `--` here would
    lose every recipient. Fail open by contract: any error returns whatever
    was parsed so far, defaults for the rest.

    Returns:
        tuple: (cli_sender, cli_sasl_username or None, cli_recipients)
    """
    cli_sender = ""
    cli_sasl_username = None
    cli_recipients = []
    try:
        if "-f" in args:
            idx = args.index("-f")
            if idx + 1 < len(args):
                cli_sender = args[idx + 1]
        if "-a" in args:
            idx = args.index("-a")
            if idx + 1 < len(args):
                candidate = args[idx + 1]
                if candidate and candidate != "--" and not candidate.startswith("-"):
                    cli_sasl_username = candidate
        if "--" in args:
            idx = args.index("--")
            cli_recipients = args[idx + 1 :]
    except Exception:
        pass
    return cli_sender, cli_sasl_username, cli_recipients


def main():
    """
    Main entry point for the Postfix tracking injector.

    This function handles the top-level execution flow and ensures
    proper error handling and logging for integration with Postfix.
    """
    try:
        logger.info("Starting Postfix Email Tracking Injector v1.0")
        logger.info(f"Process ID: {os.getpid()}")
        logger.info(f"Command line: {' '.join(sys.argv)}")

        # Parse command-line args -- see _parse_cli_args for the pipe(8)
        # empty-${sasl_username} removal gotcha.
        cli_sender, cli_sasl_username, cli_recipients = _parse_cli_args(sys.argv[1:])

        logger.info(
            f"CLI sender: {cli_sender}, sasl_username: {cli_sasl_username or '(none)'}, "
            f"recipients: {cli_recipients}"
        )

        # Create and run the tracking injector
        injector = PostfixTrackingInjector()
        injector.cli_sender = cli_sender
        injector.cli_sasl_username = cli_sasl_username
        injector.cli_recipients = cli_recipients
        injector.process_email()

        logger.info("Postfix tracking injection completed successfully")

    except KeyboardInterrupt:
        logger.warning("Tracking injector interrupted by user")
        sys.exit(1)
    except SystemExit:
        # Re-raise SystemExit to preserve exit codes
        raise
    except Exception as e:
        logger.error(f"Fatal error in tracking injector main: {e}", exc_info=True)
        sys.exit(75)  # EX_TEMPFAIL


if __name__ == "__main__":
    main()
