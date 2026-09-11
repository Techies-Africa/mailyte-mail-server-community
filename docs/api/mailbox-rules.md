# Mailbox Rules, Forwarding, Vacation and Blocked Senders

The mailbox holder's own mail-filtering surface: filter rules, forwarding, the vacation responder and the blocked-senders list. All four compile to Sieve and are stored in the holder's Dovecot account over ManageSieve, so they apply at delivery time to every client -- webmail, IMAP, POP3 -- not just to the webmail that set them.

**Base path:** `/api/v1/mailbox`
**Auth:** mailbox session -- the `mailyte_mailbox_session` cookie (plus the CSRF header on mutations) or `Authorization: Bearer <token>` from `POST /api/v1/mailbox-auth/login`. No endpoint here takes an account id: the session says which mailbox this is.

## Is it available? Check capabilities first

```
GET /api/v1/mailbox/capabilities
```

```json
{
  "type": "success",
  "msg": "Capabilities",
  "data": {
    "email_address": "jane@example.com",
    "capabilities": {
      "mail": true, "send": true, "settings": true, "two_factor": true,
      "rules": true, "forwarding": true, "vacation": true,
      "ai": false
    }
  }
}
```

`rules`, `forwarding` and `vacation` (and the blocked-senders list, which shares their backend) are advertised only when this deployment **can serve them right now**: a master credential is configured *and* a cached probe (every `SIEVE_PROBE_TTL` seconds, default 60) has just confirmed that Dovecot's ManageSieve service is reachable, negotiates TLS and offers a PLAIN login. A failure seen by a real request turns the capability off for the same interval; a success turns it back on immediately. Render nothing for a capability that is not advertised -- that is the contract, and it is what keeps a dead backend from showing up as a control that errors.

## How the scripts are laid out

Sieve allows one active script per mailbox, and this surface manages four independent features, so each feature is its own *component* script and one small *active* script includes them, in a fixed order:

| Script name | Feature | Runs |
|---|---|---|
| `mailyte-main` | the active script; `require ["include"]` and nothing else | -- |
| `mailyte-blocked-senders` | blocked senders | 1st |
| `mailyte-rules` | filter rules | 2nd |
| `mailyte-forwarding` | forwarding | 3rd |
| `mailyte-vacation` | vacation responder | 4th |

Every include is `:optional`, so a mailbox that has only ever used one feature still compiles. The order is deliberate: a blocked message is filed and processing **stops**, so it is neither forwarded nor auto-replied to; rules run before forwarding so `redirect :copy` still forwards a message a rule filed elsewhere; vacation runs last so nothing already discarded draws a reply.

Each component carries its settings in a marker comment (`# mailyte-rule: {...}`, `# mailyte-forwarding: {...}`, ...). Reading is exact for anything the API wrote. A script under one of these names that the API did *not* write (edited by hand over ManageSieve, or by another client) is reported with `"managed": false` and no settings -- saving through the API then replaces it. Scripts under any other name are never read, written or deleted.

!!! note "Mailboxes migrated from mailcow"
    Mailboxes that came over from the mailcow/SOGo server carry an inactive script named `sogo` in their ManageSieve account. It is ignored by every endpoint here -- it is not one of the managed names -- and it is never deleted. It has no effect on delivery while `mailyte-main` is the active script.

!!! warning "Limits enforced by the server"
    Dovecot compiles every script on upload: `sieve_max_actions = 32`, `sieve_max_redirects = 4`, `sieve_max_script_size = 1M`. A script the compiler refuses comes back as a `400` carrying the compiler's own diagnostic.

## Forwarding

```
GET /api/v1/mailbox/forwarding
PUT /api/v1/mailbox/forwarding
```

Backed by Sieve `redirect` (with `:copy` when a copy is kept), never by a Postfix alias -- the alias route delivered two copies of every forwarded message.

**Request body (PUT)**

| Field | Type | Description |
|---|---|---|
| `enabled` | boolean | Turn forwarding on or off. Off keeps the addresses so turning it back on is not a retype. |
| `addresses` | string[] | Up to 5 addresses. The mailbox's own address is dropped (it would be a loop). |
| `keep_copy` | boolean | `true` (default) forwards *and* delivers locally; `false` forwards only. |

