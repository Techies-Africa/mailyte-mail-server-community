# Postfix Configuration

How Mailyte's SMTP server is set up — virtual domains, TLS, authentication, rate limiting, and the tracking filter.

---

Postfix handles all inbound and outbound email. It talks to MySQL for domain and mailbox lookups, hands off authentication to Dovecot, and pipes outgoing mail through the tracking content filter. The config is **baked into the image** from `mailer/postfix/config/` (`main.cf`, `master.cf`, the MySQL lookup files, and header/body checks); at container start, `entrypoint.sh` runs `envsubst` over the MySQL lookup files and applies environment-driven overrides with `postconf -e`.

## main.cf — The Core Config

The main Postfix config lives at `/etc/postfix/main.cf` (source: `mailer/postfix/config/main.cf`). Here are the key sections as actually shipped.

### Server Identity

```ini
myhostname = mail.example.com    # overridden at start: postconf -e "myhostname=${HOSTNAME}"
mydomain = example.com           # derived from $HOSTNAME by the entrypoint
myorigin = $mydomain
mydestination = localhost
mynetworks = 127.0.0.0/8 [::1]/128   # entrypoint appends ${POSTFIX_MYNETWORKS:-172.25.0.0/16}
compatibility_level = 3.6
smtpd_helo_required = yes
disable_vrfy_command = yes
disable_etrn_command = yes
smtpd_forbid_bare_newline = reject
```

`mydestination` is set to `localhost` because all real domains are handled as virtual domains through MySQL. This is intentional — don't add your domain here.

### Virtual Domain Setup

