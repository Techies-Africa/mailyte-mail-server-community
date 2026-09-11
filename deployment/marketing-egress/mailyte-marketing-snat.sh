#!/usr/bin/env bash
# Give the marketing sending stream its own public egress IP.
#
# Postfix runs on a Docker BRIDGE network, so `smtp_bind_address` cannot bind
# the public marketing IP (it is not in the container's namespace) and Docker's
# MASQUERADE rewrites every container's outbound source to the host's primary
# address. Receiver headers proved it on 2026-09-06: the marketing transport
# announced relay.mailyte.com but connected from 66.29.133.223.
#
# The fix is a two-part handshake:
#   - the postfix entrypoint adds MARKETING_CONTAINER_BIND_IP (a bridge-
#     internal alias) to the container and the marketing transport binds it;
#   - this script SNATs exactly that source to MARKETING_BIND_IP, inserted
#     AHEAD of Docker's MASQUERADE so it wins. Transactional/mailbox mail keeps
#     the container's primary address and still masquerades to the primary IP.
#
# Idempotent: safe to run on every boot / docker restart (the systemd unit
# does). Reads its values from the persisted .env so it can never disagree
# with what the container was started with.
set -euo pipefail

ENV_FILE="${MAILYTE_ENV_FILE:-/var/www/mailyte-email-server/.env}"
NETWORK_NAME="${MAILYTE_BRIDGE_NETWORK:-mailyte-prod_mailserver_network}"

getenv() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

PUBLIC_IP="$(getenv MARKETING_BIND_IP)"
ALIAS_IP="$(getenv MARKETING_CONTAINER_BIND_IP)"

if [ -z "$PUBLIC_IP" ] || [ -z "$ALIAS_IP" ]; then
  echo "marketing-snat: MARKETING_BIND_IP or MARKETING_CONTAINER_BIND_IP unset in $ENV_FILE; nothing to do"
  exit 0
fi

# The bridge interface is br-<first 12 chars of the network id>. Excluding it
# (! -o) mirrors Docker's own MASQUERADE rule: intra-bridge traffic must not
# be rewritten, only what leaves the host.
NET_ID="$(docker network inspect "$NETWORK_NAME" --format '{{.Id}}' 2>/dev/null | cut -c1-12)"
if [ -z "$NET_ID" ]; then
  echo "marketing-snat: docker network $NETWORK_NAME not found (docker not up yet?)" >&2
  exit 1
fi
BRIDGE="br-${NET_ID}"

RULE=(-s "${ALIAS_IP}/32" ! -o "$BRIDGE" -j SNAT --to-source "$PUBLIC_IP")

# Drop any stale rule for this alias that points somewhere else (e.g. after
# the public IP changes), then ensure exactly one correct rule at position 1.
while read -r line; do
  [ -z "$line" ] && continue
  # shellcheck disable=SC2086
  iptables -t nat -D POSTROUTING $line 2>/dev/null || true
done < <(iptables -t nat -S POSTROUTING | grep -- "-s ${ALIAS_IP}/32" | grep -v -- "--to-source ${PUBLIC_IP}" | sed 's/^-A POSTROUTING //')

if iptables -t nat -C POSTROUTING "${RULE[@]}" 2>/dev/null; then
  echo "marketing-snat: rule already present (${ALIAS_IP} -> ${PUBLIC_IP} via ${BRIDGE})"
else
  iptables -t nat -I POSTROUTING 1 "${RULE[@]}"
  echo "marketing-snat: inserted ${ALIAS_IP} -> ${PUBLIC_IP} (! -o ${BRIDGE}) at POSTROUTING 1"
fi
