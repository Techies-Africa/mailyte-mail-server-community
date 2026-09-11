-- =============================================================================
-- Transport Rules — enforcement
--
-- Transport rules have been storable, orderable and exportable since the
-- feature was built, and nothing has ever applied one to a message. The API
-- wrote `transport_rules`; no milter, filter or Postfix integration read it.
-- A rule authored in the console changed nothing about how mail was handled.
--
-- This is the reading half. It runs inside rspamd because rspamd is already
-- the only milter Postfix calls (main.cf: smtpd_milters = inet:rspamd:11332),
-- so it is the one place in this stack with full message content AND the
-- ability to act on it. Writing a second, separate milter would mean a second
-- process in the delivery path for no gain.
--
-- WHAT THIS CANNOT DO
--   redirect and bcc change where a message is delivered. A milter cannot
--   reroute delivery -- that is Postfix's job. Rules using those actions are
--   evaluated and then SKIPPED here, and counted in the log, rather than
--   silently appearing to work. Wiring them means emitting a header for
--   header_checks to act on, which is a separate change to mail routing.
--
-- OFF BY DEFAULT
--   Enforcement only runs when TRANSPORT_RULES_ENABLED is true in the rspamd
--   environment. This changes what happens to live mail, so it is opt-in: an
--   operator turns it on deliberately, having read what their existing rules
--   say, rather than discovering on deploy that rules written months ago as
--   drafts are suddenly in force.
-- =============================================================================

local rspamd_logger = require "rspamd_logger"
local rspamd_redis = require "rspamd_redis"
local ucl = require "ucl"

local N = "transport_rules"

-- Rules are read from Redis, not MySQL. A database round trip per message puts
-- mail flow behind the database: if MySQL is slow, mail is slow, and if MySQL
-- is down, mail stops. The API republishes this key whenever a rule changes,
-- so Redis is the cache and MySQL stays the source of truth.
local REDIS_KEY = "mailyte:transport_rules"

local settings = {
  enabled = (os.getenv("TRANSPORT_RULES_ENABLED") or ""):lower() == "true",
  servers = "redis:6379",
  timeout = 2.0,
}

-- ---------------------------------------------------------------------------
-- Condition evaluation
-- ---------------------------------------------------------------------------

local function value_for_field(task, field)
  if field == "sender" then
    local from = task:get_from("smtp")
    return from and from[1] and from[1].addr or ""
  elseif field == "recipient" then
    local rcpt = task:get_recipients("smtp")
    return rcpt and rcpt[1] and rcpt[1].addr or ""
  elseif field == "subject" then
    return task:get_header("Subject") or ""
  elseif field == "size" then
    return tostring(task:get_size() or 0)
  elseif field == "has_attachment" then
    local parts = task:get_parts() or {}
    for _, p in ipairs(parts) do
      if p:is_attachment() then return "true" end
    end
    return "false"
  end
  -- `header` carries the header name in the condition value, so the caller
  -- handles it; anything else is a field this build does not know.
  return nil
end

