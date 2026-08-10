# Osprey CodeGraph - System Architecture

**Version:** 0.1 (draft) · **Date:** 2026-08-08 · **Status:** pre-implementation

Osprey CodeGraph indexes codebases into a deterministic, per-commit knowledge graph stored
in Postgres, and serves three products on that one core: visual dependency
analysis, CI architecture governance, and an AI query interface.

This document records the design and the reasoning. Section 14 is the decision
log; when a decision here is revisited, update both.

---

## 1. Design principles

1. **The index is the product.** The graph must be useful with no LLM attached.
   Every feature must work deterministically from stored facts.
2. **Facts over guesses.** Symbol resolution comes from compiler-grade tools
   (SCIP indexers built on Pyright and the TypeScript compiler), not from
   syntax-tree heuristics.
3. **Immutable snapshots.** A snapshot = one repo at one commit, indexed once,
   never mutated. Everything downstream (diffing, CI gating, caching,
   reproducibility) falls out of this.
4. **Read-only core.** Osprey CodeGraph analyses code; it never edits it, and it never
   runs shell commands on behalf of a model. Anything that mutates a repo is a
   different product with a different trust model.
5. **Boring infrastructure.** Postgres, containers, one queue. Every
   infrastructure choice must be operable by a team that has never seen Osprey CodeGraph.
6. **Air-gapped is first-class.** Every feature must have a documented
   local-only path, verified in CI - not a footnote.

## 2. System overview

```
                      ┌──────────────────────────────────────────────┐
                      │                 osprey-indexer               │
  git repos ────────► │  fetch → sandbox → SCIP index → classify →   │
  (webhook/cron/CLI)  │  normalize → bulk load → atomic publish      │
                      └──────────────────┬───────────────────────────┘
                                         │ COPY (staged, atomic flip)
                                         ▼
                      ┌──────────────────────────────────────────────┐
                      │            Postgres  (system of record)      │
                      │  orgs · repos · snapshots · files · symbols  │
                      │  occurrences · edges · module_edges · rules  │
                      │  audit_log                                   │
                      └──────────────────┬───────────────────────────┘
                                         │ read-only role
                                         ▼
                      ┌──────────────────────────────────────────────┐
                      │        osprey-api  (typed, read-only)        │
                      │  auth (OIDC/token) · RBAC · cost limits ·    │
                      │  audit · pagination                          │
                      └───────┬──────────────┬──────────────┬────────┘
                              │              │              │
                        ┌─────▼────┐   ┌─────▼─────┐  ┌─────▼─────┐
                        │osprey-web│   │osprey-gate│  │osprey-mcp │
                        │  (viz)   │   │ (CI gate) │  │(AI, Ph. 3)│
                        └──────────┘   └───────────┘  └───────────┘
```

Five components, one data store, one direction of data flow. The API is the
only read path; the indexer is the only write path.

## 3. Components

### 3.1 osprey-indexer (worker)

A queue-driven worker that turns `(repo, commit_sha)` into a published
snapshot. Stateless; all state lives in Postgres and the object store (raw
`.scip` files kept for debugging/reprocessing). Concurrency = N workers pulling
from one Postgres-backed job queue (`SELECT ... FOR UPDATE SKIP LOCKED` - no
Redis/RabbitMQ until proven necessary).

Pipeline stages per job (detailed in §5): fetch → detect → deps → index →
classify → normalize → load → publish.

### 3.2 osprey-api (service)

FastAPI (Python 3.12), stateless, horizontally scalable. Connects to Postgres
with a **read-only role** - the API physically cannot write graph data (it
writes only `audit_log` via a separate narrow role). Every endpoint is typed,
paginated, and cost-bounded (§7). No endpoint accepts raw query text in any
query language.

### 3.3 osprey-web (frontend)

React + TypeScript + Sigma.js (WebGL). Renders only what the server extracts:
the API returns pre-aggregated subgraphs capped at 2,000 nodes; the client
never receives the full graph. Views: module map, drill-down, blast radius,
DSM matrix, snapshot diff (§9).

### 3.4 osprey-gate (CI CLI)

A thin CLI over the API: request indexing of the PR head (or reuse an existing
snapshot), fetch the structural diff vs. the base snapshot, evaluate
`osprey.rules.yaml`, exit non-zero on violations, and emit a PR-comment
markdown summary (§8). Distributed as a Python package (`uvx osprey-gate`);
revisit a static binary if customer CI images lack Python.

### 3.5 osprey-mcp (Phase 3)

An MCP server exposing **parameterized tools** that wrap API endpoints 1:1
(`search_symbols`, `callers`, `impact`, `path_between`, `module_graph`,
`diff_summary`). The model fills typed arguments; it never generates SQL or
Cypher. Provider shim with two backends: local (Ollama/vLLM - default) and
cloud (opt-in per org). See §10.

## 4. Data model

Postgres ≥ 16. All graph tables carry `snapshot_id` as the leading column of
every index; a snapshot is deleted by cascading delete (batched) under a
retention policy (§12).

