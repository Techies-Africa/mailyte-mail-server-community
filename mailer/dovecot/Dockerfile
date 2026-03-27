FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && \
    apt-get install -y \
    dovecot-core \
    dovecot-imapd \
    dovecot-pop3d \
    dovecot-lmtpd \
    dovecot-mysql \
    dovecot-sieve \
    dovecot-managesieved \
    supervisor \
    python3 \
    python3-pip \
    mysql-client \
    rsyslog \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install mysql-connector-python requests redis

# Create vmail user and group (uid/gid 5000 — must match Postfix)
RUN groupadd -g 5000 vmail && \
    useradd -u 5000 -g vmail -d /var/mail/vhosts -s /usr/sbin/nologin -r vmail

# Create directory structure
RUN mkdir -p /var/mail/vhosts && \
    chown -R vmail:vmail /var/mail/vhosts && \
    chmod 770 /var/mail/vhosts

RUN mkdir -p /var/log/supervisor /var/log/dovecot

# Create Sieve directories
RUN mkdir -p /etc/dovecot/sieve/global

# Generate DH parameters (2048-bit, takes ~30s during build)
RUN openssl dhparam -out /etc/dovecot/dh.pem 2048

# Copy configuration and scripts
COPY config/ /etc/dovecot/
COPY scripts/ /usr/local/bin/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Set permissions
RUN chmod +x /usr/local/bin/*.py 2>/dev/null || true
RUN chmod +x /usr/local/bin/*.sh 2>/dev/null || true

# Compile the default Sieve script (smart folder routing + spam filtering)
# The .sieve file is copied from config/sieve/ by COPY config/ above
RUN sievec /etc/dovecot/sieve/default.sieve 2>/dev/null || true

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD doveadm service status 2>/dev/null || dovecot status || exit 1

EXPOSE 143 993 110 995 24 4190

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
