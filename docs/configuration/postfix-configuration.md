# Postfix Configuration

How Mailyte's SMTP server is set up — virtual domains, TLS, authentication, rate limiting, and the tracking filter.

---

Postfix handles all inbound and outbound email. It talks to MySQL for domain and mailbox lookups, hands off authentication to Dovecot, and optionally pipes outgoing mail through a tracking filter.

## main.cf — The Core Config

The main Postfix config lives at `/etc/postfix/main.cf`. Here are the key sections.

### Server Identity

```ini
myhostname = mail.yourdomain.com
mydomain = yourdomain.com
myorigin = $mydomain
mydestination = localhost
```

`mydestination` is set to `localhost` because all real domains are handled as virtual domains through MySQL. This is intentional — don't add your domain here.

### Virtual Domain Setup

Postfix uses MySQL queries to figure out which domains, mailboxes, and aliases it should handle:

```ini
virtual_mailbox_domains = mysql:/etc/postfix/mysql-virtual-mailbox-domains.cf
virtual_mailbox_maps = mysql:/etc/postfix/mysql-virtual-mailbox-maps.cf
virtual_alias_maps = mysql:/etc/postfix/mysql-virtual-alias-maps.cf
virtual_transport = lmtp:unix:private/dovecot-lmtp
```

Mail for virtual domains gets delivered to Dovecot over LMTP, which handles the final storage.

### TLS Settings

```ini
# Inbound TLS (other servers connecting to us)
smtpd_tls_cert_file = /etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
smtpd_tls_key_file = /etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
smtpd_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtpd_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtpd_tls_mandatory_ciphers = medium

# Outbound TLS (us connecting to other servers)
smtp_tls_security_level = may
smtp_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtp_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtp_tls_loglevel = 1
```

> [!NOTE]
> `smtpd_tls_security_level = may` means TLS is offered but not required for server-to-server mail. This is the standard for email — requiring TLS would break delivery to many servers that don't support it. For user authentication, `smtpd_tls_auth_only = yes` ensures credentials are never sent in the clear.

### SASL Authentication (via Dovecot)

```ini
smtpd_sasl_type = dovecot
smtpd_sasl_path = private/auth
smtpd_sasl_auth_enable = yes
smtpd_sasl_security_options = noanonymous
smtpd_sasl_local_domain = $myhostname
```

Postfix doesn't do authentication itself. It delegates to Dovecot through a Unix socket. This keeps password handling in one place.

### Message Size Limit

```ini
message_size_limit = 52428800
```

Set via the `POSTFIX_MESSAGE_SIZE_LIMIT` environment variable. The default is 50 MB.

### Rspamd Integration

```ini
milter_protocol = 6
milter_default_action = accept
smtpd_milters = inet:localhost:11332
non_smtpd_milters = inet:localhost:11332
```

All incoming and outgoing mail passes through Rspamd for spam checking and DKIM signing.

> [!WARNING]
> The `milter_default_action = accept` line means that if Rspamd goes down, mail is accepted without scanning. Change this to `tempfail` in high-security environments so mail gets deferred instead.

## master.cf — Service Definitions

The `master.cf` file at `/etc/postfix/master.cf` defines which services Postfix runs and on which ports.

```ini
# Standard SMTP (server-to-server)
smtp      inet  n       -       n       -       -       smtpd

# Submission (authenticated users, port 587)
submission inet  n       -       n       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_reject_unlisted_recipient=no
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o milter_macro_daemon_name=ORIGINATING

# SMTPS (implicit TLS, port 465)
smtps     inet  n       -       n       -       -       smtpd
  -o syslog_name=postfix/smtps
  -o smtpd_tls_wrappermode=yes
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_reject_unlisted_recipient=no
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o milter_macro_daemon_name=ORIGINATING

# Tracking filter (for open/click tracking)
mailyte-tracking unix  -       n       n       -       10      pipe
  flags=DRhu user=mailyte argv=/usr/local/bin/mailyte-tracking
  --sender ${sender} --recipient ${recipient}
```

