-- =============================================================================
-- Users & Web Sessions (phase-03: browser sessions for the CE/tenant dashboard)
-- =============================================================================
-- CE talks to this API directly from a browser (ADR-001) and cannot hold a
-- long-lived X-API-Key client-side, so this adds a real login: credentials
-- in, short-lived session cookie out. See
-- plans/01-mailyte-email-server/phase-03-auth-sessions.md.
--
-- Corrections made to the phase-03 draft after verifying the live schema
-- (2026-07-30), each because building against the doc's literal SQL would
-- have failed or silently done the wrong thing:
--
--   1. Table renamed user_sessions -> web_sessions. `user_sessions` already
--      exists (001_init_schema.sql) for webmail/IMAP sessions, keyed on
--      email_account_id -- a different, unrelated table. `CREATE TABLE IF
--      NOT EXISTS user_sessions (...)` with the doc's columns would have
--      silently no-op'd against that existing table, and every session
--      query written against user_id/token_hash/organization_id would then
--      fail at runtime against columns that don't exist. Flagged in
--      00-foundation/03-deep-audit.md §4.1, which names this exact phase.
--   2. FK corrected to `organizations(id)`. The doc's draft referenced
--      `organizations(organization_id)`, which is not a column on that
--      table -- the PK is `id`.
--   3. id / organization_id / user_id widened from the doc's VARCHAR(100)
--      to CHAR(26). Migration 009_ulid_safe.sql already converted
--      organizations.id, api_keys.id and api_keys.organization_id to
--      CHAR(26) repo-wide; conventions.md §4 rule 6 ("organization_id is
--      VARCHAR(100) everywhere") predates that migration and is stale for
--      every ULID-converted table. Rule 3 (FK column types must match
--      exactly) governs here instead -- verified live via `DESCRIBE
--      organizations` / `DESCRIBE api_keys` against the running stack.

CREATE TABLE IF NOT EXISTS users (
    id              CHAR(26)     NOT NULL PRIMARY KEY,
    organization_id CHAR(26)     NOT NULL,
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(50)  NOT NULL DEFAULT 'admin',
    is_active       TINYINT(1)   NOT NULL DEFAULT 1,
    last_login_at   DATETIME     NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_users_org_email (organization_id, email),
    KEY idx_users_org (organization_id),
    CONSTRAINT fk_users_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS web_sessions (
    id              CHAR(26)     NOT NULL PRIMARY KEY,
    user_id         CHAR(26)     NOT NULL,
    organization_id CHAR(26)     NOT NULL,
    token_hash      CHAR(64)     NOT NULL,
    ip_address      VARCHAR(45)  NULL,
    user_agent      VARCHAR(500) NULL,
    expires_at      DATETIME     NOT NULL,
    absolute_expiry DATETIME     NOT NULL,
    revoked_at      DATETIME     NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_web_sessions_token (token_hash),
    KEY idx_web_sessions_user (user_id),
    KEY idx_web_sessions_expiry (expires_at),
    CONSTRAINT fk_web_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
