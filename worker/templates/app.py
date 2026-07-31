"""
Email Templates Service

Template management with Jinja2 rendering, version control,
HTML + plaintext dual rendering, and usage analytics.

Endpoints:
  GET    /health
  POST   /templates                         — Create a new template
  GET    /templates?org_id=                  — List templates for org
  GET    /templates/{template_id}            — Get template with content
  PUT    /templates/{template_id}            — Update template
  DELETE /templates/{template_id}            — Delete template
  POST   /templates/{template_id}/render     — Render template with variables
  POST   /templates/{template_id}/duplicate  — Duplicate a template
  GET    /templates/{template_id}/versions   — Get version history
  GET    /templates/library                  — Pre-built template library
  GET    /templates/{template_id}/stats      — Template usage stats
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import os
import json
import hashlib
import mysql.connector
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, UndefinedError
from html.parser import HTMLParser
from io import StringIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Email Templates Service", version="1.0.0")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "mysql"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME", "mailserver"),
    "user": os.getenv("DB_USER", "mailuser"),
    "password": os.getenv("DB_PASSWORD", "mailpassword"),
    "charset": "utf8mb4",
}

jinja_env = Environment(loader=BaseLoader(), autoescape=True)


# ---------------------------------------------------------------------------
# HTML → Plaintext converter
# ---------------------------------------------------------------------------


class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = StringIO()
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "br":
            self.result.write("\n")
        elif tag in ("p", "div", "tr", "li"):
            self.result.write("\n")
        elif tag == "a":
            for attr_name, attr_val in attrs:
                if attr_name == "href":
                    self.result.write(f" [{attr_val}] ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.result.write("\n")

    def handle_data(self, data):
        if not self._skip:
            self.result.write(data)

    def get_text(self):
        return self.result.getvalue().strip()


def html_to_plaintext(html: str) -> str:
    stripper = HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class TemplateCreate(BaseModel):
    organization_id: str
    name: str = Field(..., description="Template name")
    description: Optional[str] = None
    category: str = Field(
        "general", description="Category: transactional, marketing, notification, general"
    )
    subject_template: str = Field(..., description="Subject line (supports Jinja2 variables)")
    html_content: str = Field(..., description="HTML body (supports Jinja2 variables)")
    plaintext_content: Optional[str] = Field(
        None, description="Plaintext body (auto-generated from HTML if empty)"
    )
    variables: List[str] = Field(
        default_factory=list, description="List of variable names used in template"
    )
    metadata: Optional[Dict[str, Any]] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    subject_template: Optional[str] = None
    html_content: Optional[str] = None
    plaintext_content: Optional[str] = None
    variables: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class RenderRequest(BaseModel):
    variables: Dict[str, Any] = Field(..., description="Template variable values")
    format: str = Field("both", description="Output format: html, plaintext, both")


class TemplateResponse(BaseModel):
    id: int
    organization_id: str
    name: str
    description: Optional[str]
    category: str
    subject_template: str
    html_content: str
    plaintext_content: Optional[str]
    variables: List[str]
    version: int
    render_count: int
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Pre-built Template Library
# ---------------------------------------------------------------------------

TEMPLATE_LIBRARY = [
    {
        "id": "welcome",
        "name": "Welcome Email",
        "description": "New user welcome email with getting started guide",
        "category": "transactional",
        "subject_template": "Welcome to {{ company_name }}, {{ first_name }}!",
        "html_content": """<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <h1 style="color: #2563eb;">Welcome, {{ first_name }}!</h1>
  <p>We're thrilled to have you join {{ company_name }}.</p>
  <p>Here's what you can do next:</p>
  <ul>
    <li>Set up your profile</li>
    <li>Explore your inbox</li>
    <li>Configure email filters</li>
  </ul>
  <a href="{{ login_url }}" style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px;">Get Started</a>
  <p style="color: #6b7280; font-size: 12px; margin-top: 32px;">{{ company_name }} — {{ company_address }}</p>
