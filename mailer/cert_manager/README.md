# Certificate Manager Module

Automated SSL/TLS certificate management with Let's Encrypt integration, multi-domain support, and automatic renewal for Mailyte mail server operations.

## Overview

This module provides comprehensive SSL/TLS certificate management with:

- **Let's Encrypt Integration**: Free, automated certificate provisioning
- **Multi-Domain Support**: Unlimited domain certificate management
- **Automatic Renewal**: Proactive certificate renewal and deployment
- **High Availability**: Zero-downtime certificate updates
- **Monitoring**: Certificate expiration tracking and alerting

## Key Features

### Certificate Provisioning
- **Domain Validation**: Automated domain ownership verification
- **SAN Certificates**: Multiple domains in single certificate
- **Wildcard Support**: Subdomain certificate coverage
- **Custom CA Support**: Enterprise certificate authority integration

### Renewal Management
- **Automated Renewal**: 30-day expiration window renewal
- **Graceful Deployment**: Hot certificate reloading
- **Rollback Capability**: Automatic recovery from failed updates
- **Notification System**: Webhook alerts for renewal events

## Service Architecture

### Certificate Lifecycle
```
Domain Registration → Validation → Issuance → Deployment → Monitoring → Renewal
```

### Integration Points
1. **DNS Validation**: Automated DNS challenge handling
2. **Web Server Integration**: Automatic configuration updates
3. **Mail Server Integration**: SMTP/IMAP certificate deployment
4. **Monitoring Integration**: Health checks and alerting

## Configuration Management

### Domain Configuration
```python
# Certificate configuration
domains = [
    "mail.example.com",      # Primary mail server
    "smtp.example.com",      # SMTP service
    "imap.example.com",      # IMAP service
    "webmail.example.com"    # Webmail interface
]
```

### Certificate Policies
- **Validation Method**: DNS-01 or HTTP-01 challenges
- **Key Algorithm**: RSA 2048-bit or ECDSA P-256
- **Renewal Window**: 30 days before expiration
- **Backup Strategy**: Encrypted certificate storage

## Scripts & Tools

### Core Scripts
- `cert_manager.py` - Main certificate management service

### Management Features
- **Bulk Certificate Operations**: Multi-domain provisioning
- **Certificate Import/Export**: Migration and backup tools
- **Health Monitoring**: Certificate status verification

## Automated Renewal Process

### Renewal Workflow
1. **Expiration Check**: Daily certificate expiration monitoring
2. **Pre-Renewal Validation**: Domain accessibility verification
3. **Certificate Request**: Automated Let's Encrypt API calls
4. **Validation Completion**: DNS/HTTP challenge handling
5. **Certificate Deployment**: Service configuration updates
6. **Service Reload**: Graceful service restarts
7. **Verification**: Post-deployment certificate validation

### Failure Handling
- **Retry Logic**: Exponential backoff for failed requests
- **Fallback Strategies**: Alternative validation methods
- **Alert Generation**: Immediate notification of failures
- **Manual Override**: Emergency manual certificate installation

## High Availability Features

### Zero-Downtime Updates
```bash
# Certificate hot-reload process
1. Generate new certificate
2. Validate certificate chain
3. Update configuration files
4. Signal services to reload
5. Verify new certificate active
6. Archive old certificate
```

### Service Integration
- **Postfix**: SMTP service certificate updates
- **Dovecot**: IMAP/POP3 service certificates
- **Nginx**: Web service certificate deployment
- **Health Monitor**: Certificate status monitoring

## Monitoring & Alerting

### Certificate Tracking
- **Expiration Monitoring**: Real-time expiration tracking
- **Chain Validation**: Complete certificate chain verification
- **Domain Coverage**: Multi-domain certificate monitoring
- **Performance Metrics**: Certificate operation timing

### Webhook Notifications
```json
{
  "event": "certificate_renewed",
  "domain": "mail.example.com",
  "expiration_date": "2024-04-15T00:00:00Z",
  "renewal_success": true,
  "services_reloaded": ["postfix", "dovecot", "nginx"]
}
```

### Alert Conditions
- **Renewal Failures**: Failed certificate renewal attempts
- **Expiration Warnings**: 7-day expiration notifications
- **Validation Errors**: Domain validation failures
- **Service Integration Issues**: Certificate deployment problems

## Security Considerations

### Private Key Management
- **Secure Generation**: Cryptographically secure key creation
- **File Permissions**: Restricted access (600 permissions)
- **Storage Encryption**: Optional private key encryption
- **Key Rotation**: Periodic key regeneration capability

### Certificate Validation
- **Chain Verification**: Complete certificate chain validation
- **Revocation Checking**: OCSP and CRL verification
- **Domain Matching**: Certificate-domain correspondence validation
- **Algorithm Validation**: Approved cryptographic algorithms

## Troubleshooting

### Common Issues
1. **Domain Validation Failures**: DNS configuration or accessibility issues
2. **Rate Limiting**: Let's Encrypt request rate exceeded
3. **Service Integration**: Certificate deployment to services failed
4. **Network Connectivity**: Let's Encrypt API connectivity problems

### Diagnostic Commands
```bash
# Check certificate status
openssl x509 -in /path/to/cert.pem -text -noout

# Verify certificate chain
openssl verify -CAfile ca-bundle.pem certificate.pem

# Test certificate expiration
openssl x509 -in certificate.pem -checkend 86400

# Check service certificate usage
openssl s_client -connect mail.example.com:993 -servername mail.example.com
```

## Dependencies

- **certbot**: Let's Encrypt client for certificate management
- **OpenSSL**: Certificate operations and validation
- **DNS Provider APIs**: Automated DNS challenge handling
- **Web Services**: HTTP challenge hosting capability

## Integration Points

- **Postfix**: SMTP service TLS certificate management
- **Dovecot**: IMAP/POP3 service certificate deployment
- **Nginx**: Web service SSL certificate configuration
- **Health Monitor**: Certificate status monitoring and alerting
- **Webhook System**: Real-time certificate event notifications

This module ensures production-grade SSL/TLS security with automated certificate lifecycle management, eliminating manual certificate administration while maintaining high security standards and service availability.