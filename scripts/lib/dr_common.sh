#!/bin/bash
# =============================================================================
# Shared disaster-recovery primitives
# =============================================================================
# Sourced by backup.sh, escrow-secrets.sh, restore.sh and dr-drill.sh so that
# encryption, S3 transfer and run-recording behave identically everywhere. A
# restore that decrypts differently from the backup that wrote it is the
# failure mode this file exists to make impossible.
#
# Configuration lives in secrets/dr.env, NOT in config/ and NOT in .env:
# deployment/deploy.sh replaces config/ on every deploy while secrets/ and
# storage/ are symlinked through, so anything that must outlive a deploy
# belongs in secrets/. Mode 600, never committed.
#
#   secrets/dr.env
#     S3_BUCKET=mailyte-dr
#     S3_PREFIX=mailyte/backups
#     S3_ENDPOINT_URL=            # empty for real AWS; set for MinIO/R2/B2
#     AWS_ACCESS_KEY_ID=...       # the backup-writer principal (Put, no Delete)
#     AWS_SECRET_ACCESS_KEY=...
#     AWS_DEFAULT_REGION=eu-west-1
#     DR_AGE_RECIPIENT=age1...    # PUBLIC key only. The identity never lands here.
#
# Encryption is age in public-key mode on purpose (PRD D5): a host that is
# fully compromised can write new backups but cannot read any backup, its own
# included. The matching identity exists only in escrow.
# =============================================================================

# Guard against double-sourcing: restore.sh sources this and may also source a
# script that sources it.
[[ -n "${_DR_COMMON_LOADED:-}" ]] && return 0
_DR_COMMON_LOADED=1

DR_PROJECT_ROOT="${DR_PROJECT_ROOT:-${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
DR_SECRETS_DIR="${DR_SECRETS_DIR:-${DR_PROJECT_ROOT}/secrets}"
DR_CONFIG_FILE="${DR_CONFIG_FILE:-${DR_SECRETS_DIR}/dr.env}"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
dr_load_config() {
    if [[ -f "$DR_CONFIG_FILE" ]]; then
        # shellcheck disable=SC1090  # path is deployment-specific by design
        set -a; source "$DR_CONFIG_FILE"; set +a
        DR_CONFIG_LOADED=true
    else
        DR_CONFIG_LOADED=false
    fi

    S3_BUCKET="${S3_BUCKET:-}"
    S3_PREFIX="${S3_PREFIX:-mailyte/backups}"
    S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-}"
    DR_AGE_RECIPIENT="${DR_AGE_RECIPIENT:-}"
    export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-west-1}"
    # Without this the CLI spends ~2 s per invocation probing a metadata
    # endpoint that does not exist on these hosts.
    export AWS_EC2_METADATA_DISABLED=true
}

dr_offsite_configured() {
    [[ -n "${S3_BUCKET:-}" ]] && command -v aws >/dev/null 2>&1
}

