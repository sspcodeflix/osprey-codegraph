"""Osprey query API (ARCHITECTURE.md §7).

Read-only by construction: every request runs in a READ ONLY transaction
with a statement timeout; traversals carry depth and node caps and report
`truncated` instead of failing. No endpoint accepts query-language text.
"""

from __future__ import annotations

import hmac
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from osprey.api import models as M
from osprey.config import settings
from osprey.names import safe_repo_name

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(settings.db_dsn, min_size=1, max_size=8,
                          kwargs={"row_factory": dict_row})
    yield
    pool.close()


app = FastAPI(title="osprey", version="0.1.0", lifespan=lifespan)

# dev CORS: the vite dev server runs on another port; production serves the
# built UI from this same origin (see the static mount at the bottom)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)


def require_token(authorization: str | None) -> None:
    """Timing-safe bearer check. No-op when no token is configured (dev)."""
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization is None or not hmac.compare_digest(authorization,
                                                        expected):
        raise HTTPException(401, "missing or invalid bearer token")


def db(authorization: Annotated[str | None, Header()] = None):
    require_token(authorization)
    with pool.connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = "
                     f"{settings.statement_timeout_ms}")
        yield conn


def audit(conn, actor: str, action: str) -> None:
    # separate autocommit write path — the request transaction is READ ONLY
    with pool.connection() as w:
        w.execute("INSERT INTO audit_log (org_id, actor, action)"
                  " SELECT id, %s, %s FROM orgs WHERE name='default'",
                  (actor, action))


def ready_snapshot(conn, snap: int) -> dict:
    row = conn.execute(
        "SELECT s.id, s.repo_id, s.commit_sha FROM snapshots s"
        " WHERE s.id=%s AND s.status='ready'", (snap,)).fetchone()
    if row is None:
        raise HTTPException(404, f"snapshot {snap} not found or not ready")
    return row


@app.get("/v1/repos", response_model=list[M.RepoOut])
def list_repos(conn=Depends(db)):
    return conn.execute("""
        SELECT r.name, o.name AS org,
               (SELECT max(s.id) FROM snapshots s
                WHERE s.repo_id=r.id AND s.status='ready') AS latest_snapshot,
               (SELECT j.ref FROM jobs j
                WHERE j.repo_id=r.id AND j.kind='index'
                ORDER BY j.id DESC LIMIT 1) AS ref
        FROM repos r JOIN orgs o ON o.id=r.org_id ORDER BY r.name
        """).fetchall()


@app.get("/v1/repos/{repo}/snapshots", response_model=list[M.SnapshotOut])
def list_snapshots(repo: str, conn=Depends(db)):
    return conn.execute("""
        SELECT s.id, s.commit_sha, s.status, s.stats, s.created_at, s.ready_at
        FROM snapshots s JOIN repos r ON r.id=s.repo_id
        WHERE r.name=%s ORDER BY s.id DESC LIMIT 100
        """, (repo,)).fetchall()


@app.get("/v1/snapshots/{snap}/symbols", response_model=list[M.SymbolOut])
def search_symbols(snap: int, q: str = Query(min_length=1), kind: str = "",
                   limit: int = Query(50, le=500), conn=Depends(db)):
    ready_snapshot(conn, snap)
    audit(conn, "api", f"symbols?q={q}")
    return conn.execute("""
        SELECT s.id, s.name, s.kind, f.path, s.start_line + 1 AS line,
               s.is_external
        FROM symbols s LEFT JOIN files f ON f.id = s.file_id
        WHERE s.snapshot_id = %(snap)s AND s.name ILIKE %(pat)s
          AND (%(kind)s = '' OR s.kind = %(kind)s)
        ORDER BY s.is_external, lower(s.name) = lower(%(q)s) DESC,
                 s.kind, s.name, f.path NULLS LAST, s.start_line
        LIMIT %(limit)s
        """, {"snap": snap, "pat": f"%{q}%", "q": q, "kind": kind,
              "limit": limit}).fetchall()


TRAVERSE_SQL = """
WITH RECURSIVE walk(id, depth) AS (
  SELECT %(sym)s::bigint, 0
  UNION
  SELECT e.{next_col}, w.depth + 1
  FROM edges e JOIN walk w ON e.snapshot_id = %(snap)s
   AND e.kind = 'CALLS' AND e.{match_col} = w.id
  WHERE w.depth < %(depth)s
)
SELECT DISTINCT ON (w.id) w.id, w.depth, s.name, s.kind, f.path,
       s.start_line + 1 AS line
FROM walk w JOIN symbols s ON s.id = w.id
LEFT JOIN files f ON f.id = s.file_id
WHERE w.depth > 0
ORDER BY w.id, w.depth
"""


def require_symbol(conn, snap: int, symbol_id: int) -> None:
    # a nonexistent id must be a loud 404, not an empty result — an AI (or
    # script) that invented an id would otherwise read silence as "no
    # callers" and report a confident false negative
    row = conn.execute(
        "SELECT 1 FROM symbols WHERE snapshot_id=%s AND id=%s",
        (snap, symbol_id)).fetchone()
    if row is None:
        raise HTTPException(
            404, f"symbol {symbol_id} does not exist in snapshot {snap}: "
            "find valid symbol ids via symbol search first")


