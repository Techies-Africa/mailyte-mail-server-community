---
title: API Endpoints
description: Complete index of every Mailyte API endpoint with method, path, purpose, and required authentication.
---

# API Endpoints

Complete index of every HTTP endpoint served by the API gateway (`worker/api`), grouped by route module in registration order. Regenerated from the route code on 2026-08-30 — 307 endpoints across 32 route modules.

**Base URL.** The production compose file publishes the API on port `8083` (`http://your-server:8083`). The development server (`python app.py`) listens on port `5000`, which is the port used in the example requests throughout this documentation. All paths below are absolute — they already include the `/api/v1/...` prefix each router is mounted under.

## Authentication legend

The API accepts three credential types (see [Authentication](../api/authentication.md)). The **Auth** column uses these labels:

| Label | Meaning |
|---|---|
| Key (read / write / admin) | `X-API-Key` header with that permission flag, or a dashboard session cookie. Organization-scoped: a tenant credential only sees its own organization; a platform-scope key sees all. |
| Platform (read / write / admin) | Requires platform scope — a platform-scope API key (`api_keys.scope = 'platform'`) or an operator session. Tenant credentials are rejected with `403` regardless of their permission flags. |
| Operator (support+ / operator+ / admin+ / owner+) | Requires an **operator session** at or above that role tier (`support < operator < admin < owner`). A bare API key — even a platform-scope one — carries no role and cannot pass a role gate. |
| Key or session | Any authenticated identity: API key, dashboard session, or operator session. |
| Mailbox session | Webmail session cookie obtained from `POST /api/v1/mailbox-auth/login`. |
| Bootstrap token | Single-use `X-Bootstrap-Token` header (fresh installs only). |
| None (public) | Unauthenticated by design. |

Session-cookie requests with state-changing methods additionally require the CSRF token; `Idempotency-Key` is honoured on every authenticated mutating route.

## Organizations

Module: `worker/api/routes/organizations.py` — mounted at `/api/v1/organizations`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/organizations` | List all organizations | Operator (support+) |
| GET | `/api/v1/organizations/{organization_id}` | Get organization details | Key (read) |
| POST | `/api/v1/organizations` | Create a new organization | Operator (admin+) |
| PUT | `/api/v1/organizations/{organization_id}` | Update an organization | Key (write) |
| DELETE | `/api/v1/organizations/{organization_id}` | Delete an organization | Operator (admin+) |
| GET | `/api/v1/organizations/{organization_id}/quotas` | Get organization quotas | Key (read) |
| GET | `/api/v1/organizations/by-external-id/{external_id}` | Look up organization by external ID | Key (read) |
| PUT | `/api/v1/organizations/{organization_id}/quotas` | Update organization quotas | Operator (admin+) |
| POST | `/api/v1/organizations/{organization_id}/quotas/clear-override` | Clear an operator quota override | Operator (admin+) |

## Domains

Module: `worker/api/routes/domains.py` — mounted at `/api/v1/domains`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/domains` | List all domains | Key (read) |
| GET | `/api/v1/domains/{domain_id}` | Get domain details | Key (read) |
| POST | `/api/v1/domains` | Create a new domain | Key (write) |
| PUT | `/api/v1/domains/{domain_id}` | Update a domain | Key (write) |
| DELETE | `/api/v1/domains/{domain_id}` | Delete a domain | Key (write) |
| GET | `/api/v1/domains/{domain_id}/verify-dns` | Verify domain DNS records | Key (read) |
| GET | `/api/v1/domains/{domain_id}/quotas` | Get domain quotas | Key (read) |
| PUT | `/api/v1/domains/{domain_id}/quotas` | Update domain quotas | Key (write) |
| POST | `/api/v1/domains/edit` | Edit domain (legacy) | Key (write) |
| POST | `/api/v1/domains/delete/domain` | Delete domains (legacy) | Key (write) |
| GET | `/api/v1/domains/get/domain/policy/{domain}` | Get domain policy | Key (read) |
| POST | `/api/v1/domains/edit/domain/policy` | Edit domain policy | Key (write) |
| GET | `/api/v1/domains/stats/{domain}` | Get domain statistics | Key (read) |
| GET | `/api/v1/domains/{domain_id}/dkim` | List a domain's DKIM keys | Operator (support+) |
| POST | `/api/v1/domains/{domain_id}/dkim/rotate` | Generate a new DKIM key under a new selector | Operator (operator+) |
| POST | `/api/v1/domains/{domain_id}/dkim/{selector}/activate` | Cut DKIM signing over to a selector | Operator (operator+) |

## Mailboxes (Email Accounts)

