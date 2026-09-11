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

# Sieve pipe programs (DR-2 inbound archive producer).
#
# sieve_pipe_bin_dir is a fixed directory that sieve_extprograms will only
# execute out of, and it refuses anything that is not executable -- a
# non-executable script here means the sieve action fails, which defers mail,
# so the chmod is load-bearing rather than tidiness.
RUN mkdir -p /usr/lib/dovecot/sieve-pipe
COPY scripts/sieve-pipe/ /usr/lib/dovecot/sieve-pipe/
RUN chmod 0755 /usr/lib/dovecot/sieve-pipe/* && \
    touch /var/log/dovecot/archive-pipe.log && \
    chown vmail:vmail /var/log/dovecot/archive-pipe.log

# Compile the default Sieve script (smart folder routing + spam filtering,
# and the DR-2 archive pipe). The .sieve file is copied from config/sieve/ by
# COPY config/ above.
#
# -P/-x are required, not decoration: vnd.dovecot.pipe is provided by the
# sieve_extprograms PLUGIN, and sievec does not read dovecot.conf, so without
# them it fails with "unknown extension" (exit 89) rather than producing a
# .svbin. Deliberately NOT silenced with `|| true`: a compile failure would
# leave the previous .svbin in place and inbound archiving silently absent,
# which is the failure mode this whole plan exists to eliminate.
RUN sievec -P sieve_extprograms -x "+vnd.dovecot.pipe" /etc/dovecot/sieve/default.sieve

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD doveadm service status 2>/dev/null || dovecot status || exit 1

EXPOSE 143 993 110 995 24 4190

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
