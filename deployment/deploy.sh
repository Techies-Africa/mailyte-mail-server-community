#!/bin/bash
# =============================================================================
# Mailyte Email Server - Production Deployment (DevPilot-driven)
# =============================================================================
# Modeled on the proven patterns already running in production for wizjob
# and informly (pinned compose project name, one-off migrate step, rolling
# updates for replicated services, scoped cleanup that never touches
# volumes) -- adapted for what makes mailyte-email-server structurally
# different from both: it's ~30 interdependent services (postfix, dovecot,
# mysql, rspamd, several stateful/singleton services that CANNOT be
# rolling-updated the way a stateless API container can) carrying real,
# currently-being-migrated production mail for real businesses. The two
# concrete data-loss risks this script exists to close:
#
# 1. DevPilot deploys into a fresh timestamped release directory each time
#    ({{RELEASE_PATH}}). Docker Compose's project name defaults to the
#    CURRENT DIRECTORY'S BASENAME when not pinned -- meaning every release
#    directory would look like a brand-new, unrelated project to Compose,
#    silently orphaning mysql_data/redis_data/rspamd_data/letsencrypt_data
#    (named volumes -- confirmed in docker-compose.yml's top-level
#    `volumes:` block) and colliding with the previous release's still-running
#    fixed container names (mysql, redis, migrate, docker-proxy, etc.).
#    Fix: EVERY compose invocation below is pinned to -p "$PROJECT_NAME",
#    never left to default.
#
# 2. storage/mail_data, storage/dkim_keys, storage/ssl_certs, and
#    secrets/{db_root_password,encryption_kek} are bind-mounted from the
#    release directory (see docker-compose.yml's volumes: sections) and are
#    all .gitignore'd -- a fresh git checkout in a fresh release directory
#    means these come up EMPTY unless something anchors them to a
#    persistent location outside the release path. encryption_kek
#    specifically is the key-encryption-key protecting DKIM private keys;
#    losing it makes existing encrypted secrets permanently unrecoverable,
#    not just "resettable."
#    Fix: this script assumes DevPilot's "Sync Directory" hook (see the
#    deployment guide this ships with) has already anchored `storage/` and
#    `secrets/` BEFORE this script runs (post_source_control, ahead of
#    everything here). It does not trust that blindly, though -- see
#    STEP 2 below.
#
# On top of both: a scripts/backup.sh --pre-deploy run before anything else, on
# every single deploy. If either protection above is ever misconfigured,
# there's still a real, fresh recovery point no more than minutes old.
#
# Usage:
#   ./deployment/deploy.sh
#
# Environment variables:
#   FORCE_REBUILD=1   Rebuild images from scratch (--no-cache --pull) instead
#                      of using layer cache.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PROJECT_NAME="mailyte-prod"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)
REPLICATED_SERVICES=(api webhooks tracking)

