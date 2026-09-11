#!/bin/bash
# =============================================================================
# Provision the DR object-storage layout (buckets, retention, IAM)
# =============================================================================
# Implements PRD 00-PRD-disaster-recovery.md decisions D2-D4 and D6 against an
# S3-compatible endpoint. Run once per environment; idempotent thereafter.
#
#   D2  two buckets: mailyte-dr (backups + escrow), mailyte-mail-archive
#   D3  versioning + Object Lock (GOVERNANCE, 30 d) on mailyte-dr
#   D4  three principals: backup-writer (Put, no Delete), archiver (Put/Get on
#       the archive prefix), dr-restore (read-only, credentials go to escrow
#       and never onto a production host)
#   D6  lifecycle: backups 30 d Standard -> IA -> Glacier IR -> expire;
#       archive 90 d Standard -> IA
#
# Against MinIO this uses `mc`. Against real AWS the same layout is created by
# `--provider aws`, which shells out to the aws CLI instead -- the resulting
# bucket/IAM shape is identical, which is the whole point: the drill exercises
# the same code path production will.
#
# Usage:
#   ./setup-buckets.sh                       # MinIO at localhost:9190 (default)
#   ./setup-buckets.sh --provider aws        # real AWS, uses current aws creds
#   ./setup-buckets.sh --print-credentials   # re-print generated access keys
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROVIDER=minio
PRINT_ONLY=false
MINIO_ALIAS="${MINIO_ALIAS:-mailyte-dr-local}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9190}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-mailyte-dr-root}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-mailyte-dr-root-secret}"
REGION="${AWS_DEFAULT_REGION:-eu-west-1}"

BUCKET_DR="${BUCKET_DR:-mailyte-dr}"
BUCKET_ARCHIVE="${BUCKET_ARCHIVE:-mailyte-mail-archive}"
LOCK_DAYS="${LOCK_DAYS:-30}"

# Where generated principal credentials land. Outside any git tree by default:
# these are real credentials, two of the three go onto production hosts, and
# the third (dr-restore) must reach escrow without ever touching a server.
CRED_DIR="${DR_CRED_DIR:-${HOME}/.mailyte-dr}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider) PROVIDER="$2"; shift 2 ;;
        --print-credentials) PRINT_ONLY=true; shift ;;
        --help) sed -n '2,30p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# `tr </dev/urandom | head -c N` is the obvious way to generate key material and
# the wrong one under `set -o pipefail`: head closes the pipe, tr dies on
# SIGPIPE, and the whole script exits mid-provisioning. Isolate the pipeline.
random_string() {
    local charset="$1" length="$2"
    ( set +o pipefail; LC_ALL=C tr -dc "$charset" < /dev/urandom | head -c "$length" )
}

info() { printf '\033[0;34m[setup]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }

# --------------------------------------------------------------------------
# Policy documents (identical JSON for MinIO and AWS -- both speak IAM policy)
# --------------------------------------------------------------------------
policy_backup_writer() {
cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PutBackupsNeverDelete",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": ["arn:aws:s3:::${BUCKET_DR}/*"]
    },
    {
      "Sid": "ListOwnBucketForIdempotentUploads",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"],
      "Resource": ["arn:aws:s3:::${BUCKET_DR}"]
    }
  ]
}
JSON
}

policy_archiver() {
cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ArchivePutGet",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"],
      "Resource": ["arn:aws:s3:::${BUCKET_ARCHIVE}/mail-archive/*"]
    },
    {
      "Sid": "ArchiveList",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::${BUCKET_ARCHIVE}"]
    }
  ]
}
JSON
}

