---
title: Security Checklist
description: Pre-deployment security verification checklist — go through every item before putting Mailyte in production.
---

# Security Checklist

Go through this checklist before deploying to production. Every unchecked item is a potential vulnerability. Items marked *(enforced)* are validated automatically by the stack itself — verify they haven't been disabled.

## Credentials and Secrets

- [ ] `scripts/generate-secrets.sh` and `scripts/generate_dkim_kek.sh` have been run
- [ ] *(enforced)* The eight required secrets (`DB_ROOT_PASSWORD`, `DB_PASSWORD`, `WEBHOOK_SECRET`, `OAUTH_TOKEN_SECRET`, `URL_HMAC_SECRET`, `ADMIN_PASSWORD`, `ADMIN_TOKEN_SECRET`, `GRAFANA_ADMIN_PASSWORD`) are set, non-default, and at least 16 characters — the `secrets-check` service refuses to let the stack start otherwise
- [ ] `.env` file is in `.gitignore` and has restrictive permissions (`chmod 600`)
- [ ] `secrets/encryption_kek` exists, mode 600, and is backed up **separately** from database dumps
- [ ] No secrets committed to version control (check git history)
- [ ] Migration `0019_api_key_hash_only` has run (redacts raw API keys from `api_keys.key_id`; verification is hash-based since 2026-08-30). If the DB may have been exposed before it ran, rotate API keys

## Network

- [ ] Deploying with `docker-compose.prod.yml` layered on top of the base file — the base file alone publishes internal ports on `0.0.0.0`
- [ ] Verified from a **different host** that only mail ports (25, 465, 587, 143, 993, 110, 995), 80, 443, and SSH answer: `nmap <public-ip>`
- [ ] MySQL (3306) and Redis (6379) have no host port at all
- [ ] Prometheus (9090), Grafana (3000), Alertmanager (9093), Qdrant (6333), Rspamd (11332/11334), and all worker ports (8082–8104) bind `127.0.0.1` only — this is the prod compose default since 2026-08-22; don't undo it
- [ ] Host firewall enabled as defense-in-depth (remember Docker bypasses ufw for published ports — the loopback binding is the real control)
- [ ] SSH access is key-based only, no root login

## TLS / SSL

- [ ] `ACME_STAGING` is `false` for production
- [ ] SSL certificate is valid and not self-signed
- [ ] TLS 1.0/1.1 and SSLv2/v3 disabled (shipped default in both Postfix and Dovecot configs)
- [ ] Certificate auto-renewal is working (cert_manager logs; renewal at 30 days before expiry)
- [ ] Port 80 is reachable for ACME challenges
- [ ] CAA DNS records restrict certificate issuance

## Email Authentication

- [ ] SPF record is published with `-all` (hard fail)
- [ ] DKIM keys are generated and DNS records published — **verify with `dig`, per domain**; DNS records that were never published is a failure mode this platform has actually hit
- [ ] DMARC record is published (start with `p=none`, move to `reject`)
- [ ] PTR (reverse DNS) record matches the advertised hostname; forward DNS matches PTR (FCrDNS)
- [ ] MTA-STS / TLSRPT configured (optional but recommended)

## Postfix

