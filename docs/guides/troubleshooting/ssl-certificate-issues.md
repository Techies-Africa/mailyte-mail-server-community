---
title: "Troubleshooting: SSL Certificate Issues"
description: Fix certificate renewal failures, domain mismatches, Let's Encrypt rate limits, and TLS configuration problems.
---

# SSL Certificate Issues

SSL/TLS problems usually show up as connection errors in mail clients, browser warnings on the API, or failed certificate renewals. Here's how to diagnose and fix them.

## Quick Check

```bash
# Check current certificate
docker exec -it postfix openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# Check IMAPS certificate
openssl s_client -connect mail.yourdomain.com:993 < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject

# Check cert-manager logs
docker logs cert_manager --tail 50
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

Check your environment:

```bash
docker exec -it cert_manager env | grep ACME_STAGING
```

If `ACME_STAGING=true`, certificates are issued by the staging CA and won't be trusted. Set it to `false` for production.

**Rate limit hit:**

Let's Encrypt has rate limits:

- 50 certificates per registered domain per week
- 5 duplicate certificates per week
- 300 new orders per account per 3 hours

If you hit a limit, wait for the window to reset. Check at [crt.sh](https://crt.sh/?q=yourdomain.com) to see recent issuance.

### Manual Renewal

```bash
# Force a renewal attempt
docker exec -it cert_manager python3 -c "from cert_manager import renew; renew(force=True)"

# Or restart the cert_manager
docker compose restart cert_manager
```

## Problem: Domain Mismatch

The certificate doesn't match the hostname the client is connecting to.

### Symptoms

- Mail clients show "certificate mismatch" warnings
- `openssl s_client` shows a different CN/SAN than expected

### Diagnosis

```bash
# Check what domains the certificate covers
docker exec -it postfix openssl s_client -connect localhost:587 -starttls smtp < /dev/null 2>/dev/null | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
```

### Fix

The certificate needs to include all hostnames clients connect to:

- `mail.yourdomain.com` (primary hostname)
- Any additional domains if using SNI

Check that `HOSTNAME` in your `.env` matches what's in the certificate:

```bash
grep HOSTNAME .env
```

For multi-domain certificates with SNI, check the SNI map:

```bash
docker exec -it postfix cat /etc/ssl/sni/sni_map
```

## Problem: Certificate Expired

### Verify Expiry

```bash
# Check expiry date
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null | openssl x509 -noout -enddate
```

### Quick Fix: Restart cert_manager

```bash
docker compose restart cert_manager
# Wait a minute, then check
docker logs cert_manager --tail 20
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
# In Postfix log
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

### Fix: Ensure Modern TLS

In Postfix config:

```bash
# config/mailer/postfix/custom/main.cf
smtpd_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtpd_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtp_tls_mandatory_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
smtp_tls_protocols = !SSLv2, !SSLv3, !TLSv1, !TLSv1.1
```

## Problem: SNI Not Working

SNI (Server Name Indication) lets you serve different certificates for different domains on the same IP.

### Check SNI Map

```bash
# View the SNI configuration
cat storage/sni_config/sni_map

# It should list domain-to-cert mappings:
# domain1.com /etc/ssl/certs/domain1.crt /etc/ssl/private/domain1.key
# domain2.com /etc/ssl/certs/domain2.crt /etc/ssl/private/domain2.key
```

### Test SNI

```bash
# Test with a specific hostname
openssl s_client -connect mail.yourdomain.com:993 -servername domain2.com < /dev/null 2>/dev/null | openssl x509 -noout -subject
```

### Fix

Make sure the cert_manager has generated certificates for all domains and that the SNI map file is mounted correctly in both Postfix and Dovecot containers.

## Problem: Wildcard Certificate Issues

If you're using a wildcard certificate for `*.yourdomain.com`:

### DNS-01 Challenge Required

Wildcard certs require DNS-01 validation (not HTTP-01). Make sure:

```bash
# Check env vars
docker exec -it cert_manager env | grep -E "WILDCARD_DOMAIN|DNS_PROVIDER"
```

`DNS_PROVIDER` must be set to your DNS provider (e.g., `cloudflare`, `route53`).

### Verify DNS API Access

```bash
# cert_manager logs will show DNS challenge status
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
