"""
Sieve Filtering & Mail Rules API — Per-User Email Filters

Manages Sieve scripts for email filtering, auto-reply, forwarding, and folder routing.
Dovecot ManageSieve (port 4190) stores scripts on the filesystem; this API provides
a REST interface for CRUD operations on those scripts.

Endpoints:
  GET    /filters                 — List user's Sieve scripts
  GET    /filters/{name}          — Get a specific script
  POST   /filters                 — Create/update a Sieve script
  DELETE /filters/{name}          — Delete a Sieve script
  PUT    /filters/{name}/activate — Set as active script
  GET    /filters/templates       — List pre-built filter templates
  POST   /filters/vacation        — Manage vacation responder
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import os
import json
import sys
from pathlib import Path

from utils.db import get_db

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.webhook_dispatcher import dispatch_event, Events

logger = logging.getLogger(__name__)
router = APIRouter()

# Dovecot mail storage base
VHOSTS_DIR = os.getenv('VHOSTS_DIR', '/var/mail/vhosts')


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class SieveScript(BaseModel):
    name: str = Field(..., description="Unique name for this filter script", example="move-newsletters")
    content: str = Field(..., description="Sieve script content", example='require "fileinto"; if header :contains "List-Unsubscribe" "" { fileinto "Newsletters"; }')
    active: bool = Field(False, description="Whether to activate this script immediately")

class SieveScriptResponse(BaseModel):
    name: str
    content: str
    active: bool
    size: int
    created_at: Optional[str] = None

class VacationRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable the vacation responder")
    subject: Optional[str] = Field("Out of Office", description="Auto-reply subject line")
    message: Optional[str] = Field(None, description="Auto-reply message body")
    start_date: Optional[str] = Field(None, description="Start date (ISO 8601)", example="2026-04-01")
    end_date: Optional[str] = Field(None, description="End date (ISO 8601)", example="2026-04-15")
    reply_interval: int = Field(86400, description="Minimum seconds between replies to same sender")
    external_only: bool = Field(False, description="Only reply to external senders")

class FilterTemplate(BaseModel):
    id: str
    name: str
    description: str
    sieve_content: str


# ---------------------------------------------------------------------------
# Pre-built filter templates
# ---------------------------------------------------------------------------
FILTER_TEMPLATES = [
    FilterTemplate(
        id="forward",
        name="Forward All Mail",
        description="Forward all incoming mail to another address (keeping a local copy)",
        sieve_content='require ["copy"];\nredirect :copy "destination@example.com";'
    ),
    FilterTemplate(
        id="auto_reply",
        name="Auto-Reply",
        description="Send automatic reply to incoming messages",
        sieve_content='require ["vacation"];\nvacation :days 1 :subject "Auto-Reply" "Thank you for your email. I will respond shortly.";'
    ),
    FilterTemplate(
        id="move_subject",
        name="Move by Subject",
        description="Move emails with specific subject keywords to a folder",
        sieve_content='require ["fileinto"];\nif header :contains "subject" "KEYWORD" {\n  fileinto "FolderName";\n  stop;\n}'
    ),
    FilterTemplate(
        id="move_sender",
        name="Move by Sender",
        description="Move emails from specific senders to a folder",
        sieve_content='require ["fileinto"];\nif address :is "from" "sender@example.com" {\n  fileinto "FolderName";\n  stop;\n}'
    ),
    FilterTemplate(
        id="reject_sender",
        name="Block Sender",
        description="Reject emails from a specific sender",
        sieve_content='require ["reject"];\nif address :is "from" "spammer@example.com" {\n  reject "Your message has been rejected.";\n  stop;\n}'
    ),
    FilterTemplate(
        id="attachment_filter",
        name="Move Attachments",
        description="Move emails with attachments to a specific folder",
        sieve_content='require ["fileinto"];\nif header :contains "Content-Type" "multipart/mixed" {\n  fileinto "Attachments";\n  stop;\n}'
    ),
]


# ---------------------------------------------------------------------------
# Helper: Sieve file paths
# ---------------------------------------------------------------------------
def _get_sieve_dir(email: str) -> str:
    """Get the Sieve directory for a user email address."""
    domain = email.split('@')[1]
    local_part = email.split('@')[0]
    return os.path.join(VHOSTS_DIR, domain, local_part, 'sieve')


def _get_active_link(email: str) -> str:
    """Get the active Sieve symlink path."""
    domain = email.split('@')[1]
    local_part = email.split('@')[0]
    return os.path.join(VHOSTS_DIR, domain, local_part, '.dovecot.sieve')


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=List[SieveScriptResponse],
    summary="List Sieve filter scripts",
    description="List all Sieve scripts for a user, including each script's content, size, creation date, and whether it is the currently active script.",
)
async def list_filters(
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """List all Sieve scripts for a user."""
    # Verify user exists
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM email_accounts WHERE email = %s AND status = 'active'", (email,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail=f"Account {email} not found")
    cursor.close()

    sieve_dir = _get_sieve_dir(email)
    active_link = _get_active_link(email)
    scripts = []

    # Get the active script name
    active_name = None
    if os.path.islink(active_link):
        active_target = os.readlink(active_link)
        active_name = os.path.basename(active_target).replace('.sieve', '')

    if os.path.isdir(sieve_dir):
        for f in os.listdir(sieve_dir):
            if f.endswith('.sieve'):
                name = f.replace('.sieve', '')
                filepath = os.path.join(sieve_dir, f)
                with open(filepath, 'r') as fh:
                    content = fh.read()
                stat = os.stat(filepath)
                scripts.append(SieveScriptResponse(
                    name=name,
                    content=content,
                    active=(name == active_name),
                    size=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_ctime).isoformat()
                ))

    return scripts


@router.get(
    "/templates",
    response_model=List[FilterTemplate],
    summary="List filter templates",
    description="Return the collection of pre-built Sieve filter templates (forward, auto-reply, move by subject/sender, block sender, attachment filter) that users can customize.",
)
async def list_templates():
    """List pre-built Sieve filter templates."""
    return FILTER_TEMPLATES


@router.get(
    "/{name}",
    response_model=SieveScriptResponse,
    summary="Get a Sieve script",
    description="Retrieve a specific Sieve script by name, including its content, size, creation date, and active status.",
)
async def get_filter(
    name: str,
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """Get a specific Sieve script by name."""
    sieve_dir = _get_sieve_dir(email)
    filepath = os.path.join(sieve_dir, f"{name}.sieve")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found")

    with open(filepath, 'r') as f:
        content = f.read()

    active_link = _get_active_link(email)
    active_name = None
    if os.path.islink(active_link):
        active_target = os.readlink(active_link)
        active_name = os.path.basename(active_target).replace('.sieve', '')

    stat = os.stat(filepath)
    return SieveScriptResponse(
        name=name,
        content=content,
        active=(name == active_name),
        size=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_ctime).isoformat()
    )


@router.post(
    "/",
    summary="Create or update a Sieve script",
    description="Create or overwrite a Sieve script for the specified user. Script names must be alphanumeric (with hyphens/underscores). If 'active' is true the script is immediately set as the active Dovecot Sieve script.",
)
async def create_filter(
    script: SieveScript,
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """Create or update a Sieve script for a user."""
    # Verify user exists
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM email_accounts WHERE email = %s AND status = 'active'", (email,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail=f"Account {email} not found")
    cursor.close()

    # Validate script name (alphanumeric + hyphens + underscores only)
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', script.name):
        raise HTTPException(status_code=400, detail="Script name must be alphanumeric (with hyphens/underscores)")

    sieve_dir = _get_sieve_dir(email)
    os.makedirs(sieve_dir, exist_ok=True)

    filepath = os.path.join(sieve_dir, f"{script.name}.sieve")
    with open(filepath, 'w') as f:
        f.write(script.content)

    # Set ownership (vmail:vmail = 5000:5000)
    os.chown(filepath, 5000, 5000)

    # If this script should be active, update the symlink
    if script.active:
        active_link = _get_active_link(email)
        if os.path.exists(active_link) or os.path.islink(active_link):
            os.unlink(active_link)
        os.symlink(filepath, active_link)
        os.lchown(active_link, 5000, 5000)

    dispatch_event(
        Events.FILTER_CREATED,
        data={"script_name": script.name, "email": email, "active": script.active},
        source_service="api",
    )

    return {"status": "ok", "message": f"Script '{script.name}' saved", "active": script.active}


@router.delete(
    "/{name}",
    summary="Delete a Sieve script",
    description="Delete a Sieve script by name. If the deleted script was the active script, the active symlink is also removed. The compiled .sievec file is cleaned up as well.",
)
async def delete_filter(
    name: str,
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """Delete a Sieve script."""
    sieve_dir = _get_sieve_dir(email)
    filepath = os.path.join(sieve_dir, f"{name}.sieve")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found")

    # Check if this is the active script
    active_link = _get_active_link(email)
    if os.path.islink(active_link):
        active_target = os.readlink(active_link)
        if os.path.basename(active_target) == f"{name}.sieve":
            os.unlink(active_link)

    os.unlink(filepath)
    # Also remove compiled version if exists
    compiled = filepath + 'c'  # .sievec
    if os.path.exists(compiled):
        os.unlink(compiled)

    dispatch_event(
        Events.FILTER_DELETED,
        data={"script_name": name, "email": email},
        source_service="api",
    )

    return {"status": "ok", "message": f"Script '{name}' deleted"}


@router.put(
    "/{name}/activate",
    summary="Activate a Sieve script",
    description="Set the specified Sieve script as the active script for the user by updating the Dovecot .dovecot.sieve symlink.",
)
async def activate_filter(
    name: str,
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """Set a Sieve script as the active script."""
    sieve_dir = _get_sieve_dir(email)
    filepath = os.path.join(sieve_dir, f"{name}.sieve")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found")

    active_link = _get_active_link(email)
    if os.path.exists(active_link) or os.path.islink(active_link):
        os.unlink(active_link)
    os.symlink(filepath, active_link)
    os.lchown(active_link, 5000, 5000)

    dispatch_event(
        Events.FILTER_UPDATED,
        data={"script_name": name, "email": email, "action": "activated"},
        source_service="api",
    )

    return {"status": "ok", "message": f"Script '{name}' is now active"}


@router.post(
    "/vacation",
    summary="Manage vacation responder",
    description="Enable or disable the vacation auto-reply for a user. When enabled, a Sieve vacation script is generated with optional start/end dates and set as the active script. The database vacation_enabled flag is updated accordingly.",
)
async def manage_vacation(
    vacation: VacationRequest,
    email: str = Query(..., description="User email address"),
    db=Depends(get_db)
):
    """Manage vacation responder (auto-reply) via Sieve."""
    # Verify user exists
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, vacation_enabled FROM email_accounts WHERE email = %s AND status = 'active'", (email,))
    account = cursor.fetchone()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {email} not found")

    if vacation.enabled:
        # Build vacation Sieve script
        extensions = ['vacation']
        conditions = []

        sieve = 'require ["vacation"'
        if vacation.start_date or vacation.end_date:
            sieve = 'require ["vacation", "date", "relational"'
        sieve += '];\n\n'

        # Date conditions
        if vacation.start_date:
            sieve += f'if currentdate :value "ge" "date" "{vacation.start_date}" {{\n'
            conditions.append('start')
        if vacation.end_date:
            if conditions:
                sieve += f'  if currentdate :value "le" "date" "{vacation.end_date}" {{\n'
            else:
                sieve += f'if currentdate :value "le" "date" "{vacation.end_date}" {{\n'
            conditions.append('end')

        # Vacation action
        indent = '  ' * len(conditions)
        days = max(1, vacation.reply_interval // 86400)
        sieve += f'{indent}vacation :days {days} :subject "{vacation.subject}" "{vacation.message}";\n'

        # Close conditions
        for _ in conditions:
            indent = '  ' * (len(conditions) - 1)
            sieve += f'{indent}}}\n'
            conditions.pop()

        # Save as vacation script
        sieve_dir = _get_sieve_dir(email)
        os.makedirs(sieve_dir, exist_ok=True)
        filepath = os.path.join(sieve_dir, 'vacation.sieve')
        with open(filepath, 'w') as f:
            f.write(sieve)
        os.chown(filepath, 5000, 5000)

        # Activate it
        active_link = _get_active_link(email)
        if os.path.exists(active_link) or os.path.islink(active_link):
            os.unlink(active_link)
        os.symlink(filepath, active_link)
        os.lchown(active_link, 5000, 5000)

    # Update database
    cursor.execute(
        "UPDATE email_accounts SET vacation_enabled = %s, vacation_message = %s WHERE id = %s",
        (vacation.enabled, vacation.message if vacation.enabled else None, account['id'])
    )
    db.commit()
    cursor.close()

    status = "enabled" if vacation.enabled else "disabled"
    return {"status": "ok", "message": f"Vacation responder {status}", "enabled": vacation.enabled}
