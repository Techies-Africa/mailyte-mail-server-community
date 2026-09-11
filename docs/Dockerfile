FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    nginx \
    certbot \
    python3-certbot-nginx \
    cron \
    supervisor \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r /tmp/requirements.txt

# ── Build layout ──
# MkDocs wants:  /srv/mkdocs.yml  (config)
#                /srv/docs/       (markdown content)
#                /srv/overrides/  (theme overrides)
#                /srv/includes/   (snippets)
WORKDIR /srv

# Copy config file one level above docs/
COPY mkdocs.yml /srv/mkdocs.yml

# Copy markdown content into docs/ subdirectory
COPY index.md              /srv/docs/
COPY getting-started/      /srv/docs/getting-started/
COPY architecture/         /srv/docs/architecture/
COPY configuration/        /srv/docs/configuration/
COPY api/                  /srv/docs/api/
COPY features/             /srv/docs/features/
COPY monitoring/           /srv/docs/monitoring/
COPY deployment/           /srv/docs/deployment/
COPY guides/               /srv/docs/guides/
COPY reference/            /srv/docs/reference/
COPY development/          /srv/docs/development/
COPY security/             /srv/docs/security/
COPY mailer/               /srv/docs/mailer/
COPY worker/               /srv/docs/worker/
COPY stylesheets/          /srv/docs/stylesheets/
COPY javascripts/          /srv/docs/javascripts/

# Copy legacy top-level docs
# Legacy root docs removed — all content is in organized subdirectories

# Copy theme overrides and includes
COPY overrides/ /srv/overrides/
COPY includes/  /srv/includes/

# Copy operational files
COPY scripts/        /usr/local/bin/
COPY nginx/          /etc/nginx/
COPY supervisord.conf /etc/supervisor/conf.d/

# Set permissions
RUN chmod +x /usr/local/bin/*.sh 2>/dev/null || true
RUN chmod +x /usr/local/bin/*.py 2>/dev/null || true

# Create directories
RUN mkdir -p /var/log/supervisor /var/log/nginx /var/log/docs-ssl /etc/ssl/docs /app/site

# Remove default nginx config
RUN rm -f /etc/nginx/sites-enabled/default

# Default nginx config serving built docs
RUN echo 'server { \n\
    listen 80; \n\
    server_name _; \n\
    root /app/site; \n\
    index index.html; \n\
    \n\
    location / { \n\
        try_files $uri $uri/ $uri/index.html =404; \n\
    } \n\
    \n\
    location ~* \\.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ { \n\
        expires 1y; \n\
        add_header Cache-Control "public, immutable"; \n\
    } \n\
    \n\
    location /health { \n\
        access_log off; \n\
        return 200 "healthy\\n"; \n\
        add_header Content-Type text/plain; \n\
    } \n\
}' > /etc/nginx/sites-enabled/docs.conf

# Initialize a git repo so git-revision-date plugin doesn't crash
RUN git init /srv && git -C /srv add -A && git -C /srv -c user.name="build" -c user.email="build@mailyte.com" commit -m "docs build" --allow-empty 2>/dev/null || true

# Build at startup (ensures all files are present after COPY)
RUN mkdir -p /app/site
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80

CMD ["/entrypoint.sh"]