```sql
CREATE TABLE orgs (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  settings     JSONB NOT NULL DEFAULT '{}'   -- provider config, retention, etc.
);

CREATE TABLE repos (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id         BIGINT NOT NULL REFERENCES orgs(id),
  name           TEXT NOT NULL,
  git_url        TEXT NOT NULL,
  default_branch TEXT NOT NULL DEFAULT 'main',
  UNIQUE (org_id, name)
);

CREATE TABLE snapshots (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id          BIGINT NOT NULL REFERENCES repos(id),
  commit_sha       TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN
                     ('queued','indexing','ready','failed')),
  indexer_versions JSONB NOT NULL,   -- {"scip-typescript":"0.x","osprey":"0.1"}
  deps_mode        TEXT NOT NULL CHECK (deps_mode IN ('none','proxied')),
  error            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ready_at         TIMESTAMPTZ,
  UNIQUE (repo_id, commit_sha, indexer_versions)  -- re-index on tool upgrade
);

CREATE TABLE files (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  path        TEXT NOT NULL,          -- repo-relative, '/'-separated
  language    TEXT NOT NULL,
  loc         INT NOT NULL,
  precision   TEXT NOT NULL DEFAULT 'scip'
              CHECK (precision IN ('scip','structural')),  -- §5 fallback tier
  UNIQUE (snapshot_id, path)
);

CREATE TABLE symbols (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id   BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  scip_symbol   TEXT NOT NULL,        -- full SCIP symbol string, verbatim
  stable_symbol TEXT NOT NULL,        -- version segment normalized (§8.1)
  kind          TEXT NOT NULL,        -- function|method|class|interface|enum|
                                      -- type|variable|module|namespace
  name          TEXT NOT NULL,
  file_id       BIGINT REFERENCES files(id),  -- NULL for external symbols
  start_line    INT,                  -- definition span (NULL for external)
  end_line      INT,
  is_exported   BOOLEAN NOT NULL DEFAULT false,
  is_external   BOOLEAN NOT NULL DEFAULT false,
  entry_kind    TEXT,                 -- NULL | 'http'|'rpc'|'main'|'cli'|'export'
                                      -- detected entry points (§8.3)
  documentation TEXT,
  UNIQUE (snapshot_id, scip_symbol)
);
CREATE INDEX ON symbols (snapshot_id, name text_pattern_ops);
CREATE INDEX ON symbols (snapshot_id, stable_symbol);
CREATE INDEX ON symbols (snapshot_id, file_id);

-- Every mention of a symbol, with provenance. This is the "find references"
-- and diff-evidence table; edges are derived from it.
CREATE TABLE occurrences (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  symbol_id   BIGINT NOT NULL REFERENCES symbols(id),
  file_id     BIGINT NOT NULL REFERENCES files(id),
  start_line  INT NOT NULL,
  start_char  INT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN
                ('definition','reference','import','write')),
  enclosing_symbol_id BIGINT REFERENCES symbols(id)  -- attribution (§6)
);
CREATE INDEX ON occurrences (snapshot_id, symbol_id, role);
CREATE INDEX ON occurrences (snapshot_id, file_id, start_line);

-- Distinct (src, dst, kind) pairs; weight = number of sites.
CREATE TABLE edges (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  src_id      BIGINT NOT NULL REFERENCES symbols(id),
  dst_id      BIGINT NOT NULL REFERENCES symbols(id),
  kind        TEXT NOT NULL CHECK (kind IN
                ('CALLS','REFERENCES','IMPORTS','INHERITS','IMPLEMENTS')),
  weight      INT NOT NULL DEFAULT 1,
  first_file_id BIGINT REFERENCES files(id),   -- one example site
  first_line    INT,
  PRIMARY KEY (snapshot_id, src_id, dst_id, kind)
);
CREATE INDEX ON edges (snapshot_id, dst_id, kind);   -- reverse traversal

-- Pre-aggregated package/module graph for the default viz view.
-- Materialized at publish time (plain table, not a MATERIALIZED VIEW, so it
-- participates in the same transaction as the snapshot flip).
CREATE TABLE module_edges (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  src_module  TEXT NOT NULL,     -- module path, e.g. 'packages/core'
  dst_module  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  weight      INT NOT NULL,
  PRIMARY KEY (snapshot_id, src_module, dst_module, kind)
);

CREATE TABLE rules (               -- governance rule sets, versioned (§8)
  id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_id   BIGINT NOT NULL REFERENCES repos(id),
  content   JSONB NOT NULL,        -- parsed osprey.rules.yaml
  source_sha TEXT NOT NULL,        -- commit the rules file came from
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     BIGINT NOT NULL,
  actor      TEXT NOT NULL,        -- user sub or token id
  action     TEXT NOT NULL,        -- endpoint + normalized params
  at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Sizing intuition** (validated in M0, §16): 1M LOC ≈ 150-400k symbols,
1.5-4M occurrences, 0.5-2M edges ≈ 1-3 GB per snapshot with indexes. Plain
B-tree indexes with `snapshot_id` leading are sufficient at tens of snapshots;
declarative partitioning by `snapshot_id` range is the known escape hatch if
retention grows, and requires no schema change to adopt.

## 5. Indexing pipeline

Each stage is a checkpoint; a failure marks the snapshot `failed` with the
stage name and log tail in `error`. Partial data is never visible: stages
write to staging tables (`_stg` suffix, same shapes), and **publish** is a
single transaction that inserts into the real tables and flips `status` to
`ready`.

1. **fetch** - shallow clone at `commit_sha` into a tmpfs workdir.
2. **detect** - enumerate index roots: `tsconfig.json`/`package.json` for
   TS/JS (a monorepo yields several), Python package roots via
   `pyproject.toml`/setup files. Record per-root config in the job.
3. **deps** *(mode per repo, recorded on the snapshot)* -
   - `none` (default for untrusted repos): skip dependency installation.
     First-party resolution still works; imports of third-party packages
     resolve to external stub symbols. Degraded but safe.
   - `proxied`: install dependencies inside the sandbox with egress restricted
     to an allowlisted registry proxy (npm mirror / PyPI mirror). Required for
     precise types flowing through third-party APIs.
4. **index** - run `scip-typescript` / `scip-python` per root, **network
   disabled**, in the sandbox (§11.1). Output: one `.scip` protobuf per root,
   archived to the object store.
   - **Structural fallback tier** (harvested from code-graph-rag's core
     insight, §17): if a root fails SCIP indexing (broken config, unresolvable
     project) or a file's language has no SCIP indexer, a tree-sitter pass
     extracts definitions and import edges heuristically instead of skipping
     the file. Affected files carry `precision='structural'`; their symbols
     and edges surface in every API response with that flag, and the UI renders
     them visually distinct. A compiler-grade indexer degrades grumpily;
     this tier makes Osprey CodeGraph degrade gracefully - partial facts beat holes,
     as long as they are honestly labeled.
5. **classify** - the call-site classifier (§6): a tree-sitter pass that tags
   each SCIP reference occurrence as *call* vs *bare reference*, and extracts
   `INHERITS`/`IMPLEMENTS` from definition-site syntax.
6. **normalize** - merge per-root indexes; dedupe symbols; compute
   `stable_symbol`; attribute occurrences to enclosing definitions; aggregate
   edges and module_edges.
7. **load** - bulk `COPY` into staging tables.
8. **publish** - transactional move + `status='ready'`.

**Triggers:** webhook on merge to default branch, nightly cron, `osprey-gate`
request for a PR head, or manual CLI. No incremental indexing in v1 - SCIP
indexers are whole-project; a 1M-LOC TS repo indexes in low tens of minutes
(budget: §12), which is acceptable at merge/nightly cadence. Incremental is
explicitly out of scope until the whole-project cost is proven to hurt (§15).

## 6. Call-graph derivation

SCIP deliberately has no call edges - it records symbols and role-tagged
occurrences (definition / import / read / write). Osprey CodeGraph derives edges in two
steps. This is the only place Osprey CodeGraph interprets syntax itself, and it is
~hundreds of lines per language, not tens of thousands.

**Step 1 - attribution.** Every reference occurrence is attributed to the
tightest enclosing *definition* span in the same file (SCIP's
`typed_enclosing_range` when the indexer emits it; otherwise binary search
over definition spans). References outside any definition attribute to the
file's module symbol. This yields `enclosing_symbol_id` on `occurrences`.

**Step 2 - classification.** A tree-sitter query at each reference site
answers one narrow question: *is this identifier in call position?*

- TS/JS: is it the callee of `call_expression` / `new_expression`? (including
  the property in `a.b()`)
- Python: is it the function child of a `call` node?

Call position → `CALLS` edge (enclosing → target). Otherwise → `REFERENCES`
(the function passed as a value, stored in a dict, exported in a table - the
distinction the original project got right and SCIP alone cannot make).
Instantiation (`new Foo()`, `Foo()` where the target is a class) is `CALLS`
with `dst.kind = 'class'` - queryable without a separate edge kind.

`INHERITS` / `IMPLEMENTS` come from definition-site syntax (extends/implements
clauses, Python base lists) with base identifiers resolved through SCIP
occurrences at those positions - so resolution stays compiler-grade even
though the clause detection is syntactic. `IMPORTS` maps directly from SCIP
import-role occurrences.

**Known limits (accepted, documented):** dynamic dispatch through
`getattr`/index-signature access is not resolved (Pyright/tsc don't resolve it
either - nobody does statically); decorators that swap implementations map to
the decorated symbol; re-export chains resolve to the original symbol (SCIP
does this correctly, which is a win over path-based heuristics).

## 7. Query API

All endpoints: read-only, authenticated, org-scoped, audited, paginated.
Traversal endpoints enforce `depth ≤ 5`, `max_nodes ≤ 2000`, and a 5s
`statement_timeout`; responses carry `truncated: true` rather than failing
when a cap is hit. Traversals are recursive CTEs over `edges` with the depth
and node caps applied inside the query.

```
GET /v1/repos                                 repos + latest snapshot status
GET /v1/repos/{repo}/snapshots                snapshot history
GET /v1/snapshots/{snap}/symbols?q=&kind=     search (name prefix/substring)
GET /v1/snapshots/{snap}/symbols/{id}         detail + definition location
GET /v1/snapshots/{snap}/symbols/{id}/callers?depth=   reverse CALLS closure
GET /v1/snapshots/{snap}/symbols/{id}/callees?depth=
GET /v1/snapshots/{snap}/symbols/{id}/references      occurrence list
GET /v1/snapshots/{snap}/impact?symbol=&depth=        callers ∪ importers ∪
                                                      inheritors (blast radius)
