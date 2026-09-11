# Dovecot Configuration

IMAP and POP3 settings — authentication, quotas, virtual users, namespaces, and SSL.

---

Dovecot is the part users actually connect to when they read email. It handles IMAP (folders, search, sync) and POP3 (download and delete), receives mail from Postfix over LMTP, serves ManageSieve, and provides the authentication backend that Postfix relies on for SMTP login — for both mailbox passwords and SMTP API credentials.

The whole configuration lives in a single baked file, `mailer/dovecot/config/dovecot.conf` (copied to `/etc/dovecot/dovecot.conf` at image build), plus the SQL connection files and an optional host-mounted `/etc/dovecot/local.conf` pulled in by a trailing `!include_try`.

## Authentication

Dovecot authenticates users against MySQL. At container start, `entrypoint.sh` substitutes `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` into the SQL config files with `sed`.

### Auth Mechanisms

```ini
disable_plaintext_auth = yes
auth_mechanisms = plain login
auth_username_format = %Lu
auth_cache_size = 10M
auth_cache_ttl = 1 hour
auth_cache_negative_ttl = 1 min
auth_failure_delay = 2 secs
auth_master_user_separator = *
```

Only `plain` and `login` mechanisms are enabled. This is fine because `disable_plaintext_auth = yes` means they only work over TLS. Credentials are always encrypted in transit.

> [!WARNING]
> `auth_cache_ttl = 1 hour` means a deleted, suspended, or password-changed account (or a revoked SMTP credential) can keep authenticating from cache for up to an hour. The API flushes the cache through Dovecot's doveadm HTTP API (`service doveadm { inet_listener http { port = 24180 } }`, key set via `DOVEADM_API_KEY`) whenever it revokes or rotates an SMTP credential. If you change credentials by hand, flush the cache yourself or the change is not enforced.

### passdb Order

Three password databases are consulted, in this order:

1. **SMTP credentials** — `protocol smtp { passdb { driver = sql; args = /etc/dovecot/dovecot-sql-smtp.conf.ext } }`. Scoped to SMTP AUTH only; IMAP/POP3 logins never query the `smtp_credentials` table.
2. **Master users** — `passdb { driver = passwd-file; master = yes; args = /etc/dovecot/master-users }`. Login form is `<mailbox>*jmap_master`; the shared secret is mirrored in the `IMAP_MASTER_PASSWORD` env var (used by the JMAP service and storage calculator).
3. **Mailboxes** — `passdb { driver = sql; args = /etc/dovecot/dovecot-sql.conf.ext }` against `email_accounts`.

### SQL Auth Configuration

`/etc/dovecot/dovecot-sql.conf.ext`:

```ini
driver = mysql
connect = host=${DB_HOST} port=${DB_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASSWORD}

default_pass_scheme = BLF-CRYPT

password_query = SELECT email as user, password FROM email_accounts \
  WHERE email='%u' AND status = 'active'

user_query = SELECT CONCAT('/var/mail/vhosts/', d.domain, '/', ea.local_part) as home, \
  CONCAT('maildir:/var/mail/vhosts/', d.domain, '/', ea.local_part) as mail, \
  5000 AS uid, 5000 AS gid, \
  CASE WHEN ea.storage_quota > 0 THEN CONCAT('*:storage=', ea.storage_quota, 'B') ELSE NULL END as quota_rule \
  FROM email_accounts ea JOIN domains d ON ea.domain_id = d.id \
  WHERE ea.email='%u' AND ea.status = 'active'
```

A few things to note:

- **`BLF-CRYPT`** — Passwords are stored as bcrypt hashes.
- **`status = 'active'`** — Suspended/inactive mailboxes can't log in (subject to the auth cache above).
- **`quota_rule`** — Per-user quota comes from `email_accounts.storage_quota` in **bytes** (`B` suffix); `0` means no per-user rule, so the global default applies.

`/etc/dovecot/dovecot-sql-smtp.conf.ext` (SMTP API credentials):

