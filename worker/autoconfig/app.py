#!/usr/bin/env python3
"""
Autoconfig / Autodiscover Service

Provides Mozilla Autoconfig, Microsoft Autodiscover, MTA-STS policy,
and DNS record generation endpoints for email client auto-setup.

Endpoints:
  - GET  /mail/config-v1.1.xml                        Mozilla Autoconfig
  - GET  /.well-known/autoconfig/mail/config-v1.1.xml  Mozilla Autoconfig (alt path)
  - POST /autodiscover/autodiscover.xml                 Microsoft Autodiscover POX
  - POST /autodiscover/autodiscover.json                Microsoft Autodiscover V2
  - GET  /mta-sts.txt                                   MTA-STS policy
  - GET  /.well-known/mta-sts.txt                       MTA-STS policy (alt path)
  - GET  /dns-records/{domain}                          DNS record generator
  - GET  /health                                        Health check
"""

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mysql.connector
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from lxml import etree
from mysql.connector import pooling

# Add shared directory to path. Import explicitly from shared: /app precedes
# the appended shared path on sys.path, so a bare `from metrics import ...`
# can silently resolve to the wrong local module -- always use shared.metrics.
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root / "shared"))
from shared.metrics import get_metrics

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "mailserver")
DB_USER = os.getenv("DB_USER", "mailuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
# The hostname mail clients are told to connect to. MAIL_HOSTNAME first,
# because that is the customer-facing name (mail.mailyte.com) and the one the
# certificate is issued for; HOSTNAME is the server's own identity
# (courier.mailyte.com), used for its PTR and SMTP HELO. Handing a client the
# HELO name works only by accident -- both resolve to the same host -- and
# shows customers a name they were never told to expect.
HOSTNAME = os.getenv("MAIL_HOSTNAME") or os.getenv("HOSTNAME", "mail.example.com")
DOMAIN = os.getenv("DOMAIN", "example.com")
MTA_STS_MODE = os.getenv("MTA_STS_MODE", "testing")
PORT = int(os.getenv("PORT", "8100"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("autoconfig")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_pool: pooling.MySQLConnectionPool | None = None


def _get_pool() -> pooling.MySQLConnectionPool:
    """Lazily initialise and return a MySQL connection pool."""
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="autoconfig_pool",
            pool_size=5,
            pool_reset_session=True,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
    return _pool


@contextmanager
def get_db():
    """Context manager that yields a DB connection from the pool."""
    conn = None
    try:
        conn = _get_pool().get_connection()
        yield conn
    except mysql.connector.Error as exc:
        logger.error("Database error: %s", exc)
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def get_mx_hostname(domain: str) -> str:
    """
    Look up the MX hostname for *domain* from the domains table.
    Falls back to the HOSTNAME environment variable.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT domain FROM domains WHERE domain = %s AND active = 1 LIMIT 1",
                (domain,),
            )
            row = cursor.fetchone()
            cursor.close()
            if row:
                # Every hosted domain connects to this shared server, so the
                # client hostname is OUR name -- not mail.<their domain>.
                #
                # This returned f"mail.{domain}" and so handed Thunderbird and
                # Outlook a hostname like mail.techies.africa, which does not
                # exist and was never in any certificate. Auto-setup could not
                # work for a single customer domain. The lookup still matters:
                # it confirms the domain is actually hosted here before
                # handing out settings for it.
                return HOSTNAME
    except Exception:
        logger.warning("Could not query MX for domain %s, using HOSTNAME fallback", domain)
    return HOSTNAME


def get_dkim_record(domain: str) -> dict[str, str] | None:
    """Fetch the active DKIM public key and selector for *domain*."""
    try:
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT dk.selector, dk.public_key
                FROM dkim_keys dk
                JOIN domains d ON dk.domain_id = d.id
                WHERE d.domain = %s AND dk.active = 1
                ORDER BY dk.created_at DESC
                LIMIT 1
                """,
                (domain,),
            )
            row = cursor.fetchone()
            cursor.close()
            return row
    except Exception:
        logger.warning("Could not fetch DKIM record for %s", domain)
    return None


def get_mx_records_for_domain(domain: str) -> list[str]:
    """Return a list of MX hostnames for a domain (used in MTA-STS)."""
    try:
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT domain FROM domains WHERE domain = %s AND active = 1",
                (domain,),
            )
            row = cursor.fetchone()
            cursor.close()
            if row:
                return [f"mail.{row['domain']}"]
    except Exception:
        logger.warning("Could not fetch MX records for %s", domain)
    return [HOSTNAME]


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------


def build_autoconfig_xml(email: str, mx_hostname: str) -> bytes:
    """Build Mozilla Autoconfig XML for the given email address."""
    local_part = email.split("@")[0] if "@" in email else email
    domain = email.split("@")[1] if "@" in email else DOMAIN

    root = etree.Element("clientConfig", version="1.1")
    provider = etree.SubElement(root, "emailProvider", id=domain)
    etree.SubElement(provider, "domain").text = domain
    etree.SubElement(provider, "displayName").text = f"{domain} Mail"
    etree.SubElement(provider, "displayShortName").text = domain

    # IMAP (incoming)
    imap = etree.SubElement(provider, "incomingServer", type="imap")
    etree.SubElement(imap, "hostname").text = mx_hostname
    etree.SubElement(imap, "port").text = "993"
    etree.SubElement(imap, "socketType").text = "SSL"
    etree.SubElement(imap, "authentication").text = "password-cleartext"
    etree.SubElement(imap, "username").text = "%EMAILADDRESS%"

    # POP3 (incoming)
    pop3 = etree.SubElement(provider, "incomingServer", type="pop3")
    etree.SubElement(pop3, "hostname").text = mx_hostname
    etree.SubElement(pop3, "port").text = "995"
    etree.SubElement(pop3, "socketType").text = "SSL"
    etree.SubElement(pop3, "authentication").text = "password-cleartext"
    etree.SubElement(pop3, "username").text = "%EMAILADDRESS%"

    # SMTP (outgoing)
    smtp = etree.SubElement(provider, "outgoingServer", type="smtp")
    etree.SubElement(smtp, "hostname").text = mx_hostname
    etree.SubElement(smtp, "port").text = "587"
    etree.SubElement(smtp, "socketType").text = "STARTTLS"
    etree.SubElement(smtp, "authentication").text = "password-cleartext"
    etree.SubElement(smtp, "username").text = "%EMAILADDRESS%"

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def build_autodiscover_xml(email: str, mx_hostname: str) -> bytes:
    """Build Microsoft Autodiscover POX XML response."""
    AUTODISCOVER_NS = "http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006"
    RESPONSE_NS = "http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a"

    nsmap = {None: AUTODISCOVER_NS}
    root = etree.Element("Autodiscover", nsmap=nsmap)

    resp = etree.SubElement(root, "{%s}Response" % RESPONSE_NS)
    account = etree.SubElement(resp, "{%s}Account" % RESPONSE_NS)
    etree.SubElement(account, "{%s}AccountType" % RESPONSE_NS).text = "email"
    etree.SubElement(account, "{%s}Action" % RESPONSE_NS).text = "settings"

    # IMAP protocol
    imap_proto = etree.SubElement(account, "{%s}Protocol" % RESPONSE_NS)
    etree.SubElement(imap_proto, "{%s}Type" % RESPONSE_NS).text = "IMAP"
    etree.SubElement(imap_proto, "{%s}Server" % RESPONSE_NS).text = mx_hostname
    etree.SubElement(imap_proto, "{%s}Port" % RESPONSE_NS).text = "993"
    etree.SubElement(imap_proto, "{%s}SSL" % RESPONSE_NS).text = "on"
    etree.SubElement(imap_proto, "{%s}LoginName" % RESPONSE_NS).text = email

    # SMTP protocol
    smtp_proto = etree.SubElement(account, "{%s}Protocol" % RESPONSE_NS)
    etree.SubElement(smtp_proto, "{%s}Type" % RESPONSE_NS).text = "SMTP"
    etree.SubElement(smtp_proto, "{%s}Server" % RESPONSE_NS).text = mx_hostname
    etree.SubElement(smtp_proto, "{%s}Port" % RESPONSE_NS).text = "587"
    etree.SubElement(smtp_proto, "{%s}SSL" % RESPONSE_NS).text = "on"
    etree.SubElement(smtp_proto, "{%s}Encryption" % RESPONSE_NS).text = "TLS"
    etree.SubElement(smtp_proto, "{%s}LoginName" % RESPONSE_NS).text = email

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def parse_autodiscover_email(body: bytes) -> str | None:
    """Extract the email address from an Autodiscover POX request body."""
    try:
        tree = etree.fromstring(body)
        # Handle namespaced and non-namespaced requests
        namespaces = {
            "ad": "http://schemas.microsoft.com/exchange/autodiscover/outlook/requestschema/2006",
        }
        node = tree.find(".//ad:EMailAddress", namespaces)
        if node is not None and node.text:
            return node.text.strip()

        # Fallback: try without namespace
        node = tree.find(".//EMailAddress")
        if node is not None and node.text:
            return node.text.strip()
    except etree.XMLSyntaxError:
        logger.warning("Failed to parse Autodiscover XML request")
    return None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Mailyte Autoconfig / Autodiscover Service",
    description="Email client auto-configuration and DNS record helper service",
    version="1.0.0",
)