# --profile console on EVERY invocation, not just `up`. The console is declared
# behind a profile so an unpublished image cannot fail the whole stack, but a
# profile that is not activated is invisible to `docker compose config
# --services` -- which is exactly how `all_services` below is built. Without
# this the deploy would silently skip the console, and worse, the
# `up -d --remove-orphans` in step 5 would REMOVE it if it were running,
# because it would not appear in the explicit service list.
#
# Naming a profile no service uses is a no-op, so this stays correct if the
# profiles: key is ever dropped from docker-compose.yml.
dc() { docker compose -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" --profile console --profile webmail "$@"; }

log()  { echo "$(date '+%H:%M:%S') $1"; }

# Each step reports how long the PREVIOUS one took, and the run ends with a
# total. Without this, "the deploy is slow" is a guess -- with it, the log
# says which step to look at.
DEPLOY_STARTED_AT=$(date +%s)
STEP_STARTED_AT=$DEPLOY_STARTED_AT
CURRENT_STEP=""
step() {
    local now; now=$(date +%s)
    if [ -n "$CURRENT_STEP" ]; then
        echo "--- ${CURRENT_STEP} took $(( now - STEP_STARTED_AT ))s"
    fi
    CURRENT_STEP="$1"
    STEP_STARTED_AT=$now
    echo ""
    echo "=== $1 ==="
}
finish_timing() {
    local now; now=$(date +%s)
    [ -n "$CURRENT_STEP" ] && echo "--- ${CURRENT_STEP} took $(( now - STEP_STARTED_AT ))s"
    echo "--- TOTAL $(( now - DEPLOY_STARTED_AT ))s"
}
fail() { echo "FAILED: $1" >&2; exit 1; }

step "1/8 PRE-FLIGHT CHECKS"
[ -f docker-compose.yml ] || fail "docker-compose.yml not found -- run from the mailyte-email-server repo root"
[ -f .env ] || fail ".env not found -- the DevPilot 'Copy environment file' hook should run before this script"
docker info > /dev/null 2>&1 || fail "Docker is not accessible"

# Docker Compose auto-loads .env for ${VAR} interpolation inside compose
# files, but that does NOT export these as real shell environment
# variables -- scripts/backup.sh (and restore.sh, mailyte-ctl.sh) read
# $DB_PASSWORD etc. directly from the shell environment, not by parsing
# .env themselves. Without this, every one of those calls below silently
# runs with empty credentials (confirmed live: backup.sh logged "DB_PASSWORD
# is not set. Skipping MySQL backup." while still exiting 0, since it
# treats a single failed component as non-fatal -- easy to miss).
set -a
# shellcheck disable=SC1091
source .env
set +a

# Anchor logs/ outside the release directory.
#
# This used to be left per-release on the reasoning that logs don't need to
# survive a deploy. That stopped being true once Postfix got real logging
# (main.cf's maillog_file -> /var/log/postfix/mail.log, bind-mounted from
# logs/mailer/postfix): mail logs are the only record of deliveries,
# deferrals and bounces, and stranding them in an old release directory at
# every deploy is exactly when you least want a truncated history. Confirmed
# live -- three release directories each holding their own separate 400-500K
# of logs, with no continuity between them.
#
# Done here rather than as a DevPilot "Sync Directory" hook so it stays in
# the repo, reviewable next to the mkdir/chown logic below that depends on
# it. The shared root is derived from where storage/ already points rather
# than hardcoded, so this follows the deployment layout instead of assuming
# one. Existing release-local logs are moved in rather than deleted.
if [ ! -L logs ]; then
    SHARED_ROOT="$(dirname "$(readlink -f storage)")"
    SHARED_LOGS="$SHARED_ROOT/logs"
    if [ "$SHARED_LOGS" != "$PROJECT_ROOT/logs" ]; then
        sudo mkdir -p "$SHARED_LOGS"
        if [ -d logs ] && [ -n "$(ls -A logs 2>/dev/null)" ]; then
            sudo cp -an logs/. "$SHARED_LOGS"/ 2>/dev/null || true
        fi
        sudo rm -rf logs
        sudo ln -s "$SHARED_LOGS" logs
        log "logs/ anchored to $SHARED_LOGS (persists across deploys)"
    fi
fi

# The bind-mount source directories
# still need to exist before `docker compose up`, or Docker auto-creates
# them as root:root, which every worker service then fails to write into
# (confirmed live: PermissionError on /app/logs/{service} in monitoring,
# rate_limiter, storage_usage). start.sh's ensure_setup() does the same
# thing for interactive local use; this is deploy.sh's non-interactive
# equivalent. sudo mkdir, not plain mkdir: a bare `docker compose up`
# attempt before this script ever ran (or a previous partial run) can
# already have auto-created part of this tree as root:root -- confirmed
# live, logs/mailer/{dovecot,rspamd} already existed root-owned, which
# blocked a plain `mkdir -p logs/mailer/postfix` with Permission Denied
# even though postfix's own subdirectory didn't exist yet. 10001 matches
# every worker Dockerfile's `useradd -u 10001` (confirmed identical across
# api/webhooks/tracking/archiver/rate_limiter/monitoring/storage_usage) --
# postfix/dovecot/rspamd are deliberately left root-owned, they run
# privileged entrypoints that already correct their own mounted
# directories' ownership at container startup.
sudo mkdir -p logs/mailer/postfix logs/mailer/dovecot logs/mailer/rspamd \
    logs/tracking logs/worker/api logs/worker/archiver logs/worker/monitoring \
    logs/worker/rate_limiter logs/worker/webhooks logs/worker/storage_usage \
    logs/worker/rag storage/api_data
sudo chown -R 10001:10001 logs/tracking logs/worker storage/api_data

# Traefik's file provider (traefik.yml) points at this exact file for its
# dynamic TLS config -- cert_manager overwrites it once it issues real
# certs, but on a first-ever deploy (or if cert_manager hasn't run yet)
# the file wouldn't exist at all, and Traefik errors on a missing
# filename provider target rather than starting with an empty cert list.
# sudo: cert_manager runs as root and already owns this directory from
# earlier runs (same root-owned-by-a-privileged-container pattern as the
# logs/ dirs above).
sudo mkdir -p storage/sni_config
[ -f storage/sni_config/traefik_certs.yml ] || printf 'tls:\n  certificates: []\n' | sudo tee storage/sni_config/traefik_certs.yml > /dev/null

log "OK"

step "2/8 VERIFYING PERSISTENT STORAGE SURVIVED THE RELEASE (data-loss guard)"
# If mailyte-prod_mysql_data already exists, this is NOT the first deploy --
# storage/ and secrets/ MUST already be non-empty (synced in by DevPilot's
# Sync Directory hook before this script ran). If the volume exists but
# these are empty, that sync silently failed: stop here rather than let
# every stateful service boot against blank storage.
if docker volume inspect "${PROJECT_NAME}_mysql_data" > /dev/null 2>&1; then
    log "Existing deployment detected (${PROJECT_NAME}_mysql_data volume already exists)"
    for f in secrets/db_root_password secrets/encryption_kek; do
        [ -s "$f" ] || fail "$f is missing/empty on a non-first deploy -- the Sync Directory hook for 'secrets' did not run correctly. STOPPING before this reaches any container. Do not remove this guard; fix the hook instead."
    done
    if [ -d storage/mail_data ] && [ -z "$(ls -A storage/mail_data 2>/dev/null)" ]; then
        fail "storage/mail_data is empty on a non-first deploy -- the Sync Directory hook for 'storage' did not run correctly. STOPPING before this reaches any container. Do not remove this guard; fix the hook instead."
    fi
    log "storage/ and secrets/ look intact"
else
    log "No existing ${PROJECT_NAME}_mysql_data volume -- treating this as the first deploy (empty storage is expected)"
fi

step "3/8 PRE-DEPLOY BACKUP"
# Real safety net regardless of how well the checks above did their job.
# See scripts/backup.sh's own header for what each mode covers. --pre-deploy
# is database + config/DKIM/SSL, local only; the scheduled timer keeps doing
# the full offsite backup.
# --pre-deploy, NOT --full.
#
# A full backup here meant dumping the database, Redis, ALL mail storage, DKIM,
# SSL, config and secrets, encrypting the lot, and waiting for it to finish
# uploading to S3 -- before a single image was built. On a real mail store that
# is the slowest thing in the deploy by a wide margin, and it protects against
# the wrong risk: a release can corrupt the database or replace config/, but it
# does not rewrite Maildir. Dovecot restarts and the mail is untouched.
#
# So this captures exactly what a release can damage -- database, config, DKIM,
# SSL -- and keeps it local, because the person who needs it will restore from
# this same disk minutes later. Offsite durability stays the job of
# mailyte-backup-full.timer, which still runs --full and still uploads.
#
# Set PREDEPLOY_FULL_BACKUP=1 to restore the old behaviour for one run (before
# a migration you are nervous about, say).
# Does this release actually put the database at risk?
#
# The DATA already sits outside the blast radius: mysql_data is a named Docker
# volume under /var/lib/docker/volumes, not a directory in the release, and
# nothing here runs `down` or removes volumes. A release therefore cannot
# delete or replace the database. The ONLY thing it can do is run migrations.
#
# So when there is no pending migration, a database dump protects against
# nothing that can happen in this release, and we skip it. When there IS one,
# it is exactly the moment the dump matters and it is taken.
#
# The head is computed from the RELEASE'S migration files on disk, not by
# asking the `migrate` image. At this point in the deploy that image has not
# been rebuilt yet, so it still contains the PREVIOUS release's migrations --
# asking it would report the old head, find it equal to the database, and skip
# the backup on precisely the deploys that add a migration.
#
# Every uncertain answer means "pending". Taking a dump we did not need costs
# seconds; skipping one we did costs the database.
migrations_pending() {
    [ -d alembic/versions ] || return 0

    local disk_head db_rev
    disk_head=$(python3 - <<'PYEOF' 2>/dev/null
import pathlib, re
revs, downs = set(), set()
for f in pathlib.Path("alembic/versions").glob("*.py"):
    t = f.read_text()
    m = re.search(r"^revision:\s*str\s*=\s*['\"]([^'\"]+)", t, re.M)
    n = re.search(r"^down_revision:[^=]*=\s*['\"]([^'\"]+)", t, re.M)
    if m: revs.add(m.group(1))
    if n: downs.add(n.group(1))
heads = revs - downs
print(next(iter(heads)) if len(heads) == 1 else "")
PYEOF
    )
    [ -n "$disk_head" ] || return 0

    db_rev=$(dc exec -T mysql mysql -u"${DB_USER:-mailuser}" -p"${DB_PASSWORD:-}" \
        "${DB_NAME:-mailserver}" -N -e "SELECT version_num FROM alembic_version;" 2>/dev/null \
        | grep -v -i warning | tr -d '[:space:]')
    [ -n "$db_rev" ] || return 0

    [ "$db_rev" != "$disk_head" ]
}

if [ "${PREDEPLOY_FULL_BACKUP:-0}" = "1" ]; then
    log "PREDEPLOY_FULL_BACKUP=1 -- taking a full offsite backup before deploying"
    bash scripts/backup.sh --full || fail "Pre-deploy backup failed -- refusing to deploy without one. Investigate before retrying."
elif migrations_pending; then
    log "Pending migration detected -- backing up the database before it runs"
    bash scripts/backup.sh --pre-deploy || fail "Pre-deploy backup failed -- refusing to deploy without one. Investigate before retrying."
else
    log "Schema already at head -- no migration will run, so no database dump is needed"
    bash scripts/backup.sh --config-only --no-upload || fail "Pre-deploy config backup failed. Investigate before retrying."
fi

# The pre-deploy copy is local by design, so the offsite copy has to come from
# somewhere else. Warn -- loudly, without blocking the release -- if the
# scheduled full backup has not produced one recently. Silence here is how a
# deployment ends up being the only thing that ever backed anything up.
# storage/backups, not backups -- that is where BACKUP_DIR actually points.
#
# `|| true` is load-bearing, not defensive noise. This script runs under
# `set -e`, and an assignment from a command substitution takes that command's
# exit status: when find had the wrong path it returned non-zero, the
# assignment failed, and the DEPLOY EXITED between the backup and the build.
# Confirmed live on server1 -- a warning that nobody asked to be fatal stopped
# the release. Any future check added here must be unable to fail.
LATEST_FULL=""
if [ -d storage/backups ]; then
    LATEST_FULL=$(find storage/backups -maxdepth 2 -name "MANIFEST.json" -mtime -2 2>/dev/null \
        | xargs -r grep -l '"type"[[:space:]]*:[[:space:]]*"full"' 2>/dev/null | head -1) || true
fi
if [ -z "$LATEST_FULL" ]; then
    log "WARNING: no full backup in the last 48h. mailyte-backup-full.timer may not be running --"
    log "         check 'systemctl list-timers mailyte-backup-*'. This deploy's own backup is LOCAL ONLY."
fi

# The archiver spool holds messages that have been accepted for delivery but
# have not reached S3 yet. It is a bind mount, so the HOST's ownership is what
# the container sees, and the image runs as uid 10001. Created here rather than
# in the Dockerfile because a Dockerfile cannot chown a host directory -- and a
# spool the archiver cannot write to fails exactly when S3 is already down.
mkdir -p storage/archive-spool
sudo chown -R 10001:10001 storage/archive-spool 2>/dev/null || chown -R 10001:10001 storage/archive-spool 2>/dev/null || \
  echo "  WARNING: could not chown storage/archive-spool to uid 10001; the archiver will not be able to spool"

# The archive service identity is read by the archiver container (uid 10001)
# and by escrow-secrets.sh running as the deploy user, so it needs to be
# readable by both: owner keeps it for the escrow bundle, group 10001 for the
# service. At mode 600 owned by the deploy user the container gets EACCES and
# GET /archive/{id} fails to decrypt -- with the object safely stored, which
# makes it look like a retrieval bug rather than a permissions one.
if [ -f secrets/archive_age_identity ]; then
    sudo chown "$(id -u):10001" secrets/archive_age_identity 2>/dev/null || true
    chmod 640 secrets/archive_age_identity 2>/dev/null || true
fi
log "Backup complete"

step "4/8 BUILDING IMAGES"
# This is the slowest step by a wide margin: 31 of the 48 services have a
# build: stanza, so every deploy walks all of them.
#
# `--pull` is NOT in the default path any more. It forces a registry round
# trip for EVERY service's base image on EVERY deploy, even when nothing has
# changed -- 31 network checks that almost always conclude "no change", and
# the single biggest avoidable cost here. Docker's layer cache then makes the
# unchanged services near-instant.
#
# Base images still need refreshing, or a CVE in node:22-alpine sits in the
# stack forever. Two ways to get it, both explicit:
#
#     PULL_BASE=1 ./deployment/deploy.sh      # refresh base images, keep cache
#     FORCE_REBUILD=1 ./deployment/deploy.sh  # no cache at all, from scratch
#
# Run PULL_BASE=1 on a schedule (weekly is reasonable), not on every deploy.
if [ "${FORCE_REBUILD:-0}" = "1" ]; then
    log "FORCE_REBUILD=1 -- building from scratch, no cache"
    dc build --no-cache --pull
elif [ "${PULL_BASE:-0}" = "1" ]; then
    log "PULL_BASE=1 -- refreshing base images, keeping the layer cache"
    dc build --pull
else
    log "Building with layer cache (set PULL_BASE=1 to refresh base images)"
    dc build
fi
log "Images built"

step "5/8 DEPLOYING (stateful + singleton services)"
# Compose recreates only services whose image/config actually changed --
# postfix/dovecot/mysql/rspamd/etc. are NOT horizontally scalable and get a
# brief (seconds) restart when their own definition changes, same as any
# `docker compose up -d`. This is acceptable for mail protocols (senders
# retry on refused connections; IMAP clients reconnect) and is NOT attempted
# as a rolling update -- there is exactly one mysql, one postfix, one
# dovecot, and trying to run two of them against the same data/ports would
# be actively unsafe, not safer.
#
# Migrations are NOT a separate step here (unlike wizjob/informly): the
# `migrate` service's depends_on: condition: service_completed_successfully
# (docker-compose.yml) already makes every dependent service wait for a
# clean migration before starting, automatically, every time `up -d` runs.
mapfile -t all_services < <(dc config --services)
singleton_services=()
for s in "${all_services[@]}"; do
    case "$s" in
        api|webhooks|tracking) ;;
        *) singleton_services+=("$s") ;;
    esac