</div>""",
        "variables": ["first_name", "company_name", "login_url", "company_address"],
    },
    {
        "id": "password_reset",
        "name": "Password Reset",
        "description": "Password reset request with secure link",
        "category": "transactional",
        "subject_template": "Reset your {{ company_name }} password",
        "html_content": """<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <h2>Password Reset Request</h2>
  <p>Hi {{ first_name }},</p>
  <p>We received a request to reset your password. Click the button below to create a new password:</p>
  <a href="{{ reset_url }}" style="display: inline-block; padding: 12px 24px; background: #dc2626; color: white; text-decoration: none; border-radius: 6px;">Reset Password</a>
  <p style="color: #6b7280; font-size: 13px;">This link expires in {{ expiry_hours }} hours. If you didn't request this, please ignore this email.</p>
  <p style="color: #6b7280; font-size: 12px; margin-top: 32px;">{{ company_name }}</p>
</div>""",
        "variables": ["first_name", "company_name", "reset_url", "expiry_hours"],
    },
    {
        "id": "invoice",
        "name": "Invoice / Receipt",
        "description": "Payment receipt or invoice email",
        "category": "transactional",
        "subject_template": "Invoice #{{ invoice_number }} from {{ company_name }}",
        "html_content": """<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <h2>Invoice #{{ invoice_number }}</h2>
  <p>Hi {{ customer_name }},</p>
  <p>Thank you for your payment.</p>
  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    <tr style="border-bottom: 1px solid #e5e7eb;"><td style="padding: 8px;">Plan</td><td style="padding: 8px; text-align: right;">{{ plan_name }}</td></tr>
    <tr style="border-bottom: 1px solid #e5e7eb;"><td style="padding: 8px;">Period</td><td style="padding: 8px; text-align: right;">{{ billing_period }}</td></tr>
    <tr style="font-weight: bold;"><td style="padding: 8px;">Total</td><td style="padding: 8px; text-align: right;">{{ amount }}</td></tr>
  </table>
  <a href="{{ invoice_url }}" style="display: inline-block; padding: 10px 20px; background: #059669; color: white; text-decoration: none; border-radius: 6px;">View Invoice</a>
</div>""",
        "variables": [
            "invoice_number",
            "customer_name",
            "company_name",
            "plan_name",
            "billing_period",
            "amount",
            "invoice_url",
        ],
    },
    {
        "id": "newsletter",
        "name": "Newsletter",
        "description": "Newsletter / marketing email layout",
        "category": "marketing",
        "subject_template": "{{ subject_line }}",
        "html_content": """<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <div style="text-align: center; padding: 24px;"><img src="{{ logo_url }}" alt="{{ company_name }}" style="max-height: 48px;"></div>
  <h1 style="text-align: center;">{{ headline }}</h1>
  <p>{{ body_text }}</p>
  {% if cta_text and cta_url %}
  <div style="text-align: center; margin: 24px 0;">
    <a href="{{ cta_url }}" style="display: inline-block; padding: 14px 28px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px; font-size: 16px;">{{ cta_text }}</a>
  </div>
  {% endif %}
  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">
  <p style="color: #6b7280; font-size: 12px; text-align: center;">
    {{ company_name }} — {{ company_address }}<br>
    <a href="{{ unsubscribe_url }}">Unsubscribe</a>
  </p>
</div>""",
        "variables": [
            "subject_line",
            "company_name",
            "logo_url",
            "headline",
            "body_text",
            "cta_text",
            "cta_url",
            "company_address",
            "unsubscribe_url",
        ],
    },
    {
        "id": "notification",
        "name": "System Notification",
        "description": "Simple system notification (alerts, status updates)",
        "category": "notification",
        "subject_template": "[{{ severity }}] {{ title }}",
        "html_content": """<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <div style="padding: 12px 16px; background: {% if severity == 'critical' %}#dc2626{% elif severity == 'warning' %}#f59e0b{% else %}#2563eb{% endif %}; color: white; border-radius: 6px 6px 0 0;">
    <strong>{{ severity | upper }}: {{ title }}</strong>
  </div>
  <div style="padding: 16px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 6px 6px;">
    <p>{{ message }}</p>
    {% if action_url %}
    <a href="{{ action_url }}" style="display: inline-block; padding: 8px 16px; background: #374151; color: white; text-decoration: none; border-radius: 4px;">{{ action_text | default('View Details') }}</a>
    {% endif %}
    <p style="color: #6b7280; font-size: 12px; margin-top: 16px;">{{ timestamp }}</p>
  </div>
