#!/bin/bash
# =============================================================================
# Mailyte Email Server - Restore Script
# =============================================================================
# Restores from backups created by backup.sh
#
# Usage:
#   ./restore.sh [OPTIONS]
#
# Options:
#   --list                  List all available backups
#   --latest                Restore from the latest backup
#   --date YYYYMMDD_HHMMSS  Restore from a specific backup by ID
#   --from-s3 BACKUP_ID     Download and restore from S3
#   --mysql-only            Restore MySQL database only
#   --redis-only            Restore Redis data only
#   --mail-only             Restore mail storage only
#   --dkim-only             Restore DKIM keys only
#   --ssl-only              Restore SSL certificates only
#   --config-only           Restore configuration files only
#   --dry-run               Show what would be restored without doing it
#   --no-restart             Do not restart services after restore
#   --force                 Skip confirmation prompts
#   --help                  Show this help message
#
# Environment variables (with defaults):
#   BACKUP_DIR              /var/backups/mailyte
#   S3_BUCKET               (empty = no S3)
#   S3_PREFIX               mailyte/backups
#   DB_HOST                 mysql
#   DB_PORT                 3306
#   DB_USER                 mailyte
#   DB_PASSWORD             (required for MySQL restore)
#   DB_NAME                 mailyte_mail
#   REDIS_HOST              redis
#   REDIS_PORT              6379
#   MAIL_DATA_DIR           /var/mail/vhosts
#   COMPOSE_FILE            docker-compose.yml
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
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

DATE=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${BACKUP_DIR}/logs/restore_${DATE}.log"

# Flags
RESTORE_MYSQL=true
RESTORE_REDIS=true
RESTORE_MAIL=true
RESTORE_DKIM=true
RESTORE_SSL=true
RESTORE_CONFIG=true
DRY_RUN=false
NO_RESTART=false
FORCE=false
LIST_MODE=false
USE_LATEST=false
FROM_S3=""
BACKUP_ID=""

# Tracking
COMPONENTS_RESTORED=()
ERRORS=()

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

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
log_dry()     { log "DRY-RUN" "${YELLOW}[WOULD] $*${NC}"; }