# Initialize metrics
metrics = get_metrics("autoconfig")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
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


# ----- Mozilla Autoconfig ------------------------------------------------


@app.get("/mail/config-v1.1.xml")
@app.get("/.well-known/autoconfig/mail/config-v1.1.xml")
async def mozilla_autoconfig(emailaddress: str = Query(..., description="Full email address")):
    """
    Mozilla Autoconfig endpoint.

    Returns XML with IMAP (993/SSL), POP3 (995/SSL), and SMTP (587/STARTTLS)
    server configuration for the provided email address.
    """
    if "@" not in emailaddress:
        raise HTTPException(status_code=400, detail="Invalid email address")

    domain = emailaddress.split("@")[1]
    mx_hostname = get_mx_hostname(domain)
    xml_bytes = build_autoconfig_xml(emailaddress, mx_hostname)

    return Response(content=xml_bytes, media_type="application/xml")


# ----- Microsoft Autodiscover POX ----------------------------------------


@app.post("/autodiscover/autodiscover.xml")
async def autodiscover_pox(request: Request):
    """
    Microsoft Autodiscover POX (Plain Old XML) endpoint.

    Parses the incoming XML request for the email address and returns an
    Autodiscover XML response with IMAP and SMTP settings.
    """
    body = await request.body()
    email = parse_autodiscover_email(body)

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Could not extract email from request")

    domain = email.split("@")[1]
    mx_hostname = get_mx_hostname(domain)
    xml_bytes = build_autodiscover_xml(email, mx_hostname)

    return Response(content=xml_bytes, media_type="application/xml")


