#!/bin/bash
# =============================================================================
# Escrow the secrets bundle (PRD 00-PRD-disaster-recovery.md §4 L0)
# =============================================================================
# The single highest-value action in the DR plan. Every backup this platform
# has ever taken is undecryptable ciphertext without secrets/mail_crypt/ --
# Dovecot's global mail_crypt EC keypair -- and that key exists in exactly one
# place: the disk it is protecting. Same for secrets/encryption_kek, which
# unwraps the DKIM/PGP/S-MIME private keys stored envelope-encrypted in MySQL.
#
# This bundles that key material, age-encrypts it to a recipient whose identity
# lives only in escrow, and ships it somewhere neither production server can
# reach. Bundle contents by role:
#
#   mail  secrets/mail_crypt/, secrets/encryption_kek, secrets/db_root_password,
#         .env, storage/dkim_keys/
#   web   .env, storage/oauth-*.key if present
#
# storage/dkim_keys is in the mail bundle deliberately even though backup.sh
# also captures it: a DKIM key loss means a signing gap and DNS republication
# for every customer domain, and the bundle is the copy that survives when the
# backup set does not.
#
# The plaintext tar is never written to disk -- it streams straight into age --
# because a plaintext window on the disk is precisely the exposure this closes.
#
# Usage:
#   ./escrow-secrets.sh --role mail          # detect from what exists (default)
#   ./escrow-secrets.sh --role web
#   ./escrow-secrets.sh --local-only         # write ciphertext, skip upload
#   ./escrow-secrets.sh --verify             # list what would be bundled
# =============================================================================
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=lib/dr_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/dr_common.sh"

ROLE=""
LOCAL_ONLY=false
VERIFY_ONLY=false
OUT_DIR="${ESCROW_OUT_DIR:-${PROJECT_ROOT}/storage/escrow}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role) ROLE="$2"; shift 2 ;;
        --local-only) LOCAL_ONLY=true; shift ;;
        --verify) VERIFY_ONLY=true; shift ;;
        --help) sed -n '2,33p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

log()  { printf '[escrow] %s\n' "$*"; }
fail() { printf '[escrow] ERROR: %s\n' "$*" >&2; exit 1; }

dr_load_config

# Role detection: the mail server is the one holding mail_crypt.
if [[ -z "$ROLE" ]]; then
    if [[ -d "${DR_SECRETS_DIR}/mail_crypt" ]]; then ROLE=mail; else ROLE=web; fi
fi

# ---------------------------------------------------------------------------
# Collect the bundle members that actually exist on this host
# ---------------------------------------------------------------------------
collect_members() {
    local candidates=()
    case "$ROLE" in
        mail)
            candidates=(
                "${DR_SECRETS_DIR}/mail_crypt"
                "${DR_SECRETS_DIR}/encryption_kek"
                "${DR_SECRETS_DIR}/db_root_password"
                "${PROJECT_ROOT}/.env"
                "${PROJECT_ROOT}/storage/dkim_keys"
            )
            ;;
        web)
            candidates=(
                "${PROJECT_ROOT}/.env"
                "${PROJECT_ROOT}/storage/oauth-private.key"
                "${PROJECT_ROOT}/storage/oauth-public.key"
            )
            ;;
        *) fail "Unknown role: ${ROLE} (expected mail|web)" ;;
    esac

    local found=()
    for c in "${candidates[@]}"; do
        [[ -e "$c" ]] && found+=("$c")
    done
    printf '%s\n' "${found[@]}"
}

