#!/bin/bash
################################################################################
# Mailyte Email Server — First User Setup
# Creates your first organization, API key, domain, and mailbox
################################################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

echo ""
echo -e "${CYAN}${BOLD}  Mailyte Email Server — First User Setup${NC}"
echo -e "  ──────────────────────────────────────────"
echo ""

# Load .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
fi

DB_ROOT_PASS="${DB_ROOT_PASSWORD:-rootpassword}"
DB_NAME="${DB_NAME:-mailserver}"
DB_HOST="${DB_HOST:-mysql}"

# Check services are running
if ! docker compose ps --format "{{.Name}}" 2>/dev/null | grep -q "mysql"; then
    echo -e "  ${RED}[!!]${NC} Services are not running. Start them first:"
    echo "       ./start.sh"
    exit 1
fi

# Gather info
echo -e "  ${BOLD}Enter your details:${NC}"
echo ""

read -p "  Organization name (e.g., my-company): " ORG_NAME
if [ -z "$ORG_NAME" ]; then
    echo -e "  ${RED}[!!]${NC} Organization name is required"
    exit 1
fi

read -p "  Domain (e.g., yourdomain.com): " DOMAIN_NAME
if [ -z "$DOMAIN_NAME" ]; then
    echo -e "  ${RED}[!!]${NC} Domain is required"
    exit 1
fi

read -p "  Email address (e.g., admin@${DOMAIN_NAME}): " EMAIL
if [ -z "$EMAIL" ]; then
    EMAIL="admin@${DOMAIN_NAME}"
fi

read -p "  Display name (e.g., Admin): " DISPLAY_NAME
if [ -z "$DISPLAY_NAME" ]; then
    DISPLAY_NAME="Admin"
fi

read -sp "  Password for ${EMAIL}: " PASSWORD
echo ""
if [ -z "$PASSWORD" ]; then
    echo -e "  ${RED}[!!]${NC} Password is required"
    exit 1
fi

LOCAL_PART="${EMAIL%%@*}"

echo ""
echo -e "  ${CYAN}Setting up...${NC}"

# Create org, API key, domain, mailbox in one go
docker exec -i mysql mysql -u root -p"${DB_ROOT_PASS}" "${DB_NAME}" <<EOSQL 2>/dev/null
INSERT IGNORE INTO organizations (id, name, active) VALUES ('${ORG_NAME}', '${ORG_NAME}', 1);
EOSQL

if [ $? -ne 0 ]; then
    echo -e "  ${RED}[!!]${NC} Failed to create organization. Check database credentials."
    exit 1
fi
echo -e "  ${GREEN}[OK]${NC} Organization: ${ORG_NAME}"

# Generate API key
API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
API_KEY_HASH=$(python3 -c "import hashlib; print(hashlib.sha256('${API_KEY}'.encode()).hexdigest())")

docker exec -i mysql mysql -u root -p"${DB_ROOT_PASS}" "${DB_NAME}" <<EOSQL 2>/dev/null
INSERT INTO api_keys (key_id, key_hash, name, permissions, organization_id, active)
VALUES ('${API_KEY}', '${API_KEY_HASH}', '${ORG_NAME}-key', '{"read": true, "write": true}', '${ORG_NAME}', 1);
EOSQL
echo -e "  ${GREEN}[OK]${NC} API Key: ${API_KEY}"

# Create domain
docker exec -i mysql mysql -u root -p"${DB_ROOT_PASS}" "${DB_NAME}" <<EOSQL 2>/dev/null
INSERT IGNORE INTO domains (organization_id, domain, active) VALUES ('${ORG_NAME}', '${DOMAIN_NAME}', 1);
EOSQL
echo -e "  ${GREEN}[OK]${NC} Domain: ${DOMAIN_NAME}"

# Create mailbox using the API container (has bcrypt)
DOMAIN_ID=$(docker exec -i mysql mysql -u root -p"${DB_ROOT_PASS}" "${DB_NAME}" -N -e "SELECT id FROM domains WHERE domain='${DOMAIN_NAME}' LIMIT 1" 2>/dev/null)

docker exec api python3 -c "
import bcrypt, sys
sys.path.insert(0, '/app')
from utils.database import get_db_connection
pw = bcrypt.hashpw('${PASSWORD}'.encode(), bcrypt.gensalt()).decode()
conn = get_db_connection()
cur = conn.cursor()
cur.execute(
    'INSERT INTO email_accounts (email, local_part, domain_id, organization_id, password, name, status) VALUES (%s, %s, %s, %s, %s, %s, %s)',
    ('${EMAIL}', '${LOCAL_PART}', ${DOMAIN_ID}, '${ORG_NAME}', pw, '${DISPLAY_NAME}', 'active')
)
conn.commit()
print('OK')
" 2>/dev/null

if [ $? -eq 0 ]; then
    echo -e "  ${GREEN}[OK]${NC} Mailbox: ${EMAIL}"
else
    echo -e "  ${YELLOW}[!!]${NC} Mailbox creation may have failed — check if it already exists"
fi

echo ""
echo -e "  ${CYAN}${BOLD}Setup complete!${NC}"
echo ""
echo -e "  ${BOLD}Your credentials:${NC}"
echo -e "  ───────────────────────────────────────────"
echo -e "  API Key:        ${GREEN}${API_KEY}${NC}"
echo -e "  Email:          ${EMAIL}"
echo -e "  Password:       (the password you entered)"
echo ""
echo -e "  ${BOLD}Where to go:${NC}"
echo -e "  Webmail:        http://localhost:8880"
echo -e "  API Docs:       http://localhost:8083/api-docs"
echo -e "  API Reference:  http://localhost:8083/api-reference"
echo ""
echo -e "  ${BOLD}Test the API:${NC}"
echo -e "  curl http://localhost:8083/api/v1/domains/ -H 'X-API-Key: ${API_KEY}'"
echo ""
echo -e "  ${BOLD}IMAP/SMTP (email clients):${NC}"
echo -e "  IMAP:  localhost:993 (SSL)    SMTP: localhost:587 (STARTTLS)"
echo -e "  User:  ${EMAIL}"
echo ""

# Save API key to a file for convenience
echo "${API_KEY}" > "$PROJECT_ROOT/.api-key"
echo -e "  ${YELLOW}[i]${NC} API key saved to .api-key (add to .gitignore)"
echo ""