done
dc up -d --remove-orphans "${singleton_services[@]}"
log "Singleton/stateful services deployed"

step "6/8 ROLLING UPDATE (replicated stateless services)"
# api/webhooks/tracking run 2 replicas each (docker-compose.prod.yml) and
# ARE safe to roll: scale up alongside the old ones, confirm the new
# container is actually healthy, THEN stop the specific old container --
# never the other way around. Same technique already proven in production
# for informly's app container.
for svc in "${REPLICATED_SERVICES[@]}"; do
    log "Rolling $svc..."
    old_ids=$(dc ps -q "$svc")

    dc up -d --scale "$svc=2" --no-deps "$svc"
    sleep 5

    healthy=0
    for i in $(seq 1 30); do
        new_ids=$(dc ps -q "$svc")
        candidate=$(comm -13 <(echo "$old_ids" | sort) <(echo "$new_ids" | sort) | head -1)
        if [ -n "$candidate" ] && [ "$(docker inspect --format '{{.State.Running}}' "$candidate" 2>/dev/null)" = "true" ]; then
            healthy=1
            break
        fi
        sleep 2
    done

    if [ "$healthy" = "1" ]; then
        log "  new $svc container is up"
        for oid in $old_ids; do
            docker stop "$oid" --time 30 > /dev/null 2>&1 || true
            docker rm "$oid" > /dev/null 2>&1 || true
        done
        dc up -d --scale "$svc=2" --no-recreate --no-deps "$svc"
    else
        log "  WARNING: could not confirm new $svc container health in time -- leaving both old and new running rather than guessing. Check manually: docker compose -p $PROJECT_NAME ps $svc"
    fi
