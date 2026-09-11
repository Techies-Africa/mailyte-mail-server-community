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
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector
import requests
from ulid import ULID

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

        # Shared volume paths — this container's own view. Postfix/Dovecot
        # mount the SAME host directories (storage/ssl_certs,
        # storage/ssl_private) but at .../custom subpaths instead (their
        # entrypoints copy the *default* server.crt/server.key out of
        # .../custom into their own top-level /etc/ssl/certs -- confirmed
        # in mailer/dovecot/scripts/entrypoint.sh -- but never per-domain
        # SNI cert files). Writing SNI map/conf entries using cert_path/
        # key_path directly pointed Postfix/Dovecot at a path only this
        # container has -- confirmed live: Dovecot fatally failed to start,
        # "Can't open file /etc/ssl/certs/courier.mailyte.com.crt: No such
        # file or directory", even though the file existed (at .../custom
        # from Dovecot's own mount). mail_cert_path/mail_key_path below are
        # the correct prefixes for _update_sni_maps' Postfix/Dovecot output
        # specifically; cert_path/key_path stay as this container's own
        # read/write paths (and Traefik's, which mounts the same way this
        # container does -- see docker-compose.prod.yml's traefik volumes).
        self.cert_path = os.getenv("SSL_CERT_PATH", "/etc/ssl/certs")
        self.key_path = os.getenv("SSL_KEY_PATH", "/etc/ssl/private")
        self.mail_cert_path = os.getenv("MAIL_SSL_CERT_PATH", "/etc/ssl/certs/custom")
        self.mail_key_path = os.getenv("MAIL_SSL_KEY_PATH", "/etc/ssl/private/custom")

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

        # Traefik's own built-in ACME (certResolver) and this container's certbot
        # both need port 80 for HTTP-01, and Traefik's internal ACME handler
        # unconditionally intercepts the entire /.well-known/acme-challenge/
        # path space on any entrypoint where a certResolver is configured --
        # confirmed live, it swallows every challenge request before our
        # acme-webroot router ever sees it, regardless of router priority.
        # Two independent ACME clients can't share port 80 that way, so
        # Traefik's admin-subdomain routers (api/docs/grafana/jmap/caldav/
        # autoconfig/traefik dashboard) get their certs from HERE instead --
        # same webroot flow as mail domains, just without the mail/smtp/imap
        # SAN expansion (see obtain_certificate's additional_sans param).
        # Traefik reads the resulting files via its file provider (see
        # _update_sni_maps' traefik_certs.yml output and traefik.yml).
        self.domain = os.getenv("DOMAIN", "")
        self.admin_subdomains = [
            s.strip()
            for s in os.getenv(
                "TRAEFIK_ADMIN_SUBDOMAINS",
                "api,autoconfig,jmap,caldav,docs,grafana,traefik,console",
            ).split(",")
            if s.strip()
        ]
        # _update_sni_maps() uses this to keep admin-subdomain certs OUT of
        # Postfix/Dovecot's SNI maps -- confirmed live: Dovecot eagerly
        # loads every cert file its config references at startup (not
        # on-demand per SNI hostname), so a single-name admin cert like
        # api.courier.mailyte.com produced a mail.api.courier.mailyte.com
        # local_name block Dovecot then fatally failed to start on
        # ("Can't open file ...: No such file or directory" -- it's not
        # mounted into Dovecot/Postfix's containers, nor should it be).
        # Admin certs still belong in Traefik's cert list, just not these.
        # Fully-qualified hostnames that are NOT <sub>.${DOMAIN}.
        #
        # admin_subdomains can only ever produce names under DOMAIN, and on a
        # deployment where DOMAIN is itself a subdomain (courier.mailyte.com)
        # that cannot express a name on the apex. The webmail is the first such
        # case: users type webmail.mailyte.com, not webmail.courier.mailyte.com,
        # so it needs a certificate for a name this list could not otherwise
        # reach.
        #
        # Each entry must be a complete hostname and must already resolve to
        # this host -- HTTP-01 is verified against real DNS, so a name that does
        # not point here fails the challenge rather than silently doing nothing.
        self.extra_hostnames = [
            h.strip() for h in os.getenv("TRAEFIK_EXTRA_HOSTNAMES", "").split(",") if h.strip()
        ]

        # Both sets are excluded from Postfix/Dovecot's SNI maps for the same
        # reason (see below): they are Traefik-only certs that are not mounted
        # into the mail containers, and Dovecot fatally fails to start on a
        # referenced cert file it cannot open.
        self.admin_hostnames = {f"{sub}.{self.domain}" for sub in self.admin_subdomains} | set(
            self.extra_hostnames
        )

        # HTTP-01 webroot -- shared with the acme_webroot service, which Traefik
        # routes ACME challenge requests to. Not --standalone: this container no
        # longer binds port 80 itself, since Traefik already owns it permanently
        # as the reverse proxy. See obtain_certificate() below.
        self.webroot_path = os.getenv("ACME_WEBROOT_PATH", "/var/www/acme-challenge")

        # Optional: send SIGHUP to Postfix/Dovecot containers after cert changes.
        # Goes through docker-proxy (phase-07 C1), not a raw socket mount --
        # see reload_dependent_services() below for why.
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

        # Certbot takes an exclusive lock on its config directory, so two
        # concurrent invocations do not queue -- the second dies immediately
        # with "Another instance of Certbot is already running". process_domains()
        # runs a ThreadPoolExecutor, so every worker past the first lost its
        # domain that way. The parallelism is still worth keeping for the DNS
        # pre-checks and deployment either side of this call; only the certbot
        # subprocess itself has to be serialised.
        self._certbot_lock = threading.Lock()

        # Ensure directories exist
        os.makedirs(self.cert_path, exist_ok=True)
        os.makedirs(self.key_path, exist_ok=True)
        os.makedirs(self.sni_config_path, exist_ok=True)
        os.makedirs(self.webroot_path, exist_ok=True)
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

    def _resolve_public(self, hostname: str) -> set:
        """A records for *hostname* as the outside world sees them.

        Uses a public resolver rather than the container's own stack: Docker
        puts the container hostname in /etc/hosts, so a local lookup of the
        mail hostname answers with a private 172.x address. Falls back to
        getaddrinfo when dig is unavailable, which is still correct for names
        that are not the container's own.
        """
        try:
            out = subprocess.run(
                ["dig", "+short", "+time=3", "+tries=2", "A", hostname, "@1.1.1.1"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if out.returncode == 0:
                found = {
                    line.strip()
                    for line in out.stdout.splitlines()
                    # skip CNAME lines in +short output, which end in a dot
                    if line.strip() and not line.strip().endswith(".")
                }
                if found:
                    return found
        except (OSError, subprocess.SubprocessError):
            pass

        try:
            return {info[4][0] for info in socket.getaddrinfo(hostname, None, socket.AF_INET)}
        except OSError:
            return set()

    def _server_ips(self) -> set:
        """The addresses this server answers HTTP-01 challenges on.

        Cached per run. CERT_SERVER_IPS overrides for hosts behind NAT, where
        the public address is not one the container can resolve for itself.
        """
        if getattr(self, "_cached_server_ips", None):
            return self._cached_server_ips

        override = os.getenv("CERT_SERVER_IPS", "").strip()
        if override:
            ips = {ip.strip() for ip in override.split(",") if ip.strip()}
            self._cached_server_ips = ips
            return ips

        # Deliberately NOT resolved from inside the container.
        #
        # Docker writes the container's own hostname into /etc/hosts, and these
        # services run with HOSTNAME=courier.mailyte.com -- so resolving the
        # mail hostname here returns the container's private address
        # (172.25.0.x), which matches nothing public. That made _points_here()
        # reject every candidate and turned an entire issuance run into
        # "Success: 0, Failed: 25". Ask the public resolver instead of
        # ourselves.
        ips = set()
        for name in filter(None, [self.domain, os.getenv("MAIL_HOSTNAME", "")]):
            ips.update(self._resolve_public(name))

        if not ips:
            logger.warning(
                "Could not determine this server's public IP; SAN filtering "
                "disabled for this run. Set CERT_SERVER_IPS to pin it."
            )

        self._cached_server_ips = ips
        return ips

    def _points_here(self, hostname: str) -> bool:
        """Does this hostname resolve to us, and so pass an HTTP-01 challenge?

        Certbot requests every -d name in ONE order, and Let's Encrypt fails
        the WHOLE order if any single name does not validate. So one stale
        subdomain takes down the certificate for names that were perfectly
        fine -- which is why mailyte.com has no certificate: it is requested
        alongside smtp.mailyte.com (a different host entirely) and
        imap.mailyte.com (does not exist), so all four fail together.
        Filtering candidates by what actually resolves here turns that
        all-or-nothing order into a best-effort one.
        """
        server_ips = self._server_ips()
        if not server_ips:
            # Nothing to compare against -- assume yes rather than silently
            # dropping every SAN and refusing to issue anything at all.
            return True
        return bool(self._resolve_public(hostname) & server_ips)

    def obtain_certificate(self, domain, additional_sans=None, _skip_sni_update=False):
        """
        Obtain SSL certificate via Let's Encrypt certbot.

        Certbot stores certs in /etc/letsencrypt/live/{domain}/.
        After success, we copy them to the shared volume so Postfix/Dovecot
        pick them up on restart.

        additional_sans: extra -d names to request alongside domain. Defaults
        (None) to the conventional mail/smtp/imap subdomains for real mail
        domains. Pass [] explicitly for infrastructure hostnames (e.g.
        Traefik admin subdomains via process_admin_domains()) that have no
        matching mail/smtp/imap DNS of their own and don't need any.

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

            # Webroot HTTP-01 challenge, not --standalone: this container
            # doesn't bind port 80 itself (Traefik does, permanently, as the
            # reverse proxy) -- certbot just drops the challenge file here,
            # and the acme_webroot service (routed to by Traefik for
            # /.well-known/acme-challenge/*) serves it.
            cmd.extend(
                [
                    "--webroot",
                    "--webroot-path",
                    self.webroot_path,
                    "--preferred-challenges",
                    "http",
                    # Pin the lineage name to the domain. Certbot otherwise
                    # names the live directory after the FIRST -d, and the
                    # primary is no longer guaranteed to be the domain itself
                    # (see the SAN filtering below) -- _deploy_certificate
                    # looks in /etc/letsencrypt/live/{domain}, so without this
                    # a successful issue would deploy nothing.
                    "--cert-name",
                    domain,
                ]
            )

            # Add mail subdomains (or the caller's own override -- see
            # additional_sans in the docstring above)
            #
            # autoconfig/autodiscover are included because Traefik serves
            # client auto-setup on those hostnames; without a certificate for
            # them, Outlook and Thunderbird get a TLS error instead of their
            # settings. mailcow issued them, and losing them in the migration
            # is why auto-setup stopped working.
            candidates = (
                additional_sans
                if additional_sans is not None
                else [
                    f"mail.{domain}",
                    f"smtp.{domain}",
                    f"imap.{domain}",
                    f"autoconfig.{domain}",
                    f"autodiscover.{domain}",
                ]
            )

            # Only request names that actually resolve here. One name that
            # does not validate fails the entire certificate order, taking
            # down names that were fine -- see _points_here().
            #
            # The bare domain is filtered too, not just the SANs. A mail
            # domain often points its apex at a website on another host:
            # mailyte.com resolves to the web server, so requesting it here
            # fails and takes mail.mailyte.com down with it. When the apex
            # does not resolve here, the first surviving SAN becomes the
            # certificate's primary name instead of abandoning the domain.
            wanted = [domain] + list(candidates)
            names = [n for n in wanted if self._points_here(n)]
            dropped = [n for n in wanted if n not in names]

            if dropped:
                logger.info(
                    f"{domain}: skipping names that do not resolve here: {', '.join(dropped)}"
                )

            if not names:
                logger.warning(
                    f"{domain}: no candidate name resolves to this server "
                    f"({', '.join(sorted(self._server_ips())) or 'unknown'}) -- "
                    f"skipping, an HTTP-01 order could only fail"
                )
                self.update_certificate_database(domain, False, "no name resolves to this server")
                return False

            if names[0] != domain:
                logger.info(f"{domain}: apex does not resolve here, using {names[0]} as primary")

            for name in names:
                cmd.extend(["-d", name])

            logger.info(f"Running: {' '.join(cmd)}")

            # Serialised: certbot refuses to run concurrently (see
            # _certbot_lock). Held only for the subprocess, not the DNS
            # pre-checks or deployment around it.
            with self._certbot_lock:
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
            # Same exclusive-lock constraint as obtain_certificate().
            with self._certbot_lock:
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
          - Postfix:  {sni_config_path}/postfix_sni.map    (tls_server_sni_maps format)
          - Dovecot:  {sni_config_path}/dovecot_sni.conf   (local_name {} blocks)
          - Traefik:  {sni_config_path}/traefik_certs.yml  (file provider tls.certificates)

        Postfix format:  hostname /path/to/key /path/to/cert
        Dovecot format:  local_name hostname { ssl_cert = ... ; ssl_key = ... }
        Traefik format:  tls.certificates: [{certFile, keyFile}, ...] -- Traefik
        matches these against router Host() rules by the cert's own SAN, not by
        filename, so one entry per cert file is enough (no per-subdomain
        expansion needed the way Postfix/Dovecot's SNI lookup requires).

        Postfix/Dovecot entrypoints copy these files on startup and (optionally)
        receive SIGHUP from cert_manager for live reloads. Traefik's file
        provider watches its file directly (providers.file, watch: true in
        traefik.yml) -- no reload signal needed.
        """
        postfix_lines = []
        dovecot_blocks = []
        traefik_certs = []
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

                # Admin-subdomain certs (api/docs/grafana/... -- single-name,
                # requested via process_admin_domains) are Traefik-only. They
                # never get mail/smtp/imap SAN siblings and Postfix/Dovecot
                # have no reason to reference them at all -- see the
                # self.admin_hostnames comment in __init__.
                if domain not in self.admin_hostnames:
                    # Postfix/Dovecot see these files at .../custom (see
                    # mail_cert_path/mail_key_path comment in __init__) --
                    # NOT this container's own cert_path/key_path.
                    mail_cert_file = Path(self.mail_cert_path) / cert_file.name
                    mail_key_file = Path(self.mail_key_path) / key_file.name

                    # Postfix SNI: bare domain + common mail subdomains
                    for hostname in [domain, f"mail.{domain}", f"smtp.{domain}", f"imap.{domain}"]:
                        postfix_lines.append(f"{hostname} {mail_key_file} {mail_cert_file}")

                    # Dovecot SNI: local_name block per hostname
                    for hostname in [domain, f"mail.{domain}", f"imap.{domain}", f"smtp.{domain}"]:
                        dovecot_blocks.append(
                            f"local_name {hostname} {{\n"
                            f"  ssl_cert = <{mail_cert_file}\n"
                            f"  ssl_key = <{mail_key_file}\n"
                            f"}}"
                        )

                # Traefik: one entry per cert file (admin subdomains and mail
                # domains alike -- harmless if a mail domain's cert never
                # matches any router's Host() rule, it just sits unused).
                traefik_certs.append(f"    - certFile: {cert_file}\n      keyFile: {key_file}")

            if not (postfix_lines or dovecot_blocks or traefik_certs):
                # Was "if not postfix_lines" -- wrong once admin-only certs
                # became possible: an admin-only deployment (e.g. only
                # api.courier.mailyte.com issued so far) legitimately has
                # empty postfix_lines/dovecot_blocks but a non-empty
                # traefik_certs, and that still needs writing.
                logger.debug("No certs found — SNI/Traefik maps empty")
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

                # Write Traefik dynamic TLS config (file provider)
                traefik_conf = Path(self.sni_config_path) / "traefik_certs.yml"
                traefik_conf.write_text(
                    "# Auto-generated by cert_manager — do not edit manually\ntls:\n"
                    "  certificates:\n" + "\n".join(traefik_certs) + "\n"
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
        A bind-mounted docker.sock's `:ro` flag only stops this container
        from writing to the *socket file itself* -- it does nothing to
        restrict which Docker API calls a client can make once connected,
        so the previous `:ro` mount here was full unrestricted API access
        despite looking safer than monitoring's read-write one (security-model.md
        C1). The proxy is what actually narrows the API surface.
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
                if resp.status_code in (204, 200):
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
            # ssl_certificates has no updated_at column (001_init_schema.sql) --
            # last_renewed is the closest equivalent ("was this row touched
            # recently"), confirmed live: the original query failed with
            # "Unknown column 'updated_at'" on every single run.
            cursor.execute(
                "SELECT COUNT(*) FROM ssl_certificates WHERE created_at >= %s OR last_renewed >= %s",
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
        """Group domains that can legitimately share one certificate.

        The default grouping is by registrable base (last two labels), so
        example.com and www.example.com get a single cert -- the primary's SAN
        expansion (mail/smtp/imap.<primary>, see obtain_certificate) covers both.

        **A domain that is a strict subdomain of ANOTHER LISTED domain is
        deliberately excluded from that grouping and gets its own cert.** The
        parent's SAN expansion is built from the parent's name, so it can never
        cover the child's own hostnames -- and the loser of the group is
        symlinked onto the winner's cert, silently.

        This is not hypothetical. `mailyte.com` and `courier.mailyte.com` are
        both mail domains here; they shared the base `mailyte.com`, courier won
        as primary, and mailyte.com.crt became a symlink to a certificate whose
        SANs are mail/imap/smtp.courier.mailyte.com. There was therefore NO
        certificate for mail.mailyte.com -- the hostname every mail client on
        this server is configured with -- and Dovecot served courier's cert to
        anyone connecting to it.
        """
        listed = set(domains)
        groups = {}
        for domain in domains:
            parts = domain.split(".")
            base = ".".join(parts[-2:]) if len(parts) >= 2 else domain
            # Own group when the base is itself a separately managed domain.
            key = domain if (domain != base and base in listed) else base
            groups.setdefault(key, []).append(domain)
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
                    # Relative target (just the filename), not the
                    # absolute src path -- confirmed live: an absolute
                    # symlink baked in THIS container's own mount path
                    # (self.cert_path, e.g. /etc/ssl/certs) resolves to
                    # nothing in Postfix/Dovecot's containers, which mount
                    # the same host directory at a different path
                    # (/etc/ssl/certs/custom). A same-directory relative
                    # target resolves correctly regardless of where the
                    # shared host directory is mounted.
                    os.symlink(f"{source_domain}{ext}", dst)

            self.update_certificate_database(domain, True, f"Linked to {source_domain}")
            logger.info(f"Linked {domain} → {source_domain}")
        except Exception as e:
            logger.error(f"Failed to link cert for {domain}: {e}")

    def update_certificate_database(self, domain, success, error_message=None):
        # ssl_certificates (001_init_schema.sql) has no `domain` column (it's
        # domain_id, a FK to domains.id), no `updated_at`, and no
        # `error_message` column -- confirmed live: every call here failed
        # with "Unknown column 'domain' in 'where clause'", silently, since
        # callers don't check this method's (non-existent) return value.
        # error_message is logged (obtain_certificate already does that)
        # but has nowhere to persist to. status is an ENUM('active',
        # 'expired', 'revoked', 'pending') -- 'failed' isn't a valid member;
        # 'pending' is the closest fit for "no working cert yet, will retry".
        conn = self.get_database_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM domains WHERE domain = %s", (domain,))
            row = cursor.fetchone()
            if not row:
                logger.error(f"DB update skipped for {domain}: no matching domains row")
                cursor.close()
                conn.close()
                return
            domain_id = row[0]

            status = "active" if success else "pending"
            now = datetime.now()
            cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
            key_path = f"/etc/letsencrypt/live/{domain}/privkey.pem"

            cursor.execute("SELECT id FROM ssl_certificates WHERE domain_id = %s", (domain_id,))

            if cursor.fetchone():
                cursor.execute(
                    "UPDATE ssl_certificates SET status = %s, certificate_path = %s, "
                    "private_key_path = %s, last_renewed = %s WHERE domain_id = %s",
                    (status, cert_path, key_path, now, domain_id),
                )
            else:
                # id is CHAR(26) with no default/AUTO_INCREMENT -- this
                # schema uses ULID primary keys throughout (009_ulid_safe.sql
                # migrated it from the original INT AUTO_INCREMENT; confirmed
                # live via DESCRIBE, and matches every other table's
                # generate_ulid() convention, e.g. bootstrap.py).
                cursor.execute(
                    "INSERT INTO ssl_certificates "
                    "(id, domain_id, certificate_path, private_key_path, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (str(ULID()), domain_id, cert_path, key_path, status, now),
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

    def process_admin_domains(self):
        """
        Obtain/renew certs for Traefik's own admin-subdomain routers (api,
        docs, grafana, jmap, caldav, autoconfig, the dashboard) -- see the
        __init__ comment on self.admin_subdomains for why these come from
        here rather than Traefik's own certResolver. Deliberately bypasses
        process_domains()'s DB-driven grouping: _group_domains() groups by
        base domain (last two labels), which would incorrectly symlink e.g.
        api.courier.mailyte.com onto courier.mailyte.com's own cert (same
        base "mailyte.com") -- wrong SAN, would break TLS for that hostname.
        Each admin hostname always gets its own independent single-name cert.
        """
        if not self.domain and not self.extra_hostnames:
            logger.debug("DOMAIN and TRAEFIK_EXTRA_HOSTNAMES both unset — skipping")
            return

        # extra_hostnames are already fully qualified; admin_subdomains are not.
        hostnames = [f"{sub}.{self.domain}" for sub in self.admin_subdomains] if self.domain else []
        hostnames += [h for h in self.extra_hostnames if h not in hostnames]
        needing_renewal = [h for h in hostnames if self.check_certificate_expiry(h)]
        if not needing_renewal:
            logger.info("All admin subdomain certificates are valid — no renewals needed")
            return

        logger.info(f"{len(needing_renewal)} admin subdomain(s) need renewal: {needing_renewal}")
        ok = 0
        for hostname in needing_renewal:
            if self.obtain_certificate(hostname, additional_sans=[], _skip_sni_update=True):
                ok += 1

        if ok > 0:
            logger.info(f"Rebuilding SNI/Traefik cert maps after {ok} admin subdomain cert(s)")
            self._update_sni_maps()

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
                self.process_admin_domains()
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
    mgr.process_admin_domains()
    mgr.run()


if __name__ == "__main__":
    main()
