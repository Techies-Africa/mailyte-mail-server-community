---
title: "Troubleshooting: SSL Certificate Issues"
description: Fix certificate renewal failures, domain mismatches, Let's Encrypt rate limits, SNI problems, and TLS configuration issues.
---

# SSL Certificate Issues

SSL/TLS problems usually show up as connection errors in mail clients, browser warnings on the API, or failed certificate renewals. Here's how to diagnose and fix them.

## How Certificates Work in Mailyte

The `cert_manager` container obtains and renews Let's Encrypt certificates using **HTTP-01 webroot challenges** (the `acme_webroot` service answers `/.well-known/acme-challenge/` on port 80; in production Traefik routes that path to it). Certificates land in `storage/ssl_certs/` and `storage/ssl_private/` on the host, which Postfix and Dovecot mount at `/etc/ssl/certs/custom` and `/etc/ssl/private/custom`. Per-domain SNI maps are written to `storage/sni_config/` (`postfix_sni.map` and `dovecot_sni.conf`).

## Quick Check

```bash
# Check the certificate served on submission
docker exec postfix sh -c "openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject -issuer"

# Check IMAPS certificate from outside
openssl s_client -connect mail.yourdomain.com:993 < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject

# Check cert_manager logs
docker logs cert_manager --tail 50

# Certificate inventory via the API
curl https://api.yourdomain.com/api/v1/ssl/status -H "X-API-Key: YOUR_API_KEY"
curl https://api.yourdomain.com/api/v1/ssl/certificates -H "X-API-Key: YOUR_API_KEY"
```

## Problem: Certificate Not Renewing

### Check cert_manager Logs

```bash
docker logs cert_manager 2>&1 | grep -i "error\|fail\|renew\|acme"
```

### Common Causes

**Port 80 not reachable:**

Let's Encrypt needs to reach port 80 on your server for HTTP-01 challenges.

```bash
# Check from outside
curl -v http://mail.yourdomain.com/.well-known/acme-challenge/test

# Check firewall
sudo iptables -L -n | grep 80
```

**DNS not pointing to your server:**

```bash
dig A mail.yourdomain.com +short
# Should return your server's IP
```

**ACME staging mode still on:**

```bash
docker exec cert_manager env | grep ACME_STAGING
```

`ACME_STAGING` **defaults to `true`** — staging-CA certificates are not trusted by clients. Set `ACME_STAGING=false` in `.env` for production and recreate the container.

**Rate limit hit:**

Let's Encrypt has rate limits:

- 50 certificates per registered domain per week
- 5 duplicate certificates per week
- 300 new orders per account per 3 hours

