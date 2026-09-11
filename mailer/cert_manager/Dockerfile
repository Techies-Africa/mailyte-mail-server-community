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
RUN pip3 install --no-cache-dir --timeout 120 --retries 10 -r /tmp/requirements.txt

# Copy scripts and configuration
COPY scripts/ /usr/local/bin/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set permissions
RUN chmod +x /usr/local/bin/*.py

# Create necessary directories
RUN mkdir -p /var/log/supervisor \
             /var/log/cert_manager \
             /etc/ssl/certs \
             /etc/ssl/private \
             /etc/letsencrypt

# /etc/ssl/certs is bind-mounted at runtime from storage/ssl_certs (this
# server's own issued-certificate output directory, shared with
# postfix/dovecot) -- that shadows the ca-certificates package's CA trust
# bundle this RUN step just installed at the same path, which certbot
# needs to verify Let's Encrypt's own TLS cert (confirmed live: certbot
# failed with "OSError: Could not find a suitable TLS CA certificate
# bundle, invalid path: /etc/ssl/certs/ca-certificates.crt" on every run).
# Stash a copy outside that path so entrypoint.sh can restore it into the
# mounted directory at container startup.
RUN cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/ca-certificates.crt.bundled

# Expose port 80 for certbot HTTP-01 challenge (standalone mode)
EXPOSE 80

# Health check — verify the cert_manager process is running
# conventions SS3: never pgrep. It lives in `procps`, which this image does
# not install, so this probe exited 127 on every run and cert_manager has been
# permanently `unhealthy` since the healthcheck was written. Nothing
# depends_on it, which is the only reason it went unnoticed.
#
# The socket probe SS3 recommends does not apply here: cert_manager runs no
# server. It is a CERT_CHECK_INTERVAL loop that shells out to certbot, which
# binds :80 only for the seconds a challenge takes. So this scans /proc with
# the python3 the image already has. The residual race -- the process could
# die between two probes -- is bounded by --retries=3.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import glob,sys; sys.exit(0 if any(b'cert_manager.py' in open(f,'rb').read() for f in glob.glob('/proc/[0-9]*/cmdline')) else 1)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
