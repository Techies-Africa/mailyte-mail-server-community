# Sending IP Flagged — Runbook

**Owner:** platform operations · **Audience:** on-call, at 2am

This is an operational document. It assumes mail is already being rejected or
junked and you need to act, not read theory. Background and the prevention
design live in `plans/07-smtp-send/` (workspace planning repo, outside this
site).

Two distinct situations hide behind "our IP got flagged." Identify which one
you are in before doing anything.

---

## 0. Which situation?

- Bounce logs show `554`/`5.7.1 ... blocked` naming a blocklist
  (Spamhaus / Barracuda / SpamCop), or the DNSBL monitor
  (`monitoring_dnsbl_listed`) alerted → **Situation A (blocklist)**.
- No listing, but Gmail/Yahoo delivery lands in spam or defers with `421`
  reputation language; Google Postmaster Tools shows reputation falling →
  **Situation B (reputation)**.

---

## Situation A — blocklist listing

1. **Scope it.** Which list, and is it the IP or a sending domain?
   Check the IP at `https://mxtoolbox.com/blacklists` and any named domain on
   the Spamhaus DBL. A domain listing points at one customer; an IP listing
   affects every sender on that IP.
2. **Stop the cause before you request delisting.** Find the responsible
   sender in the email logs around the listing time — bounce spikes and
   complaint events cluster hard around one org. Suspend that org's SMTP
   credentials (dashboard, or the org tripwire pause), and hold the marketing
   queue if the marketing IP is the one listed (`postqueue -p` to inspect,
   the queue manager to hold). Delisting while the cause still flows earns a
   re-list with a longer memory.
3. **Delist.**
     - Spamhaus: self-service at `https://check.spamhaus.org` — usually clears
       within hours once the cause has stopped.
     - Barracuda: `https://barracudacentral.org/rbl/removal-request`.
     - SpamCop: ages out automatically ~24h after reports stop.
     - Microsoft (Outlook/Hotmail deferrals): the sender support form; check
       SNDS/JMRP.
4. **Protect transactional mail during the window.**
     - If the **marketing** IP is listed: nothing to do — mailbox and
       transactional mail leave the primary IP and are unaffected. This is the
       entire point of stream separation.
     - If the **primary** IP is listed: execute the break-glass decision
       (relay transactional-only traffic through the pre-agreed third party)
       until delisted.
5. **Afterwards.** The causing org goes through the AUP process
   (pause → review → resume or eject). Record what the tripwire missed and
   tighten it. Fix this runbook if it lied to you.

---

## Situation B — Gmail/Yahoo reputation (nothing to delist)

1. Confirm in Google Postmaster Tools (domain and IP reputation graphs) and
   check the spam-complaint rate against the 0.3% line.
2. Identify and pause the sender(s) driving complaints (same method as A.2).
3. Cut marketing volume on the affected stream to a trickle. Reputation
   rebuilds only through weeks of clean, lower-volume sending — there is no
   form to fill.
4. Keep transactional mail flowing; its engagement is what pulls reputation
   back up.
5. Review ramps: whoever caused this was almost certainly ramped too fast.

---

## Standing prevention checklist (verify quarterly)

- DNSBL monitor alerting on every sending IP (`monitoring_dnsbl_listed`).
- Google Postmaster Tools + Microsoft SNDS/JMRP + Yahoo FBL registrations
  current for all sending IPs and domains.
- Tripwires armed (bounce > 5% / complaints > 0.3% auto-pause).
- The marketing IP stays warm through steady traffic; any newly added IP is
  warmed before it carries real volume.
