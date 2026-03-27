#!/bin/bash
set -e

echo "=== Postfix Entrypoint ==="

# -------------------------------------------------------
# 1. Substitute environment variables in MySQL config files
# -------------------------------------------------------
for f in /etc/postfix/mysql-*.cf; do
    if [ -f "$f" ]; then
        envsubst < "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
        chmod 640 "$f"
        chown root:postfix "$f"
    fi
done

# -------------------------------------------------------
# 2. Set Postfix configuration from environment variables
#    Using postconf avoids shell-syntax issues in main.cf
# -------------------------------------------------------

# Server identification
postconf -e "myhostname=${HOSTNAME:-mail.example.com}"
MYDOMAIN=$(echo "${HOSTNAME:-mail.example.com}" | sed 's/^[^.]*\.//')
postconf -e "mydomain=${MYDOMAIN}"
postconf -e "myorigin=\$mydomain"

# Docker network — trust the bridge subnet and localhost
postconf -e "mynetworks=127.0.0.0/8 [::1]/128 172.20.0.0/16"

# Dev mode — disable DNS-based checks that hang in Docker
if [ "${POSTFIX_DEV_MODE:-false}" = "true" ]; then
    echo "DEV MODE: disabling postscreen DNSBL, RBL client checks, and DNS lookups"
    postconf -e "postscreen_dnsbl_sites ="
    postconf -e "postscreen_dnsbl_action = ignore"
    postconf -e "postscreen_greet_wait = 1s"
    postconf -e "smtpd_peername_lookup = no"
    postconf -e "disable_dns_lookups = yes"
    postconf -e "smtpd_client_restrictions = permit_mynetworks, permit_sasl_authenticated, reject_unauth_pipelining"
fi

# TLS overrides (only apply if env var is explicitly set)
[ -n "$POSTFIX_TLS_SECURITY_LEVEL" ]       && postconf -e "smtpd_tls_security_level=$POSTFIX_TLS_SECURITY_LEVEL"
[ -n "$POSTFIX_SMTP_TLS_SECURITY_LEVEL" ]  && postconf -e "smtp_tls_security_level=$POSTFIX_SMTP_TLS_SECURITY_LEVEL"
[ -n "$POSTFIX_TLS_AUTH_ONLY" ]            && postconf -e "smtpd_tls_auth_only=$POSTFIX_TLS_AUTH_ONLY"
[ -n "$POSTFIX_TLS_PROTOCOLS" ]            && postconf -e "smtpd_tls_protocols=$POSTFIX_TLS_PROTOCOLS"
[ -n "$POSTFIX_TLS_CIPHERS" ]              && postconf -e "smtpd_tls_ciphers=$POSTFIX_TLS_CIPHERS"
[ -n "$POSTFIX_TLS_EXCLUDE_CIPHERS" ]      && postconf -e "smtpd_tls_exclude_ciphers=$POSTFIX_TLS_EXCLUDE_CIPHERS"
[ -n "$POSTFIX_TLS_LOG_LEVEL" ]            && postconf -e "smtpd_tls_loglevel=$POSTFIX_TLS_LOG_LEVEL"
[ -n "$POSTFIX_TLS_CACHE_TIMEOUT" ]        && postconf -e "smtpd_tls_session_cache_timeout=$POSTFIX_TLS_CACHE_TIMEOUT"

# Rate limiting overrides
[ -n "$POSTFIX_CONNECTION_RATE_LIMIT" ]  && postconf -e "smtpd_client_connection_rate_limit=$POSTFIX_CONNECTION_RATE_LIMIT"
[ -n "$POSTFIX_CONNECTION_COUNT_LIMIT" ] && postconf -e "smtpd_client_connection_count_limit=$POSTFIX_CONNECTION_COUNT_LIMIT"
[ -n "$POSTFIX_MESSAGE_RATE_LIMIT" ]     && postconf -e "smtpd_client_message_rate_limit=$POSTFIX_MESSAGE_RATE_LIMIT"
[ -n "$POSTFIX_RECIPIENT_RATE_LIMIT" ]   && postconf -e "smtpd_client_recipient_rate_limit=$POSTFIX_RECIPIENT_RATE_LIMIT"
[ -n "$POSTFIX_RATE_TIME_UNIT" ]         && postconf -e "anvil_rate_time_unit=$POSTFIX_RATE_TIME_UNIT"

# Timeout overrides
[ -n "$POSTFIX_SMTP_TIMEOUT" ]          && postconf -e "smtpd_timeout=$POSTFIX_SMTP_TIMEOUT"
[ -n "$POSTFIX_HELO_TIMEOUT" ]          && postconf -e "smtpd_helo_timeout=$POSTFIX_HELO_TIMEOUT"
[ -n "$POSTFIX_MAIL_TIMEOUT" ]          && postconf -e "smtpd_mail_timeout=$POSTFIX_MAIL_TIMEOUT"
[ -n "$POSTFIX_RCPT_TIMEOUT" ]          && postconf -e "smtpd_rcpt_timeout=$POSTFIX_RCPT_TIMEOUT"
[ -n "$POSTFIX_DATA_TIMEOUT" ]          && postconf -e "smtpd_data_timeout=$POSTFIX_DATA_TIMEOUT"
[ -n "$POSTFIX_CLIENT_IDLE_TIMEOUT" ]   && postconf -e "smtpd_client_idle_timeout=$POSTFIX_CLIENT_IDLE_TIMEOUT"

