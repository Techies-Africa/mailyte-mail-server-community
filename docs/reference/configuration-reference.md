---
title: Configuration Reference
description: Complete reference for every configuration file across all Mailyte services — Postfix, Dovecot, Rspamd, API, and workers.
---

# Configuration Reference

All configuration lives in the `config/` directory, organized by service. Custom overrides go into subdirectories that are mounted into the Docker containers.

## Directory Structure

```
config/
  mailer/
    postfix/          # Postfix overrides
    dovecot/          # Dovecot overrides
    rspamd/           # Rspamd overrides
```

## Postfix Configuration

Config path: `config/mailer/postfix/` (mounted to `/etc/postfix/custom/`)

### main.cf — Core Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `myhostname` | `$HOSTNAME` | Server FQDN (must match PTR record) |
| `mydomain` | `$DOMAIN` | Primary domain |
| `mynetworks` | `127.0.0.0/8 [::1]/128 172.16.0.0/12` | Trusted networks |
| `message_size_limit` | `52428800` | Max email size (50 MB) |
| `smtpd_tls_cert_file` | From cert_manager | SSL certificate path |
| `smtpd_tls_key_file` | From cert_manager | SSL private key path |
| `smtpd_tls_security_level` | `may` | TLS for inbound (`may`, `encrypt`) |
| `smtp_tls_security_level` | `may` | TLS for outbound (`may`, `encrypt`, `dane`) |
| `smtpd_tls_protocols` | `!SSLv2, !SSLv3, !TLSv1, !TLSv1.1` | Allowed inbound TLS versions |
| `virtual_mailbox_domains` | MySQL lookup | Domains handled by this server |
| `virtual_mailbox_maps` | MySQL lookup | Mailbox -> storage mapping |
| `virtual_alias_maps` | MySQL lookup | Alias -> destination mapping |
| `virtual_transport` | `lmtp:dovecot:24` | Delivery via Dovecot LMTP |
| `milter_default_action` | `accept` | What to do if Rspamd is unreachable |
| `smtpd_milters` | `inet:rspamd:11332` | Connect to Rspamd for filtering |
| `default_destination_concurrency_limit` | `5` | Max parallel deliveries per destination |
| `smtp_destination_concurrency_limit` | `5` | Max parallel SMTP connections per destination |
| `default_process_limit` | `100` | Max Postfix processes |
| `queue_run_delay` | `300s` | How often to retry deferred messages |
| `maximal_backoff_time` | `4000s` | Max retry delay |
| `minimal_backoff_time` | `300s` | Min retry delay |
| `bounce_queue_lifetime` | `5d` | How long to keep bounced messages |
| `maximal_queue_lifetime` | `5d` | How long to keep deferred messages |

### master.cf — Service Definitions

Controls which Postfix daemons run and on which ports:

| Service | Port | Description |
|---------|------|-------------|
| `smtp` | 25 | Inbound SMTP |
| `submission` | 587 | Authenticated submission (STARTTLS) |
| `smtps` | 465 | Authenticated submission (implicit TLS) |
| `pickup` | — | Local mail pickup |
| `cleanup` | — | Header/body checks |
| `qmgr` | — | Queue manager |

## Dovecot Configuration