GET /v1/snapshots/{snap}/paths?from=&to=&max=  shortest dependency paths
GET /v1/snapshots/{snap}/modules              aggregated module graph
GET /v1/snapshots/{snap}/modules/cycles       package-level cycles (Tarjan)
GET /v1/snapshots/{snap}/subgraph?root=&hops=&max_nodes=   viz extraction
GET /v1/snapshots/{snap}/deadcode?entry=      symbols unreachable from entry
                                              points (§8.3)
GET /v1/snapshots/{snap}/export               full-graph JSON dump (integration
                                              escape hatch; streamed, audited)
GET /v1/diff?base={snap}&head={snap}          structural diff (§8.1)
POST /v1/repos/{repo}/index                   request snapshot (write-path
                                              exception; queues a job)
```

The impact/callers/paths endpoints are the contract everything else consumes:
web renders them, gate evaluates rules against them, MCP wraps them. New
capability = new endpoint, never a query-language passthrough.

## 8. Structural diff & governance

### 8.1 Diff

Symbols are matched across snapshots by `stable_symbol`. SCIP symbol strings
embed the package **version** (`scip-typescript npm mylib 1.4.2 src/A#f().`),
which churns every release in monorepos and would make every diff noisy -
so `stable_symbol` replaces the version segment with `_`. Both forms are
stored; diff and rules use `stable_symbol`, cross-repo dependency linking uses
the verbatim form.

