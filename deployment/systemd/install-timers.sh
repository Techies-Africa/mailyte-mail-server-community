#!/bin/bash
# =============================================================================
# Install the DR systemd timers on a production host
# =============================================================================
# Host systemd rather than a compose service (PRD decision D7): a timer
# survives `docker compose down`, survives a deploy replacing config/, and
# backup.sh already has to run host-side to reach the Docker socket and the
# storage/ tree. A backup container that stops when the stack stops is a
# backup that is missing exactly when it is needed.
#
#   sudo ./install-timers.sh              # mail server (auto-detected)
#   sudo ./install-timers.sh --role web
#   sudo ./install-timers.sh --status
#   sudo ./install-timers.sh --uninstall
# =============================================================================
set -euo pipefail

UNIT_DIR=/etc/systemd/system
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLE=""
ACTION=install

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role) ROLE="$2"; shift 2 ;;
        --status) ACTION=status; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        --help) sed -n '2,16p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# The mail server is the one with the mail_crypt key; the web server runs
# Laravel. Detecting beats a flag nobody remembers to pass.
if [[ -z "$ROLE" ]]; then
    if [[ -d /var/www/mailyte-email-server/secrets/mail_crypt ]]; then ROLE=mail; else ROLE=web; fi
fi

case "$ROLE" in
    mail) UNITS=(mailyte-backup-full mailyte-backup-incremental mailyte-mail-sync) ;;
    web)  UNITS=(mailyte-backup-web-full mailyte-backup-web-incremental) ;;
    *)    echo "Unknown role: ${ROLE}" >&2; exit 1 ;;
esac

if [[ "$ACTION" == "status" ]]; then
    for u in "${UNITS[@]}"; do
        printf '\n=== %s ===\n' "$u"
        systemctl list-timers "${u}.timer" --all --no-pager 2>/dev/null | head -3
        systemctl is-enabled "${u}.timer" 2>&1 | sed 's/^/  enabled: /'
        systemctl show "${u}.service" -p Result -p ExecMainStatus 2>/dev/null | sed 's/^/  /'
    done
    exit 0
fi

[[ $EUID -eq 0 ]] || { echo "Must run as root (systemd units live in ${UNIT_DIR})" >&2; exit 1; }

if [[ "$ACTION" == "uninstall" ]]; then
    for u in "${UNITS[@]}"; do
        systemctl disable --now "${u}.timer" 2>/dev/null || true
        rm -f "${UNIT_DIR}/${u}.timer" "${UNIT_DIR}/${u}.service"
        echo "removed ${u}"
    done
    systemctl daemon-reload
    exit 0
fi

for u in "${UNITS[@]}"; do
    for kind in service timer; do
        src="${SRC_DIR}/${u}.${kind}"
        [[ -f "$src" ]] || { echo "missing unit file: ${src}" >&2; exit 1; }
        install -m 0644 "$src" "${UNIT_DIR}/${u}.${kind}"
    done
    echo "installed ${u}"
done

systemctl daemon-reload

for u in "${UNITS[@]}"; do
    # Enable the TIMER, never the service: enabling the service would make it
    # run once at every boot, which for a full backup means a 3 GB upload every
    # time the machine restarts.
    systemctl enable --now "${u}.timer"
    echo "enabled ${u}.timer"
done

echo ""
systemctl list-timers 'mailyte-*' --all --no-pager
