#!/bin/bash
# =============================================================================
# Mailyte Email Server - Automated Backup Script
# =============================================================================
# Covers: MySQL (full + incremental), Redis, mail storage, secrets, DKIM keys,
#         SSL certs, config files
# Destination: local disk + S3, client-side encrypted with age
#
# Usage:
#   ./backup.sh [OPTIONS]
#
# Options:
#   --full            Full backup of all components (default)
#   --incremental     Incremental backup (MySQL binlog, new mail only)
#   --mysql-only      Backup MySQL database only
#   --redis-only      Backup Redis data only
#   --mail-only       Backup mail storage only
#   --secrets-only    Backup secrets/ only (always encrypted)
#   --config-only     Backup configuration files only
#   --pre-deploy      Database + config only, local, no upload. What a
#                     release can damage; used by deployment/deploy.sh.
#   --verify          Verify existing backups
#   --no-upload       Skip S3 upload even if configured
#   --no-encrypt      Skip the age stage (refuses to run with secrets/)
#   --help            Show this help message
#
# Configuration comes from secrets/dr.env (see scripts/lib/dr_common.sh) for
# everything offsite-related, and from the environment for the rest:
#   BACKUP_DIR              <project_root>/storage/backups
#   DB_PASSWORD             (required for MySQL backup)
#   DB_NAME                 mailyte_mail
#   MAIL_DATA_DIR           /var/mail/vhosts
#   BACKUP_RETENTION_FULLS  3     local full backups kept, once offsite works
#   BACKUP_RETENTION_DAYS   30    fallback age-based prune when offsite is down
#
# Ordering matters and is not arbitrary:
#   components -> verify -> manifest -> encrypt -> upload -> prune
# Verification reads inside gzip/tar, which is impossible after encryption;
# pruning happens last and only prunes aggressively once the upload succeeded,
# so a broken offsite path can never cost us the local copies too.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
# Inside the project tree, not /var/backups/mailyte -- keeps the whole
# email server (code, persistent storage, and its own backups) under one
# root, so moving/migrating the server is "move one folder," and it's
# covered by the same Sync Directory persistence as the rest of storage/
# rather than needing its own separately-provisioned system directory.
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/storage/backups}"
MYSQL_HOST="${DB_HOST:-mysql}"
MYSQL_PORT="${DB_PORT:-3306}"
MYSQL_USER="${DB_USER:-mailyte}"
MYSQL_PASSWORD="${DB_PASSWORD:-}"
MYSQL_DATABASE="${DB_NAME:-mailyte_mail}"
# Container name (docker-compose.yml: container_name: mysql), used to exec
# mysqldump/mysqlbinlog inside the container -- see backup_mysql_full.
MYSQL_CONTAINER="${MYSQL_CONTAINER:-${CONTAINER_PREFIX:-}mysql}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
MAIL_DATA_DIR="${MAIL_DATA_DIR:-/var/mail/vhosts}"
DKIM_DIR="${DKIM_DIR:-./storage/dkim_keys}"
SSL_DIR="${SSL_DIR:-./storage/ssl_certs}"
SSL_PRIVATE_DIR="${SSL_PRIVATE_DIR:-./storage/ssl_private}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
RETENTION_FULLS="${BACKUP_RETENTION_FULLS:-3}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

DATE=$(date +%Y%m%d_%H%M%S)
DATE_SHORT=$(date +%Y%m%d)
BACKUP_SUBDIR="${BACKUP_DIR}/${DATE}"
LOG_FILE="${BACKUP_DIR}/logs/backup_${DATE}.log"
BINLOG_POS_FILE="${BACKUP_DIR}/.last_binlog_position"
LAST_MAIL_BACKUP_FILE="${BACKUP_DIR}/.last_mail_backup_time"
HOST_SHORT=$(hostname -s)

# Counters for summary
TOTAL_SIZE=0
BACKUP_START_TIME=$(date +%s)
COMPONENTS_BACKED_UP=()
ERRORS=()
UPLOADED_OBJECTS=()
HISTORY_ROW_ID=""
ENCRYPTED=false

