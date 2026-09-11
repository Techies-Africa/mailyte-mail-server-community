---
title: Compliance
description: GDPR data handling in Mailyte — the compliance API, data retention, right to erasure, legal holds, and audit logging.
edition: enterprise
---

# Compliance

If you handle email for European users, GDPR applies. Even if it doesn't, these practices are good hygiene. Mailyte ships a real compliance API (`/api/v1/compliance/*`, platform scope) backed by dedicated tables — use it rather than raw SQL.

## GDPR Considerations

Email contains personal data by definition — names, addresses, message content. Under GDPR:

- You need a **lawful basis** for processing email (usually legitimate interest or contract)
- Users have the **right to access** their data
- Users have the **right to erasure** (right to be forgotten)
- You must **report data breaches** within 72 hours
- Data should be **minimized** — don't keep what you don't need

## The Compliance API

| Endpoint | What it does |
|----------|-------------|
| `POST /api/v1/compliance/data-export/{email}` | Trigger a GDPR data export (Art. 15) — tracked in `data_export_requests` |
| `GET /api/v1/compliance/data-export/{email}/status` | Check export progress |
| `POST /api/v1/compliance/data-erasure/{email}` | Right-to-erasure request (Art. 17) — immediate soft-delete, hard deletion scheduled 30 days later; tracked in `data_erasure_requests` |
| `GET/POST /api/v1/compliance/consent/{email}` | Read / record consent (`consent_records`) |
| `GET /api/v1/compliance/audit-log` | Query the audit trail (`audit_logs`) |
| `GET/POST /api/v1/compliance/legal-holds` | List / place legal holds (`legal_holds`) |
| `GET /api/v1/compliance/legal-holds/check/{email}` | Is this address under hold? |
| `POST /api/v1/compliance/legal-holds/{id}/release` | Release a hold |
| `GET /api/v1/compliance/retention-policies` | List retention policies |
| `PUT /api/v1/compliance/retention-policies/{org_id}` | Set an organization's retention policy (`retention_policies`) |

Erasure requires the platform `owner` role; most other routes require `admin` or lower — the route decorators are authoritative.

### Legal holds block erasure — enforced

`POST /data-erasure/{email}` checks `legal_holds` **before** touching any data and refuses with `409 legal_hold_active` if the address is under an active hold. Release the hold (or refuse the erasure request citing it) first. The archiver's retention expiry honours holds as well.

## Right to Erasure

The erasure flow:

1. **Legal-hold check** — mandatory, first
2. **Soft delete** — user data is immediately soft-deleted and the request recorded (`data_erasure_requests`, status `soft_deleted`)
3. **Hard delete** — scheduled 30 days later, allowing review
4. **Audit** — the request and its progress land in `audit_logs`

```bash
curl -X POST "https://<api-host>/api/v1/compliance/data-erasure/user@example.com" \
  -H "X-API-Key: $OWNER_KEY" -H "Content-Type: application/json" \
  -d '{"reason": "user request", "requested_by": "support-ticket-1234"}'
```

Things the API-driven flow covers that ad-hoc SQL forgets: tracking events, suppression entries, sessions, and the audit record of the erasure itself. Mailbox content removal (the Maildir) still requires deleting the mailbox through the mailbox API.

## Right to Access (Data Export)

```bash
# Kick off an export
curl -X POST "https://<api-host>/api/v1/compliance/data-export/user@example.com" \
  -H "X-API-Key: $PLATFORM_KEY"

# Poll for completion
curl -s "https://<api-host>/api/v1/compliance/data-export/user@example.com/status" \
  -H "X-API-Key: $PLATFORM_KEY"
```

## Data Retention Policies

### Per-organization policies

Retention policies are stored per organization (`retention_policies`) and settable via the compliance API. The **archiver** service applies retention to archived mail (`worker/archiver` — retention-policy endpoints plus expiry that honours legal holds).

### Operational-table retention

Rows in the operational tables below accumulate independently of the archive; prune them on a schedule that matches your policy:

| Data Type | Table | Suggested Retention |
|-----------|-------|-------------------|
| Mail logs | `mail_logs`, `delivery_events` | 90 days |
| Tracking events | `email_tracking`, `tracking_events` | 90 days |
| Tracking stats (aggregated) | `tracking_statistics` | 1 year |
| Webhook logs | `webhook_delivery_logs` | 30 days |
| Health checks | `health_checks` | 7 days |
| Service metrics | `service_metrics` | 30 days |
| Usage history | `usage_history` | 90 days (billing may require longer) |
| AI transactions | `ai_transactions` | 90 days |
| Sessions | `user_sessions`, `web_sessions` | expiry + 7 days |
| Failed auth attempts | `failed_auth_attempts` | 90 days |

!!! warning "Operational-table cleanup is not automated"
    There is no shipped cron job that prunes these tables — set one up yourself. Always batch deletes with `LIMIT` and loop until zero rows are affected, to avoid long-running transactions:

```bash
#!/bin/bash
# cleanup_retention.sh — run daily via cron
DB_CMD="docker compose exec -T mysql mysql -u root -p${DB_ROOT_PASSWORD} mailserver -N -e"

for table_config in \
  "email_tracking:created_at:90" \
  "mail_logs:created_at:90" \
  "webhook_delivery_logs:created_at:30" \
  "health_checks:timestamp:7" \
  "service_metrics:timestamp:30"; do

  table=${table_config%%:*}; rest=${table_config#*:}
  column=${rest%%:*}; days=${rest##*:}

  while :; do
    deleted=$($DB_CMD "DELETE FROM $table WHERE $column < NOW() - INTERVAL $days DAY LIMIT 10000; SELECT ROW_COUNT();" | tail -1)
    echo "$table: deleted $deleted rows"
    [ "${deleted:-0}" -eq 0 ] && break
    sleep 1
  done
done
```

## Audit Logging

The `audit_logs` table (created in the baseline schema; a legacy `audit_log` view maps onto it) records:

| Event | Written by |
|-------|-----------|
| Auth failures / successes | Dovecot auth-policy server |
| API key and SMTP-credential lifecycle | API routes (`record_event`) |
| Erasure / export / consent / legal-hold actions | Compliance routes |
| Security administration (IP blocks, policy changes) | Security routes |

Query it via `GET /api/v1/compliance/audit-log` with filters for event type, actor, and time range.

## Data Processing Agreement

If you're running Mailyte as a service for others, you need a DPA (Data Processing Agreement) with your customers. It should cover:

- What data you process and why
- How long you keep it (align with your `retention_policies`)
- Security measures in place (reference the [security section](index.md))
- Sub-processors — including your S3 provider, since encrypted backups and the mail archive live there
- Breach notification procedures

## Checklist

- [x] Erasure endpoint with legal-hold enforcement (live)
- [x] Data export endpoint (live)
- [x] Consent recording (live)
- [x] Audit logging (live)
- [x] Legal holds (live)
- [x] Per-org retention policies for archived mail (live, archiver-enforced)
- [ ] Automated cleanup of operational tables — **you must schedule this yourself**
- [ ] DPA prepared for customers — organizational, not technical
- [ ] Breach notification process documented — organizational
- [ ] Privacy policy updated — organizational
