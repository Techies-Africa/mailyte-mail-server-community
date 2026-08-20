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
# --timeout/--retries per conventions §5 -- a full CE build runs every service
# in parallel and saturates the link, and a pip timeout here fails the build.
RUN pip3 install --timeout 120 --retries 10 -r /tmp/requirements.txt

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

# Health check — verify the cert_manager process is running.
#
# NOT pgrep (conventions §3): pgrep ships in `procps`, which neither this
# Dockerfile nor anything it pulls in installs, so the previous
# `pgrep -f cert_manager.py` exited 127 on every probe and left this container
# permanently `unhealthy`. Nothing in docker-compose.yml depends_on it, which
# is the only reason that went unnoticed.
#
# NOT §3's socket probe either, and this is the one service where that is
# genuinely inapplicable: cert_manager runs no server. It is a scheduling loop
# (CERT_CHECK_INTERVAL) that shells out to certbot, and certbot binds :80 only
# for the few seconds an HTTP-01 challenge is in flight -- a socket probe would
# report unhealthy ~100% of the time. A /proc scan is the closest faithful
# equivalent to the original intent, using the python3 this image already
# installs rather than adding a package for pgrep's sake.
#
# A pid can vanish between the glob and the read, which would raise and score
# one failed probe. This container runs two long-lived processes (supervisord
# and cert_manager.py) plus an occasional short certbot child, so that is close
# to unreachable, and `--retries=3` bounds it either way: three consecutive
# failures are needed to mark the container unhealthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import glob,sys; sys.exit(0 if any(b'cert_manager.py' in open(f,'rb').read() for f in glob.glob('/proc/[0-9]*/cmdline')) else 1)" || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
