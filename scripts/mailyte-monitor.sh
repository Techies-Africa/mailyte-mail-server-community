#!/bin/bash
################################################################################
# Mailyte Container Monitor and Management Script
# Quick commands to check and manage all mail server containers
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Functions
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

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Check container status
check_status() {
    print_header "Container Status"
    
    # Get all containers
    containers=$(docker compose ps --format "{{.Name}}" 2>/dev/null || echo "")
    
    if [ -z "$containers" ]; then
        print_warning "No containers found. Run './mailyte-monitor.sh start' to start services"
        return
    fi
    
    # Check each container
    while IFS= read -r container; do
        if [ -n "$container" ]; then
            status=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo "unknown")
            health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "none")
            
            if [ "$status" = "running" ]; then
                if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
                    print_success "$container: $status"
                else
                    print_warning "$container: $status (health: $health)"
                fi
            else
                print_error "$container: $status"
            fi
        fi
    done <<< "$containers"
}

# Check logs for errors
check_logs() {
    print_header "Checking Logs for Errors"
    
    containers=$(docker compose ps --format "{{.Name}}" 2>/dev/null || echo "")
    
    if [ -z "$containers" ]; then
        print_warning "No containers found"
        return
    fi
    
    while IFS= read -r container; do
        if [ -n "$container" ]; then
            errors=$(docker logs "$container" 2>&1 | tail -50 | grep -i "error\|exception\|failed\|fatal" | wc -l)
            
            if [ "$errors" -gt 0 ]; then
                print_error "$container: $errors error(s) found in logs"
                echo "  Last error:"
                docker logs "$container" 2>&1 | tail -50 | grep -i "error\|exception\|failed\|fatal" | tail -1 | sed 's/^/    /'
            else
                print_success "$container: No errors in recent logs"
            fi
        fi
    done <<< "$containers"
}

# Start all services
start_services() {
    print_header "Starting All Services"
    docker compose up -d
    sleep 5
    check_status
}

# Stop all services
stop_services() {
    print_header "Stopping All Services"
    docker compose down
}

# Restart all services
restart_services() {
    print_header "Restarting All Services"
    docker compose restart
    sleep 5
    check_status
}

# Restart a specific service
restart_service() {
    if [ -z "$1" ]; then
        print_error "Please specify a service name"
        return
    fi
    
    print_header "Restarting $1"
    docker compose restart "$1"
    sleep 3
    docker compose ps "$1"
}

# View logs for a specific service
view_logs() {
    if [ -z "$1" ]; then
        print_error "Please specify a service name"
        return
    fi
    
    print_header "Logs for $1"
    docker compose logs -f "$1"
}

# Check resource usage
check_resources() {
    print_header "Resource Usage"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" $(docker compose ps -q 2>/dev/null)
}

# Run health check
health_check() {
    print_header "Running Health Checks"
    
    # Check MySQL
    print_info "Checking MySQL..."
    if docker compose exec -T mysql mysqladmin ping -h localhost --silent 2>/dev/null; then
        print_success "MySQL: Responding"
    else
        print_error "MySQL: Not responding"
    fi
    
    # Check API endpoints
    services=("api:8080" "tracking:8086" "webhooks:8081" "rate_limiter:8082" "monitoring:8085")
    
    for service_port in "${services[@]}"; do
        IFS=':' read -r service port <<< "$service_port"
        print_info "Checking $service..."
        
        if curl -sf "http://localhost:$port/health" > /dev/null 2>&1; then
            print_success "$service: Healthy"
        else
            print_warning "$service: Health endpoint not responding"
        fi
    done
}

# Show help
show_help() {
    cat << EOF
${BOLD}Mailyte Container Monitor${NC}

Usage: $0 [command] [options]

Commands:
    status              Check status of all containers
    logs [service]      View logs (all or specific service)
    errors              Check logs for errors
    start               Start all services
    stop                Stop all services
    restart [service]   Restart all services or specific service
    health              Run health checks
    resources           Show resource usage
    fix                 Run auto-fix script
    help                Show this help message

Examples:
    $0 status
    $0 logs api
    $0 restart postfix
    $0 health

EOF
}

# Main command handler
case "${1:-status}" in
    status)
        check_status
        ;;
    logs)
        if [ -n "$2" ]; then
            view_logs "$2"
        else
            docker compose logs -f
        fi
        ;;
    errors)
        check_logs
        ;;
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        if [ -n "$2" ]; then
            restart_service "$2"
        else
            restart_services
        fi
        ;;
    health)
        health_check
        ;;
    resources)
        check_resources
        ;;
    fix)
        print_header "Running Auto-Fix Script"
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        python3 "$SCRIPT_DIR/container-health-monitor.py"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
