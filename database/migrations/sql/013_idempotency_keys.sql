-- Phase 04 — Idempotency & Error Contract (task 4.1)
--
-- Renumbered from 012 to 013 (2026-07-30): phase-03's 012_web_sessions.sql
-- and this file were authored independently on sibling branches both off
-- phase-02, and both originally claimed version 012. Phase-03 merges
-- first, so this one moves to keep one file per version number
-- (conventions.md §4 rule 7).
--
-- Correction vs the phase doc's draft, verified live before writing this
-- (conventions.md §4 rule 6, already corrected once by phase-03):
-- `id` and `organization_id` are CHAR(26) ULID here, not the doc's
-- VARCHAR(100). `009_ulid_safe.sql` converted `organizations.id` to
-- CHAR(26) with no DB-side default, so an FK column matching it (rule 3:
-- FK types must match exactly) has to be CHAR(26) too, and every other
-- ULID primary key added since (users, web_sessions) already follows this.
--
-- An explicit FK to organizations(id) is added (the doc's draft didn't
-- declare one) -- idempotency keys are meaningless once their org is gone,
-- and every other tenant-owned table in this schema enforces the same
-- relationship.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id               CHAR(26)     NOT NULL PRIMARY KEY,
    organization_id  CHAR(26)     NOT NULL,
    idempotency_key  VARCHAR(255) NOT NULL,
    request_method   VARCHAR(10)  NOT NULL,
    request_path     VARCHAR(500) NOT NULL,
    request_hash     CHAR(64)     NOT NULL,
    response_status  INT          NULL,
    response_body    MEDIUMTEXT   NULL,
    state            VARCHAR(20)  NOT NULL DEFAULT 'in_progress',
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       DATETIME     NOT NULL,
    UNIQUE KEY uq_idem (organization_id, idempotency_key),
    KEY idx_idem_expiry (expires_at),
    CONSTRAINT fk_idem_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
