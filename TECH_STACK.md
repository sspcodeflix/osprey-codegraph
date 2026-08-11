# Tech Stack

What Osprey CodeGraph is built with, and why each tool was chosen. The
authoritative decision log with trade-offs lives in
[ARCHITECTURE.md](ARCHITECTURE.md) (section 14); this page is the
readable summary.

## The stack at a glance

| Layer | Tools |
|---|---|
| Indexing core | SCIP (scip-python, scip-typescript), Tree-sitter (python, typescript), protobuf |
| Backend | Python 3.12+, FastAPI, uvicorn, Pydantic + pydantic-settings, psycopg 3 (pool), PyYAML, httpx, MCP SDK |
| Data store | PostgreSQL 16 + pgvector (one database: graph, docs, vectors, job queue) |
| AI layer (optional) | Ollama (qwen3:8b chat fallback, nomic-embed-text embeddings), pluggable providers: DeepSeek, Anthropic |
| Frontend | React 18, TypeScript, Vite, sigma.js 3 + graphology (WebGL map), mermaid, react-markdown + remark-gfm |
| Packaging / QA | uv + hatchling, pytest, Playwright + Chromium screenshot verification |
| Infrastructure | Docker Compose (pgvector/pg16, api, hardened worker), multi-arch images (arm64/amd64) |

Three console scripts ship from one package (`osprey-codegraph` on PyPI,
import name `osprey`): `osprey` (indexer, worker, API), `osprey-gate`
(CI architecture gate), `osprey-mcp` (MCP server, 11 typed tools).

## Why these tools

### SCIP indexers, not hand-written parsers

Facts must come from compilers. scip-python (Pyright-based) and
scip-typescript (tsc-based) resolve every symbol with real type
checkers, which is what makes "compiler-grade graph" true rather than
marketing. Choosing SCIP deleted roughly 38.5k lines of heuristic
name-resolution code compared to the reference project, and externalizes
indexer maintenance to an open-governance ecosystem. Accepted trade-off:
whole-project indexing only (no incremental), and full third-party
precision needs dependencies installed.

### A thin Tree-sitter classifier on top

SCIP has no call edges. A small per-language Tree-sitter pass classifies
each occurrence as CALLS vs REFERENCES (passing a function as a callback
is not calling it). This is real signal for blast radius and dead-code
analysis and costs a few hundred lines per language, not a parser.

### PostgreSQL, not a graph database

The most-questioned choice, made deliberately and validated by
measurement before committing:

- **The workload is bounded, not open-ended.** Dominant queries are
  bounded traversals (blast radius capped by hops), aggregations, and
  lookups. Recursive CTEs handle bounded traversal well: the M0 spike
  measured the worst-case depth-5 blast-radius CTE at 7ms on a 325k-LOC
  repository, 40x under budget.
- **The core model is relational.** Immutable snapshot per commit, and
  structural diffs between snapshots (the CI gate and the docs staleness
  loop are built on diffs). A diff is set algebra over stable symbol
  ids: native SQL. Snapshot GC is a relational cascade (0.29s with FK
  indexes).
- **One database instead of three.** Postgres + pgvector carries the
  graph, doc pages, embedding vectors (HNSW index), and the job queue
  (SKIP LOCKED). For a self-hosted, air-gap-friendly product, one boring
  database is a feature, not a compromise.
- **Enterprise operations.** Postgres is on every approved-software
  list; backup, HA, and monitoring tooling are mature. Graph engines
  add licensing complexity (Memgraph is BSL) and an unfamiliar ops
  surface.
- **Cypher buys nothing here.** The LLM never writes queries; all
  access goes through typed tools backed by fixed, hand-tuned SQL. An
  expressive ad-hoc query language for a system that forbids ad-hoc
  queries is dead weight.

Accepted trade-off: deep or unbounded traversals would be slower than a
native graph engine; mitigated with hard depth caps in the API. Revisit
signal: needing whole-graph algorithms (centrality, community
detection) at much larger scale. First stop then is Apache AGE (graph
extension inside Postgres), not a separate engine.

### pgvector for semantic search

Embeddings live next to the facts they describe. doc_chunks holds
768-dim nomic-embed-text vectors with an HNSW index (vector_cosine_ops,
matching the `<=>` operator). No separate vector database to deploy,
back up, or keep consistent.

### FastAPI + Pydantic

Typed request and response models on every endpoint. The API is
read-only by design (read-only transactions, statement timeouts), which
is a security property, not just a style choice.

### Local-first AI, pluggable providers

The default path keeps everything on your hardware: Ollama serves both
the chat fallback model and the embedding model. Cloud providers
(DeepSeek, Anthropic) are opt-in behind one `chat(messages, tools)`
interface. The graph is fully useful with no LLM configured; the LLM is
an optional surface on top.

### React + sigma.js on the frontend

sigma.js 3 renders the dependency map in WebGL, so multi-thousand-node
graphs stay interactive; graphology supplies the layout (ForceAtlas2).
Mermaid renders the server-compiled diagrams (the model is never
allowed to draw an edge; diagrams are compiled from real graph edges).
No component library and no chart library: the design system is
hand-rolled CSS tokens, which keeps the bundle small (~95kB gzip) and
the look consistent.

### uv, pytest, Playwright

uv for locking and installs. pytest for the backend suite. UI changes
are verified with Playwright driving system Chromium and reading actual
screenshots, because a build that compiles is not the same as a UI that
renders.

### Docker Compose with a hardened worker

The stack is one compose file: pgvector/pg16, the API, and a worker
running with dropped capabilities and no-new-privileges. Repository
indexing runs sandboxed: network off, install scripts disabled,
read-only repo mount, memory and CPU caps. Images build multi-arch
(arm64 and amd64).

## The through-line

Boring, auditable infrastructure everywhere (Postgres over a graph DB,
no ORM, no component framework), with the novelty concentrated where it
pays: SCIP for ground truth, and the verification loop that checks
every documentation claim against the graph before it publishes.
