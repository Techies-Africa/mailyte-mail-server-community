FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && \
    apt-get install -y \
    postfix \
    postfix-mysql \
    postfix-pcre \
    supervisor \
    python3 \
    python3-pip \
    mysql-client \
    redis-tools \
    rsyslog \
    ca-certificates \
    gettext-base \
    openssl \
    # iproute2: the entrypoint adds a second bridge-internal address the
    # marketing transport binds to, so a host SNAT rule can give that stream
    # its own public egress IP (Docker MASQUERADE otherwise rewrites every
    # container's source to the host's primary address).
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# pymysql: tracking_injector.py imports pymysql specifically (not
# mysql.connector, which every other script in this codebase uses) --
# confirmed live: it was never installed here, so every tracking-injected
# outbound message logged "PyMySQL not available, using default
# organization/domain IDs" and lost real per-org/domain attribution.
RUN pip3 install mysql-connector-python requests redis pymysql

# Create vmail user and group (uid/gid 5000 — must match Dovecot)
RUN groupadd -g 5000 vmail && \
    useradd -u 5000 -g vmail -d /var/mail/vhosts -s /usr/sbin/nologin -r vmail

# Create mail storage directory structure
RUN mkdir -p /var/mail/vhosts && \
    chown -R vmail:vmail /var/mail/vhosts && \
    chmod 770 /var/mail/vhosts

# Copy configuration files
COPY config/ /etc/postfix/
COPY scripts/ /usr/local/bin/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Set permissions on scripts
RUN chmod +x /usr/local/bin/*.py 2>/dev/null || true
RUN chmod +x /usr/local/bin/*.sh 2>/dev/null || true

# Create necessary directories
RUN mkdir -p /var/log/supervisor /var/log/postfix /var/spool/postfix

# Prepare chroot jail directories
RUN mkdir -p /var/spool/postfix/etc/ssl/certs

# Fix spool permissions
RUN chown -R postfix:postfix /var/spool/postfix && \
    chmod 755 /var/spool/postfix

# Create empty header/body checks if they don't exist
RUN touch /etc/postfix/header_checks \
          /etc/postfix/body_checks \
          /etc/postfix/mime_header_checks

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD postfix status || exit 1

EXPOSE 25 587 465

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