dr_encryption_configured() {
    [[ -n "${DR_AGE_RECIPIENT:-}" ]] && command -v age >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
# Streaming, so a 3 GB archive is never written to disk in plaintext and then
# encrypted in place -- the plaintext window is what an attacker with disk
# access is waiting for, and `shred` on a CoW/journalling filesystem does not
# reliably close it.
dr_encrypt_stream() {
    age --recipient "$DR_AGE_RECIPIENT"
}

# Encrypt a file that already exists, replacing it. Used when a producer
# (mysqldump, tar) has already landed its output.
dr_encrypt_file() {
    local plain="$1"
    local enc="${plain}.age"

    age --recipient "$DR_AGE_RECIPIENT" --output "$enc" "$plain" || return 1
    # Only unlink the plaintext once the ciphertext exists and is non-empty --
    # a failed encryption must never be able to destroy the only copy.
    if [[ -s "$enc" ]]; then
        rm -f "$plain"
        printf '%s\n' "$enc"
        return 0
    fi
    rm -f "$enc"
    return 1
}

# Encrypt every plaintext artefact under a directory, in place.
dr_encrypt_dir() {
    local dir="$1" failed=0
    while IFS= read -r -d '' f; do
        dr_encrypt_file "$f" >/dev/null || { failed=$((failed + 1)); }
    done < <(find "$dir" -type f \
                ! -name '*.age' ! -name 'MANIFEST.json' ! -name '*.log' -print0 2>/dev/null)
    return $((failed > 0))
}

dr_decrypt_file() {
    local enc="$1" identity="$2" out="$3"
    age --decrypt --identity "$identity" --output "$out" "$enc"
}

# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------
# Every S3 call goes through here so the endpoint override is applied in
# exactly one place. With S3_ENDPOINT_URL empty this is a plain AWS call.
dr_aws() {
    local args=()
    [[ -n "${S3_ENDPOINT_URL:-}" ]] && args+=(--endpoint-url "$S3_ENDPOINT_URL")
    aws "${args[@]}" "$@"
}

dr_s3_uri() {
    printf 's3://%s/%s' "$S3_BUCKET" "${1#/}"
}

# Upload with a storage class the endpoint will actually accept. MinIO and
# several S3-compatible providers reject STANDARD_IA outright, so a failed
# upload is retried once as STANDARD rather than reported as a backup failure.
dr_s3_upload() {
    local src="$1" key="$2" storage_class="${3:-STANDARD_IA}"
    local dest; dest=$(dr_s3_uri "$key")
    local flags=(--only-show-errors)
    [[ -d "$src" ]] && flags+=(--recursive)

    if dr_aws s3 cp "$src" "$dest" --storage-class "$storage_class" "${flags[@]}" 2>/dev/null; then
        return 0
    fi
    dr_aws s3 cp "$src" "$dest" "${flags[@]}"
}

dr_s3_download() {
    local key="$1" dest="$2"
    local src; src=$(dr_s3_uri "$key")
    local flags=(--only-show-errors)
    [[ "${3:-}" == "--recursive" ]] && flags+=(--recursive)
    dr_aws s3 cp "$src" "$dest" "${flags[@]}"
}

dr_s3_reachable() {
    dr_offsite_configured || return 1
    dr_aws s3 ls "s3://${S3_BUCKET}/" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# backup_history
# ---------------------------------------------------------------------------
# The mail server writes the table directly through the mysql container -- the
# same `docker exec` route backup.sh already uses for mysqldump, so no new
# credential path and no host mysql client needed.
#
# `hostname` distinguishes the mail server's rows from the web server's, which
# arrive through the API instead (backup-web.sh cannot reach this database).
DR_MYSQL_CONTAINER="${DR_MYSQL_CONTAINER:-mysql}"

dr_mysql() {
    local sql="$1"
    local pw="${DB_ROOT_PASSWORD:-}"
    if [[ -z "$pw" && -f "${DR_SECRETS_DIR}/db_root_password" ]]; then
        pw=$(< "${DR_SECRETS_DIR}/db_root_password")
    fi
    [[ -z "$pw" ]] && return 1

    docker exec -i -e MYSQL_PWD="$pw" "$DR_MYSQL_CONTAINER" \
        mysql -u root -N -B -D "${DR_DB_NAME:-mailserver}" -e "$sql" 2>/dev/null
}

# SQL string literal escaping. Backup paths and error messages are attacker-
# adjacent enough (a filename can contain a quote) to be worth doing properly.
dr_sql_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/\\\\/\\\\\\\\/g; s/'/\\\\'/g")"
}

# Opens a 'running' row and echoes its id, so a crashed backup leaves visible
# evidence instead of simply never appearing.
dr_history_start() {
    local backup_type="$1" target="$2" backup_id="$3"
    local id
    id=$(dr_mysql "INSERT INTO backup_history
            (backup_type, target, status, started_at, backup_id, hostname)
         VALUES ($(dr_sql_quote "$backup_type"), $(dr_sql_quote "$target"),
                 'running', NOW(), $(dr_sql_quote "$backup_id"),
                 $(dr_sql_quote "$(hostname)"));
         SELECT LAST_INSERT_ID();" | tail -1)
    [[ "$id" =~ ^[0-9]+$ ]] && printf '%s' "$id"
}

dr_history_finish() {
    local row_id="$1" status="$2" size_bytes="$3" storage_path="$4"
    local checksum="${5:-}" error="${6:-}" encrypted="${7:-0}"
    [[ -z "$row_id" ]] && return 0

    dr_mysql "UPDATE backup_history
              SET status = $(dr_sql_quote "$status"),
                  completed_at = NOW(),
                  size_bytes = ${size_bytes:-0},
                  storage_path = $(dr_sql_quote "$storage_path"),
                  checksum = $(dr_sql_quote "$checksum"),
                  encrypted = ${encrypted},
                  error_message = $(if [[ -n "$error" ]]; then dr_sql_quote "$error"; else echo NULL; fi)
              WHERE id = ${row_id};" >/dev/null
}

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
dr_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

dr_dir_bytes() {
    du -sk "$1" 2>/dev/null | cut -f1 | awk '{print $1 * 1024}'
}
