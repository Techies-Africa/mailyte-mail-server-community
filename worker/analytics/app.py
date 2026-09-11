import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector
import redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

# Add shared directory to path. Import explicitly from shared: /app precedes
# the appended shared path on sys.path, so a bare `from metrics import ...`
# can silently resolve to the wrong local module -- always use shared.metrics.
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root / "shared"))
# Import updated database models with organization hierarchy support
from shared.metrics import get_metrics

app = FastAPI(title="Analytics Service")

# Only the report scheduler logs from this module today; the rest of the
# service returns errors to its caller rather than recording them.
#
# basicConfig because this service configured no logging at all: a bare
# getLogger inherits the root logger's default WARNING level and no handler,
# so "Report scheduler started" -- the one line proving the loop is alive --
# went nowhere, and a failed tick would have been silent too.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("analytics")

# Initialize metrics
metrics = get_metrics("analytics")

REPORT_TTL_SECONDS = 90 * 86400  # 90 days


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


templates = Jinja2Templates(directory="templates")


class EmailAnalytics:
    """
    Email Analytics Service with Organization Hierarchy Support

    Provides comprehensive email analytics and reporting capabilities
    with support for the new organization/domain/email account structure.

    Features:
    - Organization-aware analytics and reporting
    - Domain-level statistics within organizations
    - Email account activity monitoring
    - Backward compatibility with existing analytics queries
    - Multi-tenant reporting and data isolation
    """

    def __init__(self):
        self.db_config = {
            "host": os.getenv("DB_HOST"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "database": os.getenv("DB_NAME"),
        }
        try:
            self.redis_client = redis.Redis(
                host=os.getenv("REDIS_HOST", "redis"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                decode_responses=True,
            )
            self.redis_client.ping()
        except Exception:
            self.redis_client = None

    def _connect(self):
        return mysql.connector.connect(**self.db_config)

    def _resolve_domain(self, domain: str) -> tuple | None:
        """Resolve a domain name to (domain_id, organization_id), or None
        if it doesn't exist. Every domain-scoped method below needs this
        first -- the domain-ownership check itself already happened at the
        gateway (verify_domain_scope), this just needs the real ids to
        query with."""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, organization_id FROM domains WHERE domain = %s LIMIT 1", (domain,)
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return None
        return row["id"], row["organization_id"]

    def get_email_stats(self, days=30):
        """Get email statistics for the last N days"""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        start_date = datetime.now() - timedelta(days=days)

        # Daily email counts. mail_logs has no `direction` column -- this
        # previously threw "Unknown column 'direction'" on every call
        # (confirmed live). sender/recipient domain-matching would need a
        # caller-supplied domain to tell inbound from outbound; without one,
        # this endpoint can only honestly report total volume.
        cursor.execute(
            """
            SELECT
                DATE(timestamp) as date,
                COUNT(*) as total_emails
            FROM mail_logs
            WHERE timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY date
        """,
            (start_date,),
        )

        daily_stats = cursor.fetchall()

        # Top domains
        cursor.execute(
            """
            SELECT
                SUBSTRING_INDEX(sender, '@', -1) as domain,
                COUNT(*) as count
            FROM mail_logs
            WHERE timestamp >= %s
            GROUP BY domain
            ORDER BY count DESC
            LIMIT 10
        """,
            (start_date,),
        )

        top_domains = cursor.fetchall()

        # Spam statistics. status is an enum (queued/sending/sent/delivered/
        # bounced/rejected/deferred) with no 'spam' value at all -- `status
        # LIKE '%spam%'` could never match a single row. spam_score is the
        # real column that tracks this.
        cursor.execute(
            """
            SELECT
                DATE(timestamp) as date,
                COUNT(*) as total_spam
            FROM mail_logs
            WHERE timestamp >= %s AND spam_score IS NOT NULL AND spam_score >= 5
            GROUP BY DATE(timestamp)
            ORDER BY date
        """,
            (start_date,),
        )

        spam_stats = cursor.fetchall()

        cursor.close()
        conn.close()

        return {"daily_stats": daily_stats, "top_domains": top_domains, "spam_stats": spam_stats}

    def get_user_activity(self, email, days=7):
        """Get user activity statistics"""
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        start_date = datetime.now() - timedelta(days=days)

        # Same missing `direction` column as get_email_stats -- sent vs
        # received is derived from which side of the message this email
        # address is on instead.
        cursor.execute(
            """
            SELECT
                DATE(timestamp) as date,
                COUNT(*) as email_count,
                SUM(CASE WHEN recipient = %s THEN 1 ELSE 0 END) as received,
                SUM(CASE WHEN sender = %s THEN 1 ELSE 0 END) as sent
            FROM mail_logs
            WHERE (sender = %s OR recipient = %s) AND timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY date
        """,
            (email, email, email, email, start_date),
        )

        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return results

    def get_domain_dashboard(self, domain: str, days: int = 30) -> dict:
        """Aggregate send/delivery/engagement metrics for a domain."""
        resolved = self._resolve_domain(domain)
        if not resolved:
            return None
        domain_id, organization_id = resolved
        start_date = datetime.now() - timedelta(days=days)

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_sent,
                SUM(status = 'delivered') as delivered,
                SUM(status = 'bounced') as bounced,
                SUM(status IN ('rejected', 'deferred')) as failed
            FROM mail_logs
            WHERE organization_id = %s AND sender LIKE %s AND timestamp >= %s
            """,
            (organization_id, f"%@{domain}", start_date),
        )
        send_stats = cursor.fetchone() or {}

        cursor.execute(
            """
            SELECT event_type, COUNT(*) as count
            FROM email_tracking
            WHERE domain_id = %s AND timestamp >= %s
            GROUP BY event_type
            """,
            (domain_id, start_date),
        )
        engagement = {row["event_type"]: row["count"] for row in cursor.fetchall()}

        cursor.close()
        conn.close()

        total_sent = send_stats.get("total_sent") or 0
        return {
            "domain": domain,
            "period_days": days,
            "total_sent": total_sent,
            "delivered": send_stats.get("delivered") or 0,
            "bounced": send_stats.get("bounced") or 0,
            "failed": send_stats.get("failed") or 0,
            "opened": engagement.get("opened", 0),
            "clicked": engagement.get("clicked", 0),
            "complained": engagement.get("complained", 0),
            "unsubscribed": engagement.get("unsubscribed", 0),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_email_volume(self, domain: str, days: int = 30) -> dict:
        """Daily send volume for a domain, split by outbound (sender on
        this domain) vs inbound (recipient on this domain) -- mail_logs has
        no direction column to read this from directly."""
        resolved = self._resolve_domain(domain)
        if not resolved:
            return None
        _domain_id, organization_id = resolved
        start_date = datetime.now() - timedelta(days=days)

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                DATE(timestamp) as date,
                SUM(sender LIKE %s) as outbound,
                SUM(recipient LIKE %s) as inbound
            FROM mail_logs
            WHERE organization_id = %s
              AND (sender LIKE %s OR recipient LIKE %s)
              AND timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY date
            """,
            (
                f"%@{domain}",
                f"%@{domain}",
                organization_id,
                f"%@{domain}",
                f"%@{domain}",
                start_date,
            ),
        )
        volume = cursor.fetchall()
        cursor.close()
        conn.close()

        return {
            "domain": domain,
            "period_days": days,
            "volume": volume,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_engagement(self, domain: str, days: int = 30) -> dict:
        """Open/click engagement rates for a domain."""
        resolved = self._resolve_domain(domain)
        if not resolved:
            return None
        domain_id, organization_id = resolved
        start_date = datetime.now() - timedelta(days=days)

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT COUNT(*) as delivered FROM mail_logs "
            "WHERE organization_id = %s AND sender LIKE %s AND status = 'delivered' "
            "AND timestamp >= %s",
            (organization_id, f"%@{domain}", start_date),
        )
        delivered = (cursor.fetchone() or {}).get("delivered") or 0

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_events,
                SUM(event_type = 'opened') as opens,
                SUM(event_type = 'clicked') as clicks,
                COUNT(DISTINCT CASE WHEN event_type = 'opened' THEN recipient END) as unique_opens,
                COUNT(DISTINCT CASE WHEN event_type = 'clicked' THEN recipient END) as unique_clicks
            FROM email_tracking
            WHERE domain_id = %s AND timestamp >= %s
            """,
            (domain_id, start_date),
        )
        row = cursor.fetchone() or {}
        cursor.close()
        conn.close()

        opens = row.get("opens") or 0
        clicks = row.get("clicks") or 0
        unique_opens = row.get("unique_opens") or 0

        return {
            "domain": domain,
            "period_days": days,
            "delivered": delivered,
            "opens": opens,
            "clicks": clicks,
            "unique_opens": unique_opens,
            "unique_clicks": row.get("unique_clicks") or 0,
            "open_rate": round((unique_opens / delivered * 100), 2) if delivered else 0.0,
            "click_rate": round((row.get("unique_clicks", 0) / delivered * 100), 2)
            if delivered
            else 0.0,
            "click_to_open_rate": round((clicks / opens * 100), 2) if opens else 0.0,
            # Not tracked anywhere in this stack -- no reply-detection or
            # read-time instrumentation exists. Reported as null rather
            # than a fabricated number.
            "reply_rate": None,
            "average_read_time_seconds": None,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_deliverability(self, domain: str, days: int = 30) -> dict:
        """Bounce/complaint rates and DKIM configuration status for a domain."""
        resolved = self._resolve_domain(domain)
        if not resolved:
            return None
        domain_id, organization_id = resolved
        start_date = datetime.now() - timedelta(days=days)

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_sent,
                SUM(status = 'bounced') as bounced,
                AVG(spam_score) as avg_spam_score
            FROM mail_logs
            WHERE organization_id = %s AND sender LIKE %s AND timestamp >= %s
            """,
            (organization_id, f"%@{domain}", start_date),
        )
        send_row = cursor.fetchone() or {}

        cursor.execute(
            "SELECT COUNT(*) as complaints FROM email_tracking "
            "WHERE domain_id = %s AND event_type = 'complained' AND timestamp >= %s",
            (domain_id, start_date),
        )
        complaints = (cursor.fetchone() or {}).get("complaints") or 0

        cursor.execute(
            "SELECT dkim_enabled, dkim_selector FROM domains WHERE id = %s", (domain_id,)
        )
        domain_row = cursor.fetchone() or {}

        cursor.close()
        conn.close()

        total_sent = send_row.get("total_sent") or 0
        bounced = send_row.get("bounced") or 0

        return {
            "domain": domain,
            "period_days": days,
            "total_sent": total_sent,
            "bounced": bounced,
            "bounce_rate": round((bounced / total_sent * 100), 2) if total_sent else 0.0,
            "complaints": complaints,
            "complaint_rate": round((complaints / total_sent * 100), 4) if total_sent else 0.0,
            "average_spam_score": round(send_row.get("avg_spam_score") or 0.0, 2),
            # dkim_enabled reflects local configuration, not a live DNS
            # verification result -- this service has no DNS resolver of
            # its own. SPF/DMARC pass rates and inbox-placement estimates
            # aren't tracked anywhere in this stack (no seed-list testing
            # infrastructure exists) -- reported as null, not guessed.
            "dkim_configured": bool(domain_row.get("dkim_enabled")),
            "dkim_selector": domain_row.get("dkim_selector"),
            "spf_pass_rate": None,
            "dmarc_pass_rate": None,
            "inbox_placement_estimate": None,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_domain_metrics(self, domain: str, days: int = 30) -> dict:
        """Combined message counts + real storage/account usage for a domain."""
        resolved = self._resolve_domain(domain)
        if not resolved:
            return None
        domain_id, organization_id = resolved

        dashboard = self.get_domain_dashboard(domain, days)

        conn = self._connect()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT total_storage_used, total_email_accounts, total_emails, "
            "total_attachments, max_quota, max_users "
            "FROM domains WHERE id = %s",
            (domain_id,),
        )
        domain_row = cursor.fetchone() or {}
        cursor.close()
        conn.close()

        return {
            **dashboard,
            "storage_used_bytes": domain_row.get("total_storage_used") or 0,
            "storage_quota_bytes": domain_row.get("max_quota") or 0,
            "active_email_accounts": domain_row.get("total_email_accounts") or 0,
            "max_email_accounts": domain_row.get("max_users") or 0,
            "lifetime_emails": domain_row.get("total_emails") or 0,
            "lifetime_attachments": domain_row.get("total_attachments") or 0,
        }

    # -- Reports (Redis-backed: no reports table exists anywhere in this
    # database, and this is a worker microservice, not the place to decide
    # on a schema migration -- see the report route docstrings for what
    # this does and doesn't cover) --

    def generate_report(self, domain: str, report_type: str, days: int) -> dict:
        generators = {
            "dashboard": self.get_domain_dashboard,
            "email-volume": self.get_email_volume,
            "engagement": self.get_engagement,
            "deliverability": self.get_deliverability,
            "metrics": self.get_domain_metrics,
        }
        generator = generators.get(report_type)
        if not generator:
            raise ValueError(f"Unknown report_type '{report_type}'")

        data = generator(domain, days)
        if data is None:
            raise LookupError(f"Domain '{domain}' not found")

        report_id = str(uuid.uuid4())
        report = {
            "report_id": report_id,
            "report_type": report_type,
            "domain": domain,
            "period_days": days,
            "status": "completed",
            "data": data,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

        if self.redis_client:
            self.redis_client.setex(f"report:{report_id}", REPORT_TTL_SECONDS, json.dumps(report))

        return report

    def get_report(self, report_id: str) -> dict | None:
        if not self.redis_client:
            return None
        raw = self.redis_client.get(f"report:{report_id}")
        return json.loads(raw) if raw else None

    def list_scheduled_reports(self, organization_id: str) -> list:
        if not self.redis_client:
            return []
        raw = self.redis_client.get(f"scheduled_reports:{organization_id}")
        return json.loads(raw) if raw else []

    def create_scheduled_report(self, organization_id: str, config: dict) -> dict:
        schedules = self.list_scheduled_reports(organization_id)
        entry = {
            "schedule_id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "domain": config.get("domain"),
            "report_type": config.get("report_type", "dashboard"),
            "interval": config.get("interval", "weekly"),
            "recipients": config.get("recipients", []),
            "created_at": datetime.utcnow().isoformat() + "Z",
            # Never run yet. The scheduler treats null as "due now" so a new
            # schedule proves itself on the next tick rather than after a
            # silent week.
            "last_run_at": None,
        }
        schedules.append(entry)
        if self.redis_client:
            self.redis_client.set(f"scheduled_reports:{organization_id}", json.dumps(schedules))
        return entry


analytics = EmailAnalytics()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "analytics"}


