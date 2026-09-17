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

### Response envelope

Every response is wrapped in the standard envelope. The schema below describes `data`.

```json
{ "type": "success", "msg": "Insights computed successfully", "data": { ... } }
```

### What is guaranteed and what is not

**Only `data.window` is always present.** The other five blocks are produced independently, and a block is **omitted from the object entirely** — not set to `null`, not sent as `{}` — when the engine cannot compute it or when it raises (`guard()` in `engine.py` logs and drops it). `topics` is additionally gated on AI configuration.

| Key | Present when |
|---|---|
| `window` | always |
| `attention` | always in practice (computed for any record set) |
| `response_time` | at least one matched reply exists, in either direction |
| `rhythm` | at least one inbound or outbound message |
| `relationships` | at least one correspondent address was seen |
| `hygiene` | at least one windowed message **or** at least one draft |
| `topics` | AI provider configured **and** enabled **and** it returned usable output |

Clients must treat all six as optional and hide the section when the key is absent. Do not map a missing block to an empty one — "we could not compute this" and "this is zero" are different statements.

### data.window

Always present. `folders` is a **dynamic map** of folder name → message count, not a fixed set of keys.

| Field | Type | Notes |
|---|---|---|
| `from` | string | ISO 8601 with offset — start of the window |
| `to` | string | ISO 8601 with offset — **compute time**, not "now". On a cached rollup this is when the rollup was built |
| `messages_considered` | int | |
| `label` | string | the `window` param echoed back: `30d` \| `90d` \| `180d` \| `365d` |
| `folders` | object&lt;string, int&gt; | dynamic keys, e.g. `{"INBOX": 40, "Sent": 1, "Drafts": 0}` |
| `skipped` | int | records dropped as unparseable |
| `truncated` | bool | true when any folder hit `cap_per_folder` |
| `cap_per_folder` | int | server-side fetch cap |
| `cached` | bool | true when served from the cached rollup |

### data.attention

| Field | Type | Notes |
|---|---|---|
| `response_rate` | float \| **null** | 0–1, 3dp. **null when the denominator is zero** |
| `read_never_answered` | int | |
| `oldest_unread_days` | int \| **null** | null when nothing is unread |
| `backlog_trend` | string | `growing` \| `shrinking` \| `flat` |
| `awaiting_you` | array | max 10 — see below |
| `awaiting_them` | array | max 10 — see below |

`awaiting_you[]` — inbound, unanswered, older than 24h. Sorted by `waiting_days` descending.

| Field | Type | Notes |
|---|---|---|
| `message_id` | string | `FOLDER:UID` |
| `from` | string | |
| `subject` | string \| **null** | null on an empty subject |
| `waiting_days` | int | measured from the **first** unanswered message in the thread |

`awaiting_them[]` — **the element shape is not a mirror of `awaiting_you`. It carries `to`, not `from`.**

| Field | Type | Notes |
|---|---|---|
| `message_id` | string | `FOLDER:UID` |
| `to` | string | first non-holder, non-noreply recipient |
| `subject` | string \| **null** | |
| `waiting_days` | int | measured from that message |

### data.response_time

| Field | Type | Notes |
|---|---|---|
| `yours_median_minutes` | int \| **null** | null when you have sent no matched replies |
| `yours_p90_minutes` | int \| **null** | nearest-rank, an observed value |
| `theirs_median_minutes` | int \| **null** | |
| `trend` | string | `improving` \| `worsening` \| `flat` |
| `fastest` | array | max 5 |
| `slowest` | array | max 5 |

`fastest[]` and `slowest[]` share one element shape — and it is **not** the `top_correspondents` shape:

| Field | Type |
|---|---|
| `email` | string |
| `median_minutes` | int |

Both rank *their* reply times, needing ≥2 samples per correspondent. `slowest` is the same ranking reversed, so with few correspondents the two lists overlap.

### data.rhythm

| Field | Type | Notes |
|---|---|---|
| `arrivals_by_weekday_hour` | int[7][24] | **always exactly 7×24**, Monday first, hour 0–23, in the reported timezone |
| `after_hours_send_share` | float \| **null** | 0–1. Outside Mon–Fri 08:00–18:00. null when you sent nothing |
| `busiest_hour` | string \| **null** | `"HH:00"`, null when the grid is empty |
| `quietest_weekday` | string \| **null** | lowercase day name, **Mon–Fri only** |
| `timezone` | string | IANA name |
| `timezone_source` | string | `mailbox_preference` \| `deployment_default` — caption the chart with it |

### data.relationships

| Field | Type | Notes |
|---|---|---|
| `one_way_senders` | array | max 10 |
| `dormant` | array | max 10 |
| `internal_share` | float \| **null** | 0–1, same-domain share |
| `top_correspondents` | array | max 10 |

`one_way_senders[]` — sent you ≥3, never got a reply. `you_sent` is **always `0`** by definition; it exists so the UI can render the pair.

| Field | Type |
|---|---|
| `email` | string |
| `received` | int |
| `you_sent` | int (always 0) |

`dormant[]` — two-way before, quiet ≥2 months, ≥3 exchanged.