Module: `worker/api/routes/mailboxes.py` — mounted at `/api/v1/mailboxes`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/mailboxes/email-accounts` | List all email accounts | Key (read) |
| GET | `/api/v1/mailboxes/email-accounts/{account_id}` | Get email account details | Key (read) |
| POST | `/api/v1/mailboxes/email-accounts` | Create a new email account | Key (write) |
| PUT | `/api/v1/mailboxes/email-accounts/{account_id}` | Update an email account | Key (write) |
| DELETE | `/api/v1/mailboxes/email-accounts/{account_id}` | Delete an email account | Key (write) |
| GET | `/api/v1/mailboxes/email-accounts/{account_id}/quotas` | Get account quota and usage | Key (read) |
| PUT | `/api/v1/mailboxes/email-accounts/{account_id}/quotas` | Update account quota settings | Key (write) |
| POST | `/api/v1/mailboxes/add` | Add a new mailbox (legacy) | Key (write) |
| GET | `/api/v1/mailboxes/get/{mailbox_id}` | Get mailbox information (legacy) | Key (read) |
| POST | `/api/v1/mailboxes/edit` | Edit mailbox settings (legacy) | Key (write) |
| POST | `/api/v1/mailboxes/delete` | Delete mailbox(es) (legacy) | Key (write) |
| GET | `/api/v1/mailboxes/get/quota/{mailbox}` | Get mailbox quota details (legacy) | Key (read) |
| POST | `/api/v1/mailboxes/edit/quota` | Update mailbox quota (legacy) | Key (write) |
| GET | `/api/v1/mailboxes/get/stats/{mailbox}` | Get mailbox statistics (legacy) | Key (read) |

## Aliases

Module: `worker/api/routes/aliases.py` — mounted at `/api/v1/aliases`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/aliases/add` | Create a new email alias | Key (write) |
| GET | `/api/v1/aliases/get/{alias_id}` | Get alias information | Key (read) |
| POST | `/api/v1/aliases/edit` | Edit alias settings | Key (write) |
| POST | `/api/v1/aliases/delete` | Delete alias(es) | Key (write) |
| GET | `/api/v1/aliases/get/stats/{domain}` | Get alias statistics for a domain | Key (read) |
| POST | `/api/v1/aliases/add/bulk` | Bulk create aliases | Key (write) |

## Analytics

Module: `worker/api/routes/analytics.py` — mounted at `/api/v1/analytics`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/analytics/dashboard/{domain}` | Domain analytics dashboard | Key (read) |
| GET | `/api/v1/analytics/email-volume/{domain}` | Email volume analytics | Key (read) |
| GET | `/api/v1/analytics/engagement/{domain}` | Email engagement metrics | Key (read) |
| GET | `/api/v1/analytics/deliverability/{domain}` | Email deliverability metrics | Key (read) |
| POST | `/api/v1/analytics/reports/generate` | Generate analytics report | Key (read) |
| GET | `/api/v1/analytics/reports/scheduled` | List scheduled reports | Key (read) |
| POST | `/api/v1/analytics/reports/scheduled` | Create scheduled report | Key (write) |
| GET | `/api/v1/analytics/reports/{report_id}` | Get report by ID | Key (read) |
| GET | `/api/v1/analytics/metrics/{domain}` | Get domain metrics | Key (read) |
| GET | `/api/v1/analytics/health` | Analytics service health | Key (read) |

## Monitoring

Module: `worker/api/routes/monitoring.py` — mounted at `/api/v1/monitoring`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/monitoring/health` | System health check | Operator (support+) |
| GET | `/api/v1/monitoring/services` | Get all services status | Operator (support+) |
| GET | `/api/v1/monitoring/services/{service_name}` | Get single service status | Operator (support+) |
| GET | `/api/v1/monitoring/metrics` | Get system metrics | Operator (support+) |
| GET | `/api/v1/monitoring/stats` | Get dashboard statistics | Operator (support+) |
| POST | `/api/v1/monitoring/services/{service_name}/restart` | Restart a service | Operator (operator+) |
| POST | `/api/v1/monitoring/auto-heal` | Trigger auto-heal for all services | Operator (operator+) |
| POST | `/api/v1/monitoring/webhooks/test` | Test webhook endpoints | Operator (operator+) |

## Queue

