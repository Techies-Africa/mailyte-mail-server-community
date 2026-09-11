#!/bin/bash
# =============================================================================
# Maildir -> S3 sync (folder and flag state between backups)
# =============================================================================
# DR-2 gives message CONTENT a near-zero RPO: every accepted message is
# archived to S3 within seconds of delivery. It does not cover the other half
# of a mailbox's state, because Maildir keeps that outside the message --
# which folder a message sits in, and whether it is read, flagged, replied to
# or deleted, all live in the file's PATH and NAME, not in its bytes.
#
# Without this, a restore between nightly backups would return every message
# (from the archive) with up to 24 h of folder moves and read/unread state
# rolled back. Maildir files are immutable once written and the whole tree is
# 3 GB, so a 15-minute incremental sync is nearly free.
#
# aws s3 sync rather than the rclone the PRD suggested: the aws CLI is already
# installed for the backup path and speaks the same credentials, so this adds
# no third binary to keep patched on a mail server. `sync` is the same
# copy-what-changed operation.
#
# Deliberately NOT age-encrypted, unlike everything else in the DR set. These
# files are already ciphertext: Dovecot mail_crypt encrypts every message at
# rest under the global EC keypair, and that key is escrowed separately (and is
# not on this host in any decryptable form). Wrapping ciphertext in a second
# layer would break per-file sync -- age output is not deterministic, so every
# file would look changed on every run and the sync would re-upload 3 GB every
# 15 minutes. What does leak is metadata: filenames carry flags and sizes.
# SSE-S3 and the bucket's blocked public access cover that; if per-file
# metadata privacy ever becomes a requirement, this needs a different design,
# not an extra flag.
#
# Usage:  ./mail-sync.sh [--dry-run]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=lib/dr_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/dr_common.sh"

MAIL_DATA_DIR="${MAIL_DATA_DIR:-${PROJECT_ROOT}/storage/mail_data}"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { printf '[mail-sync] %s\n' "$*"; }

dr_load_config

if ! dr_offsite_configured; then
    log "S3 not configured -- nothing to sync to"
    exit 0
fi
[[ -d "$MAIL_DATA_DIR" ]] || { log "no mail data at ${MAIL_DATA_DIR}"; exit 0; }

HOST_SHORT=$(hostname -s)
DEST=$(dr_s3_uri "mail-state/${HOST_SHORT}/mail_data")

flags=(--only-show-errors)
$DRY_RUN && flags+=(--dryrun)

# --delete mirrors expunges, so a restore does not resurrect mail the user
# deleted. The archive (DR-2) remains the record of anything ever accepted, so
# this deleting is a state mirror, not data loss -- and the bucket has
# versioning plus Object Lock underneath it either way.
flags+=(--delete)

# Dovecot rewrites these constantly and they are rebuildable from the Maildir
# itself; syncing them would mean re-uploading churn every 15 minutes for data
# a restore does not need.
flags+=(--exclude '*/dovecot.index*' --exclude '*/dovecot.list.index*' --exclude '*/.temp.*')

START=$(date +%s)
log "syncing ${MAIL_DATA_DIR} -> ${DEST}"
dr_aws s3 sync "$MAIL_DATA_DIR" "$DEST" "${flags[@]}"
log "done in $(( $(date +%s) - START ))s"
