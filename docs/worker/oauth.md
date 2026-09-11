---
edition: enterprise
---

# OAuth Worker

The OAuth worker is an OAuth 2.0 authorization server intended for IMAP/SMTP **XOAUTH2** authentication -- the modern alternative to basic auth. It is a FastAPI service backed by MySQL and Redis.

## The Designed Flow

1. Client requests authorization -- `GET /authorize`
2. User authenticates with their Mailyte credentials -- `POST /authorize/callback`
3. Client exchanges the auth code for tokens -- `POST /token`
4. Client uses the access token for IMAP/SMTP via SASL XOAUTH2
5. Client refreshes expired tokens -- `POST /token` with `grant_type=refresh_token`
6. The mail server validates tokens -- `POST /introspect`

!!! warning "XOAUTH2 is not wired into Dovecot yet"
    As of 2026-08-30, nothing in `mailer/dovecot/` references this service -- there is no `passdb oauth2` block, so IMAP/SMTP logins do not actually accept XOAUTH2 tokens. The authorization server side is complete; the consumer side is the missing half.

## API Endpoints

Copied from the route decorators in `worker/oauth/app.py`:

```
GET    /authorize                 -- OAuth2 authorization endpoint
POST   /authorize/callback        -- Process authorization (user login)
POST   /token                     -- Token endpoint (authorization_code / refresh_token)
POST   /introspect                -- Token introspection
POST   /revoke                    -- Revoke a token
POST   /clients                   -- Register an OAuth client
GET    /clients                   -- List clients
DELETE /clients/{client_id}       -- Delete a client
GET    /grants                    -- List a user's grants
DELETE /grants/{grant_id}         -- Revoke a grant
GET    /health                    -- Health check
GET    /metrics                   -- Prometheus metrics
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8091` | Bind port |
| `OAUTH_TOKEN_SECRET` | `change-me-in-production` (in-code fallback) | Token signing secret. The `secrets-check` compose service fails the whole stack's startup when this is missing from `.env`, which is what keeps the weak fallback from ever running in practice |
| `OAUTH_ISSUER` | `https://auth.mailyte.com` | Issuer identifier |
| `ACCESS_TOKEN_TTL` / `REFRESH_TOKEN_TTL` | (see `app.py`) | Token lifetimes |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `mysql` / `3306` / `mailserver` / -- / -- | MySQL connection |
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Redis connection |

## Docker Configuration

```yaml
oauth:
  build: ./worker/oauth
  container_name: oauth
  ports:
    - "8097:8091"     # host 8097 -> container 8091
  volumes:
    - ./shared:/app/shared
  depends_on:
    - mysql
    - migrate
    - redis
```

In production (`docker-compose.prod.yml`) the host port is bound to `127.0.0.1` only.