Module: `worker/api/routes/queue.py` — mounted at `/api/v1/queue`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/queue/queue/status` | Get mail queue status | Operator (support+) |
| GET | `/api/v1/queue/queue/domain/{domain}` | Get domain queue status | Operator (support+) |
| GET | `/api/v1/queue/mail-queue/deferred` | Get deferred mail queue | Operator (support+) |
| POST | `/api/v1/queue/mail-queue/flush` | Flush mail queue | Operator (operator+) |
| GET | `/api/v1/queue/jobs/{domain}` | Get domain queue jobs | Operator (support+) |
| GET | `/api/v1/queue/health` | Queue health check | Operator (support+) |

## Webhooks

Module: `worker/api/routes/webhooks.py` — mounted at `/api/v1/webhooks`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/webhooks/endpoints` | List all webhook endpoints for the authenticated organization | Key (read) |
| POST | `/api/v1/webhooks/endpoints` | Create a new webhook endpoint | Key (write) |
| GET | `/api/v1/webhooks/endpoints/{endpoint_id}` | Get a specific webhook endpoint | Key (read) |
| PUT | `/api/v1/webhooks/endpoints/{endpoint_id}` | Update a webhook endpoint | Key (write) |
| DELETE | `/api/v1/webhooks/endpoints/{endpoint_id}` | Delete a webhook endpoint | Key (write) |
| POST | `/api/v1/webhooks/endpoints/{endpoint_id}/test` | Send a test event to a webhook endpoint | Key (write) |
| GET | `/api/v1/webhooks/deliveries` | List webhook delivery history for the authenticated organization | Key (read) |
| GET | `/api/v1/webhooks/dead-letters` | List permanently failed webhook events (dead letter queue) | Key (read) |
| GET | `/api/v1/webhooks/dead-letters/{dead_letter_id}` | Get one dead-lettered webhook event | Operator (support+) |
| POST | `/api/v1/webhooks/dead-letters/{dead_letter_id}/replay` | Replay one dead-lettered webhook event | Operator (operator+) |
| POST | `/api/v1/webhooks/dead-letters/replay-bulk` | Replay several dead-lettered webhook events | Operator (operator+) |

## Rate Limiting

Module: `worker/api/routes/rate_limiter.py` — mounted at `/api/v1/rate-limiter`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/rate-limiter/rate-limits/domain/{domain}` | Get rate limits for a domain | Key (read) |
| POST | `/api/v1/rate-limiter/rate-limits/domain/{domain}` | Set rate limits for a domain | Key (write) |
| GET | `/api/v1/rate-limiter/rate-limits/mailbox/{email}` | Get rate limits for a mailbox | Key (read) |
| POST | `/api/v1/rate-limiter/rate-limits/mailbox/{email}` | Set rate limits for a mailbox (see set_domain_limits docstring for | Key (write) |
| GET | `/api/v1/rate-limiter/rate-limits/usage/{domain}` | Get current usage statistics for a domain | Key (read) |
| POST | `/api/v1/rate-limiter/rate-limits/reset` | Reset rate limit counters for an organization/domain/mailbox | Key (write) |
| GET | `/api/v1/rate-limiter/quotas/domain/{domain}` | Get daily/monthly quotas for a domain | Key (read) |
| POST | `/api/v1/rate-limiter/quotas/domain/{domain}` | Set daily/monthly quotas for a domain (see get_domain_quotas and | Key (write) |

## Storage

Module: `worker/api/routes/storage.py` — mounted at `/api/v1/storage`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/storage/usage/domain/{domain}` | Get domain storage usage | Key (read) |
| GET | `/api/v1/storage/usage/mailbox/{email}` | Get mailbox storage usage | Key (read) |
| GET | `/api/v1/storage/quotas/domain/{domain}` | Get domain storage quotas | Key (read) |
| POST | `/api/v1/storage/quotas/domain/{domain}` | Set domain storage quotas | Operator (admin+) |
| GET | `/api/v1/storage/quotas/mailbox/{email}` | Get mailbox storage quotas | Key (read) |
| POST | `/api/v1/storage/quotas/mailbox/{email}` | Set mailbox storage quotas | Operator (admin+) |
| POST | `/api/v1/storage/cleanup` | Trigger storage cleanup | Operator (operator+) |
| GET | `/api/v1/storage/usage/summary` | Get overall usage summary | Operator (support+) |

## Tracking

