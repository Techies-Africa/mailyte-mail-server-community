"""
One-Time Bootstrap API (phase-02 task 2.4)

Resolves the bootstrap paradox: you need an API key to call the API, but a
fresh install has none yet. app.py's startup hook writes a single-use
bootstrap token (stdout + /app/data/bootstrap-token, mode 0600) whenever no
organizations exist. POST /api/v1/bootstrap accepts that token via
X-Bootstrap-Token and creates the first organization, domain, mailbox, API
key, and (phase-03) dashboard user -- entirely through this API, no direct
SQL from any shell script.

The only direct-SQL-adjacent step left anywhere in the provisioning path is
this endpoint reading api_keys itself to decide whether bootstrap is still
available, and the raw INSERT into api_keys (there is no ORM model for
api_keys -- utils/auth.py's own auth check reads that table the same way).
"""

import hashlib
import logging
import os
import re
import secrets
import sys
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from utils.auth import create_api_response, hash_password, validate_password_strength
from utils.database import get_db_connection

from database.models.authentication import User
from database.models.core import Domain, EmailAccount, Organization
from database.models.enums import AccountStatus

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
from shared.ulid_utils import generate_ulid
from shared.webhook_dispatcher import Events, dispatch_event

logger = logging.getLogger(__name__)
router = APIRouter()

BOOTSTRAP_TOKEN_PATH = "/app/data/bootstrap-token"


