#!/bin/bash
set -e

echo "Building documentation with Material for MkDocs..."

# Set environment variables
export DOCS_DOMAIN=${DOCS_DOMAIN:-"docs.mailyte.com"}
export SITE_NAME=${SITE_NAME:-"Mailyte Email Server Handbook"}
export SITE_DESCRIPTION=${SITE_DESCRIPTION:-"The complete developer handbook for the Mailyte Email Server"}

# Determine the docs source directory
# Priority: mounted volume > bundled copy
if [ -d "/app/docs-source" ] && [ -f "/app/docs-source/mkdocs.yml" ]; then
    DOCS_DIR="/app/docs-source"
elif [ -f "/app/mkdocs.yml" ]; then
    DOCS_DIR="/app"
else
    echo "ERROR: Cannot find mkdocs.yml in /app/docs-source or /app"
    exit 1
fi

echo "Using docs source: $DOCS_DIR"

# Change to docs directory
cd "$DOCS_DIR"

# Clean previous build
rm -rf /app/site/

# Build documentation
echo "Running mkdocs build..."
mkdocs build --config-file mkdocs.yml --site-dir /app/site

# Set proper permissions
chown -R www-data:www-data /app/site/ 2>/dev/null || true
chmod -R 755 /app/site/

echo "Documentation build completed successfully"
echo "Site built in: /app/site/"
echo "Total files: $(find /app/site/ -type f | wc -l)"
echo "Site size: $(du -sh /app/site/ | cut -f1)"
