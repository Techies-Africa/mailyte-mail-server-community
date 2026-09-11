# Platform (Console)

The operations console's platform surface: estate overview, operator management, session control, the operator audit trail, provisioning failures, metrics and log tailing, platform-wide analytics, alerting (rules, channels, history), and backup reporting.

**Base path:** `/api/v1/platform`
**Auth:** every endpoint requires platform scope with an operator session (`require_scope("platform", ...)`). Minimum role tiers by area:

| Area | Minimum role |
|---|---|
| Overview, audit, provisioning reads, metrics, logs, analytics, alert reads | `support` |
| Provisioning retry, alert rule/channel writes | `operator` |
| Backups | `admin` |
| Operators and sessions | `owner` |

Responses use the standard `{"type", "msg", "data"}` envelope. Deletes and corrective actions require a `reason` in the request body, recorded by the operator audit middleware.

Operator login itself lives under a separate prefix — see `POST /api/v1/platform/auth/login` (module `routes/platform_auth.py`, documented in the [endpoint index](../reference/api-endpoints.md#platform-auth-operators)).

## Overview

### Platform Overview Counters

```
GET /api/v1/platform/overview
```

Estate-wide counters for the console landing page.

```bash
curl -H "Cookie: mailyte_operator_session=..." \
  http://your-server:5000/api/v1/platform/overview
```

(Examples below use `X-API-Key` for brevity, but remember: role-gated routes require an operator **session** — a bare platform-scope API key carries no role.)

## Operators

All operator endpoints require role `owner`.

### List Operators

```
GET /api/v1/platform/operators
```

**Query Parameters:** `q` (free text over email/name), `role`, `is_active`, `page`, `per_page`.

### Get Operator

```
GET /api/v1/platform/operators/{operator_id}
```

### Create Operator

```
POST /api/v1/platform/operators
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Operator email (also the TOTP identity) |
| `full_name` | string | Yes | Display name (1–255) |
| `password` | string | Yes | Initial password (strength policy enforced) |
| `role` | string | Yes | `support` \| `operator` \| `admin` \| `owner` |

```bash
curl -X POST -H "Cookie: mailyte_operator_session=..." \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@mailyte.com", "full_name": "Ops One", "password": "a-Strong-Passw0rd!", "role": "operator"}' \
  http://your-server:5000/api/v1/platform/operators
```

### Update Operator

```
PUT /api/v1/platform/operators/{operator_id}
```

**Request Body** (all optional): `full_name`, `role`, `is_active`.

!!! info "Deactivate rather than delete"
    There is no operator delete — audit rows reference the operator forever. Set `is_active: false` instead.

### Reset Operator MFA

```
POST /api/v1/platform/operators/{operator_id}/mfa-reset
```

Clears the operator's TOTP enrollment so they re-enroll at next login.

### List an Operator's Sessions

```
GET /api/v1/platform/operators/{operator_id}/sessions
```

### Revoke All of an Operator's Sessions

```
POST /api/v1/platform/operators/{operator_id}/sessions/revoke
```

## Sessions

Role `owner`.

### List All Live Operator Sessions

```
GET /api/v1/platform/sessions
```

### Revoke a Single Session

```
DELETE /api/v1/platform/sessions/{session_id}
```

## Audit Trail

Role `support`+ (read-only).

### Search the Operator Audit Trail

```
GET /api/v1/platform/audit
```

**Query Parameters:** `operator_id`, `operator_email`, `action`, `target_type`, `target_id`, `organization_id`, `result` (`success` | `failure`), `caller_scope`, `correlation_id`, `date_from`, `date_to`, `q` (free text), `page`, `per_page`.

```bash
curl -H "Cookie: mailyte_operator_session=..." \
  "http://your-server:5000/api/v1/platform/audit?result=failure&date_from=2026-08-01"
```

### Audit Facets

Distinct values for the audit screen's filter dropdowns.

```
GET /api/v1/platform/audit/facets
```

### Get One Audit Entry

```
GET /api/v1/platform/audit/{audit_id}
```

## Provisioning Failures

### List Provisioning Failures

Cross-tenant queue of failed provisioning attempts.

```
GET /api/v1/platform/provisioning-failures
```

**Query Parameters:** `resource_type` (e.g. `mailbox`, `domain`), `organization_id`, `page`, `per_page`. Role `support`+.

### Retry a Provisioning Failure

```
POST /api/v1/platform/provisioning-failures/{resource_type}/{resource_id}/retry
```

Role `operator`+. **Request Body:** `{ "reason": "..." }` (required, audited).

## Metrics and Logs

Role `support`+.

### Metrics Range Query

Range query against the metrics backend. Only allowlisted metric names are accepted.

```
GET /api/v1/platform/metrics/range
```

**Query Parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Allowlisted metric name |
| `hours` | integer | No | Window size (default `24`, capped) |
| `step` | string | No | Resolution, e.g. `5m` |

### Tail Service Logs

```
GET /api/v1/platform/logs
```

**Query Parameters:** `service` (service name), `level` (minimum level), `q` (case-insensitive text match), `correlation_id` (exact), `since` (ISO 8601), `lines` (count cap).

```bash
curl -H "Cookie: mailyte_operator_session=..." \
  "http://your-server:5000/api/v1/platform/logs?service=api&level=error&lines=200"
```

## Platform Analytics

Role `support`+.

### Mail Volume and Deliverability Rollup

```
GET /api/v1/platform/analytics/overview
```

**Query Parameters:** `days` (default `30`), `granularity` (`hour` | `day` | ...), `organization_id`.

### Estate Growth Curve

Cumulative growth of organizations/domains/mailboxes.

```
GET /api/v1/platform/analytics/growth
```

**Query Parameters:** `days` (default `90`), `granularity`.

### Send-Pattern Heatmap

Outbound send pattern by hour of week.

```
GET /api/v1/platform/analytics/heatmap
```

**Query Parameters:** `days` (default `30`), `organization_id`.

## Alerts

Reads require `support`+; writes require `operator`+.

### List Alertable Metrics

The server-side allowlist of metrics an alert rule may reference.

```
GET /api/v1/platform/alerts/metrics
```

### List Alert Rules

```
GET /api/v1/platform/alerts/rules
```

**Query Parameters:** `q`, `metric`, `severity`, `enabled`, `organization_id`, `sort` (`name`|`metric`|`severity`|`created_at`...), `direction`, `page`, `per_page`.

### Get Alert Rule

```
GET /api/v1/platform/alerts/rules/{rule_id}
```

### Create Alert Rule

```
POST /api/v1/platform/alerts/rules
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | 1–255 chars |
| `description` | string | No | Max 2000 |
| `metric` | string | Yes | One of the allowlist — see [List Alertable Metrics](#list-alertable-metrics) |
| `comparator` | string | Yes | `>` \| `>=` \| `<` \| `<=` \| `==` |
| `threshold` | number | Yes | Trigger threshold |
| `for_seconds` | integer | No | Dwell time the breach must hold before firing (default `0`, max 86400) |
| `severity` | string | No | `critical` \| `warning` (default) \| `info` |
| `enabled` | boolean | No | Default `true` |
| `channel_ids` | array | No | Notification channels to fire into |
| `organization_id` | string | No | Scope the rule to one tenant; `null` = platform-wide |

```bash
curl -X POST -H "Cookie: mailyte_operator_session=..." \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Deferred queue depth",
    "metric": "postfix_queue_deferred",
    "comparator": ">",
    "threshold": 100,
    "for_seconds": 600,
    "severity": "critical",
    "channel_ids": ["01J9AB..."]
  }' \
  http://your-server:5000/api/v1/platform/alerts/rules
```

### Update Alert Rule

```
PUT /api/v1/platform/alerts/rules/{rule_id}
```

Partial update — same fields as create, all optional.

### Delete Alert Rule

```
DELETE /api/v1/platform/alerts/rules/{rule_id}
```

**Request Body:** `{ "reason": "..." }` (required, audited).

### Evaluate Alert Rule Now

Evaluate one rule against live data immediately (dry-run of the evaluation loop).

```
POST /api/v1/platform/alerts/rules/{rule_id}/evaluate
```

### List Notification Channels

```
GET /api/v1/platform/alerts/channels
```

**Query Parameters:** `type` (`email` | `webhook`), `enabled`.

### Create Notification Channel

```
POST /api/v1/platform/alerts/channels
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | 1–255 chars |
| `type` | string | Yes | `email` \| `webhook` |
| `target` | string | Yes | Email address for `email`; absolute http(s) URL for `webhook` |
| `enabled` | boolean | No | Default `true` |
| `signing_secret` | string | No | Webhook HMAC secret. Stored envelope-encrypted and **never returned by any endpoint** — there is no read-back path, so keep your own copy |

### Delete Notification Channel

```
DELETE /api/v1/platform/alerts/channels/{channel_id}
```

**Request Body:** `{ "reason": "..." }` (required, audited).

### Test Notification Channel

Send a test notification through a channel.

```
POST /api/v1/platform/alerts/channels/{channel_id}/test
```

### Search Fired Alerts

```
GET /api/v1/platform/alerts/history
```

**Query Parameters:** `rule_id`, `state` (`firing` | `resolved`), `severity`, `date_from`, `date_to`, `sort`, `direction`, `page`, `per_page`.

## Backups

Role `admin`.

### Backup Health Summary

```
GET /api/v1/platform/backups/status
```

### List Backup Runs

```
GET /api/v1/platform/backups
```

**Query Parameters:** `status` (`running` | `completed` | `failed`), `target` (`database` | `mail` | `config` | `all`), `backup_type` (`full` | `incremental` | `differential`), `sort` (`started_at` | `completed_at` | ...), `direction`, `page`, `per_page`.

### Report a Backup Run

Record a backup run executed on another host — the write side backup scripts call in with.

```
POST /api/v1/platform/backups/report
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `backup_id` | string | Yes | `YYYYmmdd_HHMMSS` stamp (max 32) |
| `hostname` | string | Yes | Reporting host |
| `backup_type` | string | No | `full` (default) \| `incremental` \| `differential` |
| `target` | string | No | `database` \| `mail` \| `config` \| `all` (default) |
| `status` | string | Yes | `running` \| `completed` \| `failed` |
| `size_bytes` | integer | No | Default `0` |
| `storage_path` | string | No | Where the artifact landed |
| `checksum` | string | No | Artifact checksum |
| `encrypted` | boolean | No | Default `true` |
| `error_message` | string | No | For failed runs |

```bash
curl -X POST -H "Cookie: mailyte_operator_session=..." \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "20260830_020000",
    "hostname": "mail-01",
    "target": "database",
    "status": "completed",
    "size_bytes": 734003200,
    "storage_path": "/backups/db/20260830_020000.sql.gz.enc"
  }' \
  http://your-server:5000/api/v1/platform/backups/report
```
