---
title: Before You Install
description: What your server and your DNS need to be capable of before Mailyte can send or receive mail — and the two things that silently stop it.
---

# Before You Install

You have a server. Before you install anything, three things decide whether this
works at all, and none of them are software you can fix later:

1. **Can your server send on port 25?** Most providers say no by default.
2. **Does your IP have correct reverse DNS?** Without it, Gmail and Outlook
   distrust you no matter what else you do.
3. **Is your IP's reputation clean?** You inherit whatever the last tenant did.

Sorting these out first takes an afternoon of waiting on support tickets. Finding
out afterwards costs you a weekend of debugging a stack that was never broken.

---

## The server itself

| | Minimum | Comfortable |
|---|---|---|
| RAM | 2 GB | 4 GB |
| vCPU | 2 | 2–4 |
| Disk | 20 GB + mail storage | 40 GB + mail storage |
| OS | Any with Docker Engine 24+ | Ubuntu 22.04 / Debian 12 |

Mail storage is whatever your users will keep, plus room for the database,
indexes and backups. Postfix, Dovecot, Rspamd, MySQL, Redis and a dozen small
services all run on one host, so 2 GB is genuinely the floor rather than a
conservative estimate.

!!! tip "Storage can grow later, the IP cannot"
    Disk is easy to extend on any provider. A sending IP with a bad history is
    not — which is why the rest of this page matters more than the sizing table.

---

## 1. Port 25 outbound

**This is the single most common reason a new install cannot send mail.**

Outbound port 25 is blocked by default on almost every cloud provider, because
that is how they stop their address space being used for spam. Your stack will
start perfectly, accept a message, queue it, and then fail to deliver — with
connection timeouts in the logs and no clue that the network is the cause.

| Provider | Default | How to get it |
|---|---|---|
| AWS EC2 | Blocked | Request removal of email sending limits (also set rDNS in the same form) |
| Google Cloud | **Permanently blocked** | Not available at any tier — use a relay or another provider |
| Azure | Blocked | Only for Enterprise Agreement customers, on request |
| DigitalOcean | Blocked on new accounts | Support ticket, usually after an account has some history |
| Hetzner | Blocked on new accounts | Support ticket, generally granted |
| Vultr / Linode | Blocked | Support ticket |
| OVH / Contabo | Usually open | — |

Check before you install:

```bash
# If this connects, port 25 outbound is open.
# A timeout means it is blocked -- open a ticket with your provider.
timeout 10 bash -c 'cat < /dev/null > /dev/tcp/gmail-smtp-in.l.google.com/25' \
  && echo "port 25 outbound: OPEN" \
  || echo "port 25 outbound: BLOCKED — you cannot deliver mail until this is fixed"
```

!!! danger "Google Cloud cannot run a mail server"
    GCP blocks outbound 25 permanently, with no exception process. You can still
    run Mailyte there to *receive* mail and send through a relay, but direct
    delivery is impossible. If sending matters, choose a different provider
    before you build anything.

### Inbound ports

Inbound matters too, though it is rarely blocked. Your firewall and any provider
firewall need these open:

| Port | Protocol | Why |
|---|---|---|
| 25 | SMTP | Receiving mail from other servers |
| 587 | Submission | Your users sending mail |
| 465 | SMTPS | Your users sending mail (implicit TLS) |
| 993 / 995 | IMAPS / POP3S | Mail clients reading mail |
| 80 / 443 | HTTP/S | Let's Encrypt challenges, webmail, console |

---

## 2. Reverse DNS (PTR)

**The second most common reason mail lands in spam.**

A PTR record maps your IP back to a hostname. Receiving servers check that the
name your server introduces itself with resolves back to the IP it connected
from. When it does not, you look like a compromised machine, because that is
what compromised machines look like.

Set it so that:

```
your IP  →  PTR  →  mail.yourdomain.com
mail.yourdomain.com  →  A  →  your IP
```

Both directions must agree, and the name must be the same one you put in
`HOSTNAME` in `.env`.

PTR is set **at your provider**, not at your DNS host — it is a property of the
IP, and only whoever owns the address block can publish it. Look for "reverse
DNS", "PTR record" or "rDNS" in the server's network settings.

```bash
# Replace with your server's public IP.
dig +short -x 203.0.113.10
# Should print: mail.yourdomain.com.
# Empty, or the provider's default like 203-0-113-10.provider.net, means it is unset.
```

!!! warning "A provider default is not good enough"
    Most servers ship with a generic PTR such as
    `vps-1234.provider.example`. That resolves, so naive checks pass — but it
    does not match your mail hostname, and the major providers treat the
    mismatch almost as harshly as a missing record.

---

## 3. IP reputation

You inherit the address, and its history. Before committing to a server, check
whether it is already listed:

```bash
# Spamhaus ZEN covers the lists that matter most.
# Reverse the octets: 203.0.113.10 -> 10.113.0.203
dig +short 10.113.0.203.zen.spamhaus.org
# No output = not listed, which is what you want.
# A 127.0.0.x answer = listed; ask for a different IP rather than a delisting.
```

Also worth a look: [mxtoolbox.com/blacklists.aspx](https://mxtoolbox.com/blacklists.aspx)
checks around a hundred lists at once.

!!! tip "Ask for a different IP, do not fight for a delisting"
    Delisting a dirty IP takes weeks and often does not hold. Most providers
    will hand you a different address for free if you ask early. This is a
    five-minute check that saves a month.

---

## What you need before starting

A domain you control DNS for, and the ability to add `MX`, `TXT` and `A`
records. You do not need them published yet — [Installation](installation.md)
gets the stack running first, and [Your First Steps](first-steps.md) publishes
the records once the server can tell you its DKIM key.

---

## Pre-flight check

Run this on the server before installing. Everything should say OK.

```bash
#!/usr/bin/env bash
# Replace these two.
MAIL_HOST="mail.yourdomain.com"
MY_IP="$(curl -s https://api.ipify.org)"

echo "Public IP: $MY_IP"

timeout 10 bash -c 'cat < /dev/null > /dev/tcp/gmail-smtp-in.l.google.com/25' 2>/dev/null \
  && echo "OK   port 25 outbound is open" \
  || echo "FAIL port 25 outbound blocked — open a provider ticket first"

PTR="$(dig +short -x "$MY_IP" | sed 's/\.$//')"
[ "$PTR" = "$MAIL_HOST" ] \
  && echo "OK   PTR is $PTR" \
  || echo "FAIL PTR is '${PTR:-unset}', should be $MAIL_HOST"

REVIP="$(echo "$MY_IP" | awk -F. '{print $4"."$3"."$2"."$1}')"
[ -z "$(dig +short "$REVIP.zen.spamhaus.org")" ] \
  && echo "OK   not listed on Spamhaus ZEN" \
  || echo "FAIL listed on Spamhaus — request a different IP"

command -v docker >/dev/null \
  && echo "OK   docker $(docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)" \
  || echo "FAIL docker not installed"
```

Three OKs and Docker present means nothing on this page will surprise you later.

!!! note "You can install before these are sorted"
    The stack runs fine without any of it — you simply cannot deliver mail to
    the outside world. If you are evaluating Mailyte rather than putting real
    mail on it, install now and come back to this page before you go live.

---

## Next

[:octicons-arrow-right-24: Installation](installation.md) — clone, generate secrets, start the stack.
