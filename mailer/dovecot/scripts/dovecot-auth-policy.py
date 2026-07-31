#!/usr/bin/env python3
"""
Dovecot Authentication Policy Server — Redis-Backed

Implements the Dovecot auth_policy HTTP protocol for brute-force protection.
Uses Redis for real-time rate limiting (survives container restarts) and
MySQL for persistent audit logging and IP reputation tracking.

Protocol:
  POST /policy/allow  — Before auth: returns {"status": 0} (allow) or {"status": -N} (delay N seconds)
  POST /policy/report  — After auth: records success/failure

Features:
  - Progressive delay: 2s → 4s → 8s → 16s → 32s (exponential backoff)
  - IP blocking after configurable failure threshold
  - Per-user blocking after configurable failure threshold
  - Redis-backed state (survives restarts, shared across instances)
  - MySQL audit logging (failed_auth_attempts + audit_logs tables)
  - IP reputation scoring
"""

import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import mysql.connector
import redis

# Add project root to path for shared imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from shared.webhook_dispatcher import Events, dispatch_event

    _dispatcher_available = True
except ImportError:
    _dispatcher_available = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [auth-policy] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("auth-policy")


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_NAME = os.getenv("DB_NAME", "mailserver")
DB_USER = os.getenv("DB_USER", "mailuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mailpassword")

# Rate limiting thresholds
MAX_FAILURES_PER_IP = int(os.getenv("MAX_AUTH_FAILURES_PER_IP", "10"))
MAX_FAILURES_PER_USER = int(os.getenv("MAX_AUTH_FAILURES_PER_USER", "5"))
FAILURE_WINDOW_SECS = int(os.getenv("AUTH_FAILURE_WINDOW_SECS", "900"))  # 15 minutes
BLOCK_DURATION_SECS = int(os.getenv("AUTH_BLOCK_DURATION_SECS", "3600"))  # 1 hour

# Progressive delay parameters
BASE_DELAY = 2  # seconds
MAX_DELAY = 60  # seconds

LISTEN_HOST = os.getenv("AUTH_POLICY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("AUTH_POLICY_PORT", "8090"))


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------
def get_redis():
    """Get a Redis connection with retry logic."""
    for attempt in range(3):
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=2, decode_responses=True)
            r.ping()
            return r
        except redis.ConnectionError:
            if attempt < 2:
                time.sleep(1)
                continue
            logger.error("Cannot connect to Redis — rate limiting disabled")
            return None


