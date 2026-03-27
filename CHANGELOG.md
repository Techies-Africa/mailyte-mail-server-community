# Changelog

All notable changes to the Mailyte Email Server Community Edition will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-26

### Added
- Core mail stack: Postfix (SMTP), Dovecot (IMAP/POP3), Rspamd (anti-spam)
- REST API for managing organizations, domains, mailboxes, aliases, and filters
- Email open/click tracking with pixel injection and URL rewriting
- Webhook notifications for 50+ mail lifecycle events with HMAC signatures
- Rate limiting at organization, domain, and mailbox levels
- SSL/TLS certificate management with Let's Encrypt auto-provisioning
- Sieve mail filter support with templates (vacation, folder routing)
- SPF, DKIM, and DMARC authentication
- Email client auto-configuration (Thunderbird, Outlook, Apple Mail)
- Roundcube webmail interface
- Interactive management console (`./start.sh`)
- Docker Compose deployment with dev/prod configurations
- Database migrations with Alembic
- Backup and restore scripts
- DKIM key generation tool
- Comprehensive integration test suite
