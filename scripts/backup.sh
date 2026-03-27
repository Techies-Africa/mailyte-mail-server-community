#!/bin/bash
# =============================================================================
# Mailyte Email Server - Automated Backup Script
# =============================================================================
# Supports: MySQL (full + incremental), Redis, mail storage, DKIM keys,
#           SSL certs, config files
# Destination: Local + S3 (configurable)
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
#   --config-only     Backup configuration files only
#   --verify          Verify existing backups
#   --no-upload       Skip S3 upload even if configured
#   --help            Show this help message
#
# Environment variables (with defaults):
#   BACKUP_DIR            /var/backups/mailyte
#   S3_BUCKET             (empty = no S3 upload)
#   S3_PREFIX             mailyte/backups
#   DB_HOST               mysql
#   DB_PORT               3306
#   DB_USER               mailyte
#   DB_PASSWORD           (required for MySQL backup)
#   DB_NAME               mailyte_mail
#   REDIS_HOST            redis
#   REDIS_PORT            6379
#   MAIL_DATA_DIR         /var/mail/vhosts
#   DKIM_DIR              ./storage/dkim_keys
#   SSL_DIR               ./storage/ssl_certs
#   BACKUP_RETENTION_DAYS 30
#   COMPOSE_FILE          docker-compose.yml
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/var/backups/mailyte}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-mailyte/backups}"
MYSQL_HOST="${DB_HOST:-mysql}"
MYSQL_PORT="${DB_PORT:-3306}"
MYSQL_USER="${DB_USER:-mailyte}"
MYSQL_PASSWORD="${DB_PASSWORD:-}"
MYSQL_DATABASE="${DB_NAME:-mailyte_mail}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
MAIL_DATA_DIR="${MAIL_DATA_DIR:-/var/mail/vhosts}"
DKIM_DIR="${DKIM_DIR:-./storage/dkim_keys}"
SSL_DIR="${SSL_DIR:-./storage/ssl_certs}"
SSL_PRIVATE_DIR="${SSL_PRIVATE_DIR:-./storage/ssl_private}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

DATE=$(date +%Y%m%d_%H%M%S)
DATE_SHORT=$(date +%Y%m%d)
BACKUP_SUBDIR="${BACKUP_DIR}/${DATE}"
LOG_FILE="${BACKUP_DIR}/logs/backup_${DATE}.log"
BINLOG_POS_FILE="${BACKUP_DIR}/.last_binlog_position"
LAST_MAIL_BACKUP_FILE="${BACKUP_DIR}/.last_mail_backup_time"

# Counters for summary
TOTAL_SIZE=0
BACKUP_START_TIME=$(date +%s)
COMPONENTS_BACKED_UP=()
ERRORS=()

# Flags (defaults)
DO_MYSQL=true
DO_REDIS=true
DO_MAIL=true
DO_CONFIG=true
DO_DKIM=true
DO_SSL=true
INCREMENTAL=false
VERIFY_ONLY=false
SKIP_UPLOAD=false

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
# Cleanup trap
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Backup interrupted or failed with exit code ${exit_code}"
        # Remove incomplete backup directory
        if [[ -d "$BACKUP_SUBDIR" && ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]]; then
            log_warn "Removing incomplete backup directory: ${BACKUP_SUBDIR}"
            rm -rf "$BACKUP_SUBDIR"
        fi
    fi
    # Remove any temporary files
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
        stat -f%z "$file" 2>/dev/null || stat --format="%s" "$file" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

dir_size_human() {
    local dir="$1"
    if [[ -d "$dir" ]]; then
        du -sh "$dir" 2>/dev/null | cut -f1
    else
        echo "0B"
    fi
}

