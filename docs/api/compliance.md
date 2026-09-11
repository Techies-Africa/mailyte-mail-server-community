---
edition: enterprise
---

# Compliance (GDPR)

GDPR tooling: data export (right of access), data erasure (right to be forgotten), consent records, the compliance audit log, legal holds, and retention policies.

**Base path:** `/api/v1/compliance`
**Auth:** every endpoint requires platform scope with an operator session. Role tiers vary by sensitivity:

| Endpoints                                                                 | Minimum role |
| ------------------------------------------------------------------------- | ------------ |
| Audit log                                                                 | `support`  |
| Export status, consent (read/write), legal-hold reads, retention policies | `admin`    |
| Trigger export, request erasure, place/release legal hold                 | `owner`    |

Legal holds are treated as legal instruments — placing and releasing them is owner-only and every action requires a recorded reason.

## Trigger GDPR Data Export

Create a pending data-export request for a user. A background worker gathers emails, contacts, and settings, packages them, and updates the row with the file path.

```
POST /api/v1/compliance/data-export/{user_email}
```

**Auth:** operator session, role `owner`.

**Request Body**

| Field            | Type   | Required | Description                                                   |
| ---------------- | ------ | -------- | ------------------------------------------------------------- |
| `export_type`  | string | No       | `full` (default), `emails`, `contacts`, or `settings` |
| `requested_by` | string | Yes      | Admin email or identifier requesting the export               |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"export_type": "full", "requested_by": "dpo@acme.com"}' \
  http://your-server:5000/api/v1/compliance/data-export/john@acme.com
```

## Check Export Status

Status of the most recent data-export requests for a user (up to the 10 most recent), including file path, size, and expiration.

```
GET /api/v1/compliance/data-export/{user_email}/status
```

**Example Response** (per request)

```json
{
  "id": "01J9AB2CD3EF4GH5JK6MN7PQ8R",
  "user_email": "john@acme.com",
  "status": "completed",
  "export_type": "full",
  "file_path": "/exports/john-acme-com-20260830.zip",
  "file_size": 104857600,
  "error_message": null,
  "completed_at": "2026-08-30T11:00:00",
  "expires_at": "2026-09-06T11:00:00",
  "created_at": "2026-08-30T10:30:00"
}
```

## Request Data Erasure

Initiate a right-to-erasure request. User data is immediately **soft-deleted** and a **hard deletion is scheduled 30 days later** to allow for legal-hold review.

```
POST /api/v1/compliance/data-erasure/{user_email}
```

**Auth:** operator session, role `owner`.

**Request Body**

| Field            | Type   | Required | Description                    |
| ---------------- | ------ | -------- | ------------------------------ |
| `requested_by` | string | Yes      | Admin email or identifier      |
| `reason`       | string | No       | Reason for the erasure request |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"requested_by": "dpo@acme.com", "reason": "GDPR Art. 17 request"}' \
  http://your-server:5000/api/v1/compliance/data-erasure/john@acme.com
```

An address under an active legal hold cannot be erased — check with [the legal-hold check endpoint](#check-legal-hold) first.

## Get Consent Records

All consent records for a user — marketing, analytics, third-party sharing, and data-processing preferences with grant/revocation timestamps.

```
GET /api/v1/compliance/consent/{user_email}
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/compliance/consent/john@acme.com
```

## Record Consent

Record or update a consent preference. If a record for the `(user_email, consent_type)` pair exists it is updated; otherwise a new record is created. The client IP is captured from the `X-Forwarded-For` header.

```
POST /api/v1/compliance/consent/{user_email}
```

**Request Body**

| Field            | Type    | Required | Description                                                           |
| ---------------- | ------- | -------- | --------------------------------------------------------------------- |
| `consent_type` | string  | Yes      | `marketing`, `analytics`, `third_party`, or `data_processing` |
| `granted`      | boolean | Yes      | Grant or revoke                                                       |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"consent_type": "marketing", "granted": false}' \
  http://your-server:5000/api/v1/compliance/consent/john@acme.com
