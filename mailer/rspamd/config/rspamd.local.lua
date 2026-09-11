-- =============================================================================
-- Rspamd Local Lua Configuration
-- Loads custom plugins for Mailyte
-- =============================================================================

-- Email Classifier — Smart folder routing
-- Classifies emails into: primary, notifications, social, promotions, updates
-- Adds X-Email-Category header for Dovecot Sieve routing
dofile('/etc/rspamd/local.d/lua/email_classifier.lua')

-- Transport Rules — enforcement of the rules the console has always been able
-- to write and nothing has ever applied. Off unless TRANSPORT_RULES_ENABLED is
-- true; loading it is safe, enforcing is the opt-in.
dofile('/etc/rspamd/local.d/lua/transport_rules.lua')