# Flags (defaults)
DO_MYSQL=true
DO_REDIS=true
DO_MAIL=true
DO_CONFIG=true
DO_DKIM=true
DO_SSL=true
DO_SECRETS=true
INCREMENTAL=false
VERIFY_ONLY=false
PRE_DEPLOY_RUN=false
SKIP_UPLOAD=false
SKIP_ENCRYPT=false
# Only a run that covers everything may drive full-backup retention. Any
# --*-only selection or --incremental clears this.
FULL_RUN=true

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"
    shift
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local message="[${timestamp}] [${level}] $*"
    echo -e "$message" | tee -a "$LOG_FILE" 2>/dev/null || echo -e "$message"
}

log_info()    { log "INFO"    "$@"; }
log_warn()    { log "WARN"    "${YELLOW}$*${NC}"; }
log_error()   { log "ERROR"   "${RED}$*${NC}"; ERRORS+=("$*"); }
log_success() { log "SUCCESS" "${GREEN}$*${NC}"; }
log_header()  { log "INFO"    "${BLUE}========== $* ==========${NC}"; }

# ---------------------------------------------------------------------------
# Shared DR primitives (age, S3, backup_history) and the per-component backups
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/dr_common.sh
source "${SCRIPT_DIR}/lib/dr_common.sh"
# shellcheck source=lib/backup_components.sh
source "${SCRIPT_DIR}/lib/backup_components.sh"

# ---------------------------------------------------------------------------
# Cleanup trap
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Backup interrupted or failed with exit code ${exit_code}"
        # A run that dies mid-flight must still close its history row, or the
        # absence alert cannot tell "crashed" from "never started".
        if [[ -n "$HISTORY_ROW_ID" ]]; then
            dr_history_finish "$HISTORY_ROW_ID" failed "$TOTAL_SIZE" "$BACKUP_SUBDIR" "" \
                "interrupted (exit ${exit_code})" "$([[ "$ENCRYPTED" == true ]] && echo 1 || echo 0)" || true
        fi
        if [[ -d "$BACKUP_SUBDIR" && ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]]; then
            log_warn "Removing incomplete backup directory: ${BACKUP_SUBDIR}"
            rm -rf "$BACKUP_SUBDIR"
        fi
    fi
    rm -f /tmp/mailyte_backup_*.tmp 2>/dev/null || true
    exit $exit_code
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
file_size_human() {
    local file="$1"
    if [[ -f "$file" ]]; then
        if command -v numfmt &>/dev/null; then
            stat --format="%s" "$file" 2>/dev/null | numfmt --to=iec 2>/dev/null || \
            du -sh "$file" 2>/dev/null | cut -f1
        else
            du -sh "$file" 2>/dev/null | cut -f1
        fi
    else
        echo "0B"
    fi
}

file_size_bytes() {
    local file="$1"
    if [[ -f "$file" ]]; then
        stat --format="%s" "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

check_command() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command not found: ${cmd}"
        return 1
    fi
}

# Load database credentials from .env when the caller has not exported them.
#
# deployment/deploy.sh does `set -a; source .env` before calling this script;
# a systemd timer does not, and Docker Compose's .env auto-load only covers
# ${VAR} interpolation inside compose files -- it never reaches the shell. The
# failure is silent and expensive: backup.sh logs "DB_PASSWORD is not set.
# Skipping MySQL backup", treats one missing component as non-fatal, and exits
# 0 having produced a 2.8 GB backup with no database in it. Observed exactly
# once, on the first scheduled-style run of this script.
#
# Loaded BEFORE secrets/dr.env so the DR config wins: .env still carries the
# old empty AWS_* placeholders, which would otherwise blank real credentials.
load_project_env() {
    [[ -n "$MYSQL_PASSWORD" ]] && return 0

    local env_file="${PROJECT_ROOT}/.env"
    if [[ ! -f "$env_file" ]]; then
        log_warn "No .env at ${env_file} and DB_PASSWORD is unset"
        return 0
    fi

    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a

    MYSQL_PASSWORD="${DB_PASSWORD:-}"
    MYSQL_USER="${DB_USER:-$MYSQL_USER}"
    MYSQL_DATABASE="${DB_NAME:-$MYSQL_DATABASE}"
    MYSQL_HOST="${DB_HOST:-$MYSQL_HOST}"
    log_info "Loaded database credentials from ${env_file}"
}

