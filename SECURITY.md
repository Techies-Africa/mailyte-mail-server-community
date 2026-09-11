# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Mailyte Email Server, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, email **security@mailyte.com** with:

1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to provide a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |

## Security Best Practices

When deploying Mailyte, ensure you:

1. **Generate real secrets before first boot**: `scripts/generate-secrets.sh` then
   `scripts/generate_dkim_kek.sh`. `docker compose up` refuses to start with any of the
   eight required secrets unset, a known-weak default, or under 16 characters
   (`worker/api/startup_checks.py`, phase-07 C3) — there is no working insecure default to
   fall back on.
2. **Use TLS** for all connections (enabled by default)
3. **Restrict ports** with your firewall — only expose 25, 465, 587, 993, 995
4. **Keep Docker images updated** with `docker compose pull`
5. **Enable Let's Encrypt** for production certificates
6. **Review API keys** regularly and rotate them
7. **Back up regularly** using `./start.sh` backup option, and store
   `secrets/encryption_kek` in a *separate* backup from your database dumps — together
   they're as good as plaintext DKIM/PGP/S-MIME private keys; either alone is not.

## Accepted risks

Vulnerabilities that could not be fully remediated by a dependency bump, with the reason
and a review date. Re-evaluate at or before the review date — don't let this table go stale.

| Finding | Why it's not fixed | Review by |
|---|---|---|
| `next`, `next-auth`, and their bundled `postcss`/`sharp` (mailyte-web) carry unpatched advisories (`GHSA-ggv3-7p47-pfv8` and others) | Every published `next` release, including the current latest (16.2.12), falls inside the vulnerable range — the fix requires a version still in preview at time of writing. Not a version we chose to skip; no stable fixed version exists yet. | 2026-08-30 — check `npm view next version` again; upgrade the moment a stable release lands outside the advisory range |
| Two Dockerfiles remain on `python:3.9-slim`: `worker/analytics/Dockerfile` and `worker/api/Dockerfile.migrate` | **Python 3.9 reached end-of-life in October 2025.** The base-image migration this row originally called for is now largely done — as of 2026-08-30, 23 of the Python service Dockerfiles are `FROM python:3.11-slim`. The two stragglers still run an unsupported interpreter, and any dependency pins that were held back for 3.9 compatibility can now be lifted for the migrated services. | 2026-09-30 — migrate the last two Dockerfiles to 3.11 (re-verify each build, not a blind `FROM` swap) and sweep for stale 3.9-compat dependency pins |
| `WEBHOOK_SECRET` and `OAUTH_TOKEN_SECRET` are still plain env vars, not mounted files (H6's other two "highest-value" secrets — `DB_ROOT_PASSWORD` and the DKIM KEK are both file-based) | Unlike `DB_ROOT_PASSWORD` (one consumer: `mysql`, with native `_FILE` support), `WEBHOOK_SECRET` is read via `os.getenv('WEBHOOK_SECRET', ...)` directly — not through a shared config layer — in 12+ files across `worker/monitoring`, `worker/webhooks`, `worker/tracking`, `mailer/postfix`, `mailer/cert_manager`, and `docs/`; `OAUTH_TOKEN_SECRET` has a similar spread. Converting either properly means auditing and updating every call site, not just the mount — a bigger, separately-verifiable change than fit in this pass. `worker/api/startup_checks.py`'s `_resolve()` already supports the `<KEY>_FILE` convention generically, so the follow-up is call-site migration, not new infrastructure. | 2026-09-15 — inventory every direct `os.getenv('WEBHOOK_SECRET'` / `os.getenv('OAUTH_TOKEN_SECRET'` call site, route them through one shared reader, then flip the compose mount |

## Scope

This policy covers the Mailyte Email Server Community Edition. For security issues in the Enterprise Edition, contact enterprise-security@mailyte.com.
