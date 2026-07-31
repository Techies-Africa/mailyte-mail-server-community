#!/usr/bin/env python3
"""
Postfix Webhook Sender

Called by the Postfix webhook-filter pipe transport to:
1. Parse the incoming email from stdin
2. Fire an HTTP webhook to the webhooks service with email metadata
3. Reinject the email into Postfix on 127.0.0.1:10026 for normal delivery

The script is fail-safe: if the webhook POST fails the email is still
delivered.  We never let a webhook error block mail flow.

master.cf usage:
    webhook-filter unix -  n  n  -  -  pipe
      flags=Rq
      user=vmail
      null_sender=
      argv=/usr/local/bin/webhook_sender.py inbound -f ${sender} -- ${recipient}
"""

import email
import email.policy
import email.utils
import logging
import os
import signal
import smtplib
import sys
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Logging — write to file + stderr so Postfix can capture output
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d] %(message)s",
    handlers=[
        logging.FileHandler("/var/log/postfix/webhook.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("postfix-webhook")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WEBHOOK_SERVICE_URL = os.getenv("WEBHOOK_SERVICE_URL", "http://webhooks:8081")
WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "10"))
REINJECT_HOST = "127.0.0.1"
REINJECT_PORT = 10026
REINJECT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_email_address(raw: str) -> str:
    """Extract a bare email address from a header value like 'Name <a@b.com>'."""
    if not raw:
        return ""
    try:
        _name, addr = email.utils.parseaddr(raw)
        return addr.strip().lower()
    except Exception:
        return raw.strip().lower()


def _parse_recipient_list(header_value: str) -> list[str]:
    """Split a To/Cc/Bcc header into individual clean addresses."""
    if not header_value:
        return []
    addresses = []
    for addr in header_value.split(","):
        cleaned = _clean_email_address(addr.strip())
        if cleaned and "@" in cleaned:
            addresses.append(cleaned)
    return addresses


def extract_metadata(msg: email.message.EmailMessage) -> dict[str, Any]:
    """
    Pull the fields we need out of a parsed email message.
    Returns a dict suitable for inclusion in the webhook JSON payload.
    """
    sender = _clean_email_address(msg.get("From", ""))

    recipients: list[str] = []
    for hdr in ("To", "Cc", "Bcc"):
        recipients.extend(_parse_recipient_list(msg.get(hdr, "")))

    message_id = msg.get("Message-ID", "").strip()
    subject = msg.get("Subject", "") or ""

    # Compute size from the raw bytes if available, fall back to str length
    try:
        size = len(msg.as_bytes(policy=email.policy.default))
    except Exception:
        size = len(str(msg))

    return {
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "message_id": message_id,
        "size": size,
    }


def post_webhook(direction: str, metadata: dict[str, Any]) -> bool:
    """
    POST the webhook event to the webhooks HTTP service.

    URL:  http://webhooks:8081/webhook/email/inbound
      or  http://webhooks:8081/webhook/email/outbound

    Returns True on success, False on any error (never raises).
    """
    # Lazy-import requests so the rest of the script can still reinject mail
    # even if the requests library is somehow broken.
    try:
        import requests
    except ImportError:
        logger.error("python-requests is not installed — cannot send webhook")
        return False

    event_name = f"email.{direction}"
    url = f"{WEBHOOK_SERVICE_URL}/webhook/email/{direction}"

    payload = {
        "event": event_name,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {
            "sender": metadata.get("sender", ""),
            "recipients": metadata.get("recipients", []),
            "subject": metadata.get("subject", ""),
            "message_id": metadata.get("message_id", ""),
            "size": metadata.get("size", 0),
        },
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=WEBHOOK_TIMEOUT,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mailyte-Postfix-Webhook/1.0",
            },
        )
        if resp.status_code < 300:
            logger.info(
                "Webhook delivered: %s -> %s (HTTP %d)",
                event_name,
                url,
                resp.status_code,
            )
            return True

        logger.warning(
            "Webhook HTTP error: %s -> %s (HTTP %d): %s",
            event_name,
            url,
            resp.status_code,
            resp.text[:300],
        )
        return False

    except requests.exceptions.Timeout:
        logger.warning("Webhook timeout: %s -> %s", event_name, url)
        return False
    except requests.exceptions.ConnectionError as exc:
        logger.warning("Webhook connection error: %s -> %s: %s", event_name, url, exc)
        return False
    except Exception as exc:
        logger.error("Webhook unexpected error: %s -> %s: %s", event_name, url, exc)
        return False


