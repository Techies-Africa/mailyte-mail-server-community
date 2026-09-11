#!/bin/bash
################################################################################
# Staged Startup Script for Mailyte Mail Server
# Starts containers in stages to avoid overwhelming the system
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

print_header() {
    echo -e "\n${BLUE}${BOLD}========================================${NC}"
    echo -e "${BLUE}${BOLD}$1${NC}"
    echo -e "${BLUE}${BOLD}========================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

wait_for_healthy() {
    local service=$1
    local max_wait=${2:-60}
    local waited=0
    
    print_info "Waiting for $service to be healthy..."
    
    while [ $waited -lt $max_wait ]; do
        if docker compose ps "$service" 2>/dev/null | grep -q "Up"; then
            print_success "$service is running"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    
    print_error "$service failed to start within ${max_wait}s"
    return 1
}

# Stage 1: Core Infrastructure
print_header "Stage 1: Starting Core Infrastructure"

print_info "Starting MySQL and Qdrant..."
docker compose up -d mysql qdrant

wait_for_healthy mysql 90
wait_for_healthy qdrant 30

# Wait for MySQL to be fully ready
print_info "Waiting for MySQL to be ready for connections..."
sleep 10

# Stage 2: Core Worker Services
print_header "Stage 2: Starting Core Worker Services"

print_info "Starting API, Webhooks, and Rate Limiter..."
docker compose up -d api webhooks rate_limiter

wait_for_healthy api 60
wait_for_healthy webhooks 60
wait_for_healthy rate_limiter 60

sleep 5

# Stage 3: Tracking and Monitoring
print_header "Stage 3: Starting Tracking and Monitoring"

print_info "Starting Tracking and Monitoring services..."
docker compose up -d tracking monitoring

wait_for_healthy tracking 60
wait_for_healthy monitoring 60

sleep 5

# Stage 4: Mail Services
print_header "Stage 4: Starting Mail Services"

print_info "Starting Postfix, Dovecot, Rspamd, and Cert Manager..."
docker compose up -d postfix dovecot rspamd cert_manager

wait_for_healthy postfix 90
wait_for_healthy dovecot 90
wait_for_healthy rspamd 60
wait_for_healthy cert_manager 60

sleep 5

# Stage 5: Additional Worker Services
print_header "Stage 5: Starting Additional Worker Services"

print_info "Starting Analytics, Archiver, Queue Manager..."
docker compose up -d analytics archiver queue_manager storage_usage

wait_for_healthy analytics 60
wait_for_healthy archiver 60
wait_for_healthy queue_manager 60
wait_for_healthy storage_usage 60

sleep 5

# Stage 6: Optional Services
print_header "Stage 6: Starting Optional Services"

print_info "Starting RAG, Encryption, Dashboard, ActiveSync, Cloud Sync..."
docker compose up -d rag encryption dashboard activesync

sleep 10

# Stage 7: Documentation
print_header "Stage 7: Starting Documentation Server"

print_info "Starting Docs service..."
docker compose up -d docs

wait_for_healthy docs 30

sleep 3

# Stage 8: Console (PRD SS9 shipping model -- the console comes up with the
# stack, not as a separate startup). Last, because it depends on a healthy api.
print_header "Stage 8: Starting Mailyte Console"

# --profile console is needed while docker-compose.yml still gates the
# service; naming an unused profile is harmless, so this keeps working once
# that line is removed. A pull failure must not fail the whole startup: the
# image is published on its own cadence and a mail server without a console
# is still a working mail server.
if docker compose --profile console up -d console 2>/dev/null; then
    # `|| true` because set -e is active and wait_for_healthy returns 1 on
    # timeout — a slow console must not abort the run just before the final
    # status check, which is the output the operator actually needs.
    wait_for_healthy console 40 || true
    print_info "Console: http://localhost:${CONSOLE_PORT:-3100}"
else
    print_warn "Console did not start (image ghcr.io/techies-africa/mailyte-console not published yet?)"
    print_info "The mail server is unaffected."
fi

sleep 2

# Final Status Check
print_header "Final Status Check"

docker compose ps

print_header "Startup Complete!"
print_info "Console: http://localhost:${CONSOLE_PORT:-3100} (token: ./start.sh console-token)"
print_info "Run './start.sh' for the management menu"
print_info "Run './start.sh health' to run health checks"
