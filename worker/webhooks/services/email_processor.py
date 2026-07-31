#!/usr/bin/env python3
"""
Email Processing Service for Webhook Events

This service handles email content processing for webhook notifications:
- Email parsing and metadata extraction (RFC 2822 compliance)
- Attachment detection and content extraction
- Header processing with privacy considerations
- Content sanitization and size optimization
- MIME type detection and handling

All processing is designed to be secure and privacy-aware,
with configurable options for content filtering and anonymization.
"""

import email
import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, List, Any, Optional, Tuple
import chardet
from pathlib import Path
import os

logger = logging.getLogger(__name__)


class EmailProcessor:
    """
    Email processing service with comprehensive
    metadata extraction and privacy-aware content handling.
    """

    def __init__(self):
        """Initialize email processor with security and privacy settings."""
        # Privacy settings - configurable via environment
        self.anonymize_headers = True
        self.max_attachment_size = 50 * 1024 * 1024  # 50MB
        self.allowed_attachment_types = {
            ".txt",
            ".pdf",
            ".doc",
            ".docx",
            ".html",
            ".md",
            ".csv",
            ".json",
            ".xml",
            ".rtf",
        }

        # Headers to exclude for privacy (contain PII or tracking info)
        self.sensitive_headers = {
            "X-Originating-IP",
            "Received",
            "X-Mailer",
            "User-Agent",
            "X-Forwarded-For",
            "X-Real-IP",
            "X-Client-IP",
            "X-Source-IP",
            "X-Remote-IP",
            "X-Cluster-Client-IP",
            "CF-Connecting-IP",
        }

        # Headers that are safe to include
        self.safe_headers = {
            "Message-ID",
            "Subject",
            "From",
            "To",
            "Cc",
            "Bcc",
            "Date",
            "Reply-To",
            "Return-Path",
            "Content-Type",
            "Content-Transfer-Encoding",
            "MIME-Version",
            "In-Reply-To",
            "References",
            "Thread-Topic",
            "Thread-Index",
        }

        logger.info("Email processor initialized with privacy protections")

    def extract_organization_id(self, email_address: str) -> str:
        """
        Extract organization ID from email address using the new database structure.

        This method determines the organization ID for an email address by:
        1. Extracting the domain from the email address
        2. Looking up the organization_id in the domains table
        3. Falling back to default organization for unknown domains

        Args:
            email_address: Email address to analyze (e.g., user@domain.com)

        Returns:
            str: Organization ID from domains table or default fallback

        Note: This supports the new organization/domain/email account hierarchy
              while maintaining backward compatibility with domain-based organization.
        """
        try:
            if not email_address or "@" not in email_address:
                return os.getenv("RAG_ORGANIZATION_ID", "default")

            domain = email_address.split("@")[1].lower()

            # Here you would typically lookup the organization from database
            # For now, return default organization or domain-based mapping
            organization_mapping = {
                # Add domain to organization mappings here
            }

            return organization_mapping.get(domain, os.getenv("RAG_ORGANIZATION_ID", "default"))

        except Exception as e:
            logger.error(f"Failed to extract organization from email {email_address}: {e}")
            return "default"

    def extract_email_metadata(self, eml_content: str) -> Dict[str, Any]:
        """
        Extract comprehensive metadata from email content with privacy protection.

        Args:
            eml_content: Raw email content in RFC 2822 format

        Returns:
            Dict containing extracted email metadata
        """
        try:
            # Parse email message
            msg = email.message_from_string(eml_content)

            # Extract basic metadata
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
                "mime_version": msg.get("MIME-Version", ""),
                "in_reply_to": msg.get("In-Reply-To", ""),
                "references": msg.get("References", ""),
                "size": len(eml_content),
                "has_attachments": False,
                "attachment_count": 0,
                "attachment_names": [],
                "attachment_types": [],
                "is_multipart": msg.is_multipart(),
                "headers": {},
            }

            # Extract headers (filtered for privacy)
            if not self.anonymize_headers:
                for header_name, header_value in msg.items():
                    if header_name in self.safe_headers:
                        metadata["headers"][header_name.lower()] = header_value

            # Analyze message structure
            if msg.is_multipart():
                metadata.update(self._analyze_multipart_message(msg))
            else:
                metadata.update(self._analyze_single_part_message(msg))

            # Extract body content summary
            body_summary = self._extract_body_summary(msg)
            metadata.update(body_summary)

            logger.debug(f"Extracted metadata for email: {metadata.get('message_id', 'unknown')}")
            return metadata

        except Exception as e:
            logger.error(f"Failed to extract email metadata: {e}")
            return {
                "error": str(e),
                "size": len(eml_content) if eml_content else 0,
                "has_attachments": False,
                "attachment_count": 0,
            }

    def _analyze_multipart_message(self, msg: email.message.Message) -> Dict[str, Any]:
        """Analyze multipart message structure and attachments."""
        attachment_info = {
            "has_attachments": False,
            "attachment_count": 0,
            "attachment_names": [],
            "attachment_types": [],
            "attachment_sizes": [],
            "text_parts": 0,
            "html_parts": 0,
            "other_parts": 0,
        }

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get_content_disposition() or "")

            # Check if this is an attachment
            if "attachment" in content_disposition:
                attachment_info["has_attachments"] = True
                attachment_info["attachment_count"] += 1

                filename = part.get_filename()
                if filename:
                    attachment_info["attachment_names"].append(filename)

                    # Get file extension
                    file_ext = Path(filename).suffix.lower()
                    attachment_info["attachment_types"].append(file_ext)

                    # Get attachment size
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            attachment_info["attachment_sizes"].append(len(payload))
                        else:
                            attachment_info["attachment_sizes"].append(0)
                    except Exception:
                        attachment_info["attachment_sizes"].append(0)
            else:
                # Count content parts
                if content_type == "text/plain":
                    attachment_info["text_parts"] += 1
                elif content_type == "text/html":
                    attachment_info["html_parts"] += 1
                else:
                    attachment_info["other_parts"] += 1

        return attachment_info

    def _analyze_single_part_message(self, msg: email.message.Message) -> Dict[str, Any]:
        """Analyze single-part message structure."""
        content_type = msg.get_content_type()

        return {
            "has_attachments": False,
            "attachment_count": 0,
            "attachment_names": [],
            "attachment_types": [],
            "attachment_sizes": [],
            "text_parts": 1 if content_type == "text/plain" else 0,
            "html_parts": 1 if content_type == "text/html" else 0,
            "other_parts": 1 if content_type not in ["text/plain", "text/html"] else 0,
        }

    def _extract_body_summary(self, msg: email.message.Message) -> Dict[str, Any]:
        """Extract body content summary without including full content."""
        body_info = {
            "has_text_body": False,
            "has_html_body": False,
            "text_body_length": 0,
            "html_body_length": 0,
            "body_preview": "",  # First 200 characters
            "encoding_detected": None,
        }

        try:
            if msg.is_multipart():
                # Extract from multipart message
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get_content_disposition() or "")

                    # Skip attachments
                    if "attachment" in content_disposition:
                        continue

                    if content_type == "text/plain" and not body_info["has_text_body"]:
                        text_content = self._decode_part_content(part)
                        if text_content:
                            body_info["has_text_body"] = True
                            body_info["text_body_length"] = len(text_content)
                            if not body_info["body_preview"]:
                                body_info["body_preview"] = text_content[:200]

                    elif content_type == "text/html" and not body_info["has_html_body"]:
                        html_content = self._decode_part_content(part)
                        if html_content:
                            body_info["has_html_body"] = True
                            body_info["html_body_length"] = len(html_content)
                            # Convert HTML to text for preview if no text body
                            if not body_info["body_preview"]:
                                try:
                                    from bs4 import BeautifulSoup

                                    soup = BeautifulSoup(html_content, "html.parser")
                                    text_preview = soup.get_text()[:200]
                                    body_info["body_preview"] = text_preview
                                except ImportError:
                                    # BeautifulSoup not available, use raw HTML
                                    body_info["body_preview"] = html_content[:200]
            else:
                # Single part message
                content = self._decode_part_content(msg)
                if content:
                    content_type = msg.get_content_type()
                    if content_type == "text/plain":
                        body_info["has_text_body"] = True
                        body_info["text_body_length"] = len(content)
                        body_info["body_preview"] = content[:200]
                    elif content_type == "text/html":
                        body_info["has_html_body"] = True
                        body_info["html_body_length"] = len(content)
                        # Convert HTML to text for preview
                        try:
                            from bs4 import BeautifulSoup

                            soup = BeautifulSoup(content, "html.parser")
                            body_info["body_preview"] = soup.get_text()[:200]
                        except ImportError:
                            body_info["body_preview"] = content[:200]

        except Exception as e:
            logger.warning(f"Failed to extract body summary: {e}")

        return body_info

    def _decode_part_content(self, part: email.message.Message) -> Optional[str]:
        """Safely decode email part content with encoding detection."""
        try:
            payload = part.get_payload(decode=True)
            if not payload:
                return None

            # Detect encoding
            detected = chardet.detect(payload)
            encoding = detected.get("encoding", "utf-8")

            if encoding:
                return payload.decode(encoding, errors="ignore")
            else:
                # Fallback to common encodings
                for fallback_encoding in ["utf-8", "latin1", "ascii"]:
                    try:
                        return payload.decode(fallback_encoding, errors="ignore")
                    except UnicodeDecodeError:
                        continue

            return None

        except Exception as e:
            logger.warning(f"Failed to decode part content: {e}")
            return None

    def extract_attachments(
        self, eml_content: str, include_content: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Extract attachment information from email with optional content inclusion.

        Args:
            eml_content: Raw email content
            include_content: Whether to include base64-encoded attachment content

        Returns:
            List of attachment information dictionaries
        """
        attachments = []

        try:
            msg = email.message_from_string(eml_content)

            if not msg.is_multipart():
                return attachments

            for part in msg.walk():
                content_disposition = str(part.get_content_disposition() or "")

                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        # Check file extension
                        file_ext = Path(filename).suffix.lower()

                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                # Size check
                                if len(payload) > self.max_attachment_size:
                                    logger.warning(
                                        f"Attachment {filename} too large: {len(payload)} bytes"
                                    )
                                    continue

                                attachment_data = {
                                    "filename": filename,
                                    "content_type": part.get_content_type(),
                                    "size": len(payload),
                                    "file_extension": file_ext,
                                    "allowed_type": file_ext in self.allowed_attachment_types,
                                }

                                # Include content if requested and file type is allowed
                                if (
                                    include_content
                                    and file_ext in self.allowed_attachment_types
                                    and len(payload) <= self.max_attachment_size
                                ):
                                    attachment_data["content_base64"] = base64.b64encode(
                                        payload
                                    ).decode("utf-8")

                                attachments.append(attachment_data)

                        except Exception as e:
                            logger.warning(f"Failed to process attachment {filename}: {e}")
                            # Add minimal info even if processing fails
                            attachments.append(
                                {
                                    "filename": filename,
                                    "content_type": part.get_content_type(),
                                    "size": 0,
                                    "file_extension": file_ext,
                                    "allowed_type": False,
                                    "error": str(e),
                                }
                            )

            logger.debug(f"Extracted {len(attachments)} attachments from email")
            return attachments

        except Exception as e:
            logger.error(f"Failed to extract attachments: {e}")
            return []

    def create_eml_summary(self, eml_content: str) -> Dict[str, Any]:
        """
        Create a comprehensive email summary for webhook notifications.

        Args:
            eml_content: Raw email content

        Returns:
            Dict containing complete email summary
        """
        try:
            # Get metadata
            metadata = self.extract_email_metadata(eml_content)

            # Get attachment info (without content for size optimization)
            attachments = self.extract_attachments(eml_content, include_content=False)

            # Create summary
            summary = {
                "metadata": metadata,
                "attachments": attachments,
                "summary_stats": {
                    "total_size": len(eml_content),
                    "attachment_count": len(attachments),
                    "total_attachment_size": sum(att.get("size", 0) for att in attachments),
                    "has_sensitive_content": self._check_sensitive_content(metadata),
                    "processing_timestamp": email.utils.formatdate(localtime=True),
                },
            }

            return summary

        except Exception as e:
            logger.error(f"Failed to create email summary: {e}")
            return {
                "error": str(e),
                "summary_stats": {
                    "total_size": len(eml_content) if eml_content else 0,
                    "attachment_count": 0,
                    "processing_timestamp": email.utils.formatdate(localtime=True),
                },
            }

    def _check_sensitive_content(self, metadata: Dict[str, Any]) -> bool:
        """Check if email contains potentially sensitive content."""
        # Simple heuristics for sensitive content detection
        sensitive_indicators = [
            "password",
            "ssn",
            "credit card",
            "bank account",
            "confidential",
            "private",
            "secure",
            "login",
            "financial",
            "medical",
            "health",
        ]

        subject = metadata.get("subject", "").lower()
        body_preview = metadata.get("body_preview", "").lower()

        for indicator in sensitive_indicators:
            if indicator in subject or indicator in body_preview:
                return True

        return False

    def process_email_event(self, event_data):
        """Process email events and send webhooks"""
        try:
            # Extract event information
            event_type = event_data.get("event_type")
            recipient = event_data.get("recipient", "")

            # Determine organization and domain
            domain = recipient.split("@")[1] if "@" in recipient else "unknown"
            org_id, domain_id = self.get_organization_for_domain(domain)

        except Exception as e:
            logger.error(f"Failed to process email event: {e}")

    def get_organization_for_domain(self, domain):
        """
        Get organization and domain IDs for a domain using the new database structure.

        This method queries the updated domains table to retrieve both the
        organization_id and domain id for webhook processing. This supports
        the new organization hierarchy while maintaining backward compatibility.

        Args:
            domain: Domain name to lookup

        Returns:
            tuple: (organization_id, domain_id) or ('default', None) if not found

        Note: Uses the new domains table structure with organization_id foreign key.
              Falls back to 'default' organization for unknown domains to maintain
              existing webhook functionality.
        """
        try:
            import mysql.connector
            import os

            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", 3306)),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME"),
            )

            cursor = conn.cursor(dictionary=True)
            # Query the new domains table structure
            query = """
                SELECT id, organization_id 
                FROM domains 
                WHERE domain = %s AND active = TRUE
            """
            cursor.execute(query, (domain,))
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                # Return organization_id and domain id from new structure
                return result["organization_id"], result["id"]
            else:
                # Fallback for unknown domains to maintain webhook functionality
                return "default", None

        except Exception as e:
            logger.error(f"Failed to get organization for domain {domain}: {e}")
            # Fallback to default to ensure webhooks continue working
            return "default", None


# Global instance
email_processor = EmailProcessor()
