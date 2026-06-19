-- Vertical C: the transactional system-of-record.
-- Postgres holds COORDINATES + PROOF, never bulk state. Applied clean each time for the
-- prototype (drops existing data). Production would migrate, not drop.

DROP SCHEMA IF EXISTS controlplane CASCADE;
CREATE SCHEMA controlplane;
SET search_path TO controlplane, public;

-- ---------- enums / state machines ----------
CREATE TYPE deployment_status AS ENUM
  ('queued','building','ready','active','superseded','failed','rolled_back');
CREATE TYPE checkpoint_status AS ENUM ('pending','sealed','failed','superseded');
CREATE TYPE mutation_class    AS ENUM ('vector_store','relational_schema','memory_graph','side_effect');
CREATE TYPE reversibility     AS ENUM ('reversible','forward_fix','irreversible');
CREATE TYPE rollback_status   AS ENUM
  ('initiated','routing_flipped','state_realigning','verified','completed','failed','compensating');

-- ---------- deployments: the git hash is the spine ----------
CREATE TABLE deployments (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id      UUID NOT NULL,
  git_commit_sha  TEXT NOT NULL,
  git_ref         TEXT,
  parent_sha      TEXT,
  build_meta      JSONB NOT NULL DEFAULT '{}',
  artifact_uri    TEXT,
  status          deployment_status NOT NULL DEFAULT 'queued',
  created_by      TEXT NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  activated_at    timestamptz,
  UNIQUE (project_id, git_commit_sha)
);
CREATE INDEX dep_project_status_idx ON deployments (project_id, status);

-- ---------- routing tables: versioned, swapped atomically (the gateway reads this) ----------
CREATE TABLE routing_tables (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id  UUID NOT NULL,
  version     BIGINT NOT NULL,
  is_live     BOOLEAN NOT NULL DEFAULT false,
  reason      TEXT,
  created_by  TEXT NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, version)
);
-- INVARIANT: at most one live routing table per project -> gateway never reads a torn edit.
CREATE UNIQUE INDEX one_live_routing_per_project ON routing_tables (project_id) WHERE is_live;

CREATE TABLE routing_rules (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  routing_table_id BIGINT NOT NULL REFERENCES routing_tables(id) ON DELETE CASCADE,
  deployment_id    BIGINT NOT NULL REFERENCES deployments(id),
  weight           INTEGER NOT NULL DEFAULT 0,      -- basis points; sums to 10000 per table
  is_canary        BOOLEAN NOT NULL DEFAULT false,
  shadow_target    BOOLEAN NOT NULL DEFAULT false,  -- mirror traffic, discard responses
  match_expr       JSONB NOT NULL DEFAULT '{}',
  CONSTRAINT weight_range CHECK (weight BETWEEN 0 AND 10000)
);
CREATE INDEX rr_table_idx ON routing_rules (routing_table_id);

-- ---------- checkpoints + state pointers (the heart of this vertical) ----------
CREATE TABLE checkpoints (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  deployment_id  BIGINT NOT NULL REFERENCES deployments(id),
  git_commit_sha TEXT NOT NULL,
  status         checkpoint_status NOT NULL DEFAULT 'pending',
  manifest       JSONB NOT NULL DEFAULT '{}',
  sealed_at      timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id)
);
CREATE INDEX cp_commit_idx ON checkpoints (git_commit_sha);

CREATE TABLE state_pointers (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  checkpoint_id  BIGINT NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
  mutation_class mutation_class NOT NULL,
  reversibility  reversibility  NOT NULL,
  store_id       TEXT NOT NULL,
  coordinate     JSONB NOT NULL,
  state_digest   TEXT,
  captured_at    timestamptz NOT NULL DEFAULT now(),
  -- HONESTY GUARD: a real-world side effect can never be recorded as reversible.
  CONSTRAINT side_effects_are_irreversible
    CHECK (mutation_class <> 'side_effect' OR reversibility = 'irreversible')
);
CREATE INDEX sp_checkpoint_idx ON state_pointers (checkpoint_id);

-- ---------- memory-sync pointers (event-sourced graph HEAD) ----------
CREATE TABLE memory_sync_pointers (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id    UUID NOT NULL,
  graph_id      TEXT NOT NULL,
  deployment_id BIGINT NOT NULL REFERENCES deployments(id),
  snapshot_seq  BIGINT NOT NULL,
  log_offset    BIGINT NOT NULL,
  digest        TEXT,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, graph_id, deployment_id)
);

-- ---------- rollbacks + append-only audit ----------
CREATE TABLE rollbacks (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id       UUID NOT NULL,
  from_deployment  BIGINT NOT NULL REFERENCES deployments(id),
  to_deployment    BIGINT NOT NULL REFERENCES deployments(id),
  to_commit_sha    TEXT NOT NULL,
  status           rollback_status NOT NULL DEFAULT 'initiated',
  routing_table_id BIGINT REFERENCES routing_tables(id),
  manifest_snapshot JSONB,
  unrollbackable   JSONB NOT NULL DEFAULT '[]',
  initiated_by     TEXT NOT NULL,
  initiated_at     timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz
);
CREATE INDEX rb_project_idx ON rollbacks (project_id, initiated_at DESC);

