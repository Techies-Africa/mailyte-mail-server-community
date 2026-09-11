---
title: Configuration Reference
description: Where every configuration file actually lives — baked image configs, host-mounted overrides, and the generated files that tie them together.
---

# Configuration Reference

Mailyte's mail-stack configuration is **baked into the service images** from the repo, with a small set of host-mounted override files under `config/` and generated files under `storage/`. Environment variables adjust behavior at container start.

## Where Config Lives

| Layer | Path (repo) | Path (container) | Applied |
|-------|-------------|------------------|---------|
| Postfix baked config | `mailer/postfix/config/` | `/etc/postfix/` | Image build; entrypoint applies env overrides via `postconf -e` and `envsubst` |
| Postfix host override | `config/mailer/postfix/transport_cutover` | `/etc/postfix/custom/transport_cutover` | Live (texthash map; `postfix reload`) |
| Dovecot baked config | `mailer/dovecot/config/dovecot.conf` (+ SQL `.ext` files) | `/etc/dovecot/` | Image build; entrypoint substitutes DB credentials and `DOVEADM_API_KEY` |
| Dovecot host override | `config/mailer/dovecot/local.conf` | `/etc/dovecot/local.conf` | `!include_try` at the end of dovecot.conf (prod mount; carries mail_crypt) |
| Rspamd baked config | `mailer/rspamd/config/` (`local.d/`, Lua) | `/etc/rspamd/` | Image build |
| DKIM keys | `storage/dkim_keys/` | `/var/lib/rspamd/dkim/` | Bind mount; written by `scripts/generate_dkim.py` and the API |
| SSL certs (deployed) | `storage/ssl_certs/`, `storage/ssl_private/` | `/etc/ssl/certs[/custom]`, `/etc/ssl/private[/custom]` | Written by cert_manager |
| SNI maps | `storage/sni_config/` | `/etc/ssl/sni` | Written by cert_manager; consumed by Postfix, Dovecot, Traefik |
| Monitoring | `monitoring/prometheus/`, `monitoring/alertmanager/`, `monitoring/grafana/` | `/etc/prometheus/`, `/etc/alertmanager/`, Grafana provisioning | Read-only mounts |
| Traefik (prod) | `deployment/traefik/traefik.yml` | `/etc/traefik/traefik.yml` | Read-only mount; dynamic certs via `/etc/traefik/dynamic/traefik_certs.yml` |
| Roundcube override | `config/mailer/roundcube/custom.config.inc.php` | Roundcube config dir | Mount |

!!! warning "Baked means baked"
    Editing `/etc/postfix/main.cf` or `/etc/rspamd/local.d/*` inside a running container does not survive a container recreate, and `postconf -e` changes are re-applied from env at every start. Persistent changes belong in the repo (`mailer/*/config/`, then rebuild) or in an env var the entrypoint reads.

## Postfix — Key Effective Settings

Full detail: [Postfix Configuration](../configuration/postfix-configuration.md).

| Setting | Value | Notes |
|---------|-------|-------|
| `myhostname` | `${HOSTNAME}` | set by entrypoint |
| `mydestination` | `localhost` | all real domains are virtual |
| `virtual_mailbox_domains` / `_maps` / `virtual_alias_maps` / `smtpd_sender_login_maps` | `proxy:mysql:/etc/postfix/mysql-*.cf` | queries `domains` / `email_accounts` / `aliases` / (+ `smtp_credentials`) |
| `virtual_transport` | `lmtp:inet:dovecot:24` | |
| `smtpd_sasl_type` / `smtpd_sasl_path` | `dovecot` / `inet:dovecot:24100` | |
| `smtpd_tls_cert_file` / `key_file` | `/etc/ssl/certs/server.crt` / `/etc/ssl/private/server.key` | from cert_manager |
| `tls_server_sni_maps` | `hash:/etc/postfix/sni_certs.map` | from cert_manager |
| `smtpd_tls_security_level` / `smtp_tls_security_level` | `may` / `may` | 587/465 override to `encrypt` |
| `smtpd_milters` / `non_smtpd_milters` | `inet:rspamd:11332` | `milter_default_action = accept` |
| `message_size_limit` | `52428800` (50 MB) | baked, not env-driven |
| `maillog_file` | `/var/log/postfix/mail.log` | tailed by log_ingestor |
| `transport_maps` | `texthash:/etc/postfix/custom/transport_cutover` | migration cutover routing |
| Port 25 | postscreen (DNSBL scoring, greet test) | |
| Ports 587/465 | SASL required, `reject_sender_login_mismatch` first, tracking content filter | |
| Port 10026 (localhost) | tracking-filter re-injection listener | |
| Port 10587 (internal) | unauthenticated submission for trusted internal services | not published |

## Dovecot — Key Effective Settings

Full detail: [Dovecot Configuration](../configuration/dovecot-configuration.md).

