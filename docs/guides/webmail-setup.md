---
title: Webmail Setup
description: Run the first-party Mailyte Webmail, Roundcube, or SOGo — Docker Compose profiles, ports, and correct IMAP/SMTP wiring.
---

# Webmail Setup

Mailyte uses **Docker Compose profiles** for webmail, so you only run the client you need — or none at all if you're using the API only.

## Available Clients

| Client | Type | Profile | Port (dev) | Best For |
|--------|------|---------|------------|----------|
| **Mailyte Webmail** | First-party Next.js webmail (talks to the Mailyte API) | `webmail` | `127.0.0.1:3200` | The supported Mailyte experience; published 2026-08-26 |
| **Roundcube** | Lightweight PHP webmail (IMAP/SMTP) | `roundcube` | 8880 | Simple email access, low resource usage |
| **SOGo** | Full groupware (IMAP/SMTP) | `sogo` | 8881 | Webmail + Calendar + Contacts |
| **None** | API-only mode | — | — | Headless / custom frontend |

## Choosing a Client

Set `COMPOSE_PROFILES` in your `.env` file:

```bash
# First-party Mailyte webmail
COMPOSE_PROFILES=webmail

# Roundcube only
COMPOSE_PROFILES=roundcube

# SOGo only
COMPOSE_PROFILES=sogo

# Several at once (different ports)
COMPOSE_PROFILES=webmail,roundcube

# No webmail (API-only mode)
# Simply omit COMPOSE_PROFILES or leave it empty
```

Then start services:

```bash
docker compose up -d
```

Only the selected profiles' containers start. You can also start one ad hoc: `docker compose --profile webmail up -d webmail`.

---

## Mailyte Webmail (First-Party)