def _traverse(conn, snap: int, symbol_id: int, depth: int,
              direction: str) -> dict:
    require_symbol(conn, snap, symbol_id)
    depth = min(depth, settings.max_depth)
    next_col, match_col = (("src_id", "dst_id") if direction == "callers"
                           else ("dst_id", "src_id"))
    rows = conn.execute(
        TRAVERSE_SQL.format(next_col=next_col, match_col=match_col),
        {"sym": symbol_id, "snap": snap, "depth": depth}).fetchall()
    rows.sort(key=lambda r: (r["depth"], r["name"]))
    truncated = len(rows) > settings.max_nodes
    return {"symbol_id": symbol_id, "depth": depth,
            "count": min(len(rows), settings.max_nodes),
            "truncated": truncated, direction: rows[: settings.max_nodes]}


@app.get("/v1/snapshots/{snap}/symbols/{symbol_id}/callers", response_model=M.TraverseOut, response_model_exclude_none=True)
def callers(snap: int, symbol_id: int, depth: int = 2, conn=Depends(db)):
    ready_snapshot(conn, snap)
    audit(conn, "api", f"callers/{symbol_id}?depth={depth}")
    return _traverse(conn, snap, symbol_id, depth, "callers")


@app.get("/v1/snapshots/{snap}/symbols/{symbol_id}/callees", response_model=M.TraverseOut, response_model_exclude_none=True)
def callees(snap: int, symbol_id: int, depth: int = 2, conn=Depends(db)):
    ready_snapshot(conn, snap)
    audit(conn, "api", f"callees/{symbol_id}?depth={depth}")
    return _traverse(conn, snap, symbol_id, depth, "callees")


@app.get("/v1/snapshots/{snap}/impact", response_model=M.ImpactOut)
def impact(snap: int, symbol_id: int, depth: int = 3, conn=Depends(db)):
    """Blast radius: reverse closure over CALLS ∪ IMPORTS ∪ INHERITS."""
    ready_snapshot(conn, snap)
    require_symbol(conn, snap, symbol_id)
    audit(conn, "api", f"impact/{symbol_id}?depth={depth}")
    depth = min(depth, settings.max_depth)
    rows = conn.execute("""
        WITH RECURSIVE walk(id, depth) AS (
          SELECT %(sym)s::bigint, 0
          UNION
          SELECT e.src_id, w.depth + 1
          FROM edges e JOIN walk w ON e.snapshot_id = %(snap)s
           AND e.kind IN ('CALLS','IMPORTS','INHERITS')
           AND e.dst_id = w.id
          WHERE w.depth < %(depth)s
        )
        SELECT DISTINCT ON (w.id) w.id, w.depth, s.name, s.kind, f.path
        FROM walk w JOIN symbols s ON s.id = w.id
        LEFT JOIN files f ON f.id = s.file_id
        WHERE w.depth > 0 ORDER BY w.id, w.depth
        """, {"sym": symbol_id, "snap": snap, "depth": depth}).fetchall()
    rows.sort(key=lambda r: (r["depth"], r["name"]))
    return {"symbol_id": symbol_id, "depth": depth, "count": len(rows),
            "truncated": len(rows) > settings.max_nodes,
            "impacted": rows[: settings.max_nodes]}


@app.get("/v1/snapshots/{snap}/subgraph", response_model=M.SubgraphOut)
def subgraph(snap: int, root: int, hops: int = 2, conn=Depends(db)):
    """Bounded bidirectional neighborhood for visualization: nodes + the
    edges among them, server-extracted (§9 — the client never gets the full
    graph)."""
    ready_snapshot(conn, snap)
    audit(conn, "api", f"subgraph?root={root}&hops={hops}")
    hops = min(hops, 3)
    nodes = conn.execute("""
        WITH RECURSIVE walk(id, depth) AS (
          SELECT %(root)s::bigint, 0
          UNION
          (SELECT CASE WHEN e.src_id = w.id THEN e.dst_id ELSE e.src_id END,
                  w.depth + 1
           FROM edges e JOIN walk w
             ON e.snapshot_id = %(snap)s
            AND e.kind IN ('CALLS','IMPORTS','INHERITS')
            AND (e.src_id = w.id OR e.dst_id = w.id)
           WHERE w.depth < %(hops)s)
        )
        SELECT DISTINCT ON (w.id) w.id, w.depth, s.name, s.kind, f.path,
               s.is_external
        FROM walk w JOIN symbols s ON s.id = w.id
        LEFT JOIN files f ON f.id = s.file_id
        ORDER BY w.id, w.depth LIMIT %(cap)s
        """, {"root": root, "snap": snap, "hops": hops,
              "cap": settings.max_nodes}).fetchall()
    ids = [n["id"] for n in nodes]
    edges = conn.execute("""
        SELECT src_id, dst_id, kind, weight FROM edges
        WHERE snapshot_id = %s AND src_id = ANY(%s) AND dst_id = ANY(%s)
          AND kind IN ('CALLS','IMPORTS','INHERITS')
        """, (snap, ids, ids)).fetchall()
    return {"root": root, "hops": hops, "nodes": nodes, "edges": edges,
            "truncated": len(nodes) >= settings.max_nodes}


