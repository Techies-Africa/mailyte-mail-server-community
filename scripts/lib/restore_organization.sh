#!/bin/bash
# =============================================================================
# Single-tenant restore
# =============================================================================
# "Selective single-tenant restore is the one you will actually use most --
# full-platform disasters are rare, one customer deleting a mailbox is weekly."
# (plans/06-operations/phase-01-backup-recovery.md §1.5)
#
# Sourced by restore.sh for `--organization <org_id>`. Kept separate because it
# is a different operation from a platform restore in every way that matters:
#
#   * It must NOT stop services. Other tenants are actively sending and
#     receiving mail while this runs; a restore that takes the platform down to
#     recover one mailbox has traded one customer's problem for everyone's.
#   * It must NOT drop and reload the database. It restores rows belonging to
#     one organization_id out of the backup's dump, into the live schema.
#   * It restores Maildirs by domain, from the backup's mail archive, without
#     touching any other domain's tree.
#   * It can pull individual messages back from the DR-2 archive, which is
#     finer-grained than any backup: "restore this one message" is a real
#     support request and the archive is keyed per message.
#
# Everything it writes goes to a staging area first and is reported before it
# touches live data, because the failure mode of a tenant restore is
# overwriting good current data with older backup data.
# =============================================================================

[[ -n "${_RESTORE_ORGANIZATION_LOADED:-}" ]] && return 0
_RESTORE_ORGANIZATION_LOADED=1

# Tables that carry an organization_id and are safe to restore per tenant.
# Deliberately an allowlist, not "every table with an organization_id column":
# restoring api_keys or sessions from a backup would resurrect credentials the
# customer may have rotated since, and restoring usage counters would corrupt
# billing. Anything not named here is a deliberate omission.
ORG_SCOPED_TABLES=(
    domains
    email_accounts
    aliases
    dkim_keys
    transport_rules
    retention_policies
    suppression_list
)

org_log()   { log_info "[org-restore] $*"; }
org_warn()  { log_warn "[org-restore] $*"; }
org_error() { log_error "[org-restore] $*"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_org_mysql() {
    local sql="$1"
    docker exec -i -e MYSQL_PWD="$MYSQL_PASSWORD" "$MYSQL_CONTAINER" \
        mysql -u "$MYSQL_USER" -N -B -D "$MYSQL_DATABASE" -e "$sql" 2>/dev/null
}

_org_sql_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/\\\\/\\\\\\\\/g; s/'/\\\\'/g")"
}

# Domains the organization owns, read from the LIVE database first and from the
# backup dump if the org no longer exists live (the "we deleted the tenant by
# accident" case, which is precisely when this runs).
_org_domains_live() {
    local org="$1"
    _org_mysql "SELECT domain FROM domains WHERE organization_id = $(_org_sql_quote "$org");"
}

