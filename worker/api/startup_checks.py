#!/usr/bin/env python3
"""Fail-closed secrets validation (phase-07 C3, ported from mailyte-email-server).

Five security-relevant values used to silently fall back to weak,
publicly-known defaults (rootpassword, mailpassword, adminpass,
tokensecret, webhooksecret) if `.env` was missing or incomplete -- a fully
functional, internet-facing mail server with `rootpassword` as its database
root password (security-model.md C3). That is most dangerous here, in
Community Edition, where a self-hoster runs `docker compose up` and exposes
the result to the internet with whatever defaults we hand them.

Deliberately stdlib-only (no cryptography/SQLAlchemy/etc.) so this can run
as the very first thing in the compose dependency graph, in a plain
python:3-slim container, before mysql or anything else has started. Also
called from worker/api/app.py's own startup so `api` fails closed even when
run standalone (`docker compose run api ...`) -- but checking only the
subset `api`'s own environment block actually passes through (API_SECRETS
below). Demanding a secret the api container never receives would stop every
CE instance from booting, which is worse than the bug this closes.

CE DIVERGENCE FROM EE -- the secret list. EE's ALL_SECRETS carries eight
names; three of them do not exist anywhere in this repository and are
deliberately absent here:

  * OAUTH_TOKEN_SECRET -- CE ships no `worker/oauth` service (ADR-001 Pro
    column). Zero references repo-wide.
  * URL_HMAC_SECRET    -- likewise zero references repo-wide.
  * GRAFANA_ADMIN_PASSWORD -- CE's docker-compose.yml / .dev.yml / .prod.yml
    define no grafana service; the name survives only as a stale line in
    .env.production.example.

Requiring any of those would abort startup over a value CE has no consumer
for. Verified by grepping the whole repo, not inferred from EE's list.
"""

import os
import sys
from collections.abc import Mapping

# The full stack-wide list -- every secret CE's docker-compose.yml actually
# interpolates, each verified to have a weak shipped default today:
#   DB_ROOT_PASSWORD -> rootpassword     DB_PASSWORD    -> mailpassword
#   WEBHOOK_SECRET   -> webhooksecret    ADMIN_PASSWORD -> adminpass
#   ADMIN_TOKEN_SECRET -> tokensecret
# `python3 worker/api/startup_checks.py` validates this whole list; see the
# note in README/SECURITY about wiring it as a compose `secrets-check`
# gate, which CE does not yet have.
ALL_SECRETS = [
    "DB_ROOT_PASSWORD",
    "DB_PASSWORD",
    "WEBHOOK_SECRET",
    "ADMIN_PASSWORD",
    "ADMIN_TOKEN_SECRET",
]

# What `api` itself actually receives -- the exact intersection of
# ALL_SECRETS with the `api` service's `environment:` block in
# docker-compose.yml, checked line by line rather than copied from EE.
#
# CE DIVERGENCE: EE's API_SECRETS is ["DB_PASSWORD"] alone, because its
# phase-06 removed ADMIN_PASSWORD/ADMIN_TOKEN_SECRET from `api`'s
# environment when it deleted require_admin(). CE still ships both to this
# container (with the weak `adminpass`/`tokensecret` defaults), so they
# belong in CE's own self-check: they are guaranteed present for every
# instance running the shipped compose file, and leaving them out would mean
# the only boot gate CE has silently tolerates two known-weak values.
#
# Not included: WEBHOOK_SECRET / DB_ROOT_PASSWORD are never passed to `api`
# (H6 -- adding them just to satisfy a check would put secrets this process
# never otherwise touches onto its /proc/self/environ for nothing), and
# routes/monitoring.py's X-Admin-Token is a client-supplied pass-through
# header, not an os.getenv() read in this process.
API_SECRETS = ["DB_PASSWORD", "ADMIN_PASSWORD", "ADMIN_TOKEN_SECRET"]

# Every default this codebase has ever shipped for the values above
# (docker-compose.yml) plus generic placeholders operators commonly leave in
# place. Matched case-insensitively.
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

# Placeholder PREFIXES, not exact values -- an example file ships things
# that are the right length and not literals in WEAK above, yet are exactly
# as unset as `changeme` alone.
#
# CE DIVERGENCE: EE's prefix tuple is ("your-", "changeme"), which matches
# EE's own .env.example style. CE's two example files use a different
# placeholder convention entirely -- `CHANGE_ME_db_password`,
# `CHANGE_ME_STRONG_PASSWORD_HERE`, `CHANGE_ME_JWT_SECRET_64CHARS` -- none of
# which start with "changeme" (the underscore breaks it) or "your-". Porting
# EE's tuple verbatim would have passed every single placeholder CE ships,
# i.e. the check would have been decorative. The underscore/hyphen variants
# below are what make it real for this repo; "your-"/"your_" are kept so an
# operator who copied EE-style docs is caught too.
WEAK_PREFIXES = ("your-", "your_", "changeme", "change_me", "change-me")

MIN_LENGTH = 16


def _is_weak(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in WEAK or lowered.startswith(WEAK_PREFIXES)


def _resolve(env: Mapping[str, str], key: str) -> str:
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


def verify_secrets(
    env: Mapping[str, str] | None = None, required: list[str] | None = None
) -> None:
    """Abort the process if any required secret is missing, a known-weak
    value, or too short. Fail closed -- an error here must stop startup,
    never just log a warning and continue (security-model.md §5.1).

    Only ever reports the NAME of an offending variable, never its value:
    this runs before the log redaction filter is installed, and a startup
    banner is the last place a secret should surface (H2)."""
    source = os.environ if env is None else env
    names = ALL_SECRETS if required is None else required
    resolved = {k: _resolve(source, k) for k in names}

    missing = [k for k in names if not resolved[k]]
    if missing:
        sys.exit(f"FATAL: required secrets unset: {', '.join(missing)}")

    weak = [k for k in names if _is_weak(resolved[k])]
    if weak:
        sys.exit(f"FATAL: known-weak default secret in use for: {', '.join(weak)}")

    short = [k for k in names if len(resolved[k]) < MIN_LENGTH]
    if short:
        sys.exit(f"FATAL: secret too short (<{MIN_LENGTH} chars): {', '.join(short)}")


if __name__ == "__main__":
    verify_secrets()
    print("All required secrets present and pass minimum-strength checks.")
