# Intrusion Detection (Fail2ban)

Fail2ban jail configuration, filters, and a webhook ban-notification action for the mail server.

**Deployment model: host-side, not a container.** This directory appears in no compose file. `scripts/install.sh` (run as root on the host) installs the fail2ban package and deploys everything here into `/etc/fail2ban/`. Banning happens in the host's iptables, ahead of Docker's published ports -- which is the point; a container could not do that.

## Contents

| Path | Purpose |
|------|---------|
| `config/fail2ban.conf` | `[DEFAULT]` settings plus five jails (below) |
| `filters/postfix-auth.conf` | SASL authentication failures on 25/465/587 |
| `filters/dovecot-auth.conf` | IMAP/POP3 password mismatches |
| `filters/dovecot-brute.conf` | Slower, persistent brute-force attempts |
| `filters/postfix-spam.conf` | Repeated NOQUEUE rejects / non-SMTP commands |
| `filters/rate-limit.conf` | Repeated rate-limit hits |
| `actions/webhook.conf` | Ban/unban webhook action |
| `scripts/fail2ban-webhook.py` | Sends the ban/unban webhook payloads |
| `scripts/fail2ban-manager.py` | Programmatic ban querying/management |
| `scripts/install.sh` | Host installer (root) |

## Jails

| Jail | Log | maxretry | findtime | bantime |
|------|-----|----------|----------|---------|
| `postfix-auth` | mail.log | 3 | 600s | 3600s |
| `dovecot-auth` | dovecot.log | 3 | 600s | 3600s |
| `postfix-spam` | mail.log | 10 | 3600s | 7200s |
| `rate-limit` | mail.log | 5 | 300s | 1800s |
| `dovecot-brute` | dovecot.log | 5 | 1800s | 7200s |

Every jail pairs `iptables-multiport` with the `webhook` action.

## Log paths

The jail config's `logpath` values are the classic `/var/log/mail.log` and `/var/log/dovecot.log`. On this stack, the containers bind-mount their logs to `./logs/mailer/postfix/mail.log` and `./logs/mailer/dovecot/` under the project root -- adjust the jail `logpath`s (or symlink) when installing on a host, or fail2ban tails files nothing writes.

Full documentation: `docs/mailer/intrusion-detection.md`.