elapsed_since() {
    local start_time="$1"
    local now
    now=$(date +%s)
    local elapsed=$((now - start_time))
    printf '%dm%ds' $((elapsed / 60)) $((elapsed % 60))
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --full)
                DO_MYSQL=true; DO_REDIS=true; DO_MAIL=true
                DO_CONFIG=true; DO_DKIM=true; DO_SSL=true; DO_SECRETS=true
                INCREMENTAL=false
                shift
                ;;
            --incremental)
                INCREMENTAL=true
                FULL_RUN=false
                # Secrets change roughly never and are the most sensitive thing
                # here; shipping them hourly is exposure without benefit.
                DO_SECRETS=false
                shift
                ;;
            --mysql-only)
                FULL_RUN=false
                DO_MYSQL=true; DO_REDIS=false; DO_MAIL=false
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false; DO_SECRETS=false
                shift
                ;;
            --redis-only)
                FULL_RUN=false
                DO_MYSQL=false; DO_REDIS=true; DO_MAIL=false
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false; DO_SECRETS=false
                shift
                ;;
            --mail-only)
                FULL_RUN=false
                DO_MYSQL=false; DO_REDIS=false; DO_MAIL=true
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false; DO_SECRETS=false
                shift
                ;;
            --secrets-only)
                FULL_RUN=false
                DO_MYSQL=false; DO_REDIS=false; DO_MAIL=false
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false; DO_SECRETS=true
                shift
                ;;
            --config-only)
                FULL_RUN=false
                DO_MYSQL=false; DO_REDIS=false; DO_MAIL=false
                DO_CONFIG=true; DO_DKIM=true; DO_SSL=true; DO_SECRETS=false
                shift
                ;;
            --pre-deploy)
                # What a RELEASE can actually damage, and nothing else.
                #
                # A deploy runs migrations and replaces config/, so the
                # database and the configuration are genuinely at risk and are
                # both captured. It does not rewrite Maildir -- dovecot simply
                # restarts and the mail is untouched -- so mail storage is
                # excluded, and mail storage is the overwhelming majority of
                # the bytes and the minutes. Redis is cache and sessions,
                # rebuildable by definition. secrets/ is synced by the deploy
                # tooling rather than written by the release.
                #
                # Local only, no S3. This copy exists to be restored minutes
                # later by the person watching the deploy, off the same disk
                # they are already logged into. Blocking every release on an
                # offsite round trip buys nothing for that scenario -- offsite
                # durability is the scheduled mailyte-backup-full.timer's job,
                # and that still runs --full and still uploads.
                FULL_RUN=false
                DO_MYSQL=true
                DO_CONFIG=true; DO_DKIM=true; DO_SSL=true
                DO_MAIL=false; DO_REDIS=false; DO_SECRETS=false
                INCREMENTAL=false
                SKIP_UPLOAD=true
                PRE_DEPLOY_RUN=true
                shift
                ;;
            --verify)      VERIFY_ONLY=true; shift ;;
            --no-upload)   SKIP_UPLOAD=true; shift ;;
            --no-encrypt)  SKIP_ENCRYPT=true; shift ;;
            --help)
                head -41 "$0" | tail -n +2 | sed 's/^# \?//'
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    # secrets/ in a plaintext tar would put the mail_crypt private key, the KEK
    # and every DKIM key into an unencrypted archive -- the one outcome that is
    # strictly worse than having no secrets backup at all.
    if [[ "$SKIP_ENCRYPT" == true && "$DO_SECRETS" == true ]]; then
        log_warn "--no-encrypt given: dropping the secrets component rather than writing key material in plaintext"
        DO_SECRETS=false
    fi
}