If you hit a limit, wait for the window to reset. Check at [crt.sh](https://crt.sh/?q=yourdomain.com) to see recent issuance.

### Manual Renewal

```bash
# Trigger renewal for one domain via the API
curl -X POST https://api.yourdomain.com/api/v1/ssl/certificates/yourdomain.com/renew \
  -H "X-API-Key: YOUR_API_KEY"

# Or restart cert_manager — it re-evaluates all certificates on startup
docker compose restart cert_manager
docker logs cert_manager --tail 20
```

## Problem: Domain Mismatch

The certificate doesn't match the hostname the client is connecting to.

### Symptoms

- Mail clients show "certificate mismatch" warnings
- `openssl s_client` shows a different CN/SAN than expected

### Diagnosis

```bash
# Check what domains the certificate covers
docker exec postfix sh -c "openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -text" | grep -A1 "Subject Alternative Name"
```

### Fix

The certificate needs to include the hostname clients connect to — the advertised mail hostname (`MAIL_HOSTNAME` in `.env`, which is also what autoconfig hands out to mail clients). Check that it matches:

```bash
grep -E "^(MAIL_)?HOSTNAME" .env
```

For per-domain certificates served via SNI, check the maps cert_manager generates:

```bash
cat storage/sni_config/postfix_sni.map
cat storage/sni_config/dovecot_sni.conf
```

## Problem: Certificate Expired

### Verify Expiry

```bash
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null | openssl x509 -noout -enddate
```

### Quick Fix: Renew and Restart

```bash
curl -X POST https://api.yourdomain.com/api/v1/ssl/certificates/yourdomain.com/renew \
  -H "X-API-Key: YOUR_API_KEY"
# or
docker compose restart cert_manager
```

### Emergency: Self-Signed Certificate

If Let's Encrypt is down or you've hit rate limits, generate a temporary self-signed cert:

```bash
openssl req -x509 -nodes -days 30 \
  -newkey rsa:2048 \
  -keyout storage/ssl_private/emergency.key \
  -out storage/ssl_certs/emergency.crt \
  -subj "/CN=mail.yourdomain.com"

# Restart mail services to pick up the new cert
docker compose restart postfix dovecot
```

!!! warning "Self-signed certificates"
    Self-signed certs cause warnings in mail clients and may cause other mail servers to reject connections. Use this only as a temporary measure.

## Problem: TLS Handshake Failures

### Symptoms

```
# In the Postfix log (logs/mailer/postfix/mail.log)
warning: TLS library problem: error:14209102:SSL routines:tls_early_post_process_client_hello:unsupported protocol
```

### Check TLS Configuration

```bash
# Test which TLS versions are supported
for ver in tls1 tls1_1 tls1_2 tls1_3; do
  result=$(echo | openssl s_client -connect mail.yourdomain.com:587 -starttls smtp -$ver 2>&1 | grep "Protocol")
  echo "$ver: $result"
done
```

Usually the client is at fault (an ancient mail app requiring TLS 1.0). The server-side protocol settings live in `mailer/postfix/config/main.cf` — note this file is baked into the postfix image, so changes require a rebuild (`docker compose build postfix`), not just a restart.

## Problem: SNI Not Working

SNI (Server Name Indication) lets you serve different certificates for different domains on the same IP.

### Check the SNI Maps

```bash
# Generated by cert_manager into storage/sni_config/
cat storage/sni_config/postfix_sni.map     # tls_server_sni_maps format
cat storage/sni_config/dovecot_sni.conf    # local_name {} blocks
```

Both files are mounted read-only into postfix and dovecot at `/etc/ssl/sni`.

### Test SNI

```bash
# Test with a specific hostname
openssl s_client -connect mail.yourdomain.com:993 -servername customer-domain.com < /dev/null 2>/dev/null | openssl x509 -noout -subject
```

### Fix

Make sure cert_manager has issued a certificate for the domain (check `GET /api/v1/ssl/certificates/{domain}` and `docker logs cert_manager`), then confirm the domain appears in both SNI map files. Restart postfix/dovecot after the maps change.

## Problem: Wildcard Certificate Issues

If you're using a wildcard certificate for `*.yourdomain.com`:

### DNS-01 Challenge Required

Wildcard certs require DNS-01 validation (not HTTP-01). Make sure:

```bash
docker exec cert_manager env | grep -E "WILDCARD_DOMAIN|DNS_PROVIDER"
```

Both must be set in `.env` — `WILDCARD_DOMAIN=yourdomain.com` and `DNS_PROVIDER` naming your DNS provider (e.g., `route53`, `cloudflare`), with the provider's API credentials supplied.

### Verify DNS API Access

```bash
docker logs cert_manager 2>&1 | grep -i "dns\|challenge\|wildcard"
```

## Certificate Debugging Commands

```bash
# Full certificate details
openssl s_client -connect mail.yourdomain.com:993 < /dev/null 2>/dev/null | openssl x509 -noout -text

# Check certificate chain
openssl s_client -connect mail.yourdomain.com:993 -showcerts < /dev/null 2>/dev/null

# Check certificate file directly
openssl x509 -in storage/ssl_certs/your-cert.crt -noout -text

# Verify certificate matches private key
openssl x509 -noout -modulus -in cert.crt | md5sum
openssl rsa -noout -modulus -in key.key | md5sum
# Both MD5 sums should match
```