@app.get("/v1/snapshots/{snap}/sequence", response_model=M.SequenceOut)
def sequence(snap: int, symbol_id: int, depth: int = 3,
             max_steps: int = Query(30, le=80), conn=Depends(db)):
    """Approximate execution sequence from a starting function: a DFS over
    CALLS edges, children ordered by call-site line — so the order mirrors
    the source. Rendered as a mermaid sequenceDiagram, compiled from real
    edges like every Osprey diagram."""
    ready_snapshot(conn, snap)
    require_symbol(conn, snap, symbol_id)
    audit(conn, "api", f"sequence/{symbol_id}")
    depth = min(depth, 5)
    rows = conn.execute("""
        WITH RECURSIVE walk(id, d) AS (
          SELECT %(sym)s::bigint, 0
          UNION
          SELECT e.dst_id, w.d + 1
          FROM edges e JOIN walk w ON e.snapshot_id = %(snap)s
           AND e.kind = 'CALLS' AND e.src_id = w.id
          WHERE w.d < %(depth)s
        )
        SELECT e.src_id, e.dst_id, e.first_line, s.name AS dst_name,
               COALESCE(regexp_replace(f.path,'/[^/]+$',''),
                        '(external)') AS dst_module,
               s.is_external
        FROM edges e
        JOIN walk w ON w.id = e.src_id
        JOIN symbols s ON s.id = e.dst_id
        LEFT JOIN files f ON f.id = s.file_id
        WHERE e.snapshot_id = %(snap)s AND e.kind = 'CALLS'
        """, {"sym": symbol_id, "snap": snap, "depth": depth}).fetchall()

    root = conn.execute("""
        SELECT s.name, COALESCE(regexp_replace(f.path,'/[^/]+$',''),'') AS m
        FROM symbols s LEFT JOIN files f ON f.id = s.file_id
        WHERE s.id = %s""", (symbol_id,)).fetchone()

    children: dict[int, list] = {}
    module_of: dict[int, str] = {symbol_id: root["m"] or "(root)"}
    name_of: dict[int, str] = {symbol_id: root["name"]}
    for r in rows:
        children.setdefault(r["src_id"], []).append(r)
        module_of[r["dst_id"]] = ("(external)" if r["is_external"]
                                  else r["dst_module"] or "(root)")
        name_of[r["dst_id"]] = r["dst_name"]
    for lst in children.values():
        lst.sort(key=lambda r: (r["first_line"] is None, r["first_line"]))

    steps: list[dict] = []
    visited: set[int] = set()

    def dfs(node: int, d: int) -> None:
        if len(steps) >= max_steps or d >= depth or node in visited:
            return
        visited.add(node)
        for r in children.get(node, []):
            if len(steps) >= max_steps:
                return
            steps.append({
                "from_module": module_of[node],
                "to_module": module_of[r["dst_id"]],
                "call": name_of[r["dst_id"]],
                "site": (f"line {r['first_line'] + 1}"
                         if r["first_line"] is not None else ""),
            })
            dfs(r["dst_id"], d + 1)

    dfs(symbol_id, 0)

    participants: list[str] = []
    for s in [{"to_module": module_of[symbol_id]}, *steps]:
        for key in ("from_module", "to_module"):
            m = s.get(key)
            if m and m not in participants:
                participants.append(m)
    participants = participants[:8]
    kept = [s for s in steps if s["from_module"] in participants
            and s["to_module"] in participants]

    ident = {m: f"p{i}" for i, m in enumerate(participants)}
    lines = ["sequenceDiagram", "  autonumber"]
    for m in participants:
        lines.append(f"  participant {ident[m]} as {m}")
    for s in kept:
        lines.append(f"  {ident[s['from_module']]}->>"
                     f"{ident[s['to_module']]}: {s['call']}()")
    return {"root": root["name"], "mermaid": "\n".join(lines),
            "steps": kept,
            "truncated": len(steps) >= max_steps or len(kept) < len(steps)}


@app.get("/v1/snapshots/{snap}/endpoints", response_model=list[M.EndpointOut])
def endpoints(snap: int, conn=Depends(db)):
    """Detected entry points — HTTP route handlers, CLI commands, and main
    guards (§8.3). The answer to 'where are the endpoints / how are they
    registered'."""
    ready_snapshot(conn, snap)
    return conn.execute("""
        SELECT s.name, s.kind, s.entry_kind, f.path, s.start_line + 1 AS line
        FROM symbols s JOIN files f ON f.id = s.file_id
        WHERE s.snapshot_id = %s AND s.entry_kind IS NOT NULL
        ORDER BY s.entry_kind, f.path, s.start_line LIMIT 200
        """, (snap,)).fetchall()


