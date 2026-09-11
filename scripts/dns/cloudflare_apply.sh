#!/bin/bash
# =============================================================================
# Apply Mailyte mail DNS (MX / SPF / DKIM / DMARC) across the Cloudflare estate.
# =============================================================================
# DRY RUN BY DEFAULT. Nothing is written until you pass --apply.
#
#   export CLOUDFLARE_API_TOKEN=...        # needs Zone:Read + DNS:Edit
#   ./cloudflare_apply.sh                  # show the diff, change nothing
#   ./cloudflare_apply.sh --apply          # write it
#   ./cloudflare_apply.sh --apply --prune-mx        # also delete stale MX
#   ./cloudflare_apply.sh --apply techies.africa    # one domain only
#
# --- Two things this script must never do, and how it avoids them -----------
#
# 1. NEVER clobber an unrelated TXT record.
#    A zone apex typically holds several TXT records at the same name: SPF,
#    Google site verification, a Brevo code. Selecting "the TXT record at
#    $domain" and updating it overwrites whichever happens to sort first --
#    which in testing was a live google-site-verification on seven domains.
#    Every TXT here is located by its CONTENT PREFIX (v=spf1, v=DKIM1,
#    v=DMARC1), and created fresh when no record with that prefix exists.
#
# 2. NEVER compare against a quoted string.
#    The Cloudflare API returns TXT content wrapped in literal double quotes:
#    "v=spf1 a mx -all". A startswith("v=spf1") test therefore fails on every
#    real record, the script concludes no SPF exists, and falls through to
#    creating one -- see failure 1. Content is unquoted before any comparison.
#
# --- Scope is deliberate, not "every zone on the token" ---------------------
#
#   * Only domains that actually have mailboxes on the new mail server.
#     peekride.com (Outlook) and customer-12.example (Zoho) have ZERO mailboxes; their
#     mail is not ours and changing their MX would break it.
#   * Only domains that currently resolve. customer-15.example and customer-16.example
#     return SERVFAIL; a record written into a broken zone changes nothing.
#
# SPF is MERGED, never replaced. An existing record may authorise senders that
# have nothing to do with mail hosting; only the Namecheap forwarding include
# is dropped. The trailing `all` qualifier is preserved, so a domain on `-all`
# is not silently relaxed to `~all` as a side effect of a mail migration.
# =============================================================================
set -uo pipefail

API="https://api.cloudflare.com/client/v4"
MX_HOST="${MX_HOST:-mail.mailyte.com}"

# The include target must EXIST in DNS or receivers get an SPF permerror,
# which is worse than publishing no SPF at all -- and the verifier only checks
# that the string is present, never that it resolves, so a wrong value here
# fails silently while looking verified.
#
# spf.mail.mailyte.com was created 2026-08-27 and holds
# "v=spf1 ip4:66.29.133.223 -all". It must stay in step with MAIL_SPF_HOST on
# the mail server, or the panel advertises one include and this publishes
# another. spf.courier.mailyte.com still exists and is identical, kept as the
# server's own name; customers are only ever shown the `mail` one.
SPF_INCLUDE="${SPF_INCLUDE:-spf.mail.mailyte.com}"

RETIRE_INCLUDE="spf.efwd.registrar-servers.com"
DKIM_FILE="${DKIM_FILE:-$(dirname "$0")/dkim_raw.tsv}"

DOMAINS=(
  techies.africa      # 35 mailboxes
  customer-11.example        # 12
  customer-07.example     # 10
  customer-01.example         #  9  (publishes a STALE 1024-bit DKIM key from mailcow)
  customer-06.example             #  4  (separate Cloudflare account -- needs its own token)
  customer-03.example   #  2
  customer-04.example        #  2  (5 stale Namecheap eforward MX records still live)
  customer-09.example     #  2
  customer-13.example         #  2
  customer-10.example            #  1
  customer-14.example          #  1  (already correct; included so it stays that way)
  # Zero mailboxes today, included at the owner's request so the domain is
  # configured and consistent before anyone is added to it. Note the
  # behaviour change this brings: with no MX, mail to the domain fails at the
  # lookup; with an MX pointing here and no mailbox to match, our server
  # answers "user unknown" and the sender gets a proper bounce. Better
  # feedback, but it IS a change -- do not add a domain here casually.
  customer-08.example          #  0
)

