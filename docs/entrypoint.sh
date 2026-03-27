#!/bin/bash
set -e

echo "Building documentation..."
cd /srv
git init . 2>/dev/null || true
git add -A 2>/dev/null || true
git -c user.name="build" -c user.email="build@mailyte.com" commit -m "build" --allow-empty 2>/dev/null || true

mkdocs build --config-file /srv/mkdocs.yml --site-dir /app/site 2>&1
chown -R www-data:www-data /app/site 2>/dev/null || true
chmod -R 755 /app/site 2>/dev/null || true

echo "Documentation built successfully. Starting nginx..."
exec nginx -g "daemon off;"
