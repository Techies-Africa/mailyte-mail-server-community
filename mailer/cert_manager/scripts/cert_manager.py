#!/usr/bin/env python3
"""
SSL Certificate Manager

Automated SSL certificate management for email domains using Let's Encrypt.
Monitors certificate expiration and automatically renews certificates.
Copies issued certificates to the shared volume so Postfix and Dovecot
pick them up via their entrypoint scripts on next restart.

For development: Postfix/Dovecot entrypoints generate self-signed certs.
For production: This service obtains real Let's Encrypt certs.
"""

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/var/log/cert_manager/cert_manager.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("cert_manager")


class CertificateManager:
    """SSL Certificate Manager for automated certificate lifecycle management."""

    def __init__(self):
        self.db_config = {
            "host": os.getenv("DB_HOST", "mysql"),
            "port": int(os.getenv("DB_PORT", 3306)),
            "database": os.getenv("DB_NAME", "mailserver"),
            "user": os.getenv("DB_USER", "mailuser"),
            "password": os.getenv("DB_PASSWORD", "mailpassword"),
        }

        self.acme_email = os.getenv("ACME_EMAIL", "admin@localhost")
        self.staging = os.getenv("ACME_STAGING", "true").lower() == "true"

        # Shared volume paths — Postfix and Dovecot mount these
        self.cert_path = os.getenv("SSL_CERT_PATH", "/etc/ssl/certs")
        self.key_path = os.getenv("SSL_KEY_PATH", "/etc/ssl/private")

        self.webhook_url = os.getenv("WEBHOOK_URLS", "")
        self.webhook_secret = os.getenv("WEBHOOK_SECRET", "")

        self.renewal_days = int(os.getenv("CERT_RENEWAL_DAYS", "30"))
        self.check_interval = int(os.getenv("CERT_CHECK_INTERVAL", "21600"))

        self.use_wildcard_strategy = os.getenv("USE_WILDCARD_CERTS", "false").lower() == "true"
        self.max_domains_per_cert = int(os.getenv("MAX_DOMAINS_PER_CERT", "100"))

        # Wildcard cert — *.WILDCARD_DOMAIN becomes the shared server cert covering all orgs
        # Requires DNS_PROVIDER for DNS-01 ACME challenge (e.g. 'route53', 'cloudflare')
        self.wildcard_domain = os.getenv("WILDCARD_DOMAIN", "")
        self.dns_provider = os.getenv("DNS_PROVIDER", "")

        # SNI config path — cert_manager writes map files here; Postfix/Dovecot read them
        self.sni_config_path = os.getenv("SNI_CONFIG_PATH", "/etc/ssl/sni")

        # Optional: send SIGHUP to Postfix/Dovecot containers after cert changes
        # Reloads go through docker-proxy, never a mounted docker.sock (C1)
        self.docker_reload_enabled = os.getenv("DOCKER_RELOAD_ENABLED", "false").lower() == "true"
        self.docker_proxy_url = os.getenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
        self.postfix_container = os.getenv("POSTFIX_CONTAINER", "postfix")
        self.dovecot_container = os.getenv("DOVECOT_CONTAINER", "dovecot")

        # Multiple ACME accounts — rotate to multiply Let's Encrypt rate limits
        # ACME_EMAILS: comma-separated list, e.g. "acme1@company.com,acme2@company.com"
        # Each account gets 300 new orders / 3 hours → 3 accounts = 900 orders / 3 hours
        acme_emails_raw = os.getenv("ACME_EMAILS", self.acme_email)
        self.acme_accounts = [e.strip() for e in acme_emails_raw.split(",") if e.strip()]
        if self.acme_email not in self.acme_accounts:
            self.acme_accounts.insert(0, self.acme_email)
        self._account_index = 0
        self._account_lock = threading.Lock()

        # Parallel cert workers — process multiple domains concurrently
        # CERT_WORKER_THREADS: number of parallel certbot processes (default 3)
        # Each worker uses a different ACME account from the rotation pool
        self.cert_worker_threads = int(os.getenv("CERT_WORKER_THREADS", "3"))

        # Lock for SNI map writes — prevents concurrent threads from corrupting map files
        self._sni_lock = threading.Lock()

        # Ensure directories exist
        os.makedirs(self.cert_path, exist_ok=True)
        os.makedirs(self.key_path, exist_ok=True)
        os.makedirs(self.sni_config_path, exist_ok=True)
        os.makedirs("/var/log/cert_manager", exist_ok=True)
        os.makedirs("/etc/letsencrypt", exist_ok=True)

        if self.staging:
            logger.warning(
                "⚠ ACME staging mode is ACTIVE — certificates issued by Let's Encrypt "
                "staging CA are NOT trusted by browsers or mail clients. "
                "Set ACME_STAGING=false for production deployments."
            )

        logger.info(f"Certificate Manager initialized (staging={self.staging})")

    def get_database_connection(self):
        try:
            return mysql.connector.connect(**self.db_config)
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return None

    def get_domains_from_database(self):
        conn = self.get_database_connection()
        if not conn:
            return []
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT domain FROM domains WHERE active = 1")
            domains = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            logger.info(f"Retrieved {len(domains)} active domains")
            return domains
        except Exception as e:
            logger.error(f"Failed to fetch domains: {e}")
            return []

    def check_certificate_expiry(self, domain):
        """Check if certificate needs renewal. Returns True if cert missing or expiring."""
        cert_file = f"{self.cert_path}/{domain}.crt"

        if not os.path.exists(cert_file):
            # Also check for the shared server.crt
            if not os.path.exists(f"{self.cert_path}/server.crt"):
                logger.info(f"No certificate for {domain}")
                return True
            cert_file = f"{self.cert_path}/server.crt"

        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", cert_file, "-noout", "-enddate"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and "=" in result.stdout:
                expiry_str = result.stdout.strip().split("=", 1)[1]
                try:
                    expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                except ValueError:
                    expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y GMT")

                days_left = (expiry_date - datetime.utcnow()).days
                if days_left <= self.renewal_days:
                    logger.info(f"{domain}: expires in {days_left} days, needs renewal")
                    return True
                else:
                    logger.debug(f"{domain}: valid for {days_left} days")
                    return False

            logger.warning(f"Could not parse cert expiry for {domain}")
            return True

        except Exception as e:
            logger.error(f"Error checking cert for {domain}: {e}")
            return True

    def _get_next_acme_account(self):
        """
        Return the next ACME account email in round-robin order and record
        the selection in the acme_account_stats table for visibility.

        With multiple accounts in ACME_EMAILS, each account independently
        gets Let's Encrypt's 300 new orders / 3 hours limit, so N accounts
        gives N × 300 orders per window. Thread-safe.
        """
        with self._account_lock:
            email = self.acme_accounts[self._account_index % len(self.acme_accounts)]
            self._account_index += 1

        # Record account selection in DB for visibility (non-blocking — best effort)
        try:
            conn = self.get_database_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO acme_account_stats
                        (email, certs_issued, last_used, created_at, updated_at)
                    VALUES (%s, 1, NOW(), NOW(), NOW())
                    ON DUPLICATE KEY UPDATE
                        certs_issued = certs_issued + 1,
                        last_used = NOW(),
                        updated_at = NOW()
                """,
                    (email,),
                )
                conn.commit()
                cursor.close()
                conn.close()
        except Exception:
            pass  # Account tracking is best-effort — never block cert issuance

        return email

    def record_acme_result(self, email, success):
        """Update success/failure counters for an ACME account after cert attempt."""
        try:
            conn = self.get_database_connection()
            if conn:
                cursor = conn.cursor()
                if success:
                    cursor.execute(
                        """
                        UPDATE acme_account_stats
                        SET success_count = success_count + 1, updated_at = NOW()
                        WHERE email = %s
                    """,
                        (email,),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE acme_account_stats
                        SET failure_count = failure_count + 1, updated_at = NOW()
                        WHERE email = %s
                    """,
                        (email,),
                    )
                conn.commit()
                cursor.close()
                conn.close()
        except Exception:
            pass

    def obtain_certificate(self, domain, _skip_sni_update=False):
        """
        Obtain SSL certificate via Let's Encrypt certbot.

        Certbot stores certs in /etc/letsencrypt/live/{domain}/.
        After success, we copy them to the shared volume so Postfix/Dovecot
        pick them up on restart.

        _skip_sni_update: when True, skips the per-cert SNI map rebuild.
        Used by process_domains() which does a single rebuild after all
        parallel workers complete (more efficient than N concurrent rebuilds).
        """
        acme_email = self._get_next_acme_account()
        try:
            logger.info(f"Obtaining certificate for {domain} (account: {acme_email})")

            # Build certbot command
            cmd = [
                "certbot",
                "certonly",
                "--non-interactive",
                "--agree-tos",
                "--email",
                acme_email,
            ]

            if self.staging:
                cmd.append("--staging")

            # Use standalone HTTP-01 challenge
            cmd.extend(
                [
                    "--standalone",
                    "--preferred-challenges",
                    "http",
                    "-d",
                    domain,
                ]
            )

            # Add mail subdomains
            for sub in [f"mail.{domain}", f"smtp.{domain}", f"imap.{domain}"]:
                cmd.extend(["-d", sub])

            logger.info(f"Running: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                logger.info(f"Certbot succeeded for {domain}")

                # Copy from letsencrypt live directory to shared volume
                self._deploy_certificate(domain, _skip_sni_update=_skip_sni_update)

                self.update_certificate_database(domain, True)
                self._send_webhook("certificate_obtained", domain, True)
                self.record_acme_result(acme_email, success=True)

                logger.info(
                    f"Certificate deployed for {domain}. "
                    f"Postfix/Dovecot will use it on next restart."
                )
                return True
            else:
                error = result.stderr or result.stdout
                logger.error(f"Certbot failed for {domain}: {error}")
                self.update_certificate_database(domain, False, error[:500])
                self._send_webhook("certificate_failed", domain, False, error[:500])
                self.record_acme_result(acme_email, success=False)
                return False

        except subprocess.TimeoutExpired:
            msg = f"Certbot timeout for {domain}"
            logger.error(msg)
            self.update_certificate_database(domain, False, msg)
            self.record_acme_result(acme_email, success=False)
            return False
        except Exception as e:
            msg = f"Error obtaining cert for {domain}: {e}"
            logger.error(msg)
            self.update_certificate_database(domain, False, str(e)[:500])
            self.record_acme_result(acme_email, success=False)
            return False

    def obtain_wildcard_certificate(self):
        """
        Obtain a wildcard certificate for *.WILDCARD_DOMAIN using DNS-01 challenge.

        This becomes the default server.crt/server.key used by Postfix and Dovecot,
        covering all organizations that use smtp.mailyte.com / imap.mailyte.com.
        DNS-01 is required for wildcards — the DNS provider must be configured.

        Supported DNS providers (certbot plugins): route53, cloudflare, digitalocean, etc.
        Set DNS_PROVIDER=route53 and supply the corresponding credentials as env vars.
        """
        if not self.wildcard_domain:
            logger.debug("WILDCARD_DOMAIN not set — skipping wildcard cert")
            return False
        if not self.dns_provider:
            logger.warning("DNS_PROVIDER not set — cannot obtain wildcard cert (DNS-01 required)")
            return False

        # Check if existing wildcard cert needs renewal
        wildcard_cert = f"{self.cert_path}/wildcard.crt"
        if os.path.exists(wildcard_cert) and not self.check_certificate_expiry_file(wildcard_cert):
            logger.debug(f"Wildcard cert for *.{self.wildcard_domain} is still valid")
            return True

        logger.info(f"Obtaining wildcard certificate for *.{self.wildcard_domain}")

        cmd = [
            "certbot",
            "certonly",
            "--non-interactive",
            "--agree-tos",
            "--email",
            self.acme_email,
            f"--dns-{self.dns_provider}",
            "-d",
            self.wildcard_domain,
            "-d",
            f"*.{self.wildcard_domain}",
        ]
        if self.staging:
            cmd.append("--staging")

        logger.info(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            logger.error(f"Wildcard cert timeout for *.{self.wildcard_domain}")
            return False
        except Exception as e:
            logger.error(f"Wildcard cert error: {e}")
            return False

        if result.returncode != 0:
            error = result.stderr or result.stdout
            logger.error(f"Wildcard cert failed for *.{self.wildcard_domain}: {error[:500]}")
            return False

        # Deploy wildcard as server.crt (default cert for all connections)
        live_dir = f"/etc/letsencrypt/live/{self.wildcard_domain}"
        if not os.path.isdir(live_dir):
            logger.error(f"Wildcard live dir not found: {live_dir}")
            return False

        try:
            shutil.copy2(f"{live_dir}/fullchain.pem", f"{self.cert_path}/server.crt")
            shutil.copy2(f"{live_dir}/privkey.pem", f"{self.key_path}/server.key")
            shutil.copy2(f"{live_dir}/fullchain.pem", f"{self.cert_path}/wildcard.crt")
            shutil.copy2(f"{live_dir}/privkey.pem", f"{self.key_path}/wildcard.key")
            os.chmod(f"{self.key_path}/server.key", 0o600)
            os.chmod(f"{self.key_path}/wildcard.key", 0o600)
            logger.info(
                f"Wildcard cert deployed as server.crt/server.key for *.{self.wildcard_domain}"
            )
            self._reload_services()
            return True
        except Exception as e:
            logger.error(f"Failed to deploy wildcard cert: {e}")
            return False

    def check_certificate_expiry_file(self, cert_file):
        """Check if a specific cert file needs renewal. Returns True if expiring/missing."""
        if not os.path.exists(cert_file):
            return True
        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", cert_file, "-noout", "-enddate"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and "=" in result.stdout:
                expiry_str = result.stdout.strip().split("=", 1)[1]
                try:
                    expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                except ValueError:
                    expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y GMT")
                days_left = (expiry_date - datetime.utcnow()).days
                return days_left <= self.renewal_days
        except Exception:
            pass
        return True

    def _deploy_certificate(self, domain, _skip_sni_update=False):
        """
        Copy per-domain certificates from certbot's live directory to the shared volume.

        Writes only domain-specific files. Does NOT overwrite server.crt/server.key
        (those are managed by the wildcard cert or the entrypoint self-signed fallback).

        _skip_sni_update: when True, skips the SNI map rebuild. Used by process_domains()
        which calls _update_sni_maps() once after all parallel workers finish, rather than
        triggering N concurrent rebuilds (one per domain).
        """
        live_dir = f"/etc/letsencrypt/live/{domain}"

        if not os.path.isdir(live_dir):
            logger.warning(f"Live directory not found: {live_dir}")
            return

        try:
            # Write domain-specific cert files for SNI lookup
            shutil.copy2(f"{live_dir}/fullchain.pem", f"{self.cert_path}/{domain}.crt")
            shutil.copy2(f"{live_dir}/privkey.pem", f"{self.key_path}/{domain}.key")
            shutil.copy2(f"{live_dir}/fullchain.pem", f"{self.cert_path}/{domain}_fullchain.crt")
            os.chmod(f"{self.key_path}/{domain}.key", 0o600)

            logger.info(f"Deployed certificate files for {domain}")

            # Rebuild SNI maps so Postfix/Dovecot serve this cert for the domain.
            # Skipped when called from parallel workers — process_domains() does one
            # consolidated rebuild after all workers complete.
            if not _skip_sni_update:
                self._update_sni_maps()

        except Exception as e:
            logger.error(f"Failed to deploy cert for {domain}: {e}")

    def _update_sni_maps(self):
        """
        Scan all domain-specific cert files and rebuild SNI map files for:
          - Postfix:  {sni_config_path}/postfix_sni.map  (tls_server_sni_maps format)
          - Dovecot:  {sni_config_path}/dovecot_sni.conf (local_name {} blocks)

        Postfix format:  hostname /path/to/key /path/to/cert
        Dovecot format:  local_name hostname { ssl_cert = ... ; ssl_key = ... }

        Postfix/Dovecot entrypoints copy these files on startup and (optionally)
        receive SIGHUP from cert_manager for live reloads.
        """
        postfix_lines = []
        dovecot_blocks = []
        domain_count = 0

        try:
            for cert_file in sorted(Path(self.cert_path).glob("*.crt")):
                stem = cert_file.stem
                # Skip shared/wildcard certs — they are the default, not SNI entries
                if stem in ("server", "wildcard") or stem.endswith("_fullchain"):
                    continue

                domain = stem
                key_file = Path(self.key_path) / f"{domain}.key"
                if not key_file.exists():
                    continue

                domain_count += 1

                # Postfix SNI: bare domain + common mail subdomains
                for hostname in [domain, f"mail.{domain}", f"smtp.{domain}", f"imap.{domain}"]:
                    postfix_lines.append(f"{hostname} {key_file} {cert_file}")

                # Dovecot SNI: local_name block per hostname
                for hostname in [domain, f"mail.{domain}", f"imap.{domain}", f"smtp.{domain}"]:
                    dovecot_blocks.append(
                        f"local_name {hostname} {{\n"
                        f"  ssl_cert = <{cert_file}\n"
                        f"  ssl_key = <{key_file}\n"
                        f"}}"
                    )

            if not postfix_lines:
                logger.debug("No domain-specific certs found — SNI maps empty")
                return

            # Serialize writes — only one thread rebuilds the map at a time.
            # This prevents partial writes when parallel cert workers finish close together.
            with self._sni_lock:
                # Write Postfix SNI map
                postfix_map = Path(self.sni_config_path) / "postfix_sni.map"
                postfix_map.write_text(
                    "# Auto-generated by cert_manager — do not edit manually\n"
                    + "\n".join(postfix_lines)
                    + "\n"
                )

                # Write Dovecot SNI conf
                dovecot_conf = Path(self.sni_config_path) / "dovecot_sni.conf"
                dovecot_conf.write_text(
                    "# Auto-generated by cert_manager — do not edit manually\n\n"
                    + "\n\n".join(dovecot_blocks)
                    + "\n"
                )

            logger.info(
                f"SNI maps updated: {domain_count} domains, "
                f"{len(postfix_lines)} Postfix entries, {len(dovecot_blocks)} Dovecot blocks"
            )

            # Reload Postfix/Dovecot so they pick up the new certs without restart
            self._reload_services()

        except Exception as e:
            logger.error(f"Failed to update SNI maps: {e}")

    def _reload_services(self):
        """
        Send SIGHUP to Postfix and Dovecot containers to reload TLS config live.

        Postfix reloads tls_server_sni_maps on SIGHUP without dropping connections.
        Dovecot reloads ssl_cert/local_name blocks on SIGHUP.

        Requires DOCKER_RELOAD_ENABLED=true. Goes through docker-proxy
        (tecnativa/docker-socket-proxy, CONTAINERS+POST+ALLOW_RESTARTS only --
        covers stop/restart/kill, nothing else) rather than a socket mount.
        A bind-mounted docker.sock's `:ro` flag only stops this container from
        writing to the *socket file itself*; it does nothing to restrict which
        Docker API calls a client may make once connected, so the previous
        mount was full unrestricted Docker API access -- root on the host --
        while looking safe (C1). The proxy is what actually narrows it.

        The previous implementation also shelled out to a `docker` binary that
        this image has never installed, so live reload could not have worked
        even with the socket present: certificates renewed, and postfix and
        dovecot kept serving the old ones until something restarted them.
        """
        if not self.docker_reload_enabled:
            return

        for container in [self.postfix_container, self.dovecot_container]:
            try:
                resp = requests.post(
                    f"{self.docker_proxy_url}/containers/{container}/kill",
                    params={"signal": "HUP"},
                    timeout=10,
                )
                if resp.status_code in (200, 204):
                    logger.info(f"Sent SIGHUP to {container} — TLS config reloading")
                else:
                    logger.warning(
                        f"Failed to signal {container}: HTTP {resp.status_code} {resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"Could not signal {container}: {e}")

    def _should_use_wildcard(self, domain):
        if not self.use_wildcard_strategy:
            return False

        conn = self.get_database_connection()
        if not conn:
            return False

        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM domains WHERE domain LIKE %s AND active = 1", (f"%.{domain}",)
            )
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            return count >= 3
        except Exception as e:
            logger.error(f"Wildcard check failed for {domain}: {e}")
            return False

    def _check_rate_limits(self):
        conn = self.get_database_connection()
        if not conn:
            return {"can_proceed": True}

        try:
            cursor = conn.cursor()
            one_week_ago = datetime.now() - timedelta(days=7)
            cursor.execute(
                "SELECT COUNT(*) FROM ssl_certificates WHERE created_at >= %s OR updated_at >= %s",
                (one_week_ago, one_week_ago),
            )
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            if count >= 40:
                return {"can_proceed": False, "reason": f"{count} certs this week (limit: 50)"}
            return {"can_proceed": True, "recent_count": count}
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            return {"can_proceed": True}

    def _group_domains(self, domains):
        groups = {}
        for domain in domains:
            parts = domain.split(".")
            base = ".".join(parts[-2:]) if len(parts) >= 2 else domain
            groups.setdefault(base, []).append(domain)
        return groups

    def _link_certificate(self, domain, source_domain):
        """Create symlinks so domain uses another domain's certificate."""
        try:
            for ext, path in [(".crt", self.cert_path), (".key", self.key_path)]:
                src = f"{path}/{source_domain}{ext}"
                dst = f"{path}/{domain}{ext}"
                if os.path.exists(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.symlink(src, dst)

            self.update_certificate_database(domain, True, f"Linked to {source_domain}")
            logger.info(f"Linked {domain} → {source_domain}")
        except Exception as e:
            logger.error(f"Failed to link cert for {domain}: {e}")

    def update_certificate_database(self, domain, success, error_message=None):
        conn = self.get_database_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()
            status = "active" if success else "failed"
            now = datetime.now()

            cursor.execute("SELECT id FROM ssl_certificates WHERE domain = %s", (domain,))

            if cursor.fetchone():
                cursor.execute(
                    "UPDATE ssl_certificates SET status = %s, updated_at = %s, error_message = %s WHERE domain = %s",
                    (status, now, error_message, domain),
                )
            else:
                cursor.execute(
                    "INSERT INTO ssl_certificates (domain, status, created_at, updated_at, error_message) VALUES (%s, %s, %s, %s, %s)",
                    (domain, status, now, now, error_message),
                )

            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"DB update failed for {domain}: {e}")

    def _send_webhook(self, event_type, domain, success, error_message=None):
        if not self.webhook_url:
            return

        try:
            data = {
                "event_type": f"ssl.{event_type}",
                "domain": domain,
                "success": success,
                "timestamp": datetime.now().isoformat(),
                "service": "cert_manager",
            }
            if error_message:
                data["error_message"] = error_message

            payload = json.dumps(data)
            headers = {"Content-Type": "application/json", "User-Agent": "CertManager/1.0"}

            if self.webhook_secret:
                sig = hmac.new(
                    self.webhook_secret.encode(), payload.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={sig}"

            requests.post(self.webhook_url, data=payload, headers=headers, timeout=30)
        except Exception as e:
            logger.error(f"Webhook failed for {domain}: {e}")

    def process_domains(self):
        """
        Process all domains for certificate management using parallel workers.

        Architecture:
        1. Serial expiry check — fast openssl call per domain, no network
        2. Parallel cert issuance — CERT_WORKER_THREADS workers run concurrently,
           each using the next ACME account from the rotation pool
        3. Single SNI map rebuild after all workers complete — avoids N concurrent
           map writes and N redundant SIGHUP signals
        """
        rate_status = self._check_rate_limits()
        if not rate_status.get("can_proceed", True):
            logger.warning(f"Rate limit: {rate_status.get('reason')}")
            return

        domains = self.get_domains_from_database()
        if not domains:
            logger.info("No domains found")
            return

        groups = self._group_domains(domains)
        logger.info(f"Checking {len(groups)} domain groups ({len(domains)} domains total)")

        # Phase 1: serial expiry check — cheap, no network I/O
        groups_needing_renewal = [
            (name, group_domains)
            for name, group_domains in groups.items()
            if any(self.check_certificate_expiry(d) for d in group_domains)
        ]

        if not groups_needing_renewal:
            logger.info("All certificates are valid — no renewals needed")
            return

        logger.info(
            f"{len(groups_needing_renewal)} groups need renewal — "
            f"starting {self.cert_worker_threads} parallel workers "
            f"across {len(self.acme_accounts)} ACME account(s)"
        )

        # Use mutable containers so nested function can update without nonlocal
        results = {"ok": 0, "fail": 0}
        counter_lock = threading.Lock()

        def _process_group(args):
            group_name, group_domains = args
            try:
                primary = group_domains[0]
                # _skip_sni_update=True — we do one consolidated rebuild after all workers
                if self.obtain_certificate(primary, _skip_sni_update=True):
                    with counter_lock:
                        results["ok"] += 1
                    for d in group_domains[1:]:
                        self._link_certificate(d, primary)
                else:
                    with counter_lock:
                        results["fail"] += 1
            except Exception as e:
                logger.error(f"Worker error for group {group_name}: {e}")
                with counter_lock:
                    results["fail"] += 1

        # Phase 2: parallel cert issuance
        with ThreadPoolExecutor(max_workers=self.cert_worker_threads) as pool:
            futures = {
                pool.submit(_process_group, item): item[0] for item in groups_needing_renewal
            }
            for future in as_completed(futures):
                group_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Unhandled worker exception for {group_name}: {e}")

        ok, fail = results["ok"], results["fail"]

        # Phase 3: single consolidated SNI map rebuild + one SIGHUP
        if ok > 0:
            logger.info(f"Rebuilding SNI maps after {ok} successful cert issuances")
            self._update_sni_maps()

        logger.info(
            f"Done. Success: {ok}, Failed: {fail}, "
            f"Workers: {self.cert_worker_threads}, Accounts: {len(self.acme_accounts)}"
        )

    def setup_certificate_tables(self):
        """No-op since phase-08 (schema-migrations): both tables this used to
        create with CREATE TABLE IF NOT EXISTS are now owned by the Alembic
        migration chain -- ssl_certificates by 0001_baseline (it was already
        there before this ever ran; this call was always a no-op in
        practice), acme_account_stats by 0002_adhoc_table_tracking. compose
        now makes this service depend_on `migrate: service_completed_successfully`,
        so both are guaranteed to exist before `run()` below is reached.
        Kept as a method (rather than deleting the call site in run()) so a
        future re-add doesn't require re-wiring the startup sequence.
        """
        return

    def run(self):
        """Main loop — check certs periodically."""
        logger.info(f"Starting certificate monitoring (interval: {self.check_interval}s)")

        # Bootstrap wildcard cert on first run — covers *.mailyte.com for all standard orgs
        if self.wildcard_domain and self.dns_provider:
            self.obtain_wildcard_certificate()

        while True:
            try:
                # Renew wildcard cert if it's approaching expiry
                if self.wildcard_domain and self.dns_provider:
                    self.obtain_wildcard_certificate()
                self.process_domains()
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                logger.info("Shutting down")
                break
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                time.sleep(300)


def main():
    logger.info("Starting SSL Certificate Manager")
    mgr = CertificateManager()
    mgr.setup_certificate_tables()
    mgr.process_domains()
    mgr.run()


if __name__ == "__main__":
    main()