```ini
default_pass_scheme = BLF-CRYPT
password_query = SELECT username AS user, password, \
  IF(ip_allowlist_enabled = 1 AND JSON_LENGTH(allowed_ips) > 0, \
     REPLACE(REPLACE(REPLACE(REPLACE(CAST(allowed_ips AS CHAR), '[', ''), ']', ''), '"', ''), ' ', ''), \
     NULL) AS allow_nets \
  FROM smtp_credentials \
  WHERE username = '%u' AND active = 1 \
    AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
```

`active`, `expires_at`, and the per-key IP allowlist are all enforced inside the query — a revoked or expired key simply returns no row, and `allow_nets` makes Dovecot check the client IP on every AUTH (including cached ones).

### Auth Service for Postfix

Postfix reaches Dovecot's SASL service over TCP, not a Unix socket (they run in separate containers):

```ini
service auth {
  inet_listener {
    port = 24100
  }
}
```

Port 24100 is internal to the Docker network — never published to the host.

### Auth Policy (Brute-Force Protection)

A Redis-backed policy server (`dovecot-auth-policy.py`, run by supervisord inside the container) is consulted before and after every auth attempt:

```ini
auth_policy_server_url = http://127.0.0.1:8090/
auth_policy_check_before_auth = yes
auth_policy_check_after_auth = yes
auth_policy_reject_on_fail = no
```

Its knobs: `MAX_AUTH_FAILURES_PER_IP` (default 10), `MAX_AUTH_FAILURES_PER_USER` (5), `AUTH_FAILURE_WINDOW_SECS` (900), `AUTH_BLOCK_DURATION_SECS` (3600).

## Virtual User Mapping

All mailboxes are stored under a single system user (`vmail`, uid/gid 5000):

```ini
mail_location = maildir:/var/mail/vhosts/%d/%n
mail_uid = vmail
mail_gid = vmail
first_valid_uid = 5000
last_valid_uid = 5000
mail_fsync = optimized
mail_plugins = quota notify
```

So `john@yourdomain.com` is stored at `/var/mail/vhosts/yourdomain.com/john/`.

## Namespace Setup

The `inbox` namespace auto-creates the standard special-use folders plus Mailyte's smart folders:

| Folder | `special_use` | auto |
|--------|---------------|------|
| Drafts | `\Drafts` | subscribe |
| Junk | `\Junk` | subscribe |
| Sent | `\Sent` | subscribe |
| Trash | `\Trash` | subscribe |
| Archive | `\Archive` | subscribe |
| Notifications, Social, Promotions, Updates | — | subscribe (smart-folder targets for the Rspamd classifier) |
| "Sent Messages" | `\Sent` | no (alias for clients that use that name) |

## Quota Management

```ini
plugin {
  quota = maildir:User quota
  quota_rule = *:storage=5GB
  quota_rule2 = Trash:storage=+500MB
  quota_rule3 = Junk:storage=+100MB
  quota_grace = 10%%

  quota_warning = storage=95%% quota-warning 95 %u
  quota_warning2 = storage=80%% quota-warning 80 %u
  quota_warning3 = storage=75%% quota-warning 75 %u
}

service quota-warning {
  executable = script /usr/local/bin/dovecot-quota-warning.sh
  user = dovecot
  unix_listener quota-warning {
    user = vmail
    group = vmail
    mode = 0660
  }
}
```

How the quota system works:

- **Default quota** is 5 GB. It is overridden per-user by `email_accounts.storage_quota` through the `user_query`.
- **Trash gets +500 MB and Junk +100 MB** on top of the regular quota, so deleting or junking messages doesn't immediately fail at the limit.
- **Grace** — users can go 10% over temporarily.
- **Warnings** fire at 75%, 80%, and 95% usage via the quota-warning script.

> [!NOTE]
> The `%%` is Dovecot's escape for a literal `%` character in config files.

## SSL / TLS

```ini
ssl = required
ssl_cert = </etc/ssl/certs/server.crt
ssl_key = </etc/ssl/private/server.key
ssl_dh = </etc/dovecot/dh.pem
ssl_min_protocol = TLSv1.2
ssl_cipher_list = ECDHE+AESGCM:ECDHE+AES256:ECDHE+AES128:ECDHE+CHACHA20:!aNULL:!MD5:!DSS:!RC4
ssl_prefer_server_ciphers = yes

!include_try /etc/dovecot/conf.d/sni.conf
```