# ---------------------------------------------------------------------------
# Cleanup trap
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Restore interrupted or failed with exit code ${exit_code}"
    fi
    rm -f /tmp/mailyte_restore_*.tmp 2>/dev/null || true
    exit $exit_code
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
confirm() {
    local message="$1"
    if [[ "$FORCE" == true ]]; then
        return 0
    fi
    echo -en "${YELLOW}${message} [y/N]: ${NC}"
    read -r answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

file_size_human() {
    local file="$1"
    if [[ -f "$file" ]]; then
        du -sh "$file" 2>/dev/null | cut -f1
    else
        echo "0B"
    fi
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list)
                LIST_MODE=true
                shift
                ;;
            --latest)
                USE_LATEST=true
                shift
                ;;
            --date)
                BACKUP_ID="$2"
                shift 2
                ;;
            --from-s3)
                FROM_S3="$2"
                shift 2
                ;;
            --mysql-only)
                RESTORE_MYSQL=true; RESTORE_REDIS=false; RESTORE_MAIL=false
                RESTORE_DKIM=false; RESTORE_SSL=false; RESTORE_CONFIG=false
                shift
                ;;
            --redis-only)
                RESTORE_MYSQL=false; RESTORE_REDIS=true; RESTORE_MAIL=false
                RESTORE_DKIM=false; RESTORE_SSL=false; RESTORE_CONFIG=false
                shift
                ;;
            --mail-only)
                RESTORE_MYSQL=false; RESTORE_REDIS=false; RESTORE_MAIL=true
                RESTORE_DKIM=false; RESTORE_SSL=false; RESTORE_CONFIG=false
                shift
                ;;
            --dkim-only)
                RESTORE_MYSQL=false; RESTORE_REDIS=false; RESTORE_MAIL=false
                RESTORE_DKIM=true; RESTORE_SSL=false; RESTORE_CONFIG=false
                shift
                ;;
            --ssl-only)
                RESTORE_MYSQL=false; RESTORE_REDIS=false; RESTORE_MAIL=false
                RESTORE_DKIM=false; RESTORE_SSL=true; RESTORE_CONFIG=false
                shift
                ;;
            --config-only)
                RESTORE_MYSQL=false; RESTORE_REDIS=false; RESTORE_MAIL=false
                RESTORE_DKIM=false; RESTORE_SSL=false; RESTORE_CONFIG=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --no-restart)
                NO_RESTART=true
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --help)
                head -38 "$0" | tail -n +2 | sed 's/^# \?//'
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information."
                exit 1
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# List available backups
# ---------------------------------------------------------------------------
list_backups() {
    log_header "Available Backups"
    echo ""

    local found=false

    # List local backups
    echo -e "${BOLD}Local backups (${BACKUP_DIR}):${NC}"
    echo "------------------------------------------------------------------------"
    printf "%-24s %-12s %-10s %-8s %s\n" "BACKUP ID" "TYPE" "STATUS" "SIZE" "COMPONENTS"
    echo "------------------------------------------------------------------------"

    if [[ -d "$BACKUP_DIR" ]]; then
        while IFS= read -r backup_dir; do
            if [[ -z "$backup_dir" ]]; then
                continue
            fi
            found=true
            local bid
            bid=$(basename "$backup_dir")
            local manifest="${backup_dir}/MANIFEST.json"

            local btype="unknown"
            local bstatus="unknown"
            local bsize
            bsize=$(du -sh "$backup_dir" 2>/dev/null | cut -f1)
            local bcomponents=""

            if [[ -f "$manifest" ]]; then
                # Parse manifest JSON (simple grep-based, no jq dependency)
                btype=$(grep -o '"type": *"[^"]*"' "$manifest" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
                bstatus=$(grep -o '"status": *"[^"]*"' "$manifest" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
                bcomponents=$(grep -o '"components": *\[[^]]*\]' "$manifest" 2>/dev/null | \
                    sed 's/"components"://;s/\[//;s/\]//;s/"//g;s/,/, /g;s/^ *//' || echo "")
            else
                # Detect components from directory contents
                local comps=()
                [[ -n "$(ls "$backup_dir/mysql/" 2>/dev/null)" ]] && comps+=("mysql")
                [[ -n "$(ls "$backup_dir/redis/" 2>/dev/null)" ]] && comps+=("redis")
                [[ -n "$(ls "$backup_dir/mail/" 2>/dev/null)" ]] && comps+=("mail")
                [[ -n "$(ls "$backup_dir/dkim/" 2>/dev/null)" ]] && comps+=("dkim")
                [[ -n "$(ls "$backup_dir/ssl/" 2>/dev/null)" ]] && comps+=("ssl")
                [[ -n "$(ls "$backup_dir/config/" 2>/dev/null)" ]] && comps+=("config")
                bcomponents=$(IFS=', '; echo "${comps[*]}")
            fi

            printf "%-24s %-12s %-10s %-8s %s\n" "$bid" "$btype" "$bstatus" "$bsize" "$bcomponents"
        done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -name "[0-9]*_[0-9]*" | sort -r)
    fi

    if [[ "$found" == false ]]; then
        echo "  (no local backups found)"
    fi

    # List S3 backups if configured
    if [[ -n "$S3_BUCKET" ]]; then
        echo ""
        echo -e "${BOLD}S3 backups (s3://${S3_BUCKET}/${S3_PREFIX}/):${NC}"
        echo "------------------------------------------------------------------------"
        if command -v aws &>/dev/null; then
            aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/" 2>/dev/null | \
                awk '{print $NF}' | tr -d '/' | sort -r | \
                while read -r s3_date; do
                    if [[ -n "$s3_date" ]]; then
                        # List sub-entries for this date
                        aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${s3_date}/" 2>/dev/null | \
                            awk '{print $NF}' | tr -d '/' | \
                            while read -r s3_id; do
                                echo "  s3://${S3_BUCKET}/${S3_PREFIX}/${s3_date}/${s3_id}"
                            done
                    fi
                done
        else
            echo "  (AWS CLI not available)"
        fi
    fi

    echo ""
}

# ---------------------------------------------------------------------------
# Resolve backup directory
# ---------------------------------------------------------------------------
resolve_backup_dir() {
    local target_dir=""

    if [[ "$USE_LATEST" == true ]]; then
        target_dir=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "[0-9]*_[0-9]*" | sort -r | head -1)
        if [[ -z "$target_dir" ]]; then
            log_error "No backups found in ${BACKUP_DIR}"
            exit 1
        fi
        BACKUP_ID=$(basename "$target_dir")
    elif [[ -n "$BACKUP_ID" ]]; then
        target_dir="${BACKUP_DIR}/${BACKUP_ID}"
        if [[ ! -d "$target_dir" ]]; then
            # Try partial match (date only)
            target_dir=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "${BACKUP_ID}*" | sort -r | head -1)
            if [[ -z "$target_dir" ]]; then
                log_error "Backup not found: ${BACKUP_ID}"
                echo "Available backups:"
                find "$BACKUP_DIR" -maxdepth 1 -type d -name "[0-9]*_[0-9]*" -printf "  %f\n" 2>/dev/null | sort -r
                exit 1
            fi
            BACKUP_ID=$(basename "$target_dir")
        fi
    elif [[ -n "$FROM_S3" ]]; then
        download_from_s3 "$FROM_S3"
        target_dir="${BACKUP_DIR}/${FROM_S3}"
        BACKUP_ID="$FROM_S3"
    else
        log_error "No backup specified. Use --latest, --date, or --from-s3"
        echo "Use --list to see available backups."
        exit 1
    fi

    echo "$target_dir"
}

