# Webmail Setup

Mailyte uses **Docker Compose profiles** for webmail, so you only run the client you need — or none at all if you're using the API only.

## Available Clients

| Client | Type | Port | Best For |
|--------|------|------|----------|
| **Roundcube** | Lightweight webmail | 8880 | Simple email access, low resource usage |
| **SOGo** | Full groupware | 8881 | Webmail + Calendar + Contacts + ActiveSync |
| **None** | API-only mode | — | Headless / custom frontend |

## Choosing a Client

Set `COMPOSE_PROFILES` in your `.env` file:

```bash
# Roundcube only (default)
COMPOSE_PROFILES=roundcube

# SOGo only
COMPOSE_PROFILES=sogo

# Both (different ports)
COMPOSE_PROFILES=roundcube,sogo

# No webmail (API-only mode)
# Simply omit COMPOSE_PROFILES or leave it empty
```

Then start services:

```bash
docker compose up -d
```

Only the selected profile's containers will start.

---

## Roundcube

[Roundcube](https://roundcube.net/) is a lightweight, PHP-based webmail client with a modern responsive UI.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUNDCUBE_PORT` | `8880` | Host port for Roundcube |
| `ROUNDCUBE_DB_NAME` | `roundcubemail` | MySQL database name |

### Access

Open `http://your-server:8880` and log in with your mailbox email and password.

### Plugins

The following plugins are enabled by default:

- **archive** — Archive button for messages
- **zipdownload** — Download multiple messages as ZIP
- **managesieve** — Manage Sieve mail filters from webmail

To add more plugins, create a custom config override:

```bash
# config/mailer/roundcube/custom.config.inc.php
$config['plugins'] = array_merge($config['plugins'], [
    'enigma',           # PGP encryption
    'password',         # Change password from webmail
    'newmail_notifier', # Desktop notifications
]);
```

---

## SOGo

[SOGo](https://www.sogo.nu/) is a full groupware solution — webmail, calendar (CalDAV), contacts (CardDAV), and ActiveSync for mobile devices.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SOGO_PORT` | `8881` | Host port for SOGo |
| `SOGO_DB_NAME` | `sogo` | MySQL database name |
| `TIMEZONE` | `UTC` | Server timezone |

### Access

Open `http://your-server:8881/SOGo/` and log in with your mailbox email and password.

### Mobile Devices (ActiveSync)

SOGo includes ActiveSync support. On your phone:

1. Go to **Settings > Mail > Add Account > Exchange**
2. Server: `your-server:8881`
3. Username: your full email address
4. Password: your mailbox password

---

## Adding a Custom Webmail Client

You can add any webmail client that supports IMAP/SMTP. Here's the pattern:

### 1. Add to docker-compose.yml

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
      - IMAP_PORT=143
      - SMTP_HOST=postfix
      - SMTP_PORT=25               # Port 25 for internal relay (trusted network)
      # Or use port 587 with STARTTLS:
      # - SMTP_HOST=tls://postfix
      # - SMTP_PORT=587
      - DB_HOST=mysql
      - DB_NAME=my_webmail
      - DB_USER=${DB_USER:-mailuser}
      - DB_PASSWORD=${DB_PASSWORD:-mailpassword}
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
docker exec mysql mysql -u root -p<ROOT_PASSWORD> -e \
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
| IMAP | `dovecot` | 143 | STARTTLS | User: full email, Pass: mailbox password |
| IMAP SSL | `dovecot` | 993 | SSL/TLS | Same credentials |
| SMTP (relay) | `postfix` | 25 | None | Trusted within Docker network |
| SMTP (auth) | `postfix` | 587 | STARTTLS | Requires login credentials |
| ManageSieve | `dovecot` | 4190 | STARTTLS | For mail filter management |
| MySQL | `mysql` | 3306 | — | For webmail's own database |

!!! tip "Port 25 vs 587"
    For webmail containers **inside the Docker network**, use port 25 — Postfix trusts `172.20.0.0/16` via `mynetworks`, so no authentication is needed. Port 587 requires STARTTLS + SASL auth, which can be tricky with self-signed certificates.

### Popular Alternatives

| Client | Docker Image | Notes |
|--------|-------------|-------|
| [Roundcube](https://roundcube.net/) | `roundcube/roundcubemail` | Lightweight, PHP |
| [SOGo](https://www.sogo.nu/) | `ghcr.io/inverse-inc/sogo` | Full groupware |
| [Snappymail](https://snappymail.eu/) | `djmaze/snappymail` | Fast, lightweight |
| [Cypht](https://cypht.org/) | — | Aggregator, multiple accounts |
| [Rainloop](https://www.rainloop.net/) | `hardware/rainloop` | Simple, modern |
| [Mailtrain](https://mailtrain.org/) | — | Newsletter management |

All of these work with Mailyte by pointing IMAP to `dovecot:143` and SMTP to `postfix:25`.