```bash
curl -X PUT -b cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -d '{"enabled": true, "addresses": ["jane.backup@gmail.com"], "keep_copy": true}' \
  https://api.example.com/api/v1/mailbox/forwarding
```

**Response (GET and PUT)**

```json
{"type": "success", "msg": "Forwarding retrieved successfully",
 "data": {"enabled": true, "addresses": ["jane.backup@gmail.com"], "keep_copy": true, "managed": true}}
```

`422` when `enabled` is true with no usable address, or with more than 5.

## Rules

```
GET /api/v1/mailbox/rules
PUT /api/v1/mailbox/rules
```

`PUT` **replaces** the whole list (at most 50 rules). Rules run in list order; a rule with no usable condition or no usable action is kept in the list but emits no Sieve.

**Rule object**

| Field | Type | Description |
|---|---|---|
| `id` | string | ULID; assigned when omitted. |
| `name` | string | Display name. |
| `match` | `"all"` \| `"any"` | Combine conditions with `allof` / `anyof`. Default `all`. |
| `conditions` | object[] | `{"field", "operator", "value"}` -- see below. |
| `actions` | object[] | `{"type", "value"}` -- see below. |
| `enabled` | boolean | A disabled rule round-trips but emits no Sieve. Default `true`. |

**Conditions.** `field`: `from`, `to`, `cc`, `subject` (header tests) or `body` (`body :text`). `operator`: `contains` (default), `is`, `matches` (Sieve wildcards `*` and `?`).

**Actions.** `move` (`value` = folder name; `fileinto`), `flag` (adds `\Flagged`), `mark_read` (adds `\Seen`), `forward` (`value` = address; `redirect :copy`), `discard`.

```bash
curl -X PUT -b cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{
  "rules": [{
    "name": "Newsletters",
    "match": "any",
    "conditions": [{"field": "subject", "operator": "contains", "value": "newsletter"},
                   {"field": "from", "operator": "matches", "value": "*@news.example.com"}],
    "actions": [{"type": "move", "value": "Newsletters"}, {"type": "mark_read"}]
  }]}' https://api.example.com/api/v1/mailbox/rules
```

**Response (GET and PUT)**

```json
{"type": "success", "msg": "Rules retrieved successfully",
 "data": {"rules": [{"id": "01J...", "name": "Newsletters", "match": "any",
                     "conditions": [...], "actions": [...], "enabled": true}],
          "managed": true}}
```

## Vacation responder

```
GET /api/v1/mailbox/vacation
PUT /api/v1/mailbox/vacation
```

Sieve `vacation` with `:days 1` (at most one reply per sender per day) and `:addresses` set to the mailbox's own address, so a message merely Cc'd to a list this mailbox reads does not trigger a reply. Sieve itself also never replies to bulk mail, lists or auto-submitted messages.

**Request body (PUT)**

| Field | Type | Description |
|---|---|---|
| `enabled` | boolean | |
| `subject` | string, ≤500 | Optional; the server's default is `Auto: <original subject>`. |
| `message` | string, ≤20000 | Required when `enabled`. Plain text. |
| `start_date`, `end_date` | string | Stored and returned for display. **Not enforced by the script**: when `enabled` is true the responder is active regardless of these dates. |

`422` when `enabled` is true with an empty message.

**Response (GET and PUT)**

```json
{"type": "success", "msg": "Vacation retrieved successfully",
 "data": {"enabled": true, "subject": "Out of office", "message": "Back on Monday.",
          "start_date": null, "end_date": null, "managed": true}}
```

## Blocked senders

```
GET    /api/v1/mailbox/blocked-senders
POST   /api/v1/mailbox/blocked-senders
DELETE /api/v1/mailbox/blocked-senders/{address}
```

**Semantics.** Mail whose visible **From** address is on the list is filed to the **Junk** folder at delivery (Dovecot's `\Junk` special-use mailbox, the same folder the spam filter uses) and processing stops -- it is not forwarded and does not receive a vacation reply. Nothing is discarded: the block is visible and reversible, and unblocking never has to recover anything. Matching is the whole address, case-insensitive (`address :is "from"` with Sieve's default comparator). The envelope sender is deliberately not consulted; it is what lists and forwarders rewrite.

Addresses are validated (dot-atom local part, dotted domain, ≤254 characters), lower-cased, and de-duplicated. The list holds at most 500 addresses. A mailbox cannot block its own address.

