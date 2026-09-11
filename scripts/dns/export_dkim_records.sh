#!/bin/bash
# =============================================================================
# Export every domain's DKIM public key in DNS-publishable form.
# =============================================================================
# Run on the mail server host, from the repo root. Read-only.
#
#   ./scripts/dns/export_dkim_records.sh              # TSV for cloudflare_apply.sh
#   ./scripts/dns/export_dkim_records.sh --zonefile   # ready-to-paste TXT records
#
# Keys are stored as PEM (`-----BEGIN PUBLIC KEY-----` + wrapped base64). The
# DNS form is the base64 alone, unwrapped -- so the armour and every newline
# come off. A 2048-bit key lands at 392 characters; anything materially
# shorter is a 1024-bit key, which usually means a record left behind by a
# previous mail system rather than one this server would sign with.
#
# The resulting TXT value is ~412 characters, over the 255-character limit for
# a single character-string. Cloudflare splits it automatically; a registrar
# panel may need it entered as consecutive quoted 255-char chunks, which DNS
# concatenates back together on read.
# =============================================================================
set -uo pipefail

MODE="${1:-tsv}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-mysql}"
API_CONTAINER="${API_CONTAINER:-mailyte-prod-api-1}"

PW="$(docker exec "$API_CONTAINER" printenv DB_PASSWORD 2>/dev/null)"
[[ -n "$PW" ]] || { echo "Error: could not read DB_PASSWORD from $API_CONTAINER." >&2; exit 1; }

SQL='
SELECT d.domain,
       k.selector,
       REPLACE(REPLACE(REPLACE(REPLACE(k.public_key,
         "-----BEGIN PUBLIC KEY-----",""),
         "-----END PUBLIC KEY-----",""),
         CHAR(10),""), CHAR(13),"") AS p
FROM domains d
JOIN dkim_keys k ON k.domain_id = d.id
WHERE k.active = 1
ORDER BY d.domain;
'

rows="$(docker exec -e P="$PW" -e Q="$SQL" "$MYSQL_CONTAINER" \
  sh -c 'mysql -umailuser -p"$P" mailserver -N --batch -e "$Q"' 2>/dev/null)"

[[ -n "$rows" ]] || { echo "Error: no DKIM rows returned." >&2; exit 1; }

if [[ "$MODE" == "--zonefile" ]]; then
    echo "# DKIM records -- generated $(date -u +%Y-%m-%d)"
    echo ""
fi

while IFS=$'\t' read -r domain selector pub; do
    [[ -z "$domain" ]] && continue
    if [[ ${#pub} -lt 300 ]]; then
        echo "# WARNING: $domain key is ${#pub} chars -- not RSA-2048" >&2
    fi
    if [[ "$MODE" == "--zonefile" ]]; then
        echo "${selector}._domainkey.${domain}. TXT \"v=DKIM1; k=rsa; p=${pub}\""
    else
        printf '%s\t%s\t%s\n' "$domain" "$selector" "$pub"
    fi
done <<< "$rows"