Config path: `config/mailer/dovecot/` (mounted to `/etc/dovecot/custom/`)

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mail_location` | `maildir:/var/mail/vhosts/%d/%n/Maildir` | Where mail is stored |
| `mail_uid` | `5000` | UID for mail files |
| `mail_gid` | `5000` | GID for mail files |
| `protocols` | `imap pop3 lmtp sieve` | Enabled protocols |
| `ssl` | `required` | TLS requirement |
| `ssl_cert` | From cert_manager | SSL certificate |
| `ssl_key` | From cert_manager | SSL private key |
| `ssl_min_protocol` | `TLSv1.2` | Minimum TLS version |
| `auth_mechanisms` | `plain login` | SASL auth methods |
| `passdb driver` | `sql` | Password database (MySQL) |
| `userdb driver` | `sql` | User database (MySQL) |
| `mail_max_userip_connections` | `20` | Max connections per user/IP |
| `mail_plugins` | `quota` | Enabled plugins |
| `quota_rule` | `*:storage=1G` | Default quota per mailbox |

### Protocol-Specific

| Setting | Value | Description |
|---------|-------|-------------|
| `protocol imap: mail_plugins` | `imap_quota imap_sieve` | IMAP plugins |
| `protocol lmtp: mail_plugins` | `sieve` | LMTP plugins (server-side filtering) |
| `service imap-login: inet_listener imap` | port 143 | Plaintext IMAP (STARTTLS) |
| `service imap-login: inet_listener imaps` | port 993 | Implicit TLS IMAP |
| `service pop3-login: inet_listener pop3s` | port 995 | Implicit TLS POP3 |

## Rspamd Configuration

Config path: `config/mailer/rspamd/` (mounted to `/etc/rspamd/custom/`)

Local overrides go in `local.d/` subdirectory.

### Core Settings (`local.d/options.inc`)

| Setting | Default | Description |
|---------|---------|-------------|
| `dns.nameserver` | System default | DNS servers for lookups |
| `max_memory` | Not set | Memory limit for Rspamd |

### DKIM Signing (`local.d/dkim_signing.conf`)

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable DKIM signing |
| `path` | `/var/lib/rspamd/dkim/$domain.$selector.key` | Key file path |
| `selector` | `default` | DKIM selector |
| `use_domain` | `header` | Use From header domain for signing |
| `allow_username_mismatch` | `true` | Sign even if SMTP auth user differs |

### Actions (`local.d/actions.conf`)

| Action | Default Score | Description |
|--------|--------------|-------------|
| `reject` | `15` | Reject the message |
| `add header` | `6` | Add spam header but deliver |
| `greylist` | `4` | Greylist (defer temporarily) |
| `no action` | `0` | Deliver normally |

### Statistics (`local.d/classifier-bayes.conf`)

| Setting | Default | Description |
|---------|---------|-------------|
| `backend` | `redis` | Storage backend |
| `servers` | `redis:6379` | Redis connection |
| `autolearn` | `true` | Auto-learn from scored messages |

## API Configuration

The API is configured via environment variables. See [Environment Variables](environment-variables.md) for the complete list.

Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | API listen port |
| `DB_HOST` | — | MySQL host |
| `DB_NAME` | `mailserver` | Database name |
| `REDIS_HOST` | `redis` | Redis host |
| `ADMIN_PASSWORD` | — | Admin authentication password |
| `ADMIN_TOKEN_SECRET` | — | JWT signing secret |

## Worker Configuration

Each worker reads its configuration from environment variables. Common variables shared across workers:

| Variable | Description |
|----------|-------------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | MySQL connection |
| `REDIS_HOST`, `REDIS_PORT` | Redis connection |
| `WEBHOOK_SECRET` | Webhook signing secret |
| `WEBHOOK_SERVICE_URL` | Internal webhook service URL |

Worker-specific variables are documented in each worker's module page under [Worker Modules](../worker/index.md).

## SSL / Certificate Manager

| Variable | Default | Description |
|----------|---------|-------------|
| `ACME_EMAIL` | — | Let's Encrypt registration email |
| `ACME_STAGING` | `true` | Use staging CA (set `false` for production) |
| `CERT_RENEWAL_DAYS` | `30` | Renew certificates this many days before expiry |
| `CERT_CHECK_INTERVAL` | `21600` | Seconds between renewal checks (6 hours) |
| `WILDCARD_DOMAIN` | — | Domain for wildcard certificate |
| `DNS_PROVIDER` | — | DNS provider for DNS-01 challenges |
| `DOCKER_RELOAD_ENABLED` | `true` | Reload Postfix/Dovecot after cert changes |