@app.get("/stats")
def get_stats(days: int = Query(default=30)):
    stats = analytics.get_email_stats(days)
    return stats


@app.get("/user-activity/{email}")
def get_user_activity(email: str, days: int = Query(default=7)):
    activity = analytics.get_user_activity(email, days)
    return activity


@app.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/analytics/dashboard/{domain}")
def domain_dashboard(domain: str, days: int = Query(default=30)):
    data = analytics.get_domain_dashboard(domain, days)
    if data is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return data


@app.get("/analytics/email-volume/{domain}")
def domain_email_volume(domain: str, days: int = Query(default=30)):
    data = analytics.get_email_volume(domain, days)
    if data is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return data


@app.get("/analytics/engagement/{domain}")
def domain_engagement(domain: str, days: int = Query(default=30)):
    data = analytics.get_engagement(domain, days)
    if data is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return data


@app.get("/analytics/deliverability/{domain}")
def domain_deliverability(domain: str, days: int = Query(default=30)):
    data = analytics.get_deliverability(domain, days)
    if data is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return data


@app.get("/analytics/metrics/{domain}")
def domain_metrics(domain: str, days: int = Query(default=30)):
    data = analytics.get_domain_metrics(domain, days)
    if data is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return data


@app.post("/reports/generate")
async def reports_generate(request: Request):
    """
    Generate an analytics report for a domain.

    Body: {"domain": "example.com", "report_type": "dashboard"|"email-volume"|
    "engagement"|"deliverability"|"metrics", "days": 30}

    Generation is synchronous (these are all single-table aggregations, not
    a heavy batch job) and the result is stored in Redis for 90 days,
    retrievable by report_id via GET /reports/{report_id}. There is no
    persistent SQL table for reports anywhere in this database -- adding
    one is a schema decision this pass didn't have a mandate to make.
    """
    try:
        body = await request.json()
        domain = body.get("domain")
        report_type = body.get("report_type", "dashboard")
        days = int(body.get("days", 30))

        if not domain:
            return JSONResponse({"error": "Missing domain"}, status_code=400)

        report = analytics.generate_report(domain, report_type, days)
        return JSONResponse(report, status_code=200)

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except LookupError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate report: {e}"}, status_code=500)