@app.get("/v1/snapshots/{snap}/deadcode", response_model=M.DeadcodeOut)
def deadcode(snap: int, limit: int = Query(200, le=1000), conn=Depends(db)):
    """Symbols unreachable from any entry point (§8.3). Claims are suppressed
    for files with structural (heuristic) precision."""
    ready_snapshot(conn, snap)
    audit(conn, "api", "deadcode")
    n_entries = conn.execute(
        "SELECT count(*) AS n FROM symbols WHERE snapshot_id=%s"
        " AND entry_kind IS NOT NULL", (snap,)).fetchone()["n"]
    if n_entries == 0:
        raise HTTPException(400, "no entry points detected in this snapshot;"
                            " dead-code analysis would flag everything")
    try:
        rows = conn.execute("""
        WITH RECURSIVE reach(id) AS (
          SELECT id FROM symbols
          WHERE snapshot_id = %(snap)s AND entry_kind IS NOT NULL
          UNION
          SELECT e.dst_id FROM edges e JOIN reach r ON e.src_id = r.id
          WHERE e.snapshot_id = %(snap)s
            AND e.kind IN ('CALLS','REFERENCES','IMPORTS','INHERITS')
        )
        SELECT s.name, s.kind, f.path, s.start_line + 1 AS line
        FROM symbols s JOIN files f ON f.id = s.file_id
        WHERE s.snapshot_id = %(snap)s AND NOT s.is_external
          AND s.kind IN ('function','method','class')
          AND f.precision = 'scip'
          -- dunders dispatch via operator syntax (cache[k], len(x), with...)
          -- which call-position analysis cannot see; claiming them dead
          -- would be false confidence
          AND s.name NOT LIKE '\\_\\_%%\\_\\_'
          -- test/bench code is invoked by frameworks, not calls
          AND f.path !~ '(^|/)(tests?|__tests__|runtime-tests|benchmarks?)(/|$)'
          AND f.path !~ '(^|/)(test_[^/]*|conftest\\.py)$'
          AND NOT EXISTS (SELECT 1 FROM reach r WHERE r.id = s.id)
        ORDER BY f.path, line LIMIT %(limit)s
        """, {"snap": snap, "limit": limit}).fetchall()
    except pg_errors.QueryCanceled:
        conn.rollback()
        raise HTTPException(
            503, "dead-code analysis exceeded the time budget for this "
            "snapshot - try again shortly (statistics may still be "
            "settling)") from None
    return {"entry_points": n_entries, "candidates": rows,
            "count": len(rows)}


@app.get("/v1/snapshots/{snap}/modules", response_model=M.ModulesOut)
def modules(snap: int, kind: str = "", conn=Depends(db)):
    ready_snapshot(conn, snap)
    audit(conn, "api", "modules")
    nodes = conn.execute("""
        SELECT COALESCE(regexp_replace(path, '/[^/]+$', ''), '') AS module,
               sum(loc)::int AS loc, count(*)::int AS files
        FROM files WHERE snapshot_id = %s
        GROUP BY 1 ORDER BY 2 DESC LIMIT %s
        """, (snap, settings.max_nodes)).fetchall()
    edges = conn.execute("""
        SELECT src_module, dst_module, kind, weight FROM module_edges
        WHERE snapshot_id = %s AND (%s = '' OR kind = %s)
        ORDER BY weight DESC LIMIT %s
        """, (snap, kind, kind, settings.max_nodes)).fetchall()
    return {"nodes": nodes, "edges": edges}


def _module_adjacency(conn, snap: int) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT DISTINCT src_module, dst_module FROM module_edges"
        " WHERE snapshot_id = %s AND kind IN ('CALLS','IMPORTS','INHERITS')",
        (snap,)).fetchall()
    adj: dict[str, list[str]] = {}
    for r in rows:
        adj.setdefault(r["src_module"], []).append(r["dst_module"])
        adj.setdefault(r["dst_module"], [])
    return adj


