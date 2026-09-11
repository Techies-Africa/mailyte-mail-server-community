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
# The Docker network Postfix's sibling containers actually sit on. This was
# hardcoded to 172.20.0.0/16 while docker-compose.yml declares the network as
# 172.25.0.0/16, so permit_mynetworks never matched an internal container --
# which is the rule main.cf relies on for "internal containers deliver to
# LOCAL domains". Nothing noticed until Dovecot's Sieve `redirect` needed to
# hand a forwarded message back to Postfix and was rejected.
#
# Overridable so it cannot drift from the compose file again.
postconf -e "mynetworks=127.0.0.0/8 [::1]/128 ${POSTFIX_MYNETWORKS:-172.25.0.0/16}"

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
# 2b. Marketing sending stream (SMTP-Send phase-01)
# -------------------------------------------------------
# The whole stream is gated on MARKETING_BIND_IP: unset (the default), none of
# these postconf calls run, sender_dependent_default_transport_maps stays
# unset, and routing is byte-identical to before this block existed. The
# `marketing` master.cf service exists either way but nothing can reach it.
#
# smtp_bind_address is set PER-SERVICE with postconf -P, never globally -- a
# global value would move mailbox/transactional traffic onto the marketing IP,
# which is the exact opposite of the invariant this stream exists to enforce.
if [ -n "$MARKETING_BIND_IP" ]; then
    # Which address the transport binds depends on how this container is
    # networked. On a Docker BRIDGE network (production) the public marketing
    # IP is NOT in the container's namespace -- smtp_bind_address to it is a
    # silent no-op and Docker's MASQUERADE rewrites every outbound source to
    # the host's primary IP. Verified the hard way on 2026-09-06: Gmail
    # headers showed "relay.mailyte.com (courier.mailyte.com. [66.29.133.223])"
    # after postconf claimed the bind was set.
    #
    # So in bridge mode the stream binds a SECOND, bridge-internal address
    # (MARKETING_CONTAINER_BIND_IP, added here -- needs cap NET_ADMIN) and a
    # host nat rule SNATs exactly that source to MARKETING_BIND_IP:
    #   iptables -t nat -I POSTROUTING 1 -s $MARKETING_CONTAINER_BIND_IP \
    #     ! -o <bridge> -j SNAT --to-source $MARKETING_BIND_IP
    # (installed by deployment/marketing-egress/, see the activation runbook).
    # Transactional/mailbox mail keeps the container's primary address and
    # still falls through to MASQUERADE -- the invariant is preserved at the
    # NAT layer, not just in postconf. With MARKETING_CONTAINER_BIND_IP unset
    # (host-network deployments) the public IP is bound directly as before.
    if [ -n "$MARKETING_CONTAINER_BIND_IP" ]; then
        # "File exists" on a restart is the idempotent no-op, not an error.
        ip addr add "${MARKETING_CONTAINER_BIND_IP}/32" dev eth0 2>/dev/null || true
        if ip -4 addr show dev eth0 | grep -q "inet ${MARKETING_CONTAINER_BIND_IP}/"; then
            BIND_ADDR="$MARKETING_CONTAINER_BIND_IP"
            echo "Marketing stream ENABLED: bridge alias $BIND_ADDR added; host SNAT maps it to $MARKETING_BIND_IP"
        else
            # Fail LOUD, not open: binding the public IP here would silently
            # send marketing mail from the primary IP with the marketing HELO
            # -- a HELO/PTR mismatch on every message.
            echo "ERROR: could not add marketing alias $MARKETING_CONTAINER_BIND_IP to eth0 (missing NET_ADMIN or iproute2?); marketing transport left unbound" >&2
            BIND_ADDR=""
        fi
    else
        BIND_ADDR="$MARKETING_BIND_IP"
        echo "Marketing stream ENABLED: binding marketing transport directly to $BIND_ADDR (host networking)"
    fi
    [ -n "$BIND_ADDR" ] && postconf -P "marketing/unix/smtp_bind_address=$BIND_ADDR"
    [ -n "$MARKETING_HELO_NAME" ] && postconf -P "marketing/unix/smtp_helo_name=$MARKETING_HELO_NAME"
    # The marketing source bind is IPv4-only, so every IPv6 MX dial fails with
    # "Network is unreachable" before falling back (observed live 2026-09-08:
    # one wasted dial per marketing delivery). Prefer v4 outright.
    postconf -P "marketing/unix/smtp_address_preference=ipv4"

    # Only route marketing-flagged orgs to the transport when it is actually
    # bound to its own source address. An unbound marketing transport would
    # still be reachable through the map and would send from the primary IP
    # while announcing the marketing HELO -- the mismatch this whole block
    # exists to prevent. Unwired, flagged orgs simply keep using the default
    # transport until the alias problem is fixed.
    if [ -n "$BIND_ADDR" ]; then
        postconf -e "sender_dependent_default_transport_maps=proxy:mysql:/etc/postfix/mysql-marketing-transport.cf"

        # trivial-rewrite runs chrooted (master.cf) and cannot open a MySQL
        # connection from the jail, so the map must be readable via proxymap --
        # same reason the four existing mysql maps are proxy:-prefixed. Append to
        # the existing allowlist; bash keeps the $-references literal for postconf.
        current_proxy_read_maps=$(postconf -h proxy_read_maps)
        postconf -e "proxy_read_maps = ${current_proxy_read_maps} \$sender_dependent_default_transport_maps"
    else
        echo "Marketing stream NOT wired: transport has no bound source address; flagged orgs stay on the default transport" >&2
    fi
