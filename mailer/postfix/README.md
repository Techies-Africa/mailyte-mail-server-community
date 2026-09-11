# Postfix SMTP Server Module

Production-grade SMTP server configuration with advanced tracking, rate limiting, and security features.

## Overview

This module provides a complete Postfix configuration for handling inbound and outbound email with:

- **Email Tracking Integration**: Automatic injection of tracking pixels and link rewriting
- **Rate Limiting**: Multi-level abuse prevention and traffic shaping
- **Security**: Advanced anti-spam integration with Rspamd
- **Multi-Domain Support**: Unlimited domain hosting with virtual mailboxes
- **Content Filtering**: Two-stage processing pipeline with tracking injection

## Key Features

### Mail Processing Pipeline
- **Inbound Mail**: External MTA → postscreen/smtpd (:25) → Rspamd milter → LMTP → Dovecot storage
- **Outbound Mail**: Client (587/465 with SASL) or webmail/API (internal :10587, `permit_mynetworks`) → `tracking-filter` content filter → re-injection on 127.0.0.1:10026 → Rspamd DKIM signing → remote MTA
- **Rate limiting**: consulted once per message at DATA phase via the `policy-rate-limit` spawn service, which delegates to the `rate_limiter` worker over HTTP
- **Logging**: `/var/log/postfix/mail.log` (bind-mounted to `./logs/mailer/postfix/`) is tailed by the `log_ingestor` container -- the sole producer of `mail_logs` rows and delivery webhooks

### Security & Anti-Abuse
- **Rate Limiting**: Per-domain, per-user, and global limits
- **Authentication**: SASL authentication for submission
- **TLS Encryption**: Enforced encryption for all authenticated connections
- **Fail2ban Integration**: Automatic blocking of malicious IPs (host-side install -- see `mailer/intrusion_detection/`)

## Configuration Files

### Core Configuration
- `config/main.cf` - Primary Postfix configuration
- `config/master.cf` - Service definitions and process management
- `config/mysql-*.cf` - Database lookup configurations

### Content Filtering
- `config/header_checks` - Header-based content filtering
- `config/body_checks` - Body content filtering
- `config/mime_header_checks` - MIME header filtering

## Service Architecture

### SMTP Services (Port 25)
- **Primary SMTP**: Receives mail from external servers
- **No Authentication**: Relies on IP-based trust and anti-spam
- **Content Filtering**: Rspamd integration for spam protection

### Submission Services (Port 587/465)
- **Authenticated Submission**: Requires SASL authentication (Dovecot at `inet:dovecot:24100`; mailbox passwords or SMTP API keys)
- **Sender ownership**: `reject_sender_login_mismatch` runs on these two ports only -- never in the global sender restrictions, where it broke all inbound mail on 2026-08-22 (see main.cf's comments)
- **TLS Enforcement**: Mandatory encryption for all connections
- **Tracking Integration**: `content_filter=tracking-filter:` on both

### Internal Submission (Port 10587, compose network only)
- Trusted in-stack senders (webmail/API) that cannot SASL-authenticate; `permit_mynetworks` gated, carries the tracking filter, never published to the host

### Content Filtering Pipeline
```
Email → Tracking Filter → Processing → Final Delivery
```

1. **Tracking Filter**: Injects pixels and rewrites links
2. **Processing Service**: Handles metadata and indexing
3. **Final Delivery**: Delivers processed email

## Scripts & Tools

### Core Scripts
- `tracking_injector.py` - Content filter behind `tracking-filter`: tracking injection, delivery-optimizer checks, outbound archiving. **Must never write to stderr at volume** -- Postfix captures stderr, and a chatty stderr once made CPython exit 120 on shutdown, which Postfix turned into a bounce after delivery (fixed 2026-08-22; the logger and an atexit hook now enforce this). It reads its config from `/etc/postfix/runtime.env` (written by `entrypoint.sh`), because `pipe(8)` passes a hardcoded minimal environment
- `rate_limit_policy.py` - Postfix policy protocol → HTTP bridge to the `rate_limiter` worker; `DEFER_IF_PERMIT 4.7.1` on over-quota, fail-open `DUNNO` on outage
- `ip_access_policy.py` - Per-organization IP allowlist policy service
- `bounce_handler.py` - Bounce message processing (posts to the tracking worker)
- `webhook_sender.py` - **Dead config**: defined in `master.cf` as `webhook-filter` but referenced by no `content_filter`; delivery webhooks come from `mailer/log_ingestor/` instead

### Management Tools
- `postfix_tracking_setup.sh` - Initial tracking setup

## Performance Tuning

### High-Volume Configuration
```
# Optimized for 10,000+ emails/hour
default_process_limit = 100
smtpd_client_connection_count_limit = 50
smtpd_client_connection_rate_limit = 30
```

## Monitoring & Logging

### Health Checks
- Service status monitoring
- Queue size tracking
- Delivery rate analysis

### Integration Points
- **Dovecot**: IMAP/POP3 services for mail access
- **Tracking Service**: Real-time email tracking and analytics
- **Webhook System**: Event-driven notifications
- **RAG System**: Email content indexing and search
- **Rate Limiter**: Abuse prevention and traffic shaping

## Troubleshooting

### Common Issues
1. **Queue Backlogs**: Check rate limits and destination availability
2. **Authentication Failures**: Verify database connectivity and credentials
3. **Tracking Problems**: Ensure tracking service is running and accessible

### Diagnostic Commands
```bash
# Check queue status
postqueue -p

# Test configuration
postfix check

# Monitor real-time logs (inside the container; ./logs/mailer/postfix/ on the host)
tail -f /var/log/postfix/mail.log
```

## Dependencies

- **MySQL 8.0+**: User authentication and configuration storage
- **Python 3.8+**: Tracking and webhook scripts
- **Rspamd**: Anti-spam and content filtering
- **Fail2ban**: Intrusion detection and prevention

This module forms the core of the Mailyte mail server, handling all SMTP operations with production-grade features for security, performance, and monitoring.