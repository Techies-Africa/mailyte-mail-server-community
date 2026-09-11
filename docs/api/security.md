# Security

Console-facing security controls: failed-authentication monitoring and IP blocking, IP access rules (enforced live by the Postfix policy server), geo access policies, and DLP (data-loss-prevention) policies with a content-redacted violation feed.

**Base path:** `/api/v1/security`
**Auth:** every endpoint requires platform scope with an operator session (`require_scope("platform", ...)`). Reads and most writes require role `operator` or above; **DLP policy writes require `admin`** — a disabled DLP policy is an open exfiltration channel. Every mutation requires a `reason`, recorded by the operator audit middleware. Responses use the standard `{"type", "msg", "data"}` envelope.

## Failed Authentication

### List Failed Authentication Attempts

Paginated view over `failed_auth_attempts`, filterable by IP, account, service, block state, and date range. Each row is a **collapsed counter** (attempts within a window), not a single failure.

```
GET /api/v1/security/failed-auth
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `client_ip` | string | -- | Exact client IP |
| `username` | string | -- | Exact account/username |
| `service` | string | -- | Service the attempt hit (e.g. `api`, `api_key`, `smtp`, `imap`) |
| `blocked` | boolean | -- | `true` = currently blocked |
| `date_from` / `date_to` | string | -- | ISO date or datetime range |
| `q` | string | -- | Free text over client IP and username |
| `sort_by` | string | `last_attempt_at` | Sort column |
| `sort_dir` | string | `desc` | `asc` \| `desc` |
| `page` / `per_page` | integer | `1` / `50` | Pagination |

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  "http://your-server:5000/api/v1/security/failed-auth?blocked=true&sort_dir=desc"
```

### Failed-Auth Summary

The shape of the attack rather than a list of its rows: top IPs, top targeted accounts, and totals over a lookback window.

```
GET /api/v1/security/failed-auth/summary
```

**Query Parameters:** `hours` (default `24`, min 1) — lookback window.

!!! info "Distributed-attack signal"
    `top_ips[].distinct_accounts` is the credential-stuffing signal: one IP touching many accounts is a different incident from one IP hammering one account.

### Block an IP

Sets `blocked_until` on every `failed_auth_attempts` row for the IP — which is what the auth path consults before it will consider a credential at all.

```
POST /api/v1/security/failed-auth/{client_ip}/block
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `minutes` | integer | Yes | Block duration, 1 to 43200 (30 days). This is a lockout timer, not a permanent ban list |
| `reason` | string | Yes | Why — audited |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"minutes": 1440, "reason": "Credential stuffing against 40+ accounts"}' \
  http://your-server:5000/api/v1/security/failed-auth/203.0.113.9/block
```

### Unblock an IP

Clears `blocked_until` for the IP. The attempt counters are left intact on purpose — unblocking is a decision about the future, and erasing the history that justified the block would falsify the audit trail.

```
DELETE /api/v1/security/failed-auth/{client_ip}/block
```

**Request Body:** `{ "reason": "..." }` (required, audited).

## IP Access Rules

Rules in `ip_access_rules` are enforced by Postfix's policy delegation service on authenticated submission — changes apply on the next connection.

### List IP Rules

```
GET /api/v1/security/ip-rules
```

**Query Parameters:** `rule_type` (`whitelist` | `blacklist`), `active`, `organization_id`, `q` (free text over IP/description), `sort_by`, `sort_dir`, `page`, `per_page`.

### Create IP Rule

```
POST /api/v1/security/ip-rules
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `rule_type` | string | Yes | `whitelist` \| `blacklist` |
| `ip_address` | string | Yes | IPv4/IPv6 address or CIDR network |
| `description` | string | No | Max 255 |
| `organization_id` | string | Effectively yes | Owning tenant — a rule with no organization is never evaluated |
| `reason` | string | Yes | Why — audited |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rule_type": "whitelist",
    "ip_address": "203.0.113.0/24",
    "organization_id": "01J8ZJ...",
    "reason": "Office egress range"
  }' \
  http://your-server:5000/api/v1/security/ip-rules
```

### Update IP Rule

Changes description, active flag, or rule type. `ip_address` is deliberately immutable — editing the address in place would silently repoint an existing audit trail at a different network. Delete and re-create instead.

```
PUT /api/v1/security/ip-rules/{rule_id}
```

**Request Body:** `description`, `active`, `rule_type` (all optional) + required `reason`.

### Delete IP Rule

```
DELETE /api/v1/security/ip-rules/{rule_id}
```

**Request Body:** `{ "reason": "..." }` (required).

!!! warning "Deleting the last whitelist rule"
    Deleting a tenant's **last active whitelist rule** returns that tenant from default-deny to default-allow. The response reports how many active whitelist rules remain.