Module: `worker/api/routes/tracking.py` — mounted at `/api/v1/tracking`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/tracking/pixel/{tracking_id}` | Track email open | None (public) |
| GET | `/api/v1/tracking/click/{tracking_id}` | Track email link click | None (public) |
| GET | `/api/v1/tracking/unsubscribe/{tracking_id}` | Handle unsubscribe (GET) | None (public) |
| POST | `/api/v1/tracking/unsubscribe/{tracking_id}` | Handle unsubscribe (POST) | None (public) |
| GET | `/api/v1/tracking/stats/domain/{domain}` | Get domain tracking statistics | Key (read) |
| GET | `/api/v1/tracking/stats/email/{email_id}` | Get email tracking statistics | Key (read) |
| POST | `/api/v1/tracking/suppress` | Add email to suppression list | Key (write) |
| DELETE | `/api/v1/tracking/suppress/{email}` | Remove email from suppression list | Key (write) |
| GET | `/api/v1/tracking/suppressions` | List suppressed addresses | Operator (support+) |
| POST | `/api/v1/tracking/suppressions/bulk` | Suppress several addresses at once | Operator (operator+) |

## RAG

Module: `worker/api/routes/rag.py` — mounted at `/api/v1/rag`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/rag/search` | Semantic email search | Key (read) |
| GET | `/api/v1/rag/rag/index/status/{domain}` | Get domain indexing status | Key (read) |
| POST | `/api/v1/rag/rag/index/trigger` | Trigger email indexing | Key (write) |
| GET | `/api/v1/rag/collections/{domain}` | List domain collections | Key (read) |
| GET | `/api/v1/rag/organizations/{organization_id}/collections` | List organization collections | Key (read) |
| POST | `/api/v1/rag/collections/{domain}` | Create domain collection | Key (write) |
| POST | `/api/v1/rag/documents/{collection_id}` | Add documents to collection | Key (write) |
| GET | `/api/v1/rag/collections/{domain}/stats` | Get collection statistics | Key (read) |
| GET | `/api/v1/rag/rag/config/domain/{domain}` | Get domain RAG config | Key (read) |
| POST | `/api/v1/rag/rag/config/domain/{domain}` | Set domain RAG config | Key (write) |
| GET | `/api/v1/rag/health` | RAG service health | Key (read) |
| GET | `/api/v1/rag/organizations/{organization_id}/rag/config` | Get organization RAG config | Key (read) |
| PUT | `/api/v1/rag/organizations/{organization_id}/rag/config` | Update organization RAG config | Key (write) |
| GET | `/api/v1/rag/organizations/{organization_id}/rag/stats` | Get organization RAG stats | Key (read) |
| GET | `/api/v1/rag/organizations/{organization_id}/rag/documents` | List organization RAG documents | Key (read) |
| POST | `/api/v1/rag/organizations/{organization_id}/rag/reindex` | Trigger organization reindex | Key (write) |

## Filters (Sieve)

Module: `worker/api/routes/filters.py` — mounted at `/api/v1/filters`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/filters` | List Sieve filter scripts | Key (read) |
| GET | `/api/v1/filters/templates` | List filter templates | Key (read) |
| GET | `/api/v1/filters/{name}` | Get a Sieve script | Key (read) |
| POST | `/api/v1/filters` | Create or update a Sieve script | Key (write) |
| DELETE | `/api/v1/filters/{name}` | Delete a Sieve script | Key (write) |
| PUT | `/api/v1/filters/{name}/activate` | Activate a Sieve script | Key (write) |
| POST | `/api/v1/filters/vacation` | Manage vacation responder | Key (write) |

## Shared Mailboxes

Module: `worker/api/routes/shared_mailboxes.py` — mounted at `/api/v1/shared-mailboxes`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/shared-mailboxes` | List shared mailboxes | Key (read) |
| POST | `/api/v1/shared-mailboxes` | Create shared mailbox | Key (write) |
| GET | `/api/v1/shared-mailboxes/{mailbox_id}` | Get shared mailbox details | Key (read) |
| DELETE | `/api/v1/shared-mailboxes/{mailbox_id}` | Delete shared mailbox | Key (write) |
| POST | `/api/v1/shared-mailboxes/{mailbox_id}/members` | Add member to shared mailbox | Key (write) |
| DELETE | `/api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}` | Remove member from shared mailbox | Key (write) |
| PUT | `/api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}` | Update member permissions | Key (write) |

## Message Trace

Module: `worker/api/routes/message_trace.py` — mounted at `/api/v1/message-trace`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/message-trace/trace` | Search mail delivery logs | Operator (support+) |
| GET | `/api/v1/message-trace/trace/{msg_id}` | Get message delivery lifecycle | Operator (support+) |
| GET | `/api/v1/message-trace/quarantine` | List quarantined messages | Operator (support+) |
| POST | `/api/v1/message-trace/quarantine/{quarantine_id}/release` | Release a quarantined message | Operator (admin+) |
| POST | `/api/v1/message-trace/quarantine/{quarantine_id}/block` | Block a quarantined message and suppress its sender | Operator (operator+) |
| GET | `/api/v1/message-trace/audit` | Search audit logs | Operator (support+) |

## Transport Rules

Module: `worker/api/routes/transport_rules.py` — mounted at `/api/v1/transport-rules`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/transport-rules` | List transport rules | Key (read) |
| POST | `/api/v1/transport-rules` | Create a transport rule | Key (write) |
| GET | `/api/v1/transport-rules/{rule_id}` | Get a transport rule | Key (read) |
| PUT | `/api/v1/transport-rules/{rule_id}` | Update a transport rule | Key (write) |
| DELETE | `/api/v1/transport-rules/{rule_id}` | Delete a transport rule | Key (write) |
| PUT | `/api/v1/transport-rules/{rule_id}/enable` | Enable or disable a transport rule | Key (write) |
| PUT | `/api/v1/transport-rules/reorder` | Reorder transport rules | Key (write) |