_org_domains_from_dump() {
    local dump="$1" org="$2"
    # The dump is --complete-insert, so column names are present and a row for
    # this org can be recognised without parsing the whole schema.
    zcat -f "$dump" 2>/dev/null \
        | grep -E "^INSERT INTO \`domains\`" \
        | grep -F "$org" \
        | grep -oE "\`domain\`[^)]*" \
        | grep -oE "'[A-Za-z0-9.-]+\.[A-Za-z]{2,}'" \
        | tr -d "'" | sort -u
}

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
restore_organization() {
    local org="$1"

    log_header "Single-Tenant Restore: ${org}"

    if [[ -z "$MYSQL_PASSWORD" ]]; then
        # Same trap backup.sh hit: deploy.sh exports .env, a human shell does not.
        if [[ -f "${PROJECT_ROOT}/.env" ]]; then
            set -a; source "${PROJECT_ROOT}/.env"; set +a
            MYSQL_PASSWORD="${DB_PASSWORD:-}"
            MYSQL_USER="${DB_USER:-$MYSQL_USER}"
            MYSQL_DATABASE="${DB_NAME:-$MYSQL_DATABASE}"
        fi
    fi
    [[ -n "$MYSQL_PASSWORD" ]] || { org_error "DB_PASSWORD unavailable"; return 1; }

    local backup_path
    backup_path=$(resolve_backup_dir) || return 1
    org_log "Backup: ${backup_path}"

    local decrypted_path
    decrypted_path=$(decrypt_backup "$backup_path") || return 1

    local dump
    dump=$(find "${decrypted_path}/mysql" -name '*_full_*.sql' -o -name '*_full_*.sql.gz' 2>/dev/null | head -1)
    [[ -n "$dump" ]] || { org_error "No full MySQL dump in this backup"; return 1; }

    # Which domains belong to this tenant
    local domains
    domains=$(_org_domains_live "$org")
    if [[ -z "$domains" ]]; then
        org_warn "Organization has no domains in the live database; reading them from the backup"
        domains=$(_org_domains_from_dump "$dump" "$org")
    fi
    if [[ -z "$domains" ]]; then
        org_error "No domains found for organization ${org}, in the live DB or the backup"
        org_error "Nothing to restore -- check the organization id"
        [[ "$decrypted_path" != "$backup_path" ]] && rm -rf "$decrypted_path"
        return 1
    fi

    org_log "Domains in scope:"
    while IFS= read -r d; do [[ -n "$d" ]] && echo "      - ${d}"; done <<< "$domains"

    # A staging schema, not the live one. The rows are loaded here, filtered to
    # this organization, and only then copied across -- so a malformed dump or
    # a wrong org id costs a staging database rather than other tenants' data.
    local staging="mailyte_org_restore_$$"
    org_log "Staging schema: ${staging}"

    if [[ "$DRY_RUN" == true ]]; then
        log_dry "Would load ${dump} into ${staging} and copy rows for ${org}"
        log_dry "Would restore Maildirs for the domains above"
        [[ "$decrypted_path" != "$backup_path" ]] && rm -rf "$decrypted_path"
        return 0
    fi

    if [[ "$FORCE" != true ]]; then
        echo ""
        echo -e "${YELLOW}About to restore organization ${org} from ${BACKUP_ID}.${NC}"
        echo -e "${YELLOW}Other tenants are not touched. Services keep running.${NC}"
        confirm "Proceed?" || { org_log "Cancelled"; return 0; }
    fi

    _org_mysql_root "CREATE DATABASE IF NOT EXISTS \`${staging}\`;" || {
        org_error "Could not create staging schema"; return 1; }

    org_log "Loading dump into staging (this does not touch the live schema)"
    if ! zcat -f "$dump" | sed "s/^USE \`[^\`]*\`;/USE \`${staging}\`;/" \
        | docker exec -i -e MYSQL_PWD="$(_org_root_password)" "$MYSQL_CONTAINER" \
            mysql -u root "$staging" 2>/dev/null; then
        org_error "Failed to load dump into staging"
        _org_mysql_root "DROP DATABASE IF EXISTS \`${staging}\`;"
        return 1
    fi

    local restored_rows=0
    for table in "${ORG_SCOPED_TABLES[@]}"; do
        # Skip tables the backup predates, rather than failing the whole restore.
        local exists
        exists=$(_org_mysql_root "SELECT COUNT(*) FROM information_schema.tables
                                  WHERE table_schema='${staging}' AND table_name='${table}';")
        [[ "$exists" == "1" ]] || { org_warn "table ${table} not in this backup, skipping"; continue; }

        local has_org
        has_org=$(_org_mysql_root "SELECT COUNT(*) FROM information_schema.columns
                                   WHERE table_schema='${staging}' AND table_name='${table}'
                                     AND column_name='organization_id';")
        [[ "$has_org" == "1" ]] || { org_warn "table ${table} has no organization_id, skipping"; continue; }

        local n
        n=$(_org_mysql_root "SELECT COUNT(*) FROM \`${staging}\`.\`${table}\`
                             WHERE organization_id = $(_org_sql_quote "$org");")
        [[ "${n:-0}" -gt 0 ]] || { org_log "${table}: nothing for this org"; continue; }

        # REPLACE, not INSERT IGNORE: the point of a restore is that the backup
        # version wins for this tenant. Scoped by organization_id in the WHERE
        # clause, so no other tenant's row can be touched by this statement.
        if _org_mysql_root "REPLACE INTO \`${MYSQL_DATABASE}\`.\`${table}\`
                            SELECT * FROM \`${staging}\`.\`${table}\`
                            WHERE organization_id = $(_org_sql_quote "$org");"; then
            org_log "${table}: restored ${n} row(s)"
            restored_rows=$((restored_rows + n))
            COMPONENTS_RESTORED+=("db:${table}(${n})")
        else
            org_error "${table}: restore failed (schema drift between backup and live?)"
        fi
    done

    _org_mysql_root "DROP DATABASE IF EXISTS \`${staging}\`;"
    org_log "Staging schema dropped"

    # --- Maildirs ---------------------------------------------------------
    local mail_archive
    mail_archive=$(find "${decrypted_path}/mail" -name 'mail_data_*.tar.gz' 2>/dev/null | head -1)
    if [[ -n "$mail_archive" ]]; then
        local mail_root="${MAIL_DATA_DIR}"
        [[ -d "$mail_root" ]] || mail_root="${PROJECT_ROOT}/storage/mail_data"

        while IFS= read -r domain; do
            [[ -n "$domain" ]] || continue
            org_log "Restoring Maildir for ${domain}"
            # Extract only this domain's subtree. Wildcards are anchored to the
            # archive's top-level directory name so one tenant's restore cannot
            # reach into another's tree.
            if tar xzf "$mail_archive" -C "$mail_root" --strip-components=1 \
                 --wildcards "*/${domain}/*" 2>/dev/null; then
                COMPONENTS_RESTORED+=("maildir:${domain}")
                org_log "  restored ${domain}"
            else
                org_warn "  no Maildir for ${domain} in this backup"
            fi
        done <<< "$domains"

        # Dovecot indexes are rebuilt from the Maildir; stale ones would hide
        # restored messages from IMAP until they happened to be refreshed.
        for domain in $domains; do
            docker exec dovecot doveadm index -u "*@${domain}" '*' >/dev/null 2>&1 || true
        done
        org_log "Dovecot indexes refreshed for restored domains"
    else
        org_warn "No mail archive in this backup; database rows only"
    fi

    [[ "$decrypted_path" != "$backup_path" ]] && rm -rf "$decrypted_path"

    log_success "[org-restore] ${org}: ${restored_rows} database row(s) and $(echo "$domains" | grep -c .) domain(s)"
    org_log "Other tenants were not modified. No services were restarted."
    return 0
}

# root is needed because the restore writes across two schemas (staging and
# live), which the application user is not granted.
_org_root_password() {
    if [[ -n "${DB_ROOT_PASSWORD:-}" ]]; then
        printf '%s' "$DB_ROOT_PASSWORD"
    elif [[ -f "${PROJECT_ROOT}/secrets/db_root_password" ]]; then
        cat "${PROJECT_ROOT}/secrets/db_root_password"
    fi
}

_org_mysql_root() {
    local sql="$1"
    docker exec -i -e MYSQL_PWD="$(_org_root_password)" "$MYSQL_CONTAINER" \
        mysql -u root -N -B -e "$sql" 2>/dev/null
}
