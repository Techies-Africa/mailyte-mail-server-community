-- =============================================================================
-- Rspamd Local Lua Configuration
-- Loads custom plugins for Mailyte
-- =============================================================================

-- Email Classifier — Smart folder routing
-- Classifies emails into: primary, notifications, social, promotions, updates
-- Adds X-Email-Category header for Dovecot Sieve routing
dofile('/etc/rspamd/local.d/lua/email_classifier.lua')
