# M0 Report

**Date:** 2026-08-08 · **Verdict: GO — both legs proven end to end.**
M0 is complete: Python (leg 1) and TypeScript (leg 2) both pass ground truth
and beat every §12 budget. Proceed to M1.

---

## Leg 2 — TypeScript (hono v4.13.1: 514 indexed files, 63.5k src LOC)

| Measure | Result |
|---|---|
| scip-typescript, **no deps installed** | 8.8 s, followed all 9 project-reference tsconfigs unaided |
| scip-typescript, deps installed (`--ignore-scripts`) | similar time; **+32% CALLS edges** (7,794 vs 5,902) — the measured value of `proxied` deps_mode |
| Loader (normalize + classify + load) | 1.5 s |
| `callers(dispatch, depth=3)` — 3 → 18 → 66 transitive | **1.2 ms** |

**Ground truth (`getPath`, hand-verified): all mentions correct.**
`getPath(request)` → CALLS; the fallback assignment
`this.getPath = options?.getPath ?? getPath` → REFERENCES (the TS
passed-as-value case); the *class field* `Hono#getPath` stayed a distinct
symbol from the util function — calls through `this.getPath()` resolve to the
field, not the util. Anonymous `describe/it` callbacks attribute to module
scope (correct: no named enclosing definition exists).

**TS-leg discoveries (all fixed in the loader, all load-bearing for M1):**

1. **scip-typescript emits the same file once per tsconfig that includes it**
   (hono's build + spec configs both cover `src/`) → naive loading double-
   counts modules and IMPORTS. Normalize must merge documents by path.
2. **`this.#private()` calls are `private_property_identifier` nodes** — not
   plain `property_identifier` — and silently fell to REFERENCES until added
   to the identifier set. Found because `dispatch` showed zero callers;
   the conservative failure direction (REFERENCES, never false CALLS) worked
   as designed.
3. **Cascade-deleting a snapshot took 133 s** — un-indexed FK columns force
   per-row scans. M1 needs FK indexes (or delete-in-dependency-order /
   partition drops) for the §12 retention job. Found by accident; exactly the
   kind of ops surprise better met in a spike than in production.
4. Arrow-function consts (`const f = () => {}`) carry SCIP *term* descriptors
   → kinded `variable` (cosmetic; kind mapping can special-case later).
   Namespace descriptors with internal `/` produce backtick-flecked display
   names (cosmetic).

Monorepo root detection (§16 open risk 2) **shrank**: scip-typescript walks
project references natively; hono's 9-tsconfig maze needed zero configuration.

---

## Leg 1 — Python (code-graph-rag repo)

Pipeline proven: `scip-python` → SCIP protobuf → normalize + attribute +
classify (tree-sitter) → Postgres (§4 schema subset) → recursive-CTE queries.
Test subject: the code-graph-rag repo itself (946 Python files, ~325k LOC
including tests), indexed with `deps_mode=none` (no dependency install).

## Budgets (ARCHITECTURE.md §12) vs. measured

| Budget | Target | Measured | Margin |
|---|---|---|---|
| Index 100k LOC Python | < 3 min | **39.5 s for 325k LOC** (scip-python, cold) | ~15× |
| Normalize + classify + load | (within index budget) | **5.3 s** (single-threaded Python) | — |
| `callers` depth 3, p95 | < 300 ms | **1.3 ms** (typical) | 230× |
| Worst case: depth-5 walk from hottest symbol (1,025 direct / 3,491 transitive callers) | — | **7.1 ms** | 40× vs depth-3 budget |
| Peak indexer RSS | — | 2.5 GB | fine |

Extrapolation: a 1M-LOC Python repo ≈ 2–4 min end to end vs. the 20-min
budget class. Postgres recursive CTEs are **not** a bottleneck at this scale;
the depth/node caps in §7 are safety rails, not crutches.

## Correctness (ground truth)

**`_load_hash_cache` (graph_updater.py): 10/10 mentions correct.** Every call
site → CALLS with the correct enclosing function (attribution exact, including
test methods); every import → IMPORTS; and the decisive case —
`benchmark(_load_hash_cache, ...)` in optimize/profile_io.py:263, where the
function is *passed as a value* — correctly classified **REFERENCES**, not
CALLS. This is the distinction SCIP alone cannot make and the classifier
exists to provide.

**`fetch_all`: 69 competing definitions** (1 real + 68 test fakes) — the
exact scenario where name-matching heuristics guess. Call sites resolved to
the correct per-class definition; 64/69 fake variants show zero inbound
CALLS. No conflation. This is the compiler-grade-resolution claim, measured.

**INHERITS:** scip-python emits `is_implementation` relationships natively —
313 inheritance edges came from SCIP directly, syntax fallback adds the rest
(322 total). Better than designed: §6 assumed syntax-only.

## Indexer quirks discovered (why M0 exists)

1. **scip-python 0.6.6 never sets the SCIP `Import` role** — import
   occurrences arrive as plain reads. Recovered syntactically (occurrence
   inside an `import_statement` node → IMPORTS). One classifier question, ~10
   lines.
2. **`display_name` is empty** for first-party symbols — symbol names must be
   parsed from the SCIP descriptor grammar (`Class#method().` → `method`).
3. **Parameters/type-params arrive as symbols** (`f().(x)`) and must be
   filtered or they add ~50% noise rows (44,924 → 21,340 symbols after
   filter).
4. **Module symbols end in `__init__:`** (meta descriptor), not `/` as the
   spec's namespace examples suggest.

All four are load-bearing facts for M1's normalize stage; none threatens the
architecture.

## Final graph (snapshot 1)

946 files · 21,340 symbols (5,814 functions, 5,913 methods, 1,416 classes,
1,369 modules, 6,828 variables) · 148k occurrences · 90,546 edges
(32,600 CALLS · 45,671 REFERENCES · 11,953 IMPORTS · 322 INHERITS).

## Honest caveats (both legs)

- Two repos, two languages, one indexer version each. Broader corpus testing
  (incl. the harvested code-graph-rag fixture suite) is M1 hardening work —
  ground truth here was three hand-verified cases + distribution checks.
- Python ran `deps_mode=none` only; the TS deps-vs-nodeps compare (+32%
  CALLS) suggests measuring the same delta for Python in M1.
- Classifier misses exotic call positions by design (calls through
  `functools.partial`, `fn.call()`/`fn.apply()`, decorators applying at def
  time) — these land as REFERENCES today, the conservative direction. The
  `#dispatch` find shows the failure mode is detectable (a symbol with
  suspicious zero callers) — a graph-audit query worth shipping in M1.

## M1 must-carry list (from M0 discoveries)

1. Merge SCIP documents by path (multi-tsconfig double-emission).
2. Parse names from symbol grammar; never trust `display_name` alone.
3. Filter parameter/type-param symbols.
4. Recover imports syntactically (scip-python never sets the Import role).
5. FK indexes to make snapshot deletes O(rows), not O(rows × scans).
6. Zero-caller audit query as a classifier canary.

## Files

- `schema.sql` — §4 subset · `load.py` — parse/attribute/classify/load
  (`--repo/--index/--project`, Python + TS/TSX)
- Python leg: `index.scip` (35 MB) · `scip-index.log` · snapshot 1
- TS leg: `hono/` clone · `hono-nodeps.scip` / `hono-deps.scip` (12 MB each) ·
  snapshot 5 (deps, current)
- DB: `osprey-m0-pg` container, port 5433
