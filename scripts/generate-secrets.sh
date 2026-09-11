#!/usr/bin/env bash
# Generates a complete .env with strong random secrets (phase-07 C3). This
# is step one of the quickstart for both editions: nothing in
# docker-compose.yml has a security-relevant default any more (C3 removed
# them all -- worker/api/startup_checks.py aborts the stack at startup if
# any of the eight required secrets below is missing, a known-weak value,
# or under 16 characters), so a self-hoster must generate real ones before
# `docker compose up` will do anything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."
ENV_EXAMPLE="$REPO_ROOT/.env.example"
ENV_FILE="$REPO_ROOT/.env"

# Same eight secrets worker/api/startup_checks.py requires -- keep these
# two lists in sync.
REQUIRED_SECRETS=(
  DB_ROOT_PASSWORD
  DB_PASSWORD
  WEBHOOK_SECRET
  OAUTH_TOKEN_SECRET
  URL_HMAC_SECRET
  ADMIN_PASSWORD
  ADMIN_TOKEN_SECRET
  GRAFANA_ADMIN_PASSWORD
)

gen_secret() {
  # Matches scripts/generate_dkim_kek.sh's encoding. base64 can contain
  # '/' and '+' -- doesn't matter here, every consumer is an env var
  # (passed through, never shell-interpolated) or a docker-compose.yml
  # ${VAR} substitution, both of which take the value verbatim.
  openssl rand -base64 32
}

if [ ! -f "$ENV_FILE" ]; then
  if [ ! -f "$ENV_EXAMPLE" ]; then
    echo "FATAL: neither .env nor .env.example exists -- nothing to generate from." >&2
    exit 1
  fi
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example."
fi

updated=()
for name in "${REQUIRED_SECRETS[@]}"; do
  current="$(grep -E "^${name}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  current="${current%\"}"; current="${current#\"}"  # .env.example wraps some values in quotes
  # Anything starting with "changeme"/"your-", shorter than 16 chars, or
  # matching a known-weak literal (startup_checks.py's WEAK set) is
  # treated as still-a-placeholder and gets replaced. A real existing
  # secret (already generated, or provided by the operator) is left
  # alone -- this script is safe to re-run and won't rotate secrets you
  # already set.
  #
  # Keep the literal list in sync with startup_checks.py's WEAK set. It had
  # drifted: "change-me-in-production" (oauth/url_protection's old code
  # fallback -- 23 chars, passes the length check, and starts with
  # "change-", not "changeme", so the prefix check missed it too), "secret"
  # and "admin123" were absent here, so this script left them in place and
  # the operator only found out when secrets-check refused the whole stack.
  current_lc="$(printf '%s' "$current" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$current" ] || [ "${#current}" -lt 16 ] \
     || [[ "$current_lc" == changeme* ]] || [[ "$current_lc" == your-* ]] \
     || [[ "$current_lc" =~ ^(rootpassword|adminpass|tokensecret|admin|webhooksecret|mailpassword|mailpassword123|password|change-me-in-production|secret|admin123)$ ]]; then
    value="$(gen_secret)"
    if grep -qE "^${name}=" "$ENV_FILE"; then
      # BSD sed (macOS) and GNU sed (Linux) disagree on -i syntax; both
      # accept an explicit (possibly empty) backup suffix argument.
      sed -i.bak -E "s|^${name}=.*|${name}=${value}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
      printf '%s=%s\n' "$name" "$value" >> "$ENV_FILE"
    fi
    updated+=("$name")
  fi
done

chmod 600 "$ENV_FILE"

if [ "${#updated[@]}" -eq 0 ]; then
  echo "All required secrets already set in $ENV_FILE -- nothing to do."
else
  echo "Generated strong values for: ${updated[*]}"
fi

# H6 (security-model.md H6): mysql receives its root password as a mounted
# file (MYSQL_ROOT_PASSWORD_FILE, see docker-compose.yml) rather than a
# plain env var -- env vars set on a running container are readable via
# `docker inspect`/`docker exec env` by anything with Docker API access
# (the docker-proxy container, for one); a 0400 file is not. .env's copy
# stays the source of truth -- start.sh and scripts/mailyte-ctl.sh's
# backup/restore/console commands still read it directly, and converting
# those to the same file convention is out of scope here -- this just keeps
# a second copy in sync for mysql's own benefit. Re-run-safe: always
# mirrors whatever DB_ROOT_PASSWORD currently holds in .env, whether just
# generated above or pre-existing.
SECRETS_DIR="$REPO_ROOT/secrets"
mkdir -p "$SECRETS_DIR"
db_root_password="$(grep -E '^DB_ROOT_PASSWORD=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
db_root_password="${db_root_password%\"}"; db_root_password="${db_root_password#\"}"
# Same trap as the KEK: `docker compose up` before this script leaves an empty
# DIRECTORY here, because Docker creates one at any bind-mount source that does
# not exist. touch succeeds on a directory, chmod succeeds, and only the write
# below fails -- with "Is a directory" and no clue why.
if [ -d "$SECRETS_DIR/db_root_password" ]; then
  if [ -n "$(ls -A "$SECRETS_DIR/db_root_password" 2>/dev/null)" ]; then
    echo "FATAL: $SECRETS_DIR/db_root_password is a non-empty directory; expected a file." >&2
    exit 1
  fi
  echo "Note: secrets/db_root_password was an empty directory left by Docker. Removing it."
  rmdir "$SECRETS_DIR/db_root_password"
fi

touch "$SECRETS_DIR/db_root_password"
chmod 600 "$SECRETS_DIR/db_root_password"
printf '%s' "$db_root_password" > "$SECRETS_DIR/db_root_password"
chmod 400 "$SECRETS_DIR/db_root_password"

echo
echo "Next: scripts/generate_dkim_kek.sh (separate -- it's a mounted file, not an env var)."
