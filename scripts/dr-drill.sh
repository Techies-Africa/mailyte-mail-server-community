#!/bin/bash
# =============================================================================
# Disaster-recovery drill: rebuild from S3 and nothing else
# =============================================================================
# An untested backup is a hypothesis. This is the test.
#
# The drill deliberately uses ONLY things that survive the loss of both
# production servers:
#
#   * the dr-restore credentials (read-only, escrow-held, never on a server)
#   * the age identity (escrow-held, never on a server)
#   * whatever is in S3
#
# It never connects to a production host. If it passes, a rebuild is possible
# with the production disks gone; if it needs anything from them, it has proved
# the opposite of what it claims.
#
# What it asserts, in order of how much it matters:
#
#   1. The escrowed identity decrypts the backups. Anything less and every
#      backup we hold is ciphertext nobody can open.
#   2. The MySQL dump restores and carries the expected schema revision and
#      real tenant rows.
#   3. A known mailbox's mail is readable THROUGH mail_crypt using the key from
#      the ESCROW BUNDLE -- not a key copied off the server. This is the whole
#      ballgame: mail at rest is encrypted under a single global keypair, so a
#      restore that produces Maildir files nobody can decrypt has restored
#      nothing.
#
# It prints a measured RTO at the end, which replaces the estimate in the PRD.
#
# Usage:
#   DR_AGE_IDENTITY_FILE=~/.mailyte-dr/dr-age-identity.txt \
#   DR_CRED_DIR=~/.mailyte-dr \
#   ./dr-drill.sh [--backup-id ID] [--mail-backup-id ID] [--keep]
# =============================================================================
set -uo pipefail

DRILL_START=$(date +%s)

S3_BUCKET="${S3_BUCKET:-mailyte-ent}"
S3_PREFIX="${S3_PREFIX:-mailyte/backups}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://127.0.0.1:9190}"
BACKUP_HOST="${BACKUP_HOST:-server1}"
CRED_DIR="${DR_CRED_DIR:-${HOME}/.mailyte-dr}"
IDENTITY="${DR_AGE_IDENTITY_FILE:-${CRED_DIR}/dr-age-identity.txt}"

WORK="${DR_DRILL_WORKDIR:-$(mktemp -d -t mailyte-dr-drill)}"
KEEP=false
BACKUP_ID=""
MAIL_BACKUP_ID=""

MYSQL_CONTAINER=dr-drill-mysql
DOVECOT_CONTAINER=dr-drill-dovecot
MYSQL_IMAGE="${DR_DRILL_MYSQL_IMAGE:-mysql:8.0}"
# The project's own Dovecot image, built from mailer/dovecot -- the drill
# environment must be rebuilt from the same files production uses (phase-01
# risk: "drill environment diverges from production"). It is also the only one
# that works here: dovecot/dovecot ships amd64 only and dies under Rosetta on
# Apple Silicon with "unable to mmap ExecutableHeap".
DOVECOT_IMAGE="${DR_DRILL_DOVECOT_IMAGE:-mailyte-drill-dovecot:local}"
DOVECOT_BUILD_CONTEXT="${DR_DRILL_DOVECOT_CONTEXT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/mailer/dovecot}"
DRILL_DB=mailserver

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-id) BACKUP_ID="$2"; shift 2 ;;
        --mail-backup-id) MAIL_BACKUP_ID="$2"; shift 2 ;;
        --keep) KEEP=true; shift ;;
        --help) sed -n '2,36p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

