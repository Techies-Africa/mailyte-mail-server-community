#!/bin/bash
################################################################################
# Mailyte Email Server Control Script
# Centralized management for all email server operations
#
# Usage:
#   ./start.sh <command> [flags] [services...]
#
# Examples:
#   ./start.sh dev                    # Start dev mode
#   ./start.sh dev --rebuild          # Rebuild all then start dev
#   ./start.sh dev --rebuild api docs # Rebuild api+docs then start dev
#   ./start.sh prod                   # Start production mode
#   ./start.sh restart                # Restart all services
#   ./start.sh restart api postfix    # Restart specific services
#   ./start.sh stop                   # Stop everything
#   ./start.sh logs api               # Tail api logs
#   ./start.sh status                 # Show service status
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'

# Paths
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_MAIN="$PROJECT_ROOT/docker-compose.yml"
COMPOSE_DEV="$PROJECT_ROOT/docker-compose.dev.yml"
COMPOSE_PROD="$PROJECT_ROOT/docker-compose.prod.yml"
COMPOSE_CLOUD="$PROJECT_ROOT/docker-compose.cloud.yml"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
print_header()  { echo -e "\n${CYAN}${BOLD}  $1${NC}\n"; }
print_success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
print_error()   { echo -e "  ${RED}[!!]${NC} $1"; }
print_info()    { echo -e "  ${BLUE}[--]${NC} $1"; }
print_warn()    { echo -e "  ${YELLOW}[!!]${NC} $1"; }

check_docker() {
    if ! command -v docker &>/dev/null; then
        print_error "Docker is not installed"
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        print_error "Docker daemon is not running"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Compose wrappers — pick the right files based on MODE
# ---------------------------------------------------------------------------
MODE=""           # dev | prod | cloud | cloud-prod | (empty = base only)
REBUILD=false     # --rebuild flag
REBUILD_NOCACHE=false  # --no-cache flag
CLEAN=false       # --clean flag
FORCE=false       # --force / -f flag
SERVICES=()       # positional service names

# --profile console so the console appears in ps/logs/health/stop like any
# other service (PRD SS9). It is declared behind that profile only until its
# image is published; naming a profile no service uses is a no-op, so this
# needs no change once the gate is removed.
CONSOLE_PROFILE="--profile console"

compose() {
    case "$MODE" in
        dev)        docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_DEV" $CONSOLE_PROFILE "$@" ;;
        prod)       docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_PROD" $CONSOLE_PROFILE "$@" ;;
        cloud)      docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_CLOUD" "$@" ;;
        cloud-prod) docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_CLOUD" -f "$COMPOSE_PROD" "$@" ;;
        *)          docker compose -f "$COMPOSE_MAIN" $CONSOLE_PROFILE "$@" ;;
    esac
}

# ---------------------------------------------------------------------------
# Parse global flags from any position in args
# Returns remaining positional args in SERVICES array
# ---------------------------------------------------------------------------
parse_flags() {
    SERVICES=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rebuild)      REBUILD=true ;;
            --no-cache)     REBUILD_NOCACHE=true; REBUILD=true ;;
            --clean)        CLEAN=true ;;
            --force|-f)     FORCE=true ;;
            --dev)          MODE="dev" ;;
            --prod)         MODE="prod" ;;
            --cloud)        MODE="cloud" ;;
            -*)             print_error "Unknown flag: $1"; show_usage; exit 1 ;;
            *)              SERVICES+=("$1") ;;
        esac
        shift
    done
}