@app.get("/reports/scheduled")
async def reports_scheduled_list(request: Request):
    """
    List scheduled report configurations for the caller's organization.

    Query param: organization_id (required)

    Stores real configuration (Redis-backed, no expiry) -- but be aware
    there is no background scheduler/executor process anywhere in this
    stack that actually runs these on their interval yet. This endpoint
    persists the configuration; it does not (yet) deliver anything
    automatically.

    Declared before GET /reports/{report_id} below on purpose -- FastAPI
    matches routes in declaration order, so with the generic {report_id}
    route first, a request for /reports/scheduled always matched that one
    instead, with "scheduled" bound to report_id (confirmed live via the
    gateway, which had the exact same ordering bug).
    """
    organization_id = request.query_params.get("organization_id")
    if not organization_id:
        return JSONResponse({"error": "Missing organization_id"}, status_code=400)
    schedules = analytics.list_scheduled_reports(organization_id)
    return {"schedules": schedules, "count": len(schedules)}


@app.post("/reports/scheduled")
async def reports_scheduled_create(request: Request):
    """
    Create a scheduled report configuration (see reports_scheduled_list's
    docstring re: no executor exists to actually run this yet).

    Body: {"organization_id": "...", "domain": "...", "report_type": "dashboard",
    "interval": "daily"|"weekly"|"monthly", "recipients": ["a@b.com"]}
    """
    try:
        body = await request.json()
        organization_id = body.get("organization_id")
        if not organization_id:
            return JSONResponse({"error": "Missing organization_id"}, status_code=400)
        if not body.get("domain"):
            return JSONResponse({"error": "Missing domain"}, status_code=400)

        entry = analytics.create_scheduled_report(organization_id, body)
        return JSONResponse(entry, status_code=201)

    except Exception as e:
        return JSONResponse({"error": f"Failed to create schedule: {e}"}, status_code=500)


