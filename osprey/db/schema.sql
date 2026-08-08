-- Osprey schema (ARCHITECTURE.md §4, with M0's must-carry fixes).
--
-- Publish atomicity: rows are loaded directly against a snapshot in status
-- 'indexing'; every read path filters on status = 'ready', and the publish
-- step is a single UPDATE of that status. Partial data is therefore never
-- visible without staging tables. Failed snapshots are swept by GC.
--
-- Every FK column carries an index (M0: cascade delete was 133s without).

CREATE TABLE IF NOT EXISTS orgs (
  id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  settings JSONB NOT NULL DEFAULT '{}'
);
INSERT INTO orgs (name) VALUES ('default') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS repos (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id         BIGINT NOT NULL REFERENCES orgs(id),
  name           TEXT NOT NULL,
  git_url        TEXT NOT NULL DEFAULT '',
  default_branch TEXT NOT NULL DEFAULT 'main',
  UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS idx_repos_org ON repos (org_id);

CREATE TABLE IF NOT EXISTS snapshots (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id          BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  commit_sha       TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN
                     ('queued','indexing','ready','failed')),
  indexer_versions JSONB NOT NULL DEFAULT '{}',
  deps_mode        TEXT NOT NULL DEFAULT 'none'
                     CHECK (deps_mode IN ('none','proxied')),
  error            TEXT,
  stats            JSONB NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ready_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_snapshots_repo ON snapshots (repo_id, status);

CREATE TABLE IF NOT EXISTS files (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  path        TEXT NOT NULL,
  language    TEXT NOT NULL,
  loc         INT NOT NULL DEFAULT 0,
  precision   TEXT NOT NULL DEFAULT 'scip'
              CHECK (precision IN ('scip','structural')),
  UNIQUE (snapshot_id, path)
);

CREATE TABLE IF NOT EXISTS symbols (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id   BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  scip_symbol   TEXT NOT NULL,
  stable_symbol TEXT NOT NULL,
  kind          TEXT NOT NULL,
  name          TEXT NOT NULL,
  file_id       BIGINT REFERENCES files(id) ON DELETE CASCADE,
  start_line    INT,
  end_line      INT,
  is_exported   BOOLEAN NOT NULL DEFAULT false,
  is_external   BOOLEAN NOT NULL DEFAULT false,
  entry_kind    TEXT,
  UNIQUE (snapshot_id, scip_symbol)
);
CREATE INDEX IF NOT EXISTS idx_symbols_name
  ON symbols (snapshot_id, name text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_symbols_stable
  ON symbols (snapshot_id, stable_symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols (file_id);

CREATE TABLE IF NOT EXISTS occurrences (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  symbol_id   BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  file_id     BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  start_line  INT NOT NULL,
  start_char  INT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN
                ('definition','reference','import','write')),
  enclosing_symbol_id BIGINT REFERENCES symbols(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_occ_symbol
  ON occurrences (snapshot_id, symbol_id, role);
CREATE INDEX IF NOT EXISTS idx_occ_file
  ON occurrences (snapshot_id, file_id, start_line);
CREATE INDEX IF NOT EXISTS idx_occ_encl ON occurrences (enclosing_symbol_id);
CREATE INDEX IF NOT EXISTS idx_occ_sym_fk ON occurrences (symbol_id);
CREATE INDEX IF NOT EXISTS idx_occ_file_fk ON occurrences (file_id);

CREATE TABLE IF NOT EXISTS edges (
  snapshot_id   BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  src_id        BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  dst_id        BIGINT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN
                  ('CALLS','REFERENCES','IMPORTS','INHERITS','IMPLEMENTS')),
  weight        INT NOT NULL DEFAULT 1,
  first_file_id BIGINT REFERENCES files(id) ON DELETE CASCADE,
  first_line    INT,
  PRIMARY KEY (snapshot_id, src_id, dst_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges (snapshot_id, dst_id, kind);
CREATE INDEX IF NOT EXISTS idx_edges_src_fk ON edges (src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst_fk ON edges (dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_ffile_fk ON edges (first_file_id);

CREATE TABLE IF NOT EXISTS module_edges (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  src_module  TEXT NOT NULL,
  dst_module  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  weight      INT NOT NULL,
  PRIMARY KEY (snapshot_id, src_module, dst_module, kind)
);

CREATE TABLE IF NOT EXISTS jobs (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id    BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  ref        TEXT NOT NULL DEFAULT 'HEAD',
  status     TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
               ('queued','running','done','failed')),
  attempts   INT NOT NULL DEFAULT 0,
  locked_by  TEXT,
  locked_at  TIMESTAMPTZ,
  error      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_repo_fk ON jobs (repo_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id BIGINT NOT NULL,
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,
  at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ docs
-- Osprey Docs (ARCHITECTURE.md §18): grounded, persona-targeted docs.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS doc_pages (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  persona     TEXT NOT NULL,
  slug        TEXT NOT NULL,
  title       TEXT NOT NULL,
  position    INT NOT NULL DEFAULT 0,
  parent_slug TEXT,
  content_md  TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN
                ('draft','verified','stale','failed')),
  meta        JSONB NOT NULL DEFAULT '{}',   -- tokens, verify stats
  UNIQUE (snapshot_id, persona, slug)
);
CREATE INDEX IF NOT EXISTS idx_doc_pages ON doc_pages (snapshot_id, persona);

CREATE TABLE IF NOT EXISTS doc_refs (
  page_id       BIGINT NOT NULL REFERENCES doc_pages(id) ON DELETE CASCADE,
  stable_symbol TEXT NOT NULL,
  kind          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_refs_sym ON doc_refs (stable_symbol);
CREATE INDEX IF NOT EXISTS idx_doc_refs_page ON doc_refs (page_id);

CREATE TABLE IF NOT EXISTS doc_chunks (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  source      TEXT NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(768)
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_snap ON doc_chunks (snapshot_id);

-- additive migrations for existing databases
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'index';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}';
