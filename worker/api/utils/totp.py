#!/usr/bin/env python3
"""
TOTP (RFC 6238) for operator MFA.

WHY THIS EXISTS IN CE. ADR-002 §3 makes MFA mandatory for every platform
operator and says to reuse the existing `totp_secrets` table rather than
building a second TOTP implementation. In mailyte-email-server that means
routes/platform_auth.py calls a separate `totp` container over HTTP. CE's
docker-compose.yml has no such service -- so an HTTP call to it would fail
DNS resolution and no CE operator could ever complete login, i.e. the console
would ship to CE with no way in.

The CE-appropriate substitute is this module: the same RFC 6238 algorithm,
against the same `totp_secrets` table (which CE's 0001_baseline already
has), in-process. Nothing about the stored data differs -- the same
base32 secret, the same SHA-256-hashed backup codes, the same
enabled/verified flags -- so a database written by one edition is readable by
the other. Only the transport changes: a function call instead of a service
hop.

Standard library only (hmac/hashlib/struct/base64/secrets); no new
dependency is added to worker/api/requirements.txt for this.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import string
import struct
import time
from urllib.parse import quote

from .database import get_db_connection

logger = logging.getLogger(__name__)

# Parameters must match what authenticator apps default to, and what the
# otpauth:// URI below advertises, or a scanned QR code produces codes this
# module rejects.
TOTP_DIGITS = 6
TOTP_PERIOD = 30  # seconds
TOTP_ALGORITHM = "SHA1"
TOTP_SECRET_LENGTH = 32  # base32 characters
TOTP_TOLERANCE = 1  # accept +/- one time step, for clock skew

BACKUP_CODE_COUNT = 8
BACKUP_CODE_LENGTH = 8

TOTP_ISSUER_DEFAULT = "Mailyte"


def _issuer() -> str:
    import os

    return os.getenv("TOTP_ISSUER", TOTP_ISSUER_DEFAULT)


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------


def generate_secret(length: int = TOTP_SECRET_LENGTH) -> str:
    """A random base32 secret of `length` characters."""
    # Each base32 character encodes 5 bits; round up to whole bytes.
    byte_count = (length * 5 + 7) // 8
    encoded = base64.b32encode(secrets.token_bytes(byte_count)).decode("ascii")
    return encoded[:length].upper()


def generate_totp(secret_b32: str, time_step: int | None = None) -> str:
    """The RFC 6238 code for `secret_b32` at `time_step` (current if None)."""
    if time_step is None:
        time_step = int(time.time()) // TOTP_PERIOD

    key = base64.b32decode(secret_b32.upper())
    msg = struct.pack(">Q", time_step)
    digest = hmac.new(key, msg, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226 §5.4).
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(secret_b32: str, token: str, tolerance: int = TOTP_TOLERANCE) -> bool:
    """True if `token` matches any step within +/- tolerance.

    compare_digest rather than == so a wrong code costs the same time
    whichever digit is wrong.
    """
    if not secret_b32 or not token:
        return False
    try:
        current_step = int(time.time()) // TOTP_PERIOD
        for offset in range(-tolerance, tolerance + 1):
            if hmac.compare_digest(generate_totp(secret_b32, current_step + offset), token.strip()):
                return True
    except (ValueError, TypeError, binascii.Error) as exc:
        logger.warning(f"TOTP verification failed on a malformed secret/token: {exc}")
        return False
    return False


def build_otpauth_uri(secret: str, user_email: str) -> str:
    """The otpauth:// URI a QR code encodes."""
    issuer = _issuer()
    label = quote(f"{issuer}:{user_email}", safe="")
    params = (
        f"secret={secret}"
        f"&issuer={quote(issuer)}"
        f"&algorithm={TOTP_ALGORITHM}"
        f"&digits={TOTP_DIGITS}"
        f"&period={TOTP_PERIOD}"
    )
    return f"otpauth://totp/{label}?{params}"


def generate_backup_codes(count: int = BACKUP_CODE_COUNT, length: int = BACKUP_CODE_LENGTH) -> list:
    """Plaintext backup codes -- shown to the operator exactly once."""
    alphabet = string.ascii_uppercase + string.digits
    return ["".join(secrets.choice(alphabet) for _ in range(length)) for _ in range(count)]


def hash_backup_code(code: str) -> str:
    """Backup codes are stored hashed. SHA-256 rather than bcrypt because
    these are 8 characters of full-entropy random, not a human-chosen
    password -- there is no dictionary to slow down."""
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# totp_secrets storage
# ---------------------------------------------------------------------------


def get_secret_row(user_email: str) -> dict | None:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM totp_secrets WHERE user_email = %s", (user_email,))
        row = cursor.fetchone()
        cursor.close()
        return row
    except Exception as exc:
        logger.error(f"TOTP secret lookup failed: {exc}")
        return None
    finally:
        conn.close()


