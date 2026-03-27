#!/bin/bash
################################################################################
# Mailyte Email Server - Quick Start Script
# Helps set up the email server step by step
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
    echo -e "\n${BLUE}${BOLD}=================================================================================${NC}"
    echo -e "${BLUE}${BOLD}$1${NC}"
    echo -e "${BLUE}${BOLD}=================================================================================${NC}\n"
}

print_step() {
    echo -e "\n${GREEN}${BOLD}→ $1${NC}\n"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

check_command() {
    if command -v "$1" &> /dev/null; then
        print_success "$1 is installed"
        return 0
    else
        print_error "$1 is not installed"
        return 1
    fi
}

prompt_continue() {
    echo -e "\n${YELLOW}Press Enter to continue or Ctrl+C to cancel...${NC}"
    read -r
}

# Main script
print_header "MAILYTE EMAIL SERVER - QUICK START"

print_info "This script will help you set up the Mailyte Email Server"
print_info "Estimated time: 15-30 minutes"
prompt_continue

# Step 1: Check prerequisites
print_step "Step 1: Checking Prerequisites"

check_command "docker" || {
    print_error "Docker is required. Install from: https://docs.docker.com/get-docker/"
    exit 1
}

check_command "docker" && docker compose version &> /dev/null || {
    print_error "Docker Compose is required"
    exit 1
}

check_command "python3" || {
    print_warning "Python 3 is recommended for running diagnostics"
}

print_success "All prerequisites satisfied"
prompt_continue

# Step 2: Environment configuration
print_step "Step 2: Environment Configuration"

if [ ! -f .env ]; then
    print_info "Creating .env file from template..."
    cp .env.example .env
    print_success ".env file created"
    
    echo -e "\n${YELLOW}${BOLD}IMPORTANT: You must edit .env file with your settings!${NC}"
    echo -e "${YELLOW}Required configurations:${NC}"
    echo "  - HOSTNAME (e.g., mail.yourdomain.com)"
    echo "  - DOMAIN (e.g., yourdomain.com)"
    echo "  - ADMIN_EMAIL (e.g., admin@yourdomain.com)"
    echo "  - DB_PASSWORD (generate a strong password)"
    echo "  - ADMIN_PASSWORD (generate a strong password)"
    echo "  - WEBHOOK_SECRET (generate a strong secret)"
    echo ""
    echo -e "${GREEN}Generate strong passwords with:${NC}"
    echo "  openssl rand -hex 32"
    echo ""
    
    read -p "Do you want to edit .env now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} .env
    else
        print_warning "Remember to edit .env before starting services!"
    fi
else
    print_success ".env file already exists"
fi

prompt_continue

# Step 3: Create required directories
print_step "Step 3: Creating Required Directories"

directories=(
    "storage/mail_data"
    "storage/ssl_certs"
    "storage/attachments"
    "storage/backups"
    "storage/qdrant_data"
    "logs/mailer/postfix"
    "logs/mailer/dovecot"
    "logs/mailer/rspamd"
    "logs/worker/api"
    "logs/worker/tracking"
    "logs/worker/webhooks"
    "config/mailer/postfix"
    "config/mailer/dovecot"
    "config/mailer/rspamd"
)

for dir in "${directories[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        print_success "Created: $dir"
    else
        print_info "Exists: $dir"
    fi
done

prompt_continue

# Step 4: Run diagnostic
print_step "Step 4: Running System Diagnostic"

if command -v python3 &> /dev/null; then
    print_info "Running diagnostic tool..."
    python3 diagnostic.py
    
    print_info "\nDiagnostic report saved to: diagnostic_report.json"
    print_info "Review PRODUCTION_READINESS.md for detailed guidance"
else
    print_warning "Python 3 not available, skipping diagnostic"
fi

prompt_continue

# Step 5: Database setup
print_step "Step 5: Starting Database"

print_info "Starting MySQL database..."
docker compose up -d mysql

print_info "Waiting for MySQL to be ready..."
sleep 10

if docker compose ps mysql | grep -q "Up"; then
    print_success "MySQL is running"
    
    print_info "\nDatabase connection details:"
    print_info "Host: localhost"
    print_info "Port: 3306"
    print_info "Database: mailserver"
    print_info "User: Check your .env file"
else
    print_error "MySQL failed to start. Check logs with: docker compose logs mysql"
    exit 1
fi

prompt_continue

# Step 6: Start core services
print_step "Step 6: Starting Core Services"

services=("api" "rate_limiter")

for service in "${services[@]}"; do
    print_info "Starting $service..."
    docker compose up -d "$service"
    sleep 3
    
    if docker compose ps "$service" | grep -q "Up"; then
        print_success "$service is running"
    else
        print_warning "$service may have issues. Check logs with: docker compose logs $service"
    fi
done

prompt_continue

# Step 7: Service verification
print_step "Step 7: Verifying Services"

echo -e "${BOLD}Running services:${NC}"
docker compose ps

echo -e "\n${BOLD}Service health checks:${NC}"

# Check API
if curl -f -s http://localhost:8080/health &> /dev/null; then
    print_success "API service is healthy (http://localhost:8080)"
else
    print_warning "API service health check failed"
fi

# Check Rate Limiter
if curl -f -s http://localhost:8082/health &> /dev/null; then
    print_success "Rate Limiter service is healthy (http://localhost:8082)"
else
    print_warning "Rate Limiter service health check failed"
fi

prompt_continue

# Step 8: Next steps
print_header "SETUP COMPLETE - NEXT STEPS"

echo -e "${GREEN}${BOLD}✓ Initial setup completed!${NC}\n"

echo -e "${BOLD}What's Running:${NC}"
echo "  • MySQL Database (port 3306)"
echo "  • API Service (port 8080)"
echo "  • Rate Limiter (port 8082)"

echo -e "\n${BOLD}Next Steps:${NC}"
echo ""
echo "1. ${BOLD}Configure DNS Records${NC}"
echo "   - Add MX record pointing to your mail server"
echo "   - Add SPF, DKIM, DMARC records"
echo "   - See PRODUCTION_READINESS.md for details"
echo ""
echo "2. ${BOLD}Set Up SSL Certificates${NC}"
echo "   - Option A: docker compose up -d cert_manager (Let's Encrypt)"
echo "   - Option B: Place certificates in storage/ssl_certs/"
echo ""
echo "3. ${BOLD}Complete Database Migrations${NC}"
echo "   - Review database/migrations/"
echo "   - Run: python database/migrate.py"
echo ""
echo "4. ${BOLD}Enable Additional Services${NC}"
echo "   - Uncomment services in docker-compose.yml"
echo "   - Start with: docker compose up -d tracking webhooks analytics"
echo ""
echo "5. ${BOLD}Enable Mail Services${NC} (After DNS + SSL)"
echo "   - docker compose up -d postfix dovecot rspamd"
echo ""

echo -e "${BOLD}Useful Commands:${NC}"
echo "  • View all services:     docker compose ps"
echo "  • View service logs:     docker compose logs -f [service_name]"
echo "  • Stop all services:     docker compose down"
echo "  • Restart a service:     docker compose restart [service_name]"
echo "  • Run diagnostic:        python3 diagnostic.py"
echo ""

echo -e "${BOLD}Documentation:${NC}"
echo "  • Production Guide:  PRODUCTION_READINESS.md"
echo "  • API Documentation: docs/API_DOCUMENTATION.md"
echo "  • Project Structure: PROJECT_STRUCTURE.md"
echo ""

echo -e "${YELLOW}${BOLD}⚠ IMPORTANT:${NC}"
echo "  Before going to production:"
echo "  1. Review and complete all Priority 1 tasks in PRODUCTION_READINESS.md"
echo "  2. Configure DNS records and verify them"
echo "  3. Set up SSL certificates"
echo "  4. Run full integration tests"
echo "  5. Set up monitoring and backups"
echo ""

echo -e "${GREEN}${BOLD}Setup wizard completed successfully!${NC}\n"