check_command() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command not found: ${cmd}"
        return 1
    fi
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
                DO_CONFIG=true; DO_DKIM=true; DO_SSL=true
                INCREMENTAL=false
                shift
                ;;
            --incremental)
                INCREMENTAL=true
                shift
                ;;
            --mysql-only)
                DO_MYSQL=true; DO_REDIS=false; DO_MAIL=false
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false
                shift
                ;;
            --redis-only)
                DO_MYSQL=false; DO_REDIS=true; DO_MAIL=false
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false
                shift
                ;;
            --mail-only)
                DO_MYSQL=false; DO_REDIS=false; DO_MAIL=true
                DO_CONFIG=false; DO_DKIM=false; DO_SSL=false
                shift
                ;;
            --config-only)
                DO_MYSQL=false; DO_REDIS=false; DO_MAIL=false
                DO_CONFIG=true; DO_DKIM=true; DO_SSL=true
                shift
                ;;
            --verify)
                VERIFY_ONLY=true
                shift
                ;;
            --no-upload)
                SKIP_UPLOAD=true
                shift
                ;;
            --help)
                head -40 "$0" | tail -n +2 | sed 's/^# \?//'
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
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

    # Make sure log file is writable
    touch "$LOG_FILE"
    log_info "Backup directory: ${BACKUP_SUBDIR}"
    log_info "Log file: ${LOG_FILE}"
}