def get_db_session():
    """Get a SQLAlchemy session (matches the pattern in domains.py/organizations.py)."""
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    Session = sessionmaker(bind=engine)
    return Session()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BootstrapRequest(BaseModel):
    organization_name: str = Field(
        ..., min_length=1, max_length=255, description="Display name for the first organization"
    )
    admin_email: str = Field(
        ..., description="Admin mailbox to create -- its domain part becomes the first domain"
    )
    admin_password: str = Field(
        ...,
        description="Password for the admin mailbox (utils.auth.validate_password_strength's policy)",
    )

    @field_validator("admin_email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        # Plain str + regex rather than pydantic's EmailStr -- EmailStr
        # requires the email-validator package, which is not installed
        # (verified: importing a model that uses EmailStr crashes at class
        # definition time, i.e. at API startup, taking down every route).
        if not _EMAIL_RE.match(v):
            raise ValueError("admin_email must be a valid email address")
        return v

    @field_validator("admin_password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        valid, message = validate_password_strength(v)
        if not valid:
            raise ValueError(message)
        return v


@router.post(
    "/",
    summary="One-time platform bootstrap",
    description="Creates the first organization, domain, mailbox, API key, and dashboard user on a "
    "fresh install. The admin_email/admin_password pair logs into both the mailbox "
    "(IMAP/webmail) and mailyte-web's community-mode dashboard (POST /api/v1/auth/login). "
    "Requires the single-use bootstrap token (X-Bootstrap-Token) that app.py writes to "
    "stdout and /app/data/bootstrap-token at startup when no organization exists yet. "
    "Refuses with 409 if any organization already exists, regardless of token validity.",
)
async def bootstrap(
    body: BootstrapRequest, x_bootstrap_token: str = Header(..., alias="X-Bootstrap-Token")
):
    # The real single-use gate is "does any organization exist" -- not just
    # the token file's presence. This is robust to the file being deleted,
    # copied around, or the container restarting mid-bootstrap.
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Database connection failed")
        )
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM organizations")
        (org_count,) = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if org_count > 0:
        raise HTTPException(
            status_code=409,
            detail=create_api_response(
                "error", "Already bootstrapped -- an organization already exists"
            ),
        )

    if not os.path.isfile(BOOTSTRAP_TOKEN_PATH):
        raise HTTPException(
            status_code=409, detail=create_api_response("error", "Bootstrap is not available")
        )

    with open(BOOTSTRAP_TOKEN_PATH) as f:
        expected_token = f.read().strip()

    if not expected_token or not secrets.compare_digest(x_bootstrap_token, expected_token):
        raise HTTPException(
            status_code=401, detail=create_api_response("error", "Invalid bootstrap token")
        )

    domain_name = body.admin_email.split("@", 1)[1].lower()
    local_part = body.admin_email.split("@", 1)[0]

    session = get_db_session()
    try:
        org = Organization(name=body.organization_name, admin_email=body.admin_email, active=True)
        session.add(org)
        session.flush()  # populate org.id (ULID default) before it's referenced below
        org_id = org.id

        domain = Domain(domain=domain_name, organization_id=org_id, active=True)
        session.add(domain)
        session.flush()
        domain_id = domain.id

        # Same password for both -- the admin mailbox (webmail/IMAP) and the
        # phase-03 dashboard login are the same person's one credential at
        # bootstrap time. hash_password() is bcrypt, computed once and
        # reused rather than hashing the same plaintext twice.
        password_hash = hash_password(body.admin_password)

        mailbox = EmailAccount(
            email=body.admin_email,
            local_part=local_part,
            domain_id=domain_id,
            organization_id=org_id,
            password=password_hash,
            name="Admin",
            status=AccountStatus.ACTIVE,
        )
        session.add(mailbox)

        # phase-03: without this, there is no row in `users` for anyone to
        # log into mailyte-web with -- bootstrap previously only created an
        # api_keys row (a machine credential), never a browser-login user.
        dashboard_user = User(
            organization_id=org_id,
            email=body.admin_email,
            password_hash=password_hash,
            role="admin",
        )
        session.add(dashboard_user)
        session.commit()
        # Deliberately capture every value we need (org_id, domain_id above;
        # domain_name/body.admin_email are already plain strings) BEFORE this
        # point and never touch the ORM objects again after commit.
        # EmailAccount.status is a SQLAlchemy Enum column whose write and
        # read paths disagree: it writes the member's lowercase .value
        # ("active"), but default deserialization matches against member
        # NAMES ("ACTIVE") -- so session.commit()'s expire_on_commit
        # triggering a post-commit refresh on access (e.g. mailbox.email)
        # re-runs that broken read path and raises, even though the commit
        # itself already succeeded. Verified live: the row lands in MySQL
        # correctly; only the post-commit attribute read blows up. This is
        # a pre-existing bug in database/models/core.py / enums.py, out of
        # scope for phase-02 -- sidestepping it here (never read the ORM
        # objects after commit) is sufficient and correct.
    except Exception as exc:
        session.rollback()
        logger.error(f"Bootstrap failed while creating org/domain/mailbox: {exc}")
        raise HTTPException(
            status_code=500, detail=create_api_response("error", "Bootstrap failed")
        )
    finally:
        session.close()

    api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    api_key_id = generate_ulid()
    # key_id is a non-secret display identifier (prefix + row ULID); the raw
    # key exists only in this response. Auth matches on key_hash.
    api_key_display_id = f"{api_key[:8]}_{api_key_id}"

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_keys (id, key_id, key_hash, name, permissions, organization_id, active)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            """,
            (
                api_key_id,
                api_key_display_id,
                api_key_hash,
                f"{body.organization_name}-key",
                '{"read": true, "write": true}',
                org_id,
            ),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()

    # Invalidate the bootstrap token -- single use, regardless of what
    # happens next.
    try:
        os.remove(BOOTSTRAP_TOKEN_PATH)
    except OSError:
        pass

    dispatch_event(
        Events.ORG_CREATED,
        data={
            "organization_id": org_id,
            "name": body.organization_name,
            "admin_email": body.admin_email,
        },
        org_id=org_id,
        source_service="api",
    )

    logger.info(
        f"Bootstrap complete: organization={org_id} domain={domain_name} mailbox={body.admin_email}"
    )

    return create_api_response(
        "success",
        "Bootstrap complete",
        {
            "organization_id": org_id,
            "domain": domain_name,
            "mailbox": body.admin_email,
            "api_key": api_key,
        },
    )
