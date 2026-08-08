-- Osprey M0 schema — faithful subset of ARCHITECTURE.md §4
-- (single org/repo implied; staging/publish flow skipped for the spike)

DROP TABLE IF EXISTS edges, occurrences, symbols, files, snapshots CASCADE;

CREATE TABLE snapshots (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo             TEXT NOT NULL,
  commit_sha       TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('queued','indexing','ready','failed')),
  indexer_versions JSONB NOT NULL,
  deps_mode        TEXT NOT NULL DEFAULT 'none',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ready_at         TIMESTAMPTZ
);

CREATE TABLE files (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  path        TEXT NOT NULL,
  language    TEXT NOT NULL,
  loc         INT NOT NULL DEFAULT 0,
  precision   TEXT NOT NULL DEFAULT 'scip' CHECK (precision IN ('scip','structural')),
  UNIQUE (snapshot_id, path)
);

CREATE TABLE symbols (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id   BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  scip_symbol   TEXT NOT NULL,
  stable_symbol TEXT NOT NULL,
  kind          TEXT NOT NULL,
  name          TEXT NOT NULL,
  file_id       BIGINT REFERENCES files(id),
  start_line    INT,
  end_line      INT,
  is_external   BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (snapshot_id, scip_symbol)
);
CREATE INDEX idx_symbols_name ON symbols (snapshot_id, name text_pattern_ops);
CREATE INDEX idx_symbols_file ON symbols (snapshot_id, file_id);

CREATE TABLE occurrences (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  symbol_id   BIGINT NOT NULL REFERENCES symbols(id),
  file_id     BIGINT NOT NULL REFERENCES files(id),
  start_line  INT NOT NULL,   -- 0-based (SCIP convention) at load; presented 1-based
  start_char  INT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('definition','reference','import','write')),
  enclosing_symbol_id BIGINT REFERENCES symbols(id)
);
CREATE INDEX idx_occ_symbol ON occurrences (snapshot_id, symbol_id, role);
CREATE INDEX idx_occ_file ON occurrences (snapshot_id, file_id, start_line);

CREATE TABLE edges (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  src_id      BIGINT NOT NULL REFERENCES symbols(id),
  dst_id      BIGINT NOT NULL REFERENCES symbols(id),
  kind        TEXT NOT NULL CHECK (kind IN ('CALLS','REFERENCES','IMPORTS','INHERITS','IMPLEMENTS')),
  weight      INT NOT NULL DEFAULT 1,
  first_file_id BIGINT REFERENCES files(id),
  first_line    INT,
  PRIMARY KEY (snapshot_id, src_id, dst_id, kind)
);
CREATE INDEX idx_edges_dst ON edges (snapshot_id, dst_id, kind);