## Geo Access Policies

One policy per tenant (`geo_policies` has a UNIQUE key on `organization_id`), enforced by the geo-blocking service. No policy row = allow-all.

### List Geo Policies

```
GET /api/v1/security/geo-policies
```

**Query Parameters:** `organization_id`, `action` (`block` | `challenge` | `log_only`), `enabled`, `page`, `per_page`.

### Create Geo Policy

```
POST /api/v1/security/geo-policies
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `organization_id` | string | Platform choice | Owning tenant |
| `allowed_countries` | array | No | ISO 3166-1 alpha-2 codes |
| `blocked_countries` | array | No | ISO 3166-1 alpha-2 codes |
| `time_restrictions` | object | No | e.g. `{"allowed_hours": {"start": 8, "end": 18}, "timezone_offset": 0, "applies_to_countries": ["US"]}` |
| `action` | string | No | `block` (default) \| `challenge` \| `log_only` |
| `enabled` | boolean | No | Default `true` |
| `reason` | string | Yes | Why — audited |

A second create for the same tenant returns `409`, not a second row.

### Update Geo Policy

Partial update. The allowed/blocked conflict check runs against the **merged** policy, not just the fields in the request.

```
PUT /api/v1/security/geo-policies/{policy_id}
```

**Request Body:** any of `allowed_countries`, `blocked_countries`, `time_restrictions`, `action`, `enabled` + required `reason`.

### Delete Geo Policy

Removes the tenant's geo policy entirely, returning that tenant to unrestricted country access. Prefer `PUT` with `enabled: false` to keep the policy on file.

```
DELETE /api/v1/security/geo-policies/{policy_id}
```

**Request Body:** `{ "reason": "..." }` (required).

## DLP Policies

### List DLP Policies

```
GET /api/v1/security/dlp/policies
```

**Auth:** role `operator`+ (reads). Writes below require `admin`.

**Query Parameters:** `organization_id`, `policy_type`, `severity`, `apply_to`, `enabled`, `q`, `sort_by`, `sort_dir`, `page`, `per_page`.

### Create DLP Policy

```
POST /api/v1/security/dlp/policies
```

**Auth:** role `admin`+.

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `organization_id` | string | Platform choice | Owning tenant |
| `name` | string | Yes | Max 255 |
| `description` | string | No | Free text |
| `policy_type` | string | No | e.g. `keyword` (default), `regex`, ... |
| `patterns` | array | Yes | **Non-empty** array of patterns to match |
| `action` | string | No | e.g. `notify` (default), `block`, `quarantine` |
| `severity` | string | No | e.g. `low` \| `medium` (default) \| `high` \| `critical` |
| `enabled` | boolean | No | Default `true` |
| `apply_to` | string | No | e.g. `outbound` (default), `inbound`, `both` |
| `reason` | string | Yes | Why — audited |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_id": "01J8ZJ...",
    "name": "Card numbers outbound",
    "policy_type": "regex",
    "patterns": ["\\b(?:\\d[ -]*?){13,16}\\b"],
    "action": "block",
    "severity": "critical",
    "reason": "PCI scope"
  }' \
  http://your-server:5000/api/v1/security/dlp/policies
```

### Update DLP Policy

Partial update; `patterns`, when supplied, **replaces the array wholesale** rather than merging — a merge would make removing a pattern impossible.

```
PUT /api/v1/security/dlp/policies/{policy_id}
```

**Auth:** role `admin`+. **Body:** any policy field + required `reason`.

### Delete DLP Policy

```
DELETE /api/v1/security/dlp/policies/{policy_id}
```

**Auth:** role `admin`+. **Body:** `{ "reason": "..." }`.

Past violations are deliberately **not** removed with the policy — the violation history survives.

## DLP Violations

### List DLP Violations

Cross-tenant violation feed: which policy fired, on whose message, and what was done about it.

```
GET /api/v1/security/dlp/violations
```

**Query Parameters:** `organization_id`, `policy_id`, `severity`, `violation_type`, `action_taken`, `date_from`, `date_to`, `q` (over sender/recipient), `sort_by`, `sort_dir`, `page`, `per_page`.

!!! warning "Content is redacted"
    `matched_pattern` and `details` are **never returned and never even selected** — the matched content is out of the console's reach by design (ADR-002 §5).

### DLP Violation Summary

Counts by severity, policy, and action taken, plus a daily series — the "is exfiltration increasing" view. Aggregates only.

```
GET /api/v1/security/dlp/violations/summary
```

**Query Parameters:** `organization_id`, `days` (default `30`, 1–365).

### Get One DLP Violation

Same fields, and the same redaction, as the list view. There is deliberately no "reveal" variant of this endpoint.

```
GET /api/v1/security/dlp/violations/{violation_id}
```
