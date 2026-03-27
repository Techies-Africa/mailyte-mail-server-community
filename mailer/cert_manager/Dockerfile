FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && \
    apt-get install -y \
    certbot \
    supervisor \
    python3 \
    python3-pip \
    ca-certificates \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt

# Copy scripts and configuration
COPY scripts/ /usr/local/bin/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Set permissions
RUN chmod +x /usr/local/bin/*.py

# Create necessary directories
RUN mkdir -p /var/log/supervisor \
             /var/log/cert_manager \
             /etc/ssl/certs \
             /etc/ssl/private \
             /etc/letsencrypt

# Expose port 80 for certbot HTTP-01 challenge (standalone mode)
EXPOSE 80

# Health check — verify the cert_manager process is running
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f cert_manager.py || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
