# Dovecot Configuration

IMAP and POP3 settings — authentication, quotas, virtual users, namespaces, and SSL.

---

Dovecot is the part users actually connect to when they read email. It handles IMAP (folders, search, sync) and POP3 (download and delete). It also provides the authentication backend that Postfix relies on for SMTP login.

## Authentication

Dovecot authenticates users against MySQL. The config is split across a few files in `/etc/dovecot/conf.d/`.

### Auth Mechanisms

`/etc/dovecot/conf.d/10-auth.conf`:

```ini
disable_plaintext_auth = yes
auth_mechanisms = plain login

!include auth-sql.conf.ext
```

Only `plain` and `login` mechanisms are enabled. This is fine because `disable_plaintext_auth = yes` means these mechanisms only work over TLS connections. Credentials are always encrypted in transit.

### SQL Auth Configuration

`/etc/dovecot/dovecot-sql.conf.ext`:

```ini
driver = mysql
connect = host=mysql dbname=mailserver user=mailyte password=your-db-password

default_pass_scheme = BLF-CRYPT

password_query = SELECT email as user, password FROM virtual_users WHERE email='%u' AND active=1
user_query = SELECT CONCAT('*:storage=', quota, 'M') AS quota_rule FROM virtual_users WHERE email='%u'
```

A few things to note:

- **`BLF-CRYPT`** — Passwords are stored as bcrypt hashes. This is the strongest scheme Dovecot supports.
- **`password_query`** — Looks up the user's hashed password by email address.
- **`user_query`** — Returns the user's quota in megabytes. The `*:storage=` prefix tells Dovecot's quota plugin to apply it to all mailboxes.

> [!WARNING]
> The `connect` string contains your database password. This file should be readable only by root and the `dovecot` user:
> ```bash
> chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext
> chmod 640 /etc/dovecot/dovecot-sql.conf.ext
> ```

### Auth Socket for Postfix

`/etc/dovecot/conf.d/10-master.conf` (auth section):

```ini
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0666
    user = postfix
    group = postfix
  }

  unix_listener auth-userdb {
    mode = 0600
    user = vmail
  }
}
```

This creates the Unix socket that Postfix uses for SASL authentication. The socket lives inside Postfix's chroot so it can reach it.

## Virtual User Mapping

All mailboxes are stored under a single system user (`vmail`). Dovecot maps virtual email addresses to filesystem paths.

`/etc/dovecot/conf.d/10-mail.conf`:

```ini
mail_uid = vmail
mail_gid = vmail
mail_home = /var/mail/vhosts/%d/%n
mail_location = maildir:/var/mail/vhosts/%d/%n/Maildir
```

The variables:
- `%d` — The domain part of the email address (e.g., `yourdomain.com`).
- `%n` — The local part (e.g., `john`).

So `john@yourdomain.com` gets stored at `/var/mail/vhosts/yourdomain.com/john/Maildir/`.

> [!TIP]
> Using Maildir format (one file per message) instead of mbox gives you better performance and safer concurrent access. Each message is a separate file, so there's no locking contention.

## Namespace Setup

`/etc/dovecot/conf.d/15-mailboxes.conf`:

```ini
namespace inbox {
  inbox = yes

  mailbox Drafts {
    auto = subscribe
    special_use = \Drafts
  }

  mailbox Junk {
    auto = subscribe
    special_use = \Junk
  }

  mailbox Trash {
    auto = subscribe
    special_use = \Trash
  }

  mailbox Sent {
    auto = subscribe
    special_use = \Sent
  }

  mailbox Archive {
    auto = no
    special_use = \Archive
  }
}
```

These are the standard IMAP special-use folders. `auto = subscribe` means they're created and subscribed automatically when a mailbox is provisioned. Email clients use the `special_use` flags to identify which folder is which, regardless of the folder name.

## Quota Management

`/etc/dovecot/conf.d/90-quota.conf`:

```ini
plugin {
  quota = maildir:User quota
  quota_rule = *:storage=1G
  quota_rule2 = Trash:storage=+100M
  quota_grace = 10%%

  quota_warning = storage=95%% quota-warning 95 %u
  quota_warning2 = storage=80%% quota-warning 80 %u
  quota_status_success = DUNNO
  quota_status_nouser = DUNNO
  quota_status_overquota = "552 5.2.2 Mailbox is full"
}

service quota-warning {
  executable = script /usr/local/bin/quota-warning.sh
  user = vmail
  unix_listener quota-warning {
    user = vmail
  }
}
```

How the quota system works:

- **Default quota** is 1 GB per user. This gets overridden by the per-user value from MySQL (set via the `user_query`).
- **Trash gets extra space** — 100 MB on top of the regular quota, so deleting messages doesn't immediately fail.
- **Grace period** — Users can go 10% over quota temporarily. This prevents weird edge cases where receiving a single large email pushes them over and blocks everything.
- **Warnings** fire at 80% and 95% usage. The script sends the user a notification email.

> [!NOTE]
> The `%%` is Dovecot's escape for a literal `%` character in config files. `95%%` means "95 percent," not "95 percent of percent."

### Quota Status Service

Postfix checks quotas before accepting mail through a policy service:

```ini
service quota-status {
  executable = quota-status -p postfix
  inet_listener {
    port = 12340
  }
  client_limit = 1
}
```

This lets Postfix reject mail at the SMTP level when a user's mailbox is full, instead of accepting it and generating a bounce later.

## SSL / TLS

`/etc/dovecot/conf.d/10-ssl.conf`:

```ini
ssl = required
ssl_cert = </etc/letsencrypt/live/mail.yourdomain.com/fullchain.pem
ssl_key = </etc/letsencrypt/live/mail.yourdomain.com/privkey.pem
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes
```

The `<` before the file path is Dovecot syntax for "read the contents of this file." It's not a typo.

`ssl = required` means all connections must use TLS. There's no option for unencrypted IMAP or POP3.

### Protocol Ports

```ini
service imap-login {
  inet_listener imap {
    port = 143
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}

service pop3-login {
  inet_listener pop3 {
    port = 110
  }
  inet_listener pop3s {
    port = 995
    ssl = yes
  }
}
```

- **Port 143** — IMAP with STARTTLS (connection starts plain, upgrades to TLS).
- **Port 993** — IMAPS with implicit TLS (connection starts encrypted).
- **Port 110** — POP3 with STARTTLS.
- **Port 995** — POP3S with implicit TLS.

> [!TIP]
> Most modern email clients prefer ports 993 and 995 (implicit TLS). If you can only expose two ports, pick those.

## LMTP Delivery

Dovecot receives mail from Postfix over LMTP (Local Mail Transfer Protocol):

```ini
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}

protocol lmtp {
  mail_plugins = $mail_plugins sieve quota
}
```

LMTP supports per-recipient delivery status, so Postfix knows exactly which recipients succeeded or failed. The `sieve` plugin allows server-side mail filtering rules.

## Logging

```ini
log_path = /var/log/dovecot/dovecot.log
info_log_path = /var/log/dovecot/dovecot-info.log
auth_verbose = yes
mail_debug = no
```

Set `mail_debug = yes` temporarily when troubleshooting. It's very verbose and not suitable for production.

## Reloading Configuration

After editing Dovecot config files:

```bash
docker exec mailyte-dovecot doveconf -n    # Show non-default settings
docker exec mailyte-dovecot doveadm reload  # Apply changes without restart
```