def reinject_email(
    email_bytes: bytes,
    sender: str,
    recipients: list[str],
) -> None:
    """
    Reinject the email into Postfix via SMTP on 127.0.0.1:10026.

    Port 10026 is the post-filter listener configured in master.cf with
    content_filter= (empty), so the message is delivered without being
    passed through this filter again (no loop).
    """
    if not recipients:
        logger.error("No recipients for reinjection — cannot deliver")
        sys.exit(75)  # EX_TEMPFAIL

    logger.info(
        "Reinjecting to %s:%d  sender=%s  recipients=%s",
        REINJECT_HOST,
        REINJECT_PORT,
        sender,
        recipients,
    )

    try:
        smtp = smtplib.SMTP(REINJECT_HOST, REINJECT_PORT, timeout=REINJECT_TIMEOUT)
        smtp.sendmail(sender, recipients, email_bytes)
        smtp.quit()
        logger.info("Email reinjected successfully via port %d", REINJECT_PORT)
    except Exception as exc:
        logger.error("SMTP reinjection to %s:%d failed: %s", REINJECT_HOST, REINJECT_PORT, exc)
        sys.exit(75)  # EX_TEMPFAIL — tell Postfix to retry later


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args():
    """
    Parse command-line arguments passed by the Postfix pipe transport.

    Expected invocation:
        webhook_sender.py <direction> -f <sender> -- <recipient1> [recipient2 ...]

    Returns (direction, sender, recipients).
    """
    args = sys.argv[1:]

    if not args:
        logger.error("Usage: webhook_sender.py <direction> -f <sender> -- <recipient ...>")
        sys.exit(1)

    direction = args[0].lower()
    if direction not in ("inbound", "outbound"):
        logger.error("Invalid direction %r — must be 'inbound' or 'outbound'", direction)
        sys.exit(1)

    remaining = args[1:]

    # Extract -f <sender>
    cli_sender = ""
    if "-f" in remaining:
        idx = remaining.index("-f")
        if idx + 1 < len(remaining):
            cli_sender = remaining[idx + 1]
        # Remove -f and its argument from the list
        remaining = remaining[:idx] + remaining[idx + 2 :]

    # Everything after "--" is recipient addresses
    cli_recipients: list[str] = []
    if "--" in remaining:
        idx = remaining.index("--")
        cli_recipients = remaining[idx + 1 :]

    return direction, cli_sender, cli_recipients


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """
    Entry point called by the Postfix pipe transport.

    1. Read the raw email from stdin
    2. Parse it and extract metadata
    3. Fire the webhook (best-effort — errors are logged, not fatal)
    4. Reinject the email to 127.0.0.1:10026 for delivery
    """

    # Graceful signal handling so we don't leave partial state
    def _signal_handler(signum, _frame):
        logger.warning("Received signal %d, exiting", signum)
        sys.exit(75)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        logger.info("=" * 60)
        logger.info("webhook_sender.py starting  PID=%d  argv=%s", os.getpid(), sys.argv)

        # --- 1. Parse CLI args ------------------------------------------------
        direction, cli_sender, cli_recipients = parse_args()

        # --- 2. Read the email from stdin (bytes) ----------------------------
        raw_email = sys.stdin.buffer.read()
        if not raw_email:
            logger.error("No email content received on stdin")
            sys.exit(75)

        logger.info("Read %d bytes from stdin", len(raw_email))

        # --- 3. Parse the email -----------------------------------------------
        try:
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)
        except Exception as exc:
            logger.error("Failed to parse email: %s — reinjecting original", exc)
            reinject_email(raw_email, cli_sender, cli_recipients)
            return

        # --- 4. Extract metadata ----------------------------------------------
        metadata = extract_metadata(msg)

        # The envelope sender/recipients from the CLI take precedence over
        # what is in the headers (the envelope can differ from headers).
        envelope_sender = cli_sender or metadata["sender"]
        envelope_recipients = cli_recipients if cli_recipients else metadata["recipients"]

        logger.info(
            "Email metadata:  direction=%s  sender=%s  recipients=%s  "
            "subject=%s  message_id=%s  size=%d",
            direction,
            envelope_sender,
            envelope_recipients,
            metadata["subject"][:60],
            metadata["message_id"],
            metadata["size"],
        )

        # --- 5. Fire the webhook (best-effort) --------------------------------
        try:
            post_webhook(direction, metadata)
        except Exception as exc:
            # Catch-all so the webhook never prevents mail delivery
            logger.error("Webhook dispatch failed (non-fatal): %s", exc)

        # --- 6. Reinject the email for delivery --------------------------------
        reinject_email(raw_email, envelope_sender, envelope_recipients)

        logger.info("webhook_sender.py completed successfully")
        logger.info("=" * 60)

    except SystemExit:
        raise  # Let sys.exit() propagate
    except Exception as exc:
        logger.error("Fatal error in webhook_sender.py: %s", exc, exc_info=True)
        # Last-ditch attempt to deliver the original email
        try:
            if raw_email:
                reinject_email(
                    raw_email,
                    cli_sender if "cli_sender" in dir() else "",
                    cli_recipients if "cli_recipients" in dir() else [],
                )
        except Exception:
            pass
        sys.exit(75)


if __name__ == "__main__":
    main()