</div>""",
        "variables": ["severity", "title", "message", "action_url", "action_text", "timestamp"],
    },
]


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------


def _ensure_tables():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_templates (
            id INT AUTO_INCREMENT PRIMARY KEY,
            organization_id VARCHAR(100) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT NULL,
            category VARCHAR(50) NOT NULL DEFAULT 'general',
            subject_template TEXT NOT NULL,
            html_content MEDIUMTEXT NOT NULL,
            plaintext_content MEDIUMTEXT NULL,
            variables JSON NULL,
            metadata JSON NULL,
            version INT NOT NULL DEFAULT 1,
            content_hash VARCHAR(64) NOT NULL,
            render_count BIGINT NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_tpl_org (organization_id),
            INDEX idx_tpl_category (category),
            INDEX idx_tpl_name (organization_id, name),
            INDEX idx_tpl_active (active)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_template_versions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            template_id INT NOT NULL,
            version INT NOT NULL,
            subject_template TEXT NOT NULL,
            html_content MEDIUMTEXT NOT NULL,
            plaintext_content MEDIUMTEXT NULL,
            variables JSON NULL,
            content_hash VARCHAR(64) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_tpl_ver (template_id, version),
            CONSTRAINT fk_tpl_ver FOREIGN KEY (template_id)
                REFERENCES email_templates(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS template_render_log (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            template_id INT NOT NULL,
            organization_id VARCHAR(100) NOT NULL,
            rendered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            recipient VARCHAR(255) NULL,
            INDEX idx_render_tpl (template_id),
            INDEX idx_render_org (organization_id),
            INDEX idx_render_date (rendered_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    conn.commit()
    cursor.close()
    conn.close()


def _content_hash(subject: str, html: str) -> str:
    return hashlib.sha256(f"{subject}|{html}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/templates", response_model=TemplateResponse)
async def create_template(tpl: TemplateCreate):
    """Create a new email template."""
    # Validate Jinja2 syntax
    try:
        jinja_env.parse(tpl.subject_template)
        jinja_env.parse(tpl.html_content)
    except TemplateSyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Template syntax error: {e}")

    plaintext = tpl.plaintext_content or html_to_plaintext(tpl.html_content)
    content_h = _content_hash(tpl.subject_template, tpl.html_content)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        INSERT INTO email_templates
        (organization_id, name, description, category, subject_template,
         html_content, plaintext_content, variables, metadata, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
        (
            tpl.organization_id,
            tpl.name,
            tpl.description,
            tpl.category,
            tpl.subject_template,
            tpl.html_content,
            plaintext,
            json.dumps(tpl.variables),
            json.dumps(tpl.metadata) if tpl.metadata else None,
            content_h,
        ),
    )
    tpl_id = cursor.lastrowid

    # Save initial version
    cursor.execute(
        """
        INSERT INTO email_template_versions
        (template_id, version, subject_template, html_content, plaintext_content, variables, content_hash)
        VALUES (%s, 1, %s, %s, %s, %s, %s)
    """,
        (
            tpl_id,
            tpl.subject_template,
            tpl.html_content,
            plaintext,
            json.dumps(tpl.variables),
            content_h,
        ),
    )

    conn.commit()

    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (tpl_id,))
    created = cursor.fetchone()
    cursor.close()
    conn.close()

    return _row_to_response(created)


@app.get("/templates", response_model=List[TemplateResponse])
async def list_templates(
    organization_id: str = Query(...),
    category: Optional[str] = Query(None),
    active_only: bool = Query(True),
):
    """List templates for an organization."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    query = "SELECT * FROM email_templates WHERE organization_id = %s"
    params: list = [organization_id]
    if category:
        query += " AND category = %s"
        params.append(category)
    if active_only:
        query += " AND active = TRUE"
    query += " ORDER BY updated_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [_row_to_response(r) for r in rows]


