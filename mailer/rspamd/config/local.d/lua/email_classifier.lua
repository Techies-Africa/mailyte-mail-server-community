-- =============================================================================
-- Email Classifier — Smart Folder Routing via Rspamd
--
-- Classifies inbound emails into categories and adds X-Email-Category header:
--   - primary:       Personal/important mail (default inbox)
--   - notifications: Automated alerts, system notifications, OTP codes
--   - social:        Social network notifications (Facebook, LinkedIn, etc.)
--   - promotions:    Marketing, deals, newsletters with offers
--   - updates:       Newsletters, digests, informational updates
--
-- The Sieve script reads X-Email-Category and routes to the correct folder.
-- Phase 2 will replace heuristics with AI/ML classification via RAG service.
-- =============================================================================

local rspamd_logger = require "rspamd_logger"

-- Social network domains
local social_domains = {
  ["facebook.com"] = true, ["facebookmail.com"] = true,
  ["twitter.com"] = true, ["x.com"] = true,
  ["linkedin.com"] = true, ["linkedinmail.com"] = true,
  ["instagram.com"] = true,
  ["pinterest.com"] = true,
  ["tiktok.com"] = true,
  ["reddit.com"] = true, ["redditmail.com"] = true,
  ["discord.com"] = true, ["discordapp.com"] = true,
  ["slack.com"] = true,
  ["telegram.org"] = true,
  ["whatsapp.com"] = true,
  ["snapchat.com"] = true,
  ["tumblr.com"] = true,
  ["mastodon.social"] = true,
  ["meetup.com"] = true,
  ["nextdoor.com"] = true,
  ["quora.com"] = true,
  ["medium.com"] = true,
}

-- Known notification sender patterns (local parts)
local notification_senders = {
  "noreply", "no-reply", "no_reply",
  "notifications", "notification",
  "alert", "alerts",
  "mailer-daemon", "postmaster",
  "system", "admin",
  "security", "verify", "verification",
  "confirm", "confirmation",
  "donotreply", "do-not-reply",
  "auto", "automated",
  "support", "help",
  "info", "service",
  "billing", "invoice",
  "order", "orders", "shipping",
  "account", "accounts",
  "otp", "2fa", "mfa",
}

-- Marketing/promotion platform domains
local promo_domains = {
  ["mailchimp.com"] = true, ["mandrillapp.com"] = true,
  ["sendgrid.net"] = true, ["sendgrid.com"] = true,
  ["constantcontact.com"] = true,
  ["hubspot.com"] = true, ["hubspotemail.net"] = true,
  ["mailgun.org"] = true, ["mailgun.com"] = true,
  ["amazonses.com"] = true,
  ["salesforce.com"] = true, ["exacttarget.com"] = true,
  ["campaign-archive.com"] = true,
  ["createsend.com"] = true,
  ["cmail19.com"] = true, ["cmail20.com"] = true,
  ["brevo.com"] = true, ["sendinblue.com"] = true,
  ["klaviyo.com"] = true,
  ["activecampaign.com"] = true,
  ["drip.com"] = true,
  ["convertkit.com"] = true,
  ["getresponse.com"] = true,
  ["aweber.com"] = true,
  ["intercom.io"] = true, ["intercom-mail.com"] = true,
  ["customer.io"] = true,
  ["postmarkapp.com"] = true,
}

-- Promotion keywords in subject (lowercase)
local promo_keywords = {
  "unsubscribe", "% off", "discount", "sale", "deal",
  "limited time", "exclusive offer", "free trial", "subscribe",
  "coupon", "promo", "promotion", "special offer",
  "flash sale", "clearance", "buy now", "shop now",
  "order now", "act now", "don't miss",
}

-- Subjects that mean "act on this now": one-time codes, sign-in
-- verification, password resets, security alerts.
--
-- Checked before every bulk heuristic below. A Zoho sign-in OTP was being
-- filed into Promotions -- it carries a Feedback-ID, which the promotions
-- branch treated as proof of marketing, and promotions is evaluated before
-- notifications, so "otp" in the sender never got a chance to win. A code
-- that expires in ten minutes sitting in a folder nobody watches is the worst
-- possible misfile, so this outranks everything except spam.
local transactional_keywords = {
  "otp", "one-time", "one time pass", "verification code", "security code",
  "login code", "sign-in", "sign in code", "signin", "2fa", "two-factor",
  "authentication code", "verify your", "verification", "confirm your",
  "password reset", "reset your password", "security alert", "suspicious",
  "access code", "passcode", "confirmation code",
}

-- Helper: extract domain from email address
local function get_domain(email)
  if not email then return nil end
  local domain = email:match("@([%w%.%-]+)$")
  if domain then return domain:lower() end
  return nil
end

