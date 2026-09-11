---
title: API Versioning Policy
description: What counts as a breaking change, how deprecation works, and why /api/v1 is a promise.
---

# API Versioning Policy

**`/api/v1` is stable.** Every endpoint under this prefix — 172 pre-existing plus the
platform-scope additions from phase-06 — is covered by this policy starting 2026-07-30.
Breaking a `/api/v1` contract requires shipping `/api/v2` alongside it, not changing `/api/v1`
in place.

Three consumers depend on this today: Laravel's 22 `MailServer/Services/*` connector classes,
`mailyte-web` (hosted and community mode), and every Community Edition self-hoster calling this
API directly with curl, a generated client, or their own integration. None of them can be
broken by a routine deploy.

## What counts as breaking

Any of the following on an existing `/api/v1` endpoint:

- Removing an endpoint, or removing a field from a response
- Renaming an endpoint, a field, a query parameter, or an `error_code` string
- Changing a field's type (e.g. `storage_quota` from an integer to a string)
- Tightening request validation on a field that previously accepted a wider range of values
- Changing a status code for an existing success or error case (e.g. `200` → `201`, or a
  case that used to be `409` starting to return `422`)
- Changing the meaning of an existing field without changing its name or type (a silent
  semantic change is worse than a typed one — nothing catches it)

## What does not count as breaking

- Adding a new endpoint
- Adding a new **optional** request field with a sensible default
- Adding a new **response** field (clients that don't know about it should ignore it —
  this is why response models in `worker/api/schemas/` should avoid `Config: extra = "forbid"`
  on any model a client might reasonably see grow a field over time)
- Adding a new `error_code` value for a case that previously had none
- Widening a validation rule (accepting more than before)
- Adding a new capability string to `GET /api/v1/capabilities/` (see below)

## Capabilities are part of the contract

`GET /api/v1/capabilities/` (phase-02) is how `mailyte-web` and any other client discover what
this deployment supports (CE vs. hosted, which optional features are enabled) before the user
has even logged in. Capability strings are additive by the same rule as response fields above —
a client checking for a capability it doesn't recognise should treat it as absent, not error.
Removing a capability string a client might already be checking for is a breaking change subject
to this whole policy, same as removing an endpoint.

## Deprecation

When an endpoint, field, or capability must eventually go away:

1. Ship the replacement first. Both old and new exist simultaneously for the whole deprecation
   window.
2. Mark the deprecated one with the `Deprecation` header (RFC 8594-style: `Deprecation: true`
   or a date) and a `Sunset` header giving the removal date.
3. **Minimum six months** between marking something deprecated and actually removing it — CE
   self-hosters do not auto-update, and six months is roughly the outer bound of "a reasonable
   self-hoster will have upgraded by then" without forcing it.
4. Document the deprecation in the changelog and, if it's the primary way an endpoint is used,
   in the OpenAPI `description` field itself (surfaces directly in `/api-docs` and
   `/api-reference`).
5. Only remove after the sunset date has passed — never early, even if usage looks like zero.
   Self-hosted CE installs are invisible; "looks unused" from hosted-mode telemetry alone is not
   evidence for CE.

## When `/api/v2` becomes necessary

A breaking change to a widely-used endpoint (anything in
`plans/01-mailyte-email-server/consumed-endpoints.md`, or any of `mailboxes.py`/`domains.py`
given their consumer count) is the trigger to introduce `/api/v2` for that endpoint specifically
— not a wholesale `/api/v2` rewrite of the whole surface. `/api/v1` keeps serving unchanged
endpoints; only the changed one moves. This keeps the versioning granular and avoids forcing
every consumer to migrate everything at once for one field rename.

## Enforcement

The committed `openapi.json` (task 5.4) is the source of truth for "what does `/api/v1`
currently promise." CI (`.github/workflows/ci.yml`, job `validate-openapi-contract`) fails a PR
if the generated spec no longer matches the committed one — every change to the contract must be
a deliberate, reviewed diff in the PR, never a side effect of an unrelated code change.
