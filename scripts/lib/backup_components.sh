#!/bin/bash
# =============================================================================
# Backup components — one function per thing worth protecting
# =============================================================================
# Split out of backup.sh, which had grown past the 600-line hard limit in
# plans/00-foundation/05-engineering-standards.md §2.4 while the DR work added
# a secrets component, an encryption stage and run recording to it. backup.sh
# keeps orchestration; this file keeps the per-component knowledge.
#
# These functions deliberately keep using backup.sh's globals (BACKUP_SUBDIR,
# DATE, INCREMENTAL, TOTAL_SIZE, COMPONENTS_BACKED_UP, ERRORS) rather than
# taking arguments. Sourcing shares scope in bash, so the split changes file
# layout and nothing else — which is the point when the thing being refactored
# is the only backup a live mail platform has.
#
# Every component writes plaintext into BACKUP_SUBDIR. Encryption is a separate
# stage in backup.sh that runs after verification, because integrity checks
# (gzip -t, tar -tzf) cannot see inside an age file.
# =============================================================================

[[ -n "${_BACKUP_COMPONENTS_LOADED:-}" ]] && return 0
_BACKUP_COMPONENTS_LOADED=1

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

    # This script runs on the host, not inside the Docker network -- neither
    # a "mysqldump" binary nor the "mysql" service hostname (MYSQL_HOST) is
    # reachable from here directly (confirmed live: "mysqldump: command not
    # found"). The container's own name IS resolvable via the Docker socket
    # though, so exec mysqldump inside the mysql container itself instead --
    # no --host/--port needed since it's then talking to its own local server.
    local mysqldump_cmd=(
        docker exec "$MYSQL_CONTAINER"
        mysqldump
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

    if "${mysqldump_cmd[@]}" 2>>/tmp/mailyte_backup_mysql.tmp | gzip -9 > "$dump_file"; then
        local size bytes
        size=$(file_size_human "$dump_file")
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

    local binlog_dir="${BACKUP_SUBDIR}/mysql/binlogs"
    mkdir -p "$binlog_dir"

    local binlog_file binlog_pos
    binlog_file=$(echo "$last_entry" | grep -oP "MASTER_LOG_FILE='[^']+'" | cut -d"'" -f2 || echo "")
    binlog_pos=$(echo "$last_entry" | grep -oP "MASTER_LOG_POS=\d+" | cut -d= -f2 || echo "")

    if [[ -n "$binlog_file" && -n "$binlog_pos" ]]; then
        local incr_file="${binlog_dir}/incremental_${DATE}.sql.gz"
        if docker exec "$MYSQL_CONTAINER" mysqlbinlog \
            --user="$MYSQL_USER" \
            --password="$MYSQL_PASSWORD" \
            --start-position="$binlog_pos" \
            --read-from-remote-server \
            "$binlog_file" 2>>/tmp/mailyte_backup_binlog.tmp | gzip -9 > "$incr_file"; then

            local size bytes
            size=$(file_size_human "$incr_file")
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

    log_info "Triggering Redis BGSAVE on ${REDIS_HOST}:${REDIS_PORT}"
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" BGSAVE 2>/dev/null; then
        local waited=0
        while [[ $waited -lt 60 ]]; do
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

    log_info "Downloading Redis RDB snapshot"
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --rdb "$rdb_file" 2>/dev/null; then
        local size bytes
        size=$(file_size_human "$rdb_file")
        bytes=$(file_size_bytes "$rdb_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        log_success "Redis RDB snapshot saved: ${rdb_file} (${size})"
    else
        log_warn "redis-cli --rdb failed. Trying to copy RDB from Docker volume."
        if docker cp redis:/data/dump.rdb "$rdb_file" 2>/dev/null; then
            local size bytes
            size=$(file_size_human "$rdb_file")
            bytes=$(file_size_bytes "$rdb_file")
            TOTAL_SIZE=$((TOTAL_SIZE + bytes))
            log_success "Redis RDB snapshot copied from container: ${rdb_file} (${size})"
        else
            log_error "Failed to backup Redis RDB snapshot"
            return 1
        fi
    fi

    if docker cp redis:/data/appendonly.aof /tmp/mailyte_backup_aof.tmp 2>/dev/null; then
        gzip -9 -c /tmp/mailyte_backup_aof.tmp > "$aof_file"
        log_info "Redis AOF backup saved: ${aof_file} ($(file_size_human "$aof_file"))"
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

    date '+%Y-%m-%d %H:%M:%S' > "$LAST_MAIL_BACKUP_FILE"

    if [[ -f "$tar_file" ]]; then
        local size bytes
        size=$(file_size_human "$tar_file")
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
# Secrets Backup
# ---------------------------------------------------------------------------
# The component this backup set spent its whole existence missing. Without
# secrets/mail_crypt/ every mail archive ever taken is undecryptable ciphertext,
# and without secrets/encryption_kek the DKIM/PGP/S-MIME keys in MySQL cannot be
# unwrapped -- so a "successful" restore would produce a mail server that can
# read none of its own mail and sign none of its own domains.
#
# This runs only when age encryption is configured. Writing key material into a
# plaintext tar is worse than not backing it up: it turns every backup, and
# every place a backup is copied to, into a full platform compromise.
backup_secrets() {
    log_header "Secrets Backup"
    local start_ts
    start_ts=$(date +%s)

    if ! dr_encryption_configured; then
        log_error "Refusing to back up secrets/ without age encryption configured (DR_AGE_RECIPIENT)"
        return 1
    fi

    local secrets_dir="${PROJECT_ROOT}/secrets"
    if [[ ! -d "$secrets_dir" ]]; then
        log_warn "No secrets directory at ${secrets_dir}. Skipping."
        return 0
    fi

    mkdir -p "${BACKUP_SUBDIR}/secrets"
    # Encrypted directly rather than written plaintext and encrypted by the
    # later stage: even a few seconds of the mail_crypt private key sitting in
    # a world-of-tar on disk is the exposure this component exists to avoid.
    local out="${BACKUP_SUBDIR}/secrets/secrets_${DATE}.tar.age"

    # dr.env holds the S3 credentials this very script is using; excluding it
    # keeps a restored backup from silently re-arming an old bucket.
    if ! tar czhf - -C "$PROJECT_ROOT" --exclude='secrets/dr.env' secrets 2>/dev/null | dr_encrypt_stream > "$out"; then
        log_error "Secrets backup failed"
        rm -f "$out"
        return 1
    fi

    if [[ ! -s "$out" ]]; then
        log_error "Secrets backup produced an empty file"
        rm -f "$out"
        return 1
    fi

    chmod 600 "$out"
    local bytes
    bytes=$(file_size_bytes "$out")
    TOTAL_SIZE=$((TOTAL_SIZE + bytes))
    log_success "Secrets backup completed: ${out} ($(file_size_human "$out"))"
    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("secrets")
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

    # GPG support predates the age stage and is kept for CE installs that
    # configured it; when both are set age wins, since it is what restore.sh
    # and the escrow bundle speak.
    if [[ -z "${DR_AGE_RECIPIENT:-}" && -n "${BACKUP_GPG_RECIPIENT:-}" ]] && command -v gpg &>/dev/null; then
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
        local size bytes
        size=$(file_size_human "$tar_file")
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
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
    [[ -d "$ssl_source" ]] || ssl_source="${PROJECT_ROOT}/storage/ssl_certs"
    local ssl_private_source="$SSL_PRIVATE_DIR"
    [[ -d "$ssl_private_source" ]] || ssl_private_source="${PROJECT_ROOT}/storage/ssl_private"

    local tar_file="${BACKUP_SUBDIR}/ssl/ssl_certs_${DATE}.tar.gz"
    local files_to_backup=()
    [[ -d "$ssl_source" ]] && files_to_backup+=("$ssl_source")
    [[ -d "$ssl_private_source" ]] && files_to_backup+=("$ssl_private_source")

    if [[ ${#files_to_backup[@]} -eq 0 ]]; then
        log_warn "No SSL directories found. Skipping."
        return 0
    fi

    tar czhf "$tar_file" "${files_to_backup[@]}" 2>/dev/null || true

    if [[ -f "$tar_file" ]]; then
        local size bytes
        size=$(file_size_human "$tar_file")
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
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
    for f in \
        "${PROJECT_ROOT}/docker-compose.yml" \
        "${PROJECT_ROOT}/docker-compose.prod.yml" \
        "${PROJECT_ROOT}/docker-compose.dev.yml" \
        "${PROJECT_ROOT}/pyproject.toml" \
        "${PROJECT_ROOT}/alembic.ini" \
        "${PROJECT_ROOT}/.env" \
        "${PROJECT_ROOT}/.env.production" \
        "${PROJECT_ROOT}/.env.staging"; do
        [[ -f "$f" ]] && config_files+=("$f")
    done

    for d in \
        "${PROJECT_ROOT}/config" \
        "${PROJECT_ROOT}/deployment" \
        "${PROJECT_ROOT}/database/migrations"; do
        [[ -d "$d" ]] && config_files+=("$d")
    done

    if [[ ${#config_files[@]} -eq 0 ]]; then
        log_warn "No configuration files found. Skipping."
        return 0
    fi

    # -h because in a deployed tree .env is a symlink up to the persistent
    # root; without it the archive carries a dangling link and no .env at all.
    tar czhf "$tar_file" "${config_files[@]}" 2>/dev/null || true

    if [[ -f "$tar_file" ]]; then
        local size bytes
        size=$(file_size_human "$tar_file")
        bytes=$(file_size_bytes "$tar_file")
        TOTAL_SIZE=$((TOTAL_SIZE + bytes))
        chmod 600 "$tar_file"
        log_success "Configuration backup completed: ${tar_file} (${size})"
    fi

    log_info "Duration: $(elapsed_since "$start_ts")"
    COMPONENTS_BACKED_UP+=("config")
}

# ---------------------------------------------------------------------------
# Verify backups
# ---------------------------------------------------------------------------
# Runs before the encryption stage: gzip -t and tar -tzf cannot see inside an
# age file, so an encrypted-then-verified backup would only prove that age
# produced output, not that the archive inside it is intact.
verify_backups() {
    log_header "Verifying Backups"

    local verify_dir="${1:-$BACKUP_SUBDIR}"
    local all_ok=true

    if [[ ! -d "$verify_dir" ]]; then
        log_error "Backup directory not found: ${verify_dir}"
        return 1
    fi

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
            if ! gzip -t "$dump" 2>/dev/null; then
                log_error "MySQL dump is corrupted (gzip test failed): ${dump}"
                all_ok=false
                continue
            fi
            local header
            header=$(zcat "$dump" 2>/dev/null | head -5)
            if echo "$header" | grep -q "MySQL dump\|mysqldump\|Server version"; then
                log_success "MySQL dump verified: $(basename "$dump") ($(file_size_human "$dump"))"
            else
                log_warn "MySQL dump header looks unusual: $(basename "$dump")"
            fi
        done <<< "$mysql_dumps"
    fi

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
            local magic
            magic=$(head -c 5 "$rdb" 2>/dev/null || echo "")
            if [[ "$magic" == "REDIS" ]]; then
                log_success "Redis RDB verified: $(basename "$rdb") ($(file_size_human "$rdb"))"
            else
                log_warn "Redis RDB magic bytes mismatch: $(basename "$rdb")"
            fi
        done <<< "$redis_dumps"
    fi

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

    # Already-encrypted artefacts (the secrets bundle) can only be checked for
    # a well-formed age header — which still catches the common failure of age
    # writing an error message into the output stream.
    local encrypted
    encrypted=$(find "$verify_dir" -name "*.age" 2>/dev/null)
    if [[ -n "$encrypted" ]]; then
        while IFS= read -r enc; do
            if head -c 21 "$enc" 2>/dev/null | grep -q '^age-encryption.org/v1$'; then
                log_success "Encrypted artefact verified: $(basename "$enc") ($(file_size_human "$enc"))"
            else
                log_error "Not a valid age file: ${enc}"
                all_ok=false
            fi
        done <<< "$encrypted"
    fi

    if [[ "$all_ok" == true ]]; then
        log_success "All backup files verified successfully"
    else
        log_error "Some backup files failed verification"
        return 1
    fi
}