def is_enrolled(user_email: str) -> bool:
    """Whether this identity already has an ENABLED TOTP secret. A row that
    exists but is disabled (e.g. after an MFA reset) is not enrolled."""
    row = get_secret_row(user_email)
    return bool(row and row.get("enabled") and row.get("secret"))


def begin_enrollment(user_email: str) -> dict | None:
    """Create (or replace) a pending secret and return what the operator
    needs to scan it. Returns None if the write failed.

    ON DUPLICATE KEY UPDATE rather than DELETE+INSERT so `created_at`
    survives as evidence of when MFA was first set up on this identity, and
    so re-running setup before confirming simply supersedes the pending
    secret instead of erroring.
    """
    secret = generate_secret()
    plaintext_codes = generate_backup_codes()
    hashed_codes = [hash_backup_code(code) for code in plaintext_codes]

    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO totp_secrets (user_email, secret, backup_codes, enabled, verified)
            VALUES (%s, %s, %s, 0, 0)
            ON DUPLICATE KEY UPDATE
                secret = VALUES(secret),
                backup_codes = VALUES(backup_codes),
                enabled = 0,
                verified = 0
            """,
            (user_email, secret, json.dumps(hashed_codes)),
        )
        conn.commit()
        cursor.close()
    except Exception as exc:
        logger.error(f"TOTP enrollment write failed: {exc}")
        return None
    finally:
        conn.close()

    return {
        "secret": secret,
        "otpauth_uri": build_otpauth_uri(secret, user_email),
        # The only time these are ever returned in plaintext.
        "backup_codes": plaintext_codes,
        "digits": TOTP_DIGITS,
        "period": TOTP_PERIOD,
        "algorithm": TOTP_ALGORITHM,
    }


def confirm_enrollment(user_email: str, token: str) -> bool:
    """Verify the first code and flip enabled/verified on. Returns False on a
    bad code, leaving the secret pending rather than half-enabled."""
    row = get_secret_row(user_email)
    if not row or not row.get("secret"):
        return False
    if not verify_totp(row["secret"], token):
        return False

    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE totp_secrets SET enabled = 1, verified = 1, "
            "last_used_at = CURRENT_TIMESTAMP WHERE user_email = %s",
            (user_email,),
        )
        conn.commit()
        cursor.close()
        return True
    except Exception as exc:
        logger.error(f"TOTP enable failed: {exc}")
        return False
    finally:
        conn.close()


def _consume_backup_code(user_email: str, code: str) -> bool:
    """Single-use: a matching code is removed from the stored list before
    this returns True, so replaying it fails."""
    row = get_secret_row(user_email)
    if not row or not row.get("backup_codes"):
        return False

    stored = row["backup_codes"]
    if isinstance(stored, (bytes, bytearray)):
        stored = stored.decode("utf-8", errors="replace")
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return False
    if not isinstance(stored, list):
        return False

    candidate = hash_backup_code(code)
    remaining = [entry for entry in stored if not hmac.compare_digest(str(entry), candidate)]
    if len(remaining) == len(stored):
        return False

    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE totp_secrets SET backup_codes = %s, last_used_at = CURRENT_TIMESTAMP "
            "WHERE user_email = %s",
            (json.dumps(remaining), user_email),
        )
        conn.commit()
        cursor.close()
        logger.warning(f"TOTP backup code consumed for {user_email} ({len(remaining)} left)")
        return True
    except Exception as exc:
        logger.error(f"TOTP backup code consumption failed: {exc}")
        return False
    finally:
        conn.close()


def verify_login_token(user_email: str, token: str) -> bool:
    """Verify a code at login: a live TOTP code, or one backup code.

    Backup codes are accepted here rather than at a separate endpoint
    because on a single-operator CE instance they are the only way back in
    after a lost authenticator, and a recovery path behind a screen you can
    only reach once you have recovered is not a recovery path.
    """
    row = get_secret_row(user_email)
    if not row or not row.get("enabled"):
        return False

    if verify_totp(row.get("secret") or "", token):
        conn = get_db_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE totp_secrets SET last_used_at = CURRENT_TIMESTAMP "
                    "WHERE user_email = %s",
                    (user_email,),
                )
                conn.commit()
                cursor.close()
            except Exception as exc:
                # A failed timestamp update must not fail the login it
                # describes.
                logger.warning(f"TOTP last_used_at update failed: {exc}")
            finally:
                conn.close()
        return True

    return _consume_backup_code(user_email, token)


def reset_enrollment(user_email: str) -> bool:
    """Clear the secret so the identity must re-enroll. Disabled in place
    rather than DELETEd: totp_secrets.secret is NOT NULL so it is blanked,
    and keeping the row means created_at survives as evidence of when MFA was
    first set up. Returns whether a row was actually affected."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE totp_secrets SET secret = '', backup_codes = NULL, enabled = 0, verified = 0 "
            "WHERE user_email = %s",
            (user_email,),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        return affected > 0
    except Exception as exc:
        logger.error(f"TOTP reset failed: {exc}")
        return False
    finally:
        conn.close()