# ---------------------------------------------------------------------------
# Download from S3
# ---------------------------------------------------------------------------
download_from_s3() {
    local s3_backup_id="$1"
    log_header "Downloading Backup from S3"

    if [[ -z "$S3_BUCKET" ]]; then
        log_error "S3_BUCKET not configured"
        exit 1
    fi

    if ! command -v aws &>/dev/null; then
        log_error "AWS CLI not found"
        exit 1
    fi

    local local_dir="${BACKUP_DIR}/${s3_backup_id}"
    mkdir -p "$local_dir"

    # Try to find the backup in S3 - it could be under a date prefix
    local s3_path=""
    local date_prefix
    date_prefix=$(echo "$s3_backup_id" | cut -c1-8)

    # Try direct path first
    if aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${s3_backup_id}/" &>/dev/null; then
        s3_path="s3://${S3_BUCKET}/${S3_PREFIX}/${s3_backup_id}"
    elif aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${date_prefix}/${s3_backup_id}/" &>/dev/null; then
        s3_path="s3://${S3_BUCKET}/${S3_PREFIX}/${date_prefix}/${s3_backup_id}"
    else
        log_error "Backup not found in S3: ${s3_backup_id}"
        exit 1
    fi

    log_info "Downloading from: ${s3_path}"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would download ${s3_path} to ${local_dir}"
        return
    fi

    aws s3 cp "$s3_path" "$local_dir" --recursive --only-show-errors
    log_success "Downloaded backup from S3"
}