The three listening ports:
- **Port 25** — Server-to-server mail. No authentication required.
- **Port 587** — Submission. Users authenticate here to send mail. TLS is required.
- **Port 465** — SMTPS. Same as 587 but with implicit TLS (the connection starts encrypted).

### Tracking Filter

The `mailyte-tracking` service is a content filter that rewrites outgoing messages to add open/click tracking. It only activates when `TRACKING_ENABLED=true`.

When enabled, Postfix routes outgoing mail through this filter before final delivery. The filter:

1. Rewrites URLs to pass through the tracking domain.
2. Injects a 1x1 tracking pixel for open detection.
3. Passes the modified message back to Postfix for delivery.

## MySQL Lookup Files

These files tell Postfix how to query MySQL for domain and mailbox information. They live in `/etc/postfix/`.

### Virtual Mailbox Domains

`/etc/postfix/mysql-virtual-mailbox-domains.cf`:

```ini
user = mailyte
password = your-db-password
hosts = mysql
dbname = mailserver
query = SELECT 1 FROM virtual_domains WHERE name='%s' AND active=1
```

This query checks if an incoming domain is one we handle. If the query returns a row, Postfix accepts mail for that domain.

### Virtual Mailbox Maps

`/etc/postfix/mysql-virtual-mailbox-maps.cf`:

```ini
user = mailyte
password = your-db-password
hosts = mysql
dbname = mailserver
query = SELECT 1 FROM virtual_users WHERE email='%s' AND active=1
```

Checks if a specific email address (user@domain) has a mailbox. This prevents Postfix from accepting mail for non-existent users.

### Virtual Alias Maps

`/etc/postfix/mysql-virtual-alias-maps.cf`:

```ini
user = mailyte
password = your-db-password
hosts = mysql
dbname = mailserver
query = SELECT destination FROM virtual_aliases WHERE source='%s' AND active=1
```

Handles email forwarding. When mail arrives for an alias, this query returns the real destination address.

> [!TIP]
> These files contain database credentials in plain text. Make sure they're readable only by the `postfix` user:
> ```bash
> chown root:postfix /etc/postfix/mysql-*.cf
> chmod 640 /etc/postfix/mysql-*.cf
> ```

## Rate Limiting

Postfix has built-in rate limiting to prevent abuse.

```ini
# Limit how fast a single client can send
smtpd_client_message_rate_limit = 100
smtpd_client_recipient_rate_limit = 200
smtpd_client_connection_rate_limit = 50

# Anvil stats window
anvil_rate_time_unit = 60s
```

These settings limit each connecting IP to:
- 100 messages per minute
- 200 recipients per minute
- 50 new connections per minute

> [!NOTE]
> These are per-client limits at the SMTP level. Mailyte also enforces per-tenant rate limits through the API layer and Redis. See [Multi-Tenant Configuration](multi-tenant.md) for those settings.

## Recipient Restrictions

```ini
smtpd_recipient_restrictions =
  permit_mynetworks,
  permit_sasl_authenticated,
  reject_unauth_destination,
  reject_invalid_hostname,
  reject_non_fqdn_hostname,
  reject_non_fqdn_sender,
  reject_non_fqdn_recipient,
  reject_unknown_sender_domain,
  reject_unknown_recipient_domain,
  reject_rbl_client zen.spamhaus.org
```

This chain runs top to bottom. The first matching rule wins. Authenticated users are allowed through early. Unauthenticated connections go through a series of checks to reject obvious spam and misconfigured senders.

## Testing Your Configuration

After making changes, always check for syntax errors:

```bash
docker exec mailyte-postfix postconf -n    # Show non-default settings
docker exec mailyte-postfix postfix check  # Validate configuration
```

To reload Postfix without restarting the container:

```bash
docker exec mailyte-postfix postfix reload
```