| Field | Type |
|---|---|
| `email` | string |
| `exchanged` | int |
| `months_since` | int |

`top_correspondents[]` — two-way only, ranked by total volume.

| Field | Type | Notes |
|---|---|---|
| `email` | string | |
| `sent` | int | |
| `received` | int | |
| `median_reply_minutes` | int \| **null** | **null is common** — it needs a matched reply from you |

### data.hygiene

| Field | Type | Notes |
|---|---|---|
| `monthly_growth_mb` | float | 1dp |
| `bounces` | int | DSN / MAILER-DAEMON heuristic |
| `stale_drafts` | int | untouched ≥7 days |
| `oldest_draft_days` | int \| **null** | null when there are no drafts |
| `automated_share` | float \| **null** | 0–1 |
| `attachment_heavy_senders` | array | max 5 |

`attachment_heavy_senders[]` — senders whose attachments total ≥1.0 MB in the window:

| Field | Type | Notes |
|---|---|---|
| `email` | string | |
| `mb` | float | 1dp |

### data.topics

Present only when AI is configured **and** enabled. It is a **bare array**, not an object:

```json
"topics": [ { "label": "Invoices and billing", "share": 0.31 } ]
```

| Field | Type | Notes |
|---|---|---|
| `label` | string | ≤60 chars |
| `share` | float | 0–1 |

### Why an array comes back empty

An empty array means "nothing cleared the threshold", not "not implemented" — these are the gates, so a small or young mailbox legitimately returns several empties at once:

| Array | Needs |
|---|---|
| `awaiting_you` | inbound, unanswered, ≥24h old |
| `awaiting_them` | your message is the thread's last, ≥24h old, with a real recipient |
| `fastest` / `slowest` | ≥2 timed replies **from** a correspondent |
| `one_way_senders` | ≥3 received, 0 sent |
| `dormant` | ≥3 exchanged, both directions, quiet ≥2 months |
| `top_correspondents` | ≥1 sent **and** ≥1 received |
| `attachment_heavy_senders` | ≥1.0 MB of attachments from one sender |

### Full example

Every block present and every array populated — the shape to map against. A real response will usually be sparser.

```json
{
  "type": "success",
  "msg": "Insights computed successfully",
  "data": {
    "window": {
      "from": "2026-06-18T07:13:04.640420+00:00",
      "to": "2026-09-16T07:13:04.640420+00:00",
      "messages_considered": 412,
      "label": "90d",
      "folders": { "INBOX": 380, "Sent": 28, "Drafts": 4 },
      "skipped": 0,
      "truncated": false,
      "cap_per_folder": 20000,
      "cached": false
    },
    "attention": {
      "response_rate": 0.62,
      "read_never_answered": 17,
      "oldest_unread_days": 12,
      "backlog_trend": "growing",
      "awaiting_you": [
        {
          "message_id": "INBOX:4",
          "from": "hr@techies.africa",
          "subject": "Re: Notice of unavailability",
          "waiting_days": 41
        }
      ],
      "awaiting_them": [
        {
          "message_id": "Sent:19",
          "to": "vendor@example.com",
          "subject": "Revised quote",
          "waiting_days": 6
        }
      ]
    },
    "response_time": {
      "yours_median_minutes": 94,
      "yours_p90_minutes": 612,
      "theirs_median_minutes": 208,
      "trend": "improving",
      "fastest": [{ "email": "ops@example.com", "median_minutes": 21 }],
      "slowest": [{ "email": "legal@example.com", "median_minutes": 2880 }]
    },
    "rhythm": {
      "arrivals_by_weekday_hour": [
        [0,0,0,0,0,0,0,0,1,0,0,1,0,0,0,0,0,0,0,0,1,0,0,0],
        [0,0,0,0,0,0,0,1,0,0,7,2,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,1,0,0,7,3,0,1,0,1,0,0,0,1,0,0,0,2,0,1,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0],
        [0,0,0,0,0,0,0,3,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,2,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
      ],
      "after_hours_send_share": 0.14,
      "busiest_hour": "07:00",
      "quietest_weekday": "thursday",
      "timezone": "Africa/Lagos",
      "timezone_source": "mailbox_preference"
    },
    "relationships": {
      "one_way_senders": [
        { "email": "appleid@id.apple.com", "received": 7, "you_sent": 0 }
      ],
      "dormant": [
        { "email": "oldclient@example.com", "exchanged": 14, "months_since": 5 }
      ],
      "internal_share": 0.051,
      "top_correspondents": [
        {
          "email": "hr@techies.africa",
          "sent": 9,
          "received": 11,
          "median_reply_minutes": 143
        }
      ]
    },
    "hygiene": {
      "monthly_growth_mb": 18.4,
      "bounces": 2,
      "stale_drafts": 3,
      "oldest_draft_days": 44,
      "automated_share": 0.368,
      "attachment_heavy_senders": [
        { "email": "design@example.com", "mb": 42.7 }
      ]
    },
    "topics": [
      { "label": "Invoices and billing", "share": 0.31 },
      { "label": "Recruitment", "share": 0.18 }
    ]
  }
}
```

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
