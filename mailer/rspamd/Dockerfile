
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

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