# ---------------------------------------------------------------------------
# Auto-setup: SSL certs, dirs, .env
# ---------------------------------------------------------------------------
ensure_setup() {
    local dirs=(
        "$PROJECT_ROOT/storage/ssl_certs"
        "$PROJECT_ROOT/storage/ssl_private"
        "$PROJECT_ROOT/storage/sni_config"
        "$PROJECT_ROOT/storage/mail_data"
        "$PROJECT_ROOT/storage/attachments"
        "$PROJECT_ROOT/storage/dkim_keys"
        "$PROJECT_ROOT/storage/backups"
        "$PROJECT_ROOT/logs/mailer/postfix"
        "$PROJECT_ROOT/logs/mailer/dovecot"
        "$PROJECT_ROOT/logs/mailer/rspamd"
        "$PROJECT_ROOT/logs/worker/api"
        "$PROJECT_ROOT/logs/worker/tracking"
        "$PROJECT_ROOT/logs/worker/webhooks"
    )
    for d in "${dirs[@]}"; do
        mkdir -p "$d" 2>/dev/null
    done

    if [ ! -f "$PROJECT_ROOT/storage/ssl_certs/server.crt" ] || [ ! -f "$PROJECT_ROOT/storage/ssl_private/server.key" ]; then
        print_info "Generating self-signed SSL certificate for development..."
        openssl req -x509 -newkey rsa:2048 \
            -keyout "$PROJECT_ROOT/storage/ssl_private/server.key" \
            -out "$PROJECT_ROOT/storage/ssl_certs/server.crt" \
            -days 365 -nodes \
            -subj "/CN=${HOSTNAME:-mail.localhost}/O=Mailyte Dev" 2>/dev/null
        chmod 600 "$PROJECT_ROOT/storage/ssl_private/server.key"
        print_success "SSL certificate generated"
    fi

    if [ ! -f "$PROJECT_ROOT/.env" ] && [ -f "$PROJECT_ROOT/.env.example" ]; then
        cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
        print_warn ".env created from .env.example — edit it with your settings"
    fi
}