# ----- Microsoft Autodiscover V2 (JSON) ----------------------------------


@app.post("/autodiscover/autodiscover.json")
async def autodiscover_v2(request: Request):
    """
    Microsoft Autodiscover V2 JSON endpoint.

    Returns a JSON response with Protocol and Url fields pointing to the
    appropriate IMAP server URL.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    email = data.get("EmailAddress", data.get("emailaddress", ""))
    protocol = data.get("Protocol", data.get("protocol", "AutodiscoverV1"))

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="EmailAddress is required")

    domain = email.split("@")[1]
    mx_hostname = get_mx_hostname(domain)

    return JSONResponse(
        content={
            "Protocol": "IMAP",
            "Url": f"imaps://{mx_hostname}:993",
        }
    )


# ----- MTA-STS policy ----------------------------------------------------


def _build_mta_sts_body(domain: str) -> str:
    """Build the MTA-STS policy plain-text body."""
    mx_hosts = get_mx_records_for_domain(domain)
    mx_lines = "\n".join(f"mx: {mx}" for mx in mx_hosts)
    return f"version: STSv1\nmode: {MTA_STS_MODE}\n{mx_lines}\nmax_age: 86400\n"


@app.get("/mta-sts.txt")
@app.get("/.well-known/mta-sts.txt")
async def mta_sts_policy(request: Request):
    """
    MTA-STS policy file.

    Returns a plain-text policy with version, mode, MX patterns, and max_age.
    The mode is controlled by the MTA_STS_MODE environment variable
    (default: testing).
    """
    # Determine domain from Host header or fall back to DOMAIN env
    host = request.headers.get("host", DOMAIN)
    # Strip port if present and remove mta-sts. prefix
    domain = host.split(":")[0]
    if domain.startswith("mta-sts."):
        domain = domain[len("mta-sts.") :]

    body = _build_mta_sts_body(domain)
    return Response(content=body, media_type="text/plain")


# ----- DNS record generator ----------------------------------------------


@app.get("/dns-records/{domain}")
async def dns_records(
    domain: str = PathParam(..., description="Domain name to generate DNS records for"),
):
    """
    Generate all required DNS records for a domain.

    Returns a structured JSON payload with copy-paste-ready values for:
    MX, SPF, DKIM, DMARC, MTA-STS, SRV (autoconfig, caldav, carddav),
    and CNAME records.
    """
    mx_hostname = get_mx_hostname(domain)
    dkim = get_dkim_record(domain)

    # Build DKIM TXT value
    dkim_selector = "default"
    dkim_txt_value = ""
    if dkim:
        dkim_selector = dkim["selector"]
        # Strip PEM headers/footers and whitespace for DNS record
        pub_key = dkim["public_key"]
        pub_key_clean = (
            pub_key.replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\n", "")
            .replace("\r", "")
            .strip()
        )
        dkim_txt_value = f"v=DKIM1; k=rsa; p={pub_key_clean}"

    records: list[dict[str, Any]] = [
        # MX record
        {
            "type": "MX",
            "name": domain,
            "value": mx_hostname,
            "priority": 10,
            "ttl": 3600,
            "description": "Mail exchange record pointing to your mail server",
        },
        # SPF TXT record
        {
            "type": "TXT",
            "name": domain,
            "value": f"v=spf1 mx a:{mx_hostname} ~all",
            "ttl": 3600,
            "description": "SPF record authorising your mail server to send email",
        },
        # DKIM TXT record
        {
            "type": "TXT",
            "name": f"{dkim_selector}._domainkey.{domain}",
            "value": dkim_txt_value
            if dkim_txt_value
            else "(DKIM key not found -- generate one first)",
            "ttl": 3600,
            "description": "DKIM public key for email signing verification",
        },
        # DMARC TXT record
        {
            "type": "TXT",
            "name": f"_dmarc.{domain}",
            # dmarc@ (not dmarc-reports@/dmarc-forensics@): one convention
            # across the platform, matching what mailyte-api publishes and
            # what DmarcReportAliasService provisions a delivering alias for.
            # Three rival conventions meant reports bounced 550 wherever the
            # address nobody provisioned happened to be the published one.
            "value": f"v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}; ruf=mailto:dmarc@{domain}; fo=1",
            "ttl": 3600,
            "description": "DMARC policy for email authentication reporting",
        },
        # MTA-STS TXT record
        {
            "type": "TXT",
            "name": f"_mta-sts.{domain}",
            "value": "v=STSv1; id=20240101000000",
            "ttl": 3600,
            "description": "MTA-STS policy identifier (update id when policy changes)",
        },
        # MTA-STS policy hosting CNAME
        {
            "type": "CNAME",
            "name": f"mta-sts.{domain}",
            "value": mx_hostname,
            "ttl": 3600,
            "description": "CNAME for MTA-STS policy hosting",
        },
        # TLSRPT TXT record
        {
            "type": "TXT",
            "name": f"_smtp._tls.{domain}",
            "value": f"v=TLSRPTv1; rua=mailto:tls-reports@{domain}",
            "ttl": 3600,
            "description": "SMTP TLS Reporting policy",
        },
        # SRV records -- Autoconfig / Autodiscover
        {
            "type": "SRV",
            "name": f"_autodiscover._tcp.{domain}",
            "value": f"0 1 443 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 443,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for Microsoft Autodiscover",
        },
        {
            "type": "SRV",
            "name": f"_imaps._tcp.{domain}",
            "value": f"0 1 993 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 993,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for IMAP over SSL",
        },
        {
            "type": "SRV",
            "name": f"_submission._tcp.{domain}",
            "value": f"0 1 587 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 587,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for SMTP submission (STARTTLS)",
        },
        {
            "type": "SRV",
            "name": f"_pop3s._tcp.{domain}",
            "value": f"0 1 995 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 995,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for POP3 over SSL",
        },
        # CalDAV / CardDAV SRV records
        {
            "type": "SRV",
            "name": f"_caldavs._tcp.{domain}",
            "value": f"0 1 443 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 443,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for CalDAV over SSL",
        },
        {
            "type": "SRV",
            "name": f"_carddavs._tcp.{domain}",
            "value": f"0 1 443 {mx_hostname}",
            "priority": 0,
            "weight": 1,
            "port": 443,
            "target": mx_hostname,
            "ttl": 3600,
            "description": "SRV record for CardDAV over SSL",
        },
        # Autoconfig / Autodiscover CNAME
        {
            "type": "CNAME",
            "name": f"autoconfig.{domain}",
            "value": mx_hostname,
            "ttl": 3600,
            "description": "CNAME for Mozilla Autoconfig endpoint",
        },
        {
            "type": "CNAME",
            "name": f"autodiscover.{domain}",
            "value": mx_hostname,
            "ttl": 3600,
            "description": "CNAME for Microsoft Autodiscover endpoint",
        },
    ]

    return JSONResponse(
        content={
            "domain": domain,
            "mx_hostname": mx_hostname,
            "records": records,
            "total": len(records),
        }
    )


# ----- Health check -------------------------------------------------------


@app.get("/health")
async def health():
    """Basic health check."""
    db_ok = False
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            db_ok = True
    except Exception:
        pass

    status = "healthy" if db_ok else "degraded"
    return JSONResponse(
        content={
            "status": status,
            "service": "autoconfig",
            "database": "connected" if db_ok else "unreachable",
        },
        status_code=200 if db_ok else 503,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Autoconfig/Autodiscover service on port %d", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