**List**

```bash
curl -b cookies.txt https://api.example.com/api/v1/mailbox/blocked-senders
```

```json
{"type": "success", "msg": "Blocked senders retrieved successfully",
 "data": {"addresses": ["spam@example.net"], "managed": true, "folder": "Junk", "limit": 500}}
```

**Block**

```bash
curl -X POST -b cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -d '{"address": "Spam@Example.net"}' https://api.example.com/api/v1/mailbox/blocked-senders
```

Returns the updated list. Blocking an address that is already blocked returns `200` with `"msg": "Sender is already blocked"` and writes nothing. `422` for an invalid address, the mailbox's own address, or a full list.

**Unblock**

```bash
curl -X DELETE -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  https://api.example.com/api/v1/mailbox/blocked-senders/spam%40example.net
```

Returns the updated list. Unblocking an address that is not on the list returns `200` with `"msg": "Sender was not blocked"`. Mail already filed to Junk stays there.

**What gets written** (the `mailyte-blocked-senders` component):

```sieve
# mailyte-blocked-senders: {"addresses":["spam@example.net"]}
require ["fileinto", "mailbox"];

if address :is "from" ["spam@example.net"] {
    fileinto :create "Junk";
    stop;
}
```

With an empty list the script is comments only -- deliberately not `keep;`, because an explicit `keep` is an action that would deliver an INBOX copy alongside anything a rule files elsewhere.

## Errors

All four features share these. See [errors.md](errors.md) for the envelope.

| Status | `error_code` | Meaning | What to do |
|---|---|---|---|
| `503` | `sieve_not_configured` | This deployment has no ManageSieve master credential (`IMAP_MASTER_USER` / `IMAP_MASTER_PASSWORD`). Permanent until an operator sets one. | Hide the features; `/capabilities` already reports them `false`. |
| `503` | `sieve_unavailable` | The credential exists but Dovecot's ManageSieve could not be reached or logged into for this request (or answered `TRYLATER`). Transient. | Retry later. `/capabilities` reports the features `false` for the next `SIEVE_PROBE_TTL` seconds. |
| `400` | -- | Dovecot's Sieve compiler refused the generated script, or a quota (`QUOTA/MAXSIZE`, `QUOTA/MAXSCRIPTS`). `msg` carries the server's diagnostic. | Show `msg`; the caller can act on it. |
| `422` | -- | Semantic validation (bad address, self-forward, over a list limit, enabled with nothing to do). | Show `msg`. |
| `502` | -- | ManageSieve refused a command for a reason that is neither transport nor compiler (should not happen; logged server-side). | Report it. |

A mailbox that has never saved a feature is **not** an error: `GET` returns the empty/off shape with `"managed": true`. (Until 2026-08-31 it was a `502`: Pigeonhole answers a missing script with `NO (NONEXISTENT) "Sieve script ... not found"` and the client matched only the RFC's example wording. The response code is now what is checked.)

## Operator notes

| Variable | Default | Purpose |
|---|---|---|
| `IMAP_MASTER_USER`, `IMAP_MASTER_PASSWORD` | -- | Dovecot master user (see `mailer/dovecot/config/master-users`). Without both, every feature here is off and reports `sieve_not_configured`. |
| `SIEVE_HOST`, `SIEVE_PORT` | `dovecot`, `4190` | Where ManageSieve is. Falls back to `IMAP_HOST`. |
| `SIEVE_TIMEOUT` | `15` | Seconds per command connection. |
| `SIEVE_PROBE_TTL` | `60` | How long a capability probe result (either way) is trusted. |
| `SIEVE_PROBE_TIMEOUT` | `3` | Seconds the probe waits before declaring the service unreachable. |
| `SIEVE_PROBE_ACCOUNT` | -- | Optional existing mailbox (e.g. `postmaster@example.com`). When set, the probe performs a full master-user login as it, which also proves the master credential; otherwise the probe stops at "reachable, TLS negotiated, PLAIN offered" and a bad credential is detected by the first real request instead. |

The api service reaches Dovecot over the compose network; port 4190 does not need to be published on the host for any of this. Dovecot's `ssl = required` is satisfied by STARTTLS, which the client always negotiates when offered (certificate not verified: the connection is to the service name on the container network, not the public hostname).
