#!/bin/bash
# =============================================================================
# Install the Mailyte backup timers
# =============================================================================
# scripts/backup.sh has always existed and nothing has ever run it. On a
# self-hosted install there is no ops team to notice, so this makes backups
# automatic in one command -- which is the whole difference between having a
# backup script and having backups.
#
# Defaults to LOCAL DISK only. That protects against the failure people
# actually hit first (a bad migration, `rm -rf` in the wrong directory, a
# corrupted table) and needs no account anywhere. It does NOT protect against
# losing the machine: for that you need an offsite copy, which is one variable
# away -- see docs/operations/backups.md.
#
# Host systemd rather than a container: a timer survives `docker compose down`,
# and backup.sh has to run host-side anyway to reach the Docker socket and the
# storage/ tree. A backup container that stops when the stack stops is missing
# exactly when it is needed.
#
#   sudo ./install-timers.sh                     # auto-detect root and user
#   sudo ./install-timers.sh --root /opt/mailyte --user mailyte
#   sudo ./install-timers.sh --status
#   sudo ./install-timers.sh --uninstall
# =============================================================================
set -euo pipefail

UNIT_DIR=/etc/systemd/system
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAILYTE_ROOT=""
MAILYTE_USER=""
ACTION=install
UNITS=(mailyte-backup-full mailyte-backup-incremental)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --root) MAILYTE_ROOT="$2"; shift 2 ;;
        --user) MAILYTE_USER="$2"; shift 2 ;;
        --status) ACTION=status; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        --help) sed -n '2,26p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ "$ACTION" == "status" ]]; then
    for u in "${UNITS[@]}"; do
        printf '\n=== %s ===\n' "$u"
        systemctl list-timers "${u}.timer" --all --no-pager 2>/dev/null | head -3
        systemctl is-enabled "${u}.timer" 2>&1 | sed 's/^/  enabled: /'
    done
    echo ""
    echo "Recent runs:  journalctl -u mailyte-backup-full --since '3 days ago'"
    exit 0
fi

[[ $EUID -eq 0 ]] || { echo "Must run as root (units live in ${UNIT_DIR})" >&2; exit 1; }

if [[ "$ACTION" == "uninstall" ]]; then
    for u in "${UNITS[@]}"; do
        systemctl disable --now "${u}.timer" 2>/dev/null || true
        rm -f "${UNIT_DIR}/${u}.timer" "${UNIT_DIR}/${u}.service"
        echo "removed ${u}"
    done
    systemctl daemon-reload
    exit 0
fi

# The install root is the repository root, two levels up from this script.
[[ -n "$MAILYTE_ROOT" ]] || MAILYTE_ROOT="$(cd "${SRC_DIR}/../.." && pwd)"
[[ -f "${MAILYTE_ROOT}/scripts/backup.sh" ]] || {
    echo "No scripts/backup.sh under ${MAILYTE_ROOT}. Pass --root explicitly." >&2
    exit 1
}

# Run as whoever owns the checkout: that account already has the Docker access
# and file permissions the backup needs. Running as root would create
# root-owned backup files the rest of the stack cannot manage.
[[ -n "$MAILYTE_USER" ]] || MAILYTE_USER="$(stat -c '%U' "${MAILYTE_ROOT}/scripts/backup.sh")"
id "$MAILYTE_USER" >/dev/null 2>&1 || { echo "No such user: ${MAILYTE_USER}" >&2; exit 1; }

if ! id -nG "$MAILYTE_USER" | tr ' ' '\n' | grep -qx docker && [[ "$MAILYTE_USER" != root ]]; then
    echo "WARNING: ${MAILYTE_USER} is not in the 'docker' group."
    echo "         backup.sh execs into the mysql and redis containers and will fail."
    echo "         Fix with: sudo usermod -aG docker ${MAILYTE_USER}"
fi

echo "root: ${MAILYTE_ROOT}"
echo "user: ${MAILYTE_USER}"

for u in "${UNITS[@]}"; do
    for kind in service timer; do
        src="${SRC_DIR}/${u}.${kind}"
        [[ -f "$src" ]] || { echo "missing unit file: ${src}" >&2; exit 1; }
        sed -e "s|__MAILYTE_ROOT__|${MAILYTE_ROOT}|g" \
            -e "s|__MAILYTE_USER__|${MAILYTE_USER}|g" \
            "$src" > "${UNIT_DIR}/${u}.${kind}"
        chmod 0644 "${UNIT_DIR}/${u}.${kind}"
    done
    echo "installed ${u}"
done

systemctl daemon-reload

for u in "${UNITS[@]}"; do
    # Enable the TIMER, never the service: enabling the service would run a
    # full backup at every boot.
    systemctl enable --now "${u}.timer"
done

echo ""
systemctl list-timers 'mailyte-*' --all --no-pager
echo ""
echo "Backups are now automatic: full nightly at 02:30, incremental hourly at :15."
echo "They are written to <root>/storage/backups on THIS machine only."
echo ""
echo "That does not survive losing this machine. To add an offsite copy, set"
echo "S3_BUCKET and AWS credentials in .env -- see docs/operations/backups.md."
echo ""
echo "Run one now to confirm it works:  sudo systemctl start mailyte-backup-full.service"
echo "Watch it:                         journalctl -u mailyte-backup-full -f"