`diff(base, head)` = set difference on `(stable_src, stable_dst, kind)` plus
symbol-level added/removed/moved (moved = same `stable_symbol`, different
file), rolled up per module. Output is JSON plus a human summary. Cost: two
index scans - this is the payoff of immutable snapshots.

### 8.2 Rules (`osprey.rules.yaml`, lives in the repo)

```yaml
layers:
  core:         ["packages/core/**"]
  api:          ["packages/api/**"]
  experimental: ["packages/experimental/**"]

rules:
  - deny: "core -> experimental"        # layering violation
    severity: error
  - deny: "api -> core.internal"        # submodule opacity
    severity: error
  - no_new_cycles: package              # cycle budget: current count is the cap
    severity: error
  - public_api_freeze: ["packages/sdk/**"]   # exported-symbol diff must be ∅
    severity: warn
```

`osprey-gate check --base main` evaluates rules against the head snapshot and
the diff: `deny` rules check module_edges; `no_new_cycles` compares cycle
counts; `public_api_freeze` checks the exported-symbol diff. Violations report
the rule, the offending edge, and the example site (`first_file_id:first_line`).
Exit 1 on any `error`. The gate never blocks on Osprey CodeGraph being down - it fails
open with a loud warning (configurable to fail closed).

### 8.3 Entry points & dead code

A detection pass (harvested concept, §17) marks symbols with `entry_kind`:
HTTP/RPC route handlers (Express/Fastify/Flask/FastAPI decorators and route
registrations), `main` guards, CLI command registrations, and exported public
API of designated packages. Entry points power two features:

- **Dead code**: symbols unreachable from any entry point via
  CALLS/REFERENCES/INHERITS closure - a single reverse-reachability query over
  `edges`, exposed as `/deadcode` and as a gate rule
  (`no_new_dead_code: warn`). The lesson from code-graph-rag: dead-code
  detection is only as good as entry-point detection, so `structural`-precision
  files suppress dead-code claims for their symbols (a heuristic hole must not
  become a deletion recommendation).
- **Surface-aware blast radius**: impact reports and PR comments name the
  *public endpoints* whose reachable set includes changed code - "this PR
  touches code reachable from `POST /billing/charge`" lands harder with a
  reviewer than a count of affected functions.

## 9. Visualization

Server extracts, client renders. Every view is an API response, never a
client-side traversal of a full graph dump.

- **Module map** (default): `module_edges` aggregated, bundled edges with
  weights, node size = LOC, ≤ a few hundred nodes for any sane repo.
- **Drill-down:** module → files → symbols, each level a fresh `subgraph`
  call; hard cap 2,000 rendered nodes, always with a "truncated" indicator.
- **Blast radius:** `impact` result rendered as concentric layers by hop
  count. This is the money view.
- **DSM matrix:** modules × modules with edge weights; the only view where
  cycles and layering violations are visible at a glance in large repos.
- **Diff view:** two snapshots, added edges green / removed red, driven by
  `/v1/diff`.

Rendering: Sigma.js (WebGL) - chosen because it stays interactive above
10k elements where SVG/D3-force dies at ~1-2k. Layout computed server-side
for module views (deterministic, cacheable), client-side force only within
small drill-down scopes.

## 10. AI layer (Phase 3)

- MCP server (`osprey-mcp`) + optional chat panel in osprey-web, both driving
  the same typed tools, which wrap §7 endpoints 1:1.
- **The model never writes queries.** It selects tools and fills typed,
  validated arguments. Injection surface: none; auditability: every tool call
  is an audit_log row; determinism: same tool call, same snapshot, same answer.
- Every answer must cite `file:line` provenance drawn from tool results; the
  UI renders citations as links into the code.
