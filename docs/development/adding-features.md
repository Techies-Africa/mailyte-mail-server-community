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
6. Update docs (and `openapi.json`)
7. Open a pull request

## Step 1: Create a Branch

```bash
git checkout develop
git checkout -b feature/mailbox-export
```

Follow the branch naming convention: `feature/short-description`, `fix/issue-description`, `chore/task-name`.

## Step 2: Database Changes (If Needed)

If your feature needs new tables or columns, create an Alembic migration:

```bash
python manage.py migrate:create "add mailbox export tracking"
```

This creates a new sequentially-numbered file in `alembic/versions/`. Edit it:

```python
# alembic/versions/00XX_add_mailbox_export_tracking.py
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "mailbox_exports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("email_account_id", sa.Integer, sa.ForeignKey("email_accounts.id")),
        sa.Column("organization_id", sa.String(100), sa.ForeignKey("organizations.id")),
        sa.Column("format", sa.String(50), default="mbox"),
        sa.Column("status", sa.Enum("pending", "processing", "completed", "failed")),
        sa.Column("file_path", sa.String(500)),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime),
    )


def downgrade():
    op.drop_table("mailbox_exports")
```

Run the migration locally:

```bash
python manage.py migrate
```

!!! warning "Your migration must survive CI's Alembic gates"
    The `validate-migrations` CI job runs `alembic upgrade head` on a fresh database, re-runs it (must be a no-op), then seeds data, downgrades, and re-upgrades — a migration that loses seeded rows fails the build. Always write a real `downgrade()`.

!!! warning "Rebuild the migrate image in Docker dev"
    The `migrate` compose service bakes `alembic/` into its image. If you (or a teammate pulling your branch) run the stack without rebuilding it, the stale image silently no-ops and the database stays behind the code. `docker compose build migrate` first.

## Step 3: Create the API Route

Add a route module under `worker/api/routes/`. Routers do **not** set their own prefix — the prefix is applied when the module is registered:

```python
# worker/api/routes/exports.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from utils.auth import require_api_key

router = APIRouter()


class ExportRequest(BaseModel):
    email: str
    format: str = "mbox"


class ExportResponse(BaseModel):
    type: str
    msg: str
    export_id: int | None = None


@router.post("/mailbox", response_model=ExportResponse)
async def export_mailbox(request: ExportRequest, auth: dict = Depends(require_api_key("write"))):
    """Start an asynchronous mailbox export."""
    mailbox = await get_mailbox(request.email)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")

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
```

!!! note "Every endpoint needs a `response_model`"
    CI counts endpoints whose 200/201 response has no typed schema and compares against `openapi-untyped-baseline.txt` — the count may only shrink. A new endpoint without a `response_model` fails the build (or forces you to type an existing endpoint to offset it).

### Register the Route

Routes are registered declaratively in the `route_modules` list near the bottom of `worker/api/app.py` — add a `(module_name, prefix, tag)` tuple:

```python
# worker/api/app.py
route_modules = [
    # ... existing entries ...
    ("exports", "/api/v1/export", "Exports"),
]
```

Order matters only when one prefix is a string-prefix of another (the more specific mount must come first — see the `platform_auth`/`platform` comment in `app.py`).

## Step 4: Business Logic

Keep business logic separate from route handlers — the API gateway keeps helpers in `worker/api/utils/`, larger workers use a `services/` package:

```python
# worker/api/utils/export_service.py
async def create_export(email_account_id: int, organization_id: str, format: str) -> int:
    """Create an export job and queue it for processing."""
    db = get_db()

    result = db.execute(
        "INSERT INTO mailbox_exports (email_account_id, organization_id, format, status) "
        "VALUES (%s, %s, %s, 'pending')",
        (email_account_id, organization_id, format),
    )
    export_id = result.lastrowid

    # Queue the export for async processing
    await queue_export_job(export_id)

    return export_id
```

!!! danger "Don't block the event loop"
    An `async def` endpoint runs on the shared event loop — synchronous DB or network calls inside it freeze the **entire** API process for every caller. Either make the handler a plain `def` (FastAPI runs it in a threadpool) or use genuinely async I/O.

If your feature fires notifications, dispatch through the global webhook dispatcher rather than making HTTP calls yourself:

```python
from shared.webhook_dispatcher import dispatch_event

dispatch_event("mailbox.export.completed", {"export_id": export_id}, org_id=org_id)
```

## Step 5: Write Tests

Unit tests go in `tests/unit/` (no Docker, no database — use the mock fixtures from `tests/conftest.py`); live-stack tests go in `tests/integration/`:

```python
# tests/unit/test_exports.py
class TestMailboxExport:
    def test_export_nonexistent_mailbox_returns_404(self, mock_db_connection):
        mock_conn, mock_cursor = mock_db_connection
        mock_cursor.fetchone.return_value = None
        ...

    def test_export_requires_auth(self): ...
```

Run them:

```bash
pytest tests/unit/test_exports.py -v
```

See [Testing](testing.md) for the fixture list, the CI coverage ratchet (changed lines need ≥ 60% coverage), and how to run the integration suite.

## Step 6: Update Docs and the OpenAPI Spec

1. Regenerate the committed `openapi.json` — CI fails if it doesn't match the live spec:

    ```bash
    DB_PASSWORD=local-openapi-regen-placeholder \
      python -c "import json; from worker.api.app import app; json.dump(app.openapi(), open('openapi.json','w'), indent=2, sort_keys=True)"
    ```

    (Importing the app runs `startup_checks.py`, which rejects missing or short secret values — hence the 16+ character placeholder; nothing actually connects to a database.)

    Route modules must not touch the database or require `DB_*` env at **import time** (build engines lazily, on first use — see `routes/smtp_credentials.py` for the pattern). `app.py` skips any route module that fails to import, and CI's OpenAPI contract job now hard-fails if any module in `route_modules` did not register — an import-time `create_engine()` once made every `/smtp-credentials` path silently vanish from the spec.

2. Update `docs/reference/api-endpoints.md` with the new endpoints
3. If it's a major feature, add a section in the relevant feature docs
4. Update the changelog

## Step 7: Open a PR

```bash
git add .
git commit -m "feat: add mailbox export API endpoint"
git push origin feature/mailbox-export
```

Then open a pull request. See [Contributing](contributing.md) for the full PR process and the list of CI gates it must pass.

## Common Patterns

### Adding a New Worker

See [Custom Workers](custom-workers.md) for the full guide.

### Adding a New Configuration Option

1. Add the env var to `docker-compose.yml` (and `.env.example` if user-facing)
2. Read it in the relevant service
3. Document it in `docs/reference/environment-variables.md`
4. Add a safe default value — but never for secrets: the `secrets-check` container fails the stack on weak or missing secret values by design

### Adding a Database Index

If queries are slow, add an index via migration:

```python
def upgrade():
    op.create_index("idx_new_index", "table_name", ["column1", "column2"])
```