The `<` before the file path is Dovecot syntax for "read the contents of this file." The `server.crt`/`server.key` pair and the per-domain `sni.conf` (a series of `local_name <host> { ssl_cert = ... }` blocks) are produced by cert_manager — see [SSL Certificates](ssl-certificates.md).

`ssl = required` means all connections must use TLS; `login_trusted_networks` covers the internal Docker/RFC1918 ranges so internal services can connect.

### Protocol Ports

| Port | Protocol | Published to host |
|------|----------|-------------------|
| 143 | IMAP (STARTTLS) | yes |
| 993 | IMAPS (implicit TLS) | yes |
| 110 | POP3 (STARTTLS) | yes |
| 995 | POP3S (implicit TLS) | yes |
| 4190 | ManageSieve | yes |
| 24 | LMTP (from Postfix) | no — internal only |
| 24100 | SASL auth (for Postfix) | no — internal only |
| 24180 | doveadm HTTP API | no — internal only |

## LMTP Delivery

Dovecot receives mail from Postfix over LMTP on TCP port 24:

```ini
service lmtp {
  inet_listener lmtp {
    port = 24
    address = *
  }
}
```

LMTP supports per-recipient delivery status, so Postfix knows exactly which recipients succeeded or failed.

## Sieve

```ini
protocols = imap pop3 lmtp sieve
submission_host = postfix:25

plugin {
  sieve = file:~/sieve;active=~/.dovecot.sieve
  sieve_global_path = /etc/dovecot/sieve/default.sieve
  sieve_extensions = +notify +imapflags +vacation-seconds +editheader +vnd.dovecot.pipe
  sieve_plugins = sieve_extprograms
  sieve_pipe_bin_dir = /usr/lib/dovecot/sieve-pipe
}
```

The global `default.sieve` runs on every delivery: it first pipes a copy of the message to the `archive-message` program (which POSTs it to the archiver service — env `ARCHIVE_SERVICE_URL`, default `http://archiver:8083`), then files `X-Spam: Yes` into Junk, then routes on Rspamd's `X-Email-Category` header into Social / Promotions / Updates. Per-user scripts are managed over ManageSieve (port 4190) by the API's filter routes.

## mail_crypt (Encryption at Rest)

Encryption at rest is **not** in the baked `dovecot.conf`. It lives in the host-mounted override `config/mailer/dovecot/local.conf`, which production compose mounts at `/etc/dovecot/local.conf` together with the key pair at `/etc/dovecot/mail_crypt/`:

```ini
mail_plugins = $mail_plugins mail_crypt zlib

plugin {
  mail_crypt_global_private_key = </etc/dovecot/mail_crypt/ecprivkey.pem
  mail_crypt_global_public_key = </etc/dovecot/mail_crypt/ecpubkey.pem
  mail_crypt_save_version = 2
  zlib_save = lz4
}
```

The keys are **global** (one pair decrypts every mailbox) and were imported from the mailcow source system during migration; message bodies are additionally LZ4-compressed. Both layers are required to read migrated mail.

> [!WARNING]
> This key pair exists in exactly the places it's escrowed to (`scripts/escrow-secrets.sh`). Losing it makes every encrypted mailbox unreadable — no password reset can recover the mail.

## Logging

```ini
log_path = /dev/stderr
info_log_path = /dev/stderr
```

Dovecot logs to stderr — read it with `docker logs dovecot`. There are no log files inside the container.

## Reloading Configuration

After editing Dovecot config files:

```bash
docker exec dovecot doveconf -n     # Show non-default settings
docker exec dovecot doveadm reload  # Apply changes without restart
```

Remember that `dovecot.conf` is baked into the image — persistent changes go in `mailer/dovecot/config/` (rebuild) or in the host-mounted `config/mailer/dovecot/local.conf` (reload).