# ---------------------------------------------------------------------------
# MySQL Full Backup
# ---------------------------------------------------------------------------
backup_mysql_full() {
    log_header "MySQL Full Backup"
    local start_ts
    start_ts=$(date +%s)
    local dump_file="${BACKUP_SUBDIR}/mysql/${MYSQL_DATABASE}_full_${DATE}.sql.gz"

    if [[ -z "$MYSQL_PASSWORD" ]]; then
        log_error "DB_PASSWORD is not set. Skipping MySQL backup."
        return 1
    fi

    log_info "Dumping database '${MYSQL_DATABASE}' from ${MYSQL_HOST}:${MYSQL_PORT}"

    # Build mysqldump command
    local mysqldump_cmd=(
        mysqldump
        --host="$MYSQL_HOST"
        --port="$MYSQL_PORT"
        --user="$MYSQL_USER"
        --password="$MYSQL_PASSWORD"
        --single-transaction
        --routines
        --triggers
        --events
        --set-gtid-purged=OFF
        --flush-logs
        --master-data=2
        --hex-blob
        --complete-insert
        --add-drop-table
        --databases "$MYSQL_DATABASE"
    )

    # Execute dump with compression
    if "${mysqldump_cmd[@]}" 2>>/tmp/mailyte_backup_mysql.tmp | gzip -9 > "$dump_file"; then
        local size
        size=$(file_size_human "$dump_file")
        local bytes
        bytes=$(file_size_bytes "$dump_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))

        # Record binary log position for incremental backups
        if zcat "$dump_file" | head -100 | grep -q "CHANGE MASTER TO"; then
            local binlog_info
            binlog_info=$(zcat "$dump_file" | head -100 | grep "CHANGE MASTER TO" | head -1)
            echo "${DATE}|${binlog_info}" > "$BINLOG_POS_FILE"
            log_info "Binary log position saved for incremental backups"
        fi

        log_success "MySQL full dump completed: ${dump_file} (${size})"
        log_info "Duration: $(elapsed_since "$start_ts")"
        COMPONENTS_BACKED_UP+=("mysql-full")
    else
        local err_output=""
        if [[ -f /tmp/mailyte_backup_mysql.tmp ]]; then
            err_output=$(cat /tmp/mailyte_backup_mysql.tmp)
        fi
        log_error "MySQL dump failed: ${err_output}"
        rm -f "$dump_file"
        return 1
    fi

    rm -f /tmp/mailyte_backup_mysql.tmp
}

# ---------------------------------------------------------------------------
# MySQL Incremental Backup (binary log based)
# ---------------------------------------------------------------------------
backup_mysql_incremental() {
    log_header "MySQL Incremental Backup"
    local start_ts
    start_ts=$(date +%s)

    if [[ -z "$MYSQL_PASSWORD" ]]; then
        log_error "DB_PASSWORD is not set. Skipping MySQL incremental backup."
        return 1
    fi

    if [[ ! -f "$BINLOG_POS_FILE" ]]; then
        log_warn "No previous binary log position found. Falling back to full backup."
        backup_mysql_full
        return $?
    fi

    local last_entry
    last_entry=$(tail -1 "$BINLOG_POS_FILE")
    log_info "Last backup position: ${last_entry}"

    # Get current binary logs
    local binlog_dir="${BACKUP_SUBDIR}/mysql/binlogs"
    mkdir -p "$binlog_dir"

    # Use mysqlbinlog to fetch binary logs since last position
    local binlog_file
    binlog_file=$(echo "$last_entry" | grep -oP "MASTER_LOG_FILE='[^']+'" | cut -d"'" -f2 || echo "")
    local binlog_pos
    binlog_pos=$(echo "$last_entry" | grep -oP "MASTER_LOG_POS=\d+" | cut -d= -f2 || echo "")

    if [[ -n "$binlog_file" && -n "$binlog_pos" ]]; then
        local incr_file="${binlog_dir}/incremental_${DATE}.sql.gz"
        if mysqlbinlog \
            --host="$MYSQL_HOST" \
            --port="$MYSQL_PORT" \
            --user="$MYSQL_USER" \
            --password="$MYSQL_PASSWORD" \
            --start-position="$binlog_pos" \
            --read-from-remote-server \
            "$binlog_file" 2>>/tmp/mailyte_backup_binlog.tmp | gzip -9 > "$incr_file"; then

            local size
            size=$(file_size_human "$incr_file")
            local bytes
            bytes=$(file_size_bytes "$incr_file")
            TOTAL_SIZE=$((TOTAL_SIZE + bytes))

            log_success "MySQL incremental backup completed: ${incr_file} (${size})"
            log_info "Duration: $(elapsed_since "$start_ts")"
            COMPONENTS_BACKED_UP+=("mysql-incremental")
        else
            log_warn "Binary log backup failed, falling back to full dump"
            rm -f "$incr_file" /tmp/mailyte_backup_binlog.tmp
            backup_mysql_full
            return $?
        fi
        rm -f /tmp/mailyte_backup_binlog.tmp
    else
        log_warn "Could not parse binary log position. Falling back to full backup."
        backup_mysql_full
        return $?
    fi
}

# ---------------------------------------------------------------------------
# Redis Backup
# ---------------------------------------------------------------------------
backup_redis() {
    log_header "Redis Backup"
    local start_ts
    start_ts=$(date +%s)
    local rdb_file="${BACKUP_SUBDIR}/redis/dump_${DATE}.rdb"
    local aof_file="${BACKUP_SUBDIR}/redis/appendonly_${DATE}.aof.gz"

    # Trigger a background save first
    log_info "Triggering Redis BGSAVE on ${REDIS_HOST}:${REDIS_PORT}"
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" BGSAVE 2>/dev/null; then
        # Wait for BGSAVE to complete (max 60 seconds)
        local waited=0
        while [[ $waited -lt 60 ]]; do
            local last_save
            last_save=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LASTSAVE 2>/dev/null || echo "")
            local bgsave_in_progress
            bgsave_in_progress=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" INFO persistence 2>/dev/null | grep "rdb_bgsave_in_progress:1" || echo "")
            if [[ -z "$bgsave_in_progress" ]]; then
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done
        log_info "Redis BGSAVE completed after ${waited}s"
    fi

    # Download RDB snapshot
    log_info "Downloading Redis RDB snapshot"
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --rdb "$rdb_file" 2>/dev/null; then
        local size
        size=$(file_size_human "$rdb_file")
        local bytes
        bytes=$(file_size_bytes "$rdb_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        log_success "Redis RDB snapshot saved: ${rdb_file} (${size})"
    else
        log_warn "redis-cli --rdb failed. Trying to copy RDB from Docker volume."
        # Fallback: copy from Docker volume
        if docker cp redis:/data/dump.rdb "$rdb_file" 2>/dev/null; then
            local size
            size=$(file_size_human "$rdb_file")
            local bytes
            bytes=$(file_size_bytes "$rdb_file")
            TOTAL_SIZE=$((TOTAL_SIZE + bytes))
            log_success "Redis RDB snapshot copied from container: ${rdb_file} (${size})"
        else
            log_error "Failed to backup Redis RDB snapshot"
            return 1
        fi
    fi

    # Also backup AOF if available
    if docker cp redis:/data/appendonly.aof /tmp/mailyte_backup_aof.tmp 2>/dev/null; then
        gzip -9 -c /tmp/mailyte_backup_aof.tmp > "$aof_file"
        local aof_size
        aof_size=$(file_size_human "$aof_file")
        log_info "Redis AOF backup saved: ${aof_file} (${aof_size})"
        rm -f /tmp/mailyte_backup_aof.tmp
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("redis")
}

# ---------------------------------------------------------------------------
# Mail Storage Backup
# ---------------------------------------------------------------------------
backup_mail() {
    log_header "Mail Storage Backup"
    local start_ts
    start_ts=$(date +%s)

    if [[ ! -d "$MAIL_DATA_DIR" ]]; then
        log_warn "Mail data directory not found: ${MAIL_DATA_DIR}"
        # Try the project-relative path
        local project_mail_dir="${PROJECT_ROOT}/storage/mail_data"
        if [[ -d "$project_mail_dir" ]]; then
            MAIL_DATA_DIR="$project_mail_dir"
            log_info "Using project-relative mail data: ${MAIL_DATA_DIR}"
        else
            log_error "No mail data directory found. Skipping."
            return 1
        fi
    fi

    local tar_file="${BACKUP_SUBDIR}/mail/mail_data_${DATE}.tar.gz"

    if [[ "$INCREMENTAL" == true && -f "$LAST_MAIL_BACKUP_FILE" ]]; then
        local last_backup_time
        last_backup_time=$(cat "$LAST_MAIL_BACKUP_FILE")
        log_info "Incremental mail backup: files modified since ${last_backup_time}"
        tar_file="${BACKUP_SUBDIR}/mail/mail_data_incremental_${DATE}.tar.gz"

        tar czf "$tar_file" \
            --newer-mtime="$last_backup_time" \
            -C "$(dirname "$MAIL_DATA_DIR")" \
            "$(basename "$MAIL_DATA_DIR")" \
            2>/dev/null || true
    else
        log_info "Full mail storage backup from: ${MAIL_DATA_DIR}"
        tar czf "$tar_file" \
            -C "$(dirname "$MAIL_DATA_DIR")" \
            "$(basename "$MAIL_DATA_DIR")" \
            2>/dev/null || true
    fi

    # Record timestamp for next incremental
    date '+%Y-%m-%d %H:%M:%S' > "$LAST_MAIL_BACKUP_FILE"

    if [[ -f "$tar_file" ]]; then
        local size
        size=$(file_size_human "$tar_file")
        local bytes
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        log_success "Mail storage backup completed: ${tar_file} (${size})"
    else
        log_error "Mail storage backup file was not created"
        return 1
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("mail-storage")
}

# ---------------------------------------------------------------------------
# DKIM Keys Backup
# ---------------------------------------------------------------------------
backup_dkim() {
    log_header "DKIM Keys Backup"
    local start_ts
    start_ts=$(date +%s)

    local dkim_source="$DKIM_DIR"
    if [[ ! -d "$dkim_source" ]]; then
        dkim_source="${PROJECT_ROOT}/storage/dkim_keys"
    fi

    if [[ ! -d "$dkim_source" ]]; then
        log_warn "DKIM keys directory not found. Skipping."
        return 0
    fi

    local tar_file="${BACKUP_SUBDIR}/dkim/dkim_keys_${DATE}.tar.gz"

    # Encrypt DKIM keys backup if GPG is available and a key is configured
    if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]] && command -v gpg &>/dev/null; then
        log_info "Encrypting DKIM keys backup with GPG"
        tar czf - -C "$(dirname "$dkim_source")" "$(basename "$dkim_source")" | \
            gpg --encrypt --recipient "$BACKUP_GPG_RECIPIENT" --output "${tar_file}.gpg"
        tar_file="${tar_file}.gpg"
    else
        tar czf "$tar_file" \
            -C "$(dirname "$dkim_source")" \
            "$(basename "$dkim_source")" \
            2>/dev/null
    fi

    if [[ -f "$tar_file" ]]; then
        local size
        size=$(file_size_human "$tar_file")
        local bytes
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        # Set restrictive permissions on DKIM backup
        chmod 600 "$tar_file"
        log_success "DKIM keys backup completed: ${tar_file} (${size})"
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("dkim-keys")
}