@app.get("/reports/{report_id}")
async def reports_get(report_id: str):
    """Retrieve a previously generated report. 90-day retention (Redis TTL)."""
    report = analytics.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or expired")
    return report


# ---------------------------------------------------------------------------
# Scheduled report delivery
# ---------------------------------------------------------------------------
#
# Schedules could be created and listed, and nothing ever ran them: this
# service had list_scheduled_reports and create_scheduled_report and no
# scheduler, so a recurring digest was configured and never arrived. Reports
# generated on demand always worked, which made the gap easy to miss -- the
# feature looked alive from the one path anybody tested.
#
# No advisory lock here, unlike the alert evaluator: `analytics` is a singleton
# (deploy.sh replicates only api/webhooks/tracking), so exactly one of these
# runs by construction.

_SCHEDULE_INTERVALS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}

_REPORT_TICK_SECONDS = int(os.getenv("REPORT_SCHEDULER_INTERVAL_SECONDS", "900"))


def _schedule_is_due(entry: dict, now: datetime) -> bool:
    period = _SCHEDULE_INTERVALS.get(entry.get("interval", "weekly"))
    if period is None:
        return False
    last = entry.get("last_run_at")
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last.rstrip("Z")) >= period
    except ValueError:
        # An unparseable timestamp must not wedge the schedule forever.
        return True