def get_db():
    """Get a MySQL connection."""
    try:
        return mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=5,
        )
    except mysql.connector.Error as e:
        logger.error(f"MySQL connection failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Auth Policy Logic
# ---------------------------------------------------------------------------
class AuthPolicyEngine:
    """Core brute-force protection logic with Redis + MySQL backing."""

    def __init__(self):
        self.redis = get_redis()

    def _redis_key(self, prefix, identifier):
        return f"auth_policy:{prefix}:{identifier}"

    def check_before_auth(self, login, remote_ip, protocol):
        """
        Check if the auth attempt should be allowed, delayed, or blocked.
        Returns delay in seconds (0 = allow, >0 = delay).
        """
        if not self.redis:
            return 0  # Fail open if Redis is down

        try:
            # Check if IP is hard-blocked
            block_key = self._redis_key("blocked_ip", remote_ip)
            if self.redis.exists(block_key):
                ttl = self.redis.ttl(block_key)
                logger.warning(
                    f"Blocked IP {remote_ip} attempted login as {login} ({protocol}), blocked for {ttl}s more"
                )
                return -1  # Signal to reject

            # Check if user is hard-blocked
            block_key = self._redis_key("blocked_user", login)
            if self.redis.exists(block_key):
                ttl = self.redis.ttl(block_key)
                logger.warning(
                    f"Blocked user {login} attempted login from {remote_ip} ({protocol}), blocked for {ttl}s more"
                )
                return -1  # Signal to reject

            # Count recent failures for progressive delay
            ip_failures = self._get_failure_count("ip", remote_ip)
            user_failures = self._get_failure_count("user", login)
            max_failures = max(ip_failures, user_failures)

            if max_failures == 0:
                return 0

            # Progressive delay: 2^(failures-1) * BASE_DELAY, capped at MAX_DELAY
            delay = min(BASE_DELAY * (2 ** (max_failures - 1)), MAX_DELAY)
            logger.info(
                f"Delaying auth for {login} from {remote_ip}: {delay}s (ip_fails={ip_failures}, user_fails={user_failures})"
            )
            return int(delay)

        except redis.RedisError as e:
            logger.error(f"Redis error in check_before_auth: {e}")
            return 0  # Fail open

    def report_auth_result(self, login, remote_ip, protocol, success):
        """Record auth result — update Redis counters and MySQL audit logs."""
        if success:
            self._handle_success(login, remote_ip, protocol)
        else:
            self._handle_failure(login, remote_ip, protocol)

    def _handle_success(self, login, remote_ip, protocol):
        """Clear failure counters on successful authentication."""
        if self.redis:
            try:
                # Clear user failures on success (IP stays — other users might be under attack)
                user_key = self._redis_key("failures_user", login)
                self.redis.delete(user_key)
            except redis.RedisError as e:
                logger.error(f"Redis error clearing success: {e}")

        # Log to audit_logs
        self._write_audit_log("auth.success", protocol, login, remote_ip, "info")

        # Update IP reputation (positive)
        self._update_ip_reputation(remote_ip, success=True)

        # Dispatch global webhook event
        if _dispatcher_available:
            try:
                domain = login.split("@")[-1] if "@" in login else None
                dispatch_event(
                    Events.AUTH_LOGIN_SUCCESS,
                    data={"user": login, "ip_address": remote_ip, "protocol": protocol},
                    domain=domain,
                    source_service="dovecot",
                    use_redis=True,
                )
            except Exception:
                pass  # Never block auth over webhook dispatch

    def _handle_failure(self, login, remote_ip, protocol):
        """Record failure, check thresholds, potentially block."""
        now = time.time()

        if self.redis:
            try:
                # Increment IP failure counter (sorted set with timestamps)
                ip_key = self._redis_key("failures_ip", remote_ip)
                self.redis.zadd(ip_key, {str(now): now})
                self.redis.zremrangebyscore(ip_key, 0, now - FAILURE_WINDOW_SECS)
                self.redis.expire(ip_key, FAILURE_WINDOW_SECS)

                # Increment user failure counter
                user_key = self._redis_key("failures_user", login)
                self.redis.zadd(user_key, {str(now): now})
                self.redis.zremrangebyscore(user_key, 0, now - FAILURE_WINDOW_SECS)
                self.redis.expire(user_key, FAILURE_WINDOW_SECS)

                # Check thresholds for blocking
                ip_count = self.redis.zcard(ip_key)
                user_count = self.redis.zcard(user_key)

                if ip_count >= MAX_FAILURES_PER_IP:
                    block_key = self._redis_key("blocked_ip", remote_ip)
                    self.redis.setex(block_key, BLOCK_DURATION_SECS, "1")
                    logger.warning(
                        f"IP {remote_ip} BLOCKED for {BLOCK_DURATION_SECS}s after {ip_count} failures"
                    )
                    self._write_audit_log(
                        "auth.ip_blocked",
                        protocol,
                        login,
                        remote_ip,
                        "critical",
                        {"failure_count": ip_count, "block_duration": BLOCK_DURATION_SECS},
                    )
                    if _dispatcher_available:
                        try:
                            dispatch_event(
                                Events.SECURITY_BRUTE_FORCE,
                                data={
                                    "ip_address": remote_ip,
                                    "user": login,
                                    "protocol": protocol,
                                    "failure_count": ip_count,
                                    "block_duration_secs": BLOCK_DURATION_SECS,
                                    "block_type": "ip",
                                },
                                source_service="dovecot",
                                use_redis=True,
                            )
                        except Exception:
                            pass

                if user_count >= MAX_FAILURES_PER_USER:
                    block_key = self._redis_key("blocked_user", login)
                    self.redis.setex(block_key, BLOCK_DURATION_SECS, "1")
                    logger.warning(
                        f"User {login} BLOCKED for {BLOCK_DURATION_SECS}s after {user_count} failures"
                    )
                    self._write_audit_log(
                        "auth.user_blocked",
                        protocol,
                        login,
                        remote_ip,
                        "critical",
                        {"failure_count": user_count, "block_duration": BLOCK_DURATION_SECS},
                    )
                    if _dispatcher_available:
                        try:
                            dispatch_event(
                                Events.SECURITY_BRUTE_FORCE,
                                data={
                                    "ip_address": remote_ip,
                                    "user": login,
                                    "protocol": protocol,
                                    "failure_count": user_count,
                                    "block_duration_secs": BLOCK_DURATION_SECS,
                                    "block_type": "user",
                                },
                                source_service="dovecot",
                                use_redis=True,
                            )
                        except Exception:
                            pass

            except redis.RedisError as e:
                logger.error(f"Redis error recording failure: {e}")

        # Log to audit_logs
        self._write_audit_log("auth.failed", protocol, login, remote_ip, "warning")

        # Write to failed_auth_attempts table
        self._write_failed_attempt(remote_ip, login, protocol)

        # Update IP reputation (negative)
        self._update_ip_reputation(remote_ip, success=False)

        # Dispatch global webhook event
        if _dispatcher_available:
            try:
                domain = login.split("@")[-1] if "@" in login else None
                dispatch_event(
                    Events.AUTH_LOGIN_FAILURE,
                    data={"user": login, "ip_address": remote_ip, "protocol": protocol},
                    domain=domain,
                    source_service="dovecot",
                    use_redis=True,
                )
            except Exception:
                pass  # Never block auth over webhook dispatch

    def _get_failure_count(self, type_, identifier):
        """Get current failure count from Redis sorted set."""
        try:
            key = self._redis_key(f"failures_{type_}", identifier)
            now = time.time()
            # Clean old entries
            self.redis.zremrangebyscore(key, 0, now - FAILURE_WINDOW_SECS)
            return self.redis.zcard(key)
        except redis.RedisError:
            return 0

    def _write_audit_log(self, event_type, protocol, login, remote_ip, severity, details=None):
        """Write event to audit_logs table (fire-and-forget)."""
        conn = get_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO audit_logs (event_type, event_source, user_email, client_ip, severity, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    event_type,
                    protocol or "dovecot",
                    login,
                    remote_ip,
                    severity,
                    json.dumps(details) if details else None,
                ),
            )
            conn.commit()
            cursor.close()
        except mysql.connector.Error as e:
            logger.debug(f"Audit log write failed (table may not exist yet): {e}")
        finally:
            conn.close()

    def _write_failed_attempt(self, remote_ip, login, protocol):
        """Write or update failed_auth_attempts table."""
        conn = get_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            # Upsert: increment if exists within window, else insert
            cursor.execute(
                "INSERT INTO failed_auth_attempts (client_ip, username, service, attempt_count, first_attempt_at, last_attempt_at) "
                "VALUES (%s, %s, %s, 1, NOW(), NOW()) "
                "ON DUPLICATE KEY UPDATE attempt_count = attempt_count + 1, last_attempt_at = NOW()",
                (remote_ip, login, protocol or "imap"),
            )
            conn.commit()
            cursor.close()
        except mysql.connector.Error as e:
            logger.debug(f"Failed attempt write failed: {e}")
        finally:
            conn.close()

    def _update_ip_reputation(self, remote_ip, success):
        """Update ip_reputation table with auth result."""
        conn = get_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            if success:
                cursor.execute(
                    "INSERT INTO ip_reputation (ip_address, reputation_score, total_connections, last_seen, first_seen) "
                    "VALUES (%s, 55.00, 1, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "  total_connections = total_connections + 1, "
                    "  reputation_score = LEAST(100, reputation_score + 0.5), "
                    "  last_seen = NOW()",
                    (remote_ip,),
                )
            else:
                cursor.execute(
                    "INSERT INTO ip_reputation (ip_address, reputation_score, total_connections, auth_failure_count, last_seen, first_seen) "
                    "VALUES (%s, 40.00, 1, 1, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "  total_connections = total_connections + 1, "
                    "  auth_failure_count = auth_failure_count + 1, "
                    "  reputation_score = GREATEST(0, reputation_score - 5.0), "
                    "  last_seen = NOW()",
                    (remote_ip,),
                )
            conn.commit()
            cursor.close()
        except mysql.connector.Error as e:
            logger.debug(f"IP reputation update failed: {e}")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# HTTP Handler — Dovecot auth_policy protocol