## White Label

Module: `worker/api/routes/whitelabel.py` — mounted at `/api/v1/whitelabel`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/whitelabel/config/{org_id}` | Get white-label config | Operator (admin+) |
| PUT | `/api/v1/whitelabel/config/{org_id}` | Update white-label config | Operator (admin+) |
| POST | `/api/v1/whitelabel/config/{org_id}/verify-domain` | Verify custom brand domain | Operator (admin+) |
| GET | `/api/v1/whitelabel/config/{org_id}/preview` | Preview branded login page | Operator (admin+) |
| DELETE | `/api/v1/whitelabel/config/{org_id}` | Remove white-label config | Operator (admin+) |

## Reseller

Module: `worker/api/routes/reseller.py` — mounted at `/api/v1/reseller`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/reseller/sub-organizations` | Create sub-organization | Operator (admin+) |
| GET | `/api/v1/reseller/sub-organizations` | List sub-organizations | Operator (admin+) |
| GET | `/api/v1/reseller/sub-organizations/{org_id}` | Get sub-organization details | Operator (admin+) |
| PUT | `/api/v1/reseller/sub-organizations/{org_id}/plan` | Update sub-organization plan | Operator (admin+) |
| DELETE | `/api/v1/reseller/sub-organizations/{org_id}` | Deactivate sub-organization | Operator (admin+) |
| GET | `/api/v1/reseller/billing/summary` | Get reseller billing summary | Operator (admin+) |

## Compliance (GDPR)

Module: `worker/api/routes/compliance.py` — mounted at `/api/v1/compliance`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/compliance/data-export/{user_email}` | Trigger GDPR data export | Operator (owner+) |
| GET | `/api/v1/compliance/data-export/{user_email}/status` | Check data export status | Operator (admin+) |
| POST | `/api/v1/compliance/data-erasure/{user_email}` | Request GDPR data erasure | Operator (owner+) |
| GET | `/api/v1/compliance/consent/{user_email}` | Get user consent records | Operator (admin+) |
| POST | `/api/v1/compliance/consent/{user_email}` | Record or update consent | Operator (admin+) |
| GET | `/api/v1/compliance/audit-log` | Query audit log | Operator (support+) |
| GET | `/api/v1/compliance/legal-holds` | List legal holds | Operator (admin+) |
| GET | `/api/v1/compliance/legal-holds/check/{user_email}` | Check whether an address is under legal hold | Operator (admin+) |
| POST | `/api/v1/compliance/legal-holds` | Place a legal hold | Operator (owner+) |
| POST | `/api/v1/compliance/legal-holds/{hold_id}/release` | Release a legal hold | Operator (owner+) |
| GET | `/api/v1/compliance/retention-policies` | List retention policies | Operator (admin+) |
| PUT | `/api/v1/compliance/retention-policies/{organization_id}` | Set an organization's retention policy | Operator (admin+) |

## Migration

Module: `worker/api/routes/migration.py` — mounted at `/api/v1/migration`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/migration/import` | Import emails from external provider | Key (write) |
| POST | `/api/v1/migration/import/bulk` | Bulk import from external providers | Key (write) |
| POST | `/api/v1/migration/export` | Export emails to external provider | Key (write) |
| POST | `/api/v1/migration/export/bulk` | Bulk export to external providers | Key (write) |
| GET | `/api/v1/migration/jobs` | List migration jobs | Key (read) |
| GET | `/api/v1/migration/jobs/{job_id}` | Get migration job status | Key (read) |
| GET | `/api/v1/migration/jobs/{job_id}/errors` | Get migration job errors | Key (read) |
| POST | `/api/v1/migration/jobs/{job_id}/cancel` | Cancel a migration job | Key (write) |
| POST | `/api/v1/migration/jobs/{job_id}/retry-failed` | Retry failed messages | Key (write) |
| POST | `/api/v1/migration/jobs/{job_id}/delta` | Run incremental delta sync | Key (write) |
| GET | `/api/v1/migration/summary` | Get migration summary | Key (read) |

## SSL Certificates

