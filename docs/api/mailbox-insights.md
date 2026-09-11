---
title: Mailbox Insights
description: Server-side mailbox analytics for the holder — response behavior, rhythm, relationships, hygiene — plus per-message conversation context.
---

# Mailbox Insights

Server-side analytics over the authenticated holder's own mailbox (INBOX + Sent + Drafts), built for the mobile app's Insights page (mobile requirements v2 §18). Everything except `topics` is header arithmetic — **no AI provider, entitlement, or consent is required** for it.

Routes live in `worker/api/routes/mailbox_insights.py` over the aggregation engine in `worker/api/utils/insights/`; auth is a mailbox session (Bearer token or cookie), same as the rest of `/api/v1/mailbox/*`.

## GET /api/v1/mailbox/insights

```
GET /api/v1/mailbox/insights?window=90d
Authorization: Bearer <mailbox session token>
```

| Param | Values | Default |
|---|---|---|
| `window` | `30d`, `90d`, `180d`, `365d` | `90d` |
| `refresh` | `true` bypasses the cached rollup (per-mailbox rate-limited) | `false` |

The response is assembled from independent blocks — a block the engine cannot compute is **omitted**, not null-filled, and the client hides its section:

| Block | Carries |
|---|---|
| `window` | `from`, `to`, `messages_considered`, and the `timezone` used for bucketing (the mailbox preference when set, else the deployment default) |
| `attention` | `response_rate` (received messages that ever got a reply from this mailbox), `read_never_answered` (`\Seen`, unanswered, and older than the awaiting window), `oldest_unread_days`, `backlog_trend` (`growing`/`shrinking`/`flat`, threshold applied server-side), `awaiting_you[]`, `awaiting_them[]` |
| `response_time` | your median/p90 reply minutes, their median, `trend`, `fastest[]`/`slowest[]` correspondents |
| `rhythm` | `arrivals_by_weekday_hour` (exactly 7×24, Monday first, holder's timezone), `after_hours_send_share`, `busiest_hour`, `quietest_weekday` |
| `relationships` | `one_way_senders[]`, `dormant[]`, `internal_share`, `top_correspondents[]` |
| `hygiene` | `monthly_growth_mb`, `bounces` (DSN/MAILER-DAEMON detection, documented heuristic), `stale_drafts`, `oldest_draft_days`, `automated_share`, `attachment_heavy_senders[]` |
| `topics` | **Omitted entirely unless the deployment has an AI provider configured** — the only AI-gated block |

Threading is computed from `Message-ID`/`In-Reply-To`/`References` fetched header-only in UID batches, bounded per folder; the work runs off the event loop and reuses the IMAP connection slots, so a heavy mailbox cannot starve webmail. Results are cached per (mailbox, window) — `window.to` is the compute time, so a figure a few hours old is labelled, not hidden.

## GET /api/v1/mailbox/messages/{message_id}/context

Header-only context for one message — returned **regardless of AI state** (the AI recap lives on the [Mailbox AI](mailbox-ai.md) surface):

```json
{
  "correspondent": {
    "email": "jane@example.com",
    "messages_exchanged": 34, "first_contact": "2025-02-11", "last_contact": "2026-08-30",
    "you_replied_to_last": false, "unanswered_from_them": 2,
    "median_reply_minutes": 240
  },
  "thread": {"messages": 7, "spans_days": 12, "your_last_reply": "2026-08-18"}
}
```

`message_id` is the usual `FOLDER:UID` identifier.
