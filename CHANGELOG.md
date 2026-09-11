# Changelog

All notable changes to the Mailyte Mail Server Community Edition will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-11

First public release. The version number was claimed in March but never tagged or
published; this is the first tree anyone can actually clone and run.

### Added

- Core mail stack: Postfix (SMTP), Dovecot (IMAP/POP3), Rspamd (anti-spam)
- REST API for organizations, domains, mailboxes, aliases, filters and SMTP credentials
- Mailbox API (`/api/v1/mailbox/*`, `/api/v1/mailbox-auth/*`) — the surface
  [mailyte-webmail](https://github.com/Techies-Africa/mailyte-webmail) runs on
- One-time bootstrap (`POST /api/v1/bootstrap/`) creating the first organization,
  domain, mailbox and API key, so a fresh install has a way in
- Security module: DLP and geo-blocking policy management (policy editing and
  violation history; the enforcement workers are Enterprise-only)
- Analytics: volume and deliverability reporting
- Email open/click tracking, unsubscribe handling, and `List-Unsubscribe` injection
- Webhooks for mail lifecycle events, HMAC-signed
- Rate limiting at organization, domain and mailbox level
- SSL/TLS via Let's Encrypt, with SNI
- Sieve filters, SPF/DKIM/DMARC, and client auto-configuration
- Webmail: Mailyte Webmail, Roundcube or SOGo — all three ship, Mailyte's starts by default
- Mailyte Console on port 3100, which adapts to this edition automatically by
  reading `/api/v1/capabilities`
- **Before You Install** guide covering outbound port 25, reverse DNS and IP
  reputation — the things that stop a mail server that is otherwise perfectly healthy

### Changed

- This repository is now **generated** from the Mailyte Email Server source rather
  than maintained by hand. Application code is replaced on each release; see
  CONTRIBUTING.md for what is maintained here and where to send changes.
- `docker compose up -d` starts the whole platform, console and webmail included.
- `COMPOSE_PROJECT_NAME` defaults to `mailyte-ce`, so this edition can never share
  volumes with an Enterprise stack on the same host.
- `MAILSERVER_SUBNET`, `INTERNAL_SUBNET` and `CONTAINER_PREFIX` make the network
  ranges and container names configurable, so both editions can run side by side.

### Fixed

Everything below stopped a clean install from working, and was found by running one:

- `DB_USER` defaulted to `root`, which the MySQL image refuses outright, so the
  database never started
- Four unquoted `.env` values containing spaces made `source .env` execute them —
  one of them ran `/usr/bin/login`
- The secrets gate ran on Python 3.9 against code using 3.10+ syntax; because every
  container waits on that gate, nothing started, and the error named a type hint
- Migration `0003` declared foreign keys `INTEGER`/`VARCHAR(100)` against `CHAR(26)`
  parents, so the migration chain could never apply
- The API required two secrets the compose file no longer passes it, and crash-looped
  with them correct in `.env`
- The MySQL healthcheck pinged over the unix socket, which answers during first-run
  initialisation while the network is still closed — so the first
  `docker compose up` always failed and the second always worked
- `generate-secrets.sh` and `generate_dkim_kek.sh` died on the empty directory Docker
  leaves at a bind-mount path when `docker compose up` runs before them
- The migration chain was 15 revisions behind the models it ships with, leaving
  5 tables and 20 columns missing
- The capability manifest advertised `webmail`, `security` and `analytics` with no
  code behind any of them, and `logs`, which nothing has ever consumed

[1.0.0]: https://github.com/Techies-Africa/mailyte-mail-server-community/releases/tag/v1.0.0
