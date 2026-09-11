#!/bin/bash
set -e

echo "=== Cert Manager Entrypoint ==="

# /etc/ssl/certs is bind-mounted from storage/ssl_certs (see the
# Dockerfile's comment) -- restore the CA trust bundle certbot needs into
# it if the mounted directory doesn't already have one. Idempotent: the
# host directory persists across restarts, so this only ever copies once
# per fresh host directory.
if [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then
    cp /etc/ssl/ca-certificates.crt.bundled /etc/ssl/certs/ca-certificates.crt
    echo "Restored CA trust bundle into /etc/ssl/certs"
fi

echo "=== Cert Manager configuration complete ==="

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
