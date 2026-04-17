-- Phase 2 audit sync — Railway Postgres destination.
-- CNJ 615/2025 redundancy. Local JSON-L (/data/audit) is source of truth;
-- this table is a write-only sink queryable by ops/compliance.
--
-- Apply once via:    psql "$DATABASE_URL" -f migrations/001_audit_entries.sql
-- Or set AUDIT_SYNC_AUTO_MIGRATE=true on first dashboard start (then flip back to false).

CREATE TABLE IF NOT EXISTS audit_entries (
  id              BIGSERIAL PRIMARY KEY,
  event_type      TEXT NOT NULL,
  processo_numero TEXT NOT NULL,
  fonte           TEXT NOT NULL,
  tribunal        TEXT NOT NULL,
  status          TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  documento_id    TEXT,
  documento_tipo  TEXT,
  documento_nome  TEXT,
  tamanho_bytes   BIGINT,
  checksum_sha256 TEXT,
  batch_id        TEXT,
  client_ip       INET,
  api_key_hash    TEXT,
  erro            TEXT,
  duracao_s       DOUBLE PRECISION,
  raw             JSONB NOT NULL,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Composite dedupe (ts + event_type + processo_numero + documento_id).
  -- NULLS NOT DISTINCT makes NULL==NULL so events without documento_id
  -- (e.g., batch_started, session_login) still dedupe correctly.
  CONSTRAINT audit_entries_dedupe
    UNIQUE NULLS NOT DISTINCT (ts, event_type, processo_numero, documento_id)
);

CREATE INDEX IF NOT EXISTS audit_entries_ts_idx
  ON audit_entries (ts DESC);
CREATE INDEX IF NOT EXISTS audit_entries_processo_idx
  ON audit_entries (processo_numero, ts DESC);
CREATE INDEX IF NOT EXISTS audit_entries_event_type_idx
  ON audit_entries (event_type, ts DESC);

-- ─────────────────────────────────────────────────────────────
-- Append-only role (defense in depth) — run manually as admin:
--
--   CREATE ROLE audit_writer LOGIN PASSWORD 'CHANGEME';
--   GRANT CONNECT ON DATABASE railway TO audit_writer;
--   GRANT USAGE ON SCHEMA public TO audit_writer;
--   GRANT INSERT, SELECT ON audit_entries TO audit_writer;
--   GRANT USAGE, SELECT ON SEQUENCE audit_entries_id_seq TO audit_writer;
--
-- SELECT is required by Postgres for the INSERT ... ON CONFLICT (cols)
-- DO NOTHING path (arbiter-index lookup needs row visibility). It does
-- NOT enable UPDATE, DELETE or TRUNCATE — the role is still effectively
-- append-only: rows can be inserted and read, nothing else.
--
-- Then set DATABASE_URL to use audit_writer, NOT the admin role.
-- ─────────────────────────────────────────────────────────────