done
log "Rolling updates complete"

step "7/8 HEALTH CHECK"
# api has no host-published port in prod (docker-compose.prod.yml resets
# it -- Traefik reaches it over the internal network only), so this can't
# curl a host URL the way a dev-only check could. Instead poll the same
# Docker HEALTHCHECK (docker-compose.yml's own urllib request to
# localhost:8080/health inside the container) that step 6's containers
# already report -- confirms the running replicas are actually serving,
# not just that the process started.
healthy=0
for i in $(seq 1 30); do
    api_ids=$(dc ps -q api)
    if [ -n "$api_ids" ]; then
        all_healthy=1
        for cid in $api_ids; do
            status=$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")
            [ "$status" = "healthy" ] || all_healthy=0
        done
        if [ "$all_healthy" = "1" ]; then
            healthy=1
            break
        fi
    fi
    sleep 2
done
if [ "$healthy" != "1" ]; then
    log "api did not become healthy in time -- showing container status for diagnosis:"
    dc ps
    fail "Health check failed. Containers are left running for inspection -- nothing was rolled back automatically. Check logs: docker compose -p $PROJECT_NAME logs api"
fi
log "api is healthy"

step "8/8 SCOPED CLEANUP"
# Filtered strictly by this project's own label. No `docker volume prune`,
# ever -- that is not scoped to this project and can remove volumes
# belonging to anything else on a shared host.
docker ps -aq --filter "label=com.docker.compose.project=${PROJECT_NAME}" --filter status=exited | xargs -r docker rm -f
docker image prune -f
# The build cache is what makes step 4 fast. Pruning it aggressively here
# means paying for it again on the next deploy, so the window is generous:
# anything still referenced within a week survives. Lower it only if disk
# pressure is a real problem on this host, and expect slower builds if you do.
docker builder prune -f --filter until="${BUILDER_CACHE_TTL:-168h}"

finish_timing
echo ""
echo "Deployment complete."
dc ps --format "table {{.Name}}\t{{.Status}}"