mapfile -t MEMBERS < <(collect_members)
[[ ${#MEMBERS[@]} -eq 0 ]] && fail "Nothing to escrow for role=${ROLE} under ${PROJECT_ROOT}"

if [[ "$VERIFY_ONLY" == true ]]; then
    log "role=${ROLE} would bundle:"
    for m in "${MEMBERS[@]}"; do printf '    %s (%s)\n' "$m" "$(du -sh "$m" 2>/dev/null | cut -f1)"; done
    exit 0
fi

dr_encryption_configured || fail \
    "DR_AGE_RECIPIENT is unset or age is missing. The bundle must never be written in plaintext."

# A private key wrapped to a recipient nobody can decrypt is worse than no
# escrow: it looks done. Reject anything that is not a well-formed age
# recipient before producing a file that claims to be an escrow copy.
[[ "$DR_AGE_RECIPIENT" =~ ^age1[a-z0-9]{58}$ ]] || fail \
    "DR_AGE_RECIPIENT does not look like an age public key: ${DR_AGE_RECIPIENT}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
HOST=$(hostname -s)
BUNDLE="${OUT_DIR}/secrets-${ROLE}-${HOST}-${STAMP}.tar.age"

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

log "role=${ROLE} host=${HOST}"
log "members:"
for m in "${MEMBERS[@]}"; do printf '    %s\n' "$m"; done

# Paths are stored relative to PROJECT_ROOT so a restore onto a differently
# laid-out machine does not have to fight absolute paths out of the tar.
REL_MEMBERS=()
for m in "${MEMBERS[@]}"; do REL_MEMBERS+=("${m#"${PROJECT_ROOT}"/}"); done

log "encrypting to ${DR_AGE_RECIPIENT}"
# -h (dereference) is not optional here. In a deployed tree PROJECT_ROOT is
# deployments/<ts>/, and .env, secrets/ and storage/ are all symlinks up to the
# persistent root. Without -h the tar contains three dangling symlinks and no
# key material whatsoever -- an escrow bundle that is empty and looks fine.
tar czhf - -C "$PROJECT_ROOT" "${REL_MEMBERS[@]}" | dr_encrypt_stream > "$BUNDLE"
chmod 600 "$BUNDLE"

[[ -s "$BUNDLE" ]] || fail "Bundle is empty -- encryption produced nothing"

# age files start with the literal "age-encryption.org/v1" header. Cheap, and
# it catches the case where age wrote an error message into the stream.
head -c 21 "$BUNDLE" | grep -q '^age-encryption.org/v1$' || fail \
    "Bundle does not carry an age header -- refusing to call this escrowed"

SHA=$(dr_sha256 "$BUNDLE")
SIZE=$(stat -c%s "$BUNDLE" 2>/dev/null || stat -f%z "$BUNDLE")

# The manifest is deliberately plaintext: it must be readable during a disaster
# by someone who has the ciphertext but has not yet found the identity key. It
# names no secret -- only which files are inside and how to check the bytes.
MANIFEST="${BUNDLE%.tar.age}.manifest.json"
cat > "$MANIFEST" <<JSON
{
  "bundle": "$(basename "$BUNDLE")",
  "role": "${ROLE}",
  "hostname": "$(hostname)",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "age_recipient": "${DR_AGE_RECIPIENT}",
  "sha256": "${SHA}",
  "size_bytes": ${SIZE},
  "members": [$(printf '"%s",' "${REL_MEMBERS[@]}" | sed 's/,$//')],
  "decrypt": "age --decrypt --identity <ESCROWED_IDENTITY> $(basename "$BUNDLE") | tar tzf -"
}
JSON

log "bundle:   ${BUNDLE} ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B"))"
log "sha256:   ${SHA}"
log "manifest: ${MANIFEST}"

if [[ "$LOCAL_ONLY" == true ]]; then
    log "--local-only: skipping upload"
    exit 0
fi

dr_offsite_configured || fail \
    "S3_BUCKET is unset or aws is missing -- a bundle that stays on this host is not escrow"

KEY_PREFIX="escrow/${ROLE}/${HOST}"
log "uploading to $(dr_s3_uri "${KEY_PREFIX}/")"

# Escrow is the one thing that must never be lifecycled into a restore delay,
# so it goes to STANDARD and stays there.
dr_s3_upload "$BUNDLE"   "${KEY_PREFIX}/$(basename "$BUNDLE")"   STANDARD
dr_s3_upload "$MANIFEST" "${KEY_PREFIX}/$(basename "$MANIFEST")" STANDARD

# `latest` pointers so a restore does not have to guess a timestamp while the
# building is on fire.
dr_s3_upload "$BUNDLE"   "${KEY_PREFIX}/latest.tar.age"       STANDARD
dr_s3_upload "$MANIFEST" "${KEY_PREFIX}/latest.manifest.json" STANDARD

log "escrowed: $(dr_s3_uri "${KEY_PREFIX}/$(basename "$BUNDLE")")"
log "OK"
