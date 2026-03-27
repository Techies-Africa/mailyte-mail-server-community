#!/bin/bash
################################################################################
# Docker Health Check Script
# Runs inside Docker containers to verify service health
################################################################################

set -e

SERVICE_NAME="${SERVICE_NAME:-unknown}"
HEALTH_CHECK_ENDPOINT="${HEALTH_CHECK_ENDPOINT:-/health}"
SERVICE_PORT="${SERVICE_PORT:-8080}"

echo "🏥 Health Check for: $SERVICE_NAME"

# Function to check HTTP endpoint
check_http_endpoint() {
    local url="http://localhost:${SERVICE_PORT}${HEALTH_CHECK_ENDPOINT}"
    echo "Checking endpoint: $url"
    
    if command -v curl &> /dev/null; then
        if curl -f -s -o /dev/null -w "%{http_code}" "$url" | grep -q "200"; then
            echo "✓ HTTP endpoint healthy"
            return 0
        else
            echo "✗ HTTP endpoint unhealthy"
            return 1
        fi
    elif command -v wget &> /dev/null; then
        if wget --spider -q "$url" 2>&1 | grep -q "200 OK"; then
            echo "✓ HTTP endpoint healthy"
            return 0
        else
            echo "✗ HTTP endpoint unhealthy"
            return 1
        fi
    else
        echo "⚠ No HTTP client available (curl/wget)"
        return 1
    fi
}

# Function to check database connectivity
check_database() {
    if [ -n "$DB_HOST" ]; then
        echo "Checking database connection to $DB_HOST:${DB_PORT:-3306}"
        
        if command -v nc &> /dev/null; then
            if nc -z -w3 "$DB_HOST" "${DB_PORT:-3306}" 2>/dev/null; then
                echo "✓ Database port reachable"
                return 0
            else
                echo "✗ Database port unreachable"
                return 1
            fi
        elif command -v telnet &> /dev/null; then
            if timeout 3 telnet "$DB_HOST" "${DB_PORT:-3306}" 2>/dev/null | grep -q "Connected"; then
                echo "✓ Database port reachable"
                return 0
            else
                echo "✗ Database port unreachable"
                return 1
            fi
        else
            echo "⚠ No network tools available (nc/telnet)"
            return 1
        fi
    else
        echo "⚠ No database configuration found"
        return 0
    fi
}

# Function to check process is running
check_process() {
    local process_name="$1"
    
    if pgrep -f "$process_name" > /dev/null; then
        echo "✓ Process '$process_name' is running"
        return 0
    else
        echo "✗ Process '$process_name' is not running"
        return 1
    fi
}

# Service-specific health checks
case "$SERVICE_NAME" in
    "postfix")
        check_process "postfix" || exit 1
        if [ -S /var/spool/postfix/public/pickup ]; then
            echo "✓ Postfix pickup socket exists"
        else
            echo "✗ Postfix pickup socket missing"
            exit 1
        fi
        ;;
        
    "dovecot")
        check_process "dovecot" || exit 1
        if [ -S /var/run/dovecot/auth-userdb ]; then
            echo "✓ Dovecot auth socket exists"
        else
            echo "✗ Dovecot auth socket missing"
            exit 1
        fi
        ;;
        
    "rspamd")
        check_process "rspamd" || exit 1
        check_http_endpoint || exit 1
        ;;
        
    "mysql")
        if mysqladmin ping -h localhost --silent 2>/dev/null; then
            echo "✓ MySQL is responding"
        else
            echo "✗ MySQL is not responding"
            exit 1
        fi
        ;;
        
    "api"|"tracking"|"webhooks"|"rate_limiter"|"analytics"|"monitoring")
        check_database || exit 1
        check_http_endpoint || exit 1
        ;;
        
    *)
        echo "⚠ Unknown service type, performing generic checks"
        check_http_endpoint || check_database || exit 1
        ;;
esac

echo "✓ $SERVICE_NAME health check passed"
exit 0
