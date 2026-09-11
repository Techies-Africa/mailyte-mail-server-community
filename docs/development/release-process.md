---
title: Release Process
description: How releases work — versioning, changelog, CI gates, the Deploy workflow, and rollback.
---

# Release Process

This documents how we version, build, and deploy Mailyte releases.

## Branches and Pipelines

- **`develop`** is the working branch — CI (`.github/workflows/ci.yml`) runs on every push
- **`main`** is the release branch — PRs into `main` run CI, and **production deploys must be launched from `main`** (the Deploy workflow enforces this)
- Deploys are driven by **`.github/workflows/deploy.yml`**, a manually-triggered (`workflow_dispatch`) pipeline

!!! note "Staging auto-deploy is currently disabled"
    Auto-deploy to staging on every merge to `develop` was disabled on 2026-07-31: the AWS/SSH secrets and the GitHub "staging" Environment it needs don't exist yet (ops phase-02). Until they do, all deploys — staging included — are manual `workflow_dispatch` runs.

## Versioning

We use [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, tracked in `pyproject.toml` (`[project] version`).

| Bump | When | Example |
|------|------|---------|
| **MAJOR** | Breaking API changes, schema migrations that require manual steps | 1.0.0 -> 2.0.0 |
| **MINOR** | New features, backward-compatible | 1.0.0 -> 1.1.0 |
| **PATCH** | Bug fixes, security patches | 1.0.0 -> 1.0.1 |

Deployed **images** are additionally tagged per run by the Deploy workflow as `{environment}-{short-sha}-{timestamp}` — that tag, not the semver, is what identifies exactly what's running and what you roll back to.

## Release Checklist

- [ ] All CI gates green on the release commit (lint/type baselines, tests, coverage ratchet, OpenAPI contract, Alembic validation)
- [ ] `CHANGELOG.md` updated
- [ ] Version bumped in `pyproject.toml`
- [ ] Database migrations tested up **and** down (CI's `validate-migrations` job proves fresh-install, idempotency, and upgrade-with-data)
- [ ] Docker images build (CI's build matrix covers every service)
- [ ] Documentation updated for new features
- [ ] Security scan results reviewed (Bandit / pip-audit job)

## Step 1: Prepare the Release

### Update the Changelog

Add a section for the new version at the top of `CHANGELOG.md`:

```markdown
## [1.2.0] - 2026-08-30

### Added
- Mailbox export API endpoint (#123)

### Fixed
- Webhook delivery retry logic not respecting backoff (#125)

### Changed
- Upgraded Postfix base image to 3.8
```

### Bump the Version

```toml
# pyproject.toml
[project]
version = "1.2.0"
```

### Commit and Merge to main

```bash
git add CHANGELOG.md pyproject.toml
git commit -m "chore: prepare release 1.2.0"
```

Open a PR from `develop` into `main` and merge once CI is green.

## Step 2: Tag the Release

```bash
VERSION="1.2.0"
git checkout main && git pull
git tag -a "v${VERSION}" -m "Release ${VERSION}"
git push origin "v${VERSION}"

gh release create "v${VERSION}" --title "v${VERSION}" --generate-notes
```

## Step 3: Run the Deploy Workflow

Deploys are one workflow run — building, pushing, and rolling out are not manual steps:

```bash
gh workflow run deploy.yml \
  -f environment=production \
  -f services=all          # or a comma-separated subset, e.g. "api,tracking"
```

Optional inputs: `skip_backup=true` (skip the pre-deploy backup), `dry_run=true` (build images only, deploy nothing).

What the workflow does, in order:

1. **Preflight** — validates compose config, generates the image tag, and refuses a production deploy that isn't launched from `main`
2. **CI verification** — re-runs the full CI workflow
3. **Pre-deployment backup** (production only) — SSHes to the server and runs `scripts/backup.sh --full`
4. **Build & push** — builds each requested service image and pushes it to Amazon ECR as `mailyte-{service}:{tag}` and `:latest`
5. **Deploy** — SSHes to the target, pulls the new images, and rolls services one at a time with `docker compose up -d --no-deps`, then waits and reports any unhealthy containers
6. **Verify & notify** — polls the API `/health` endpoint and posts the result to Slack

### Database Migrations During Deploys

Schema is owned by the `migrate` compose service: it runs `alembic upgrade head` (via `scripts/run_migrations.py`) once per `docker compose up`, before dependent services start — a failed migration stops the deploy rather than letting a half-migrated API come up.

!!! warning "The migrate image must be rebuilt to see new migrations"
    `migrate` bakes `alembic/` into its image at build time. A deploy that pulls new code but reuses an old migrate image silently no-ops the migration step while reporting success. Ensure the migrate image is rebuilt as part of any release that includes migrations.

### Verify Manually

```bash
# On the server (or via the published ports)
docker compose ps
curl -s http://localhost:8083/health

# Spot-check worker health endpoints (host-mapped ports)
for port in 8081 8082 8083 8085 8086 8087 8088; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health)
  echo "Port $port: $status"
done
```

## Rollback

Every deploy's images stay in ECR under their immutable `{environment}-{sha}-{timestamp}` tag. To roll back:

1. Find the previous run's tag (in the Deploy workflow logs or ECR)
2. On the server: pull that tag for the affected services, re-tag it as what compose expects, and `docker compose up -d --no-deps <service>` — the same mechanics as step 5 above
3. If a migration must be undone:

```bash
python manage.py migrate:rollback            # last migration
python manage.py migrate:rollback --steps=2  # or N
```

Only roll back migrations that are genuinely reversible — CI proves `downgrade()` works with seeded data, but data written by the *new* code into *new* columns is still lost by a downgrade.

## Hotfix Process

For urgent fixes in production:

1. Branch from the release tag: `git checkout -b hotfix/1.2.1 v1.2.0`
2. Fix the issue and write a test
3. Bump the patch version and update the changelog
4. PR into `main`, tag, and run the Deploy workflow with only the affected `services`
5. Merge the fix back to `develop` so it isn't lost
