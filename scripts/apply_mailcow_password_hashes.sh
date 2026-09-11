#!/bin/bash
# =============================================================================
# Apply mailcow's existing password hashes to already-created mailyte
# mailboxes -- every user's EXISTING mailcow password keeps working after
# migration, unchanged. This never resets anything: it copies the same
# already-hashed value mailcow already had, verbatim.
# =============================================================================
# Why this works: both mailcow and mailyte-email-server hash mailbox
# passwords with bcrypt and tell Dovecot to verify with the same scheme:
#   mailcow: "{BLF-CRYPT}$2y$..."      (data/web/inc/functions.inc.php:hash_password())
#   mailyte: default_pass_scheme = BLF-CRYPT   (mailer/dovecot/config/dovecot-sql.conf.ext)
#            raw bcrypt via worker/api/utils/auth.py:hash_password()
# bcrypt is a portable, standard hash format -- Dovecot verifies a
# {BLF-CRYPT}-prefixed value identically regardless of which language
# produced it. Copying the hash verbatim preserves each user's real
# password without mailyte (or mailcow) ever seeing what it actually is.
#
# This writes DIRECTLY to mailyte-email-server's own mailserver database,
# bypassing its normal mailbox API on purpose: that API only accepts a
# PLAINTEXT password (it hashes + enforces its own strength policy on
# whatever you send it -- see worker/api/routes/mailboxes.py), so it cannot
# accept an already-hashed value without hashing the hash itself, which
# would silently break login entirely.
#
# Run this ON THE MAILYTE-EMAIL-SERVER HOST, from the mailyte-email-server
# repo root, AFTER `php artisan email-accounts:bulk-create` has created
# every mailbox listed here (this overwrites the auto-generated placeholder
# password bulk-create assigned, with each mailbox's real, existing one).
#
# Usage:
#   ./apply_mailcow_password_hashes.sh [mailbox_hashes.csv]
#
# mailbox_hashes.csv: email,password_hash per line, from extract_from_mailcow.sh.
# =============================================================================

set -euo pipefail

HASHES_FILE="${1:-mailbox_hashes.csv}"
ENV_FILE=".env"

[[ -f "$HASHES_FILE" ]] || { echo "Error: $HASHES_FILE not found." >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Error: $ENV_FILE not found -- run this from the mailyte-email-server repo root." >&2; exit 1; }

DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | cut -d= -f2-); DB_NAME="${DB_NAME:-mailserver}"
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | cut -d= -f2-); DB_USER="${DB_USER:-mailuser}"
DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)

[[ -n "$DB_PASSWORD" ]] || { echo "Error: could not read DB_PASSWORD from $ENV_FILE." >&2; exit 1; }

# Escapes a value for safe inclusion inside a single-quoted SQL string
# literal (backslash first, so the quote-escaping backslash itself isn't
# re-escaped).
sql_escape() {
    printf '%s' "$1" | sed "s/\\\\/\\\\\\\\/g; s/'/''/g"
}

total=0
updated=0
not_found=0
failed=0

while IFS=',' read -r email hash || [[ -n "$email" ]]; do
    email="$(echo -n "$email" | tr -d '[:space:]')"
    [[ -z "$email" || "$email" == \#* ]] && continue
    total=$((total + 1))

    if [[ -z "$hash" ]]; then
        echo "  SKIP    $email -- empty hash in $HASHES_FILE"
        failed=$((failed + 1))
        continue
    fi

    query="UPDATE email_accounts SET password = '$(sql_escape "$hash")' WHERE email = '$(sql_escape "$email")'; SELECT ROW_COUNT();"
    # </dev/null: without this, docker compose exec inherits this loop's
    # stdin (fd 0, redirected from $HASHES_FILE below) and consumes the
    # rest of the file on its first iteration, silently ending the loop
    # after one row. grep -v: the mysql CLI's harmless
    # "[Warning] Using a password..." line lands in $result too (2>&1),
    # which breaks the exact "1"/"0" match in the case statement below.
    result=$(docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" -N -e "$query" 2>&1 </dev/null | grep -v '^mysql: \[Warning\]')

    case "$result" in
        1)
            echo "  OK      $email"
            updated=$((updated + 1))
            ;;
        0)
            echo "  NOTFOUND $email -- no mailyte mailbox with this email yet (run email-accounts:bulk-create first)"
            not_found=$((not_found + 1))
            ;;
        *)
            echo "  FAILED  $email -- $result" >&2
            failed=$((failed + 1))
            ;;
    esac
done < "$HASHES_FILE"

echo
echo "=== Password-hash apply summary ==="
echo "Total:     $total"
echo "Updated:   $updated"
echo "Not found: $not_found"
echo "Failed:    $failed"

if [[ "$not_found" -gt 0 || "$failed" -gt 0 ]]; then
    exit 1
fi
echo "Every listed mailbox now authenticates with its original mailcow password."