CREATE TABLE audit_log (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id  UUID NOT NULL,
  rollback_id BIGINT REFERENCES rollbacks(id),
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_ref  TEXT,
  payload     JSONB NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_rollback_idx ON audit_log (rollback_id, occurred_at);

-- ---------- OTel spans: Postgres as short buffer; same shape maps to ClickHouse ----------
CREATE TABLE otel_spans (
  trace_id       BYTEA NOT NULL,
  span_id        BYTEA NOT NULL,
  parent_span_id BYTEA,
  project_id     UUID NOT NULL,
  deployment_id  BIGINT REFERENCES deployments(id),
  rollback_id    BIGINT REFERENCES rollbacks(id),
  name           TEXT NOT NULL,
  kind           SMALLINT NOT NULL,
  start_unixnano BIGINT NOT NULL,
  end_unixnano   BIGINT NOT NULL,
  status_code    SMALLINT NOT NULL DEFAULT 0,
  attributes     JSONB NOT NULL DEFAULT '{}',
  resource       JSONB NOT NULL DEFAULT '{}',
  scope          JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX spans_project_time_idx ON otel_spans (project_id, start_unixnano DESC);

-- ---------- multi-tenant RBAC (1.0): orgs / projects / api_keys ----------
-- project_id is already the tenancy dimension on every table above; these give it a real
-- identity + authorization model. role-per-key is the right altitude for a key-authenticated
-- control plane with no external IdP (users/role_bindings are a documented post-1.0 extension).
CREATE TYPE rbac_role AS ENUM ('viewer','developer','admin','owner');

CREATE TABLE orgs (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug       TEXT UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
  id         UUID PRIMARY KEY,
  org_id     UUID NOT NULL REFERENCES orgs(id),
  slug       TEXT NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);

-- users + role bindings (post-1.0): an org member, and the role they hold on a project. A key may
-- belong to a user, in which case its EFFECTIVE role is the user's binding on the key's project
-- (central management), falling back to the key's own role for standalone keys (the 1.0 model).
CREATE TABLE users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL REFERENCES orgs(id),
  email      TEXT NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, email)
);

CREATE TABLE role_bindings (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id UUID NOT NULL REFERENCES projects(id),
  role       rbac_role NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, project_id)
);

CREATE TABLE api_keys (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id),
  user_id    UUID REFERENCES users(id) ON DELETE SET NULL,  -- NULL = standalone key (1.0 model)
  name       TEXT NOT NULL,
  key_prefix TEXT NOT NULL,                 -- shown in UIs/logs; never the secret
  key_hash   TEXT NOT NULL,                 -- sha256(secret); the secret is never stored
  role       rbac_role NOT NULL DEFAULT 'developer',
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);
CREATE UNIQUE INDEX api_keys_hash_idx ON api_keys (key_hash);
CREATE INDEX api_keys_project_idx ON api_keys (project_id);

-- ---------- bootstrap (the backward-compat keystone) ----------
-- Seed a real default org/project whose id IS the historic DEMO_PROJECT_ID, plus an owner key.
-- This is what lets resolve_principal(None) fall back to a project row that actually exists, so
-- the zero-config demo and every existing test keep working unchanged. Idempotent.
INSERT INTO orgs (id, slug)
  VALUES ('00000000-0000-0000-0000-0000000000a0','default')
  ON CONFLICT (id) DO NOTHING;
INSERT INTO projects (id, org_id, slug)
  VALUES ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000a0','default')
  ON CONFLICT (id) DO NOTHING;
-- bootstrap key = 'actl_dev_bootstrap_0000000000000000' (documented in docs/LOCAL_SETUP.md);
-- only its sha256 is stored. Role owner so the no-key default path is fully capable locally.
INSERT INTO api_keys (project_id, name, key_prefix, key_hash, role)
  VALUES ('00000000-0000-0000-0000-0000000000a1','bootstrap','actl_dev_boo',
          '4567f685fabf4123b67e3cda67d3e10b56fbb1dbd5e5df36fe6626429ca27b0a','owner')
  ON CONFLICT (key_hash) DO NOTHING;

-- ---------- hard tenancy FK (post-1.0) ----------
-- 1.0 deliberately kept deployments.project_id a SOFT reference (no FK) to avoid ordering changes
-- in the seed/tests. Now that projects is created and the bootstrap project (the historic
-- DEMO_PROJECT_ID) is seeded above, every deployment's project resolves to a real row, so the FK is
-- safe to enforce. Added by ALTER (after projects + the seed exist) rather than inline, since the
-- deployments table is declared before projects. Default RESTRICT: a project with deployments can't
-- be dropped, which is the correct tenancy invariant.
ALTER TABLE deployments
  ADD CONSTRAINT deployments_project_fk FOREIGN KEY (project_id) REFERENCES projects(id);
