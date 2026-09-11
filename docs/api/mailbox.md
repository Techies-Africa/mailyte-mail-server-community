# Mailbox (Webmail & Mobile)

The mailbox-**holder** surface -- what the open-source webmail and the mobile
app are built on. Everything lives under the `/api/v1/mailbox` prefix and is
authenticated by a **mailbox session**
(see [Authentication -- Mailbox Sessions](authentication.md#mailbox-sessions-webmail)):
the `mailyte_mailbox_session` cookie the login sets, or
`Authorization: Bearer <token>` for a native client.

**No route here carries an account id.** The session says which mailbox the
request is for, which makes cross-mailbox access structurally impossible
rather than a per-route check. Responses use the standard envelope
(`{"type", "msg", "data"}`); some errors additionally carry a stable
`error_code` the client can branch on. A `502` always means the mail server
itself was unreachable -- nothing the caller sends can fix it.

!!! note "Message ids are `FOLDER:UID`"
    A message id scopes the IMAP UID with the folder that holds it, e.g.
    `INBOX:1042` or `Clients/Acme:7`. Moving a message (including to Trash)
    gives it a **new id**, returned by the move. Treat ids as opaque and
    short-lived: a folder's `uid_validity` changing invalidates every id
    minted from it.

This page covers folders, messages, sending, contacts and settings. The same
router also serves [`GET /capabilities`](#capabilities), the security/2FA
group, optional AI assistance, and the Sieve-backed rules/forwarding/vacation
group -- the capability manifest says which of those exist on a deployment.

## Capabilities

```
GET /api/v1/mailbox/capabilities
```

Runtime feature detection: which optional groups are configured on **this**
deployment (`rules`, `forwarding`, `vacation`, `ai`, ...). Clients fetch it
right after sign-in and hide any control it does not advertise.

## Folders

### List Folders

```
GET /api/v1/mailbox/folders
```

**Response `data`** -- an array, INBOX first, then special-use, then the rest
alphabetically:

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable folder identifier (md5 of the name). This is what the folder routes below take in their path |
| `name` | string | Full IMAP name, `/`-separated for nested folders (`Clients/Acme`) |
| `role` | string\|null | `inbox`, `drafts`, `sent`, `junk`, `trash`, `archive`, or null for a user folder. Detected from the SPECIAL-USE attribute first, well-known names second |
| `total` / `unread` | integer | Message counts |
| `uid_next` / `uid_validity` | integer | IMAP change tokens |

!!! tip "Cheap change detection"
    Poll this list and compare `uid_next` / `uid_validity` per folder:
    unchanged tokens mean nothing arrived, moved or was deleted -- one
    request, no messages transferred.

### Create Folder

```
POST /api/v1/mailbox/folders
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | 1-255 chars; no `/`, `\` or control characters |

Creates and subscribes the folder. Labels **are** folders here. Returns the
full folder list.

### Rename Folder

```
PATCH /api/v1/mailbox/folders/{folder}
```

`{folder}` is the `id` from the folder list (the literal name is also
accepted for flat folders, but names can contain `/`, so the id is the form
to store).

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | The new **last segment** only; same character rules as creation |

A nested folder keeps its parent (`Clients/Acme` renamed to `Acme Ltd`
becomes `Clients/Acme Ltd`), and subfolders move along with it. Returns
`{"id", "name", "folders"}` -- the rename changes the folder's id, and every
message id minted under the old name with it.

**Errors**

| Status | `error_code` | When |
|---|---|---|
| `409` | `cannot_rename_special_folder` | INBOX or a special-use folder (Sent, Drafts, Junk, Trash, Archive -- by SPECIAL-USE attribute *or* well-known name) |
| `400` | -- | Name already exists, reserved, or refused by the server |

### Delete Folder

```
DELETE /api/v1/mailbox/folders/{folder}
```

Deletes an **empty**, non-special folder (unsubscribing it first) and returns
the updated folder list.

**Errors**

| Status | `error_code` | When |
|---|---|---|
| `409` | `cannot_delete_special_folder` | INBOX or a special-use folder |
| `409` | `folder_not_empty` | Messages still inside. Deliberate: IMAP DELETE destroys contents with no Trash step, so the holder empties the folder first. **The client should warn the holder**, not retry |
| `409` | `folder_has_subfolders` | Subfolders still inside |
| `404` | -- | No such folder |

## Messages

### List Messages

```
GET /api/v1/mailbox/messages
```

**Query Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `INBOX` | Folder **name** (not id). Omit it *while passing a search* to search the whole account |
| `search` / `q` | string | -- | Server-side IMAP search over headers **and bodies** (both names accepted) |
| `unread` | boolean | `false` | Only unread |
| `starred` | boolean | `false` | Only starred |
| `limit` | integer | `50` | 1-200 |
| `offset` | integer | `0` | Paging offset |

**Response `data`**: `{"messages", "total", "offset", "limit", "has_more",
"folder"}`. Each row carries:

`id`, `folder`, `subject`, `from`/`to`/`cc`/`bcc`/`reply_to` (arrays of
`{name, email}`), `received_at` (ISO 8601 or null), `size`,
`has_attachment`, `is_read`, `is_starred`, `is_answered`, `is_draft`,
`preview` (~200 chars or null), and the threading identifiers below.

**Threading fields** (list rows *and* detail, always present, null when the
message carries no identifier):

| Field | Description |
|---|---|
| `message_id` | The RFC 5322 `Message-ID`, unfolded |
| `in_reply_to` | The `In-Reply-To` header, unfolded |
| `references` | The `References` header, unfolded (single-spaced) |
| `thread_id` | The conversation key: the **first `References` token** (the root of the chain), else the `In-Reply-To` id, else the message's own `message_id`. Derived identically for list rows and detail, so grouping on it never disagrees between the two |

`is_answered` reflects the IMAP `\Answered` flag; the send route sets it on
the original when a reply goes out (see [Send](#send-a-message)).

### Read One Message

```
GET /api/v1/mailbox/messages/{message_id}
```

Everything a list row has, plus `body_text`, `body_html` (each string or
null), `attachments` (below), and the **provenance block** -- fields read
from headers the *receiving* side stamped, never from anything the sender
wrote for display. Each is always present and **null when absent**:

| Field | Type | Description |
|---|---|---|
| `mailed_by` | string\|null | Domain of the `Return-Path` (the envelope sender). Null for the null sender (`<>`) and for messages never received (e.g. your own Sent copies) |
| `signed_by` | string\|null | The `d=` domain of the message's DKIM signature. When several signatures exist, the one `Authentication-Results` reports as `dkim=pass` wins; otherwise the first signature's `d=` is still reported -- read `authentication.dkim` for the verdict |
| `security` | string\|null | `"tls"` when the receiving hop for this server used TLS (`ESMTPS`), `"none"` when it did not. Internal hops (Dovecot LMTP, loopback re-injection) are skipped. Null when the message has no `Received` trail at all |
| `list_unsubscribe` | object\|null | `{"mailto", "url", "one_click"}` parsed from `List-Unsubscribe`. `one_click` is true only for RFC 8058 compliance: an `https` target **and** `List-Unsubscribe-Post: List-Unsubscribe=One-Click` |
| `authentication` | object | `{"spf", "dkim", "dmarc"}`, each `pass` \| `fail` \| `softfail` \| `neutral` \| `none` \| `temperror` \| `permerror` \| null, from the topmost `Authentication-Results` (the one this server's resolver wrote at ingress) |

!!! note "Detail only"
    The provenance block requires the full header block, which list rows
    deliberately never fetch -- rows stay one cheap batched FETCH per page.

Each entry of `attachments`:

| Field | Description |
|---|---|
| `index` | Position in MIME walk order -- the handle the download and forward routes take |
| `name`, `type`, `size` | Filename, content type, decoded size in bytes |
| `is_inline` | Declared `Content-Disposition: inline` (usually a picture shown in the body) |
| `content_id` | The `Content-ID` the HTML references as `cid:<content_id>`, or null |

Reading a message does **not** mark it read (the fetch uses `BODY.PEEK`);
the client says so explicitly via `/mark-read`.

### Download the Original (raw RFC 822)

```
GET /api/v1/mailbox/messages/{message_id}/raw
```

The stored bytes exactly as the mail server holds them -- headers, MIME
structure and encodings untouched, never a re-serialisation. Returned as
`Content-Type: message/rfc822` with `Content-Disposition: attachment` and a
sanitised `<subject>-<uid>.eml` filename. This is what "Show original" /
"Download .eml" load. Does not mark the message read.

### Attachments

```
GET /api/v1/mailbox/messages/{message_id}/attachments
GET /api/v1/mailbox/messages/{message_id}/attachments/{index}
```

The list route returns the same array as the detail view. The download route
streams the decoded part with its own content type; `Content-Disposition` is
always `attachment` (rendering sender-supplied HTML in the app's origin
would be stored XSS). A stale `index` is a `404`.

### Thread

```
GET /api/v1/mailbox/messages/{message_id}/thread
```

The conversation the message belongs to, oldest first, as list-row objects.
Follows `Message-ID` / `In-Reply-To` / `References` -- never subject lines --
searching the message's own folder plus Sent.

### Flags

```
POST /api/v1/mailbox/messages/{message_id}/mark-read
POST /api/v1/mailbox/messages/{message_id}/mark-unread
POST /api/v1/mailbox/messages/{message_id}/star
POST /api/v1/mailbox/messages/{message_id}/unstar
```

Each sets or clears one IMAP system flag and answers
`{"type": "success", "msg": "Message updated"}`.

### Move, Trash, Delete

```
POST /api/v1/mailbox/messages/{message_id}/move      {"folder": "<name>"}
POST /api/v1/mailbox/messages/{message_id}/trash
DELETE /api/v1/mailbox/messages/{message_id}
```

`move` and `trash` return the message's **new id**. Trashing something
already in Trash is a no-op, never a destroy. Permanent deletion is refused
with `409` / `error_code: not_in_trash` unless the message is already in
Trash -- nothing outside Trash is ever destroyed.

## Drafts

```
POST /api/v1/mailbox/messages/draft
DELETE /api/v1/mailbox/messages/draft/{message_id}
```

Drafts are real messages in the Drafts folder, readable by every client on
the account. Fields: `to`/`cc`/`bcc` (arrays or one comma-separated string),
`subject`, `body_text`, `body_html`, `in_reply_to`, `references`, and
`replace_id` (alias `replaces_id`) -- the draft this save supersedes, expunged
only **after** the new copy lands. All fields tolerate being absent: autosave
never fails on a half-typed message. Returns `{"id", "folder"}`.

The DELETE route permanently discards a draft (allowed outside Trash --
a discarded draft was never delivered mail).

## Send a Message

```
POST /api/v1/mailbox/messages/send
```

Accepts **JSON** (no uploaded files) or **multipart/form-data** (with
files). The From address is always the signed-in mailbox -- nothing the
client sends can change it. Repeated fields are accepted both bare (`to`)
and PHP-style (`to[]`).

| Field | Type | Required | Description |
|---|---|---|---|
| `to`, `cc`, `bcc` | array\|string | at least one recipient total | Bare addresses or `Name <addr>` forms; comma/semicolon lists accepted |
| `subject` | string | No | |
| `body_text`, `body_html` | string | one of them | `data:` images in the HTML are converted to Content-ID parts on send |
| `in_reply_to` | string | No | The RFC `message_id` of the message being replied to (brackets optional). Sets the reply's `In-Reply-To` header **and flags the original `\Answered`** after a successful submit |
| `in_reply_to_id` | string | No | The API id (`FOLDER:UID`) of the message being replied to. Optional hint that lets the `\Answered` flag land without a search; verified against the `Message-ID` before being trusted |
| `references` | string | No | The reply's `References` chain (the original's `references` plus its `message_id`) |
| `attachments` | file(s) | No | Multipart only; up to 20 files, 25 MB total |
| `forward_attachments_from` | string | No | The API id of a message in **this** mailbox whose attachments should be carried over (forwarding) -- the client never re-uploads them |
| `attachment_indexes` | array of int \| string | No | Which `index` values from that message to carry (JSON array, repeated fields, or `"1,3"`). A stale index is a `422` naming it |

Forwarded inline parts whose `content_id` the new `body_html` still
references (`cid:<content_id>`) go out as inline parts under the **same
id**, so pictures render where the original showed them; carried parts the
body no longer references become ordinary attachments rather than being
dropped. The 20-attachment / 25 MB limits apply to uploads and carried
parts together.

**Response `data`**: `{"id", "message_id", "answered_id"}` -- the Sent copy's
API id (null if filing failed after a successful send), the new message's
RFC `Message-ID`, and the API id of the message that was flagged `\Answered`
(null when `in_reply_to` was absent or the original could not be found).

!!! note "Person-to-person mail is not tracked"
    Campaign mail on this platform gets an open pixel and rewritten links
    injected in transit. Mailbox sends **opt out by default**: the API adds
    an internal `X-Mailyte-Tracking: off` header, the filter skips injection
    and strips the header before delivery, so it never reaches a recipient.
    A deployment that wants mailbox sends tracked sets
    `MAILBOX_SEND_TRACKING=true` on the api service.

**Example (JSON reply)**

```bash
curl -X POST -H "Authorization: Bearer $MAILBOX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["bob@example.net"],
    "subject": "Re: lunch?",
    "body_text": "Sounds good.",
    "in_reply_to": "<m1@example.net>",
    "in_reply_to_id": "INBOX:1042",
    "references": "<m0@example.net> <m1@example.net>"
  }' \
  http://your-server:8083/api/v1/mailbox/messages/send
```

**Example (forward with carried attachments)**

```bash
curl -X POST -H "Authorization: Bearer $MAILBOX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["carol@example.com"],
    "subject": "Fwd: contract",
    "body_html": "<p>FYI</p>",
    "forward_attachments_from": "INBOX:1042",
    "attachment_indexes": [2, 3]
  }' \
  http://your-server:8083/api/v1/mailbox/messages/send
```

## Contacts

```
GET /api/v1/mailbox/contacts
```

Addresses this mailbox actually corresponds with -- harvested from Sent and
INBOX headers, most-used first, as `{name, email, count}` -- for compose
autocomplete. There is no address book to maintain.

## Settings

### Read Settings

```
GET /api/v1/mailbox/settings
```

**Response `data`**

| Field | Description |
|---|---|
| `email_address`, `name` | The signed-in mailbox |
| `signature_html` | Sanitised HTML signature (empty string when unset) |
| `signature_on_reply` | Whether the signature is added to replies too |
| `display_density` | `comfortable` or `compact` |
| `undo_send_enabled`, `undo_send_seconds` | Undo window; seconds is one of 5, 10, 20, 30 |
| `storage` | Live quota from the mail server: `used_mb`, `quota_mb`, `percentage` (null = unlimited), `used_bytes`, `quota_bytes`, `messages` |

### Update Settings

```
PUT /api/v1/mailbox/settings
```

Partial update -- only the fields present change. The signature is sanitised
against an allowlist before storage (it is HTML this server later attaches
to outgoing mail); an unsafe signature is a `422` with the reason. Returns
the same payload as the read.

## Related groups on this router

* **Security & two-factor** -- `GET /security`, `POST /security/2fa/*`,
  `GET/DELETE /security/sessions*`. Mailbox 2FA gates the **webmail sign-in
  only**, never IMAP/SMTP.
* **AI assistance** -- `POST /ai/compose`, `POST /ai/summarize/{message_id}`;
  present only when the deployment configures an endpoint
  (`503` / `ai_not_configured` otherwise).
* **Rules, forwarding, vacation** -- `GET/PUT /rules`, `/forwarding`,
  `/vacation`; Sieve-backed, absent without a ManageSieve master credential
  (`503` / `sieve_not_configured`).