-- Helper: extract local part from email address
local function get_local_part(email)
  if not email then return nil end
  local local_part = email:match("^([^@]+)@")
  if local_part then return local_part:lower() end
  return nil
end

-- Helper: check if string contains any pattern from list
local function contains_any(str, patterns)
  if not str then return false end
  str = str:lower()
  for _, pattern in ipairs(patterns) do
    if str:find(pattern, 1, true) then
      return true
    end
  end
  return false
end

-- Main classification function
local function classify_email(task)
  -- Skip outgoing mail (only classify inbound)
  if task:has_symbol('DKIM_SIGNED') and not task:has_symbol('DKIM_VALID') then
    -- Likely our own outgoing mail
    return
  end

  local from = task:get_from('mime')
  local from_addr = from and from[1] and from[1].addr
  local from_domain = get_domain(from_addr)
  local from_local = get_local_part(from_addr)
  local subject = task:get_subject() or ""
  local category = "primary"  -- Default

  -- Check headers for classification signals
  local list_unsub = task:get_header('List-Unsubscribe')
  local precedence = task:get_header('Precedence')
  local auto_submitted = task:get_header('Auto-Submitted')
  local x_mailer = task:get_header('X-Mailer')
  local feedback_id = task:get_header('Feedback-ID')
  local list_id = task:get_header('List-Id')

  -- ---------------------------------------------------------------
  -- 0. Transactional / security mail — outranks every bulk heuristic
  -- ---------------------------------------------------------------
  -- Deliberately first. These arrive through the same platforms as marketing
  -- and carry the same bulk headers, so any domain- or header-based rule
  -- below will happily file a login code under Promotions. The subject is the
  -- only signal that separates them, and getting this wrong costs someone
  -- their sign-in rather than an unread advert.
  if contains_any(subject, transactional_keywords) then
    category = "notifications"

  -- ---------------------------------------------------------------
  -- 1. Social network detection
  -- ---------------------------------------------------------------
  elseif from_domain and social_domains[from_domain] then
    category = "social"

  -- ---------------------------------------------------------------
  -- 2. Promotion/Marketing detection
  -- ---------------------------------------------------------------
  elseif from_domain and promo_domains[from_domain] then
    category = "promotions"
  elseif feedback_id and (list_unsub or contains_any(subject, promo_keywords)) then
    -- Feedback-ID marks a bulk-capable SENDING PLATFORM, not a marketing
    -- message: Zoho, SES and the rest attach it to one-time passcodes and
    -- receipts too. On its own it filed every transactional mail from those
    -- providers under Promotions, so it now needs corroboration -- an
    -- unsubscribe link or actual promotional language.
    category = "promotions"
  elseif list_unsub and contains_any(subject, promo_keywords) then
    -- Has unsubscribe link AND promotional language in subject
    category = "promotions"

  -- ---------------------------------------------------------------
  -- 3. Notification detection
  -- ---------------------------------------------------------------
  elseif auto_submitted and auto_submitted:lower() ~= "no" then
    -- Auto-Submitted header indicates automated messages
    category = "notifications"
  elseif from_local then
    for _, pattern in ipairs(notification_senders) do
      if from_local == pattern or from_local:find(pattern, 1, true) then
        category = "notifications"
        break
      end
    end

  -- ---------------------------------------------------------------
  -- 4. Updates/Newsletter detection
  -- ---------------------------------------------------------------
  elseif list_id then
    -- List-Id header indicates mailing list
    category = "updates"
  elseif precedence and (precedence:lower() == "bulk" or precedence:lower() == "list") then
    category = "updates"
  elseif list_unsub then
    -- Has unsubscribe but no promo keywords — likely a newsletter/update
    category = "updates"
  end

  -- ---------------------------------------------------------------
  -- 5. Set the classification header
  -- ---------------------------------------------------------------
  if category ~= "primary" then
    task:set_milter_reply({
      add_headers = {
        ['X-Email-Category'] = { value = category, order = 1 }
      }
    })
    rspamd_logger.infox(task, "classified email from %s as: %s", from_addr or "unknown", category)
  end
end

-- Register the classifier as a callback symbol
local classifier_id = rspamd_config:register_symbol({
  name = 'EMAIL_CLASSIFIER',
  type = 'callback',
  callback = classify_email,
  score = 0.0,
  description = 'Classify email into smart folder categories',
  -- Run after other checks but before milter_headers
  priority = 5,
})

-- Register category symbols for tracking/statistics
local categories = {"social", "notifications", "promotions", "updates"}
for _, cat in ipairs(categories) do
  local sym_name = "EMAIL_CAT_" .. cat:upper()
  rspamd_config:register_symbol({
    name = sym_name,
    type = 'virtual',
    parent = classifier_id,
    score = 0.0,
    description = 'Email classified as ' .. cat,
  })
end