```

## Query Audit Log

Query the GDPR audit log with optional filters, paginated and ordered newest-first.

```
GET /api/v1/compliance/audit-log
```

**Auth:** operator session, role `support` or above.

**Query Parameters**

| Parameter                     | Type    | Default        | Description                            |
| ----------------------------- | ------- | -------------- | -------------------------------------- |
| `start_date` / `end_date` | string  | --             | ISO 8601 range                         |
| `action`                    | string  | --             | Filter by action (e.g.`gdpr_export`) |
| `user_email`                | string  | --             | Filter by user                         |
| `ip_address`                | string  | --             | Filter by IP                           |
| `page` / `per_page`       | integer | `1` / `50` | `per_page` max 500                   |

**Example Response**

```json
{
  "status": "success",
  "data": [
    {
      "id": "01J9AB...",
      "action": "gdpr_export",
      "user_email": "john@acme.com",
      "performed_by": "dpo@acme.com",
      "ip_address": "198.51.100.7",
      "details": "full export",
      "created_at": "2026-08-30T10:30:00"
    }
  ],
  "pagination": { "page": 1, "per_page": 50, "total": 1, "total_pages": 1 }
}
```

## List Legal Holds

Active and lapsed legal holds.

```
GET /api/v1/compliance/legal-holds
```

**Query Parameters:** `organization_id`, `active` (`'true'`/`'false'`), `q` (substring on name/description), `page`, `per_page` (max 200).

!!! warning "An empty custodian list is the widest hold"
    A hold with no custodians covers **every address in its organization** — that is not an empty hold, it is the widest possible one.

## Check Legal Hold

Whether an address is under legal hold — the same check the erasure flow itself consults. Exposed separately so the console can warn **before** an operator types a confirmation.

```
GET /api/v1/compliance/legal-holds/check/{user_email}
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/compliance/legal-holds/check/john@acme.com
```

## Place Legal Hold

Placing a hold blocks GDPR erasure for the addresses it covers and overrides retention deletion. **Owner-only** — a hold is a legal instrument, and so is its absence.

```
POST /api/v1/compliance/legal-holds
```

**Request Body**

| Field               | Type   | Required | Description                                                |
| ------------------- | ------ | -------- | ---------------------------------------------------------- |
| `organization_id` | string | Yes      | Organization the hold applies to                           |
| `name`            | string | Yes      | Case name or matter (max 255)                              |
| `description`     | string | No       | Scope and context                                          |
| `custodians`      | array  | No       | Email addresses under hold. Empty = the whole organization |
| `end_date`        | string | No       | When the hold lapses (ISO 8601). Null = indefinite         |
| `reason`          | string | Yes      | Min 10 characters — recorded in the operator audit trail  |

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_id": "01J8ZJ...",
    "name": "Matter 2026-014",
    "custodians": ["john@acme.com", "jane@acme.com"],
    "reason": "Litigation hold per counsel instruction"
  }' \
  http://your-server:5000/api/v1/compliance/legal-holds
```

## Release Legal Hold

Deactivates a hold, un-blocking erasure and retention deletion for everything it covered. Owner-only, reason mandatory, and the row is never deleted — releasing is as significant as placing.

```
POST /api/v1/compliance/legal-holds/{hold_id}/release
```

**Request Body:** `{ "reason": "..." }` (min 10 characters).

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Matter closed; counsel released hold 2026-08-29"}' \
  http://your-server:5000/api/v1/compliance/legal-holds/14/release
```

## List Retention Policies

Per-organization retention settings.

```
GET /api/v1/compliance/retention-policies
```

**Query Parameters:** `organization_id`, `page`, `per_page` (max 200).

!!! info "`legal_hold` on a policy is an org-wide override"
    The `legal_hold` flag on a retention policy is an organization-wide "never delete" override that wins over `retention_days`. The UI is expected to surface that conflict rather than resolve it silently.

## Set Retention Policy

Upserts an organization's retention policy.

```
PUT /api/v1/compliance/retention-policies/{organization_id}
```

**Request Body**

| Field                  | Type    | Required | Description                  |
| ---------------------- | ------- | -------- | ---------------------------- |
| `retention_days`     | integer | Yes      | 1–36500                     |
| `auto_archive`       | boolean | No       | Default`true`              |
| `archive_after_days` | integer | No       | Default`90` (1–36500)     |
| `reason`             | string  | Yes      | Min 10 characters — audited |

```bash
curl -X PUT -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"retention_days": 2555, "archive_after_days": 365, "reason": "7-year financial-records policy"}' \
  http://your-server:5000/api/v1/compliance/retention-policies/01J8ZJ...
```

!!! info "Cannot set `legal_hold` here"
    This endpoint deliberately cannot set the `legal_hold` override — that flag is placed and released only through the legal-hold endpoints, which are owner-only and audited as legal instruments.
