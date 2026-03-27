---
title: Adding Features
description: How to add a new feature to Mailyte — from creating a route to writing tests and updating docs.
---

# Adding Features

This walks through the process of adding a feature end to end. We'll use a concrete example: adding an endpoint to export mailbox data.

## The Workflow

```mermaid
graph LR
    A[Branch] --> B[Model/Schema]
    B --> C[API Route]
    C --> D[Business Logic]
    D --> E[Tests]
    E --> F[Docs]
    F --> G[PR]
```

1. Create a feature branch
2. Add or update the database model (if needed)
3. Create the API route
4. Implement the business logic
5. Write tests
6. Update docs
7. Open a pull request

## Step 1: Create a Branch

```bash
git checkout -b feature/mailbox-export
```

Follow the branch naming convention: `feature/short-description`, `fix/issue-description`, `chore/task-name`.

## Step 2: Database Changes (If Needed)

If your feature needs new tables or columns, create an Alembic migration:

```bash
python manage.py migrate:create "add mailbox export tracking"
```

This creates a new file in `alembic/versions/`. Edit it:

```python
# alembic/versions/xxx_add_mailbox_export_tracking.py
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'mailbox_exports',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('email_account_id', sa.Integer, sa.ForeignKey('email_accounts.id')),
        sa.Column('organization_id', sa.String(100), sa.ForeignKey('organizations.id')),
        sa.Column('format', sa.String(50), default='mbox'),
        sa.Column('status', sa.Enum('pending', 'processing', 'completed', 'failed')),
        sa.Column('file_path', sa.String(500)),
        sa.Column('file_size', sa.BigInteger),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime),
    )

def downgrade():
    op.drop_table('mailbox_exports')
```

Run the migration:

```bash
python manage.py migrate:run
```

## Step 3: Create the API Route

Add your route in the appropriate module under `worker/api/`:

```python
# worker/api/routes/exports.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/v1", tags=["exports"])

class ExportRequest(BaseModel):
    email: str
    format: Optional[str] = "mbox"

class ExportResponse(BaseModel):
    type: str
    msg: str
    export_id: Optional[int] = None

@router.post("/export/mailbox", response_model=ExportResponse)
async def export_mailbox(request: ExportRequest, api_key=Depends(verify_api_key)):
    """Start an asynchronous mailbox export."""
    # Verify the mailbox exists
    mailbox = await get_mailbox(request.email)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")

    # Start the export
    export_id = await create_export(
        email_account_id=mailbox.id,
        organization_id=mailbox.organization_id,
        format=request.format,
    )

    return ExportResponse(
        type="success",
        msg=f"Export started for {request.email}",
        export_id=export_id,
    )

@router.get("/export/mailbox/{export_id}")
async def get_export_status(export_id: int, api_key=Depends(verify_api_key)):
    """Check the status of a mailbox export."""
    export = await get_export(export_id)
    if not export:
        raise HTTPException(status_code=404, detail="export_not_found")

    return {
        "type": "success",
        "export_id": export.id,
        "status": export.status,
        "file_size": export.file_size,
        "created_at": export.created_at.isoformat(),
    }
```

### Register the Route

Add it to the main API router:

```python
# worker/api/main.py (or wherever routes are registered)
from routes.exports import router as exports_router
app.include_router(exports_router)
```

## Step 4: Business Logic

Keep business logic separate from route handlers. Create a service module:

```python
# worker/api/services/export_service.py
import asyncio
from database import get_db

async def create_export(email_account_id: int, organization_id: str, format: str) -> int:
    """Create an export job and queue it for processing."""
    db = get_db()

    result = db.execute(
        "INSERT INTO mailbox_exports (email_account_id, organization_id, format, status) "
        "VALUES (%s, %s, %s, 'pending')",
        (email_account_id, organization_id, format)
    )
    export_id = result.lastrowid

    # Queue the export for async processing
    await queue_export_job(export_id)

    return export_id

async def queue_export_job(export_id: int):
    """Push the export job to the queue for a worker to pick up."""
    import redis
    r = redis.Redis(host='redis', port=6379)
    r.lpush('mailyte:export_jobs', str(export_id))
```

## Step 5: Write Tests

Create a test file:

```python
# tests/test_exports.py
import pytest
from unittest.mock import patch, MagicMock

class TestMailboxExport:
    def test_start_export(self, client, api_key_header):
        """Test starting a mailbox export."""
        response = client.post(
            "/api/v1/export/mailbox",
            headers=api_key_header,
            json={"email": "user@example.com", "format": "mbox"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "success"
        assert "export_id" in data

    def test_export_nonexistent_mailbox(self, client, api_key_header):
        """Test exporting a mailbox that doesn't exist."""
        response = client.post(
            "/api/v1/export/mailbox",
            headers=api_key_header,
            json={"email": "nonexistent@example.com"}
        )
        assert response.status_code == 404

    def test_get_export_status(self, client, api_key_header):
        """Test checking export status."""
        # First create an export
        create_resp = client.post(
            "/api/v1/export/mailbox",
            headers=api_key_header,
            json={"email": "user@example.com"}
        )
        export_id = create_resp.json()["export_id"]

        # Then check its status
        status_resp = client.get(
            f"/api/v1/export/mailbox/{export_id}",
            headers=api_key_header,
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "pending"

    def test_export_requires_auth(self, client):
        """Test that export requires an API key."""
        response = client.post(
            "/api/v1/export/mailbox",
            json={"email": "user@example.com"}
        )
        assert response.status_code == 401
```

Run the tests:

```bash
pytest tests/test_exports.py -v
```

## Step 6: Update Docs

Add the new endpoint to the API reference:

1. Update `docs/reference/api-endpoints.md` with the new endpoints
2. If it's a major feature, add a section in the relevant feature docs
3. Update the changelog

## Step 7: Open a PR

```bash
git add .
git commit -m "feat: add mailbox export API endpoint"
git push origin feature/mailbox-export
```

Then open a pull request. See [Contributing](contributing.md) for the full PR process.

## Common Patterns

### Adding a New Worker

See [Custom Workers](custom-workers.md) for the full guide.

### Adding a New Configuration Option

1. Add the env var to `docker-compose.yml`
2. Read it in the relevant service
3. Document it in `docs/reference/environment-variables.md`
4. Add a default value

### Adding a Database Index

If queries are slow, add an index via migration:

```python
def upgrade():
    op.create_index('idx_new_index', 'table_name', ['column1', 'column2'])
```
