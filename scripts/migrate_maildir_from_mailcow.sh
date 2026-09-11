#!/bin/bash
# =============================================================================
# Bulk mail migration from mailcow to mailyte-email-server via direct
# Maildir copy (rsync over SSH) -- no IMAP login, no passwords involved.
# =============================================================================
# Both mailcow and mailyte-email-server store mail as plain Dovecot Maildir
# on disk, in the same <domain>/<local_part>/ layout (confirmed against both
# projects' real dovecot.conf: mailcow's `mail_home = /var/vmail/%d/%n`,
# mailyte's `mail_location = maildir:/var/mail/vhosts/%d/%n`). That means
# mail can be copied file-for-file with no IMAP authentication, and
# therefore no need to know, extract, or reset any mailbox's password --
# this migration never touches mailcow's live credentials.
#
# Run this ON THE MAILYTE-EMAIL-SERVER HOST (the destination), from the
# mailyte-email-server repo root -- it pulls from mailcow over SSH.
#
# Preconditions:
#   1. SSH key access from this host to the mailcow host, for a user that
#      can read mailcow's vmail docker volume (root, or a user in the
#      relevant group).
#   2. Every destination mailbox already created in mailyte via
#      `php artisan email-accounts:bulk-create mailboxes.txt` (mailyte-api)
#      -- this script only copies mail files, it never creates accounts.
#   3. rsync installed on both hosts.
#
# Usage:
#   MAILCOW_SSH=root@old-mailcow-host \
#   ./migrate_maildir_from_mailcow.sh domains.txt mailboxes.txt
#
# domains.txt / mailboxes.txt: from extract_from_mailcow.sh -- one domain
# and one email address per line, respectively. Only domains/mailboxes
# listed are touched; anything else on mailcow is left alone.
# =============================================================================

set -euo pipefail

DOMAINS_FILE="${1:-domains.txt}"
MAILBOXES_FILE="${2:-mailboxes.txt}"
DEST_DIR="./storage/mail_data"

[[ -f "$DOMAINS_FILE" ]] || { echo "Error: $DOMAINS_FILE not found." >&2; exit 1; }
[[ -f "$MAILBOXES_FILE" ]] || { echo "Error: $MAILBOXES_FILE not found." >&2; exit 1; }
[[ -n "${MAILCOW_SSH:-}" ]] || { echo "Error: MAILCOW_SSH env var required, e.g. root@old-mailcow-host." >&2; exit 1; }
[[ -d "$DEST_DIR" ]] || { echo "Error: $DEST_DIR not found -- run this from the mailyte-email-server repo root." >&2; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "Error: rsync is not installed." >&2; exit 1; }

echo "==> Resolving mailcow's vmail volume path on $MAILCOW_SSH..."
VMAIL_PATH=$(ssh "$MAILCOW_SSH" \
    'docker volume inspect --format "{{ .Mountpoint }}" "$(docker volume ls --format "{{.Name}}" | grep -E "vmail-vol-1$")"')

[[ -n "$VMAIL_PATH" ]] || { echo "Error: could not resolve mailcow's vmail volume path over SSH." >&2; exit 1; }
echo "    found: $VMAIL_PATH"

total=0
failed=0

echo
echo "==> Copying mail, one domain at a time..."
while IFS= read -r domain || [[ -n "$domain" ]]; do
    domain="$(echo -n "$domain" | tr -d '[:space:]')"
    [[ -z "$domain" || "$domain" == \#* ]] && continue
    total=$((total + 1))

    echo "  $domain"
    mkdir -p "$DEST_DIR/$domain"

    if rsync -az -e ssh \
        "${MAILCOW_SSH}:${VMAIL_PATH}/${domain}/" \
        "$DEST_DIR/$domain/"; then
        echo "    copied"
    else
        echo "    FAILED"
        failed=$((failed + 1))
    fi
done < "$DOMAINS_FILE"

echo
echo "=== Copy summary ==="
echo "Domains processed: $total"
echo "Failed:             $failed"

echo
echo "==> Fixing ownership so Dovecot (vmail) can read the copied mail..."
docker compose exec -T --user root dovecot chown -R vmail:vmail /var/mail/vhosts

echo
echo "==> Rebuilding Dovecot's index per mailbox..."
while IFS= read -r email || [[ -n "$email" ]]; do
    email="$(echo -n "$email" | tr -d '[:space:]')"
    [[ -z "$email" || "$email" == \#* ]] && continue
    # </dev/null: without this, docker compose exec inherits this loop's
    # stdin (fd 0, redirected from $MAILBOXES_FILE below) and consumes the
    # rest of the file on its first iteration, silently ending the loop
    # after one mailbox (same bug as apply_mailcow_password_hashes.sh).
    docker compose exec -T --user root dovecot doveadm force-resync -u "$email" '*' </dev/null \
        || echo "    warning: force-resync failed for $email (it will still lazily reindex on first login)"
done < "$MAILBOXES_FILE"

if [[ "$failed" -gt 0 ]]; then
    echo
    echo "Some domains failed to copy -- see above. Re-run this script (rsync is safe to re-run/resume) once fixed."
    exit 1
fi

echo
echo "Done. Verify a couple of mailboxes actually show their mail (webmail or a real IMAP client) before considering this complete."
