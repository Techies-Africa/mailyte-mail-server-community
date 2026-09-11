#!/bin/bash
################################################################################
# Mailyte Email Server — First User Setup
# Creates your first organization, domain, mailbox, and API key via the API's
# one-time bootstrap endpoint (POST /api/v1/bootstrap). This script no longer
# writes to MySQL directly -- see worker/api/routes/bootstrap.py.
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

API_BASE="${API_BASE:-http://localhost:8083}"

# Check the API service is up -- this script now talks only to the API,
# never to the database directly.
# Matched on the SERVICE name, not the container name. Container names carry
# CONTAINER_PREFIX (set when running two editions on one host), so a
# `grep "^api$"` against {{.Name}} reported "services are not running" while the
# whole stack was healthy.
if ! docker compose ps --services --filter status=running 2>/dev/null | grep -q "^api$"; then
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

read -p "  Admin email address (e.g., admin@yourdomain.com): " EMAIL
if [ -z "$EMAIL" ]; then
    echo -e "  ${RED}[!!]${NC} Email address is required"
    exit 1
fi
if [[ "$EMAIL" != *"@"*"."* ]]; then
    echo -e "  ${RED}[!!]${NC} Email address must be in the form user@yourdomain.com"
    exit 1
fi
# The domain is derived from the email, not asked separately -- that is the
# only domain the bootstrap endpoint accepts, so a separately-typed domain
# could silently mismatch the one actually created.
DOMAIN_NAME="${EMAIL#*@}"

read -sp "  Password for ${EMAIL}: " PASSWORD
echo ""
if [ -z "$PASSWORD" ]; then
    echo -e "  ${RED}[!!]${NC} Password is required"
    exit 1
fi
if [ "${#PASSWORD}" -lt 8 ]; then
    echo -e "  ${RED}[!!]${NC} Password must be at least 8 characters"
    exit 1
fi

echo ""
echo -e "  ${CYAN}Setting up via the API...${NC}"

# Read the single-use bootstrap token the API wrote at startup. This is the
# only way in -- the bootstrap paradox: you need an API key to call the API,
# but a fresh install has none yet. See worker/api/app.py's startup hook and
# worker/api/routes/bootstrap.py. The token is refused as soon as any
# organization exists, so this only ever works once.
TOKEN=$(docker compose exec -T api cat /app/data/bootstrap-token 2>/dev/null | tr -d '\r\n')

if [ -z "$TOKEN" ]; then
    echo -e "  ${RED}[!!]${NC} No bootstrap token is available."
    echo "       Either an organization already exists (already set up), or"
    echo "       the API hasn't finished starting yet. Check: docker compose logs api"
    exit 1
fi

RESPONSE_FILE=$(mktemp)
HTTP_CODE=$(curl -sS -o "$RESPONSE_FILE" -w "%{http_code}" -X POST "${API_BASE}/api/v1/bootstrap/" \
    -H "X-Bootstrap-Token: ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"organization_name\":\"$(printf '%s' "$ORG_NAME" | sed 's/"/\\"/g')\",\"admin_email\":\"${EMAIL}\",\"admin_password\":\"$(printf '%s' "$PASSWORD" | sed 's/"/\\"/g')\"}")
BODY=$(cat "$RESPONSE_FILE")
rm -f "$RESPONSE_FILE"

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}[!!]${NC} Bootstrap failed (HTTP ${HTTP_CODE}):"
    echo "  $BODY"
    exit 1
fi

API_KEY=$(python3 -c "import sys, json; print(json.load(sys.stdin)['data']['api_key'])" <<< "$BODY" 2>/dev/null \
    || echo "$BODY" | grep -o '"api_key"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*"([^"]+)"$/\1/')

if [ -z "$API_KEY" ]; then
    echo -e "  ${RED}[!!]${NC} Bootstrap succeeded but no API key was found in the response:"
    echo "  $BODY"
    exit 1
fi

echo -e "  ${GREEN}[OK]${NC} Organization: ${ORG_NAME}"
echo -e "  ${GREEN}[OK]${NC} Domain: ${DOMAIN_NAME}"
echo -e "  ${GREEN}[OK]${NC} Mailbox: ${EMAIL}"
echo -e "  ${GREEN}[OK]${NC} API Key: ${API_KEY}"

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
echo -e "  ${YELLOW}[i]${NC} API key saved to .api-key (gitignored)"
echo ""