# ---------------------------------------------------------------------------
# Core operations: rebuild, clean, start
# ---------------------------------------------------------------------------
do_rebuild() {
    local cache_flag=""
    if [ "$REBUILD_NOCACHE" = true ]; then
        cache_flag="--no-cache"
    fi

    if [ ${#SERVICES[@]} -gt 0 ]; then
        print_info "Rebuilding: ${SERVICES[*]}..."
        compose build $cache_flag "${SERVICES[@]}"
    else
        print_info "Rebuilding all services..."
        compose build $cache_flag
    fi
}

do_clean() {
    print_info "Stopping and removing containers, orphans, and volumes..."
    compose down --remove-orphans -v
    docker image prune -f
    docker volume prune -f
    print_success "Cleanup complete"
}

do_start() {
    if [ ${#SERVICES[@]} -gt 0 ]; then
        print_info "Starting: ${SERVICES[*]}..."
        compose up -d "${SERVICES[@]}"
    else
        compose up -d
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_dev() {
    MODE="dev"
    parse_flags "$@"
    print_header "Development Mode"
    ensure_setup

    if [ "$CLEAN" = true ]; then
        do_clean
    fi
    if [ "$REBUILD" = true ]; then
        do_rebuild
    fi

    do_start
    echo ""
    print_success "Development environment started"
    compose ps
}

cmd_prod() {
    MODE="prod"
    parse_flags "$@"
    print_header "Production Mode"
    ensure_setup

    if [ "$CLEAN" = true ]; then
        do_clean
    fi
    if [ "$REBUILD" = true ]; then
        do_rebuild
    fi

    do_start
    echo ""
    print_success "Production environment started"
    compose ps
}

cmd_cloud() {
    MODE="cloud"
    parse_flags "$@"
    print_header "Cloud Mode (Remote DB/Redis)"
    ensure_setup

    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    if [ "$DB_HOST" = "mysql" ] || [ -z "$DB_HOST" ]; then
        print_warn "DB_HOST is still 'mysql' (local container)"
        print_info "Set DB_HOST and REDIS_HOST in .env for cloud mode"
        if [ "$FORCE" != true ]; then
            echo -ne "  Continue anyway? (y/n): "
            read -r confirm
            [[ ! "$confirm" =~ ^[Yy]$ ]] && exit 0
        fi
    fi

    if [ "$CLEAN" = true ]; then
        do_clean
    fi
    if [ "$REBUILD" = true ]; then
        do_rebuild
    fi

    do_start
    echo ""
    print_success "Cloud mode started"
    compose ps
}

cmd_start() {
    parse_flags "$@"
    print_header "Starting Services"
    ensure_setup

    if [ "$REBUILD" = true ]; then
        do_rebuild
    fi

    do_start
    print_success "Services started"
    compose ps
}

cmd_stop() {
    parse_flags "$@"
    print_header "Stopping Services"

    if [ ${#SERVICES[@]} -gt 0 ]; then
        compose stop "${SERVICES[@]}"
    else
        compose down
    fi
    print_success "Services stopped"
}

cmd_restart() {
    parse_flags "$@"
    print_header "Restarting Services"

    if [ "$REBUILD" = true ]; then
        if [ ${#SERVICES[@]} -gt 0 ]; then
            compose stop "${SERVICES[@]}"
            do_rebuild
            compose up -d "${SERVICES[@]}"
        else
            compose down
            do_rebuild
            compose up -d
        fi
    else
        if [ ${#SERVICES[@]} -gt 0 ]; then
            compose restart "${SERVICES[@]}"
        else
            compose down
            compose up -d
        fi
    fi
    print_success "Restart complete"
    compose ps
}

cmd_rebuild() {
    parse_flags "$@"
    REBUILD=true
    REBUILD_NOCACHE=true
    print_header "Rebuilding Services"

    if [ ${#SERVICES[@]} -gt 0 ]; then
        compose stop "${SERVICES[@]}"
    else
        compose down
    fi

    do_rebuild

    do_start
    print_success "Rebuild complete"
    compose ps
}

cmd_status() {
    print_header "Service Status"
    compose ps -a
}

cmd_logs() {
    parse_flags "$@"
    if [ ${#SERVICES[@]} -gt 0 ]; then
        compose logs -f --tail=100 "${SERVICES[@]}"
    else
        compose logs -f --tail=100
    fi
}

cmd_health() {
    parse_flags "$@"
    print_header "Health Check"

    local targets=("${SERVICES[@]}")
    if [ ${#targets[@]} -eq 0 ]; then
        targets=($(compose ps --format "{{.Service}}" 2>/dev/null))
    fi

    for svc in "${targets[@]}"; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "no-container")
        case "$status" in
            healthy)      print_success "$svc" ;;
            unhealthy)    print_error "$svc (unhealthy)" ;;
            starting)     print_warn "$svc (starting...)" ;;
            no-container) print_warn "$svc (not running)" ;;
            *)            print_info "$svc ($status)" ;;
        esac
    done
}

cmd_stats() {
    print_header "Resource Usage"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
}

cmd_shell() {
    parse_flags "$@"
    if [ ${#SERVICES[@]} -eq 0 ]; then
        print_error "Usage: ./start.sh shell <service>"
        exit 1
    fi
    compose exec "${SERVICES[0]}" /bin/bash 2>/dev/null || compose exec "${SERVICES[0]}" /bin/sh
}

cmd_exec_cmd() {
    if [ $# -lt 2 ]; then
        print_error "Usage: ./start.sh exec <service> <command...>"
        exit 1
    fi
    local svc="$1"; shift
    compose exec "$svc" "$@"
}

# Database commands
cmd_db_shell() {
    print_header "Database Shell"
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    compose exec mysql mysql -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" "${DB_NAME:-mailserver}"
}

cmd_db_backup() {
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="$PROJECT_ROOT/storage/backups/backup_${timestamp}.sql"
    mkdir -p "$PROJECT_ROOT/storage/backups"
    print_header "Database Backup"
    print_info "Backing up to $backup_file..."
    compose exec -T mysql mysqldump -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" "${DB_NAME:-mailserver}" > "$backup_file"
    print_success "Backup saved: $backup_file"
}

cmd_db_restore() {
    parse_flags "$@"
    if [ ${#SERVICES[@]} -eq 0 ]; then
        print_error "Usage: ./start.sh db-restore <backup_file>"
        exit 1
    fi
    local file="${SERVICES[0]}"
    if [ ! -f "$file" ]; then
        print_error "File not found: $file"
        exit 1
    fi
    print_warn "This will overwrite the current database!"
    if [ "$FORCE" != true ]; then
        echo -ne "  Continue? (y/n): "
        read -r confirm
        [[ ! "$confirm" =~ ^[Yy]$ ]] && exit 0
    fi
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    compose exec -T mysql mysql -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" "${DB_NAME:-mailserver}" < "$file"
    print_success "Database restored from: $file"
}

cmd_db_status() {
    print_header "Database Status"
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    compose exec mysql mysql -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" -e "
        SELECT table_schema AS 'Database',
               ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)'
        FROM information_schema.tables
        WHERE table_schema = '${DB_NAME:-mailserver}'
        GROUP BY table_schema;

        SELECT table_name AS 'Table', table_rows AS 'Rows',
               ROUND(((data_length + index_length) / 1024 / 1024), 2) AS 'Size (MB)'
        FROM information_schema.tables
        WHERE table_schema = '${DB_NAME:-mailserver}'
        ORDER BY (data_length + index_length) DESC;
    "
}

# Mail commands
cmd_mail_queue() {
    print_header "Mail Queue"
    compose exec postfix postqueue -p 2>/dev/null || print_error "Postfix is not running"
}

cmd_mail_flush() {
    print_header "Flushing Mail Queue"
    compose exec postfix postqueue -f
    print_success "Mail queue flushed"
}

cmd_mail_test() {
    parse_flags "$@"
    if [ ${#SERVICES[@]} -eq 0 ]; then
        print_error "Usage: ./start.sh mail-test <recipient@example.com>"
        exit 1
    fi
    local recipient="${SERVICES[0]}"
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    compose exec -T postfix sendmail "$recipient" <<EOF
From: test@${DOMAIN:-yourdomain.com}
To: $recipient
Subject: Mailyte Test Email - $(date)

This is a test email from Mailyte Email Server.
If you receive this, your mail server is working correctly.

-- Mailyte Email Server
EOF
    print_success "Test email queued for delivery to $recipient"
}

# Maintenance commands
cmd_update() {
    parse_flags "$@"
    print_header "Updating Services"
    print_info "Pulling latest changes..."
    git pull || print_warn "Not a git repository or no remote"
    REBUILD=true
    REBUILD_NOCACHE=true
    do_rebuild
    do_start
    print_success "Update complete"
    compose ps
}

cmd_clean() {
    print_header "Cleaning Up"
    do_clean
}

cmd_reset() {
    print_header "Resetting All Services"
    print_warn "This will stop all services and DELETE all data volumes!"
    if [ "$FORCE" != true ]; then
        echo -ne "  Are you sure? (y/n): "
        read -r confirm
        [[ ! "$confirm" =~ ^[Yy]$ ]] && exit 0
    fi
    compose down -v --remove-orphans
    docker image prune -f
    docker volume prune -f
    print_success "Full reset complete"
}

cmd_migrate() {
    print_header "Running Migrations"
    python3 "$PROJECT_ROOT/manage.py" migrate
}

cmd_migrate_status() {
    print_header "Migration Status"
    python3 "$PROJECT_ROOT/manage.py" migrate:status
}

cmd_dkim() {
    # Delegates to the host-side orchestrator, which drives
    # worker/api/utils/dkim_sync.py inside the api container (the only
    # service with both DB access and the decryption KEK) and materialises
    # the key files + selector map inside the rspamd container. Running
    # generate_dkim.py directly against MySQL is impossible from the host
    # (the DB port is deliberately not published).
    print_header "DKIM Key Sync (MySQL -> rspamd)"
    if [ $# -gt 0 ]; then
        python3 "$SCRIPTS_DIR/generate_dkim.py" "$@"
    else
        python3 "$SCRIPTS_DIR/generate_dkim.py" sync
    fi
}

cmd_console_token() {
    # The one thing standing between `./start.sh` and a working console on a
    # fresh install. The API writes this single-use token only while no
    # operator exists, and deletes it the moment it is consumed, so "not
    # found" almost always means an owner account already exists.
    print_header "Console Bootstrap Token"
    local token
    token=$(compose exec -T api cat /app/data/operator-bootstrap-token 2>/dev/null | tr -d '\r\n')
    if [ -n "$token" ]; then
        echo "  Token: $token"
        echo ""
        echo "  Open http://localhost:${CONSOLE_PORT:-3100}/bootstrap and paste it"
        echo "  to create the first owner account."
    else
        print_warn "No bootstrap token available."
        echo ""
        echo "  This is expected if an operator account already exists — the"
        echo "  token is single-use and removed once consumed. If you are"
        echo "  locked out, see docs on recovering operator access."
        echo ""
        echo "  If the API is not running yet, start it first:  ./start.sh"
    fi
}

cmd_services() {
    print_header "Available Services"
    compose config --services | sort
}

cmd_ports() {
    print_header "Exposed Ports"
    compose ps --format "table {{.Service}}\t{{.Ports}}"
}

cmd_version() {
    print_header "Version Information"
    echo "  Mailyte Email Server"
    echo "  Docker: $(docker --version 2>/dev/null)"
    echo "  Compose: $(docker compose version 2>/dev/null)"
    [ -f "$PROJECT_ROOT/VERSION" ] && echo "  Server: $(cat "$PROJECT_ROOT/VERSION")"
}

cmd_config() {
    compose config
}

cmd_diagnostic() {
    print_header "System Diagnostic"
    python3 "$SCRIPTS_DIR/diagnostic.py"
}

cmd_setup() {
    print_header "First-Time Setup"
    ensure_setup
    bash "$SCRIPTS_DIR/quick-start.sh"
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
show_usage() {
    cat << 'EOF'

  Mailyte Email Server Control

  USAGE
    ./start.sh <command> [flags] [services...]

  MODES                                  Start with environment-specific config
    dev   [flags] [services...]          Development mode (hot-reload)
    prod  [flags] [services...]          Production mode (Traefik, resource limits)
    cloud [flags] [services...]          Cloud mode (remote DB/Redis)

  SERVICE MANAGEMENT
    start   [flags] [services...]        Start services (all if none specified)
    stop    [services...]                Stop services (all if none specified)
    restart [flags] [services...]        Restart services
    rebuild [flags] [services...]        Rebuild and restart services
    status                               Show all service statuses
    logs    [services...]                Tail service logs
    health  [services...]                Check service health
    shell   <service>                    Open a shell in a container
    exec    <service> <command...>       Run command in a container

  FLAGS (work with any command above)
    --rebuild                            Rebuild images before starting
    --no-cache                           Rebuild without Docker cache
    --clean                              Remove everything first (fresh start)
    --force, -f                          Skip confirmation prompts

  DATABASE
    db-shell                             Open MySQL shell
    db-backup                            Dump database to storage/backups/
    db-restore <file>                    Restore from backup file
    db-status                            Show tables and sizes

  MAIL
    mail-queue                           Show Postfix queue
    mail-flush                           Flush Postfix queue
    mail-test  <email>                   Send a test email

  MAINTENANCE
    update                               Git pull + rebuild + restart
    clean                                Remove stopped containers and volumes
    reset [--force]                      Stop everything and delete all volumes
    migrate                              Run database migrations
    migrate-status                       Show migration status
    dkim [sync|backfill|dns <domain>]    Sync DKIM keys from MySQL to rspamd
                                         (backfill also mints keys for domains without one)
    console-token                        Show the console bootstrap token (first run)

  INFO
    services                             List all available services
    ports                                Show exposed ports
    version                              Show version info
    config                               Dump resolved compose config
    diagnostic                           Run system diagnostic
    setup                                First-time setup wizard

  EXAMPLES
    ./start.sh dev                       Start everything in dev mode
    ./start.sh dev --rebuild             Rebuild all, then start dev
    ./start.sh dev --rebuild api docs    Rebuild api+docs, then start dev
    ./start.sh dev --no-cache            Full rebuild (no Docker cache) + dev
    ./start.sh dev --clean               Nuke everything, fresh dev start
    ./start.sh restart postfix dovecot   Restart just mail services
    ./start.sh restart --rebuild api     Rebuild api then restart it
    ./start.sh rebuild docs              Rebuild and restart docs only
    ./start.sh prod --rebuild            Rebuild all for production
    ./start.sh logs api postfix          Tail logs for api and postfix
    ./start.sh stop                      Stop all services

EOF
}

# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------
check_docker

COMMAND="${1:-help}"
shift 2>/dev/null || true

case "$COMMAND" in
    # Modes
    dev)              cmd_dev "$@" ;;
    prod)             cmd_prod "$@" ;;
    cloud)            cmd_cloud "$@" ;;

    # Service management
    start)            cmd_start "$@" ;;
    stop)             cmd_stop "$@" ;;
    restart)          cmd_restart "$@" ;;
    rebuild)          cmd_rebuild "$@" ;;
    status|ps)        cmd_status ;;
    logs)             cmd_logs "$@" ;;
    health)           cmd_health "$@" ;;
    shell)            cmd_shell "$@" ;;
    exec)             cmd_exec_cmd "$@" ;;
    stats)            cmd_stats ;;

    # Database
    db-shell)         cmd_db_shell ;;
    db-backup)        cmd_db_backup ;;
    db-restore)       cmd_db_restore "$@" ;;
    db-status)        cmd_db_status ;;

    # Mail
    mail-queue)       cmd_mail_queue ;;
    mail-flush)       cmd_mail_flush ;;
    mail-test)        cmd_mail_test "$@" ;;

    # Maintenance
    update)           cmd_update "$@" ;;
    clean)            cmd_clean ;;
    reset)            parse_flags "$@"; cmd_reset ;;
    migrate)          cmd_migrate ;;
    migrate-status)   cmd_migrate_status ;;
    dkim)             cmd_dkim "$@" ;;
    console-token)    cmd_console_token ;;

    # Info
    services)         cmd_services ;;
    ports)            cmd_ports ;;
    version)          cmd_version ;;
    config)           cmd_config ;;
    diagnostic)       cmd_diagnostic ;;
    setup)            cmd_setup ;;

    help|--help|-h)   show_usage ;;
    *)                print_error "Unknown command: $COMMAND"; show_usage; exit 1 ;;
esac