APPLY=0
PRUNE_MX=0
ONLY=""
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --prune-mx) PRUNE_MX=1 ;;
    --*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) ONLY="$arg" ;;
  esac
done

[[ -n "${CLOUDFLARE_API_TOKEN:-}" ]] || { echo "Error: CLOUDFLARE_API_TOKEN not set." >&2; exit 1; }
[[ -f "$DKIM_FILE" ]] || { echo "Error: $DKIM_FILE not found (domain<TAB>selector<TAB>base64)." >&2; exit 1; }
command -v jq >/dev/null || { echo "Error: jq is required." >&2; exit 1; }

# Every call retries, and FAILS LOUDLY rather than returning an empty result.
#
# This matters more than it looks. A dropped TLS connection (LibreSSL on macOS
# throws "bad record mac" under rapid successive requests) produces an empty
# body. Treated as data, an empty body means "no such record" -- so the script
# would decide a domain has no SPF and create a second one alongside the one
# that is really there. Two SPF records is a permerror: strictly worse than
# either record alone, and it breaks mail for a domain that was fine.
# A lookup that fails must abort that record, never fall through to create.
cf() {
  local method="$1" path="$2" body="${3:-}" attempt=1 out rc
  while [[ $attempt -le 3 ]]; do
    if [[ -n "$body" ]]; then
      out=$(curl -sS --http1.1 --connect-timeout 15 --max-time 45 -X "$method" "$API$path" \
        -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
        -H "Content-Type: application/json" --data "$body" 2>/dev/null); rc=$?
    else
      out=$(curl -sS --http1.1 --connect-timeout 15 --max-time 45 -X "$method" "$API$path" \
        -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>/dev/null); rc=$?
    fi
    if [[ $rc -eq 0 ]] && jq -e '.success' >/dev/null 2>&1 <<< "$out"; then
      echo "$out"; return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  echo "${out:-}"
  return 1
}

ok() { [[ "$(echo "$1" | jq -r '.success' 2>/dev/null)" == "true" ]]; }
err() { echo "$1" | jq -r '.errors[0].message // "request failed"' 2>/dev/null || echo "request failed"; }

# Locate a TXT record by content prefix, unquoting first. Emits "id<TAB>content".
# Returns 2 if the lookup itself failed -- distinct from "found nothing".
find_txt() {
  local zone="$1" name="$2" prefix="$3" json
  json=$(cf GET "/zones/$zone/dns_records?type=TXT&name=$name&per_page=100") || return 2
  # A TXT value longer than 255 characters is stored as several
  # character-strings, and the API hands them back as one field with the
  # chunk boundaries still in it: "first255..." "...rest". Stripping only the
  # outer quotes leaves an internal `" "` in the middle of the value, which
  # made a byte-identical DKIM key measure 3 characters longer than the one we
  # hold and report as needing replacement. Collapse the boundaries first.
  jq -r --arg p "$prefix" '
        .result[]
        | . as $r
        | ($r.content | gsub("\" +\""; "") | gsub("^\"|\"$"; "")) as $c
        | select($c | startswith($p))
        | "\($r.id)\t\($c)"' <<< "$json" \
    | head -1
}

write_txt() {
  local zone="$1" id="$2" name="$3" content="$4" label="$5" res payload
  payload=$(jq -nc --arg n "$name" --arg c "$content" '{type:"TXT",name:$n,content:$c,ttl:300}')
  [[ $APPLY -eq 1 ]] || return 0
  if [[ -n "$id" ]]; then
    res=$(cf PUT "/zones/$zone/dns_records/$id" "$payload")
  else
    res=$(cf POST "/zones/$zone/dns_records" "$payload")
  fi
  ok "$res" && echo "          done" || { echo "          FAILED: $(err "$res")"; return 1; }
}

# Merge an existing SPF with ours, dropping the forwarding include we retire.
merge_spf() {
  local current="$1" out="v=spf1" seen_include=0 qualifier="" m
  if [[ -n "$current" ]]; then
    for m in $current; do
      case "$m" in
        v=spf1) ;;
        *"$RETIRE_INCLUDE"*) ;;
        "include:$SPF_INCLUDE") seen_include=1; out="$out $m" ;;
        ?all|all) qualifier="$m" ;;
        *) out="$out $m" ;;
      esac
    done
  fi
  [[ $seen_include -eq 0 ]] && out="$out include:$SPF_INCLUDE"
  echo "$out ${qualifier:-~all}"
}