- [ ] `mynetworks` is the shipped minimal value (`127.0.0.0/8 [::1]/128`) — never add public IPs or the Docker subnet
- [ ] Open relay test passes (use [MXToolbox](https://mxtoolbox.com/))
- [ ] `smtpd_tls_auth_only = yes` — AUTH never offered without TLS (shipped default)
- [ ] `reject_sender_login_mismatch` present on **587/465 service overrides only, listed before `permit_sasl_authenticated`** — and absent from the global `smtpd_sender_restrictions`. Global enforcement broke all inbound mail on 2026-08-22; the `main.cf` comment block explains why
- [ ] No stale `transport_maps` entries pointing at a decommissioned relay — a leftover cutover transport loop-bounced 13 domains' inbound in production (fixed 2026-08-27)
- [ ] Anvil rate limits reviewed for your traffic profile

## Dovecot

- [ ] `ssl = required` and `disable_plaintext_auth = yes` (shipped defaults)
- [ ] Mailbox passwords are bcrypt (`BLF-CRYPT`) — legacy hashes are rejected by the API
- [ ] Auth-policy server running inside the Dovecot container (`supervisorctl status` shows `dovecot-auth-policy`)
- [ ] `DOVEADM_API_KEY` set — without it, credential revocations degrade to the 1-hour auth-cache window and the API logs degraded flushes
- [ ] Operational habit in place: **flush the auth cache after any mailbox suspension/password change that must take effect immediately** (`doveadm auth cache flush <user>`)

## Rspamd

- [ ] Rspamd web UI (11334) not publicly accessible (loopback-bound in prod)
- [ ] Controller password set
- [ ] DNSBL checks enabled, Bayesian filter training
- [ ] DKIM signing verified with a real send to Gmail
- [ ] ClamAV: decide explicitly — it is **disabled by default and no container ships**; if you need attachment antivirus, deploy a `clamav` service and flip `enabled = true` in `mailer/rspamd/config/local.d/antivirus.conf`

## API

- [ ] API reached only through Traefik on 443 (prod: `api` has no host port)
- [ ] Every route requires a credential; platform routes unreachable for tenant credentials (ADR-002 — shipped behavior, don't bypass with custom routes)
- [ ] Invalid-key rate limiting active (shipped: 20 per 5 min per IP)
- [ ] CORS configured appropriately
- [ ] Parameterized queries only (shipped pattern — keep it in new code)

## Data Protection

- [ ] `mail_crypt` keys present and mail readable through Dovecot but ciphertext on disk
- [ ] Backups encrypted with age before upload; `DR_AGE_RECIPIENT` set — the `UnencryptedBackupsPresent` alert watches for violations
- [ ] Backup restore has been drilled (see the [DR runbook](../operations/disaster-recovery.md); last drills 2026-08-22 and 2026-08-23 both passed)
- [ ] Retention cleanup for operational tables scheduled ([Compliance](compliance.md#operational-table-retention) — not automated by the stack)
- [ ] Log rotation configured (shipped: rotating file handlers + Docker `max-size` in prod)

## Brute-Force Protection

- [ ] Dovecot auth-policy server active (progressive delays + IP/user blocking — shipped default)
- [ ] Thresholds reviewed (`MAX_AUTH_FAILURES_PER_IP`=10, `MAX_AUTH_FAILURES_PER_USER`=5 defaults)
- [ ] `failed_auth_attempts` visible in the console / queried in reviews
- [ ] Fail2ban: understand it is **not deployed**; install on the host yourself only if you want firewall-level bans on top ([Intrusion Detection](intrusion-detection.md#optional-host-level-fail2ban))

## Monitoring

- [ ] Prometheus scraping all deployed targets (the `postfix`/`dovecot` exporter jobs being DOWN is expected — no exporters exist)
- [ ] Backup-absence alerts firing capability verified (`monitoring_backup_age_seconds` series present)
- [ ] Aware that Alertmanager's webhook receiver endpoint (`webhooks:8081/alertmanager`) does not exist yet — firing alerts are visible in the Prometheus/Alertmanager UIs only
- [ ] Monitoring-service webhook notifications pointed at a real receiver (`webhook_urls` table)
- [ ] External uptime monitoring in place

## Docker

- [ ] Images built from this repo; base images current (python:3.11-slim for most workers)
- [ ] Containers run as non-root where the Dockerfile sets a user (the security services do; verify new services follow)
- [ ] Docker socket only exposed through `docker-proxy` (CONTAINERS/POST/ALLOW_RESTARTS only) — never bind-mount it into an app container
- [ ] Memory limits set (prod compose ships them)
- [ ] `restart: always` in prod (shipped)

## System

- [ ] OS up to date with security patches; automatic security updates enabled
- [ ] Time synchronization configured (NTP) — DKIM and TLS both care
- [ ] Deploy-persistence understood: `secrets/` and `storage/` are symlinked across deploys, `config/` is replaced every deploy — a hand-edit in `config/` will vanish

---

!!! tip "Print this out"
    Seriously. Print this checklist and go through it item by item before your first production deployment. Then revisit it quarterly.