# ---------------------------------------------------------------------------
# SSL Certificates Backup
# ---------------------------------------------------------------------------
backup_ssl() {
    log_header "SSL Certificates Backup"
    local start_ts
    start_ts=$(date +%s)

    local ssl_source="$SSL_DIR"
    if [[ ! -d "$ssl_source" ]]; then
        ssl_source="${PROJECT_ROOT}/storage/ssl_certs"
    fi

    local ssl_private_source="$SSL_PRIVATE_DIR"
    if [[ ! -d "$ssl_private_source" ]]; then
        ssl_private_source="${PROJECT_ROOT}/storage/ssl_private"
    fi

    local tar_file="${BACKUP_SUBDIR}/ssl/ssl_certs_${DATE}.tar.gz"
    local files_to_backup=()

    if [[ -d "$ssl_source" ]]; then
        files_to_backup+=("$ssl_source")
    fi
    if [[ -d "$ssl_private_source" ]]; then
        files_to_backup+=("$ssl_private_source")
    fi

    if [[ ${#files_to_backup[@]} -eq 0 ]]; then
        log_warn "No SSL directories found. Skipping."
        return 0
    fi

    # Create tar with all SSL-related directories
    tar czf "$tar_file" "${files_to_backup[@]}" 2>/dev/null || true

    if [[ -f "$tar_file" ]]; then
        local size
        size=$(file_size_human "$tar_file")
        local bytes
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        # Set restrictive permissions on SSL backup
        chmod 600 "$tar_file"
        log_success "SSL certificates backup completed: ${tar_file} (${size})"
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("ssl-certs")
}

# ---------------------------------------------------------------------------
# Configuration Files Backup
# ---------------------------------------------------------------------------
backup_config() {
    log_header "Configuration Files Backup"
    local start_ts
    start_ts=$(date +%s)
    local tar_file="${BACKUP_SUBDIR}/config/config_${DATE}.tar.gz"

    local config_files=()

    # Collect configuration files that exist
    for f in \
        "${PROJECT_ROOT}/docker-compose.yml" \
        "${PROJECT_ROOT}/docker-compose.prod.yml" \
        "${PROJECT_ROOT}/docker-compose.dev.yml" \
        "${PROJECT_ROOT}/pyproject.toml" \
        "${PROJECT_ROOT}/alembic.ini" \
        "${PROJECT_ROOT}/.env" \
        "${PROJECT_ROOT}/.env.production" \
        "${PROJECT_ROOT}/.env.staging"; do
        if [[ -f "$f" ]]; then
            config_files+=("$f")
        fi
    done

    # Config directories
    for d in \
        "${PROJECT_ROOT}/config" \
        "${PROJECT_ROOT}/deployment" \
        "${PROJECT_ROOT}/database/migrations"; do
        if [[ -d "$d" ]]; then
            config_files+=("$d")
        fi
    done

    if [[ ${#config_files[@]} -eq 0 ]]; then
        log_warn "No configuration files found. Skipping."
        return 0
    fi

    tar czf "$tar_file" "${config_files[@]}" 2>/dev/null || true

    if [[ -f "$tar_file" ]]; then
        local size
        size=$(file_size_human "$tar_file")
        local bytes
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        # Set restrictive permissions since .env files may contain secrets
        chmod 600 "$tar_file"
        log_success "Configuration backup completed: ${tar_file} (${size})"
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("config")
}

# ---------------------------------------------------------------------------
# Upload to S3
# ---------------------------------------------------------------------------
upload_to_s3() {
    if [[ -z "$S3_BUCKET" || "$SKIP_UPLOAD" == true ]]; then
        log_info "S3 upload skipped (bucket not configured or --no-upload specified)"
        return 0
    fi

    log_header "Uploading to S3"
    local start_ts
    start_ts=$(date +%s)

    if ! check_command aws; then
        log_error "AWS CLI not found. Install it to enable S3 uploads."
        return 1
    fi

    local s3_dest="s3://${S3_BUCKET}/${S3_PREFIX}/${DATE_SHORT}/${DATE}"

    log_info "Uploading to: ${s3_dest}"

    if aws s3 cp "$BACKUP_SUBDIR" "$s3_dest" \
        --recursive \
        --storage-class STANDARD_IA \
        --only-show-errors \
        2>&1 | tee -a "$LOG_FILE"; then

        log_success "S3 upload completed"
        log_info "Duration: $(elapsed_since "$start_ts")"

        # Also upload the log file
        aws s3 cp "$LOG_FILE" "${s3_dest}/backup_${DATE}.log" --only-show-errors 2>/dev/null || true

        # Clean up old S3 backups
        cleanup_s3_backups
    else
        log_error "S3 upload failed"
        return 1
    fi

    COMPONENTS_BACKED_UP+=("s3-upload")
}

# ---------------------------------------------------------------------------
# Cleanup old S3 backups
# ---------------------------------------------------------------------------
cleanup_s3_backups() {
    if [[ -z "$S3_BUCKET" ]]; then
        return 0
    fi

    log_info "Cleaning up S3 backups older than ${RETENTION_DAYS} days"

    local cutoff_date
    cutoff_date=$(date -d "-${RETENTION_DAYS} days" +%Y%m%d 2>/dev/null || \
                  date -v-"${RETENTION_DAYS}"d +%Y%m%d 2>/dev/null || echo "")

    if [[ -z "$cutoff_date" ]]; then
        log_warn "Could not calculate cutoff date for S3 cleanup"
        return 0
    fi

    # List S3 prefixes (date-based directories) and remove old ones
    aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/" 2>/dev/null | \
        awk '{print $NF}' | \
        tr -d '/' | \
        while read -r dir_date; do
            if [[ "$dir_date" =~ ^[0-9]{8}$ && "$dir_date" < "$cutoff_date" ]]; then
                log_info "Removing old S3 backup: ${dir_date}"
                aws s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}/${dir_date}" \
                    --recursive --only-show-errors 2>/dev/null || true
            fi
        done
}

# ---------------------------------------------------------------------------
# Cleanup old local backups
# ---------------------------------------------------------------------------
cleanup_local_backups() {
    log_header "Cleaning Up Old Backups"

    local count=0

    # Find and remove backup directories older than RETENTION_DAYS
    if [[ -d "$BACKUP_DIR" ]]; then
        while IFS= read -r -d '' old_dir; do
            local dir_name
            dir_name=$(basename "$old_dir")
            # Only remove directories matching our date pattern
            if [[ "$dir_name" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
                log_info "Removing old backup: ${old_dir}"
                rm -rf "$old_dir"
                count=$((count + 1))
            fi
        done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -print0 2>/dev/null)
    fi

    # Clean up old log files
    find "${BACKUP_DIR}/logs" -name "backup_*.log" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

    log_info "Removed ${count} old backup(s)"
}

# ---------------------------------------------------------------------------
# Verify backups
# ---------------------------------------------------------------------------
verify_backups() {
    log_header "Verifying Backups"

    local verify_dir="${1:-$BACKUP_SUBDIR}"
    local all_ok=true

    if [[ ! -d "$verify_dir" ]]; then
        log_error "Backup directory not found: ${verify_dir}"
        return 1
    fi

    # Verify MySQL dump
    local mysql_dumps
    mysql_dumps=$(find "$verify_dir/mysql" -name "*.sql.gz" 2>/dev/null)
    if [[ -n "$mysql_dumps" ]]; then
        while IFS= read -r dump; do
            log_info "Verifying MySQL dump: $(basename "$dump")"
            local size
            size=$(file_size_bytes "$dump")
            if [[ "$size" -lt 100 ]]; then
                log_error "MySQL dump is suspiciously small (${size} bytes): ${dump}"
                all_ok=false
                continue
            fi

            # Verify gzip integrity
            if ! gzip -t "$dump" 2>/dev/null; then
                log_error "MySQL dump is corrupted (gzip test failed): ${dump}"
                all_ok=false
                continue
            fi

            # Verify SQL header
            local header
            header=$(zcat "$dump" 2>/dev/null | head -5)
            if echo "$header" | grep -q "MySQL dump\|mysqldump\|Server version"; then
                log_success "MySQL dump verified: $(basename "$dump") ($(file_size_human "$dump"))"
            else
                log_warn "MySQL dump header looks unusual: $(basename "$dump")"
            fi
        done <<< "$mysql_dumps"
    fi

    # Verify Redis RDB
    local redis_dumps
    redis_dumps=$(find "$verify_dir/redis" -name "*.rdb" 2>/dev/null)
    if [[ -n "$redis_dumps" ]]; then
        while IFS= read -r rdb; do
            log_info "Verifying Redis RDB: $(basename "$rdb")"
            local size
            size=$(file_size_bytes "$rdb")
            if [[ "$size" -lt 10 ]]; then
                log_error "Redis RDB is suspiciously small (${size} bytes): ${rdb}"
                all_ok=false
                continue
            fi

            # Check RDB magic bytes (REDIS)
            local magic
            magic=$(head -c 5 "$rdb" 2>/dev/null || echo "")
            if [[ "$magic" == "REDIS" ]]; then
                log_success "Redis RDB verified: $(basename "$rdb") ($(file_size_human "$rdb"))"
            else
                log_warn "Redis RDB magic bytes mismatch: $(basename "$rdb")"
            fi
        done <<< "$redis_dumps"
    fi

    # Verify tar.gz archives
    local archives
    archives=$(find "$verify_dir" -name "*.tar.gz" 2>/dev/null)
    if [[ -n "$archives" ]]; then
        while IFS= read -r archive; do
            log_info "Verifying archive: $(basename "$archive")"
            if tar tzf "$archive" &>/dev/null; then
                local file_count
                file_count=$(tar tzf "$archive" 2>/dev/null | wc -l)
                log_success "Archive verified: $(basename "$archive") ($(file_size_human "$archive"), ${file_count} files)"
            else
                log_error "Archive is corrupted: ${archive}"
                all_ok=false
            fi
        done <<< "$archives"
    fi

    if [[ "$all_ok" == true ]]; then
        log_success "All backup files verified successfully"
    else
        log_error "Some backup files failed verification"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Generate backup manifest
# ---------------------------------------------------------------------------
generate_manifest() {
    local manifest_file="${BACKUP_SUBDIR}/MANIFEST.json"

    log_info "Generating backup manifest"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - BACKUP_START_TIME))

    local error_count=${#ERRORS[@]}
    local status="success"
    if [[ $error_count -gt 0 ]]; then
        status="partial"
    fi
    if [[ ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]]; then
        status="failed"
    fi

    # Build JSON manually to avoid jq dependency
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
        "prefix": "${S3_PREFIX}"
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
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - BACKUP_START_TIME))

    echo ""
    log_header "Backup Summary"
    echo ""
    log_info "Backup ID:      ${DATE}"
    log_info "Type:           $(if [[ "$INCREMENTAL" == true ]]; then echo "Incremental"; else echo "Full"; fi)"
    log_info "Directory:      ${BACKUP_SUBDIR}"
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

    # Verify-only mode
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
    echo ""

    # Run selected backup components
    if [[ "$DO_MYSQL" == true ]]; then
        if [[ "$INCREMENTAL" == true ]]; then
            backup_mysql_incremental || true
        else
            backup_mysql_full || true
        fi
    fi

    if [[ "$DO_REDIS" == true ]]; then
        backup_redis || true
    fi

    if [[ "$DO_MAIL" == true ]]; then
        backup_mail || true
    fi

    if [[ "$DO_DKIM" == true ]]; then
        backup_dkim || true
    fi

    if [[ "$DO_SSL" == true ]]; then
        backup_ssl || true
    fi

    if [[ "$DO_CONFIG" == true ]]; then
        backup_config || true
    fi

    # Verify the backup
    verify_backups "$BACKUP_SUBDIR" || true

    # Generate manifest
    generate_manifest

    # Upload to S3
    upload_to_s3 || true

    # Clean up old local backups
    cleanup_local_backups

    # Print summary
    print_summary

    # Exit with error if no components were backed up
    if [[ ${#COMPONENTS_BACKED_UP[@]} -eq 0 ]]; then
        exit 1
    fi
}

main "$@"
