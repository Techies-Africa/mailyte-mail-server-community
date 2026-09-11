# "copy" (RFC 3894) is what provides the `:copy` tag below. Without it the
# script fails to compile with "unknown tagged argument ':copy' for the pipe
# command" -- pipe itself comes from vnd.dovecot.pipe, the tag does not.
require ["fileinto", "mailbox", "envelope", "variables", "copy", "vnd.dovecot.pipe"];

# --- Step 0: Archive a copy before anything else -----------------------------
# Every rule below ends in `stop`, so this has to come first or messages that
# match any classification would never be archived.
#
# `:copy` means "in addition to normal delivery" -- without it the pipe would
# count as the delivery action and cancel the implicit keep, i.e. mail would be
# archived instead of delivered rather than as well as.
#
# archive-message never exits non-zero, so a failure here cannot defer mail.
# See mailer/dovecot/scripts/sieve-pipe/archive-message.
if envelope :matches "to" "*" {
  set "archive_rcpt" "${1}";
}
pipe :copy "archive-message" [ "${archive_rcpt}" ];

# =============================================================================
# Default Sieve Script — Smart Folder Routing
#
# Applied globally to all users. Handles:
#   1. Spam → Junk folder (from Rspamd X-Spam header)
#   2. Smart folder routing (from Rspamd X-Email-Category header)
#
# Users can override with their own Sieve scripts via ManageSieve (port 4190).
# Per-user scripts are stored in ~/sieve/ and take priority over this default.
# =============================================================================

# --- Step 1: Spam detection (highest priority) ---
# Rspamd adds "X-Spam: Yes" header when score exceeds add_header threshold
if header :is "X-Spam" "Yes" {
  fileinto :create "Junk";
  stop;
}

# --- Step 2: Smart folder routing based on content classification ---
# Rspamd's email_classifier.lua adds X-Email-Category header

# Notifications: DELIBERATELY NOT ROUTED -- these belong in the inbox.
#
# email_classifier.lua decides "notifications" by matching the sender's local
# part as a SUBSTRING against a list that includes "support", "help", "info",
# "service", "billing", "invoice", "order" and "account". Those are addresses
# people correspond from, not just robots, and substring matching widens it
# further -- "info" also catches "infosec@", "admin" catches "administrator@".
# Real mail was being filed out of the inbox, and since delivery succeeds there
# is no bounce or error to notice: indistinguishable from mail never arriving.
#
# The categories below are kept because their signals are structural rather
# than a guess at the sender's name: a known social domain, an ESP domain, or
# List-Id / Precedence: bulk headers.
#
# To route notifications again, fix the classifier first: exact-match local
# parts instead of substrings and drop the human-used addresses.

# Social: Facebook, LinkedIn, Twitter, etc.
if header :is "X-Email-Category" "social" {
  fileinto :create "Social";
  stop;
}

# Promotions: marketing emails, deals, bulk offers
if header :is "X-Email-Category" "promotions" {
  fileinto :create "Promotions";
  stop;
}

# Updates: newsletters, digests, mailing lists
if header :is "X-Email-Category" "updates" {
  fileinto :create "Updates";
  stop;
}

# --- Default: Primary inbox (no action needed, delivered to INBOX) ---
