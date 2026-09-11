# Autoconfig Worker

The autoconfig worker serves email client auto-configuration for **every hosted domain**: Mozilla Autoconfig (Thunderbird), Microsoft Autodiscover (Outlook), MTA-STS policies, and a DNS record generator. It is a FastAPI service that answers with the advertised mail hostname (`MAIL_HOSTNAME`) so clients are configured against the customer-facing IMAP/SMTP name, not the server's PTR identity.

## Publicly Routed Since 2026-08-27

Client auto-setup must answer for every customer domain, not just this server's own. The production Traefik router uses `HostRegexp` wildcards:

```
HostRegexp(`autoconfig.{domain:.+}`) || HostRegexp(`autodiscover.{domain:.+}`) || HostRegexp(`mta-sts.{domain:.+}`)
```

Before 2026-08-27 the rule was a single literal `Host('autoconfig.${DOMAIN}')`, so `autoconfig.<customer-domain>` and every `autodiscover.*` host 404'd -- while the panel told customers to create exactly those DNS records. Certificates for these hostnames are issued by cert_manager.

## API Endpoints

Copied from the route decorators in `worker/autoconfig/app.py`:

```
GET  /mail/config-v1.1.xml                          -- Mozilla Autoconfig
GET  /.well-known/autoconfig/mail/config-v1.1.xml   -- Mozilla Autoconfig (alt path)
POST /autodiscover/autodiscover.xml                 -- Microsoft Autodiscover POX
POST /autodiscover/autodiscover.json                -- Microsoft Autodiscover V2
GET  /mta-sts.txt                                   -- MTA-STS policy
GET  /.well-known/mta-sts.txt                       -- MTA-STS policy (canonical path)
GET  /dns-records/{domain}                          -- DNS record generator for a domain
GET  /health                                        -- Health check
GET  /metrics                                       -- Prometheus metrics
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8100` | Bind port |
| `HOSTNAME` | `mail.example.com` | This server's own identity (PTR/HELO) |
| `MAIL_HOSTNAME` | (unset) | The customer-facing IMAP/SMTP hostname handed to clients -- distinct from `HOSTNAME` on purpose |
| `DOMAIN` | `example.com` | Base domain |
| `MTA_STS_MODE` | `testing` (dev) / `enforce` (prod override) | MTA-STS policy mode |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `mysql` / `3306` / `mailserver` / -- / -- | Domain lookups |

## Docker Configuration

```yaml
autoconfig:
  build:
    context: .
    dockerfile: ./worker/autoconfig/Dockerfile
  container_name: autoconfig
  ports:
    - "8100:8100"
  depends_on:
    - mysql
    - migrate
```

In production the host port is bound to `127.0.0.1`; all public traffic arrives through the Traefik wildcard router above.

## Gotchas

!!! warning "Every customer domain needs the DNS records"
    Auto-setup only works when `autoconfig.<domain>` / `autodiscover.<domain>` (and `mta-sts.<domain>` for MTA-STS) resolve to this server -- `GET /dns-records/{domain}` generates the full set a customer needs.

!!! note "Not the same as Z-Push autodiscover"
    The ActiveSync worker (Enterprise Edition) bundles Z-Push's own autodiscover for EAS clients on its container. Platform-wide autodiscover routing points here.