Module: `worker/api/routes/ssl.py` — mounted at `/api/v1/ssl`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/ssl/status` | Get SSL certificate status | Operator (support+) |
| GET | `/api/v1/ssl/certificates` | List all SSL certificates | Operator (support+) |
| GET | `/api/v1/ssl/certificates/{domain}` | Get certificate for a domain | Operator (support+) |
| POST | `/api/v1/ssl/certificates/{domain}/renew` | Trigger certificate renewal for a domain | Operator (operator+) |
| GET | `/api/v1/ssl/accounts` | List ACME accounts | Operator (admin+) |

## SMTP Credentials

Module: `worker/api/routes/smtp_credentials.py` — mounted at `/api/v1/smtp-credentials`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/smtp-credentials` | List SMTP credentials | Key (read) |
| POST | `/api/v1/smtp-credentials` | Create an SMTP credential | Key (write) |
| GET | `/api/v1/smtp-credentials/{credential_id}` | Get an SMTP credential | Key (read) |
| PATCH | `/api/v1/smtp-credentials/{credential_id}` | Update an SMTP credential | Key (write) |
| POST | `/api/v1/smtp-credentials/{credential_id}/rotate` | Rotate an SMTP credential's secret | Key (write) |
| POST | `/api/v1/smtp-credentials/{credential_id}/revoke` | Revoke an SMTP credential | Key (write) |
| POST | `/api/v1/smtp-credentials/{credential_id}/enable` | Re-enable a revoked SMTP credential | Key (write) |
| DELETE | `/api/v1/smtp-credentials/{credential_id}` | Delete an SMTP credential | Key (write) |

## SMTP Credential Reports

Module: `worker/api/routes/smtp_credential_reports.py` — mounted at `/api/v1/smtp-credentials`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/smtp-credentials/{credential_id}/events` | Audit trail for an SMTP credential | Key (read) |
| GET | `/api/v1/smtp-credentials/{credential_id}/usage` | Delivery counts for an SMTP credential | Key (read) |

## Capabilities

Module: `worker/api/routes/capabilities.py` — mounted at `/api/v1/capabilities`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/capabilities` | Get edition capability manifest | None (public) |

## Bootstrap

Module: `worker/api/routes/bootstrap.py` — mounted at `/api/v1/bootstrap`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/bootstrap` | One-time platform bootstrap | Bootstrap token |

## Auth (Dashboard)

Module: `worker/api/routes/auth.py` — mounted at `/api/v1/auth`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/auth/login` | Log in with email and password | None (public) |
| POST | `/api/v1/auth/logout` | Log out | Key or session (read) |
| GET | `/api/v1/auth/me` | Current authenticated identity | Key or session (read) |
| POST | `/api/v1/auth/password` | Change own password | Key or session (read) |
| POST | `/api/v1/auth/sessions/revoke-all` | Revoke all sessions for the current user | Key or session (read) |

## Platform Auth (Operators)