local function matches(operator, actual, expected)
  actual = tostring(actual or ""):lower()
  expected = tostring(expected or ""):lower()

  if operator == "equals" then
    return actual == expected
  elseif operator == "contains" then
    return actual:find(expected, 1, true) ~= nil
  elseif operator == "starts_with" then
    return actual:sub(1, #expected) == expected
  elseif operator == "ends_with" then
    return #expected <= #actual and actual:sub(-#expected) == expected
  elseif operator == "regex" then
    -- pcall: an operator can save any string as a pattern, and a bad one must
    -- fail that single rule rather than error the whole scan and, with
    -- milter_default_action = accept, wave every message through unchecked.
    local ok, found = pcall(function() return actual:find(expected) ~= nil end)
    return ok and found
  elseif operator == "greater_than" then
    return (tonumber(actual) or 0) > (tonumber(expected) or 0)
  elseif operator == "less_than" then
    return (tonumber(actual) or 0) < (tonumber(expected) or 0)
  end
  return false
end

local function rule_matches(task, rule)
  local conditions = rule.conditions or {}
  if #conditions == 0 then
    -- A rule with no conditions would match every message. Refuse rather than
    -- apply: it is far more likely to be a half-finished rule than an
    -- intentional catch-all, and the blast radius is all mail.
    return false
  end

  for _, cond in ipairs(conditions) do
    local actual
    if cond.field == "header" then
      actual = task:get_header(cond.header or cond.value_field or "") or ""
    else
      actual = value_for_field(task, cond.field)
    end
    if actual == nil then return false end
    if not matches(cond.operator, actual, cond.value) then
      -- Conditions are ANDed. The schema has no operator to say otherwise, and
      -- guessing OR would make rules fire far more often than their author
      -- expected.
      return false
    end
  end
  return true
end

-- ---------------------------------------------------------------------------
-- Actions
-- ---------------------------------------------------------------------------

local function apply_actions(task, rule)
  for _, action in ipairs(rule.actions or {}) do
    local params = action.params or {}

    if action.type == "add_header" then
      task:set_milter_reply({
        add_headers = { [params.name or "X-Mailyte-Rule"] = params.value or rule.name },
      })

    elseif action.type == "modify_subject" then
      local subject = task:get_header("Subject") or ""
      task:set_milter_reply({
        remove_headers = { Subject = 1 },
        add_headers = { Subject = (params.prefix or "[FLAGGED] ") .. subject },
      })

    elseif action.type == "reject" then
      task:set_pre_result("reject", params.message or "Rejected by transport rule", N)

    elseif action.type == "quarantine" then
      -- Hand off to the mechanism that already holds mail: this header is what
      -- header_checks HOLDs on, and the console's quarantine screen reads that
      -- hold queue. No second quarantine path.
      task:set_milter_reply({
        add_headers = { ["X-Mailyte-Quarantine"] = rule.name or "transport rule" },
      })

    elseif action.type == "add_disclaimer" then
      task:set_milter_reply({
        add_headers = { ["X-Mailyte-Disclaimer"] = params.text or "" },
      })

    elseif action.type == "redirect" or action.type == "bcc" then
      -- Deliberately not attempted. See the header comment: a milter cannot
      -- reroute delivery. Logged so an operator can see the rule matched and
      -- why nothing happened, instead of concluding the rule is broken.
      rspamd_logger.infox(task,
        "transport rule '%s' matched but action '%s' is not enforceable in a milter; skipped",
        rule.name, action.type)

    else
      rspamd_logger.infox(task, "transport rule '%s': unknown action '%s'", rule.name, action.type)
    end
  end
end

-- ---------------------------------------------------------------------------
-- Entry point
-- ---------------------------------------------------------------------------

local function evaluate(task)
  if not settings.enabled then return end
  rspamd_logger.infox(task, "transport rules: evaluating")

  local function on_rules(err, data)
    if err or not data then
      -- Fail open, and say so. milter_default_action is already `accept`, so
      -- refusing mail because a cache read failed would be a self-inflicted
      -- outage; the log line is what makes the gap visible.
      rspamd_logger.errx(task, "transport rules unavailable (%s); message not evaluated", err)
      return
    end

    local parser = ucl.parser()
    local ok = parser:parse_string(data)
    if not ok then
      rspamd_logger.errx(task, "transport rules payload is not valid JSON; message not evaluated")
      return
    end

    local rules = parser:get_object() or {}
    -- Priority order, first match wins -- which is what the console's ordering
    -- screen has always claimed and nothing has ever honoured.
    table.sort(rules, function(a, b) return (a.priority or 0) < (b.priority or 0) end)

    for _, rule in ipairs(rules) do
      if rule.enabled ~= false and rule_matches(task, rule) then
        rspamd_logger.infox(task, "transport rule '%s' matched", rule.name)
        -- Insert the virtual child so the match is countable in
        -- `rspamc counters` and visible in the scan result, not only in a log
        -- line somebody has to know to grep for.
        task:insert_result('MAILYTE_TRANSPORT_RULE_MATCHED', 1.0, rule.name)
        apply_actions(task, rule)
        return
      end
    end
  end

  -- make_request returns false when it cannot even dispatch -- a host it
  -- cannot parse, no upstream available. Ignoring that return is why this
  -- looked like "evaluated, matched nothing": the callback never ran, and the
  -- error branch inside it never ran either, so nothing was logged at all.
  local dispatched = rspamd_redis.make_request({
    task = task,
    host = settings.servers,
    timeout = settings.timeout,
    cmd = 'GET',
    args = { REDIS_KEY },
    callback = on_rules,
  })

  if not dispatched then
    rspamd_logger.errx(task,
      "transport rules: could not dispatch the Redis request to %s; message not evaluated",
      settings.servers)
  end
end

-- Registered exactly the way email_classifier.lua is, that being the plugin
-- proven to run in this rspamd (2.7): a `callback` parent with a `virtual`
-- child hung off its id. The child is what the callback inserts on a match,
-- and it is also what makes the symbol visible in `rspamc counters` -- a bare
-- callback with no children reports nothing, so "registered but never fired"
-- and "fired but matched nothing" look identical from outside.
local transport_id = rspamd_config:register_symbol({
  name = 'MAILYTE_TRANSPORT_RULES',
  type = 'callback',
  callback = evaluate,
  score = 0.0,
  description = 'Applies Mailyte transport rules',
  priority = 10,
})

rspamd_config:register_symbol({
  name = 'MAILYTE_TRANSPORT_RULE_MATCHED',
  type = 'virtual',
  parent = transport_id,
  score = 0.0,
  description = 'A Mailyte transport rule matched this message',
})

if settings.enabled then
  rspamd_logger.infox(rspamd_config, "Mailyte transport rules: ENFORCING")
else
  rspamd_logger.infox(rspamd_config,
    "Mailyte transport rules: loaded but NOT enforcing (set TRANSPORT_RULES_ENABLED=true)")
end
