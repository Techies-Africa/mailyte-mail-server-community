# Capabilities

The capability manifest — the CE/Pro edition-gating contract. `mailyte-web` and `mailyte-console` fetch this once at boot, **before login**, to decide which navigation entries and routes to render.

**Base path:** `/api/v1/capabilities`
**Auth:** none — unauthenticated by design, and it never returns tenant data.

This module contains a single endpoint. Capability strings are a public contract consumed by the frontends: once shipped, renaming one is a breaking change.

## Get Capability Manifest

Returns the platform edition and the list of capabilities it ships.

```
GET /api/v1/capabilities
```

**Example Request**

```bash
curl http://your-server:5000/api/v1/capabilities
```

**Example Response** (community edition)

```json
{
  "edition": "community",
  "version": "1.0.0",
  "capabilities": [
    "domains", "mailboxes", "aliases", "dkim", "webmail", "queue", "logs", "health",
    "overview", "directory", "mail_flow", "infrastructure", "security", "analytics",
    "governance", "docs", "settings"
  ]
}
```

**Edition resolution.** The edition comes from the `MAILYTE_EDITION` environment variable and defaults to `community` — fail safe, not fail open: an unset or misconfigured value must never accidentally advertise Pro-only capabilities.

**Enterprise adds** (returned in addition to the community list when `MAILYTE_EDITION=enterprise`):

```
organizations, billing, teams, reports, audit, deliverability, dkim_rotation,
migration, rag, whitelabel, reseller, priority_support,
dlp, reputation, compliance, analytics_engagement, analytics_reports
```

!!! info "The manifest gates console sections"
    The console checks these exact strings to decide which sections render — a capability the console checks for and the server never reports is a section that silently never appears. The names are one contract shared with `mailyte-console`'s navigation configuration.