Module: `worker/api/routes/platform_auth.py` — mounted at `/api/v1/platform/auth`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/platform/auth/bootstrap` | One-time first-operator bootstrap | None (public) |
| POST | `/api/v1/platform/auth/login` | Operator login, step 1 of 2 | None (public) |
| POST | `/api/v1/platform/auth/mfa/setup` | Begin MFA enrollment | None (public) |
| POST | `/api/v1/platform/auth/mfa` | Operator login, step 2 of 2 | None (public) |
| POST | `/api/v1/platform/auth/logout` | Log out | Platform (read) |
| GET | `/api/v1/platform/auth/me` | Current operator identity | Platform (read) |

## Platform (Console)

Module: `worker/api/routes/platform.py` — mounted at `/api/v1/platform`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/platform/overview` | Platform overview counters | Operator (support+) |
| GET | `/api/v1/platform/operators` | List platform operators | Operator (owner+) |
| GET | `/api/v1/platform/operators/{operator_id}` | Get one platform operator | Operator (owner+) |
| POST | `/api/v1/platform/operators` | Create a platform operator | Operator (owner+) |
| PUT | `/api/v1/platform/operators/{operator_id}` | Update a platform operator | Operator (owner+) |
| POST | `/api/v1/platform/operators/{operator_id}/mfa-reset` | Reset an operator's MFA enrollment | Operator (owner+) |
| GET | `/api/v1/platform/operators/{operator_id}/sessions` | List one operator's sessions | Operator (owner+) |
| POST | `/api/v1/platform/operators/{operator_id}/sessions/revoke` | Revoke all of an operator's live sessions | Operator (owner+) |
| GET | `/api/v1/platform/sessions` | List all live operator sessions | Operator (owner+) |
| DELETE | `/api/v1/platform/sessions/{session_id}` | Revoke a single operator session | Operator (owner+) |
| GET | `/api/v1/platform/audit` | Search the operator audit trail | Operator (support+) |
| GET | `/api/v1/platform/audit/facets` | Distinct values for audit filter dropdowns | Operator (support+) |
| GET | `/api/v1/platform/audit/{audit_id}` | Get one audit entry | Operator (support+) |
| GET | `/api/v1/platform/provisioning-failures` | Cross-tenant provisioning failure queue | Operator (support+) |
| POST | `/api/v1/platform/provisioning-failures/{resource_type}/{resource_id}/retry` | Retry a failed provisioning attempt | Operator (operator+) |
| GET | `/api/v1/platform/metrics/range` | Range query against the metrics backend | Operator (support+) |
| GET | `/api/v1/platform/logs` | Tail this service's log | Operator (support+) |
| GET | `/api/v1/platform/analytics/overview` | Platform-wide mail volume and deliverability rollup | Operator (support+) |
| GET | `/api/v1/platform/analytics/growth` | Cumulative estate growth curve | Operator (support+) |
| GET | `/api/v1/platform/analytics/heatmap` | Outbound send pattern by hour of week | Operator (support+) |
| GET | `/api/v1/platform/alerts/metrics` | List the alertable metrics | Operator (support+) |
| GET | `/api/v1/platform/alerts/rules` | List alert rules | Operator (support+) |
| GET | `/api/v1/platform/alerts/rules/{rule_id}` | Get one alert rule | Operator (support+) |
| POST | `/api/v1/platform/alerts/rules` | Create an alert rule | Operator (operator+) |
| PUT | `/api/v1/platform/alerts/rules/{rule_id}` | Update an alert rule | Operator (operator+) |
| DELETE | `/api/v1/platform/alerts/rules/{rule_id}` | Delete an alert rule | Operator (operator+) |
| POST | `/api/v1/platform/alerts/rules/{rule_id}/evaluate` | Evaluate one alert rule against live data now | Operator (operator+) |
| GET | `/api/v1/platform/alerts/channels` | List notification channels | Operator (support+) |
| POST | `/api/v1/platform/alerts/channels` | Create a notification channel | Operator (operator+) |
| DELETE | `/api/v1/platform/alerts/channels/{channel_id}` | Delete a notification channel | Operator (operator+) |
| POST | `/api/v1/platform/alerts/channels/{channel_id}/test` | Send a test notification through a channel | Operator (operator+) |
| GET | `/api/v1/platform/alerts/history` | Search fired alerts | Operator (support+) |
| GET | `/api/v1/platform/backups/status` | Backup health summary | Operator (admin+) |
| GET | `/api/v1/platform/backups` | List backup runs | Operator (admin+) |
| POST | `/api/v1/platform/backups/report` | Record a backup run from another host | Operator (admin+) |

## Security

Module: `worker/api/routes/security.py` — mounted at `/api/v1/security`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/security/failed-auth` | List failed authentication attempts | Operator (operator+) |
| GET | `/api/v1/security/failed-auth/summary` | Brute-force and distributed-attack summary | Operator (operator+) |
| POST | `/api/v1/security/failed-auth/{client_ip}/block` | Block an IP at the authentication layer | Operator (operator+) |
| DELETE | `/api/v1/security/failed-auth/{client_ip}/block` | Lift an authentication-layer block on an IP | Operator (operator+) |
| GET | `/api/v1/security/ip-rules` | List IP access rules | Operator (operator+) |
| POST | `/api/v1/security/ip-rules` | Create an IP access rule | Operator (operator+) |
| PUT | `/api/v1/security/ip-rules/{rule_id}` | Update an IP access rule | Operator (operator+) |
| DELETE | `/api/v1/security/ip-rules/{rule_id}` | Delete an IP access rule | Operator (operator+) |
| GET | `/api/v1/security/geo-policies` | List geo access policies | Operator (operator+) |
| POST | `/api/v1/security/geo-policies` | Create a geo access policy | Operator (operator+) |
| PUT | `/api/v1/security/geo-policies/{policy_id}` | Update a geo access policy | Operator (operator+) |
| DELETE | `/api/v1/security/geo-policies/{policy_id}` | Delete a geo access policy | Operator (operator+) |
| GET | `/api/v1/security/dlp/policies` | List DLP policies | Operator (operator+) |
| POST | `/api/v1/security/dlp/policies` | Create a DLP policy | Operator (admin+) |
| PUT | `/api/v1/security/dlp/policies/{policy_id}` | Update a DLP policy | Operator (admin+) |
| DELETE | `/api/v1/security/dlp/policies/{policy_id}` | Delete a DLP policy | Operator (admin+) |
| GET | `/api/v1/security/dlp/violations` | List DLP violations (content redacted) | Operator (operator+) |
| GET | `/api/v1/security/dlp/violations/summary` | DLP violation summary | Operator (operator+) |
| GET | `/api/v1/security/dlp/violations/{violation_id}` | Get one DLP violation (content redacted) | Operator (operator+) |

## Reputation

Module: `worker/api/routes/reputation.py` — mounted at `/api/v1/reputation`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/reputation/domains` | Domain reputation | Operator (support+) |
| GET | `/api/v1/reputation/domains/{domain}` | One domain's reputation and recent complaints | Operator (support+) |
| GET | `/api/v1/reputation/ips` | IP reputation | Operator (support+) |
| GET | `/api/v1/reputation/feedback-loops` | ISP feedback-loop complaints | Operator (support+) |
| GET | `/api/v1/reputation/summary` | Reputation summary | Operator (support+) |