| Setting | Value |
|---------|-------|
| `protocols` | `imap pop3 lmtp sieve` |
| `mail_location` | `maildir:/var/mail/vhosts/%d/%n` |
| `mail_uid` / `mail_gid` | `vmail` (5000/5000) |
| `ssl` | `required`; `ssl_min_protocol = TLSv1.2`; SNI via `conf.d/sni.conf` |
| `auth_mechanisms` | `plain login` (TLS-only) |
| passdbs (in order) | SMTP-only `smtp_credentials` SQL → master passwd-file → `email_accounts` SQL |
| `default_pass_scheme` | `BLF-CRYPT` (bcrypt) |
| `auth_cache_ttl` | `1 hour` (flushed via doveadm HTTP API on credential changes) |
| Quota | `*:storage=5GB` default, per-user override from `email_accounts.storage_quota`; Trash +500MB, Junk +100MB, 10% grace; warnings at 75/80/95% |
| LMTP | TCP port 24; SASL for Postfix on 24100; doveadm HTTP on 24180 (all internal-only) |
| Sieve | global `default.sieve` (archive pipe → spam→Junk → category smart folders); per-user via ManageSieve 4190 |
| mail_crypt | in host-mounted `local.conf` (global EC key pair + LZ4) — production only |

## Rspamd — Key Effective Settings

Full detail: [Rspamd Configuration](../configuration/rspamd-configuration.md).

| Setting | Value |
|---------|-------|
| Actions | `reject = 15; rewrite_subject = 10; add_header = 6; greylist = 4` |
| DKIM/ARC | enabled; selector `default`; keys `/var/lib/rspamd/dkim/{domain}.{selector}.key`; `selector_map` generated |
| Bayes | Redis backend, `per_user`, `min_learns = 200` |
| Greylisting | 300 s, skip authenticated/local/DKIM-valid; 34-domain provider whitelist |
| Antivirus | **disabled** (no ClamAV container ships) |
| Per-org | `settings` module from Redis (`rspamd_settings:` prefix) + multimap white/blacklists |
| Custom Lua | `email_classifier.lua` (X-Email-Category), `transport_rules.lua` (env-gated) |
| Workers | proxy 11332 (milter), normal 11333, controller 11334 (no password — keep internal) |

## API and Worker Configuration

The API and all workers are configured **entirely via environment variables** — see [Environment Variables](environment-variables.md). Highlights:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | per-service (API: compose sets `8080`) | Bind port |
| `DB_*`, `REDIS_*` | `mysql` / `redis` | Backing stores |
| `ADMIN_PASSWORD` / `ADMIN_TOKEN_SECRET` | — | API admin auth |
| `DOVEADM_URL` / `DOVEADM_API_KEY` | `http://dovecot:24180` / — | SMTP-credential cache flush |
| `WEBHOOK_SECRET` / `WEBHOOK_URLS` | — | Webhook signing/global endpoint |

## cert_manager

| Variable | Default | Description |
|----------|---------|-------------|
| `ACME_EMAIL` (`ACME_EMAILS` for a pool) | `admin@localhost` | ACME account(s) |
| `ACME_STAGING` | `true` (prod: `false`) | Staging CA |
| `CERT_RENEWAL_DAYS` / `CERT_CHECK_INTERVAL` | `30` / `21600` | Renewal loop |
| `SSL_CERT_PATH` / `SSL_KEY_PATH` | `/etc/ssl/certs` / `/etc/ssl/private` | Deploy directories |
| `SNI_CONFIG_PATH` | `/etc/ssl/sni` | SNI map output |
| `TRAEFIK_ADMIN_SUBDOMAINS` | `api,autoconfig,jmap,caldav,docs,grafana,traefik,console` | Admin hostnames |
| `WILDCARD_DOMAIN` / `DNS_PROVIDER` | — | DNS-01 wildcard issuance |
| `DOCKER_RELOAD_ENABLED` / `DOCKER_PROXY_URL` | `true` (compose) / `http://docker-proxy:2375` | SIGHUP Postfix/Dovecot after deploys |

See [SSL Certificates](../configuration/ssl-certificates.md) for the issuance flow (webroot HTTP-01, SAN selection by live DNS, SNI map generation).

## Monitoring Stack

- `monitoring/prometheus/prometheus.yml` — scrape config (15 s interval, all worker `/metrics` endpoints, mysql/redis exporters).
- `monitoring/prometheus/rules/mail_alerts.yml`, `backup_alerts.yml` — alert rules.
- `monitoring/alertmanager/alertmanager.yml` — routes everything to `http://webhooks:8081/alertmanager`.
- `monitoring/grafana/` — provisioning + the `mail_overview` and `security_dashboard` dashboards.

See Monitoring Configuration (Enterprise Edition) and Prometheus Setup (Enterprise Edition).

!!! note "Legacy configs to ignore"
    `worker/monitoring/prometheus.yml`, `worker/monitoring/alertmanager.yml`, `worker/monitoring/alert_rules.yml`, and everything under `worker/monitoring/config/` are unreferenced legacy files that contradict the active `monitoring/` tree — nothing mounts them.