Postfix queries MySQL through the `proxy:` prefix (the `proxymap` daemon keeps persistent connections; it's also required because `smtpd` runs chrooted):

```ini
virtual_mailbox_domains = proxy:mysql:/etc/postfix/mysql-virtual-mailbox-domains.cf
virtual_mailbox_maps    = proxy:mysql:/etc/postfix/mysql-virtual-mailbox-maps.cf
virtual_alias_maps      = proxy:mysql:/etc/postfix/mysql-virtual-alias-maps.cf
smtpd_sender_login_maps = proxy:mysql:/etc/postfix/mysql-sender-login-maps.cf
virtual_transport = lmtp:inet:dovecot:24
virtual_mailbox_base = /var/mail/vhosts
virtual_uid_maps = static:5000
virtual_gid_maps = static:5000
```

Mail for virtual domains is delivered to the Dovecot container over LMTP on TCP port 24 (a Unix socket can't cross container boundaries).

### TLS Settings

```ini
# Inbound TLS (other servers connecting to us)
smtpd_tls_cert_file = /etc/ssl/certs/server.crt
smtpd_tls_key_file = /etc/ssl/private/server.key
tls_server_sni_maps = hash:/etc/postfix/sni_certs.map
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
smtpd_tls_protocols = !SSLv2,!SSLv3,!TLSv1,!TLSv1.1
smtpd_tls_ciphers = high
smtpd_tls_exclude_ciphers = aNULL, MD5, DES, RC4, 3DES, ADH, EXPORT, LOW

# Outbound TLS (us connecting to other servers)
smtp_tls_security_level = may
smtp_tls_protocols = !SSLv2,!SSLv3,!TLSv1,!TLSv1.1
smtp_tls_loglevel = 1
smtp_tls_note_starttls_offer = yes
```

The `server.crt`/`server.key` pair and the SNI map are produced by cert_manager (see [SSL Certificates](ssl-certificates.md)) — the entrypoint copies them into place and runs `postmap -F hash:` on the SNI map at every start.

> [!NOTE]
> `smtpd_tls_security_level = may` means TLS is offered but not required for server-to-server mail. This is the standard for email — requiring TLS would break delivery to many servers that don't support it. For user authentication, `smtpd_tls_auth_only = yes` ensures credentials are never sent in the clear.

### SASL Authentication (via Dovecot)

```ini
smtpd_sasl_type = dovecot
smtpd_sasl_path = inet:dovecot:24100
smtpd_sasl_auth_enable = yes
smtpd_sasl_security_options = noanonymous
smtpd_sasl_local_domain = $myhostname
```

Postfix doesn't do authentication itself. It delegates to Dovecot's auth service over TCP port 24100 (containers can't share a Unix socket). Dovecot checks the `email_accounts` table for mailbox logins and the `smtp_credentials` table for SMTP API keys (see [Dovecot Configuration](dovecot-configuration.md)).

### Sender Login Enforcement

`smtpd_sender_login_maps` maps a MAIL FROM address to the SASL logins allowed to use it — a mailbox owns its own address, an alias's destinations own the alias, and a domain-scoped SMTP credential owns every address in its domain:

```sql
SELECT email FROM email_accounts WHERE email='%s' AND status = 'active'
UNION
SELECT destination FROM aliases WHERE source='%s' AND active = 1
UNION
SELECT sc.username FROM smtp_credentials sc
JOIN domains d ON sc.domain_id = d.id
WHERE d.domain = SUBSTRING_INDEX('%s', '@', -1) AND sc.active = 1
```

> [!WARNING]
> `reject_sender_login_mismatch` is enforced **only** in the port 587/465 `master.cf` overrides (placed *before* `permit_sasl_authenticated`), never in the global `smtpd_sender_restrictions`. Putting it in the global value makes port 25 demand a SASL login from every connecting MTA and breaks **all inbound mail** — this happened in production on 2026-08-22.

### Message Size Limit

```ini
message_size_limit = 52428800
mailbox_size_limit = 0
```

The 50 MB limit is baked into `main.cf` (it is *not* env-configurable — the `POSTFIX_MESSAGE_SIZE_LIMIT` entry in `.env.example` is not read by the entrypoint).

### Rspamd Integration

```ini
milter_protocol = 6
milter_default_action = accept
smtpd_milters = inet:rspamd:11332
non_smtpd_milters = inet:rspamd:11332
milter_mail_macros = i {mail_addr} {client_addr} {client_name} {auth_authen}
```

All incoming and outgoing mail passes through Rspamd for spam checking and DKIM signing.

> [!WARNING]
> The `milter_default_action = accept` line means that if Rspamd goes down, mail is accepted without scanning. Change this to `tempfail` in high-security environments so mail gets deferred instead.

### Postscreen (Port 25)

Port 25 is fronted by `postscreen`, which weeds out botnet clients before they reach an smtpd process:

```ini
postscreen_greet_action = enforce
postscreen_dnsbl_sites = zen.spamhaus.org*3, b.barracudacentral.org*2,
    bl.spamcop.net*2, dnsbl.sorbs.net*1, psbl.surriel.com*1
postscreen_dnsbl_threshold = 3
postscreen_dnsbl_action = enforce
postscreen_access_list = permit_mynetworks
```

The deeper protocol tests (`postscreen_non_smtp_command_enable`, `postscreen_bare_newline_enable`, `postscreen_pipelining_enable`) are deliberately **off** — enabling them delayed and broke delivery from large providers.

### Logging

```ini
maillog_file = /var/log/postfix/mail.log
```

Postfix logs to a file via its built-in `postlogd` (the `postlog` service in `master.cf`) — there is no syslog daemon in the container. The `log_ingestor` service tails this file to populate the `mail_logs` and `delivery_events` tables.

## master.cf — Service Definitions

`master.cf` is baked into the image and never modified at runtime. The listening services:

| Service | Port | Purpose |
|---------|------|---------|
| `smtp` (postscreen) | 25 | Server-to-server mail; postscreen in front, no auth |
| `submission` | 587 | Authenticated submission (STARTTLS required) |
| `smtps` | 465 | Authenticated submission (implicit TLS) |
| `10587` | 10587 (internal only) | Unauthenticated submission for trusted internal services (`permit_mynetworks,reject`); not published to the host |
| `127.0.0.1:10026` | 10026 | Re-injection listener for the tracking filter (`content_filter=` cleared to avoid loops) |

Port 587/465 overrides (both identical apart from `smtpd_tls_wrappermode=yes` on 465):

```ini
submission inet n - y - - smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_tls_auth_only=yes
  -o smtpd_reject_unlisted_recipient=yes
  -o smtpd_sender_restrictions=reject_sender_login_mismatch,permit_sasl_authenticated,reject
  -o milter_macro_daemon_name=ORIGINATING
  -o content_filter=tracking-filter:
```

Plus tighter per-client limits on submission: connection rate 10, connection count 20, message rate 50, recipient rate 100.

### Tracking Filter

Outgoing mail on 587/465 (and the internal 10587 listener) passes through the `tracking-filter` pipe service:

```ini
tracking-filter unix - n n - 25 pipe
  flags=Rq user=vmail argv=/usr/local/bin/tracking_injector.py -f ${sender} -- ${recipient}
```

The injector rewrites links, adds an open-tracking pixel, then re-injects the message via SMTP to `127.0.0.1:10026`. It reads its settings (`TRACKING_ENABLED`, `TRACKING_SERVICE_URL`, DB credentials, …) from `/etc/postfix/runtime.env`, a file the entrypoint writes at start — Postfix's `import_environment` does not pass env vars to `pipe`/`spawn` children.

Two policy services also run from `master.cf`:

- `policy-rate-limit` (`rate_limit_policy.py`) — consulted at exactly one point, `smtpd_data_restrictions` (adding it elsewhere double-counts messages).
- `policy-ip-access` (`ip_access_policy.py`) — consulted from `smtpd_recipient_restrictions`.

An inbound `webhook-filter` pipe service (`webhook_sender.py`) emits inbound-mail webhooks.

## MySQL Lookup Files

These live in `/etc/postfix/` and get their credentials substituted from `DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT`/`DB_NAME` at container start:

| File | Query |
|------|-------|
| `mysql-virtual-mailbox-domains.cf` | `SELECT 1 FROM domains WHERE domain='%s' AND active = 1` |
| `mysql-virtual-mailbox-maps.cf` | `SELECT 1 FROM email_accounts WHERE email='%s' AND status = 'active'` |
| `mysql-virtual-alias-maps.cf` | `SELECT destination FROM aliases WHERE source='%s' AND active = 1` |
| `mysql-sender-login-maps.cf` | 3-branch UNION shown above |

> [!TIP]
> These files contain database credentials after substitution. The entrypoint sets them to `640`, owner `root:postfix` — keep them that way.

## Rate Limiting

Per-client (per-IP) limits at the SMTP level, from `main.cf`:

```ini
smtpd_client_connection_rate_limit = 30
smtpd_client_connection_count_limit = 50
smtpd_client_message_rate_limit = 100
smtpd_client_recipient_rate_limit = 200
anvil_rate_time_unit = 60s
```

All four are overridable via `POSTFIX_CONNECTION_RATE_LIMIT`, `POSTFIX_CONNECTION_COUNT_LIMIT`, `POSTFIX_MESSAGE_RATE_LIMIT`, `POSTFIX_RECIPIENT_RATE_LIMIT`, and `POSTFIX_RATE_TIME_UNIT`.

> [!NOTE]
> These are per-client limits at the SMTP level. Mailyte also enforces per-tenant and per-SMTP-credential rate limits through the `rate_limiter` service (consulted by the `policy-rate-limit` policy daemon at DATA time) — see [Multi-Tenant Configuration](multi-tenant.md).

## Restrictions

The full chains as shipped (first matching rule wins):

```ini
smtpd_client_restrictions =
    permit_mynetworks, permit_sasl_authenticated, reject_unauth_pipelining,
    reject_rbl_client zen.spamhaus.org, warn_if_reject reject_unknown_client_hostname

smtpd_recipient_restrictions =
    permit_mynetworks, permit_sasl_authenticated, reject_non_fqdn_recipient,
    reject_unknown_recipient_domain, reject_unauth_destination,
    reject_unlisted_recipient, reject_rbl_client zen.spamhaus.org,
    check_policy_service unix:private/policy-ip-access, permit

smtpd_sender_restrictions =
    permit_mynetworks, permit_sasl_authenticated,
    reject_non_fqdn_sender, reject_unknown_sender_domain

smtpd_relay_restrictions =
    permit_mynetworks, permit_sasl_authenticated, reject_unauth_destination

smtpd_data_restrictions =
    reject_unauth_pipelining, reject_multi_recipient_bounce,
    check_policy_service unix:private/policy-rate-limit
```

Only **one** RBL (`zen.spamhaus.org`) is consulted at smtpd time — several others were removed after they blocked legitimate senders. `permit_mynetworks` in `smtpd_relay_restrictions` is required: the webmail container submits unauthenticated over the Docker network.

## Transport Cutover Map

```ini
transport_maps = texthash:/etc/postfix/custom/transport_cutover
```

`config/mailer/postfix/transport_cutover` (bind-mounted to `/etc/postfix/custom/`) lists domains that are provisioned on this server but whose MX still points elsewhere; `smtp:` with no nexthop routes their mail by public MX instead of delivering locally. Because it's a `texthash:` map, edits need only `postfix reload` — no `postmap`.

> [!WARNING]
> Remove a domain's line the moment its MX cuts over to this server, or its inbound mail will loop-bounce (`5.4.6 ... loops back to myself`). A stale entry here silently destroyed 167 inbound messages across 13 domains before it was caught on 2026-08-27.

## Environment Variables Read by the Entrypoint

`HOSTNAME`, `POSTFIX_MYNETWORKS`, `POSTFIX_DEV_MODE`, `POSTFIX_TLS_SECURITY_LEVEL`, `POSTFIX_SMTP_TLS_SECURITY_LEVEL`, `POSTFIX_TLS_AUTH_ONLY`, `POSTFIX_TLS_PROTOCOLS`, `POSTFIX_TLS_CIPHERS`, `POSTFIX_TLS_EXCLUDE_CIPHERS`, `POSTFIX_TLS_LOG_LEVEL`, `POSTFIX_TLS_CACHE_TIMEOUT`, the rate-limit overrides listed above, the timeout/error-limit overrides (`POSTFIX_SMTP_TIMEOUT`, `POSTFIX_HELO_TIMEOUT`, `POSTFIX_MAIL_TIMEOUT`, `POSTFIX_RCPT_TIMEOUT`, `POSTFIX_DATA_TIMEOUT`, `POSTFIX_SOFT_ERROR_LIMIT`, `POSTFIX_HARD_ERROR_LIMIT`, `POSTFIX_JUNK_COMMAND_LIMIT`, `POSTFIX_DNS_TIMEOUT`), plus the set written into `/etc/postfix/runtime.env` for pipe/spawn children (`DB_*`, `REDIS_*`, `TRACKING_*`, `DELIVERY_OPTIMIZER_*`, `DEFAULT_ORGANIZATION_ID`, `DEFAULT_DOMAIN_ID`, `WEBHOOK_SECRET`).

> [!WARNING]
> The `POSTFIX_MYHOSTNAME`, `POSTFIX_MYDOMAIN`, `POSTFIX_VIRTUAL_*`, `POSTFIX_MESSAGE_SIZE_LIMIT`, `POSTFIX_CONTENT_FILTER`, and `POSTFIX_HEADER_CHECKS` entries in `.env.example` are **not read by anything** — setting them has no effect. The entrypoint reads only the names listed above.

## Testing Your Configuration

After making changes, always check for syntax errors:

```bash
docker exec postfix postconf -n    # Show non-default settings
docker exec postfix postfix check  # Validate configuration
```

To reload Postfix without restarting the container:

```bash
docker exec postfix postfix reload
```

Remember that `main.cf` is baked into the image and re-applied by the entrypoint: `postconf -e` edits inside a running container **vanish when the container is recreated**. Persistent changes belong in `mailer/postfix/config/` (rebuild) or in an env var the entrypoint reads.