# ---------------------------------------------------------------------------
# Initialize backup directories
# ---------------------------------------------------------------------------
init_dirs() {
    log_header "Initializing backup directories"

    local dirs=(
        "${BACKUP_DIR}"
        "${BACKUP_DIR}/logs"
        "${BACKUP_SUBDIR}"
        "${BACKUP_SUBDIR}/mysql"
        "${BACKUP_SUBDIR}/redis"
        "${BACKUP_SUBDIR}/mail"
        "${BACKUP_SUBDIR}/dkim"
        "${BACKUP_SUBDIR}/ssl"
        "${BACKUP_SUBDIR}/config"
    )
    for dir in "${dirs[@]}"; do
        mkdir -p "$dir"
    done

    touch "$LOG_FILE"
    log_info "Backup directory: ${BACKUP_SUBDIR}"
    log_info "Log file: ${LOG_FILE}"
}

# ---------------------------------------------------------------------------
# Encryption stage
# ---------------------------------------------------------------------------
# Everything the components wrote in plaintext becomes <name>.age here. Runs
# after verification (which needs to read inside the archives) and before
# upload (so nothing unencrypted ever leaves the host).
encrypt_backup() {
    if [[ "$SKIP_ENCRYPT" == true ]]; then
        log_warn "Encryption skipped (--no-encrypt). This backup contains plaintext DKIM keys and .env."
        return 0
    fi
    if ! dr_encryption_configured; then
        log_error "DR_AGE_RECIPIENT unset or age missing -- backup stays plaintext. Set it in secrets/dr.env."
        return 1
    fi

    log_header "Encrypting Backup"
    local start_ts
    start_ts=$(date +%s)

    if dr_encrypt_dir "$BACKUP_SUBDIR"; then
        ENCRYPTED=true
        log_success "All artefacts encrypted to ${DR_AGE_RECIPIENT}"
        log_info "Duration: $(elapsed_since "$start_ts")"
        COMPONENTS_BACKED_UP+=("encrypted")
    else
        log_error "One or more artefacts failed to encrypt"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Upload to S3
# ---------------------------------------------------------------------------
upload_to_s3() {
    if [[ "$SKIP_UPLOAD" == true ]]; then
        log_info "S3 upload skipped (--no-upload)"
        return 0
    fi
    if ! dr_offsite_configured; then
        log_error "S3_BUCKET unset or aws CLI missing -- this backup never leaves the disk it protects"
        return 1
    fi

    log_header "Uploading to S3"
    local start_ts
    start_ts=$(date +%s)

    # Host is in the key path because two machines now back up into one bucket
    # and "which server produced this" must be answerable without opening it.
    local key_prefix="${S3_PREFIX}/mail/${HOST_SHORT}/${DATE}"
    log_info "Uploading to $(dr_s3_uri "${key_prefix}/")"

    if dr_s3_upload "$BACKUP_SUBDIR" "${key_prefix}/"; then
        UPLOADED_OBJECTS+=("$(dr_s3_uri "${key_prefix}/")")
        log_success "S3 upload completed"
        log_info "Duration: $(elapsed_since "$start_ts")"
        # The log is uploaded last and separately: it records the upload itself,
        # so it is only complete once the upload is.
        dr_s3_upload "$LOG_FILE" "${key_prefix}/backup_${DATE}.log" >/dev/null 2>&1 || true
        COMPONENTS_BACKED_UP+=("s3-upload")
    else
        log_error "S3 upload failed"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Cleanup old local backups
# ---------------------------------------------------------------------------
# Two modes on purpose. When THIS run is a full backup that reached S3, local
# copies are a convenience rather than the durable copy, so we keep only the
# last N *full* ones -- the disk was at 87% with 37 GB of backups when this was
# written. Any other run (incremental, --config-only, or a failed upload) falls
# back to the age-based prune, because then the local copies may be the only
# copies there are.
#
# "Full" is decided by reading each directory's MANIFEST.json, not by counting
# directories. Counting directories is what the first version of this did, and
# a single --config-only run promptly pruned 47 directories including every
# full backup on the disk: three tiny config archives satisfied "keep the last
# three". Retention has to reason about what a backup contains.
is_full_backup_dir() {
    local manifest="${1}/MANIFEST.json"
    [[ -f "$manifest" ]] || return 1
    grep -q '"type": *"full"' "$manifest" || return 1
    # A full backup without the mail archive protects nothing that matters.
    grep -q '"mail-storage"' "$manifest"
}

cleanup_local_backups() {
    log_header "Cleaning Up Old Backups"

    local count=0

    if [[ "$FULL_RUN" == true && ${#UPLOADED_OBJECTS[@]} -gt 0 ]]; then
        log_info "Full backup confirmed offsite -- keeping the last ${RETENTION_FULLS} full backups locally"
        local kept=0
        while IFS= read -r dir; do
            if is_full_backup_dir "$dir"; then
                kept=$((kept + 1))
                if [[ $kept -gt $RETENTION_FULLS ]]; then
                    log_info "Removing old full backup: $(basename "$dir")"
                    rm -rf "$dir"
                    count=$((count + 1))
                fi
            elif [[ -n "$(find "$dir" -maxdepth 0 -mtime +"$RETENTION_DAYS" 2>/dev/null)" ]]; then
                # Partial/incremental directories age out on the day rule; they
                # are small and never stand in for a full backup.
                log_info "Removing old partial backup: $(basename "$dir")"
                rm -rf "$dir"
                count=$((count + 1))
            fi
        done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -name '[0-9]*_[0-9]*' | sort -r)
    else
        local why="this run is not a full backup"
        [[ "$FULL_RUN" == true ]] && why="nothing reached S3 this run"
        log_warn "Conservative prune (${why}) -- only removing backups older than ${RETENTION_DAYS} days"
        while IFS= read -r -d '' old_dir; do
            local dir_name
            dir_name=$(basename "$old_dir")
            if [[ "$dir_name" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
                log_info "Removing old backup: ${old_dir}"
                rm -rf "$old_dir"
                count=$((count + 1))
            fi
        done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -print0 2>/dev/null)
    fi

    find "${BACKUP_DIR}/logs" -name "backup_*.log" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
    log_info "Removed ${count} old backup(s)"
}

# ---------------------------------------------------------------------------
# Generate backup manifest
# ---------------------------------------------------------------------------
# Written before encryption so it stays readable in S3 without the identity
# key: during a disaster you need to know what a prefix contains before you
# can decide whether to fetch and decrypt it.
generate_manifest() {
    local manifest_file="${BACKUP_SUBDIR}/MANIFEST.json"

    log_info "Generating backup manifest"

    local end_time duration error_count status
    end_time=$(date +%s)
    duration=$((end_time - BACKUP_START_TIME))
    error_count=${#ERRORS[@]}

    status="success"
    [[ $error_count -gt 0 ]] && status="partial"
    [[ ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]] && status="failed"

    cat > "$manifest_file" <<MANIFEST_EOF
{
    "backup_id": "${DATE}",
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "type": "$(if [[ "$INCREMENTAL" == true ]]; then echo "incremental"; else echo "full"; fi)",
    "status": "${status}",
    "duration_seconds": ${duration},
    "components": [$(printf '"%s",' "${COMPONENTS_BACKED_UP[@]}" | sed 's/,$//')],
    "errors": [$(printf '"%s",' "${ERRORS[@]}" 2>/dev/null | sed 's/,$//' || echo "")],
    "total_size_bytes": ${TOTAL_SIZE},
    "retention_days": ${RETENTION_DAYS},
    "retention_fulls": ${RETENTION_FULLS},
    "encryption": {
        "enabled": $(if [[ "$SKIP_ENCRYPT" == true ]]; then echo false; else echo true; fi),
        "scheme": "age-x25519",
        "recipient": "${DR_AGE_RECIPIENT:-none}"
    },
    "mysql": {
        "host": "${MYSQL_HOST}",
        "database": "${MYSQL_DATABASE}"
    },
    "redis": {
        "host": "${REDIS_HOST}",
        "port": ${REDIS_PORT}
    },
    "s3": {
        "bucket": "${S3_BUCKET:-none}",
        "prefix": "${S3_PREFIX}/mail/${HOST_SHORT}/${DATE}",
        "endpoint": "${S3_ENDPOINT_URL:-aws}"
    },
    "hostname": "$(hostname)",
    "backup_dir": "${BACKUP_SUBDIR}"
}
MANIFEST_EOF

    log_info "Manifest written: ${manifest_file}"
}

# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------
print_summary() {
    local end_time duration
    end_time=$(date +%s)
    duration=$((end_time - BACKUP_START_TIME))

    echo ""
    log_header "Backup Summary"
    echo ""
    log_info "Backup ID:      ${DATE}"
    log_info "Type:           $(if [[ "$INCREMENTAL" == true ]]; then echo "Incremental"; else echo "Full"; fi)"
    log_info "Directory:      ${BACKUP_SUBDIR}"
    log_info "Encrypted:      ${ENCRYPTED}"
    log_info "Offsite:        $(if [[ ${#UPLOADED_OBJECTS[@]} -gt 0 ]]; then echo "${UPLOADED_OBJECTS[0]}"; else echo "NONE"; fi)"
    log_info "Total duration: $(printf '%dm%ds' $((duration / 60)) $((duration % 60)))"
    log_info "Total size:     $(echo "$TOTAL_SIZE" | numfmt --to=iec 2>/dev/null || echo "${TOTAL_SIZE} bytes")"
    echo ""

    if [[ ${#COMPONENTS_BACKED_UP[@]} -gt 0 ]]; then
        log_info "Components backed up:"
        for comp in "${COMPONENTS_BACKED_UP[@]}"; do
            echo "    - ${comp}"
        done
    fi

    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        echo ""
        log_error "Errors encountered (${#ERRORS[@]}):"
        for err in "${ERRORS[@]}"; do
            echo "    - ${err}"
        done
    else
        echo ""
        log_success "Backup completed successfully with no errors"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    load_project_env
    dr_load_config

    if [[ "$VERIFY_ONLY" == true ]]; then
        init_dirs
        local latest_backup
        latest_backup=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "[0-9]*_[0-9]*" | sort -r | head -1)
        if [[ -n "$latest_backup" ]]; then
            verify_backups "$latest_backup"
        else
            log_error "No backups found to verify in ${BACKUP_DIR}"
            exit 1
        fi
        exit 0
    fi

    init_dirs

    log_header "Mailyte Email Server Backup"
    log_info "Date: $(date)"
    log_info "Mode: $(if [[ "$INCREMENTAL" == true ]]; then echo "Incremental"; else echo "Full"; fi)"
    log_info "Host: $(hostname)"
    log_info "Offsite: $(if dr_offsite_configured; then echo "s3://${S3_BUCKET}"; else echo "NOT CONFIGURED"; fi)"
    log_info "Encryption: $(if dr_encryption_configured; then echo "age -> ${DR_AGE_RECIPIENT}"; else echo "NOT CONFIGURED"; fi)"
    echo ""

    HISTORY_ROW_ID=$(dr_history_start \
        "$(if [[ "$INCREMENTAL" == true ]]; then echo incremental; else echo full; fi)" \
        "$(if [[ "$DO_MYSQL" == true && "$DO_MAIL" == true ]]; then echo all; elif [[ "$DO_MYSQL" == true ]]; then echo database; elif [[ "$DO_MAIL" == true ]]; then echo mail; else echo config; fi)" \
        "$DATE" || true)
    [[ -n "$HISTORY_ROW_ID" ]] && log_info "backup_history row: ${HISTORY_ROW_ID}"

    if [[ "$DO_MYSQL" == true ]]; then
        if [[ "$INCREMENTAL" == true ]]; then
            backup_mysql_incremental || true
        else
            backup_mysql_full || true
        fi
    fi
    [[ "$DO_REDIS"   == true ]] && { backup_redis   || true; }
    [[ "$DO_MAIL"    == true ]] && { backup_mail    || true; }
    [[ "$DO_DKIM"    == true ]] && { backup_dkim    || true; }
    [[ "$DO_SSL"     == true ]] && { backup_ssl     || true; }
    [[ "$DO_CONFIG"  == true ]] && { backup_config  || true; }
    [[ "$DO_SECRETS" == true ]] && { backup_secrets || true; }

    verify_backups "$BACKUP_SUBDIR" || true
    generate_manifest
    encrypt_backup || true
    upload_to_s3 || true
    cleanup_local_backups
    print_summary

    # Status recorded in the database is what the absence alerts read, so it
    # must reflect whether this backup is actually protecting anything --
    # "components ran" is not the same as "a copy exists somewhere else".
    local final_status=completed
    [[ ${#ERRORS[@]} -gt 0 ]] && final_status=failed
    [[ ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]] && final_status=failed

    local storage_path="$BACKUP_SUBDIR"
    [[ ${#UPLOADED_OBJECTS[@]} -gt 0 ]] && storage_path="${UPLOADED_OBJECTS[0]}"

    local manifest_sha=""
    [[ -f "${BACKUP_SUBDIR}/MANIFEST.json" ]] && manifest_sha=$(dr_sha256 "${BACKUP_SUBDIR}/MANIFEST.json")

    dr_history_finish "$HISTORY_ROW_ID" "$final_status" "$TOTAL_SIZE" "$storage_path" \
        "$manifest_sha" "${ERRORS[0]:-}" "$([[ "$ENCRYPTED" == true ]] && echo 1 || echo 0)" || true
    HISTORY_ROW_ID=""   # closed; the EXIT trap must not reopen it as failed

    if [[ ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]]; then
        exit 1
    fi

    # A pre-deploy backup exists to protect the DATABASE across a release --
    # migrations are the thing a deploy can actually destroy. Without the dump
    # it protects nothing that matters, so it must not report success.
    #
    # This gate is not theoretical: the first run of this mode wrote dkim, ssl
    # and config, failed the mysqldump on a privilege error, and still exited 0
    # because "some components were backed up". The deploy would have carried
    # straight on into migrations with no database backup at all.
    if [[ "${PRE_DEPLOY_RUN:-false}" == true ]]; then
        if ! printf '%s\n' "${COMPONENTS_BACKED_UP[@]}" | grep -q '^mysql-full$'; then
            log_error "Pre-deploy backup has no database dump -- refusing to report success"
            exit 1
        fi
    fi

    # Exit non-zero when a full backup is missing something a restore cannot do
    # without, so systemd marks the unit failed and the alert path notices. A
    # full backup with no database, or one that never left this disk, is not a
    # backup -- and exiting 0 is how the first such run went unnoticed.
    if [[ "$FULL_RUN" == true ]]; then
        local complete=true
        printf '%s\n' "${COMPONENTS_BACKED_UP[@]}" | grep -q '^mysql-full$'  || {
            log_error "Full backup has no database dump"; complete=false; }
        printf '%s\n' "${COMPONENTS_BACKED_UP[@]}" | grep -q '^mail-storage$' || {
            log_error "Full backup has no mail storage"; complete=false; }
        if [[ "$SKIP_UPLOAD" != true && ${#UPLOADED_OBJECTS[@]} -eq 0 ]]; then
            log_error "Full backup never reached S3 -- it protects nothing this disk does not already hold"
            complete=false
        fi
        [[ "$complete" == true ]] || exit 1
    fi
}

main "$@"
