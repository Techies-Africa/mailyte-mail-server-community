# SSL Certificates

How cert_manager issues and renews Let's Encrypt certificates, generates SNI maps for Postfix/Dovecot/Traefik, and what to do for manual certs.

---

Every connection to Mailyte — SMTP, IMAP, POP3, and the HTTPS surfaces — is encrypted with TLS. Certificates are managed end-to-end by the `cert_manager` container (`mailer/cert_manager/scripts/cert_manager.py`), which runs certbot with the **webroot HTTP-01** challenge.

## How It Works

1. cert_manager reads the active domains from MySQL and builds a SAN candidate list per mail domain: the apex plus `mail.`, `smtp.`, `imap.`, `autoconfig.`, and `autodiscover.` subdomains (the last two exist so Outlook/Thunderbird auto-setup works).
2. Every candidate is checked against **live public DNS** (`A` lookup via 1.1.1.1) and kept only if it resolves to this server — one non-resolving name would fail the whole certbot order.
3. `certbot certonly --webroot --webroot-path /var/www/acme-challenge --preferred-challenges http --cert-name <domain> -d <surviving names...>` issues the cert. The `acme_webroot` nginx container serves `/.well-known/acme-challenge/` (Traefik routes that path prefix to it on port 80, at priority 100 — above the HTTPS redirect).
4. The lineage lands in the `letsencrypt_data` volume (`/etc/letsencrypt/live/<domain>/`); cert_manager then **deploys** copies to `${SSL_CERT_PATH}/<domain>.crt`, `<domain>_fullchain.crt` and `${SSL_KEY_PATH}/<domain>.key` (host: `storage/ssl_certs/`, `storage/ssl_private/`), and records the cert in the `ssl_certificates` table.
5. It regenerates three SNI outputs under `SNI_CONFIG_PATH` (`/etc/ssl/sni`, host `storage/sni_config/`):
   - `postfix_sni.map` — hostname → key/cert lines; the Postfix entrypoint copies it to `/etc/postfix/sni_certs.map` and runs `postmap -F`
   - `dovecot_sni.conf` — `local_name` blocks; the Dovecot entrypoint copies it to `/etc/dovecot/conf.d/sni.conf`
   - `traefik_certs.yml` — a Traefik file-provider TLS list, watched live
6. It reloads Postfix and Dovecot by sending SIGHUP through the scoped docker-socket proxy (`DOCKER_RELOAD_ENABLED=true`, `DOCKER_PROXY_URL=http://docker-proxy:2375`).

Admin hostnames (`TRAEFIK_ADMIN_SUBDOMAINS`, default `api,autoconfig,jmap,caldav,docs,grafana,traefik,console` under `$DOMAIN`, plus any fully-qualified `TRAEFIK_EXTRA_HOSTNAMES`) get their own single-name certs and are excluded from the Postfix/Dovecot maps.

## Required Environment Variables

```bash
ACME_EMAIL=admin@yourdomain.com   # ACME account (ACME_EMAILS=a@x,b@y for an account pool)
ACME_STAGING=false                # true = staging CA (untrusted certs, for testing)
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com
```

The `ACME_EMAIL` address receives expiration warnings from Let's Encrypt if renewal fails — use a monitored address.

> [!NOTE]
> `SSL_CERT_PATH` and `SSL_KEY_PATH` are **directories** (defaults `/etc/ssl/certs` and `/etc/ssl/private`), not file paths. Postfix and Dovecot read the shared `server.crt` / `server.key` from their own mounts of these directories, plus their per-domain SNI files.

## Auto-Renewal

cert_manager loops every `CERT_CHECK_INTERVAL` seconds (default `21600` = 6 hours) and renews any certificate within `CERT_RENEWAL_DAYS` (default 30) of expiry, then regenerates the SNI outputs and reloads Postfix/Dovecot. There is no cron — it's one long-lived process under supervisord.

Manual check/renewal:

```bash
docker exec cert_manager certbot certificates          # what exists
docker exec cert_manager certbot renew --dry-run       # test renewal
docker logs cert_manager --tail 100                    # what the manager is doing
```

> [!WARNING]
> certbot takes an exclusive lock on its config directory, so parallel issuance is serialized internally — a long queue of new domains is processed one order at a time. A built-in guard also refuses new orders when ~40 certificates have been issued in the last 7 days (Let's Encrypt limit: 50/week).

## Wildcard Certificates (DNS-01)

Only if **both** `WILDCARD_DOMAIN` and `DNS_PROVIDER` are set, cert_manager issues `-d $WILDCARD_DOMAIN -d *.$WILDCARD_DOMAIN` via the matching certbot DNS plugin and deploys the result as the shared `server.crt`/`server.key` (plus `wildcard.crt`/`wildcard.key`). DNS-01 is the only way to get wildcards; provider credentials must be supplied the way the certbot plugin expects.

## Manual Certificate Installation

If you bring your own certificates, bypass cert_manager for those names:

1. Place PEM files where the containers can see them, e.g. copy into `storage/ssl_certs/` / `storage/ssl_private/` using cert_manager's naming (`<domain>.crt`, `<domain>_fullchain.crt`, `<domain>.key`, key mode `0600`).
2. Add the hostnames to the SNI files (or replace `server.crt`/`server.key` for the default identity).
3. Reload:

```bash
docker exec postfix postfix reload
docker exec dovecot doveadm reload
```

> [!WARNING]
> You own renewal for manual certs, and cert_manager may regenerate the SNI files when other domains renew — keep manual entries in the database (`ssl_certificates`) consistent or they'll be dropped from the maps.

## Certificate File Formats

Mailyte expects PEM. Converting:

```bash
openssl x509 -inform DER -in cert.der -out cert.pem
openssl pkcs12 -in cert.pfx -out cert.pem -nodes
openssl pkcs12 -in cert.pfx -out key.pem -nodes -nocerts
```

## Verifying Your Setup

```bash
# SMTP STARTTLS (587) — SNI matters, use -servername
openssl s_client -starttls smtp -connect mail.yourdomain.com:587 -servername mail.yourdomain.com

# IMAPS (993)
openssl s_client -connect mail.yourdomain.com:993 -servername mail.yourdomain.com

# SMTPS (465)
openssl s_client -connect mail.yourdomain.com:465 -servername mail.yourdomain.com

# Expiry
echo | openssl s_client -connect mail.yourdomain.com:993 -servername mail.yourdomain.com 2>/dev/null | openssl x509 -noout -dates
```

All of these should show the expected certificate without errors. If a customer domain shows the default certificate instead of its own, check that its hostnames resolve to this server (step 2 above) and that its SNI entries exist in `storage/sni_config/`.
