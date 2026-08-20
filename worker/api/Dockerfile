FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY . .

# H1 (00-foundation/07-security-model.md): non-root runtime user, ported from
# mailyte-email-server.
#
# CE-specific addition -- `mkdir -p /app/data` before the chown. app.py writes
# the single-use operator bootstrap token to /app/data/operator-bootstrap-token
# (0600) at startup and os.remove()s it once consumed, and /app itself is
# bind-mounted from the host source tree by docker-compose.yml, so UID 10001
# cannot create a new subdirectory under it. EE solves this with a host
# directory (./storage/api_data) that its deploy.sh pre-creates and chowns to
# 10001; CE has no equivalent deploy step -- a self-hoster just runs
# `docker compose up` -- so CE mounts a Docker-managed named volume over this
# path instead. Creating the directory here, owned by mailyte, is what gives
# that volume the right ownership on first use.
RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && mkdir -p /app/data \
 && chown -R mailyte:mailyte /app
USER mailyte

EXPOSE 8080

CMD ["python", "app.py"]
