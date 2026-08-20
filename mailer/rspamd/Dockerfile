
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && \
    apt-get install -y \
    rspamd \
    supervisor \
    redis-tools \
    rsyslog \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy configuration files
COPY config/ /etc/rspamd/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# rsyslog's default config (Ubuntu package default, not ours) loads imklog
# to read the *host* kernel's log ring buffer via /proc/kmsg -- meaningless
# inside a container (there's no container-local kernel log) and exactly
# the kind of host-visibility a container shouldn't have regardless of
# capabilities. It fails under cap_drop: ALL (H1); the fix is disabling
# the module, not re-granting CAP_SYSLOG to read host kernel messages.
RUN sed -i 's/^module(load="imklog".*/#&/' /etc/rsyslog.conf

# Create necessary directories
RUN mkdir -p /var/log/supervisor \
             /var/log/rspamd \
             /var/lib/rspamd \
             /var/lib/rspamd/dkim

# Set permissions
RUN chown -R _rspamd:_rspamd /var/lib/rspamd
RUN chown -R _rspamd:_rspamd /var/log/rspamd

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD rspamc stat || exit 1

EXPOSE 11332 11334

ENTRYPOINT ["/entrypoint.sh"]
