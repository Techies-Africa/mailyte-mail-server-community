#!/bin/bash
# =============================================================================
# Extract domains + mailboxes + password hashes from a mailcow-dockerized instance
# =============================================================================
# Run this ON THE MAILCOW HOST, from its install directory (wherever
# docker-compose.yml + mailcow.conf live -- NOT on the mailyte VPS).
#
# Reads mailcow's own MySQL database directly (table/column names confirmed
# against mailcow-dockerized's schema in data/web/inc/init_db.inc.php):
#   domain table:  pk `domain` (the domain name), `active` (0/1)
#   mailbox table: pk `username` (the full email address), `active` (0/1),
#                  `password` (already-hashed, e.g. "{BLF-CRYPT}$2y$...")
#
# mailbox.password is extracted as-is -- it is already a one-way bcrypt hash
# (confirmed via data/web/inc/functions.inc.php:hash_password()), never the
# real password. Nothing here is a plaintext credential, and nothing is
# reset: apply_mailcow_password_hashes.sh writes this same hash into
# mailyte, so each mailbox's EXISTING password keeps working unchanged.
#
# Produces:
#   domains.txt          -- one domain per line, feeds migrate_maildir_from_mailcow.sh
#   mailboxes.txt         -- one email address per line, feeds
#                            `php artisan email-accounts:bulk-create` in mailyte-api
#   mailbox_hashes.csv    -- email,password_hash per line, feeds
#                            apply_mailcow_password_hashes.sh
#
# Usage:
#   ./extract_from_mailcow.sh
#
# Environment variables:
#   MAILCOW_DIR      mailcow-dockerized install directory   (default: current directory)
# =============================================================================

set -euo pipefail

MAILCOW_DIR="${MAILCOW_DIR:-.}"
CONF="$MAILCOW_DIR/mailcow.conf"

[[ -f "$CONF" ]] || { echo "Error: $CONF not found. Run this from (or set MAILCOW_DIR to) your mailcow-dockerized install directory." >&2; exit 1; }

DBNAME=$(grep -E '^DBNAME=' "$CONF" | cut -d= -f2-)
DBUSER=$(grep -E '^DBUSER=' "$CONF" | cut -d= -f2-)
DBPASS=$(grep -E '^DBPASS=' "$CONF" | cut -d= -f2-)

[[ -n "$DBNAME" && -n "$DBUSER" && -n "$DBPASS" ]] || { echo "Error: could not read DBNAME/DBUSER/DBPASS from $CONF." >&2; exit 1; }

run_sql() {
    docker compose --project-directory "$MAILCOW_DIR" exec -T mysql-mailcow \
        mysql -u"$DBUSER" -p"$DBPASS" "$DBNAME" -N -e "$1"
}

echo "==> Extracting active domains..."
run_sql "SELECT domain FROM domain WHERE active = '1' ORDER BY domain;" > domains.txt
echo "    $(wc -l < domains.txt | tr -d ' ') domain(s) written to domains.txt"

echo "==> Extracting active mailboxes..."
run_sql "SELECT username FROM mailbox WHERE active = '1' ORDER BY username;" > mailboxes.txt
echo "    $(wc -l < mailboxes.txt | tr -d ' ') mailbox(es) written to mailboxes.txt"

echo "==> Extracting password hashes (already one-way hashed, never plaintext)..."
run_sql "SELECT CONCAT(username, ',', password) FROM mailbox WHERE active = '1' ORDER BY username;" > mailbox_hashes.csv
echo "    $(wc -l < mailbox_hashes.csv | tr -d ' ') hash(es) written to mailbox_hashes.csv"

echo
echo "Treat mailbox_hashes.csv as sensitive (it's still an auth credential, just a hashed one) and delete it once applied."
echo
echo "Next steps:"
echo "  1. On mailyte-api:           php artisan email-accounts:bulk-create mailboxes.txt"
echo "  2. On mailyte-email-server:  ./apply_mailcow_password_hashes.sh mailbox_hashes.csv"
echo "  3. On mailyte-email-server:  MAILCOW_SSH=user@mailcow-host ./migrate_maildir_from_mailcow.sh domains.txt mailboxes.txt"
