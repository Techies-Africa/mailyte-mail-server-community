#!/bin/bash
################################################################################
# Mailyte Email Server - Interactive Management Console
# Single entry point for all server operations
################################################################################

set -e

# Resolve project root (where this script lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'

# Compose files
COMPOSE_MAIN="$SCRIPT_DIR/docker-compose.yml"
COMPOSE_DEV="$SCRIPT_DIR/docker-compose.dev.yml"
COMPOSE_PROD="$SCRIPT_DIR/docker-compose.prod.yml"
COMPOSE_CLOUD="$SCRIPT_DIR/docker-compose.cloud.yml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}  $1${NC}"
    echo -e "${DIM}  $(printf '%.0s-' $(seq 1 ${#1}))${NC}"
    echo ""
}

print_success() { echo -e "  ${GREEN}[OK]${NC} $1"; }
print_error()   { echo -e "  ${RED}[!!]${NC} $1"; }
print_info()    { echo -e "  ${BLUE}[--]${NC} $1"; }
print_warn()    { echo -e "  ${YELLOW}[!!]${NC} $1"; }

press_enter() {
    echo ""
    echo -ne "  ${DIM}Press Enter to return to menu...${NC}"
    read -r
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        print_error "Docker is not installed. Install from https://docs.docker.com/get-docker/"
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        print_error "Docker daemon is not running. Start Docker first."
        exit 1
    fi
}

# Which compose command to use
# --profile console on every wrapper: the console is declared behind a profile
# until its image is published, but PRD §9 requires it to behave like any other
# service here -- start with the stack, appear in ps/logs/health, stop with
# `down`. Passing the profile centrally is what makes that true without every
# call site remembering. Naming a profile no service uses is a no-op, so this
# needs no change once the gate is removed.
CONSOLE_PROFILE="--profile console"

compose_cmd() {
    docker compose -f "$COMPOSE_MAIN" $CONSOLE_PROFILE "$@"
}

compose_dev_cmd() {
    docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_DEV" $CONSOLE_PROFILE "$@"
}

compose_prod_cmd() {
    docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_PROD" $CONSOLE_PROFILE "$@"
}

compose_cloud_cmd() {
    docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_CLOUD" "$@"
}

compose_cloud_prod_cmd() {
    docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_CLOUD" -f "$COMPOSE_PROD" "$@"
}