The `webmail` service runs `ghcr.io/techies-africa/mailyte-webmail` (source: [Techies-Africa/mailyte-webmail](https://github.com/Techies-Africa/mailyte-webmail)). Unlike Roundcube/SOGo it does not speak IMAP directly from the browser — it proxies every call server-side to the Mailyte API, which keeps the mailbox session token out of page scripts.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBMAIL_VERSION` | `latest` | Image tag |
| `WEBMAIL_PORT` | `127.0.0.1:3200` | Host binding (dev; production publishes via Traefik instead) |
| `MAILBOX_API_BASE_URL` | `http://api:8080/api/v1` | Where the mail API is *from the container* — read at request time, never baked into the browser bundle |

### Access

- **Development**: `http://localhost:3200` after `docker compose --profile webmail up -d webmail`
- **Production**: served by Traefik on two hostnames — `WEBMAIL_HOST` (default `webmail.<domain>`) **and** `MAIL_HOSTNAME` (the advertised IMAP/SMTP hostname), so typing the mail hostname into a browser lands on webmail, mail.google.com-style. Both names get certificates from cert_manager. (Live since 2026-08-28.)

Log in with a mailbox email address and password — authentication is verified against Dovecot via the API, so it always matches what IMAP would accept.

---

## Roundcube

[Roundcube](https://roundcube.net/) is a lightweight, PHP-based webmail client with a modern responsive UI.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUNDCUBE_PORT` | `8880` | Host port for Roundcube |
| `ROUNDCUBE_DB_NAME` | `roundcubemail` | MySQL database name |

As wired in `docker-compose.yml`, Roundcube connects to IMAP at `dovecot:143` and — importantly — sends through **`tls://postfix:587` with LOGIN authentication**, not port 25.

### Access

Open `http://your-server:8880` and log in with your mailbox email and password.

### Plugins

The following plugins are enabled by default (`ROUNDCUBEMAIL_PLUGINS`):

- **archive** — Archive button for messages
- **zipdownload** — Download multiple messages as ZIP
- **managesieve** — Manage Sieve mail filters from webmail

Extra configuration is mounted from `config/mailer/roundcube/custom.config.inc.php`:

```php
# config/mailer/roundcube/custom.config.inc.php
$config['plugins'] = array_merge($config['plugins'], [
    'newmail_notifier', # Desktop notifications
]);
```

---

## SOGo

[SOGo](https://www.sogo.nu/) is a full groupware solution — webmail, calendar, and contacts.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SOGO_PORT` | `8881` | Host port for SOGo |
| `SOGO_DB_NAME` | `sogo` | MySQL database name |
| `TIMEZONE` | `UTC` | Server timezone |

SOGo connects to `dovecot` for IMAP and `postfix` for SMTP, using its own MySQL database.

### Access

Open `http://your-server:8881/SOGo/` and log in with your mailbox email and password.

---

## Mobile and Desktop Clients (instead of webmail)

Since 2026-08-27 Mailyte serves client auto-setup for every hosted domain: Thunderbird autoconfig at `autoconfig.<domain>`, Outlook autodiscover at `autodiscover.<domain>`, and MTA-STS policies — all pointing clients at the advertised `MAIL_HOSTNAME` with the right ports. For Exchange-style mobile sync there is a native ActiveSync service (`activesync`, host port 8084). Users can usually just type their email address and password into their mail app.

## Adding a Custom Webmail Client

You can add any webmail client that supports IMAP/SMTP. The pattern:

### 1. Add to docker-compose.yml (or an override file)

```yaml
services:
  my-webmail:
    image: your-webmail-image:latest
    container_name: my-webmail
    profiles: ["my-webmail"]           # <-- profile name
    ports:
      - "${MY_WEBMAIL_PORT:-8882}:80"
    environment:
      # Point to Mailyte's Dovecot and Postfix
      - IMAP_HOST=dovecot
      - IMAP_PORT=143                  # STARTTLS
      - SMTP_HOST=postfix
      - SMTP_PORT=587                  # STARTTLS + SASL auth (see note below)
      - DB_HOST=mysql
      - DB_NAME=my_webmail
      - DB_USER=${DB_USER:-mailuser}
      - DB_PASSWORD=${DB_PASSWORD}
    depends_on:
      mysql:
        condition: service_healthy
      dovecot:
        condition: service_started
      postfix:
        condition: service_started
    networks:
      - mailserver_network
    restart: unless-stopped
```

### 2. Add to .env

```bash
COMPOSE_PROFILES=my-webmail
MY_WEBMAIL_PORT=8882
```

### 3. Create the database

```bash
docker exec mysql mysql -u root -p"$(cat secrets/db_root_password)" -e \
  "CREATE DATABASE IF NOT EXISTS my_webmail;
   GRANT ALL PRIVILEGES ON my_webmail.* TO 'mailuser'@'%';
   FLUSH PRIVILEGES;"
```

### 4. Start it

```bash
docker compose up -d
```

### Connection Details for Any Webmail

| Protocol | Host | Port | Auth | Notes |
|----------|------|------|------|-------|
| IMAP | `dovecot` | 143 | STARTTLS + login | User: full email, Pass: mailbox password |
| IMAP SSL | `dovecot` | 993 | SSL/TLS + login | Same credentials |
| SMTP submission | `postfix` | 587 | STARTTLS + SASL login | Required for sending |
| SMTPS | `postfix` | 465 | TLS + SASL login | Alternative to 587 |
| ManageSieve | `dovecot` | 4190 | STARTTLS | For mail filter management |
| MySQL | `mysql` | 3306 | — | For the webmail's own database |

!!! warning "Authenticate on 587/465 — port 25 is not a free relay"
    Postfix's `mynetworks` is only `127.0.0.0/8` — containers on the Docker network are **not** trusted, so unauthenticated submission on port 25 from a webmail container is rejected. Send on 587 (STARTTLS) or 465 (TLS) with the user's own credentials. Sender-login mismatch is enforced on those ports: the authenticated user must own the `From:` address (this is how Roundcube is wired).

### Popular Alternatives

| Client | Docker Image | Notes |
|--------|-------------|-------|
| [Roundcube](https://roundcube.net/) | `roundcube/roundcubemail` | Lightweight, PHP |
| [SOGo](https://www.sogo.nu/) | `ghcr.io/inverse-inc/sogo` | Full groupware |
| [SnappyMail](https://snappymail.eu/) | `djmaze/snappymail` | Fast, lightweight |
| [Cypht](https://cypht.org/) | — | Aggregator, multiple accounts |

All of these work with Mailyte by pointing IMAP at `dovecot:143` and authenticated SMTP at `postfix:587`.
