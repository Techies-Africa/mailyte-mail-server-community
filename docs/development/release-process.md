---
title: Release Process
description: How releases work — versioning, changelog, building Docker images, tagging, and deployment.
---

# Release Process

This documents how we version, build, and deploy Mailyte releases.

## Versioning

We use [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

| Bump | When | Example |
|------|------|---------|
| **MAJOR** | Breaking API changes, schema migrations that require manual steps | 1.0.0 -> 2.0.0 |
| **MINOR** | New features, backward-compatible | 1.0.0 -> 1.1.0 |
| **PATCH** | Bug fixes, security patches | 1.0.0 -> 1.0.1 |

Pre-release versions use suffixes: `1.2.0-rc.1`, `1.2.0-beta.1`.

## Release Checklist

- [ ] All tests pass on `main`
- [ ] Changelog updated
- [ ] Version bumped in `pyproject.toml`
- [ ] Database migrations tested (up and down)
- [ ] Docker images build successfully
- [ ] Documentation updated for new features
- [ ] Security vulnerabilities addressed
- [ ] Performance regression check (if applicable)

## Step 1: Prepare the Release

### Update the Changelog

Add a section for the new version at the top of `CHANGELOG.md`:

```markdown
## [1.2.0] - 2025-03-25

### Added
- Mailbox export API endpoint (#123)
- DKIM key rotation automation (#124)

### Fixed
- Webhook delivery retry logic not respecting backoff (#125)
- Storage calculation race condition (#126)

### Changed
- Upgraded Postfix base image to 3.8
- Improved queue manager throughput by 40%
```

### Bump the Version

```bash
# In pyproject.toml
[project]
version = "1.2.0"
```

### Commit the Release Prep

```bash
git add CHANGELOG.md pyproject.toml
git commit -m "chore: prepare release 1.2.0"
```

## Step 2: Create a Release Branch (for major/minor)

```bash
# For minor/major releases
git checkout -b release/1.2.0
git push origin release/1.2.0
```

For patch releases, you can release directly from `main`.

## Step 3: Build Docker Images

### Build All Images

```bash
# Build with version tag
docker compose build

# Tag images
VERSION="1.2.0"
for service in api tracking webhooks analytics rate-limiter queue-manager storage-usage monitoring rag; do
  docker tag mailyte-${service}:latest mailyte-${service}:${VERSION}
done
```

### Build Mailer Images

```bash
for service in postfix dovecot rspamd cert_manager; do
  docker tag mailyte-${service}:latest mailyte-${service}:${VERSION}
done
```

### Push to Registry

```bash
REGISTRY="ghcr.io/techiesafrica"
VERSION="1.2.0"

for image in api tracking webhooks analytics rate-limiter queue-manager storage-usage monitoring rag postfix dovecot rspamd cert_manager; do
  docker tag mailyte-${image}:${VERSION} ${REGISTRY}/mailyte-${image}:${VERSION}
  docker tag mailyte-${image}:${VERSION} ${REGISTRY}/mailyte-${image}:latest
  docker push ${REGISTRY}/mailyte-${image}:${VERSION}
  docker push ${REGISTRY}/mailyte-${image}:latest
done
```

## Step 4: Tag the Release

```bash
VERSION="1.2.0"

# Create annotated tag
git tag -a "v${VERSION}" -m "Release ${VERSION}"

# Push the tag
git push origin "v${VERSION}"
```

## Step 5: Create GitHub Release

```bash
VERSION="1.2.0"

gh release create "v${VERSION}" \
  --title "v${VERSION}" \
  --notes-file <(sed -n "/## \[${VERSION}\]/,/## \[/p" CHANGELOG.md | head -n -1)
```

Or create it through the GitHub UI using the tag.

## Step 6: Deploy

### Production Deployment

```bash
# On the production server
cd /opt/mailyte

# Pull the new images
docker compose pull

# Apply database migrations
docker exec -it api python manage.py migrate:run

# Rolling restart (zero downtime)
docker compose up -d --no-deps api
docker compose up -d --no-deps tracking webhooks analytics
docker compose up -d --no-deps queue-manager rate-limiter storage-usage

# Restart mail services (brief interruption)
docker compose up -d --no-deps postfix dovecot rspamd
```

### Verify the Deployment

```bash
# Check all services are healthy
docker compose ps

# Check the API version
curl http://localhost:8083/api/v1/get/status/version

# Check health endpoints
for port in 8080 8081 8082 8084 8085 8086 8087 8088; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health)
  echo "Port $port: $status"
done

# Send a test email
curl -X POST http://localhost:8083/api/v1/send/email \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"from": "test@yourdomain.com", "to": "verify@yourdomain.com", "subject": "Post-deploy test", "text": "Release verified."}'
```

## Rollback

If something goes wrong:

```bash
# Roll back to previous version
VERSION_OLD="1.1.0"

docker compose pull  # Make sure old images are cached

# Update docker-compose.yml image tags to ${VERSION_OLD}
# Or if using :latest tags, re-tag:
for service in api tracking webhooks analytics; do
  docker tag mailyte-${service}:${VERSION_OLD} mailyte-${service}:latest
done

docker compose up -d

# Roll back database migration if needed
docker exec -it api python manage.py migrate:rollback
```

## Hotfix Process

For urgent fixes in production:

1. Branch from the release tag: `git checkout -b hotfix/1.2.1 v1.2.0`
2. Fix the issue
3. Write a test
4. Bump patch version
5. Follow the normal release process (build, tag, deploy)
6. Cherry-pick the fix back to `main`: `git checkout main && git cherry-pick <commit>`

## Automation

Consider setting up GitHub Actions for:

- Running tests on every PR
- Building Docker images on merge to `main`
- Auto-tagging and releasing when version changes
- Deploying to staging on merge, production on tag