# ---------------------------------------------------------------------------
# Service tiers — essential vs optional
# ---------------------------------------------------------------------------
# The console is essential, not optional (PRD §9 shipping model): it is how a
# self-hoster administers the server, and first boot lands on its
# operator-bootstrap screen. That IS the CE onboarding. Starts last because it
# depends_on api being healthy.
ESSENTIAL_SERVICES="redis mysql rspamd postfix dovecot api console"
WORKER_SERVICES="webhooks tracking rate_limiter"
OPTIONAL_SERVICES="cert_manager autoconfig templates"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
show_banner() {
    [ -t 1 ] && clear 2>/dev/null || true
    echo ""
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║     Mailyte Email Server — Community Edition      ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
show_main_menu() {
    echo -e "  ${BOLD}Quick Start${NC}"
    echo -e "    ${GREEN}1${NC}  First-time setup wizard"
    echo -e "    ${GREEN}2${NC}  Start essential services       ${DIM}(redis, mysql, postfix, dovecot, rspamd, api)${NC}"
    echo -e "    ${GREEN}3${NC}  Start all services             ${DIM}(essential + workers + optional)${NC}"
    echo -e "    ${GREEN}4${NC}  Start in development mode      ${DIM}(with hot-reload)${NC}"
    echo -e "    ${GREEN}5${NC}  Start in production mode"
    echo -e "    ${DIM}     Enterprise Edition adds cloud DB, monitoring, AI search, and more${NC}"
    echo ""
    echo -e "  ${BOLD}Service Management${NC}"
    echo -e "    ${GREEN}7${NC}  Service status"
    echo -e "    ${GREEN}8${NC}  Stop all services"
    echo -e "    ${GREEN}9${NC}  Restart services"
    echo -e "    ${GREEN}10${NC} View logs"
    echo ""
    echo -e "  ${BOLD}Health & Monitoring${NC}"
    echo -e "    ${GREEN}11${NC} Health check"
    echo -e "    ${GREEN}12${NC} Run diagnostics"
    echo -e "    ${GREEN}13${NC} Auto-fix container issues"
    echo -e "    ${GREEN}14${NC} Resource usage"
    echo ""
    echo -e "  ${BOLD}Database${NC}"
    echo -e "    ${GREEN}15${NC} Run migrations"
    echo -e "    ${GREEN}16${NC} Migration status"
    echo -e "    ${GREEN}17${NC} Database shell"
    echo -e "    ${GREEN}18${NC} Database backup"
    echo ""
    echo -e "  ${BOLD}Mail Operations${NC}"
    echo -e "    ${GREEN}19${NC} Mail queue status"
    echo -e "    ${GREEN}20${NC} Send test email"
    echo -e "    ${GREEN}21${NC} Generate DKIM keys"
    echo ""
    echo -e "  ${BOLD}Maintenance${NC}"
    echo -e "    ${GREEN}22${NC} Rebuild a service"
    echo -e "    ${GREEN}23${NC} Clean up (prune images/volumes)"
    echo -e "    ${GREEN}24${NC} Open shell in container"
    echo ""
    echo -e "    ${GREEN}0${NC}  Exit"
    echo ""
}

# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-setup: ensure SSL certs, directories, and .env exist before starting
# ---------------------------------------------------------------------------
ensure_setup() {
    # Create required directories
    local dirs=(
        "$SCRIPT_DIR/storage/ssl_certs"
        "$SCRIPT_DIR/storage/ssl_private"
        "$SCRIPT_DIR/storage/sni_config"
        "$SCRIPT_DIR/storage/mail_data"
        "$SCRIPT_DIR/storage/attachments"
        "$SCRIPT_DIR/storage/dkim_keys"
        "$SCRIPT_DIR/storage/backups"
        "$SCRIPT_DIR/logs/mailer/postfix"
        "$SCRIPT_DIR/logs/mailer/dovecot"
        "$SCRIPT_DIR/logs/mailer/rspamd"
        "$SCRIPT_DIR/logs/worker/api"
        "$SCRIPT_DIR/logs/worker/tracking"
        "$SCRIPT_DIR/logs/worker/webhooks"
        # Was missing entirely, so Docker created it as root and rate_limiter
        # could not write into it.
        "$SCRIPT_DIR/logs/worker/rate_limiter"
    )
    for d in "${dirs[@]}"; do
        mkdir -p "$d" 2>/dev/null
    done

    # Generate self-signed SSL certs if missing
    if [ ! -f "$SCRIPT_DIR/storage/ssl_certs/server.crt" ] || [ ! -f "$SCRIPT_DIR/storage/ssl_private/server.key" ]; then
        print_info "Generating self-signed SSL certificate for development..."
        openssl req -x509 -newkey rsa:2048 \
            -keyout "$SCRIPT_DIR/storage/ssl_private/server.key" \
            -out "$SCRIPT_DIR/storage/ssl_certs/server.crt" \
            -days 365 -nodes \
            -subj "/CN=${HOSTNAME:-mail.localhost}/O=Mailyte Dev" 2>/dev/null
        chmod 600 "$SCRIPT_DIR/storage/ssl_private/server.key"
        print_success "SSL certificate generated (self-signed, valid 365 days)"
        print_info "For production, replace with real certs or enable cert_manager"
    fi

    # Create .env from example if missing.
    #
    # The placeholders are REPLACED with generated values rather than copied
    # through. secrets-check (C3) refuses to start the stack on a placeholder
    # or a known-weak default, so a straight `cp` would hand every new
    # self-hoster a stack that cannot boot and a message about a file they
    # have not read yet. Generating here means the secure path is also the
    # default path -- the operator never has to choose it.
    if [ ! -f "$SCRIPT_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        chmod 600 "$SCRIPT_DIR/.env"

        # The five names in startup_checks.ALL_SECRETS. Keep in step with it.
        for var in DB_ROOT_PASSWORD DB_PASSWORD WEBHOOK_SECRET ADMIN_PASSWORD ADMIN_TOKEN_SECRET; do
            # base64 then strip non-alphanumerics: these values travel through
            # compose interpolation, MySQL command lines and connection URLs,
            # where +, / and = are variously special. 32 bytes in, ~40 chars
            # out, comfortably past startup_checks.MIN_LENGTH of 16.
            value="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)"
            if grep -q "^${var}=" "$SCRIPT_DIR/.env" 2>/dev/null; then
                # -i '' is BSD/macOS, -i is GNU. Neither is portable, so use a
                # temp file and move it.
                sed "s|^${var}=.*|${var}=${value}|" "$SCRIPT_DIR/.env" > "$SCRIPT_DIR/.env.tmp" \
                    && mv "$SCRIPT_DIR/.env.tmp" "$SCRIPT_DIR/.env"
            else
                printf '%s=%s\n' "$var" "$value" >> "$SCRIPT_DIR/.env"
            fi
        done
        chmod 600 "$SCRIPT_DIR/.env"
        print_success ".env created with generated secrets (not the example placeholders)"
        print_info "Review it for host-specific settings: HOSTNAME, DOMAIN, ports"
    fi

    # Key Encryption Key for envelope-encrypted DKIM private keys (C2).
    #
    # This must exist before `docker compose up`. The api service bind-mounts
    # the file, and Docker silently creates a DIRECTORY at any bind-mount
    # source that does not exist -- which then fails to open as a file, on
    # every DKIM operation, with an error that does not mention Docker.
    if [ ! -f "$SCRIPT_DIR/secrets/encryption_kek" ]; then
        if [ -x "$SCRIPTS_DIR/generate_dkim_kek.sh" ]; then
            bash "$SCRIPTS_DIR/generate_dkim_kek.sh" >/dev/null 2>&1
        else
            mkdir -p "$SCRIPT_DIR/secrets"
            openssl rand -base64 32 > "$SCRIPT_DIR/secrets/encryption_kek"
        fi
        chmod 700 "$SCRIPT_DIR/secrets" 2>/dev/null
        chmod 600 "$SCRIPT_DIR/secrets/encryption_kek" 2>/dev/null
        print_success "DKIM key-encryption key generated at secrets/encryption_kek"
        print_warn "BACK THIS UP. Losing it makes every stored DKIM private key unrecoverable."
    fi
}

do_first_time_setup() {
    print_header "First-Time Setup Wizard"
    ensure_setup
    bash "$SCRIPTS_DIR/quick-start.sh"
    press_enter
}

# The console ships as a published image, so unlike every other service here
# it can fail for a reason unrelated to this host: the image is not in the
# registry yet. That must not take the mail server down with it -- a running
# server with no console is recoverable; a start.sh that aborts at stage 5
# looks like the whole stack is broken.
start_console() {
    if compose_cmd up -d console 2>/dev/null; then
        print_success "Console started — http://127.0.0.1:${CONSOLE_PORT:-3100}"
        print_info  "First run: create the owner account with the bootstrap token"
        print_info  "  ./start.sh console-token"
    else
        print_warn "Console did not start (image ghcr.io/techies-africa/mailyte-console not published yet?)"
        print_info "The mail server is unaffected."
    fi
}

do_start_essential() {
    ensure_setup
    print_header "Starting Essential Services"
    print_info "Services: $ESSENTIAL_SERVICES"
    echo ""

    print_info "Stage 1: Infrastructure (redis, mysql)..."
    compose_cmd up -d redis mysql
    print_info "Waiting for MySQL to be healthy..."

    local waited=0
    while [ $waited -lt 60 ]; do
        if docker inspect --format='{{.State.Health.Status}}' mysql 2>/dev/null | grep -q healthy; then
            print_success "MySQL is healthy"
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done
    if [ $waited -ge 60 ]; then
        print_warn "MySQL took too long — check logs with: docker compose logs mysql"
    fi

    # Wait for redis
    local rwait=0
    while [ $rwait -lt 20 ]; do
        if docker inspect --format='{{.State.Health.Status}}' redis 2>/dev/null | grep -q healthy; then
            print_success "Redis is healthy"
            break
        fi
        sleep 2
        rwait=$((rwait + 2))
    done

    print_info "Stage 2: Anti-spam (rspamd)..."
    compose_cmd up -d rspamd
    sleep 3

    print_info "Stage 3: Mail services (postfix, dovecot)..."
    compose_cmd up -d postfix dovecot
    sleep 3

    print_info "Stage 4: API..."
    compose_cmd up -d api
    sleep 2

    print_info "Stage 5: Console..."
    start_console

    echo ""
    print_success "Essential services started"
    echo ""
    compose_cmd ps
    press_enter
}

do_start_all() {
    ensure_setup
    print_header "Starting All Services (Staged)"
    bash "$SCRIPTS_DIR/staged-startup.sh"
    press_enter
}

do_start_dev() {
    ensure_setup
    print_header "Starting Development Mode"
    print_info "Using docker-compose.yml + docker-compose.dev.yml"
    print_info "Workers will run with hot-reload enabled"
    echo ""
    compose_dev_cmd up -d
    echo ""
    print_success "Development environment started"
    compose_dev_cmd ps
    press_enter
}

do_start_prod() {
    ensure_setup
    print_header "Starting Production Mode"
    print_info "Using docker-compose.yml + docker-compose.prod.yml"
    echo ""
    compose_prod_cmd up -d
    echo ""
    print_success "Production environment started"
    compose_prod_cmd ps
    press_enter
}

do_start_cloud() {
    ensure_setup
    print_header "Starting with Cloud DB/Redis"

    # Validate remote hosts are configured
    source "$SCRIPT_DIR/.env" 2>/dev/null || true
    if [ "$DB_HOST" = "mysql" ] || [ -z "$DB_HOST" ]; then
        print_warn "DB_HOST is still set to 'mysql' (Docker container)"
        print_info "Update .env with your remote MySQL host first:"
        echo ""
        echo "    DB_HOST=your-instance.xxxx.rds.amazonaws.com"
        echo "    REDIS_HOST=your-cluster.xxxx.cache.amazonaws.com"
        echo ""
        echo -ne "  Continue anyway? (y/n): "
        read -r confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            press_enter
            return
        fi
    fi

    print_info "Using docker-compose.yml + docker-compose.cloud.yml"
    print_info "Local mysql/redis containers will NOT run"
    print_info "DB_HOST=$DB_HOST | REDIS_HOST=$REDIS_HOST"
    echo ""
    compose_cloud_cmd up -d
    echo ""
    print_success "Cloud mode started"
    compose_cloud_cmd ps
    press_enter
}

do_status() {
    print_header "Service Status"
    compose_cmd ps -a
    press_enter
}

do_stop() {
    print_header "Stopping All Services"
    compose_cmd down
    print_success "All services stopped"
    press_enter
}

do_restart() {
    print_header "Restart Services"
    echo -e "  Enter service name(s) to restart, or ${BOLD}all${NC} for everything:"
    echo -ne "  > "
    read -r svc
    if [ "$svc" = "all" ] || [ -z "$svc" ]; then
        compose_cmd restart
    else
        compose_cmd restart $svc
    fi
    print_success "Restart complete"
    press_enter
}

do_logs() {
    print_header "View Logs"
    echo "  Available services:"
    compose_cmd ps --format "{{.Service}}" 2>/dev/null | sed 's/^/    /'
    echo ""
    echo -e "  Enter service name (or ${BOLD}all${NC}):"
    echo -ne "  > "
    read -r svc
    echo -e "  ${DIM}(Ctrl+C to stop following logs)${NC}"
    echo ""
    if [ "$svc" = "all" ] || [ -z "$svc" ]; then
        compose_cmd logs -f --tail=100
    else
        compose_cmd logs -f --tail=100 "$svc"
    fi
    press_enter
}

do_health() {
    print_header "Health Check"
    bash "$SCRIPTS_DIR/mailyte-monitor.sh" health
    press_enter
}

do_diagnostics() {
    print_header "System Diagnostics"
    python3 "$SCRIPTS_DIR/diagnostic.py"
    press_enter
}

do_autofix() {
    print_header "Auto-Fix Container Issues"
    python3 "$SCRIPTS_DIR/container-health-monitor.py"
    press_enter
}

do_resources() {
    print_header "Resource Usage"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
    press_enter
}

do_migrate() {
    print_header "Run Database Migrations"
    python3 "$SCRIPT_DIR/manage.py" migrate
    press_enter
}

do_migration_status() {
    print_header "Migration Status"
    python3 "$SCRIPT_DIR/manage.py" migrate:status
    press_enter
}

do_db_shell() {
    print_header "Database Shell"
    print_info "Connecting to MySQL..."
    compose_cmd exec mysql mysql -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" "${DB_NAME:-mailserver}"
    press_enter
}

do_db_backup() {
    print_header "Database Backup"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="$SCRIPT_DIR/storage/backups/backup_${timestamp}.sql"
    mkdir -p "$SCRIPT_DIR/storage/backups"
    print_info "Backing up to $backup_file..."
    compose_cmd exec -T mysql mysqldump -u root -p"${DB_ROOT_PASSWORD:-rootpassword}" "${DB_NAME:-mailserver}" > "$backup_file"
    print_success "Backup saved: $backup_file"
    press_enter
}

do_mail_queue() {
    print_header "Mail Queue"
    compose_cmd exec postfix postqueue -p 2>/dev/null || print_warn "Postfix is not running"
    press_enter
}

do_mail_test() {
    print_header "Send Test Email"
    echo -ne "  Recipient email: "
    read -r recipient
    if [ -z "$recipient" ]; then
        print_error "No recipient provided"
    else
        compose_cmd exec -T postfix sendmail "$recipient" <<EOF
From: test@${DOMAIN:-yourdomain.com}
To: $recipient
Subject: Mailyte Test Email - $(date)

This is a test email from Mailyte Email Server.
If you receive this, your mail server is working correctly.

-- Mailyte Email Server
EOF
        print_success "Test email queued for delivery to $recipient"
    fi
    press_enter
}

do_generate_dkim() {
    print_header "Generate DKIM Keys"
    python3 "$SCRIPTS_DIR/generate_dkim.py"
    press_enter
}

do_start_docs() {
    print_header "Starting Documentation Server"

    # Detect which mode we're likely running
    local running_dev=false
    if docker compose -f "$COMPOSE_MAIN" -f "$COMPOSE_DEV" ps --format "{{.Service}}" 2>/dev/null | grep -q api; then
        running_dev=true
    fi

    if [ "$running_dev" = true ]; then
        print_info "Dev mode detected — starting docs with live-reload"
        print_info "Edit docs/ files and changes appear instantly"
        compose_dev_cmd up -d docs
    else
        print_info "Starting docs with static build (production-style)"
        compose_cmd up -d docs
    fi

    # Wait for docs to be ready
    local waited=0
    while [ $waited -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' docs 2>/dev/null | grep -q healthy; then
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    local port="${DOCS_PORT:-8000}"
    echo ""
    print_success "Documentation server running"
    print_info "Open http://localhost:$port in your browser"
    press_enter
}

do_rebuild_docs() {
    print_header "Rebuilding Documentation"
    print_info "Stopping docs container..."
    compose_cmd stop docs 2>/dev/null || true
    print_info "Rebuilding docs image (no cache)..."
    compose_cmd build --no-cache docs
    print_info "Starting docs..."
    compose_cmd up -d docs
    echo ""
    local port="${DOCS_PORT:-8000}"
    print_success "Docs rebuilt and running at http://localhost:$port"
    press_enter
}

do_run_tests() {
    print_header "Integration Tests"
    print_info "Running 139 tests across 18 files (4 parallel workers)"
    echo ""

    # Determine Docker network name
    local network
    network=$(docker network ls --format "{{.Name}}" | grep mailserver_network | head -1)
    if [ -z "$network" ]; then
        print_error "Docker network not found. Start services first: ./start.sh dev"
        [ "${CLI_MODE:-}" != "true" ] && press_enter
        return 1
    fi

    # Source .env for DB credentials
    source "$SCRIPT_DIR/.env" 2>/dev/null || true

    docker run --rm \
      --network "$network" \
      -v "$SCRIPT_DIR/tests:/tests" \
      -v "$SCRIPT_DIR/.gitignore:/project/.gitignore:ro" \
      -e TEST_SMTP_HOST=postfix \
      -e TEST_SMTP_PORT=587 \
      -e TEST_SMTP_PORT_25=25 \
      -e TEST_IMAP_HOST=dovecot \
      -e TEST_IMAP_PORT=143 \
      -e TEST_IMAP_SSL_PORT=993 \
      -e TEST_POP3_HOST=dovecot \
      -e TEST_POP3_PORT=110 \
      -e TEST_POP3_SSL_PORT=995 \
      -e TEST_API_BASE=http://api:8080 \
      -e TEST_TRACKING_BASE=http://tracking:8086 \
      -e TEST_WEBHOOKS_BASE=http://webhooks:8081 \
      -e TEST_RATE_LIMITER_BASE=http://rate_limiter:8082 \
      -e TEST_DB_HOST=mysql \
      -e TEST_DB_PORT=3306 \
      -e TEST_DB_NAME="${DB_NAME:-mailserver}" \
      -e TEST_DB_USER="${DB_USER:-mailuser}" \
      -e TEST_DB_PASS="${DB_PASSWORD:-mailpassword123}" \
      -e TEST_REDIS_HOST=redis \
      -e TEST_REDIS_PORT=6379 \
      -e TEST_API_KEY=test-api-key-123 \
      -e TEST_ADMIN_TOKEN="${ADMIN_TOKEN_SECRET:-tokensecret}" \
      -e TEST_USER=user@test.local \
      -e TEST_PASS=testpass123 \
      -e TEST_DOMAIN=test.local \
      -e TEST_ORG=test-org \
      -w /tests \
      python:3.11-slim \
      bash -c "pip install -q pytest pytest-xdist pytest-timeout requests mysql-connector-python redis bcrypt 2>/dev/null && python -m pytest integration/ -n 4 --dist loadgroup --timeout=90 -v 2>&1"

    local exit_code=$?
    echo ""
    if [ $exit_code -eq 0 ]; then
        print_success "All tests passed"
    else
        print_error "Some tests failed (exit code: $exit_code)"
    fi
    [ "${CLI_MODE:-}" != "true" ] && press_enter
    return $exit_code
}

do_rebuild() {
    print_header "Rebuild Service"
    echo "  Available services:"
    compose_cmd config --services 2>/dev/null | sed 's/^/    /'
    echo ""
    echo -ne "  Service to rebuild: "
    read -r svc
    if [ -z "$svc" ]; then
        print_error "No service specified"
    else
        print_info "Stopping $svc..."
        compose_cmd stop "$svc"
        print_info "Rebuilding $svc (no cache)..."
        compose_cmd build --no-cache "$svc"
        print_info "Starting $svc..."
        compose_cmd up -d "$svc"
        print_success "$svc rebuilt and started"
    fi
    press_enter
}

do_clean() {
    print_header "Clean Up"
    print_info "Removing stopped containers and orphans..."
    compose_cmd down --remove-orphans 2>/dev/null || true
    print_info "Pruning unused images..."
    docker image prune -f
    print_info "Pruning unused volumes..."
    docker volume prune -f
    print_success "Cleanup complete"
    press_enter
}

do_shell() {
    print_header "Open Shell in Container"
    echo "  Running services:"
    compose_cmd ps --format "{{.Service}}" 2>/dev/null | sed 's/^/    /'
    echo ""
    echo -ne "  Service: "
    read -r svc
    if [ -z "$svc" ]; then
        print_error "No service specified"
    else
        compose_cmd exec "$svc" /bin/bash 2>/dev/null || compose_cmd exec "$svc" /bin/sh
    fi
    press_enter
}

do_tests() {
    print_header "Run Tests"
    echo -e "    ${GREEN}1${NC}  Unit tests"
    echo -e "    ${GREEN}2${NC}  Integration tests"
    echo -e "    ${GREEN}3${NC}  All tests"
    echo ""
    echo -ne "  Choice: "
    read -r choice
    case "$choice" in
        1) python3 -m pytest "$SCRIPT_DIR/tests/unit/" -v ;;
        2) python3 -m pytest "$SCRIPT_DIR/tests/e2e/" -v ;;
        3) python3 -m pytest "$SCRIPT_DIR/tests/" -v ;;
        *) print_error "Invalid choice" ;;
    esac
    press_enter
}