# Error handling overrides
[ -n "$POSTFIX_SOFT_ERROR_LIMIT" ]      && postconf -e "smtpd_soft_error_limit=$POSTFIX_SOFT_ERROR_LIMIT"
[ -n "$POSTFIX_HARD_ERROR_LIMIT" ]      && postconf -e "smtpd_hard_error_limit=$POSTFIX_HARD_ERROR_LIMIT"
[ -n "$POSTFIX_JUNK_COMMAND_LIMIT" ]    && postconf -e "smtpd_junk_command_limit=$POSTFIX_JUNK_COMMAND_LIMIT"
[ -n "$POSTFIX_DNS_TIMEOUT" ]           && postconf -e "smtp_dns_timeout=$POSTFIX_DNS_TIMEOUT"

# -------------------------------------------------------
# 3. SSL Certificate Setup
# -------------------------------------------------------

# Check for certificates from shared volume mount (cert_manager writes here)
mkdir -p /etc/ssl/private
if [ -f /etc/ssl/certs/custom/server.crt ] && [ -f /etc/ssl/private/custom/server.key ]; then
    echo "Using certificates from shared volume"
    cp /etc/ssl/certs/custom/server.crt /etc/ssl/certs/server.crt
    cp /etc/ssl/private/custom/server.key /etc/ssl/private/server.key
    chmod 600 /etc/ssl/private/server.key
elif [ ! -f /etc/ssl/certs/server.crt ] || [ ! -f /etc/ssl/private/server.key ]; then
    echo "No certificates found — generating self-signed certificate for development"
    openssl req -new -x509 -days 365 -nodes \
        -out /etc/ssl/certs/server.crt \
        -keyout /etc/ssl/private/server.key \
        -subj "/CN=${HOSTNAME:-mail.example.com}/O=Mailyte/C=GB" \
        2>/dev/null
    chmod 600 /etc/ssl/private/server.key
fi

# -------------------------------------------------------
# 4. Build Postfix SNI cert map
# cert_manager writes /etc/ssl/sni/postfix_sni.map with:
#   hostname /path/to/key /path/to/cert
# We copy it to /etc/postfix/sni_certs.map and build the hash db.
# Postfix reloads this on SIGHUP — no restart needed after cert changes.
# -------------------------------------------------------
mkdir -p /etc/ssl/sni
if [ -f /etc/ssl/sni/postfix_sni.map ] && [ -s /etc/ssl/sni/postfix_sni.map ]; then
    cp /etc/ssl/sni/postfix_sni.map /etc/postfix/sni_certs.map
    postmap -F hash:/etc/postfix/sni_certs.map
    ENTRY_COUNT=$(grep -c '^[^#]' /etc/postfix/sni_certs.map 2>/dev/null || echo 0)
    echo "SNI cert map loaded: ${ENTRY_COUNT} hostname entries"
else
    # Create empty map so Postfix starts without error
    touch /etc/postfix/sni_certs.map
    postmap -F hash:/etc/postfix/sni_certs.map
    echo "SNI cert map: empty (no per-domain certs yet)"
fi

# -------------------------------------------------------
# 5. Set up chroot jail prerequisites
# -------------------------------------------------------
mkdir -p /var/spool/postfix/etc/ssl/certs

# DNS resolution inside chroot
cp /etc/resolv.conf    /var/spool/postfix/etc/resolv.conf    2>/dev/null || true
cp /etc/nsswitch.conf  /var/spool/postfix/etc/nsswitch.conf  2>/dev/null || true
cp /etc/services       /var/spool/postfix/etc/services       2>/dev/null || true
cp /etc/hosts          /var/spool/postfix/etc/hosts          2>/dev/null || true

# CA certificates for TLS verification inside chroot
cp /etc/ssl/certs/ca-certificates.crt /var/spool/postfix/etc/ssl/certs/ 2>/dev/null || true

# -------------------------------------------------------
# 5. Fix permissions
# -------------------------------------------------------
chown -R vmail:vmail /var/mail/vhosts 2>/dev/null || true
postfix set-permissions 2>/dev/null || true

# -------------------------------------------------------
# 6. Generate aliases database
# -------------------------------------------------------
newaliases 2>/dev/null || true

# -------------------------------------------------------
# 7. Prepare log files for tracking injector
# -------------------------------------------------------
touch /var/log/postfix_tracking.log
chown vmail:vmail /var/log/postfix_tracking.log
chmod 644 /var/log/postfix_tracking.log

echo "=== Postfix configuration complete ==="

# Start supervisord
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