policy_dr_restore() {
cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadEverythingRestoreNeeds",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetObjectVersion"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_DR}/*",
        "arn:aws:s3:::${BUCKET_ARCHIVE}/*"
      ]
    },
    {
      "Sid": "ListForRestore",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_DR}",
        "arn:aws:s3:::${BUCKET_ARCHIVE}"
      ]
    }
  ]
}
JSON
}

lifecycle_dr() {
cat <<JSON
{
  "Rules": [
    {
      "ID": "backups-tier-then-expire",
      "Filter": {"Prefix": "mailyte/backups/"},
      "Status": "Enabled",
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"},
        {"Days": 120, "StorageClass": "GLACIER_IR"}
      ],
      "Expiration": {"Days": 400}
    },
    {
      "ID": "escrow-never-expires",
      "Filter": {"Prefix": "escrow/"},
      "Status": "Enabled",
      "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}]
    }
  ]
}
JSON
}

lifecycle_archive() {
cat <<JSON
{
  "Rules": [
    {
      "ID": "archive-standard-then-ia",
      "Filter": {"Prefix": "mail-archive/"},
      "Status": "Enabled",
      "Transitions": [{"Days": 90, "StorageClass": "STANDARD_IA"}]
    }
  ]
}
JSON
}

# --------------------------------------------------------------------------
# MinIO implementation
# --------------------------------------------------------------------------
# `mc` is run from a version-matched container rather than a host install.
# MinIO's admin API is not version-stable: a Homebrew mc from 2025-08 answers
# `mc admin user add` against the 2025-04 server with "Failed to parse server
# response ... Not Found", so user/policy provisioning silently never happens.
# Pinning the client to the server's own release removes that whole class of
# problem and drops the brew dependency.
MC_IMAGE="${MC_IMAGE:-minio/mc:RELEASE.2025-04-16T18-13-26Z}"
MC_NETWORK="${MC_NETWORK:-mailyte-dr-local_default}"

MC_MOUNT=()
mc_run() {
    # Alias is re-registered per invocation: the container is disposable, and
    # a shared config volume would just be state to clean up later.
    docker run --rm --network "$MC_NETWORK" "${MC_MOUNT[@]}" --entrypoint sh "$MC_IMAGE" -c \
        "mc alias set m ${MINIO_INTERNAL_ENDPOINT} ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD} >/dev/null 2>&1; $*"
}

# Inside the compose network MinIO answers on its service name, not the
# host-published port.
MINIO_INTERNAL_ENDPOINT="${MINIO_INTERNAL_ENDPOINT:-http://minio:9000}"

setup_minio() {
    command -v docker >/dev/null || { echo "docker not found (needed to run the pinned mc client)" >&2; exit 1; }

    info "Talking to MinIO at ${MINIO_INTERNAL_ENDPOINT} via ${MC_IMAGE}"

    local tmp; tmp=$(mktemp -d)
    lifecycle_dr           > "$tmp/lc-dr.json"
    lifecycle_archive      > "$tmp/lc-archive.json"
    policy_backup_writer   > "$tmp/backup-writer.json"
    policy_archiver        > "$tmp/archiver.json"
    policy_dr_restore      > "$tmp/dr-restore.json"
    # The pinned client runs in a container, so policy/lifecycle documents have
    # to be visible from inside it.
    MC_MOUNT=(-v "${tmp}:/work:ro")

    # Object Lock can only be turned on at creation time -- on either MinIO or
    # AWS. A bucket that already exists without it cannot be upgraded, so this
    # warns rather than silently leaving D3 unmet.
    if mc_run "mc ls m/${BUCKET_DR}" >/dev/null 2>&1; then
        if mc_run "mc retention info m/${BUCKET_DR}" 2>/dev/null | grep -qi governance; then
            ok "${BUCKET_DR} exists with Object Lock"
        else
            warn "${BUCKET_DR} exists WITHOUT Object Lock -- recreate it to satisfy D3"
        fi
    else
        info "Creating ${BUCKET_DR} with Object Lock"
        mc_run "mc mb --with-lock --region ${REGION} m/${BUCKET_DR}"
        mc_run "mc retention set --default GOVERNANCE ${LOCK_DAYS}d m/${BUCKET_DR}"
        ok "${BUCKET_DR} created: versioning + GOVERNANCE ${LOCK_DAYS}d"
    fi

    if mc_run "mc ls m/${BUCKET_ARCHIVE}" >/dev/null 2>&1; then
        ok "${BUCKET_ARCHIVE} exists"
    else
        info "Creating ${BUCKET_ARCHIVE}"
        mc_run "mc mb --region ${REGION} m/${BUCKET_ARCHIVE}"
        mc_run "mc version enable m/${BUCKET_ARCHIVE}" >/dev/null
        ok "${BUCKET_ARCHIVE} created with versioning"
    fi

    # Lifecycle: MinIO parses the AWS JSON shape but rejects transitions to
    # storage classes it has no remote tier for, which a single-node dev MinIO
    # never has. Not fatal -- the same document is what AWS gets, and AWS is
    # where tiering actually has to work.
    mc_run "mc ilm import m/${BUCKET_DR} < /work/lc-dr.json" >/dev/null 2>&1 \
        && ok "lifecycle applied to ${BUCKET_DR}" \
        || warn "lifecycle not applied to ${BUCKET_DR} (no remote tiers on dev MinIO; AWS accepts it)"
    mc_run "mc ilm import m/${BUCKET_ARCHIVE} < /work/lc-archive.json" >/dev/null 2>&1 \
        && ok "lifecycle applied to ${BUCKET_ARCHIVE}" \
        || warn "lifecycle not applied to ${BUCKET_ARCHIVE} (no remote tiers on dev MinIO)"

    mkdir -p "$CRED_DIR"; chmod 700 "$CRED_DIR"

    local name cred_file ak sk
    for name in backup-writer archiver dr-restore; do
        cred_file="${CRED_DIR}/${name}.cred"

        mc_run "mc admin policy create m mailyte-${name} /work/${name}.json" >/dev/null 2>&1 || true

        if [[ -f "$cred_file" ]]; then
            ok "principal ${name} already provisioned (${cred_file})"
        else
            # AWS-shaped key material so nothing downstream has to special-case
            # MinIO credentials when the endpoint is swapped for real S3.
            ak="MLYT$(random_string 'A-Z0-9' 16)"
            sk=$(random_string 'A-Za-z0-9' 40)
            mc_run "mc admin user add m ${ak} ${sk}" >/dev/null
            printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n' "$ak" "$sk" > "$cred_file"
            chmod 600 "$cred_file"
            ok "principal ${name} created -> ${cred_file}"
        fi

        ak=$(grep '^AWS_ACCESS_KEY_ID=' "$cred_file" | cut -d= -f2)
        mc_run "mc admin policy attach m mailyte-${name} --user ${ak}" >/dev/null 2>&1 || true
    done

    rm -rf "$tmp"
}

# --------------------------------------------------------------------------
# AWS implementation
# --------------------------------------------------------------------------
setup_aws() {
    command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
    aws sts get-caller-identity >/dev/null || { echo "no usable AWS credentials" >&2; exit 1; }

    local tmp; tmp=$(mktemp -d)

    if aws s3api head-bucket --bucket "$BUCKET_DR" 2>/dev/null; then
        ok "${BUCKET_DR} exists"
    else
        info "Creating ${BUCKET_DR} with Object Lock"
        aws s3api create-bucket --bucket "$BUCKET_DR" --region "$REGION" \
            --create-bucket-configuration "LocationConstraint=${REGION}" \
            --object-lock-enabled-for-bucket >/dev/null
        aws s3api put-object-lock-configuration --bucket "$BUCKET_DR" \
            --object-lock-configuration "ObjectLockEnabled=Enabled,Rule={DefaultRetention={Mode=GOVERNANCE,Days=${LOCK_DAYS}}}" >/dev/null
        ok "${BUCKET_DR} created: versioning + GOVERNANCE ${LOCK_DAYS}d"
    fi

    if aws s3api head-bucket --bucket "$BUCKET_ARCHIVE" 2>/dev/null; then
        ok "${BUCKET_ARCHIVE} exists"
    else
        aws s3api create-bucket --bucket "$BUCKET_ARCHIVE" --region "$REGION" \
            --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
        aws s3api put-bucket-versioning --bucket "$BUCKET_ARCHIVE" \
            --versioning-configuration Status=Enabled >/dev/null
        ok "${BUCKET_ARCHIVE} created with versioning"
    fi

    # Public access is blocked explicitly rather than relying on the account
    # default -- a DR bucket that leaks is a full platform compromise.
    for b in "$BUCKET_DR" "$BUCKET_ARCHIVE"; do
        aws s3api put-public-access-block --bucket "$b" --public-access-block-configuration \
            'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' >/dev/null
        aws s3api put-bucket-encryption --bucket "$b" --server-side-encryption-configuration \
            '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
    done
    ok "public access blocked + SSE-S3 default on both buckets"

    lifecycle_dr      > "$tmp/lc-dr.json"
    lifecycle_archive > "$tmp/lc-archive.json"
    aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET_DR" \
        --lifecycle-configuration "file://$tmp/lc-dr.json" >/dev/null && ok "lifecycle applied to ${BUCKET_DR}"
    aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET_ARCHIVE" \
        --lifecycle-configuration "file://$tmp/lc-archive.json" >/dev/null && ok "lifecycle applied to ${BUCKET_ARCHIVE}"

    mkdir -p "$CRED_DIR"; chmod 700 "$CRED_DIR"

    local name policy_fn
    for spec in "backup-writer:policy_backup_writer" "archiver:policy_archiver" "dr-restore:policy_dr_restore"; do
        name="${spec%%:*}"; policy_fn="${spec##*:}"
        local iam_user="mailyte-${name}"
        local cred_file="${CRED_DIR}/${name}.cred"

        aws iam get-user --user-name "$iam_user" >/dev/null 2>&1 || \
            aws iam create-user --user-name "$iam_user" >/dev/null
        "$policy_fn" > "$tmp/${name}.json"
        aws iam put-user-policy --user-name "$iam_user" \
            --policy-name "mailyte-${name}" --policy-document "file://$tmp/${name}.json" >/dev/null

        if [[ -f "$cred_file" ]]; then
            ok "principal ${name} already provisioned (${cred_file})"
        else
            local out
            out=$(aws iam create-access-key --user-name "$iam_user" --output json)
            printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n' \
                "$(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])')" \
                "$(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessKey"]["SecretAccessKey"])')" \
                > "$cred_file"
            chmod 600 "$cred_file"
            ok "principal ${name} created -> ${cred_file}"
        fi
    done

    rm -rf "$tmp"
}

# --------------------------------------------------------------------------
print_credentials() {
    echo ""
    echo "Generated principals (${CRED_DIR}):"
    for name in backup-writer archiver dr-restore; do
        local f="${CRED_DIR}/${name}.cred"
        [[ -f "$f" ]] || { echo "  ${name}: (not provisioned)"; continue; }
        printf '  %-14s %s\n' "$name" "$(grep '^AWS_ACCESS_KEY_ID=' "$f" | cut -d= -f2)"
    done
    echo ""
    echo "  backup-writer -> both production hosts (secrets/dr.env)"
    echo "  archiver      -> mail server only (archiver service env)"
    echo "  dr-restore    -> ESCROW ONLY. Never place this on a production host."
    echo ""
}

if [[ "$PRINT_ONLY" == true ]]; then print_credentials; exit 0; fi

case "$PROVIDER" in
    minio) setup_minio ;;
    aws)   setup_aws ;;
    *)     echo "Unknown provider: ${PROVIDER} (expected minio|aws)" >&2; exit 1 ;;
esac

print_credentials
ok "Bucket layout ready (provider=${PROVIDER})"
