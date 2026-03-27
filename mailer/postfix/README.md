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
- **Inbound Mail**: External MTA → Postfix → Processing → Storage
- **Outbound Mail**: User → API → Tracking Injection → Postfix → External MTA
- **Two-Stage Delivery**: Tracking injection with post-processing delivery

### Security & Anti-Abuse
- **Rate Limiting**: Per-domain, per-user, and global limits
- **Authentication**: SASL authentication for submission
- **TLS Encryption**: Enforced encryption for all authenticated connections
- **Fail2ban Integration**: Automatic blocking of malicious IPs

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
- **Authenticated Submission**: Requires SASL authentication
- **TLS Enforcement**: Mandatory encryption for all connections
- **Rate Limited**: Configurable per-user sending limits
- **Tracking Integration**: Automatic tracking injection for outbound mail

### Content Filtering Pipeline
```
Email → Tracking Filter → Processing → Final Delivery
```

1. **Tracking Filter**: Injects pixels and rewrites links
2. **Processing Service**: Handles metadata and indexing
3. **Final Delivery**: Delivers processed email

## Scripts & Tools

### Core Scripts
- `tracking_injector.py` - Email tracking injection service
- `webhook_sender.py` - Real-time webhook notifications
- `rate_limit_policy.py` - Dynamic rate limiting engine
- `bounce_handler.py` - Bounce message processing

### Management Tools
- `postfix_tracking_setup.sh` - Initial tracking setup
- Configuration validation and testing utilities

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

# Monitor real-time logs
tail -f /var/log/mail.log
```

## Dependencies

- **MySQL 8.0+**: User authentication and configuration storage
- **Python 3.8+**: Tracking and webhook scripts
- **Rspamd**: Anti-spam and content filtering
- **Fail2ban**: Intrusion detection and prevention

This module forms the core of the Mailyte mail server, handling all SMTP operations with production-grade features for security, performance, and monitoring.