# SSL Certificates

Let's Encrypt auto-renewal, manual certificate installation, and SNI support for multiple domains.

---

Every connection to Mailyte — SMTP, IMAP, POP3, and the API — is encrypted with TLS. By default, Mailyte uses Let's Encrypt for free, automatically renewed certificates.

## Let's Encrypt Setup

The `cert-manager` container handles certificate provisioning and renewal. It uses the ACME protocol to get certificates from Let's Encrypt.

### How It Works

1. On first boot, cert-manager requests a certificate for the hostname defined in `HOSTNAME`.
2. Let's Encrypt verifies you control the domain (via HTTP-01 or DNS-01 challenge).
3. The certificate is saved to `/etc/letsencrypt/live/$HOSTNAME/`.
4. cert-manager checks daily and renews certificates that expire within 30 days.
5. After renewal, it signals Postfix and Dovecot to reload their TLS config.

### Required Environment Variables

```bash
HOSTNAME=mail.yourdomain.com
ACME_EMAIL=admin@yourdomain.com
SSL_CERT_PATH=/etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
SSL_KEY_PATH=/etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
```

The `ACME_EMAIL` receives expiration warnings from Let's Encrypt if auto-renewal fails. Use a real address you monitor.

### Challenge Types

**HTTP-01 (default):** Let's Encrypt makes an HTTP request to your server on port 80. The cert-manager container handles the response. You need port 80 open and pointing to the server.

**DNS-01:** Let's Encrypt checks for a specific DNS TXT record. Useful if port 80 isn't available or if you need wildcard certificates. Configure it in the cert-manager settings:

```yaml
# docker-compose.yml cert-manager section
environment:
  ACME_CHALLENGE: dns
  DNS_PROVIDER: cloudflare
  CF_API_TOKEN: your-cloudflare-api-token
```

> [!TIP]
> DNS-01 is the only way to get wildcard certificates (`*.yourdomain.com`). If you're hosting multiple subdomains, this can simplify your setup.

## Auto-Renewal

Renewal happens automatically. The cert-manager container runs a daily check. When a certificate is within 30 days of expiration, it renews.

After renewal, the container sends a `SIGHUP` to Postfix and Dovecot, which makes them reload certificates without dropping active connections.

You can manually trigger a renewal check:

```bash
docker exec mailyte-cert-manager certbot renew --dry-run  # Test only
docker exec mailyte-cert-manager certbot renew             # Actually renew
```

> [!NOTE]
> Let's Encrypt certificates are valid for 90 days. The 30-day renewal window gives you plenty of buffer. If renewal fails, you'll get email warnings at the `ACME_EMAIL` address at 20 days, 10 days, and 1 day before expiration.

## Manual Certificate Installation

If you have certificates from another CA (or self-signed certs for testing), you can skip Let's Encrypt entirely.

### Step 1: Place Your Files

Put your certificate and key where the containers can reach them:

```bash
mkdir -p /opt/mailyte/ssl
cp your-cert.pem /opt/mailyte/ssl/fullchain.pem
cp your-key.pem /opt/mailyte/ssl/privkey.pem
chmod 600 /opt/mailyte/ssl/privkey.pem
```

### Step 2: Mount in Docker Compose

```yaml
services:
  postfix:
    volumes:
      - /opt/mailyte/ssl:/etc/ssl/mailyte:ro

  dovecot:
    volumes:
      - /opt/mailyte/ssl:/etc/ssl/mailyte:ro
```

### Step 3: Update Environment Variables

```bash
SSL_CERT_PATH=/etc/ssl/mailyte/fullchain.pem
SSL_KEY_PATH=/etc/ssl/mailyte/privkey.pem
```

### Step 4: Reload Services

```bash
docker exec mailyte-postfix postfix reload
docker exec mailyte-dovecot doveadm reload
```

> [!WARNING]
> When using manual certificates, you're responsible for renewal. Set a calendar reminder. Expired certificates mean broken email for all users.

## SNI for Multiple Domains

Server Name Indication (SNI) lets Mailyte serve different certificates for different domains on the same IP address. This is essential for multi-tenant setups where each organization has their own domain.

### Postfix SNI

Add to `/etc/postfix/main.cf`:

```ini
tls_server_sni_maps = hash:/etc/postfix/sni_maps
```

Create `/etc/postfix/sni_maps`:

```
mail.example.com    /etc/letsencrypt/live/mail.example.com/fullchain.pem /etc/letsencrypt/live/mail.example.com/privkey.pem
mail.another.org    /etc/letsencrypt/live/mail.another.org/fullchain.pem /etc/letsencrypt/live/mail.another.org/privkey.pem
```

Then hash the file:

```bash
docker exec mailyte-postfix postmap -F hash:/etc/postfix/sni_maps
docker exec mailyte-postfix postfix reload
```

### Dovecot SNI

Add to `/etc/dovecot/conf.d/10-ssl.conf`:

```ini
local_name mail.example.com {
  ssl_cert = </etc/letsencrypt/live/mail.example.com/fullchain.pem
  ssl_key = </etc/letsencrypt/live/mail.example.com/privkey.pem
}

local_name mail.another.org {
  ssl_cert = </etc/letsencrypt/live/mail.another.org/fullchain.pem
  ssl_key = </etc/letsencrypt/live/mail.another.org/privkey.pem
}
```

### Automating Multi-Domain Certificates

For multi-tenant deployments, you'll want to automate certificate provisioning when new domains are added. The Mailyte API handles this — when a new domain is added through the API, it:

1. Triggers cert-manager to request a certificate for the domain.
2. Updates the Postfix SNI map and Dovecot config.
3. Reloads both services.

See the API documentation for the domain provisioning endpoints.

## Certificate File Formats

Mailyte expects PEM-formatted certificates. If you have certificates in other formats:

```bash
# Convert DER to PEM
openssl x509 -inform DER -in cert.der -out cert.pem

# Convert PKCS#12 (.pfx) to PEM
openssl pkcs12 -in cert.pfx -out cert.pem -nodes

# Extract key from PKCS#12
openssl pkcs12 -in cert.pfx -out key.pem -nodes -nocerts
```

## Verifying Your Setup

Check that TLS is working correctly:

```bash
# Test SMTP TLS (port 587)
openssl s_client -starttls smtp -connect mail.yourdomain.com:587

# Test IMAPS (port 993)
openssl s_client -connect mail.yourdomain.com:993

# Test SMTPS (port 465)
openssl s_client -connect mail.yourdomain.com:465

# Check certificate expiration
echo | openssl s_client -connect mail.yourdomain.com:993 2>/dev/null | openssl x509 -noout -dates
```

All of these should show your certificate details without errors.
