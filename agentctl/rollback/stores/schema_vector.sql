-- Real state backend for Vertical C (Workstream 1): a pgvector-backed vector store and a
-- Postgres event-sourced memory store. Kept in their OWN schemas (vectorstore / memorystore) so
-- the controlplane SoR stays coordinates-only. Applied only when AGENTCTL_STATE_BACKEND=pgvector;
-- idempotent (IF NOT EXISTS) so it can be re-applied alongside the controlplane schema.

-- Pin the extension to public so the `vector` type is resolvable from any search_path (the schema
-- is applied with search_path=controlplane,public, but seed/rollback connect with the default).
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;

-- ---------- vector store: commit-scoped collections + an alias pointer ----------
-- Drop+recreate each apply (mirrors schema_postgres.sql dropping controlplane), so the tables are
-- always clean and never stale-skipped by IF NOT EXISTS. Production would migrate, not drop.
DROP SCHEMA IF EXISTS vectorstore CASCADE;
CREATE SCHEMA vectorstore;

CREATE TABLE vectorstore.embeddings (
  project_id UUID    NOT NULL,
  collection TEXT    NOT NULL,            -- the commit-scoped namespace
  vector_id  TEXT    NOT NULL,
  version    INTEGER NOT NULL DEFAULT 1,  -- bumped on re-upsert
  embedding  public.vector(8),            -- small dim for the demo; never mutated on rollback
  PRIMARY KEY (project_id, collection, vector_id)
);

CREATE TABLE vectorstore.collection_snapshots (
  project_id UUID  NOT NULL,
  collection TEXT  NOT NULL,
  snapshot_id TEXT NOT NULL,
  member_ids JSONB NOT NULL DEFAULT '[]',
  PRIMARY KEY (project_id, collection, snapshot_id)
);

-- THE alias: rollback is an idempotent upsert here; historical collections are left intact.
CREATE TABLE vectorstore.live_alias (
  project_id UUID PRIMARY KEY,
  collection TEXT NOT NULL
);

-- ---------- memory graph: append-only event log + a single HEAD per project ----------
DROP SCHEMA IF EXISTS memorystore CASCADE;
CREATE SCHEMA memorystore;

CREATE TABLE memorystore.event_log (
  project_id UUID   NOT NULL,
  seq        BIGINT NOT NULL,
  op         TEXT   NOT NULL,
  origin     TEXT,
  graph_id   TEXT,
  PRIMARY KEY (project_id, seq)
);

-- HEAD = (snapshot_seq, log_offset). Rollback rewinds it; the log past HEAD is tombstoned, not
-- destroyed (a later roll-forward can replay it). One HEAD per project mirrors the stub.
CREATE TABLE memorystore.head (
  project_id   UUID PRIMARY KEY,
  snapshot_seq BIGINT NOT NULL,
  log_offset   BIGINT NOT NULL,
  graph_id     TEXT
);