- Provider shim: `local` (Ollama/vLLM endpoint; default) or `cloud`
  (Anthropic/OpenAI/Google; per-org opt-in with an explicit "code excerpts
  leave the network" acknowledgment recorded in org settings).
- Semantic/embedding search is deferred past Phase 3 v1 - name/structure
  search covers most queries; embeddings add a vector store and an
  air-gap-complicating model dependency for marginal gain. Revisit with usage
  data.

## 11. Security model

### 11.1 Indexer sandbox

Indexing untrusted code must not execute it. SCIP indexers are parsers, but
the *deps* stage (npm/pip install) can run arbitrary scripts, so the whole
pipeline runs contained:

- Rootless container (podman), non-root UID, read-only source mount, tmpfs
  scratch, no host mounts.
- **Network:** `none` for fetch-completed stages; the deps stage (only in
  `proxied` mode) gets egress solely to the registry proxy. The index stage is
  always `network=none`.
- cgroup CPU/memory caps and a wall-clock timeout per stage.
- npm lifecycle scripts disabled (`--ignore-scripts`); pip with
  `--only-binary :all:` preferred, sdist builds allowed only in `proxied`
  mode inside the sandbox.
- Default `deps_mode=none` for newly added repos - precise third-party types
  are an upgrade you opt into per repo, not a default risk.

### 11.2 Service boundaries

- API: OIDC for humans, scoped tokens for CI; RBAC at org/repo granularity;
  every request audited. Postgres roles: `osprey_ro` (API), `osprey_ingest`
  (indexer), `osprey_audit` (audit writes). No superuser at runtime.
- All binds loopback/cluster-internal by default; TLS at the edge; nothing
  listens unauthenticated on a routable interface (the exact failure of the
  original project's Memgraph/Qdrant exposure).
- LLM egress is impossible in `local` provider mode by construction (no cloud
  credentials configured), not by promise.

## 12. Scale envelope & budgets

| Budget | Target |
|---|---|
| Index 1M LOC TS monorepo (8 cores, deps cached) | < 20 min |
| Index 100k LOC Python service | < 3 min |
| `callers` depth 3 on 10M-edge snapshot, p95 | < 300 ms |
| `subgraph` 2k nodes, p95 | < 1 s |
| `diff` between adjacent snapshots | < 5 s |
| Storage per 1M LOC snapshot | 1-3 GB |

Retention: keep last N snapshots per repo (default 30) + protected snapshots
(default-branch heads, releases, any snapshot referenced by a rules baseline);
nightly GC job deletes the rest in batches. These budgets are M0 exit
criteria, not aspirations - if M0 misses them by >2×, the design (Postgres
CTEs, whole-project indexing) gets re-examined before any product code is
built on top.

## 13. Deployment

- **v1: single-node docker-compose** - Postgres 16, api, indexer (×N), web,
  proxy. Suits the GB10-class box (20 cores / 121 GB) with room for a local
  LLM (vLLM/Ollama) beside it. aarch64 and x86_64 images from day one (dev box
  is aarch64 - this is a build-matrix requirement, not an afterthought).
- **Air-gapped install:** a single artifact bundle (images + SCIP indexer
  binaries + a local model) importable without internet; CI includes an
  egress-blocked integration test that must pass - that's what "first-class"
  means operationally.
- Kubernetes (Helm) when a customer needs HA - the services are stateless and
  Postgres HA is a solved, buyable problem; nothing in the design assumes
  single-node.

## 14. Decision log

| # | Decision | Rationale | Trade-off accepted |
|---|---|---|---|
| 1 | SCIP indexers, not hand-written parsers | Compiler-grade resolution (Pyright/tsc); ~38.5k lines of heuristic resolution deleted vs. the reference project; indexer maintenance externalized to an open-governance ecosystem (Uber/Meta on steering committee) | Whole-project indexing only (no incremental); bound to indexer release cadence; deps needed for full third-party precision |
| 2 | Derive CALLS/REFERENCES via thin tree-sitter classifier | SCIP has no call edges; the call-position distinction is real signal (callback-passing ≠ calling) and costs ~hundreds of lines per language | Two parsing passes per file; a per-language query to maintain (bounded: one question per language) |
| 3 | Postgres, not a graph DB (Memgraph/FalkorDB/Neo4j) | Durable, HA-capable, RLS, ops-familiar; dominant queries are ≤5-hop bounded traversals = recursive CTEs; snapshot delete = relational cascade | Deep/unbounded traversals slower than native graph engines; mitigated by hard depth caps, re-examined at M0 |
| 4 | Immutable snapshot per commit | Enables structural diff (the CI product), reproducibility, trivial caching; publish is atomic | Storage × retention (GC policy); no live-mutating "current" graph |
| 5 | Typed API; LLM never generates queries | Kills injection surface; deterministic, cacheable, auditable; tools = endpoints keeps three consumers on one contract | Less expressive than raw Cypher for exotic one-off questions; escape hatch is adding an endpoint |
| 6 | Read-only core | Analysis tool ≠ code-mutation agent; removes the hardest trust problems (shell tools, YOLO modes) from the product entirely | Code-editing features conceded to other tools |
| 7 | Python/FastAPI across services | One-language velocity for a small team; SCIP protobuf + tree-sitter bindings mature; API is I/O-bound | Indexer normalize stage is CPU-bound Python; acceptable at v1 scale, port-to-Rust candidate later |
| 8 | `stable_symbol` version normalization | SCIP symbols embed package versions → cross-snapshot diff would drown in version churn | Two symbol forms stored; verbatim kept for cross-repo linking |
| 9 | `deps_mode=none` default | Untrusted-repo indexing must not run package scripts; degraded external resolution is a safe default | Third-party type precision requires opt-in `proxied` mode |
| 10 | Defer embeddings/semantic search | Avoids vector store + model dependency in v1; structure covers most queries | "Find code that does X" fuzzy search absent until data justifies it |
| 11 | Structural fallback tier (tree-sitter), precision-flagged | Graceful degradation on broken/unindexable projects - code-graph-rag's core virtue, adopted with honest labeling instead of silent mixing | Two-tier precision complicates every consumer (flag must propagate to UI/gate/MCP); heuristic edges re-enter the system, quarantined by flag |
| 12 | Entry-point detection + dead code as queries | High enterprise value at low cost once the call graph exists; makes blast radius endpoint-aware | Framework-specific detection patterns to maintain (bounded: routes/main/CLI per language) |
| 13 | Harvest code-graph-rag's test corpus as ground truth | 250k lines of MIT-licensed real-world edge cases (JSX refs, tsconfig paths, import fallbacks) de-risk M0 classifier validation for free | Attribution required; fixtures must be curated, not bulk-copied |

## 15. Non-goals (v1)

- Code editing, refactoring, shell execution - permanently out of the core.
- Data-flow / taint tracking (`FLOWS_TO`-style) - real feature, new work
  (SCIP offers nothing here); revisit post-v1 with a concrete security
  use-case owner. code-graph-rag's taint model (§17) is the reference design
  when that day comes: its resource/kind/via schema is sound; its
  implementation is coupled to their parser internals and is not portable.
- Languages beyond TS/JS + Python - the schema and pipeline are
  language-agnostic (add an indexer + a classifier query); Java/Go are
  candidates 3 and 4, chosen by customer demand not symmetry. The structural
  fallback tier (Decision 11) additionally opens a cheap long-tail path:
  code-graph-rag proved a language can get modules/functions/classes/imports
  from a YAML pattern file alone (their Ruby tier) - that pattern fits the
  fallback tier as-is.
- Incremental indexing; per-keystroke/live updates (merge + nightly cadence).
- Embeddings/semantic search (Decision 10).
- SaaS multi-region - self-hosted single-region first; schema is
  multi-tenant-ready (`org_id` everywhere) so SaaS is a deployment change,
  not a redesign.

## 16. Milestones & open risks

**M0 - proving spike (~1 week).** Run scip-typescript and scip-python on two
real repos (the code-graph-rag clone qualifies for Python); parse the `.scip`
protobuf; load the §4 schema; implement attribution + classification for one
language; answer `callers("X", depth=3)` correctly against manually verified
ground truth; measure against §12 budgets. Ground truth comes cheap: curate
classifier fixtures from code-graph-rag's own test corpus (§17) - their tests
encode exactly the edge cases (callbacks stored in dicts, JSX component
references, re-export chains) that distinguish CALLS from REFERENCES.
**Exit = go/no-go on the whole architecture.** The riskiest assumptions -
classifier fidelity and CTE performance - die or survive here for the price
of a week.

**M1 - Phase 0 (~4 wks):** indexer pipeline + sandbox + API core.
**M2 - Phase 1 (~5 wks):** osprey-web (module map, drill-down, blast radius,
DSM, diff view).
**M3 - Phase 2 (~4 wks):** osprey-gate + rules engine + PR comments.
**M4 - Phase 3 (~4 wks):** osprey-mcp + provider shim + chat panel.

**Open risks, ranked:**

1. **Classifier fidelity** - if call-position detection is materially wrong for
   idiomatic TS (optional chaining, HOC patterns), CALLS edges lose trust.
   Mitigated by M0 ground-truth checks; fallback is shipping REFERENCES-only
   with CALLS behind a flag.
2. **Monorepo root detection** - multi-tsconfig repos are messy in practice
   (project references, path aliases). Budget real time in M1; scip-typescript
   has `--infer-tsconfig` but expect edge cases.
3. **scip-python on untyped code** - Pyright's inference degrades without
   annotations; resolution quality on legacy Python needs an honest measured
   number in M0, published in docs (enterprises will ask).
4. **CTE performance cliff** - dense graphs (utility symbols with 10k callers)
   can blow up bounded traversals; caps + `truncated` flags are the design
   answer, M0 must confirm they trigger gracefully, not at 30 s.
5. **Symbol stability across indexer upgrades** - a scip-typescript upgrade
   that changes symbol formatting would poison diffs; pin indexer versions per
   org, re-baseline explicitly on upgrade (this is why `indexer_versions` is
   part of the snapshot identity).

## 17. Provenance: what Osprey CodeGraph takes from code-graph-rag

Osprey CodeGraph began as a study of [code-graph-rag](https://github.com/vitali87/code-graph-rag)
(MIT-licensed). It is not a fork - no code is inherited - but five of its ideas
are deliberately harvested, and honesty about that lineage keeps the decision
log meaningful.

**Taken:**

1. **CALLS vs REFERENCES as distinct edge kinds** (§6). Their tree-sitter
   heritage got this right; SCIP alone cannot make the distinction. Osprey CodeGraph's
   classifier exists to preserve it.
2. **Graceful degradation on unparseable projects** (§5, Decision 11). Their
   all-tree-sitter design works on any source text; Osprey CodeGraph's structural
   fallback tier buys that resilience back, with a `precision` flag so
   heuristic facts never masquerade as compiler facts.
3. **Entry-point detection powering dead code and endpoint-aware impact**
   (§8.3, Decision 12). Their endpoint/RPC detection (~3.7k lines) proved the
   concept and catalogued the framework patterns worth detecting.
4. **The test corpus as ground truth** (§16, Decision 13). 250k lines of
   tests encoding years of real-world parsing edge cases - curated (with
   attribution) into M0 classifier fixtures rather than re-discovered.
5. **The YAML-pattern language tier** (§15). Their Ruby support demonstrated a
   language can join the graph from a single ast-grep pattern file - the
   adoption path for long-tail languages in Osprey CodeGraph's fallback tier.

Their **taint/data-flow schema** (`FLOWS_TO` with `kind`/`via` properties,
Resource nodes for files/env/network/db) is recorded as the reference design
for a future phase - the model is sound; the implementation is not portable.

Their **assurance-case security documentation** (explicit threat model, trust
boundaries, "what users should not expect") is adopted as practice: Osprey CodeGraph's
security docs follow that format from day one.

**Deliberately not taken:** LLM-generated Cypher (Decision 5 is its
antithesis), shell/edit tools in the core (Decision 6), the in-memory graph
database (Decision 3), hand-written per-language resolution (Decision 1), and
live file-watching (snapshot cadence instead). Each rejection is argued in the
decision log, not assumed.

## 18. Osprey CodeGraph Docs - the AI documentation platform (design)

**Status:** design (2026-08-08), pre-implementation. This section extends the
system with a documentation product built ON the graph, not beside it.

### 18.1 Product definition & positioning

Osprey CodeGraph Docs ingests a repository (the existing pipeline), then uses LLM
agents to synthesize **structured, persona-targeted documentation with
diagrams**, persists it per snapshot, and serves it through the portal with
RAG chat.

The category (auto-wiki + repo chat) is contested - DeepWiki, Swimm,
Mintlify. The wedge is that competitors are **RAG-over-text**: their prose
and diagrams are what an LLM *guessed* about structure. Osprey CodeGraph docs are
**grounded in the deterministic graph**:

1. **Diagrams are compiled, not imagined** - every Mermaid diagram is
   generated from real edges. An LLM never draws an arrow.
2. **Claims are verified before publish** - a checker validates every cited
   symbol, location, and relationship in a draft section against the graph;
   failed claims are regenerated or dropped. Docs that show their work.
3. **Docs are versioned and stale-proof** - pages hang off immutable
   snapshots; the structural diff identifies exactly which sections
   reference changed symbols, so regeneration is section-level and
   automatic, and every page says which commit it describes.
4. **Private/air-gapped first** - local LLM + pgvector + self-hosted; code
   never leaves unless an org opts into a cloud provider.
5. **Personas from one set of facts** - new-engineer, architect, on-call,
   security views are different traversals + templates over the same graph,
   never divergent prose sources.

### 18.2 Data model (additions)

```sql
CREATE TABLE doc_pages (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  persona     TEXT NOT NULL,          -- onboarding|architect|oncall|security
  slug        TEXT NOT NULL,          -- 'overview', 'modules/server', ...
  title       TEXT NOT NULL,
  position    INT NOT NULL,           -- tree order
  parent_slug TEXT,
  content_md  TEXT NOT NULL,          -- markdown w/ mermaid fences
  status      TEXT NOT NULL CHECK (status IN
                ('draft','verified','stale','failed')),
  UNIQUE (snapshot_id, persona, slug)
);

-- provenance: which graph facts a page depends on (drives staleness)
CREATE TABLE doc_refs (
  page_id       BIGINT NOT NULL REFERENCES doc_pages(id) ON DELETE CASCADE,
  stable_symbol TEXT NOT NULL,        -- survives across snapshots
  kind          TEXT NOT NULL         -- 'cites' | 'diagram' | 'section-scope'
);
CREATE INDEX ON doc_refs (stable_symbol);

-- pgvector chunks for RAG (docs + code excerpts)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE doc_chunks (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  source      TEXT NOT NULL,          -- 'doc:<persona>/<slug>' | 'code:<path>'
  content     TEXT NOT NULL,
  embedding   vector(768)             -- nomic-embed-text via Ollama (local)
);
CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);
```

Decisions: **pgvector, not a second database** (the boring-infrastructure
principle; air-gap-clean); **local embeddings by default** (nomic-embed-text
via the existing Ollama), cloud embedding opt-in mirrors the chat shim.

### 18.3 Synthesis pipeline (per snapshot, after indexing)

```
outline (deterministic) → synthesize (grounded LLM) → verify (graph check)
      → persist + embed → publish        …later: diff → section-level regen
```

1. **Outline - no LLM.** The doc tree is computed from the graph: overview,
   architecture (module graph + cycles), one page per major module (by LOC/
   traffic), entry-points page, hotspots page, persona-specific extras. The
   *structure* of the wiki is a fact, not a guess.
2. **Synthesize - grounded.** Per section, the writer agent receives (a) the
   section's graph facts (exports, deps, callers, entry points - from the
   existing tool layer), (b) relevant source slices, (c) the persona
   template. It writes markdown; **diagram slots are filled by the server**
   from `/export/mermaid`-style queries scoped to the section, never by the
   model. Citations use a strict `[sym:stable_symbol]` / `path:line` form.
3. **Verify - the moat.** A checker parses every citation and structural
   claim marker in the draft and validates it against the graph (symbol
   exists, edge exists, location matches). Sections with failures get one
   regeneration with the errors attached (the ask-loop correction pattern,
   proven in §10); still-failing claims are cut. Pages record
   `status='verified'`. No other docs product can honestly ship this gate.
4. **Persist + embed.** Pages + refs + chunks (page sections and the code
   they cite) into pgvector.
5. **Staleness (the Swimm killer).** On a new snapshot: structural diff →
   changed stable_symbols → join `doc_refs` → affected sections flagged
   `stale` and queued for regeneration. Everything else is reused verbatim -
   this is also the cost model: **you pay LLM tokens proportional to the
   diff, not the repo.**

### 18.4 Serving & RAG chat

- Portal: a **Docs** tab - persona switcher, page tree, rendered markdown
  (mermaid rendered client-side), every section footed with its citations
  and "describes commit `abc123`".
- Chat: the existing Ask loop gains a `search_docs` tool (pgvector top-k
  over doc+code chunks). **Hybrid grounding** - the model retrieves prose
  context semantically AND verifies structure through graph tools; answers
  cite both page anchors and file:line. Pure-RAG competitors have only the
  first half.
- API: `GET /v1/snapshots/{snap}/docs/{persona}` (tree),
  `.../docs/{persona}/{slug}` (page), `POST /v1/docs/generate` (queued job,
  same worker), `search_docs` internal to ask.

### 18.5 Milestones

- **D0 (spike, ~1 session):** pgvector + embeddings in compose; outline +
  synthesis + verify for ONE persona (onboarding) on one mid-size repo;
  docs tab rendering; measure tokens/page and verification failure rate.
  Exit: a generated onboarding wiki whose every claim passes the checker.
- **D1:** staleness loop (diff→regen), remaining personas, search_docs in
  Ask, feedback flag on sections.
- **D2:** editorial layer (human overrides that survive regen), export
  (static site), org config (persona set, tone), public read-only tier.

### 18.6 Risks

- **Token economics** - mitigated structurally by section-level regen, but
  D0 must measure real cost/page before committing to big repos.
- **Prose quality** - grounding prevents lies, not blandness; persona
  templates and the editorial layer (D2) are the lever.
- **DeepWiki's free public tier** is unbeatable on price for OSS - do not
  compete there; the buyer is private/regulated teams (the §10/§11
  local-first posture is the moat they pay for).
- **Verification coverage** - the checker validates structural claims, not
  every English sentence; scope honestly (citations, diagrams, dependency
  claims) and label the rest as narrative.

## 19. Post-v1 implementation notes (shipped)

**Status:** shipped (2026-08-09). Deltas beyond the original design above,
recorded so this document stays true to the running system.

### 19.1 Docs staleness loop: shipped and automatic (was §18 D1 design)

After every index job the worker auto-enqueues a docs refresh for each
persona already documented on that repo (`OSPREY_DOCS_AUTO_REFRESH`,
default on). `generate_docs` diffs the new snapshot against the last
documented one (changed stable symbols incl. edge endpoints, touched
modules, module-edge changes, entry-point set, hotspot membership) and
rewrites only pages whose inputs changed; unchanged pages carry forward
verbatim at **zero LLM tokens**, after re-checking their citations against
the new snapshot (line-drift guard). Measured: an identical re-index
carried 18/18 pages at 0 completion tokens; a real minor-release diff
regenerated only the pages whose inputs moved. The intended trigger is a
CI step that indexes each merge to the default branch; a ready-to-use
GitHub Actions workflow ships in examples/github-actions/ (the git
webhook from the §5 design remains future work).

### 19.2 Docs grounding: a second gate

Beyond citation verification, a `FACTS_LEAK` check rejects drafts that quote
internal data-plumbing field names (totals, module_dependencies, …) in
prose, with one corrective retry then a stats note. House style forbids
em-dashes in all user-facing text, enforced by a pipeline post-pass on LLM
output. Diagrams remain compiled-from-edges only.

### 19.3 AI layer: context-aware, guardrailed (extends §10)

Ask is a floating assistant on every page. It only calls typed tools
(never generates queries), shows its tool trace, and enforces a **scope
guardrail** in the system prompt: it refuses anything not about the indexed
codebase and will not write new code. Client-supplied chat history is
constrained to user/assistant roles (a caller cannot inject a `system` turn
to bypass the guardrail). It also receives a short **on-screen context**
string (current space, lens, and selected symbol/folder) so "what does this
do?" resolves to the selection, used only as a deixis hint; the model
still verifies with tools.

### 19.4 Security hardening (extends §11)

Implemented and scan-clean (`bandit` 0 High/0 Med, `npm audit` 0 vulns;
see `SECURITY.md` for the trust-boundary breakdown and operator checklist):

- Repo names are validated to a single safe path segment (`osprey/names.py`)
  at every entry point (they become `repo_root / name` on the worker.
- Bearer-token checks are timing-safe (`hmac.compare_digest`), centralized.
- Ask/index inputs are length-bounded (question, history size + per-message,
  git_url); markdown renders with raw HTML disabled and URL sanitization.
- The ref charset already blocks git option injection; the host allowlist
  blocks SSRF (both pre-existing, retained).

### 19.5 Demo mode: hosting a guided public instance (extends §13)

`OSPREY_DEMO_MODE` turns an instance into a browse-only demo behind a shared
access code (`OSPREY_API_TOKEN`): direct indexing and doc generation return
403, and visitors submit **repo requests** (`repo_requests` table) that the
operator fulfills by hand (`osprey requests`). Overlay:
`deploy/compose.demo.yml`.

### 19.6 UI evolution

Reorganized by intent into three spaces: Overview (dashboard + Health/
Cleanup/Changes drill-ins), Explore (dependency Map + a merged **Focus**
lens: "what uses this" / "what this uses"), and Documentation (persona
docs). Global `/` or ⌘K palette; collapsible sidebar; charts and a ranked
hotspots leaderboard on Overview; navy + purple theme with red reserved for
risk. The MCP server is documented for in-IDE use (Claude / Cursor /
Copilot) in the README.