@app.get("/templates/library")
async def get_template_library():
    """Get pre-built template library."""
    return TEMPLATE_LIBRARY


@app.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: int):
    """Get a specific template."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (template_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return _row_to_response(row)


@app.put("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(template_id: int, update: TemplateUpdate):
    """Update a template (creates a new version)."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (template_id,))
    existing = cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")

    # Merge updates
    name = update.name or existing["name"]
    desc = update.description if update.description is not None else existing.get("description")
    cat = update.category or existing["category"]
    subject = update.subject_template or existing["subject_template"]
    html = update.html_content or existing["html_content"]
    plaintext = update.plaintext_content or (
        html_to_plaintext(html) if update.html_content else existing.get("plaintext_content")
    )
    variables = (
        update.variables
        if update.variables is not None
        else (
            json.loads(existing["variables"])
            if isinstance(existing.get("variables"), str)
            else existing.get("variables") or []
        )
    )
    metadata = (
        update.metadata
        if update.metadata is not None
        else (
            json.loads(existing["metadata"])
            if isinstance(existing.get("metadata"), str)
            else existing.get("metadata")
        )
    )

    # Validate
    try:
        jinja_env.parse(subject)
        jinja_env.parse(html)
    except TemplateSyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Template syntax error: {e}")

    new_version = existing["version"] + 1
    content_h = _content_hash(subject, html)

    cursor.execute(
        """
        UPDATE email_templates SET
            name=%s, description=%s, category=%s, subject_template=%s,
            html_content=%s, plaintext_content=%s, variables=%s, metadata=%s,
            version=%s, content_hash=%s, updated_at=NOW()
        WHERE id=%s
    """,
        (
            name,
            desc,
            cat,
            subject,
            html,
            plaintext,
            json.dumps(variables),
            json.dumps(metadata) if metadata else None,
            new_version,
            content_h,
            template_id,
        ),
    )

    # Save version
    cursor.execute(
        """
        INSERT INTO email_template_versions
        (template_id, version, subject_template, html_content, plaintext_content, variables, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """,
        (template_id, new_version, subject, html, plaintext, json.dumps(variables), content_h),
    )

    conn.commit()

    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (template_id,))
    updated = cursor.fetchone()
    cursor.close()
    conn.close()

    return _row_to_response(updated)


@app.delete("/templates/{template_id}")
async def delete_template(template_id: int):
    """Soft-delete a template."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("UPDATE email_templates SET active = FALSE WHERE id = %s", (template_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok", "message": "Template deleted"}


@app.post("/templates/{template_id}/render")
async def render_template(template_id: int, req: RenderRequest):
    """Render a template with provided variables."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM email_templates WHERE id = %s AND active = TRUE", (template_id,))
    tpl = cursor.fetchone()

    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    try:
        subject_tpl = jinja_env.from_string(tpl["subject_template"])
        subject = subject_tpl.render(**req.variables)

        result = {"subject": subject}

        if req.format in ("html", "both"):
            html_tpl = jinja_env.from_string(tpl["html_content"])
            result["html"] = html_tpl.render(**req.variables)

        if req.format in ("plaintext", "both"):
            pt_content = tpl.get("plaintext_content") or html_to_plaintext(tpl["html_content"])
            pt_tpl = jinja_env.from_string(pt_content)
            result["plaintext"] = pt_tpl.render(**req.variables)

    except UndefinedError as e:
        raise HTTPException(status_code=400, detail=f"Missing variable: {e}")
    except TemplateSyntaxError as e:
        raise HTTPException(status_code=500, detail=f"Template syntax error: {e}")

    # Increment render count
    cursor.execute(
        "UPDATE email_templates SET render_count = render_count + 1 WHERE id = %s", (template_id,)
    )

    # Log render
    cursor.execute(
        """
        INSERT INTO template_render_log (template_id, organization_id, recipient)
        VALUES (%s, %s, %s)
    """,
        (
            template_id,
            tpl["organization_id"],
            req.variables.get("email", req.variables.get("recipient")),
        ),
    )

    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "ok", **result}


