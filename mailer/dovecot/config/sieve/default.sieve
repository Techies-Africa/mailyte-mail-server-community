require ["fileinto", "mailbox"];

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

# Notifications: automated alerts, OTP codes, system messages
if header :is "X-Email-Category" "notifications" {
  fileinto :create "Notifications";
  stop;
}

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
