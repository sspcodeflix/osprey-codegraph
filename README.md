# Osprey

**A code-graph platform for enterprises.** Osprey indexes codebases into a
deterministic, per-commit knowledge graph and serves three products on one core:

1. **See** — visual dependency & impact analysis (blast radius, cycles, layering)
2. **Govern** — architecture rules enforced in CI via structural graph diffs
3. **Ask** — an AI interface grounded in graph facts, never in guesses

Facts come from compilers (via [SCIP](https://github.com/sourcegraph/scip)),
not heuristics. The graph is useful with no LLM at all; the LLM is an optional
query surface on top.

## Quick start

```bash
docker compose up -d          # db + api + worker
open http://localhost:8800    # the UI
```

Then either paste a GitHub URL into **＋ Add repo** in the UI, or index a
local checkout:

```bash
docker compose exec worker osprey repo-add myrepo   # lives in ./m0/myrepo
docker compose exec worker osprey enqueue myrepo
```

Chat (the **Ask** tab) defaults to a local Ollama model; set
`OSPREY_CHAT_PROVIDER` + the matching API key in `.env` for DeepSeek or
Anthropic. `docker compose --profile local-chat up -d` bundles Ollama.

## Pointers

- [ARCHITECTURE.md](ARCHITECTURE.md) — full system design, schema, and decision log
- v1 languages: TypeScript/JavaScript, Python (via SCIP indexers)
- Deployment: self-hosted; every port binds loopback by default
- Dev without docker: `uv sync`, `osprey db-init`, `osprey api`, `osprey worker`