# ---------------------------------------------------------------------------
class PolicyHandler(BaseHTTPRequestHandler):
    """
    Handles Dovecot auth policy HTTP requests.

    Dovecot sends:
      POST /policy/allow   — check before auth (JSON body)
      POST /policy/report  — report after auth (JSON body with 'success' field)
    """

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}

            login = data.get("login", "")
            remote_ip = data.get("remote", "")
            protocol = data.get("protocol", "imap")
            path = self.path

            if "/allow" in path:
                # Pre-auth check
                delay = self.server.engine.check_before_auth(login, remote_ip, protocol)
                if delay == -1:
                    # Hard block — respond with large delay
                    response = {
                        "status": -BLOCK_DURATION_SECS,
                        "msg": "IP or user temporarily blocked",
                    }
                elif delay > 0:
                    response = {"status": -delay, "msg": f"Rate limited, retry after {delay}s"}
                else:
                    response = {"status": 0, "msg": "ok"}

            elif "/report" in path:
                # Post-auth report
                success = data.get("success", False)
                policy_reject = data.get("policy_reject", False)
                if not policy_reject:  # Only count real auth results
                    self.server.engine.report_auth_result(login, remote_ip, protocol, success)
                response = {"status": 0}

            else:
                response = {"status": 0}

            response_body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        except Exception as e:
            logger.error(f"Error handling request: {e}")
            # Fail open — don't block auth if policy server has bugs
            fallback = json.dumps({"status": 0, "msg": "policy error"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(fallback)))
            self.end_headers()
            self.wfile.write(fallback)

    def log_message(self, format, *args):
        """Suppress default HTTP logging — we use our own logger."""
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    engine = AuthPolicyEngine()

    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), PolicyHandler)
    server.engine = engine

    logger.info(f"Auth policy server listening on {LISTEN_HOST}:{LISTEN_PORT}")
    logger.info(
        f"Rate limits: {MAX_FAILURES_PER_IP} fails/IP, {MAX_FAILURES_PER_USER} fails/user in {FAILURE_WINDOW_SECS}s window"
    )
    logger.info(f"Block duration: {BLOCK_DURATION_SECS}s | Redis: {REDIS_HOST}:{REDIS_PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down auth policy server")
        server.shutdown()


if __name__ == "__main__":
    main()