fi

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

# ip_access_policy.py (master.cf's policy-ip-access spawn service) runs as
# vmail and opens this log with logging.FileHandler (append mode) at
# import time -- confirmed live: Docker had already auto-created
# /var/log/postfix/ip_access_policy.log as root:root before this ran,
# vmail couldn't append to it, and the resulting unhandled PermissionError
# crashed the script on every single RCPT (spawn: "exit status 1"),
# Postfix correctly failing closed with "451 4.3.5 ... Server
# configuration problem" -- rejecting real inbound mail, not just a log
# nicety.
touch /var/log/postfix/ip_access_policy.log
chown vmail:vmail /var/log/postfix/ip_access_policy.log
chmod 644 /var/log/postfix/ip_access_policy.log

# -------------------------------------------------------
# 8. Runtime config file for pipe/spawn child scripts
# -------------------------------------------------------
# tracking_injector.py (pipe) and ip_access_policy.py (spawn) both read
# DB_HOST/DB_USER/DELIVERY_OPTIMIZER_URL/etc. via os.getenv() -- but
# Postfix's pipe(8)/spawn(8) daemons hand the external command they exec
# a hardcoded minimal environment (LANG, MAIL_CONFIG, PATH, LC_CTYPE) by
# design, REGARDLESS of main.cf's import_environment setting. Confirmed
# live by dumping os.environ from inside both scripts as Postfix actually
# invoked them -- adding names to import_environment (which does work for
# other Postfix daemon types) had zero effect here; this is a real,
# documented pipe(8)/spawn(8) constraint, not a config mistake. Writing
# the real values to a plain KEY=VALUE file both scripts load explicitly
# at their own startup (see their own env-file-loading code) is the
# actual fix -- this step runs with the container's full real
# environment, unlike the scripts once Postfix execs them.
cat > /etc/postfix/runtime.env <<EOF
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_TIMEOUT=${DB_TIMEOUT}
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
TRACKING_SERVICE_URL=${TRACKING_SERVICE_URL}
TRACKING_ENABLED=${TRACKING_ENABLED}
TRACKING_API_TIMEOUT=${TRACKING_API_TIMEOUT}
TRACKING_WEBHOOK_URL=${TRACKING_WEBHOOK_URL}
TRACKING_WEBHOOK_TIMEOUT=${TRACKING_WEBHOOK_TIMEOUT}
DELIVERY_OPTIMIZER_URL=${DELIVERY_OPTIMIZER_URL}
DELIVERY_OPTIMIZER_TIMEOUT=${DELIVERY_OPTIMIZER_TIMEOUT}
DELIVERY_OPTIMIZER_FALLBACK_DELAY=${DELIVERY_OPTIMIZER_FALLBACK_DELAY}
DEFAULT_ORGANIZATION_ID=${DEFAULT_ORGANIZATION_ID}
DEFAULT_DOMAIN_ID=${DEFAULT_DOMAIN_ID}
WEBHOOK_SECRET=${WEBHOOK_SECRET}
HOSTNAME=${HOSTNAME}
EOF
chmod 640 /etc/postfix/runtime.env
chown root:vmail /etc/postfix/runtime.env

echo "=== Postfix configuration complete ==="

# Start supervisord
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
