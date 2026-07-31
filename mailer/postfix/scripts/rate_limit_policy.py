#!/usr/bin/env python3
"""
Postfix Rate Limiting Policy Server (HTTP Bridge)

This script is invoked by the Postfix spawn(8) service via master.cf:

    policy-rate-limit unix - n n - - spawn
      user=vmail
      argv=/usr/local/bin/rate_limit_policy.py

It speaks the Postfix policy delegation protocol on stdin/stdout and
delegates rate-limit decisions to the centralised rate limiter FastAPI
service over HTTP.

Protocol summary
----------------
Postfix writes key=value pairs (one per line) terminated by a blank line.
We reply with a single line:

    action=DUNNO\n\n              -- allow (let other restrictions decide)
    action=DEFER_IF_PERMIT ...\n\n -- soft-reject when over quota

If the HTTP service is unreachable the script fails open (DUNNO) so that
mail is never silently dropped because of a service outage.
"""

import os
import sys
import json
import logging
from typing import Dict
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RATE_LIMITER_URL = os.environ.get("RATE_LIMITER_URL", "http://rate_limiter:8082")
RATE_LIMITER_CHECK_ENDPOINT = f"{RATE_LIMITER_URL}/check_rate_limit"
RATE_LIMITER_POLICY_ENDPOINT = f"{RATE_LIMITER_URL}/policy"
HTTP_TIMEOUT = int(os.environ.get("RATE_LIMITER_TIMEOUT", "5"))

# Whether to use the /policy endpoint (plain-text Postfix protocol over HTTP)
# or the /check_rate_limit endpoint (JSON API).  The /policy endpoint is
# simpler but the JSON API gives richer error detail.
USE_POLICY_ENDPOINT = os.environ.get("USE_POLICY_ENDPOINT", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Logging — writes to file and stderr (Postfix captures stderr in maillog)
# ---------------------------------------------------------------------------
log_handlers = [logging.StreamHandler()]
try:
    log_handlers.append(logging.FileHandler("/var/log/postfix/rate_limit_policy.log"))
except (OSError, PermissionError):
    pass  # File may not be writable in all environments

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("rate_limit_policy")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _http_post_json(url: str, payload: dict, timeout: int = HTTP_TIMEOUT) -> dict:
    """POST JSON to *url* and return the parsed response body."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_text(url: str, body: str, timeout: int = HTTP_TIMEOUT) -> str:
    """POST plain text to *url* and return the response body as a string."""
    data = body.encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "text/plain"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Policy logic
# ---------------------------------------------------------------------------
def check_via_json_api(attrs: Dict[str, str]) -> str:
    """
    Check rate limit using the /check_rate_limit JSON API.

    Returns the Postfix policy action string (without the ``action=`` prefix).
    """
    sender = attrs.get("sender", "")
    sasl_username = attrs.get("sasl_username", "")
    email = sasl_username if sasl_username else sender

    if not email or "@" not in email:
        return "DUNNO"

    direction = "outbound" if sasl_username else "inbound"

    payload = {
        "email": email,
        "direction": direction,
    }

    try:
        resp = _http_post_json(RATE_LIMITER_CHECK_ENDPOINT, payload)
    except URLError as exc:
        logger.error(f"Rate limiter HTTP error: {exc}")
        return "DUNNO"  # fail open
    except Exception as exc:
        logger.error(f"Rate limiter unexpected error: {exc}")
        return "DUNNO"

    allowed = resp.get("allowed", True)
    message = resp.get("message", "")

    if allowed:
        return "DUNNO"
    else:
        logger.warning(f"Rate limit exceeded for {email} ({direction}): {message}")
        return f"DEFER_IF_PERMIT 4.7.1 Rate limit exceeded for {email}. Try again later."


def check_via_policy_endpoint(raw_request: str) -> str:
    """
    Forward the raw Postfix policy request to the /policy HTTP endpoint
    and return whatever action it sends back.

    The /policy endpoint already returns ``action=...\n\n``, so we
    extract just the action value.
    """
    try:
        resp_text = _http_post_text(RATE_LIMITER_POLICY_ENDPOINT, raw_request)
    except URLError as exc:
        logger.error(f"Rate limiter /policy HTTP error: {exc}")
        return "DUNNO"
    except Exception as exc:
        logger.error(f"Rate limiter /policy unexpected error: {exc}")
        return "DUNNO"

    # Response is "action=DUNNO\n\n" or "action=DEFER_IF_PERMIT ...\n\n"
    for line in resp_text.splitlines():
        line = line.strip()
        if line.lower().startswith("action="):
            return line.split("=", 1)[1]

    return "DUNNO"


def process_request(attrs: Dict[str, str], raw_request: str) -> str:
    """Decide which backend to use and return the Postfix policy action."""
    if USE_POLICY_ENDPOINT:
        return check_via_policy_endpoint(raw_request)
    else:
        return check_via_json_api(attrs)


# ---------------------------------------------------------------------------
# Main loop — Postfix policy protocol on stdin/stdout
# ---------------------------------------------------------------------------
def main():
    logger.info(
        f"Starting Postfix rate-limit policy bridge "
        f"(rate_limiter={RATE_LIMITER_URL}, "
        f"use_policy_endpoint={USE_POLICY_ENDPOINT})"
    )

    while True:
        try:
            attrs: Dict[str, str] = {}
            raw_lines = []

            for line in sys.stdin:
                line = line.strip()
                raw_lines.append(line)
                if not line:
                    break
                if "=" in line:
                    key, value = line.split("=", 1)
                    attrs[key] = value

            if not attrs:
                # EOF or empty request — Postfix closed the pipe
                break

            raw_request = "\n".join(raw_lines) + "\n"
            action = process_request(attrs, raw_request)

            sys.stdout.write(f"action={action}\n\n")
            sys.stdout.flush()

        except KeyboardInterrupt:
            logger.info("Shutting down")
            break
        except Exception as exc:
            logger.error(f"Request processing error: {exc}")
            # Fail open — never block mail because of an internal error
            sys.stdout.write("action=DUNNO\n\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
