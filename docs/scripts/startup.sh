#!/bin/bash
set -e

echo "Starting Documentation Service..."

# Environment variables
export DOCS_DOMAIN=${DOCS_DOMAIN:-"docs.mailyte.com"}
export ACME_EMAIL=${ACME_EMAIL:-"admin@mailyte.com"}
export ACME_STAGING=${ACME_STAGING:-"false"}

echo "Configuration:"
echo "  Domain: $DOCS_DOMAIN"
echo "  ACME Email: $ACME_EMAIL"
echo "  Staging: $ACME_STAGING"

# Create necessary directories
mkdir -p /var/log/docs-ssl
mkdir -p /var/www/html
mkdir -p /etc/ssl/docs

# Build documentation
echo "Building documentation..."
/usr/local/bin/build-docs.sh

# In production mode with a real domain, handle SSL
if [ "$ACME_STAGING" = "false" ] && [ "$DOCS_DOMAIN" != "localhost" ]; then
    if [ ! -f "/etc/letsencrypt/live/$DOCS_DOMAIN/fullchain.pem" ]; then
        echo "Setting up initial SSL certificate..."
        python3 /usr/local/bin/docs-ssl-manager.py setup
    else
        echo "SSL certificate exists, checking validity..."
        python3 /usr/local/bin/docs-ssl-manager.py renew
    fi
else
    echo "Running in local/staging mode — skipping SSL setup"
fi

echo "Documentation service startup completed"
