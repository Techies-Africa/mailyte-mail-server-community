---
title: Security Checklist
description: Pre-deployment security verification checklist — go through every item before putting Mailyte in production.
---

# Security Checklist

Go through this checklist before deploying to production. Every unchecked item is a potential vulnerability.

## Credentials and Secrets

- [ ] `ADMIN_PASSWORD` is a strong, unique password (32+ characters)
- [ ] `ADMIN_TOKEN_SECRET` is a random string (32+ characters)
- [ ] `DB_PASSWORD` and `DB_ROOT_PASSWORD` are strong and unique
- [ ] `WEBHOOK_SECRET` is set and unique per deployment
- [ ] All passwords are in `.env` file, not in `docker-compose.yml`
- [ ] `.env` file is in `.gitignore`
- [ ] `.env` file has restrictive permissions (`chmod 600`)
- [ ] No secrets committed to version control (check git history)
- [ ] API keys have expiry dates set
- [ ] Default passwords changed for Grafana, Rspamd UI

## Network

- [ ] Firewall is enabled and configured (UFW or iptables)
- [ ] Only necessary ports are exposed (25, 465, 587, 993, 80, 443)
- [ ] MySQL port (3306) is NOT accessible from the internet
- [ ] Redis port (6379) is NOT accessible from the internet
- [ ] Qdrant port (6333) is NOT accessible from the internet
- [ ] Prometheus port (9090) is NOT accessible from the internet
- [ ] Grafana port (3000) is NOT accessible (or behind auth proxy)
- [ ] Worker ports (8080-8090) are NOT accessible from the internet
- [ ] Rspamd UI port (11334) is NOT accessible from the internet
- [ ] SSH access is restricted (key-based only, no root login)
- [ ] Docker internal ports use `127.0.0.1` binding or no binding

## TLS / SSL

- [ ] `ACME_STAGING` is set to `false` for production
- [ ] SSL certificate is valid and not self-signed
- [ ] TLS 1.0 and 1.1 are disabled
- [ ] SSLv2 and SSLv3 are disabled
- [ ] Certificate auto-renewal is working
- [ ] Port 80 is reachable for ACME challenges
- [ ] SNI is configured for multi-domain setups
- [ ] CAA DNS records restrict certificate issuance

## Email Authentication

- [ ] SPF record is published with `-all` (hard fail)
- [ ] DKIM keys are generated and DNS records published
- [ ] DMARC record is published (start with `p=none`, move to `reject`)
- [ ] PTR (reverse DNS) record matches `HOSTNAME`
- [ ] Forward DNS matches PTR (FCrDNS)
- [ ] MTA-STS is configured
- [ ] TLSRPT is configured

## Postfix

- [ ] `mynetworks` only includes trusted IPs and Docker network
- [ ] Open relay test passes (use [MXToolbox](https://mxtoolbox.com/))
- [ ] SASL authentication is required for submission (port 587/465)
- [ ] Sender restrictions prevent spoofing (`reject_sender_login_mismatch`)
- [ ] Message size limit is set appropriately
- [ ] Bounce queue lifetime is reasonable (5 days default)

## Dovecot

- [ ] `ssl = required` is set
- [ ] Plaintext authentication is disabled without TLS
- [ ] `auth_verbose_passwords = no` (don't log passwords)
- [ ] Max user connections limit is set
- [ ] Login process isolation is enabled

## Rspamd

- [ ] Rspamd web UI is not publicly accessible
- [ ] DNSBL checks are enabled
- [ ] Bayesian filter is trained
- [ ] DKIM signing is working
- [ ] Spam thresholds are appropriate for your use case

## API

- [ ] API is behind a reverse proxy with HTTPS
- [ ] Rate limiting is enabled
- [ ] API keys are required for all endpoints
- [ ] Admin password is not the default
- [ ] CORS is configured appropriately (or disabled)
- [ ] /metrics endpoint is not publicly accessible
- [ ] Input validation is in place for all endpoints
- [ ] SQL injection prevention (parameterized queries)

## Data Protection

- [ ] Data retention policies are defined
- [ ] Automated cleanup scripts are scheduled
- [ ] Right to erasure process is implemented
- [ ] Backups are encrypted (or stored on encrypted volumes)
- [ ] Backup access is restricted
- [ ] Audit logging is enabled
- [ ] Log rotation is configured

## Intrusion Detection

- [ ] Fail2ban is installed and running
- [ ] SMTP auth jail is configured
- [ ] IMAP auth jail is configured
- [ ] API auth jail is configured
- [ ] Relay abuse jail is configured
- [ ] Recidive (repeat offender) jail is configured
- [ ] Your own IPs are whitelisted
- [ ] Fail2ban is monitored (alerting on high ban rates)

## Monitoring

- [ ] Prometheus is scraping all services
- [ ] Grafana dashboards are set up
- [ ] Alerts are configured for critical events
- [ ] Alert notifications are tested (Slack, email, PagerDuty)
- [ ] Disk space alerts are active
- [ ] SSL certificate expiry alerts are active
- [ ] External uptime monitoring is in place

## Docker

- [ ] Docker images are from trusted sources
- [ ] Base images are up to date
- [ ] Container image vulnerability scan is clean (no critical CVEs)
- [ ] Containers run as non-root where possible
- [ ] Docker socket is not mounted unnecessarily
- [ ] Memory limits are set for all containers
- [ ] `restart: unless-stopped` is set for all services
- [ ] Volume permissions are correct (especially mail storage)

## Backup and Recovery

- [ ] Backup script is running and tested
- [ ] Backups include database, mail data, DKIM keys, and SSL certs
- [ ] Backup restore has been tested (at least once!)
- [ ] Offsite backup is configured (S3, etc.)
- [ ] Backup monitoring alerts on stale backups

## System

- [ ] OS is up to date with security patches
- [ ] Automatic security updates are enabled
- [ ] SSH is secured (key-only, no root, non-standard port optional)
- [ ] System logging is working (syslog, journald)
- [ ] Time synchronization is configured (NTP)

---

!!! tip "Print this out"
    Seriously. Print this checklist and go through it item by item before your first production deployment. Then revisit it quarterly.
