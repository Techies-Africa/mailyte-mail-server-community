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

1. **Change all default passwords** in `.env` before deploying — the API refuses
   to start on a missing, known-weak, or still-`CHANGE_ME_` secret
   (`worker/api/startup_checks.py`). Validate the full stack-wide set up front
   with `python3 worker/api/startup_checks.py`; there is no working insecure
   default to fall back on.
2. **Generate the private-key encryption KEK** with `./scripts/generate_dkim_kek.sh`
   and mount it read-only at `/run/secrets/encryption_kek`. DKIM signing keys are
   stored AES-256-GCM encrypted under it, so a stolen database dump alone does not
   let anyone sign mail as your domains. Back the KEK up separately from your
   database backups. Upgrading an existing install? Its old keys were stored in
   plaintext and must be assumed compromised — run
   `python3 scripts/generate_dkim.py --rotate-all` and publish the new DNS records
   it prints.
3. **Use TLS** for all connections (enabled by default)
4. **Restrict ports** with your firewall — only expose 25, 465, 587, 993, 995
5. **Keep Docker images updated** with `docker compose pull`
6. **Enable Let's Encrypt** for production certificates
7. **Review API keys** regularly and rotate them
8. **Back up regularly** using `./start.sh` backup option

## Scope

This policy covers the Mailyte Email Server Community Edition. For security issues in the Enterprise Edition, contact enterprise-security@mailyte.com.
