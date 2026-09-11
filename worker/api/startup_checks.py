#!/usr/bin/env python3
"""Fail-closed secrets validation (phase-07 C3).

Nine security-relevant values used to silently fall back to weak,
publicly-known defaults (rootpassword, adminpass, tokensecret, admin,
webhooksecret, change-me-in-production) if `.env` was missing or
incomplete -- a fully functional, internet-facing mail server with
`rootpassword` as its database root password (security-model.md C3). This
is most dangerous for Community Edition, where a self-hoster runs
`docker compose up` and exposes the result to the internet with whatever
defaults we hand them.

Deliberately stdlib-only (no cryptography/SQLAlchemy/etc.) so this can run
as the very first thing in the compose dependency graph, in a plain
python:3-slim container, before mysql or anything else has started --
see the `secrets-check` service in docker-compose.yml, which validates the
full stack-wide list. Also called from worker/api/app.py's own startup so
`api` still fails closed if it's ever run standalone
(`docker compose run api ...`), bypassing that gate -- but checking only
the subset `api`'s own environment block actually passes through
(DB_PASSWORD). It deliberately does NOT receive
WEBHOOK_SECRET/OAUTH_TOKEN_SECRET/URL_HMAC_SECRET/DB_ROOT_PASSWORD/
GRAFANA_ADMIN_PASSWORD -- it has no use for them, and adding them just to
satisfy a check here would mean putting secrets the api container never
otherwise touches onto its /proc/self/environ for nothing (H6).

Phase-06 correction (ADR-002): ADMIN_PASSWORD/ADMIN_TOKEN_SECRET were
removed from `api`'s own environment block -- utils/auth.py's
require_admin(), their only consumer inside this service, is deleted in
favour of real, individually-revocable operator identities
(platform_operators/operator_sessions). Both vars are still required
stack-wide (ALL_SECRETS below, unchanged) because worker/monitoring/app.py
and worker/jmap/app.py each depend on them for their own separate,
pre-existing mechanisms that phase-06 does not touch. monitoring.py's
restart/auto-heal/test-webhooks routes still forward a client-supplied
X-Admin-Token header straight through to the monitoring service -- that's
a pass-through header, not read via os.getenv() in this process, so it
needs no entry here.
"""

import os
import sys

# The full stack-wide list -- what `secrets-check` validates before
# anything else starts. Keep in sync with scripts/generate-secrets.sh's
# REQUIRED_SECRETS.
ALL_SECRETS = [
    "DB_ROOT_PASSWORD",
    "DB_PASSWORD",
    "WEBHOOK_SECRET",
    "OAUTH_TOKEN_SECRET",
    "URL_HMAC_SECRET",
    "ADMIN_PASSWORD",
    "ADMIN_TOKEN_SECRET",
    "GRAFANA_ADMIN_PASSWORD",
]

# What `api` itself actually receives (see its docker-compose.yml
# environment: block) -- the subset its own self-check validates.
API_SECRETS = ["DB_PASSWORD"]

# Every default this codebase has ever shipped for the values above
# (docker-compose.yml git history) plus generic placeholders operators
# commonly leave in place. Matched case-insensitively.
WEAK = {
    "rootpassword",
    "adminpass",
    "tokensecret",
    "admin",
    "webhooksecret",
    "change-me-in-production",
    "mailpassword",
    "mailpassword123",
    "password",
    "changeme",
    "secret",
    "admin123",
}

# Placeholder PREFIXES, not exact values -- .env.example ships things like
# `your-webhook-secret-key` and `changeme_root_db_password`, which the exact
# WEAK set above doesn't match (right length, not a literal in the set) but
# are exactly as unset as `changeme` alone. Caught live: the running stack
# had been serving with WEBHOOK_SECRET=your-webhook-secret-key for its
# entire uptime -- 23 characters, not in WEAK, so verify_secrets() passed it
# -- despite scripts/generate-secrets.sh's bash-side `your-*`/`changeme*`
# prefix check existing for exactly this reason. The two checks had drifted;
# this brings the Python gate back in line with the bash one so `secrets-check`
# (and worker/api/app.py's own startup call) actually catch what
# generate-secrets.sh already knows to replace.
WEAK_PREFIXES = ("your-", "changeme")

MIN_LENGTH = 16


def _is_weak(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in WEAK or lowered.startswith(WEAK_PREFIXES)


def _resolve(env: dict, key: str) -> str:
    """Return a secret's effective value, honoring the `<KEY>_FILE`
    convention (H6: some secrets are delivered as a mounted file --
    e.g. DB_ROOT_PASSWORD_FILE for mysql's own root password -- rather
    than a plain env var). Falls back to the plain env var when no
    `_FILE` variant is set, so this stays a no-op for every secret that
    hasn't been converted yet."""
    file_path = env.get(f"{key}_FILE")
    if file_path:
        try:
            with open(file_path) as f:
                return f.read().strip()
        except OSError as exc:
            sys.exit(f"FATAL: cannot read {key}_FILE at {file_path}: {exc}")
    return env.get(key, "")


def verify_secrets(env: dict = None, required: list = None) -> None:
    """Abort the process if any required secret is missing, a known-weak
    value, or too short. Fail closed -- an error here must stop startup,
    never just log a warning and continue (security-model.md SS5.1)."""
    env = os.environ if env is None else env
    required = ALL_SECRETS if required is None else required
    resolved = {k: _resolve(env, k) for k in required}

    missing = [k for k in required if not resolved[k]]
    if missing:
        sys.exit(f"FATAL: required secrets unset: {', '.join(missing)}")

    weak = [k for k in required if _is_weak(resolved[k])]
    if weak:
        sys.exit(f"FATAL: known-weak default secret in use for: {', '.join(weak)}")

    short = [k for k in required if len(resolved[k]) < MIN_LENGTH]
    if short:
        sys.exit(f"FATAL: secret too short (<{MIN_LENGTH} chars): {', '.join(short)}")


if __name__ == "__main__":
    verify_secrets()
    print("All required secrets present and pass minimum-strength checks.")