@app.post("/templates/{template_id}/duplicate")
async def duplicate_template(template_id: int, new_name: str = Query(...)):
    """Duplicate a template with a new name."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (template_id,))
    original = cursor.fetchone()

    if not original:
        raise HTTPException(status_code=404, detail="Template not found")

    content_h = _content_hash(original["subject_template"], original["html_content"])
    variables = original.get("variables")
    if isinstance(variables, str):
        variables = variables
    else:
        variables = json.dumps(variables or [])

    cursor.execute(
        """
        INSERT INTO email_templates
        (organization_id, name, description, category, subject_template,
         html_content, plaintext_content, variables, metadata, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
        (
            original["organization_id"],
            new_name,
            f"Copy of {original['name']}",
            original["category"],
            original["subject_template"],
            original["html_content"],
            original.get("plaintext_content"),
            variables,
            original.get("metadata"),
            content_h,
        ),
    )
    new_id = cursor.lastrowid
    conn.commit()

    cursor.execute("SELECT * FROM email_templates WHERE id = %s", (new_id,))
    created = cursor.fetchone()
    cursor.close()
    conn.close()

    return _row_to_response(created)


@app.get("/templates/{template_id}/versions")
async def get_template_versions(template_id: int):
    """Get version history for a template."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, version, content_hash, created_at FROM email_template_versions WHERE template_id = %s ORDER BY version DESC",
        (template_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            "id": r["id"],
            "version": r["version"],
            "content_hash": r["content_hash"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
        }
        for r in rows
    ]


@app.get("/templates/{template_id}/stats")
async def get_template_stats(template_id: int):
    """Get usage statistics for a template."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT render_count, created_at FROM email_templates WHERE id = %s", (template_id,)
    )
    tpl = cursor.fetchone()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    # Recent renders (last 30 days by day)
    cursor.execute(
        """
        SELECT DATE(rendered_at) as day, COUNT(*) as count
        FROM template_render_log
        WHERE template_id = %s AND rendered_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY DATE(rendered_at) ORDER BY day
    """,
        (template_id,),
    )
    daily = cursor.fetchall()

    cursor.close()
    conn.close()

    return {
        "template_id": template_id,
        "total_renders": tpl["render_count"],
        "created_at": tpl["created_at"].isoformat() if tpl.get("created_at") else "",
        "daily_renders": [{"date": str(d["day"]), "count": d["count"]} for d in daily],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(r: dict) -> TemplateResponse:
    variables = r.get("variables")
    if isinstance(variables, str):
        variables = json.loads(variables)
    return TemplateResponse(
        id=r["id"],
        organization_id=r["organization_id"],
        name=r["name"],
        description=r.get("description"),
        category=r["category"],
        subject_template=r["subject_template"],
        html_content=r["html_content"],
        plaintext_content=r.get("plaintext_content"),
        variables=variables or [],
        version=r.get("version", 1),
        render_count=r.get("render_count", 0),
        created_at=r["created_at"].isoformat() if r.get("created_at") else "",
        updated_at=r["updated_at"].isoformat() if r.get("updated_at") else "",
    )


# ---------------------------------------------------------------------------
# Health & Startup
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "templates", "library_templates": len(TEMPLATE_LIBRARY)}


@app.on_event("startup")
async def startup():
    _ensure_tables()
    logger.info("Templates service started")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8089))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
