---
edition: enterprise
---

# Transport Rules

Organization-wide mail-flow rules, evaluated in priority order (ascending — lower numbers run first). Each rule pairs a set of conditions (sender, recipient, subject, header, size, attachment presence) with one or more actions (add header, modify subject, redirect, BCC, reject, add disclaimer, quarantine).

**Base path:** `/api/v1/transport-rules`
**Auth:** `X-API-Key` (read for GETs, write for mutations) or a dashboard session. Tenant credentials operate only on their own organization's rules; platform-scope credentials can operate across organizations.

## The rule object

Endpoints with a response model return this shape directly (no envelope):

| Field | Type | Description |
|---|---|---|
| `id` | string | Rule ULID |
| `name` | string | Display name |
| `description` | string \| null | Free text |
| `organization_id` | string | Owning organization |
| `direction` | string | `inbound`, `outbound`, or `both` |
| `conditions` | array | `[{"field", "operator", "value"}]` |
| `condition_logic` | string | `all` (AND) or `any` (OR) |
| `actions` | array | `[{"type", "params"}]` |
| `priority` | integer | Lower runs first |
| `enabled` | boolean | Whether the rule is active |
| `hit_count` | integer | How many messages have matched |
| `created_at` / `updated_at` | string | ISO 8601 |

**Condition fields:** `sender`, `recipient`, `subject`, `header`, `size`, `has_attachment`.
**Condition operators:** `equals`, `contains`, `starts_with`, `ends_with`, `regex`, `greater_than`, `less_than`.
**Action types:** `add_header`, `modify_subject`, `redirect`, `bcc`, `reject`, `add_disclaimer`, `quarantine` — each with action-specific `params`.

## List Transport Rules

List all transport rules for the caller's organization, ordered by priority (ascending).

```
GET /api/v1/transport-rules
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `enabled_only` | boolean | `false` | Only return enabled rules |

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/transport-rules?enabled_only=true"
```

**Example Response**

```json
[
  {
    "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
    "name": "Block external to HR",
    "description": null,
    "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
    "direction": "inbound",
    "conditions": [
      { "field": "sender", "operator": "contains", "value": "external.com" }
    ],
    "condition_logic": "all",
    "actions": [
      { "type": "add_header", "params": { "name": "X-HR-Filtered", "value": "true" } }
    ],
    "priority": 10,
    "enabled": true,
    "hit_count": 42,
    "created_at": "2026-06-01T09:00:00",
    "updated_at": "2026-08-20T11:30:00"
  }
]
```

## Create Transport Rule

```
POST /api/v1/transport-rules
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Rule display name |
| `description` | string | No | Free text |
| `organization_id` | string | Platform only | Ignored for tenant credentials (their org is always derived from the API key). Required for platform-scope callers — omitting it there is a `400` |
| `direction` | string | No | `inbound`, `outbound`, or `both` (default `both`) |
| `conditions` | array | Yes | At least one `{field, operator, value}` |
| `condition_logic` | string | No | `all` (AND, default) or `any` (OR) |
| `actions` | array | Yes | At least one `{type, params}` |
| `priority` | integer | No | Default `100`; lower runs first |
| `enabled` | boolean | No | Default `true` |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Quarantine large external attachments",
    "direction": "inbound",
    "conditions": [
      {"field": "size", "operator": "greater_than", "value": "10485760"},
      {"field": "has_attachment", "operator": "equals", "value": "true"}
    ],
    "condition_logic": "all",
    "actions": [{"type": "quarantine", "params": {"reason": "large attachment"}}],
    "priority": 50
  }' \
  http://your-server:5000/api/v1/transport-rules
```

Returns the created rule object.

## Get Transport Rule

Retrieve a single rule by ID, including conditions, actions, priority, hit count, and timestamps.

```
GET /api/v1/transport-rules/{rule_id}
```

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/transport-rules/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

`404` if the rule does not exist (or belongs to another organization).

## Update Transport Rule

Replace all fields of an existing rule. The full set of conditions, actions, and metadata must be provided (same body as [Create](#create-transport-rule)).

```
PUT /api/v1/transport-rules/{rule_id}
```

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "name": "Quarantine large attachments", "conditions": [...], "actions": [...] }' \
  http://your-server:5000/api/v1/transport-rules/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

## Delete Transport Rule

Permanently delete a rule.

```
DELETE /api/v1/transport-rules/{rule_id}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/transport-rules/01J9AB2CD3EF4GH5JK6MN7PQ8R
```

`404` if the rule does not exist.

## Enable / Disable Transport Rule

Toggle a rule on or off without modifying its conditions or actions.

```
PUT /api/v1/transport-rules/{rule_id}/enable
```

**Query Parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | Yes | `true` to activate, `false` to deactivate |

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  "http://your-server:5000/api/v1/transport-rules/01J9AB2CD3EF4GH5JK6MN7PQ8R/enable?enabled=false"
```

## Reorder Transport Rules

Set rule priorities based on the position of each rule ID in the provided array. Priorities are spaced by 10 to allow future insertions.

```
PUT /api/v1/transport-rules/reorder
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `rule_ids` | array | Yes | Rule IDs (ULIDs) in desired priority order |

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"rule_ids": ["01J9AB...", "01J9AC...", "01J9AD..."]}' \
  http://your-server:5000/api/v1/transport-rules/reorder
```

**Example Response**

```json
{ "status": "ok", "message": "Rules reordered" }
```

`404` if any of the given `rule_ids` is not found in the caller's organization.