## Mailbox Auth (Webmail)

Module: `worker/api/routes/mailbox_auth.py` — mounted at `/api/v1/mailbox-auth`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/mailbox-auth/login` | Sign in to the webmail with mailbox credentials | None (public) |
| POST | `/api/v1/mailbox-auth/logout` | End the current webmail session | None (public) |

## Mailbox (Webmail)

Module: `worker/api/routes/mailbox.py` — mounted at `/api/v1/mailbox`

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/mailbox/capabilities` | What this deployment's webmail can actually do | Mailbox session |
| GET | `/api/v1/mailbox/folders` | List the mailbox's folders | Mailbox session |
| POST | `/api/v1/mailbox/folders` | Create a folder | Mailbox session |
| GET | `/api/v1/mailbox/messages` | List messages in a folder | Mailbox session |
| POST | `/api/v1/mailbox/messages/draft` | Save a draft | Mailbox session |
| DELETE | `/api/v1/mailbox/messages/draft/{message_id}` | Discard a draft | Mailbox session |
| GET | `/api/v1/mailbox/messages/{message_id}` | Read one message | Mailbox session |
| GET | `/api/v1/mailbox/messages/{message_id}/attachments` | List a message's attachments | Mailbox session |
| GET | `/api/v1/mailbox/messages/{message_id}/attachments/{index}` | Download one attachment | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/mark-read` | Mark a message read | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/mark-unread` | Mark a message unread | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/star` | Star a message | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/unstar` | Unstar a message | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/move` | Move a message to another folder | Mailbox session |
| POST | `/api/v1/mailbox/messages/{message_id}/trash` | Move a message to Trash | Mailbox session |
| DELETE | `/api/v1/mailbox/messages/{message_id}` | Permanently delete a message | Mailbox session |
| GET | `/api/v1/mailbox/messages/{message_id}/thread` | The conversation a message belongs to | Mailbox session |
| GET | `/api/v1/mailbox/contacts` | Addresses this mailbox corresponds with | Mailbox session |
| POST | `/api/v1/mailbox/messages/send` | Send a message | Mailbox session |
| GET | `/api/v1/mailbox/settings` | The mailbox holder's own settings | Mailbox session |
| PUT | `/api/v1/mailbox/settings` | Update the mailbox holder's settings | Mailbox session |
| GET | `/api/v1/mailbox/security` | The mailbox holder's security state | Mailbox session |
| POST | `/api/v1/mailbox/security/2fa/begin` | Start two-factor enrolment | Mailbox session |
| POST | `/api/v1/mailbox/security/2fa/confirm` | Confirm two-factor enrolment with a code | Mailbox session |
| POST | `/api/v1/mailbox/security/2fa/disable` | Turn two-factor off | Mailbox session |
| GET | `/api/v1/mailbox/security/sessions` | Where this mailbox is signed in | Mailbox session |
| DELETE | `/api/v1/mailbox/security/sessions/{session_id}` | Sign out another device | Mailbox session |
| POST | `/api/v1/mailbox/ai/compose` | Draft or rewrite a message with AI | Mailbox session |
| POST | `/api/v1/mailbox/ai/summarize/{message_id}` | Summarise a message | Mailbox session |
| GET | `/api/v1/mailbox/forwarding` | Mail forwarding settings | Mailbox session |
| PUT | `/api/v1/mailbox/forwarding` | Update mail forwarding | Mailbox session |
| GET | `/api/v1/mailbox/rules` | The mailbox's filter rules | Mailbox session |
| PUT | `/api/v1/mailbox/rules` | Replace the mailbox's filter rules | Mailbox session |
| GET | `/api/v1/mailbox/vacation` | The vacation responder | Mailbox session |
| PUT | `/api/v1/mailbox/vacation` | Update the vacation responder | Mailbox session |
