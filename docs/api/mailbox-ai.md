---
title: Mailbox AI (Maya)
description: The AI assistant's gating model — deployment, org policy, entitlement, consent — and the consent ledger, thread summarize, classify, and quota endpoints.
---

# Mailbox AI (Maya)

The paid AI assistant over a holder's mailbox (mobile requirements v1 §13). Routes live in `worker/api/routes/mailbox_ai.py` (plus the pre-existing `/ai/compose` and `/ai/summarize/{id}` in `mailbox.py`), gated by `worker/api/utils/ai_consent.py`. Auth is a mailbox session.

## Four gates, resolved in order

`resolve_maya()` walks the gates and fails with the **outermost** failing one, so the client can tell the holder the thing whose fix actually unblocks them:

| Order | Gate | Failure |
|---|---|---|
| 1 | Deployment has an AI provider (`AI_BASE_URL` + `AI_API_KEY`) | `503 ai_not_configured` |
| 2 | Organisation policy (`ai_org_policy`) | `403 org_policy_blocked` — `unset` **is treated as blocked**; `blocked` is a hard veto overriding existing consent; `accepted_for_all` skips gate 4's individual ask |
| 3 | Organisation entitlement (`ai_entitled` / `ai_plan`) | `403 not_entitled` |
| 4 | Holder consent at the **current** terms version | `403 consent_required`, or `403 terms_version_stale` when an older version was accepted |

`GET /api/v1/mailbox/capabilities` reports the commercial state as `entitlements: {maya, maya_plan, maya_org_policy}` — an absent object means nothing entitled. Staff/Laravel set the org side with `PUT /api/v1/organizations/{id}/ai-settings` (`ai_entitled`, `ai_plan`, `ai_org_policy`, `ai_monthly_quota`); flipping the policy to `blocked` writes a `revoked_by_organisation` entry into every affected holder's history.

## Consent ledger

Consent is a **record**, not a preference — Maya reads mailbox content. Assistant and training are two independent decisions.

| Endpoint | Does |
|---|---|
| `GET /api/v1/mailbox/ai/consent` | Current state + `current_terms_version` + `document_url`. Consent to one version is not consent to the next |
| `POST /api/v1/mailbox/ai/consent` | `{accept_assistant, accept_training, terms_version}` — `409 terms_version_stale` if the version isn't current; on success stores the ledger row and generates the consent document, returning `{document_url, consent_id}` |
| `GET /api/v1/mailbox/ai/consent/{consent_id}/document` | The holder's own consent PDF (mailbox address, both decisions separately, terms version, full terms text, timestamp, actor). Retrievable after a later revoke |
| `DELETE /api/v1/mailbox/ai/consent` | Withdraw everything |
| `DELETE /api/v1/mailbox/ai/consent/training` | Withdraw only the training decision |
| `GET /api/v1/mailbox/ai/consent/history` | Append-only trail: `accepted`, `revoked`, `training_accepted`, `training_revoked`, `revoked_by_organisation`, each with `actor` and `document_url` |

!!! note "What "signed" means here"
    The PDF is integrity-sealed — the canonical consent record is hashed (SHA-256) and HMAC'd with a server secret, and both are stored beside the document — not X.509-signed. The ledger rows are deliberately FK-free so a consent record survives deletion of the mailbox and organisation it describes. Terms text lives versioned in `worker/api/ai_terms/`; it states plainly that redaction is best-effort and that a mailbox contains other people's personal data.

## Assistant endpoints

All pass the four gates first; every AI response carries `ai_calls_remaining` from the per-mailbox monthly quota (org `ai_monthly_quota`, default via `AI_MONTHLY_QUOTA_DEFAULT`; exhausted ⇒ `429 ai_quota_exhausted`).

| Endpoint | Does |
|---|---|
| `POST /api/v1/mailbox/ai/summarize/thread/{message_id}` | Recap of the whole conversation (one model call over the stitched thread) |
| `POST /api/v1/mailbox/ai/classify` | Batch labels with confidence over `{items: [{id, subject, preview}]}`, bounded batch size |
| `POST /api/v1/mailbox/ai/compose`, `POST /api/v1/mailbox/ai/summarize/{id}` | Pre-existing single-message endpoints, now behind the same gates |
