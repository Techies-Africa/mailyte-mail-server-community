# Dovecot IMAP/POP3 Server Module

High-performance IMAP and POP3 server with MySQL authentication, quota management, and enterprise security features.

## Overview

This module provides a complete Dovecot configuration for secure email access with:

- **MySQL Authentication**: Database-driven user management
- **Quota Management**: Per-user storage limits with webhook notifications
- **Security**: TLS/SSL encryption and authentication policies
- **Performance**: Optimized for high-concurrency access
- **Monitoring**: Real-time webhook notifications for system events

## Key Features

### Authentication & Authorization
- **MySQL Backend**: Centralized user authentication
- **Virtual Mailboxes**: Support for unlimited domains and users
- **Password Security**: Secure password hashing and validation
- **Access Control**: Fine-grained permission management

### Storage & Quota Management
- **Flexible Quotas**: Per-user storage and message limits
- **Real-time Monitoring**: Usage tracking and threshold alerts
- **Webhook Integration**: Automated notifications for quota events
- **Storage Optimization**: Efficient mailbox storage formats

## Configuration Files

### Core Configuration
- `config/dovecot.conf` - Main Dovecot configuration
- `config/dovecot-sql.conf.ext` - MySQL authentication settings

## Service Architecture

### IMAP Service (Port 143/993)
- **IMAP4rev1**: Full IMAP protocol support
- **IDLE Support**: Real-time email notifications
- **TLS Encryption**: Secure connections (STARTTLS and implicit SSL)

### POP3 Service (Port 110/995)
- **POP3**: Traditional email retrieval
- **UIDL Support**: Unique message identification
- **TLS Encryption**: Secure connections

### Authentication Flow
```
Client → Dovecot → MySQL → User Validation → Mailbox Access
```

## Scripts & Tools

### Core Scripts
- `dovecot-auth-policy.py` - Authentication policy enforcement
- `dovecot-quota-warning.sh` - Quota threshold notifications
- `dovecot-webhook-notify.sh` - Real-time event webhooks
- `webhook_notify.sh` - General webhook notification utility

## Quota System

### Quota Types
- **Storage Quota**: Maximum mailbox size in bytes
- **Message Quota**: Maximum number of messages

### Notification Thresholds
- **Warning**: 80% quota usage
- **Critical**: 95% quota usage
- **Exceeded**: 100% quota usage

### Webhook Events
```json
{
  "event": "quota_warning",
  "user": "user@domain.com",
  "usage_percent": 85,
  "quota_bytes": 1073741824,
  "used_bytes": 912680550
}
```

## Security Features

### Connection Security
- **TLS 1.2+**: Modern encryption standards
- **Certificate Management**: Automatic SSL/TLS setup
- **Perfect Forward Secrecy**: Enhanced connection security

### Authentication Security
- **Secure Password Storage**: Industry-standard hashing
- **Failed Login Protection**: Automatic account protection
- **Rate Limiting**: Brute force attack prevention

## Performance Tuning

### High-Concurrency Configuration
```
# Optimized for 1000+ concurrent connections
default_process_limit = 1000
default_client_limit = 1000
service imap-login {
  process_min_avail = 10
  process_limit = 500
}
```

## Troubleshooting

### Common Issues
1. **Authentication Failures**: Check MySQL connectivity and user credentials
2. **Quota Problems**: Verify quota calculations and webhook delivery
3. **Connection Issues**: Validate TLS configuration and certificates

### Diagnostic Commands
```bash
# Check Dovecot status
doveadm service status

# Test user authentication
doveadm auth test user@domain.com password

# Check quota usage
doveadm quota get -u user@domain.com