def _tarjan_cycles(adj: dict[str, list[str]]) -> list[list[str]]:
    """SCCs with >1 member (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = 0
    for root in adj:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            advanced = False
            for i in range(pi, len(adj[node])):
                nxt = adj[node][i]
                if nxt not in index:
                    work[-1] = (node, i + 1)
                    work.append((nxt, 0))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            if low[node] == index[node]:
                scc = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc.append(member)
                    if member == node:
                        break
                if len(scc) > 1:
                    sccs.append(sorted(scc))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    sccs.sort()
    return sccs


@app.get("/v1/snapshots/{snap}/modules/cycles",
         response_model=M.CyclesOut)
def module_cycles(snap: int, conn=Depends(db)):
    """Package-level dependency cycles: strongly connected components of the
    module graph with more than one member."""
    ready_snapshot(conn, snap)
    audit(conn, "api", "modules/cycles")
    sccs = _tarjan_cycles(_module_adjacency(conn, snap))
    return {"count": len(sccs), "cycles": sccs}


@app.get("/v1/snapshots/{snap}/hotspots",
         response_model=list[M.HotspotOut])
def hotspots(snap: int, limit: int = Query(10, le=50), conn=Depends(db)):
    """Most depended-on first-party callables: top inbound-CALLS targets.
    These are the symbols whose changes ripple widest."""
    ready_snapshot(conn, snap)
    return conn.execute("""
        SELECT s.id AS symbol_id, s.name, s.kind, f.path,
               s.start_line + 1 AS line, count(*)::int AS inbound
        FROM edges e
        JOIN symbols s ON s.id = e.dst_id
        JOIN files f ON f.id = s.file_id
        WHERE e.snapshot_id = %s AND e.kind = 'CALLS' AND NOT s.is_external
          AND s.kind IN ('function','method')
          AND s.name NOT IN ('<constructor>', '__init__', 'constructor')
        GROUP BY s.id, s.name, s.kind, f.path, s.start_line
        ORDER BY inbound DESC LIMIT %s
        """, (snap, limit)).fetchall()


@app.get("/v1/snapshots/{snap}/overview", response_model=M.OverviewOut)
def overview(snap: int, conn=Depends(db)):
    """One-call summary for the landing page: sizes, findings, hotspots."""
    s = ready_snapshot(conn, snap)
    audit(conn, "api", "overview")
    langs = {r["language"]: r["loc"] for r in conn.execute(
        "SELECT language, sum(loc)::int AS loc FROM files"
        " WHERE snapshot_id=%s GROUP BY language ORDER BY 2 DESC",
        (snap,)).fetchall()}
    counts = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, count(*)::int AS n FROM symbols"
        " WHERE snapshot_id=%s AND NOT is_external GROUP BY kind",
        (snap,)).fetchall()}
    files_loc = conn.execute(
        "SELECT count(*)::int AS files, COALESCE(sum(loc),0)::int AS loc"
        " FROM files WHERE snapshot_id=%s", (snap,)).fetchone()
    n_modules = conn.execute(
        "SELECT count(DISTINCT src_module || ' ' || dst_module)::int AS n"
        " FROM module_edges WHERE snapshot_id=%s", (snap,)).fetchone()["n"]
    n_mods = conn.execute(
        "SELECT count(DISTINCT regexp_replace(path, '/[^/]+$', ''))::int"
        " AS n FROM files WHERE snapshot_id=%s", (snap,)).fetchone()["n"]
    cycles = _tarjan_cycles(_module_adjacency(conn, snap))
    entries = conn.execute(
        "SELECT count(*)::int AS n FROM symbols WHERE snapshot_id=%s"
        " AND entry_kind IS NOT NULL", (snap,)).fetchone()["n"]
    hot = conn.execute("""
        SELECT s.id AS symbol_id, s.name, s.kind, f.path,
               s.start_line + 1 AS line, count(*)::int AS inbound
        FROM edges e JOIN symbols s ON s.id = e.dst_id
        JOIN files f ON f.id = s.file_id
        WHERE e.snapshot_id = %s AND e.kind = 'CALLS' AND NOT s.is_external
          AND s.kind IN ('function','method')
          AND s.name NOT IN ('<constructor>', '__init__', 'constructor')
        GROUP BY s.id, s.name, s.kind, f.path, s.start_line
        ORDER BY inbound DESC LIMIT 5
        """, (snap,)).fetchall()
    # the reachability walk is the most expensive card on this page; if it
    # exceeds the statement timeout the landing page must degrade (deadcode
    # shows as unavailable), never 500. Runs LAST: a cancelled statement
    # aborts the transaction, killing any query that would follow it.
    dead = None
    if entries:
        try:
            dead = conn.execute("""
            WITH RECURSIVE reach(id) AS (
              SELECT id FROM symbols
              WHERE snapshot_id = %(snap)s AND entry_kind IS NOT NULL
              UNION
              SELECT e.dst_id FROM edges e JOIN reach r ON e.src_id = r.id
              WHERE e.snapshot_id = %(snap)s
                AND e.kind IN ('CALLS','REFERENCES','IMPORTS','INHERITS')
            )
            SELECT count(*)::int AS n
            FROM symbols s JOIN files f ON f.id = s.file_id
            WHERE s.snapshot_id = %(snap)s AND NOT s.is_external
              AND s.kind IN ('function','method','class')
              AND f.precision = 'scip'
              AND s.name NOT LIKE '\\_\\_%%\\_\\_'
              -- test/bench code is invoked by frameworks, not calls
              AND f.path !~ '(^|/)(tests?|__tests__|runtime-tests|benchmarks?)(/|$)'
              AND f.path !~ '(^|/)(test_[^/]*|conftest\\.py)$'
              AND NOT EXISTS (SELECT 1 FROM reach r WHERE r.id = s.id)
            """, {"snap": snap}).fetchone()["n"]
        except pg_errors.QueryCanceled:
            conn.rollback()
            dead = None
    return {
        "commit": s["commit_sha"], "files": files_loc["files"],
        "loc": files_loc["loc"], "languages": langs, "symbols": counts,
        "modules": n_mods, "module_dependencies": n_modules,
        "cycles": [c for c in cycles], "entry_points": entries,
        "deadcode": dead, "hotspots": hot,
    }


def _module_graph_for_export(conn, snap: int, limit: int = 500):
    nodes = conn.execute(
        "SELECT COALESCE(regexp_replace(path, '/[^/]+$', ''), '') AS module,"
        " sum(loc)::int AS loc FROM files WHERE snapshot_id=%s GROUP BY 1",
        (snap,)).fetchall()
    edges = conn.execute(
        "SELECT src_module, dst_module, kind, weight FROM module_edges"
        " WHERE snapshot_id=%s ORDER BY weight DESC LIMIT %s",
        (snap, limit)).fetchall()
    return nodes, edges


@app.get("/v1/snapshots/{snap}/export/graphml")
def export_graphml(snap: int, conn=Depends(db)):
    """Module graph as GraphML — opens in Gephi, yEd, Cytoscape."""
    from xml.sax.saxutils import escape

    from fastapi.responses import Response
    ready_snapshot(conn, snap)
    audit(conn, "api", "export/graphml")
    nodes, edges = _module_graph_for_export(conn, snap)
    used = {e["src_module"] for e in edges} | {e["dst_module"] for e in edges}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<key id="loc" for="node" attr.name="loc" attr.type="int"/>',
        '<key id="kind" for="edge" attr.name="kind" attr.type="string"/>',
        '<key id="weight" for="edge" attr.name="weight" attr.type="int"/>',
        '<graph edgedefault="directed">',
    ]
    loc_of = {n["module"]: n["loc"] for n in nodes}
    for m in sorted(used):
        lines.append(f'<node id="{escape(m, {chr(34): "&quot;"})}">'
                     f'<data key="loc">{loc_of.get(m, 0)}</data></node>')
    for i, e in enumerate(edges):
        src = escape(e["src_module"], {'"': "&quot;"})
        dst = escape(e["dst_module"], {'"': "&quot;"})
        lines.append(
            f'<edge id="e{i}" source="{src}" target="{dst}">'
            f'<data key="kind">{e["kind"]}</data>'
            f'<data key="weight">{e["weight"]}</data></edge>')
    lines += ["</graph>", "</graphml>"]
    return Response(
        "\n".join(lines), media_type="application/xml",
        headers={"Content-Disposition":
                 f'attachment; filename="osprey-snapshot-{snap}.graphml"'})


@app.get("/v1/snapshots/{snap}/export/mermaid")
def export_mermaid(snap: int, kind: str = "CALLS",
                   limit: int = Query(60, le=200), conn=Depends(db)):
    """Module graph as a Mermaid flowchart — paste into READMEs, wikis, PRs."""
    from fastapi.responses import PlainTextResponse
    ready_snapshot(conn, snap)
    audit(conn, "api", "export/mermaid")
    edges = conn.execute(
        "SELECT src_module, dst_module, weight FROM module_edges"
        " WHERE snapshot_id=%s AND kind=%s ORDER BY weight DESC LIMIT %s",
        (snap, kind, limit)).fetchall()
    ident: dict[str, str] = {}

    def nid(module: str) -> str:
        if module not in ident:
            ident[module] = f"m{len(ident)}"
        return ident[module]

    lines = ["flowchart LR"]
    for e in edges:
        s, d = e["src_module"] or "(root)", e["dst_module"] or "(root)"
        lines.append(f'  {nid(s)}["{s}"] --> {nid(d)}["{d}"]')
    if len(edges) == limit:
        lines.append(f"  %% truncated to top {limit} edges by weight")
    return PlainTextResponse("\n".join(lines))


@app.get("/v1/snapshots/{snap}/module-edge-sites",
         response_model=list[M.EdgeSiteOut])
def module_edge_sites(snap: int, src_module: str, dst_module: str,
                      limit: int = Query(5, le=50), conn=Depends(db)):
    """Symbol-level evidence for a module-level edge: who exactly depends on
    whom, with an example site. Powers gate violation reports."""
    ready_snapshot(conn, snap)
    return conn.execute("""
        SELECT ss.name AS src_name, sd.name AS dst_name, e.kind,
               f.path, e.first_line + 1 AS line
        FROM edges e
        JOIN symbols ss ON ss.id = e.src_id
        JOIN files fs ON fs.id = ss.file_id
        JOIN symbols sd ON sd.id = e.dst_id
        JOIN files fd ON fd.id = sd.file_id
        LEFT JOIN files f ON f.id = e.first_file_id
        WHERE e.snapshot_id = %(snap)s
          AND e.kind IN ('CALLS','IMPORTS','INHERITS')
          AND COALESCE(regexp_replace(fs.path, '/[^/]+$', ''), '')
              = %(src)s
          AND COALESCE(regexp_replace(fd.path, '/[^/]+$', ''), '')
              = %(dst)s
        ORDER BY e.weight DESC LIMIT %(limit)s
        """, {"snap": snap, "src": src_module, "dst": dst_module,
              "limit": limit}).fetchall()


_GIT_URL_RE = re.compile(
    r"https://([a-z0-9.-]+)/([\w.-]+)/([\w.-]+?)(?:\.git)?"
    r"(?:/(?:tree|commits?|releases/tag)/([^?#\s]+))?/?", re.I)
# refs are passed to `git fetch origin <ref>`: restrict the charset and
# forbid a leading '-' so a ref can never be parsed as a git option
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def _safe_name(name: str) -> str:
    try:
        return safe_repo_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _validate_git_url(url: str, name: str | None,
                      ref: str | None) -> tuple[str, str, str]:
    m = _GIT_URL_RE.fullmatch(url.strip())
    if m is None:
        raise HTTPException(
            400, "URL must look like https://github.com/owner/repo "
                 "(optionally .../tree/<branch> or .../releases/tag/<tag>)")
    host, _owner, repo = m.group(1).lower(), m.group(2), m.group(3)
    allowed = {h.strip().lower()
               for h in settings.allowed_git_hosts.split(",") if h.strip()}
    if host not in allowed:
        raise HTTPException(
            400, f"host {host!r} is not allowed (allowed: {sorted(allowed)})")
    # explicit ref beats a ref embedded in the URL; default HEAD
    chosen = (ref or "").strip() or (m.group(4) or "").strip() or "HEAD"
    if _REF_RE.fullmatch(chosen) is None:
        raise HTTPException(
            400, f"invalid ref {chosen!r}: use a branch, tag, or commit sha")
    # name reaches the worker as a filesystem path segment; validate it
    # (this also rejects a repo like '..' derived from a crafted URL)
    safe = _safe_name(name if name else repo)
    return (f"https://{host}/{m.group(2)}/{repo}.git", safe, chosen)


@app.post("/v1/repos/index", response_model=M.IndexJobOut)
def index_repo(body: M.IndexRequestIn,
               authorization: Annotated[str | None, Header()] = None):
    """Register a repository by URL and queue an indexing job. The write-path
    exception (§7): everything else on this API is read-only. Remote repos
    are always indexed in the container sandbox with deps disabled."""
    require_token(authorization)
    if settings.demo_mode:
        raise HTTPException(403, "this is a demo instance: submit a repo "
                            "request and the operator will index it for you")
    git_url, name, ref = _validate_git_url(body.git_url, body.name, body.ref)
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO repos (org_id, name, git_url)"
            " SELECT id, %s, %s FROM orgs WHERE name='default'"
            " ON CONFLICT (org_id, name)"
            " DO UPDATE SET git_url = EXCLUDED.git_url", (name, git_url))
        job = conn.execute(
            "INSERT INTO jobs (repo_id, ref)"
            " SELECT r.id, %s FROM repos r JOIN orgs o ON o.id = r.org_id"
            " WHERE o.name='default' AND r.name=%s RETURNING id",
            (ref, name)).fetchone()
        conn.execute(
            "INSERT INTO audit_log (org_id, actor, action)"
            " SELECT id, 'api', %s FROM orgs WHERE name='default'",
            (f"index_repo {git_url}@{ref}",))
    return {"job_id": job["id"], "repo": name}


@app.get("/v1/snapshots/{snap}/docs/{persona}",
         response_model=list[M.DocPageMeta])
def docs_tree(snap: int, persona: str, conn=Depends(db)):
    ready_snapshot(conn, snap)
    return conn.execute("""
        SELECT slug, title, position, parent_slug, status FROM doc_pages
        WHERE snapshot_id=%s AND persona=%s ORDER BY position, slug
        """, (snap, persona)).fetchall()


@app.get("/v1/snapshots/{snap}/docs/{persona}/{slug:path}",
         response_model=M.DocPageOut)
def docs_page(snap: int, persona: str, slug: str, conn=Depends(db)):
    s = ready_snapshot(conn, snap)
    row = conn.execute("""
        SELECT slug, title, status, content_md FROM doc_pages
        WHERE snapshot_id=%s AND persona=%s AND slug=%s
        """, (snap, persona, slug)).fetchone()
    if row is None:
        raise HTTPException(404, f"no doc page {persona}/{slug}")
    return {**row, "commit": s["commit_sha"], "persona": persona}


@app.get("/v1/me", response_model=M.MeOut)
def me():
    """Who is using this instance. Real identity arrives with SSO; until
    then this is the honest configurable label for the top-bar chip."""
    return {"user": settings.user_label,
            "auth": "token" if settings.api_token else "local",
            "demo": settings.demo_mode}


@app.post("/v1/repo-requests", response_model=M.RepoRequestOut)
def repo_request(body: M.RepoRequestIn,
                 authorization: Annotated[str | None, Header()] = None):
    """Demo-instance intake: visitors ask for a repository to be indexed;
    the operator reviews and fulfills manually (osprey requests)."""
    require_token(authorization)
    # same allowlist + ref hygiene as direct indexing
    git_url, _name, ref = _validate_git_url(body.git_url, None, body.ref)
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO repo_requests (git_url, ref, contact, note)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (git_url, ref, body.contact.strip(),
             body.note.strip())).fetchone()
        conn.execute(
            "INSERT INTO audit_log (org_id, actor, action)"
            " SELECT id, 'api', %s FROM orgs WHERE name='default'",
            (f"repo_request {git_url}@{ref}",))
    return {"id": row["id"], "status": "pending"}


@app.post("/v1/docs/generate", response_model=M.IndexJobOut)
def docs_generate(body: M.DocsGenerateIn,
                  authorization: Annotated[str | None, Header()] = None):
    """Queue doc synthesis for a ready snapshot (runs on the worker, where
    the LLM provider and source checkout live)."""
    require_token(authorization)
    if settings.demo_mode:
        raise HTTPException(403, "this is a demo instance: docs are "
                            "generated by the operator")
    from osprey.docs.pipeline import PERSONAS
    if body.persona not in PERSONAS:
        raise HTTPException(400, f"unknown persona {body.persona!r}; "
                            f"available: {sorted(PERSONAS)}")
    import json as _json
    with pool.connection() as conn:
        snap = conn.execute(
            "SELECT s.repo_id, r.name FROM snapshots s"
            " JOIN repos r ON r.id=s.repo_id"
            " WHERE s.id=%s AND s.status='ready'",
            (body.snapshot_id,)).fetchone()
        if snap is None:
            raise HTTPException(404, "snapshot not found or not ready")
        job = conn.execute(
            "INSERT INTO jobs (repo_id, kind, payload) VALUES (%s,'docs',%s)"
            " RETURNING id",
            (snap["repo_id"], _json.dumps(
                {"snapshot_id": body.snapshot_id,
                 "persona": body.persona}))).fetchone()
        conn.execute(
            "INSERT INTO audit_log (org_id, actor, action)"
            " SELECT id, 'api', %s FROM orgs WHERE name='default'",
            (f"docs_generate snap={body.snapshot_id} {body.persona}",))
    return {"job_id": job["id"], "repo": snap["name"]}


@app.get("/v1/snapshots/{snap}/docs-search",
         response_model=list[M.DocSearchHit])
def docs_search(snap: int, q: str = Query(min_length=2),
                limit: int = Query(5, le=20), conn=Depends(db)):
    """Semantic search over generated docs (pgvector). Complements the
    structural tools — prose recall + graph precision."""
    ready_snapshot(conn, snap)
    import httpx as _httpx
    import json as _json
    try:
        res = _httpx.post(f"{settings.ollama_url}/api/embed", json={
            "model": "nomic-embed-text", "input": [q]}, timeout=30)
        res.raise_for_status()
        emb = res.json()["embeddings"][0]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503, "doc search needs the local embedding model "
            f"(nomic-embed-text via Ollama): {str(exc)[:120]}") from exc
    return conn.execute("""
        SELECT source, content,
               1 - (embedding <=> %(e)s::vector) AS score
        FROM doc_chunks WHERE snapshot_id=%(s)s AND embedding IS NOT NULL
        ORDER BY embedding <=> %(e)s::vector LIMIT %(l)s
        """, {"e": _json.dumps(emb), "s": snap, "l": limit}).fetchall()


@app.post("/v1/ask", response_model=M.AskOut)
def ask(body: M.AskIn, conn=Depends(db)):
    """English questions over the graph. The model only uses the typed tool
    menu (§10) — every claim in the answer is backed by a traced tool call."""
    snap = conn.execute(
        "SELECT s.id, s.commit_sha, r.name AS repo FROM snapshots s"
        " JOIN repos r ON r.id = s.repo_id"
        " WHERE s.id = %s AND s.status = 'ready'",
        (body.snapshot_id,)).fetchone()
    if snap is None:
        raise HTTPException(404, "snapshot not found or not ready")
    audit(conn, "api", f"ask?snapshot={body.snapshot_id}")
    from osprey.api.ask import run_ask
    try:
        return run_ask(snap["id"], snap["repo"], snap["commit_sha"],
                       body.question,
                       [m.model_dump() for m in body.history[-8:]])
    except Exception as exc:  # noqa: BLE001 — surface provider problems
        raise HTTPException(
            502, f"chat provider unavailable: {str(exc)[:200]}") from exc


@app.get("/v1/jobs/{job_id}", response_model=M.JobOut)
def job_status(job_id: int, conn=Depends(db)):
    row = conn.execute("""
        SELECT j.id, j.status, j.error, r.name AS repo,
               (SELECT s.status FROM snapshots s
                WHERE s.repo_id = j.repo_id
                ORDER BY s.id DESC LIMIT 1) AS snapshot_status
        FROM jobs j JOIN repos r ON r.id = j.repo_id WHERE j.id = %s
        """, (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"job {job_id} not found")
    return row


@app.get("/v1/diff", response_model=M.DiffOut)
def diff(base: int, head: int, conn=Depends(db)):
    """Structural diff on stable-symbol identity (ARCHITECTURE.md §8.1)."""
    b, h = ready_snapshot(conn, base), ready_snapshot(conn, head)
    if b["repo_id"] != h["repo_id"]:
        raise HTTPException(400, "snapshots belong to different repos")
    audit(conn, "api", f"diff?base={base}&head={head}")

    edge_diff_sql = """
        (SELECT ss.stable_symbol AS src, sd.stable_symbol AS dst, e.kind
         FROM edges e
         JOIN symbols ss ON ss.id = e.src_id
         JOIN symbols sd ON sd.id = e.dst_id
         WHERE e.snapshot_id = %(a)s)
        EXCEPT
        (SELECT ss.stable_symbol, sd.stable_symbol, e.kind
         FROM edges e
         JOIN symbols ss ON ss.id = e.src_id
         JOIN symbols sd ON sd.id = e.dst_id
         WHERE e.snapshot_id = %(b)s)
        LIMIT 500
    """
    added = conn.execute(edge_diff_sql, {"a": head, "b": base}).fetchall()
    removed = conn.execute(edge_diff_sql, {"a": base, "b": head}).fetchall()

    sym_diff_sql = """
        (SELECT stable_symbol FROM symbols
         WHERE snapshot_id = %(a)s AND NOT is_external)
        EXCEPT
        (SELECT stable_symbol FROM symbols
         WHERE snapshot_id = %(b)s AND NOT is_external)
        LIMIT 500
    """
    sym_added = conn.execute(sym_diff_sql,
                             {"a": head, "b": base}).fetchall()
    sym_removed = conn.execute(sym_diff_sql,
                               {"a": base, "b": head}).fetchall()

    return {
        "base": {"snapshot": base, "commit": b["commit_sha"]},
        "head": {"snapshot": head, "commit": h["commit_sha"]},
        "edges_added": added, "edges_removed": removed,
        "symbols_added": [r["stable_symbol"] for r in sym_added],
        "symbols_removed": [r["stable_symbol"] for r in sym_removed],
    }


# serve the built UI (osprey/web/dist) from the API origin when present —
# single-process deployment, no separate web server needed
from pathlib import Path as _Path  # noqa: E402

_dist = (_Path(settings.ui_dist) if settings.ui_dist
         else _Path(__file__).resolve().parents[2] / "web" / "dist")
if _dist.exists():
    from fastapi.staticfiles import StaticFiles  # noqa: E402
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