# ---------------------------------------------------------------------------
# CLI mode — handle test command here, pass rest to mailyte-ctl.sh
# ---------------------------------------------------------------------------
if [ -n "$1" ]; then
    check_docker
    if [ "$1" = "test" ] || [ "$1" = "tests" ]; then
        CLI_MODE=true do_run_tests
        exit $?
    fi
    bash "$SCRIPTS_DIR/mailyte-ctl.sh" "$@"
    exit $?
fi

# ---------------------------------------------------------------------------
# Interactive menu loop
# ---------------------------------------------------------------------------
check_docker

while true; do
    show_banner
    show_main_menu
    echo -ne "  ${BOLD}Select an option:${NC} "
    read -r choice

    case "$choice" in
        1)  do_first_time_setup ;;
        2)  do_start_essential ;;
        3)  do_start_all ;;
        4)  do_start_dev ;;
        5)  do_start_prod ;;
        6)  print_error "Cloud mode is available in Mailyte Enterprise Edition"; press_enter ;;
        7)  do_status ;;
        8)  do_stop ;;
        9)  do_restart ;;
        10) do_logs ;;
        11) do_health ;;
        12) do_diagnostics ;;
        13) do_autofix ;;
        14) do_resources ;;
        15) do_migrate ;;
        16) do_migration_status ;;
        17) do_db_shell ;;
        18) do_db_backup ;;
        19) do_mail_queue ;;
        20) do_mail_test ;;
        21) do_generate_dkim ;;
        22) do_rebuild ;;
        23) do_clean ;;
        24) do_shell ;;
        0|q|exit)
            echo ""
            print_info "Goodbye!"
            echo ""
            exit 0
            ;;
        *)
            print_error "Invalid option: $choice"
            sleep 1
            ;;
    esac
done
