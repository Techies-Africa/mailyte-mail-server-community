#!/bin/bash
set -e

echo "=== Dovecot Entrypoint ==="

# doveadm HTTP API key (SMTP API keys K6) -- substituted into dovecot.conf
# before the DB substitutions below. The sentinel is __NAME__ rather than
# ${NAME} because dovecot.conf is parsed by Dovecot's own config parser,
# which expands ${...} itself and fails on the placeholder. An unset key
# leaves the listener answering 401 to everything, which degrades cache
# flushing without opening an unauthenticated admin surface.
sed -i -e "s|__DOVEADM_API_KEY__|${DOVEADM_API_KEY:-}|g" /etc/dovecot/dovecot.conf

# -------------------------------------------------------
# 0. Substitute environment variables in SQL config
# -------------------------------------------------------
if [ -f /etc/dovecot/dovecot-sql.conf.ext ]; then
    sed -i \
        -e "s|\${DB_HOST}|${DB_HOST:-mysql}|g" \
        -e "s|\${DB_PORT}|${DB_PORT:-3306}|g" \
        -e "s|\${DB_NAME}|${DB_NAME:-mailserver}|g" \
        -e "s|\${DB_USER}|${DB_USER:-mailuser}|g" \
        -e "s|\${DB_PASSWORD}|${DB_PASSWORD:-mailpassword}|g" \
        /etc/dovecot/dovecot-sql.conf.ext
    chmod 640 /etc/dovecot/dovecot-sql.conf.ext
    chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext
    echo "SQL config: DB_HOST=${DB_HOST:-mysql} DB_PORT=${DB_PORT:-3306} DB_NAME=${DB_NAME:-mailserver}"
fi

# Same substitution for the SMTP-credential-only passdb (SMTP-credentials
# workstream) -- a separate file, checked only for SMTP AUTH, never IMAP/POP3.
if [ -f /etc/dovecot/dovecot-sql-smtp.conf.ext ]; then
    sed -i \
        -e "s|\${DB_HOST}|${DB_HOST:-mysql}|g" \
        -e "s|\${DB_PORT}|${DB_PORT:-3306}|g" \
        -e "s|\${DB_NAME}|${DB_NAME:-mailserver}|g" \
        -e "s|\${DB_USER}|${DB_USER:-mailuser}|g" \
        -e "s|\${DB_PASSWORD}|${DB_PASSWORD:-mailpassword}|g" \
        /etc/dovecot/dovecot-sql-smtp.conf.ext
    chmod 640 /etc/dovecot/dovecot-sql-smtp.conf.ext
    chown root:dovecot /etc/dovecot/dovecot-sql-smtp.conf.ext
fi

# -------------------------------------------------------
# 1. SSL Certificate Setup
# -------------------------------------------------------

# Check for certificates from shared volume mount (cert_manager writes here)
if [ -f /etc/ssl/certs/custom/server.crt ] && [ -f /etc/ssl/private/custom/server.key ]; then
    echo "Using certificates from shared volume"
    cp /etc/ssl/certs/custom/server.crt /etc/ssl/certs/server.crt
    cp /etc/ssl/private/custom/server.key /etc/ssl/private/server.key
elif [ ! -f /etc/ssl/certs/server.crt ] || [ ! -f /etc/ssl/private/server.key ]; then
    echo "No certificates found — generating self-signed certificate for development"
    mkdir -p /etc/ssl/private
    openssl req -new -x509 -days 365 -nodes \
        -out /etc/ssl/certs/server.crt \
        -keyout /etc/ssl/private/server.key \
        -subj "/CN=${HOSTNAME:-mail.example.com}/O=Mailyte/C=GB" \
        2>/dev/null
fi

# Fix certificate permissions
chmod 644 /etc/ssl/certs/server.crt 2>/dev/null || true
chmod 600 /etc/ssl/private/server.key 2>/dev/null || true

# Generate DH params if not exists (should exist from Dockerfile build)
if [ ! -f /etc/dovecot/dh.pem ]; then
    echo "Generating DH parameters (this may take a moment)..."
    openssl dhparam -out /etc/dovecot/dh.pem 2048
fi

# -------------------------------------------------------
# 2. Set up Dovecot SNI config
# cert_manager writes /etc/ssl/sni/dovecot_sni.conf with local_name {} blocks:
#   local_name mail.clientdomain.com { ssl_cert = <...; ssl_key = <... }
# We copy it to /etc/dovecot/conf.d/sni.conf (included via !include_try).
# Dovecot reloads local_name blocks on SIGHUP — no restart needed.
# -------------------------------------------------------
mkdir -p /etc/ssl/sni /etc/dovecot/conf.d
if [ -f /etc/ssl/sni/dovecot_sni.conf ] && [ -s /etc/ssl/sni/dovecot_sni.conf ]; then
    cp /etc/ssl/sni/dovecot_sni.conf /etc/dovecot/conf.d/sni.conf
    BLOCK_COUNT=$(grep -c '^local_name' /etc/dovecot/conf.d/sni.conf 2>/dev/null || echo 0)
    echo "Dovecot SNI config loaded: ${BLOCK_COUNT} custom hostname blocks"
else
    # Create empty file so !include_try doesn't warn
    touch /etc/dovecot/conf.d/sni.conf
    echo "Dovecot SNI config: empty (no per-domain certs yet)"
fi

# -------------------------------------------------------
# 3. Fix Permissions
# -------------------------------------------------------

chown -R vmail:vmail /var/mail/vhosts 2>/dev/null || true
chown -R dovecot:dovecot /etc/dovecot/sieve 2>/dev/null || true

# -------------------------------------------------------
# 4. Create Sieve global directory structure
# -------------------------------------------------------

mkdir -p /etc/dovecot/sieve/global

# Compile default sieve script if it exists
if [ -f /etc/dovecot/sieve/default.sieve ]; then
    sievec /etc/dovecot/sieve/default.sieve 2>/dev/null || true
fi

echo "=== Dovecot configuration complete ==="

# Start supervisord
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