def _email_report(recipients: list, entry: dict, report: dict) -> tuple:
    """Send one report. Returns (delivered, error)."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        return False, "SMTP_HOST is not set on the analytics service"
    from_address = (
        os.getenv("REPORT_FROM_ADDRESS", "").strip() or os.getenv("ADMIN_EMAIL", "").strip()
    )
    if not from_address:
        return False, "REPORT_FROM_ADDRESS/ADMIN_EMAIL is not set"

    try:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = (
            f"{entry.get('report_type', 'dashboard')} report for {entry.get('domain')}"
        )
        message["From"] = from_address
        message["To"] = ", ".join(recipients)
        # The report as JSON rather than a rendered document. Honest about what
        # this is: the same payload the on-demand endpoint returns. A formatted
        # PDF is a separate piece of work, and shipping an empty-looking HTML
        # skeleton would be worse than shipping the data.
        message.set_content(
            f"Scheduled {entry.get('interval')} report for {entry.get('domain')}.\n\n"
            + json.dumps(report.get("data", report), indent=2, default=str)
        )
        with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "25")), timeout=15) as smtp:
            smtp.send_message(message)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _run_due_schedules(now: datetime) -> None:
    if not analytics.redis_client:
        return
    for key in analytics.redis_client.scan_iter("scheduled_reports:*"):
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            entries = json.loads(analytics.redis_client.get(key_str) or "[]")
        except (ValueError, TypeError):
            continue

        changed = False
        for entry in entries:
            if not _schedule_is_due(entry, now):
                continue
            try:
                report = analytics.generate_report(
                    entry.get("domain"), entry.get("report_type", "dashboard"), 7
                )
            except Exception as exc:
                logger.error(f"Scheduled report {entry.get('schedule_id')} failed: {exc}")
                # Stamped even on failure, so one broken schedule (a deleted
                # domain, say) does not retry every tick forever.
                entry["last_run_at"] = now.isoformat() + "Z"
                entry["last_error"] = str(exc)[:300]
                changed = True
                continue

            recipients = entry.get("recipients") or []
            if recipients:
                delivered, error = _email_report(recipients, entry, report)
                entry["last_error"] = None if delivered else (error or "")[:300]
                if not delivered:
                    logger.warning(
                        f"Scheduled report {entry.get('schedule_id')} not delivered: {error}"
                    )
            else:
                entry["last_error"] = "no recipients configured"

            entry["last_run_at"] = now.isoformat() + "Z"
            changed = True

        if changed:
            analytics.redis_client.set(key_str, json.dumps(entries))


@app.on_event("startup")
async def start_report_scheduler():
    async def loop():
        while True:
            await asyncio.sleep(_REPORT_TICK_SECONDS)
            try:
                await run_in_threadpool(_run_due_schedules, datetime.utcnow())
            except Exception as exc:
                # The loop must outlive a bad tick.
                logger.error(f"Report scheduler tick failed: {exc}")

    try:
        asyncio.create_task(loop())
        logger.info("Report scheduler started")
    except Exception as exc:
        logger.warning(f"Report scheduler failed to start (non-fatal): {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8085)
