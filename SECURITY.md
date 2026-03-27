# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Mailyte Email Server, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, email **security@mailyte.com** with:

1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to provide a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |

## Security Best Practices

When deploying Mailyte, ensure you:

1. **Change all default passwords** in `.env` before deploying
2. **Use TLS** for all connections (enabled by default)
3. **Restrict ports** with your firewall — only expose 25, 465, 587, 993, 995
4. **Keep Docker images updated** with `docker compose pull`
5. **Enable Let's Encrypt** for production certificates
6. **Review API keys** regularly and rotate them
7. **Back up regularly** using `./start.sh` backup option

## Scope

This policy covers the Mailyte Email Server Community Edition. For security issues in the Enterprise Edition, contact enterprise-security@mailyte.com.