[[ $APPLY -eq 1 ]] && echo "=== APPLYING CHANGES ===" || echo "=== DRY RUN (pass --apply to write) ==="
echo "    MX          -> $MX_HOST"
echo "    SPF include -> $SPF_INCLUDE"
[[ $PRUNE_MX -eq 1 ]] && echo "    stale MX    -> WILL BE DELETED"
echo ""

processed=0; skipped=0; failed=0

for domain in "${DOMAINS[@]}"; do
  [[ -n "$ONLY" && "$ONLY" != "$domain" ]] && continue
  echo "  $domain"

  zone=$(cf GET "/zones?name=$domain" | jq -r '.result[0].id // empty')
  if [[ -z "$zone" ]]; then
    echo "      - not on this Cloudflare token, skipped"
    skipped=$((skipped + 1)); echo ""; continue
  fi

  # --- MX -------------------------------------------------------------------
  # Both mail.mailyte.com and courier.mailyte.com resolve to the same server,
  # so either is a correct destination and neither is worth rewriting. Only
  # customer-14.example is on `courier`, and it is the one domain that currently passes
  # verification -- swapping it to `mail` for tidiness would break it, since
  # the server still generates records against the `courier` name. Churn on a
  # working MX record risks live mail for no delivery benefit.
  #
  # What DOES need removing is an MX pointing somewhere else entirely:
  # customer-04.example still lists five Namecheap eforward hosts at equal or lower
  # priority, so its inbound mail is being split between two systems.
  mx_json=$(cf GET "/zones/$zone/dns_records?type=MX&name=$domain&per_page=100") || {
    echo "      ! MX lookup failed after retries -- skipped"
    failed=$((failed + 1)); mx_json='{"result":[]}'
  }
  mine=$(jq -r '.result[] | select(.content=="mail.mailyte.com" or .content=="courier.mailyte.com") | .content' <<< "$mx_json" | head -1)
  stale=$(jq -r '.result[] | select(.content!="mail.mailyte.com" and .content!="courier.mailyte.com") | "\(.id)\t\(.priority)\t\(.content)"' <<< "$mx_json")

  if [[ -n "$mine" ]]; then
    echo "      = MX $mine present"
  else
    echo "      + MX $MX_HOST (priority 10)"
    if [[ $APPLY -eq 1 ]]; then
      res=$(cf POST "/zones/$zone/dns_records" \
        "$(jq -nc --arg n "$domain" --arg c "$MX_HOST" '{type:"MX",name:$n,content:$c,priority:10,ttl:300}')")
      ok "$res" && echo "          done" || { echo "          FAILED: $(err "$res")"; failed=$((failed+1)); }
    fi
  fi

  if [[ -n "$stale" ]]; then
    while IFS=$'\t' read -r sid sprio scontent; do
      [[ -z "$sid" ]] && continue
      if [[ $PRUNE_MX -eq 1 ]]; then
        echo "      - MX prio $sprio $scontent  (deleting)"
        if [[ $APPLY -eq 1 ]]; then
          res=$(cf DELETE "/zones/$zone/dns_records/$sid")
          ok "$res" && echo "          deleted" || { echo "          FAILED: $(err "$res")"; failed=$((failed+1)); }
        fi
      else
        echo "      ! MX prio $sprio $scontent  (stale -- splits mail; --prune-mx to remove)"
      fi
    done <<< "$stale"
  fi

  # --- SPF ------------------------------------------------------------------
  spf_row=$(find_txt "$zone" "$domain" "v=spf1"); spf_rc=$?
  if [[ $spf_rc -eq 2 ]]; then
    echo "      ! SPF lookup failed after retries -- skipped (will NOT create a duplicate)"
    failed=$((failed + 1))
    spf_skip=1
  else
    spf_skip=0
  fi
  spf_id=$(cut -f1 <<< "$spf_row"); spf_now=$(cut -f2 <<< "$spf_row")
  [[ "$spf_row" == "" ]] && { spf_id=""; spf_now=""; }
  spf_new=$(merge_spf "$spf_now")

  if [[ $spf_skip -eq 1 ]]; then
    :
  elif [[ "$spf_now" == "$spf_new" ]]; then
    echo "      = SPF already correct"
  elif [[ -n "$spf_id" ]]; then
    echo "      ~ SPF"
    echo "          from: $spf_now"
    echo "          to:   $spf_new"
    write_txt "$zone" "$spf_id" "$domain" "$spf_new" "SPF" || failed=$((failed+1))
  else
    echo "      + SPF (no v=spf1 record existed)"
    echo "          set:  $spf_new"
    write_txt "$zone" "" "$domain" "$spf_new" "SPF" || failed=$((failed+1))
  fi

  # --- DMARC ----------------------------------------------------------------
  # Only ever ADDED, never rewritten. An existing policy is a deliberate
  # decision about what receivers do with failing mail; a migration script does
  # not get to relax it. Where none exists we publish p=none -- monitoring
  # only, because jumping straight to quarantine on a domain that has never had
  # DMARC silently diverts mail from senders nobody has inventoried yet.
  dmarc_row=$(find_txt "$zone" "_dmarc.$domain" "v=DMARC1"); dmarc_rc=$?
  if [[ $dmarc_rc -eq 2 ]]; then
    echo "      ! DMARC lookup failed after retries -- skipped"
    failed=$((failed + 1))
  elif [[ -n "$dmarc_row" ]]; then
    echo "      = DMARC present, left alone ($(cut -f2 <<< "$dmarc_row" | grep -o 'p=[a-z]*' | head -1))"
  else
    echo "      + DMARC (none present)"
    echo "          set:  v=DMARC1; p=none; rua=mailto:dmarc-reports@$domain"
    write_txt "$zone" "" "_dmarc.$domain" \
      "v=DMARC1; p=none; rua=mailto:dmarc-reports@$domain" "DMARC" || failed=$((failed+1))
  fi

  # --- DKIM -----------------------------------------------------------------
  line=$(awk -F'\t' -v d="$domain" '$1==d {print; exit}' "$DKIM_FILE")
  if [[ -z "$line" ]]; then
    echo "      ! no DKIM key on file for $domain"
    failed=$((failed + 1))
  else
    sel=$(cut -f2 <<< "$line"); pub=$(cut -f3 <<< "$line")
    dkim_name="${sel}._domainkey.$domain"
    dkim_want="v=DKIM1; k=rsa; p=$pub"
    dkim_row=$(find_txt "$zone" "$dkim_name" "v=DKIM1"); dkim_rc=$?
    dkim_id=$(cut -f1 <<< "$dkim_row"); dkim_now=$(cut -f2 <<< "$dkim_row")
    [[ "$dkim_row" == "" ]] && { dkim_id=""; dkim_now=""; }

    # Compare on the key material alone: the live record may differ only in
    # spacing ("v=DKIM1;k=rsa;p=") and that is not a reason to rewrite it.
    now_p=$(sed 's/.*p=//' <<< "$dkim_now")
    if [[ $dkim_rc -eq 2 ]]; then
      echo "      ! DKIM lookup failed after retries -- skipped"
      failed=$((failed + 1))
    elif [[ "$now_p" == "$pub" ]]; then
      echo "      = DKIM already correct"
    elif [[ -n "$dkim_id" ]]; then
      echo "      ~ DKIM ($sel)  ${#now_p} -> ${#pub} chars"
      [[ ${#now_p} -lt 300 ]] && echo "          (replacing a 1024-bit key -- signatures currently FAIL)"
      write_txt "$zone" "$dkim_id" "$dkim_name" "$dkim_want" "DKIM" || failed=$((failed+1))
    else
      echo "      + DKIM ($sel)"
      write_txt "$zone" "" "$dkim_name" "$dkim_want" "DKIM" || failed=$((failed+1))
    fi
  fi

  processed=$((processed + 1))
  echo ""
done

echo "=== processed: $processed   skipped: $skipped   failures: $failed ==="
[[ $APPLY -eq 0 ]] && echo "Nothing was written. Re-run with --apply."
exit 0
