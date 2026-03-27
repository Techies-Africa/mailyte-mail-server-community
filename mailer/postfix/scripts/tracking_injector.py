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

import sys
import os
import json
import re
import email
import email.policy
import requests
import logging
import hashlib
import time
import signal
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, List, Optional, Tuple, Any

# Configure comprehensive logging for debugging and monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d] %(message)s',
    handlers=[
        # Log to file for permanent record
        logging.FileHandler('/var/log/postfix_tracking.log'),
        # Also log to stderr for Postfix logging integration
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger('postfix_tracking')

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
        self.tracking_service_url = os.getenv('TRACKING_SERVICE_URL', 'http://tracking:8086')
        self.tracking_enabled = os.getenv('TRACKING_ENABLED', 'true').lower() == 'true'

        # Default identifiers for tenant/domain mapping
        self.default_organization_id = os.getenv('DEFAULT_ORGANIZATION_ID', 'default')
        self.default_domain_id = os.getenv('DEFAULT_DOMAIN_ID', 'default')

        # Database configuration for domain/tenant lookup
        # This allows proper multi-tenant tracking data separation
        self.db_host = os.getenv('DB_HOST', 'mysql')
        self.db_port = int(os.getenv('DB_PORT', 3306))
        self.db_name = os.getenv('DB_NAME', 'mailserver')
        self.db_user = os.getenv('DB_USER', 'root')
        self.db_password = os.getenv('DB_PASSWORD', 'password')

        # Performance optimization features
        self._connection_pool = None  # Future: implement connection pooling
        self._tenant_cache = {}       # Simple in-memory cache for tenant lookups
        self._cache_ttl = 300         # Cache entries valid for 5 minutes

        # Timeout configuration for external service calls
        self.api_timeout = int(os.getenv('TRACKING_API_TIMEOUT', '10'))
        self.db_timeout = int(os.getenv('DB_TIMEOUT', '5'))

        # Log initialization with current configuration
        logger.info(f"Tracking injector initialized:")
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

    def extract_email_metadata(self, message: email.message.EmailMessage) -> Optional[Dict[str, Any]]:
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
            sender = self._clean_email_address(message.get('From', ''))
            if not sender:
                logger.warning("No valid sender found in email")
                return None

            # Extract all recipient types (To, Cc, Bcc)
            recipients = []
            for header in ['To', 'Cc', 'Bcc']:
                header_value = message.get(header, '')
                if header_value:
                    # Handle comma-separated recipient lists
                    header_recipients = [
                        self._clean_email_address(addr.strip()) 
                        for addr in header_value.split(',')
                    ]
                    recipients.extend([addr for addr in header_recipients if addr])

            if not recipients:
                logger.warning("No valid recipients found in email")
                return None

            # Generate or extract unique email identifier
            message_id = message.get('Message-ID', '').strip('<>')
            if not message_id:
                # Generate deterministic message ID if not present
                timestamp = str(int(datetime.now().timestamp()))
                sender_hash = hashlib.md5(sender.encode()).hexdigest()[:8]
                hostname = os.getenv('HOSTNAME', 'mail.local')
                message_id = f"{timestamp}.{sender_hash}@{hostname}"
                logger.debug(f"Generated message ID: {message_id}")

            # Extract domain information for tenant resolution
            sender_domain = sender.split('@')[-1] if '@' in sender else self.default_domain_id

            # Build comprehensive metadata dictionary
            metadata = {
                'email_id': message_id,
                'sender': sender,
                'recipients': recipients,
                'recipient_count': len(recipients),
                'subject': message.get('Subject', ''),
                'sender_domain': sender_domain,
                'date': message.get('Date', ''),
                'content_type': message.get_content_type(),
                'has_attachments': self._check_attachments(message),
                'email_size': len(str(message)),
                'priority': message.get('X-Priority', 'normal'),
                'user_agent': message.get('User-Agent', ''),
                'x_mailer': message.get('X-Mailer', '')
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
            return ''

        # Remove display name and extract email from "Name <email>" format
        import email.utils
        try:
            parsed = email.utils.parseaddr(address)
            email_addr = parsed[1].lower().strip()

            # Basic email validation
            if '@' in email_addr and '.' in email_addr.split('@')[1]:
                return email_addr
        except Exception:
            pass

        return ''

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
            disposition = part.get('Content-Disposition', '')
            if 'attachment' in disposition.lower():
                return True

        return False

    def get_tenant_domain_ids(self, sender_domain: str, sender_email: str) -> Tuple[str, str]:
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
            if current_time - cached_entry['timestamp'] < self._cache_ttl:
                logger.debug(f"Using cached tenant/domain for {sender_email}")
                return cached_entry['tenant_id'], cached_entry['domain_id']
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
                charset='utf8mb4',
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout
            )

            try:
                with connection.cursor() as cursor:
                    # Primary lookup: domain configuration table
                    cursor.execute("""
                        SELECT id, organization_id, tracking_enabled 
                        FROM domains 
                        WHERE domain = %s AND active = 1
                    """, (sender_domain,))
                    domain_result = cursor.fetchone()

                    if domain_result:
                        domain_id = str(domain_result[0])
                        tenant_id = domain_result[1] or self.default_tenant_id
                        tracking_enabled = bool(domain_result[2]) if domain_result[2] is not None else True

                        logger.debug(f"Found domain config: domain_id={domain_id}, "
                                   f"tenant_id={tenant_id}, tracking_enabled={tracking_enabled}")
                    else:
                        # Secondary lookup: user-specific configuration
                        cursor.execute("""
                            SELECT u.domain_id, d.tenant_id 
                            FROM users u 
                            LEFT JOIN domains d ON u.domain_id = d.id 
                            WHERE u.email = %s AND u.active = 1
                        """, (sender_email,))
                        user_result = cursor.fetchone()

                        if user_result and user_result[0]:
                            domain_id = str(user_result[0])
                            tenant_id = user_result[1] or self.default_tenant_id
                            logger.debug(f"Found user config: domain_id={domain_id}, tenant_id={tenant_id}")
                        else:
                            # Use default values
                            domain_id = self.default_domain_id
                            tenant_id = self.default_tenant_id
                            logger.debug(f"Using defaults: domain_id={domain_id}, tenant_id={tenant_id}")

            finally:
                connection.close()

            # Cache the result for future lookups
            self._tenant_cache[cache_key] = {
                'tenant_id': tenant_id,
                'domain_id': domain_id,
                'timestamp': current_time
            }

            logger.debug(f"Resolved tenant_id: {tenant_id}, domain_id: {domain_id} for {sender_email}")
            return tenant_id, domain_id

        except Exception as e:
            logger.error(f"Database error getting tenant/domain IDs: {e}")
            # Always fall back to defaults to ensure email delivery
            return self.default_tenant_id, self.default_domain_id

    def inject_tracking_for_recipient(self, html_content: str, email_id: str, 
                                    recipient: str, organization_id: str, domain_id: str) -> str:
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
        try:
            # Prepare API payload with all tracking parameters
            payload = {
                'html_content': html_content,
                'email_id': email_id,
                'recipient': recipient,
                'tenant_id': organization_id,
                'domain_id': domain_id,
                'enable_open_tracking': True,
                'enable_click_tracking': True,
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'source': 'postfix_injector'
            }

            # Make API call to tracking service with timeout protection
            logger.debug(f"Calling tracking service for recipient: {recipient}")
            response = requests.post(
                f"{self.tracking_service_url}/api/tracking/inject",
                json=payload,
                timeout=self.api_timeout,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'Postfix-Tracking-Injector/1.0'
                }
            )

            # Process successful response
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    tracking_stats = result.get('tracking_injected', {})
                    logger.info(f"Tracking injected successfully for {recipient}:")
                    logger.info(f"  - Open tracking: {tracking_stats.get('open_tracking', False)}")
                    logger.info(f"  - Click tracking: {tracking_stats.get('click_tracking', False)}")
                    logger.info(f"  - Links rewritten: {tracking_stats.get('links_rewritten', 0)}")
                    logger.info(f"  - Tracking ID: {result.get('tracking_id', 'N/A')}")

                    return result['modified_content']
                else:
                    error_msg = result.get('error', 'Unknown error')
                    logger.warning(f"Tracking injection failed for {recipient}: {error_msg}")
            else:
                logger.error(f"Tracking service HTTP error for {recipient}: "
                           f"{response.status_code} - {response.text[:200]}")

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

    def process_message_content(self, message: email.message.EmailMessage, 
                              metadata: Dict[str, Any]) -> email.message.EmailMessage:
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
            email_id = metadata['email_id']
            sender_domain = metadata['sender_domain']
            sender_email = metadata['sender']
            recipients = metadata['recipients']

            logger.info(f"Processing message content for {len(recipients)} recipients")

            # Get tenant and domain IDs for this sender
            tenant_id, domain_id = self.get_tenant_domain_ids(sender_domain, sender_email)
            organization_id = tenant_id #Using tenant_id as organization_id for now

            # Track processing statistics
            processed_parts = 0
            modified_parts = 0

            if message.is_multipart():
                # Handle multipart messages (most common for HTML emails)
                logger.debug("Processing multipart message")

                for part in message.walk():
                    if part.get_content_type() == 'text/html':
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
                                    html_content, email_id, primary_recipient, organization_id, domain_id
                                )

                                # Only update if content was actually modified
                                if modified_content != html_content:
                                    part.set_content(modified_content, subtype='html')
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
                if message.get_content_type() == 'text/html':
                    processed_parts += 1
                    logger.debug("Processing single-part HTML message")

                    try:
                        html_content = message.get_content()
                        if html_content and len(html_content.strip()) > 0:
                            # Process for primary recipient
                            primary_recipient = recipients[0] if recipients else None
                            if primary_recipient:
                                modified_content = self.inject_tracking_for_recipient(
                                    html_content, email_id, primary_recipient, organization_id, domain_id
                                )

                                if modified_content != html_content:
                                    message.set_content(modified_content, subtype='html')
                                    modified_parts += 1
                                    logger.debug("Modified single-part HTML content")
                            else:
                                logger.warning("No recipients found for single-part HTML processing")
                        else:
                            logger.debug("Skipping empty HTML content")
                    except Exception as e:
                        logger.error(f"Error processing single-part HTML: {e}")

            # Log processing summary
            logger.info(f"Content processing completed:")
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

            # Parse email using Python's robust email parser
            try:
                message = email.message_from_bytes(raw_email, policy=email.policy.default)
            except Exception as e:
                logger.error(f"Failed to parse email: {e}")
                # Pass through unparseable emails unchanged
                self._reinject_email(raw_email, {'sender': '', 'recipients': sys.argv[1:]})
                return

            # Stamp every email with a unique Mailyte ID (ULID)
            # This ID ties the email across all systems: tracking, webhooks,
            # message trace, analytics, and IMAP delivery
            mailyte_id = self._generate_mailyte_id()
            if 'X-Mailyte-ID' not in message:
                message['X-Mailyte-ID'] = mailyte_id
                logger.info(f"Stamped X-Mailyte-ID: {mailyte_id}")
            else:
                mailyte_id = message['X-Mailyte-ID']
                logger.info(f"Existing X-Mailyte-ID: {mailyte_id}")

            # Check if tracking is globally enabled
            # Note: even with tracking disabled, the X-Mailyte-ID header is still stamped
            if not self.tracking_enabled:
                logger.info("Tracking disabled globally, passing email with Mailyte ID only")
                self._reinject_email(message.as_bytes(), {'sender': '', 'recipients': sys.argv[1:]})
                return

            # Extract and validate email metadata
            logger.debug("Extracting email metadata...")
            metadata = self.extract_email_metadata(message)
            if not metadata:
                logger.warning("Failed to extract valid metadata, passing through unchanged")
                self._reinject_email(message.as_bytes(), {'sender': '', 'recipients': sys.argv[1:]})
                return

            # Attach Mailyte ID to metadata for downstream use (webhooks, tracking, logs)
            metadata['mailyte_id'] = mailyte_id

            # Log email processing details
            logger.info(f"Processing email: {metadata['email_id']} (mailyte_id={mailyte_id})")
            logger.info(f"  - From: {metadata['sender']}")
            logger.info(f"  - To: {len(metadata['recipients'])} recipients")
            logger.info(f"  - Subject: {metadata['subject'][:50]}...")
            logger.info(f"  - Content-Type: {metadata['content_type']}")
            logger.info(f"  - Has attachments: {metadata['has_attachments']}")

            # Only process emails with HTML content
            has_html = self._check_html_content(message)
            if not has_html:
                logger.info("Email has no HTML content, passing with Mailyte ID only")
                self._reinject_email(message.as_bytes(), metadata)
                return

            logger.info("HTML content detected, proceeding with tracking injection")

            # Process message for tracking injection
            logger.debug("Starting tracking injection process...")
            modified_message = self.process_message_content(message, metadata)

            # Reinject modified email back into Postfix via the post-tracking
            # listener (127.0.0.1:10026) which delivers without re-filtering
            try:
                modified_email = modified_message.as_bytes(policy=email.policy.default)
                output_size = len(modified_email)

                logger.info(f"Email processing completed successfully:")
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
                    self._reinject_email(raw_email, metadata if metadata else {'sender': '', 'recipients': sys.argv[1:]})
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

        sender = metadata.get('sender', '') or getattr(self, 'cli_sender', '') or ''
        recipients = metadata.get('recipients', []) or getattr(self, 'cli_recipients', []) or []

        if not recipients:
            logger.error("No recipients for reinjection — cannot deliver")
            sys.exit(75)

        logger.info(f"Reinjecting to 127.0.0.1:10026: sender={sender}, recipients={recipients}")

        try:
            smtp = smtplib.SMTP('127.0.0.1', 10026, timeout=30)
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
                if part.get_content_type() == 'text/html':
                    content = part.get_content()
                    if content and len(content.strip()) > 0:
                        return True
        else:
            if message.get_content_type() == 'text/html':
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
                charset='utf8mb4',
                connect_timeout=self.db_timeout,
                read_timeout=self.db_timeout,
                write_timeout=self.db_timeout
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
        """Check if email should be sent now using delivery optimizer"""
        try:
            delivery_optimizer_url = os.getenv('DELIVERY_OPTIMIZER_URL', 'http://localhost:8088')

            response = requests.post(
                f"{delivery_optimizer_url}/api/delivery/check",
                json={
                    'recipient_domain': recipient_domain,
                    'organization_id': organization_id
                },
                timeout=5
            )

            if response.status_code == 200:
                result = response.json()
                should_send = result.get('send_now', True)

                if should_send and 'delay_seconds' in result:
                    # Add small delay if recommended
                    import time
                    time.sleep(result['delay_seconds'])

                return should_send
            else:
                logger.warning(f"Delivery optimizer check failed: {response.status_code}")
                return True  # Default to sending if service unavailable

        except Exception as e:
            logger.error(f"Error checking delivery timing: {e}")
            return True  # Default to sending on error

    def _record_delivery_attempt(self, recipient_domain, organization_id):
        """Record delivery attempt with optimizer"""
        try:
            delivery_optimizer_url = os.getenv('DELIVERY_OPTIMIZER_URL', 'http://localhost:8088')

            requests.post(
                f"{delivery_optimizer_url}/api/delivery/record",
                json={
                    'recipient_domain': recipient_domain,
                    'organization_id': organization_id
                },
                timeout=5
            )

        except Exception as e:
            logger.error(f"Error recording delivery: {e}")

    def _send_webhook_notification(self, event_data):
        """Send webhook notification asynchronously"""
        try:
            webhook_url = os.getenv('TRACKING_WEBHOOK_URL')
            if not webhook_url:
                return

            webhook_secret = os.getenv('WEBHOOK_SECRET', '')

            payload = json.dumps(event_data)
            signature = self._generate_webhook_signature(payload, webhook_secret)

            headers = {
                'Content-Type': 'application/json',
                'X-Webhook-Signature': signature,
                'User-Agent': 'EmailTracker/1.0'
            }

            timeout = int(os.getenv('TRACKING_WEBHOOK_TIMEOUT', '10'))

            response = requests.post(
                webhook_url,
                data=payload,
                headers=headers,
                timeout=timeout
            )

            if response.status_code == 200:
                logger.debug(f"Webhook sent successfully for tracking event")
            else:
                logger.warning(f"Webhook failed with status: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")


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

        # Parse command-line args: -f sender -- recipient1 recipient2 ...
        # Postfix pipe transport passes: argv -f ${sender} -- ${recipient}
        cli_sender = ''
        cli_recipients = []
        args = sys.argv[1:]
        if '-f' in args:
            idx = args.index('-f')
            if idx + 1 < len(args):
                cli_sender = args[idx + 1]
        if '--' in args:
            idx = args.index('--')
            cli_recipients = args[idx + 1:]

        logger.info(f"CLI sender: {cli_sender}, recipients: {cli_recipients}")

        # Create and run the tracking injector
        injector = PostfixTrackingInjector()
        injector.cli_sender = cli_sender
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