# ---------------------------------------------------------------------------
# Stop services
# ---------------------------------------------------------------------------
stop_services() {
    log_header "Stopping Services"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would stop Docker Compose services"
        return
    fi

    cd "$PROJECT_ROOT"

    # Determine which services to stop based on what we are restoring
    local services_to_stop=()

    if [[ "$RESTORE_MYSQL" == true ]]; then
        # Stop all services that depend on MySQL
        services_to_stop+=(api webhooks tracking rate_limiter monitoring analytics
                          archiver queue_manager rag storage_usage delivery_optimizer
                          encryption templates url_protection oauth jmap migration
                          autoconfig caldav activesync dashboard postfix dovecot
                          cert_manager mysql)
    fi
    if [[ "$RESTORE_REDIS" == true ]]; then
        services_to_stop+=(redis)
    fi
    if [[ "$RESTORE_MAIL" == true ]]; then
        services_to_stop+=(postfix dovecot)
    fi

    # Deduplicate
    local unique_services
    unique_services=($(echo "${services_to_stop[@]}" | tr ' ' '\n' | sort -u | tr '\n' ' '))

    if [[ ${#unique_services[@]} -gt 0 ]]; then
        log_info "Stopping services: ${unique_services[*]}"
        docker compose -f "$COMPOSE_FILE" stop "${unique_services[@]}" 2>/dev/null || \
            docker-compose -f "$COMPOSE_FILE" stop "${unique_services[@]}" 2>/dev/null || \
            log_warn "Could not stop services via docker compose"
    fi
}

# ---------------------------------------------------------------------------
# Start services
# ---------------------------------------------------------------------------
start_services() {
    if [[ "$NO_RESTART" == true ]]; then
        log_info "Service restart skipped (--no-restart)"
        return
    fi

    log_header "Starting Services"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would start Docker Compose services"
        return
    fi

    cd "$PROJECT_ROOT"

    log_info "Starting all services..."
    docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null || \
        docker-compose -f "$COMPOSE_FILE" up -d 2>/dev/null || \
        log_error "Could not start services via docker compose"

    # Wait for health checks
    log_info "Waiting for services to become healthy..."
    local max_wait=120
    local waited=0

    while [[ $waited -lt $max_wait ]]; do
        local unhealthy
        unhealthy=$(docker compose -f "$COMPOSE_FILE" ps 2>/dev/null | grep -c "unhealthy\|starting" || echo "0")
        if [[ "$unhealthy" -eq 0 ]]; then
            break
        fi
        sleep 5
        waited=$((waited + 5))
        log_info "Waiting for services... (${waited}s / ${max_wait}s, ${unhealthy} not ready)"
    done

    if [[ $waited -ge $max_wait ]]; then
        log_warn "Some services may not be fully healthy after ${max_wait}s"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
    else
        log_success "All services started and healthy"
    fi
}

# ---------------------------------------------------------------------------
# Restore MySQL
# ---------------------------------------------------------------------------
restore_mysql() {
    local backup_path="$1"
    log_header "Restoring MySQL Database"

    if [[ -z "$MYSQL_PASSWORD" ]]; then
        log_error "DB_PASSWORD is not set. Cannot restore MySQL."
        return 1
    fi

    # Find the SQL dump file
    local dump_file=""
    dump_file=$(find "$backup_path/mysql" -name "*_full_*.sql.gz" -o -name "*.sql.gz" 2>/dev/null | sort -r | head -1)

    if [[ -z "$dump_file" ]]; then
        log_warn "No MySQL dump found in ${backup_path}/mysql/"
        return 1
    fi

    log_info "Dump file: ${dump_file} ($(file_size_human "$dump_file"))"

    # Verify dump integrity
    log_info "Verifying dump integrity..."
    if ! gzip -t "$dump_file" 2>/dev/null; then
        log_error "MySQL dump file is corrupted: ${dump_file}"
        return 1
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore MySQL from: ${dump_file}"
        log_dry "Target: ${MYSQL_HOST}:${MYSQL_PORT}/${MYSQL_DATABASE}"
        return 0
    fi

    if ! confirm "Restore MySQL database '${MYSQL_DATABASE}' from $(basename "$dump_file")? This will OVERWRITE existing data."; then
        log_warn "MySQL restore cancelled by user"
        return 0
    fi

    # Restore
    log_info "Restoring MySQL database '${MYSQL_DATABASE}'..."
    if zcat "$dump_file" | mysql \
        --host="$MYSQL_HOST" \
        --port="$MYSQL_PORT" \
        --user="$MYSQL_USER" \
        --password="$MYSQL_PASSWORD" \
        2>/tmp/mailyte_restore_mysql.tmp; then

        log_success "MySQL database restored successfully"
        COMPONENTS_RESTORED+=("mysql")
    else
        local err
        err=$(cat /tmp/mailyte_restore_mysql.tmp 2>/dev/null || echo "unknown error")
        log_error "MySQL restore failed: ${err}"
        return 1
    fi

    # Also apply incremental binlogs if they exist
    local binlog_files
    binlog_files=$(find "$backup_path/mysql/binlogs" -name "incremental_*.sql.gz" 2>/dev/null | sort)
    if [[ -n "$binlog_files" ]]; then
        log_info "Applying incremental binary log backups..."
        while IFS= read -r binlog; do
            log_info "Applying: $(basename "$binlog")"
            if zcat "$binlog" | mysql \
                --host="$MYSQL_HOST" \
                --port="$MYSQL_PORT" \
                --user="$MYSQL_USER" \
                --password="$MYSQL_PASSWORD" \
                2>/dev/null; then
                log_success "Applied: $(basename "$binlog")"
            else
                log_warn "Failed to apply incremental: $(basename "$binlog")"
            fi
        done <<< "$binlog_files"
    fi

    rm -f /tmp/mailyte_restore_mysql.tmp
}

# ---------------------------------------------------------------------------
# Restore Redis
# ---------------------------------------------------------------------------
restore_redis() {
    local backup_path="$1"
    log_header "Restoring Redis"

    local rdb_file=""
    rdb_file=$(find "$backup_path/redis" -name "*.rdb" 2>/dev/null | sort -r | head -1)

    if [[ -z "$rdb_file" ]]; then
        log_warn "No Redis RDB file found in ${backup_path}/redis/"
        return 1
    fi

    log_info "RDB file: ${rdb_file} ($(file_size_human "$rdb_file"))"

    # Verify RDB file
    local magic
    magic=$(head -c 5 "$rdb_file" 2>/dev/null || echo "")
    if [[ "$magic" != "REDIS" ]]; then
        log_error "Invalid Redis RDB file: ${rdb_file}"
        return 1
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore Redis from: ${rdb_file}"
        return 0
    fi

    if ! confirm "Restore Redis from $(basename "$rdb_file")? This will OVERWRITE existing data."; then
        log_warn "Redis restore cancelled by user"
        return 0
    fi

    # Stop Redis, replace RDB, start Redis
    log_info "Stopping Redis..."
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SHUTDOWN NOSAVE 2>/dev/null || \
        docker compose -f "$PROJECT_ROOT/$COMPOSE_FILE" stop redis 2>/dev/null || \
        docker-compose -f "$PROJECT_ROOT/$COMPOSE_FILE" stop redis 2>/dev/null || true

    sleep 2

    # Copy RDB file into Redis container data directory
    log_info "Copying RDB file to Redis container..."
    if docker cp "$rdb_file" redis:/data/dump.rdb 2>/dev/null; then
        log_success "Redis RDB file copied to container"
    else
        # Fallback: try direct file path
        local redis_data_dir="/var/lib/redis"
        if [[ -d "$redis_data_dir" ]]; then
            cp "$rdb_file" "${redis_data_dir}/dump.rdb"
            chown redis:redis "${redis_data_dir}/dump.rdb" 2>/dev/null || true
            log_success "Redis RDB file copied to ${redis_data_dir}"
        else
            log_error "Cannot copy RDB file to Redis data directory"
            return 1
        fi
    fi

    # Start Redis
    log_info "Starting Redis..."
    docker compose -f "$PROJECT_ROOT/$COMPOSE_FILE" start redis 2>/dev/null || \
        docker-compose -f "$PROJECT_ROOT/$COMPOSE_FILE" start redis 2>/dev/null || true

    # Wait for Redis to be ready
    local waited=0
    while [[ $waited -lt 30 ]]; do
        if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" PING 2>/dev/null | grep -q "PONG"; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" PING 2>/dev/null | grep -q "PONG"; then
        local dbsize
        dbsize=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DBSIZE 2>/dev/null || echo "unknown")
        log_success "Redis restored and running (${dbsize})"
        COMPONENTS_RESTORED+=("redis")
    else
        log_error "Redis is not responding after restore"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Restore Mail Storage
# ---------------------------------------------------------------------------
restore_mail() {
    local backup_path="$1"
    log_header "Restoring Mail Storage"

    local tar_file=""
    tar_file=$(find "$backup_path/mail" -name "mail_data_*.tar.gz" 2>/dev/null | sort -r | head -1)

    if [[ -z "$tar_file" ]]; then
        log_warn "No mail storage archive found in ${backup_path}/mail/"
        return 1
    fi

    log_info "Archive: ${tar_file} ($(file_size_human "$tar_file"))"

    # Verify archive
    if ! tar tzf "$tar_file" &>/dev/null; then
        log_error "Mail archive is corrupted: ${tar_file}"
        return 1
    fi

    local file_count
    file_count=$(tar tzf "$tar_file" 2>/dev/null | wc -l | tr -d ' ')
    log_info "Archive contains ${file_count} entries"

    # Determine target directory
    local target_dir="$MAIL_DATA_DIR"
    if [[ ! -d "$target_dir" ]]; then
        target_dir="${PROJECT_ROOT}/storage/mail_data"
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore mail storage from: ${tar_file}"
        log_dry "Target directory: ${target_dir}"
        log_dry "Files: ${file_count}"
        return 0
    fi

    if ! confirm "Restore mail storage to ${target_dir}? This may overwrite existing mailboxes."; then
        log_warn "Mail restore cancelled by user"
        return 0
    fi

    # Create backup of current mail data
    if [[ -d "$target_dir" && "$(ls -A "$target_dir" 2>/dev/null)" ]]; then
        local pre_restore_backup="${BACKUP_DIR}/pre_restore_mail_${DATE}.tar.gz"
        log_info "Backing up current mail data to: ${pre_restore_backup}"
        tar czf "$pre_restore_backup" -C "$(dirname "$target_dir")" "$(basename "$target_dir")" 2>/dev/null || true
    fi

    # Extract
    log_info "Extracting mail storage to: ${target_dir}"
    mkdir -p "$target_dir"
    tar xzf "$tar_file" -C "$(dirname "$target_dir")" 2>/dev/null

    # Fix permissions
    log_info "Fixing mail storage permissions..."
    chown -R 5000:5000 "$target_dir" 2>/dev/null || \
        log_warn "Could not set mail directory ownership (vmail:vmail). You may need to fix this manually."

    log_success "Mail storage restored: ${file_count} entries to ${target_dir}"
    COMPONENTS_RESTORED+=("mail-storage")
}

# ---------------------------------------------------------------------------
# Restore DKIM Keys
# ---------------------------------------------------------------------------
restore_dkim() {
    local backup_path="$1"
    log_header "Restoring DKIM Keys"

    local tar_file=""
    tar_file=$(find "$backup_path/dkim" -name "dkim_keys_*.tar.gz" -o -name "dkim_keys_*.tar.gz.gpg" 2>/dev/null | sort -r | head -1)

    if [[ -z "$tar_file" ]]; then
        log_warn "No DKIM keys archive found in ${backup_path}/dkim/"
        return 1
    fi

    log_info "Archive: ${tar_file} ($(file_size_human "$tar_file"))"

    local target_dir="${PROJECT_ROOT}/storage/dkim_keys"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore DKIM keys from: ${tar_file}"
        log_dry "Target directory: ${target_dir}"
        return 0
    fi

    if ! confirm "Restore DKIM keys to ${target_dir}?"; then
        log_warn "DKIM restore cancelled by user"
        return 0
    fi

    mkdir -p "$target_dir"

    # Handle GPG-encrypted archives
    if [[ "$tar_file" == *.gpg ]]; then
        log_info "Decrypting GPG-encrypted DKIM backup..."
        gpg --decrypt "$tar_file" 2>/dev/null | tar xzf - -C "$(dirname "$target_dir")"
    else
        tar xzf "$tar_file" -C "$(dirname "$target_dir")" 2>/dev/null
    fi

    # Set restrictive permissions on keys
    chmod -R 600 "$target_dir"/*.key 2>/dev/null || true
    chmod 700 "$target_dir" 2>/dev/null || true

    log_success "DKIM keys restored to ${target_dir}"
    COMPONENTS_RESTORED+=("dkim-keys")
}

# ---------------------------------------------------------------------------
# Restore SSL Certificates
# ---------------------------------------------------------------------------
restore_ssl() {
    local backup_path="$1"
    log_header "Restoring SSL Certificates"

    local tar_file=""
    tar_file=$(find "$backup_path/ssl" -name "ssl_certs_*.tar.gz" 2>/dev/null | sort -r | head -1)

    if [[ -z "$tar_file" ]]; then
        log_warn "No SSL archive found in ${backup_path}/ssl/"
        return 1
    fi

    log_info "Archive: ${tar_file} ($(file_size_human "$tar_file"))"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore SSL certificates from: ${tar_file}"
        return 0
    fi

    if ! confirm "Restore SSL certificates? This will overwrite current certificates."; then
        log_warn "SSL restore cancelled by user"
        return 0
    fi

    # Extract - the archive preserves original paths
    log_info "Extracting SSL certificates..."
    tar xzf "$tar_file" -C / 2>/dev/null || \
        tar xzf "$tar_file" -C "$PROJECT_ROOT" 2>/dev/null || \
        log_warn "Could not extract SSL archive to original paths, trying project root"

    # Set proper permissions
    chmod -R 644 "${PROJECT_ROOT}/storage/ssl_certs/"* 2>/dev/null || true
    chmod -R 600 "${PROJECT_ROOT}/storage/ssl_private/"* 2>/dev/null || true

    log_success "SSL certificates restored"
    COMPONENTS_RESTORED+=("ssl-certs")
}

# ---------------------------------------------------------------------------
# Restore Configuration Files
# ---------------------------------------------------------------------------
restore_config() {
    local backup_path="$1"
    log_header "Restoring Configuration Files"

    local tar_file=""
    tar_file=$(find "$backup_path/config" -name "config_*.tar.gz" 2>/dev/null | sort -r | head -1)

    if [[ -z "$tar_file" ]]; then
        log_warn "No config archive found in ${backup_path}/config/"
        return 1
    fi

    log_info "Archive: ${tar_file} ($(file_size_human "$tar_file"))"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would restore configuration from: ${tar_file}"
        echo "  Contents:"
        tar tzf "$tar_file" 2>/dev/null | head -20 | while IFS= read -r f; do
            echo "    ${f}"
        done
        return 0
    fi

    if ! confirm "Restore configuration files? This will overwrite current config."; then
        log_warn "Config restore cancelled by user"
        return 0
    fi

    # Back up current config
    local pre_restore="${BACKUP_DIR}/pre_restore_config_${DATE}.tar.gz"
    log_info "Backing up current configuration to: ${pre_restore}"
    tar czf "$pre_restore" \
        "${PROJECT_ROOT}/docker-compose.yml" \
        "${PROJECT_ROOT}/docker-compose.prod.yml" \
        "${PROJECT_ROOT}/.env" \
        2>/dev/null || true

    # Extract config archive (preserves original paths)
    log_info "Extracting configuration..."
    tar xzf "$tar_file" -C / 2>/dev/null || \
        tar xzf "$tar_file" -C "$PROJECT_ROOT" 2>/dev/null || true

    log_success "Configuration files restored"
    COMPONENTS_RESTORED+=("config")
}

# ---------------------------------------------------------------------------
# Verify restoration
# ---------------------------------------------------------------------------
verify_restore() {
    log_header "Verifying Restoration"
    local all_ok=true

    # Verify MySQL if restored
    if [[ " ${COMPONENTS_RESTORED[*]} " =~ " mysql " ]]; then
        log_info "Verifying MySQL..."
        local table_count
        table_count=$(mysql \
            --host="$MYSQL_HOST" \
            --port="$MYSQL_PORT" \
            --user="$MYSQL_USER" \
            --password="$MYSQL_PASSWORD" \
            --database="$MYSQL_DATABASE" \
            -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${MYSQL_DATABASE}';" \
            2>/dev/null || echo "0")

        if [[ "$table_count" -gt 0 ]]; then
            log_success "MySQL verified: ${table_count} tables in ${MYSQL_DATABASE}"
        else
            log_error "MySQL verification failed: no tables found"
            all_ok=false
        fi
    fi

    # Verify Redis if restored
    if [[ " ${COMPONENTS_RESTORED[*]} " =~ " redis " ]]; then
        log_info "Verifying Redis..."
        if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" PING 2>/dev/null | grep -q "PONG"; then
            local dbsize
            dbsize=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DBSIZE 2>/dev/null || echo "")
            log_success "Redis verified: ${dbsize}"
        else
            log_error "Redis verification failed: not responding"
            all_ok=false
        fi
    fi

    # Verify mail storage if restored
    if [[ " ${COMPONENTS_RESTORED[*]} " =~ " mail-storage " ]]; then
        local target_dir="$MAIL_DATA_DIR"
        if [[ ! -d "$target_dir" ]]; then
            target_dir="${PROJECT_ROOT}/storage/mail_data"
        fi
        if [[ -d "$target_dir" && "$(ls -A "$target_dir" 2>/dev/null)" ]]; then
            local domain_count
            domain_count=$(ls -d "$target_dir"/*/ 2>/dev/null | wc -l | tr -d ' ')
            log_success "Mail storage verified: ${domain_count} domain(s)"
        else
            log_warn "Mail storage directory is empty or missing"
        fi
    fi

    if [[ "$all_ok" == true ]]; then
        log_success "All restored components verified successfully"
    else
        log_warn "Some components could not be verified"
    fi
}

# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    log_header "Restore Summary"
    echo ""
    log_info "Backup ID: ${BACKUP_ID}"
    echo ""

    if [[ ${#COMPONENTS_RESTORED[@]} -gt 0 ]]; then
        log_info "Components restored:"
        for comp in "${COMPONENTS_RESTORED[@]}"; do
            echo -e "    ${GREEN}+${NC} ${comp}"
        done
    else
        log_warn "No components were restored"
    fi

    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        echo ""
        log_error "Errors encountered (${#ERRORS[@]}):"
        for err in "${ERRORS[@]}"; do
            echo -e "    ${RED}-${NC} ${err}"
        done
    fi

    echo ""
    if [[ "$DRY_RUN" == true ]]; then
        log_info "This was a DRY RUN. No changes were made."
    else
        if [[ ${#ERRORS[@]} -eq 0 && ${#COMPONENTS_RESTORED[@]} -gt 0 ]]; then
            log_success "Restore completed successfully"
        elif [[ ${#COMPONENTS_RESTORED[@]} -gt 0 ]]; then
            log_warn "Restore completed with some errors"
        else
            log_error "Restore failed"
        fi
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"

    # Ensure log directory exists
    mkdir -p "${BACKUP_DIR}/logs" 2>/dev/null || true
    touch "$LOG_FILE" 2>/dev/null || true

    # List mode
    if [[ "$LIST_MODE" == true ]]; then
        list_backups
        exit 0
    fi

    # Resolve the backup to restore from
    local backup_path
    backup_path=$(resolve_backup_dir)

    log_header "Mailyte Email Server Restore"
    log_info "Date: $(date)"
    log_info "Backup ID: ${BACKUP_ID}"
    log_info "Backup path: ${backup_path}"
    log_info "Dry run: ${DRY_RUN}"
    echo ""

    # Show manifest if available
    local manifest="${backup_path}/MANIFEST.json"
    if [[ -f "$manifest" ]]; then
        log_info "Backup manifest:"
        cat "$manifest" | while IFS= read -r line; do
            echo "    ${line}"
        done
        echo ""
    fi

    # Confirmation
    if [[ "$DRY_RUN" != true ]]; then
        echo -e "${RED}${BOLD}WARNING: This operation will restore data from backup ${BACKUP_ID}.${NC}"
        echo -e "${RED}Existing data may be overwritten. Make sure you have a current backup.${NC}"
        echo ""
        if ! confirm "Proceed with restore?"; then
            log_info "Restore cancelled by user"
            exit 0
        fi
    fi

    # Stop services before restore (unless dry run)
    if [[ "$DRY_RUN" != true ]]; then
        stop_services
    fi

    # Restore each component
    if [[ "$RESTORE_MYSQL" == true ]]; then
        restore_mysql "$backup_path" || true
    fi

    if [[ "$RESTORE_REDIS" == true ]]; then
        restore_redis "$backup_path" || true
    fi

    if [[ "$RESTORE_MAIL" == true ]]; then
        restore_mail "$backup_path" || true
    fi

    if [[ "$RESTORE_DKIM" == true ]]; then
        restore_dkim "$backup_path" || true
    fi

    if [[ "$RESTORE_SSL" == true ]]; then
        restore_ssl "$backup_path" || true
    fi

    if [[ "$RESTORE_CONFIG" == true ]]; then
        restore_config "$backup_path" || true
    fi

    # Verify restoration
    if [[ "$DRY_RUN" != true && ${#COMPONENTS_RESTORED[@]} -gt 0 ]]; then
        # Start services first so we can verify
        start_services
        sleep 5
        verify_restore
    elif [[ "$DRY_RUN" != true ]]; then
        # Still restart services even if nothing was restored
        start_services
    fi

    # Print summary
    print_summary
}

main "$@"