PASS=0
FAIL=0
step()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok()    { printf '  \033[0;32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()   { printf '  \033[0;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
info()  { printf '       %s\n' "$*"; }

cleanup() {
    if [[ "$KEEP" == true ]]; then
        printf '\n(--keep) drill environment left at %s\n' "$WORK"
        printf '        containers %s / %s left running\n' "$MYSQL_CONTAINER" "$DOVECOT_CONTAINER"
        return
    fi
    docker rm -f "$MYSQL_CONTAINER" "$DOVECOT_CONTAINER" >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK"
chmod 700 "$WORK"

# ---------------------------------------------------------------------------
# Credentials: read-only, and deliberately nothing else
# ---------------------------------------------------------------------------
step "Credentials"
if [[ ! -f "${CRED_DIR}/dr-restore.cred" ]]; then
    bad "dr-restore credentials not found at ${CRED_DIR}/dr-restore.cred"
    exit 1
fi
# shellcheck disable=SC1090
set -a; source "${CRED_DIR}/dr-restore.cred"; set +a
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-west-1}"
export AWS_EC2_METADATA_DISABLED=true
ok "using the read-only dr-restore principal (${AWS_ACCESS_KEY_ID})"

[[ -f "$IDENTITY" ]] || { bad "escrowed age identity not found at ${IDENTITY}"; exit 1; }
ok "escrowed age identity present (never on a production host)"

s3() { aws --endpoint-url "$S3_ENDPOINT_URL" "$@"; }

# ---------------------------------------------------------------------------
# Discover what is actually recoverable
# ---------------------------------------------------------------------------
step "Inventory: what does S3 hold?"
BASE="s3://${S3_BUCKET}/${S3_PREFIX}/mail/${BACKUP_HOST}"

if [[ -z "$BACKUP_ID" ]]; then
    # Newest backup that contains a database dump: a backup without one cannot
    # rebuild a platform, so picking "the newest" blindly would pick wrong.
    while IFS= read -r candidate; do
        [[ -n "$candidate" ]] || continue
        if s3 s3 ls "${BASE}/${candidate}/mysql/" 2>/dev/null | grep -q '\.sql\.gz\.age'; then
            BACKUP_ID="$candidate"; break
        fi
    done < <(s3 s3 ls "${BASE}/" 2>/dev/null | awk '{print $2}' | tr -d '/' | sort -r)
fi
[[ -n "$BACKUP_ID" ]] || { bad "no backup with a database dump found under ${BASE}/"; exit 1; }
ok "database backup: ${BACKUP_ID}"

if [[ -z "$MAIL_BACKUP_ID" ]]; then
    while IFS= read -r candidate; do
        [[ -n "$candidate" ]] || continue
        if s3 s3 ls "${BASE}/${candidate}/mail/" 2>/dev/null | grep -q '\.tar\.gz\.age'; then
            MAIL_BACKUP_ID="$candidate"; break
        fi
    done < <(s3 s3 ls "${BASE}/" 2>/dev/null | awk '{print $2}' | tr -d '/' | sort -r)
fi
[[ -n "$MAIL_BACKUP_ID" ]] && ok "mail backup: ${MAIL_BACKUP_ID}" || bad "no mail backup found"

# ---------------------------------------------------------------------------
# 1. Escrow decrypt
# ---------------------------------------------------------------------------
step "1. Secrets bundle: pull and decrypt with the escrowed identity"
mkdir -p "${WORK}/escrow"
if s3 s3 cp "s3://${S3_BUCKET}/escrow/mail/${BACKUP_HOST}/latest.tar.age" \
        "${WORK}/escrow/bundle.tar.age" --only-show-errors 2>/dev/null; then
    ok "escrow bundle downloaded"
else
    bad "escrow bundle missing -- a rebuild cannot get the mail_crypt key"
    exit 1
fi

if age --decrypt --identity "$IDENTITY" --output "${WORK}/escrow/bundle.tar" \
        "${WORK}/escrow/bundle.tar.age" 2>/dev/null; then
    ok "escrow bundle decrypted with the escrowed identity"
else
    bad "escrow bundle would not decrypt -- every backup we hold is unreadable"
    exit 1
fi

tar xzf "${WORK}/escrow/bundle.tar" -C "${WORK}/escrow" 2>/dev/null
MAILCRYPT_KEY="${WORK}/escrow/secrets/mail_crypt/ecprivkey.pem"
if [[ -f "$MAILCRYPT_KEY" ]]; then
    ok "mail_crypt private key recovered from escrow"
else
    bad "no mail_crypt key in the escrow bundle"
    exit 1
fi
[[ -f "${WORK}/escrow/secrets/encryption_kek" ]] && ok "encryption_kek recovered (DKIM/PGP unwrapping)"
DKIM_COUNT=$(find "${WORK}/escrow/storage/dkim_keys" -name '*.key' 2>/dev/null | wc -l | tr -d ' ')
[[ "${DKIM_COUNT:-0}" -gt 0 ]] && ok "${DKIM_COUNT} DKIM private keys recovered"

# ---------------------------------------------------------------------------
# 2. Database
# ---------------------------------------------------------------------------
step "2. Database: pull, decrypt, restore"
mkdir -p "${WORK}/db"
DUMP_KEY=$(s3 s3 ls "${BASE}/${BACKUP_ID}/mysql/" | awk '{print $4}' | grep '\.sql\.gz\.age$' | head -1)
s3 s3 cp "${BASE}/${BACKUP_ID}/mysql/${DUMP_KEY}" "${WORK}/db/dump.sql.gz.age" --only-show-errors 2>/dev/null \
    && ok "dump downloaded (${DUMP_KEY})" || { bad "dump download failed"; exit 1; }

if age --decrypt --identity "$IDENTITY" "${WORK}/db/dump.sql.gz.age" | gzip -dc > "${WORK}/db/dump.sql" 2>/dev/null; then
    ok "dump decrypted and decompressed ($(wc -c < "${WORK}/db/dump.sql" | tr -d ' ') bytes)"
else
    bad "dump would not decrypt"
    exit 1
fi

info "starting scratch MySQL (${MYSQL_IMAGE})"
# `docker rm -f` returns before the name is released, so a run started
# immediately after can attach to the dying container -- which presents as
# "scratch MySQL up" followed by "Access denied for user 'root'", because the
# ping and the load talked to different containers.
docker rm -f "$MYSQL_CONTAINER" >/dev/null 2>&1
for _ in $(seq 1 30); do
    docker ps -a --format '{{.Names}}' | grep -qx "$MYSQL_CONTAINER" || break
    sleep 1
done
docker run -d --name "$MYSQL_CONTAINER" \
    -e MYSQL_ROOT_PASSWORD=drilldrill \
    -e MYSQL_DATABASE="$DRILL_DB" \
    "$MYSQL_IMAGE" --default-authentication-plugin=mysql_native_password >/dev/null 2>&1

# An authenticated query, not `mysqladmin ping`. During initialisation the
# image runs a temporary server that answers ping before the root password has
# been applied, so ping reports "up" and the very next statement fails with
# "Access denied for user 'root'" -- which reads like a credentials bug rather
# than a readiness one.
mysql_ready() {
    docker exec -e MYSQL_PWD=drilldrill "$MYSQL_CONTAINER" \
        mysql -uroot -N -B -e "SELECT 1" >/dev/null 2>&1
}
for _ in $(seq 1 90); do
    mysql_ready && break
    sleep 2
done
if mysql_ready; then
    ok "scratch MySQL up"
else
    bad "scratch MySQL never became ready"; exit 1
fi

if docker exec -i -e MYSQL_PWD=drilldrill "$MYSQL_CONTAINER" mysql -uroot \
        < "${WORK}/db/dump.sql" 2> "${WORK}/db/load.err"; then
    ok "dump loaded"
else
    bad "dump failed to load"
    # Silencing this is how the previous run reported four downstream failures
    # with no way to tell which one was the cause.
    info "$(head -3 "${WORK}/db/load.err")"
fi

q() { docker exec -e MYSQL_PWD=drilldrill "$MYSQL_CONTAINER" mysql -uroot -N -B -D "$DRILL_DB" -e "$1" 2>/dev/null; }

REV=$(q "SELECT version_num FROM alembic_version;")
[[ -n "$REV" ]] && ok "schema revision: ${REV}" || bad "no alembic_version -- schema did not restore"

TABLES=$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DRILL_DB}';")
[[ "${TABLES:-0}" -gt 20 ]] && ok "${TABLES} tables restored" || bad "only ${TABLES:-0} tables restored"

ACCOUNTS=$(q "SELECT COUNT(*) FROM email_accounts;")
DOMAINS=$(q "SELECT COUNT(*) FROM domains;")
[[ "${ACCOUNTS:-0}" -gt 0 ]] && ok "${ACCOUNTS} mailboxes in the restored database" || bad "no mailboxes restored"
[[ "${DOMAINS:-0}" -gt 0 ]] && ok "${DOMAINS} domains in the restored database" || bad "no domains restored"

# ---------------------------------------------------------------------------
# 3. Mail, read through mail_crypt with the escrowed key
# ---------------------------------------------------------------------------
step "3. Mail: restore a Maildir and read it through mail_crypt"
mkdir -p "${WORK}/mail"
if [[ -n "$MAIL_BACKUP_ID" ]]; then
    MAIL_KEY=$(s3 s3 ls "${BASE}/${MAIL_BACKUP_ID}/mail/" | awk '{print $4}' | grep '\.tar\.gz\.age$' | head -1)
    s3 s3 cp "${BASE}/${MAIL_BACKUP_ID}/mail/${MAIL_KEY}" "${WORK}/mail/mail.tar.gz.age" --only-show-errors 2>/dev/null \
        && ok "mail archive downloaded (${MAIL_KEY})" || bad "mail archive download failed"

    if age --decrypt --identity "$IDENTITY" "${WORK}/mail/mail.tar.gz.age" \
            | tar xz -C "${WORK}/mail" 2>/dev/null; then
        ok "mail archive decrypted and extracted"
    else
        bad "mail archive would not decrypt/extract"
    fi
fi

# Find a mailbox with at least one message. The Maildir layout is
# <domain>/<user>/{cur,new}; whichever domain the archive holds, this locates a
# real message rather than assuming one.
MSG_FILE=$(find "${WORK}/mail" -type f -path '*/cur/*' -size +100c 2>/dev/null | head -1)
if [[ -z "$MSG_FILE" ]]; then
    MSG_FILE=$(find "${WORK}/mail" -type f -path '*/new/*' -size +100c 2>/dev/null | head -1)
fi

if [[ -z "$MSG_FILE" ]]; then
    bad "no message files found in the restored Maildir"
else
    ok "found a restored message: ${MSG_FILE#"${WORK}/mail/"}"

    # This is the assertion the whole drill exists for. Dovecot mail_crypt
    # writes an encrypted container with a "CRYPTED" magic; if the file starts
    # with that, the bytes on disk are NOT readable mail and a restore that
    # stopped here would have restored nothing usable.
    MAGIC=$(head -c 7 "$MSG_FILE" | tr -d '\0')
    if [[ "$MAGIC" == "CRYPTED" ]]; then
        info "message is mail_crypt-encrypted on disk (as expected)"
        ENCRYPTED_AT_REST=true
    else
        info "message is plaintext on disk (mail_crypt not in use for this message)"
        ENCRYPTED_AT_REST=false
    fi

    # Maildir++ layout is <domain>/<user>/[.Folder[.Sub]]/{cur,new}/<file>.
    # Naive dirname-twice reads ".Sent" as the username and "support" as the
    # domain, which then fails userdb lookup for a mailbox that does not exist.
    # Walk back from the cur/new component instead, skipping a dot-folder.
    REL="${MSG_FILE#"${WORK}/mail/"}"
    IFS='/' read -ra PARTS <<< "$REL"
    CUR_IDX=-1
    for i in "${!PARTS[@]}"; do
        if [[ "${PARTS[$i]}" == "cur" || "${PARTS[$i]}" == "new" ]]; then CUR_IDX=$i; fi
    done

    MAILBOX=INBOX
    if [[ $CUR_IDX -ge 2 && "${PARTS[$((CUR_IDX-1))]}" == .* ]]; then
        # Maildir++ encodes hierarchy as dots: ".Sent.2024" is Sent/2024.
        MAILBOX="${PARTS[$((CUR_IDX-1))]#.}"
        MAILBOX="${MAILBOX//./\/}"
        USER_IDX=$((CUR_IDX-2))
    else
        USER_IDX=$((CUR_IDX-1))
    fi
    DOMAIN_IDX=$((USER_IDX-1))
    MAIL_USER="${PARTS[$USER_IDX]}"
    MAIL_DOMAIN="${PARTS[$DOMAIN_IDX]:-${PARTS[0]}}"

    # Everything above the domain directory is the archive's own root, and it
    # varies: a whole-tree backup tars `mail_data/`, a single-domain one tars
    # the domain itself. Dovecot's mail_location is <root>/%d/%n, so pointing
    # it one level too high finds an empty mailbox and doveadm returns nothing
    # -- no error, just silence. Same wrong-nesting-level trap as the mailcow
    # migration.
    MAIL_ROOT="${WORK}/mail"
    for ((i = 0; i < DOMAIN_IDX; i++)); do
        MAIL_ROOT="${MAIL_ROOT}/${PARTS[$i]}"
    done
    info "test mailbox: ${MAIL_USER}@${MAIL_DOMAIN} (folder ${MAILBOX})"
    info "maildir root: ${MAIL_ROOT#"${WORK}/"}"

    if [[ "$ENCRYPTED_AT_REST" == true ]]; then
        info "starting Dovecot with ONLY the escrowed mail_crypt key"
        mkdir -p "${WORK}/dovecot"
        cp "$MAILCRYPT_KEY" "${WORK}/dovecot/ecprivkey.pem"
        chmod 644 "${WORK}/dovecot/ecprivkey.pem"

        cat > "${WORK}/dovecot/dovecot.conf" <<DOVECONF
# Minimal Dovecot for the drill. The only thing that matters here is that
# mail_crypt is loaded with the key recovered from ESCROW -- not a key copied
# off a production host.
mail_home = /srv/mail/%d/%n
mail_location = maildir:/srv/mail/%d/%n
# 5000 is the vmail uid the project image creates. NOT 0: Dovecot refuses to
# run mail processes as root ("userdb returned 0 as uid") no matter what
# first_valid_uid says, so a drill configured with uid=0 fails at fetch time
# having otherwise looked healthy.
mail_uid = 5000
mail_gid = 5000
first_valid_uid = 1
mail_plugins = \$mail_plugins mail_crypt
auth_mechanisms = plain
disable_plaintext_auth = no
log_path = /dev/stderr
ssl = no

passdb {
  driver = static
  args = password=drill
}
userdb {
  driver = static
  args = uid=5000 gid=5000 home=/srv/mail/%d/%n
}

plugin {
  mail_crypt_global_private_key = </keys/ecprivkey.pem
  mail_crypt_save_version = 2
}

service auth {
  unix_listener auth-userdb {
    mode = 0666
  }
}
namespace inbox {
  inbox = yes
  separator = /
}
DOVECONF

        if ! docker image inspect "$DOVECOT_IMAGE" >/dev/null 2>&1; then
            info "building ${DOVECOT_IMAGE} from ${DOVECOT_BUILD_CONTEXT}"
            docker build -q -t "$DOVECOT_IMAGE" "$DOVECOT_BUILD_CONTEXT" >/dev/null 2>&1
        fi

        docker rm -f "$DOVECOT_CONTAINER" >/dev/null 2>&1
        # `dovecot -F` rather than the image's supervisord entrypoint: the drill
        # needs Dovecot and nothing else, with the drill's own config.
        # The Maildir came out of a tar as whatever uid ran the drill; Dovecot
        # will not open a mail tree it does not own.
        docker run -d --name "$DOVECOT_CONTAINER" \
            --entrypoint sh \
            -v "${MAIL_ROOT}:/srv/mail" \
            -v "${WORK}/dovecot/dovecot.conf:/etc/dovecot/dovecot.conf:ro" \
            -v "${WORK}/dovecot:/keys:ro" \
            "$DOVECOT_IMAGE" -c 'chown -R 5000:5000 /srv/mail 2>/dev/null; exec dovecot -F' \
            >/dev/null 2>&1

        # Wait for the auth socket, not a fixed sleep. The container chowns the
        # restored tree before starting Dovecot, and on a full Maildir that is
        # ~31k files -- far longer than any sleep worth writing. A guess here
        # fails as "auth-userdb: No such file or directory", which reads like a
        # Dovecot misconfiguration rather than a race.
        for _ in $(seq 1 120); do
            docker exec "$DOVECOT_CONTAINER" test -S /run/dovecot/auth-userdb 2>/dev/null && break
            sleep 2
        done

        if docker exec "$DOVECOT_CONTAINER" test -S /run/dovecot/auth-userdb 2>/dev/null; then
            ok "Dovecot started with the escrowed key"

            FETCH=$(docker exec "$DOVECOT_CONTAINER" \
                doveadm fetch -u "${MAIL_USER}@${MAIL_DOMAIN}" text mailbox "$MAILBOX" 2>&1 | head -60)
            if printf '%s' "$FETCH" | grep -qiE 'Subject:|Content-Type:|Received:|From:'; then
                ok "MAIL IS READABLE THROUGH mail_crypt USING THE ESCROWED KEY"
                info "first readable header: $(printf '%s' "$FETCH" | grep -iE '^(Subject|From):' | head -1)"
            else
                bad "doveadm could not produce readable mail"
                info "${FETCH:-(doveadm returned no output at all -- wrong Maildir root?)}"
            fi
        else
            bad "Dovecot's auth socket never appeared"
            info "$(docker logs "$DOVECOT_CONTAINER" 2>&1 | tail -5)"
        fi
    else
        # Not encrypted at rest: readability is a direct check.
        if head -c 2000 "$MSG_FILE" | grep -qiE 'Subject:|From:'; then
            ok "restored message is readable"
        else
            bad "restored message is not readable and not mail_crypt-encrypted"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
ELAPSED=$(( $(date +%s) - DRILL_START ))
step "Drill result"
printf '  checks passed : %d\n' "$PASS"
printf '  checks failed : %d\n' "$FAIL"
printf '  MEASURED RTO  : %dm%02ds (restore only; excludes VPS provisioning and DNS)\n' \
    $((ELAPSED / 60)) $((ELAPSED % 60))
echo ""
if [[ $FAIL -eq 0 ]]; then
    printf '  \033[0;32mThe platform is rebuildable from S3 alone.\033[0m\n'
    exit 0
fi
printf '  \033[0;31mThe platform is NOT fully rebuildable from S3 -- see failures above.\033[0m\n'
exit 1
