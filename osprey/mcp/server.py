"""osprey-mcp: the graph as typed MCP tools (ARCHITECTURE.md §10).

The model selects tools and fills validated arguments - it never generates
SQL or Cypher. Every tool wraps a query-API endpoint 1:1, so auth, audit,
read-only enforcement, and cost caps all stay server-side. Every result
carries provenance: the snapshot id + commit it came from, and file:line on
each row. Results are capped - an agent context is a budget, not a dump site.

Config: OSPREY_API_URL (default http://127.0.0.1:8800), OSPREY_API_TOKEN.
Run: `osprey-mcp` (stdio transport).
"""

from __future__ import annotations

import httpx
from mcp.server import MCPServer

from osprey.config import settings

mcp = MCPServer(
    name="osprey",
    version="0.1.0",
    instructions=(
        "Osprey serves deterministic, compiler-grade facts about indexed "
        "codebases: who calls what, what breaks if a symbol changes, module "
        "dependencies, cycles, dead code, and structural diffs between "
        "commits. Typical flow: search_symbols to find a symbol id, then "
        "get_callers / blast_radius with that id. Cite the file:line "
        "provenance from tool results in answers."),
)

_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        headers = ({"Authorization": f"Bearer {settings.api_token}"}
                   if settings.api_token else {})
        _client = httpx.Client(base_url=settings.api_url, headers=headers,
                               timeout=30)
    return _client


def get(path: str, **params) -> dict | list:
    res = client().get(path, params=params or None)
    res.raise_for_status()
    return res.json()


def short(sym: str) -> str:
    """SCIP symbol -> readable descriptor (drop scheme/manager/pkg/version)."""
    parts = sym.split(" ")
    return " ".join(parts[4:]) if len(parts) > 4 else sym


def resolve(repo: str, ref: str) -> dict:
    """ref: 'latest', 'previous', a snapshot id, or a commit-sha prefix."""
    snaps = [s for s in get(f"/v1/repos/{repo}/snapshots")
             if s["status"] == "ready"]
    if not snaps:
        raise ValueError(f"repo {repo!r} has no ready snapshots")
    if ref == "latest":
        return snaps[0]
    if ref == "previous":
        if len(snaps) < 2:
            raise ValueError(f"repo {repo!r} has only one snapshot")
        return snaps[1]
    for s in snaps:
        if str(s["id"]) == ref or s["commit_sha"].startswith(ref):
            return s
    raise ValueError(f"no ready snapshot of {repo!r} matches {ref!r}")


def _prov(snap: dict) -> dict:
    return {"snapshot_id": snap["id"], "commit": snap["commit_sha"][:12]}


# --------------------------------------------------------------- discovery

@mcp.tool()
def list_repos() -> list[dict]:
    """List indexed repositories and their latest ready snapshot id."""
    return get("/v1/repos")


@mcp.tool()
def list_snapshots(repo: str, limit: int = 10) -> list[dict]:
    """List recent snapshots (immutable per-commit indexes) of a repository,
    newest first. Use snapshot ids or commit shas as refs in other tools."""
    rows = get(f"/v1/repos/{repo}/snapshots")
    return [{"id": s["id"], "commit": s["commit_sha"][:12],
             "status": s["status"], "stats": s.get("stats", {})}
            for s in rows[:limit]]


@mcp.tool()
def search_symbols(repo: str, query: str, kind: str = "",
                   ref: str = "latest") -> dict:
    """Find functions/classes/methods by name substring. Returns symbol ids
    for use with get_callers / get_callees / blast_radius. kind filters to
    one of: function, method, class, module, variable."""
    snap = resolve(repo, ref)
    rows = get(f"/v1/snapshots/{snap['id']}/symbols", q=query, kind=kind,
               limit=25)
    return {**_prov(snap), "results": [
        {"symbol_id": r["id"], "name": r["name"], "kind": r["kind"],
         "location": f"{r['path']}:{r['line']}" if r["path"] else "external"}
        for r in rows]}


# --------------------------------------------------------------- traversal

def _traverse(snapshot_id: int, symbol_id: int, depth: int,
              direction: str) -> dict:
    data = get(f"/v1/snapshots/{snapshot_id}/symbols/{symbol_id}/{direction}",
               depth=depth)
    rows = data.get(direction) or []
    return {"snapshot_id": snapshot_id, "count": data["count"],
            "truncated": data["truncated"], direction: [
                {"symbol_id": r["id"], "hops": r["depth"], "name": r["name"],
                 "kind": r["kind"],
                 "location": f"{r['path']}:{r.get('line')}" if r["path"]
                 else "external"}
                for r in rows[:100]]}


@mcp.tool()
def get_callers(snapshot_id: int, symbol_id: int, depth: int = 2) -> dict:
    """Who calls this symbol, transitively up to `depth` hops (CALLS edges
    only, compiler-resolved). Get symbol_id from search_symbols first."""
    return _traverse(snapshot_id, symbol_id, depth, "callers")


@mcp.tool()
def get_callees(snapshot_id: int, symbol_id: int, depth: int = 2) -> dict:
    """What this symbol calls, transitively up to `depth` hops."""
    return _traverse(snapshot_id, symbol_id, depth, "callees")


@mcp.tool()
def blast_radius(snapshot_id: int, symbol_id: int, depth: int = 3) -> dict:
    """Everything affected if this symbol changes: reverse closure over
    CALLS + IMPORTS + INHERITS. The definitive 'what breaks?' answer."""
    data = get(f"/v1/snapshots/{snapshot_id}/impact", symbol_id=symbol_id,
               depth=depth)
    by_hop: dict[int, int] = {}
    for r in data["impacted"]:
        by_hop[r["depth"]] = by_hop.get(r["depth"], 0) + 1
    return {"snapshot_id": snapshot_id, "affected": data["count"],
            "truncated": data["truncated"],
            "by_hop": by_hop,
            "symbols": [
                {"hops": r["depth"], "name": r["name"], "kind": r["kind"],
                 "location": r["path"] or "external"}
                for r in data["impacted"][:100]]}


# --------------------------------------------------------------- structure

@mcp.tool()
def module_graph(repo: str, ref: str = "latest", kind: str = "CALLS",
                 limit: int = 50) -> dict:
    """Directory-level dependency graph: which modules depend on which, with
    weights. kind: CALLS, IMPORTS, or INHERITS."""
    snap = resolve(repo, ref)
    data = get(f"/v1/snapshots/{snap['id']}/modules", kind=kind)
    return {**_prov(snap),
            "modules": [{"module": n["module"], "loc": n["loc"]}
                        for n in data["nodes"][:limit]],
            "dependencies": [
                {"from": e["src_module"], "to": e["dst_module"],
                 "kind": e["kind"], "sites": e["weight"]}
                for e in data["edges"][:limit]]}


@mcp.tool()
def find_cycles(repo: str, ref: str = "latest") -> dict:
    """Package-level dependency cycles (strongly connected components of the
    module graph). Cycles are the classic architecture erosion signal."""
    snap = resolve(repo, ref)
    data = get(f"/v1/snapshots/{snap['id']}/modules/cycles")
    return {**_prov(snap), "count": data["count"], "cycles": data["cycles"]}


@mcp.tool()
def edge_evidence(snapshot_id: int, src_module: str,
                  dst_module: str) -> list[dict]:
    """Symbol-level proof of a module dependency: exactly which symbols in
    src_module reach which in dst_module, with an example file:line each."""
    rows = get(f"/v1/snapshots/{snapshot_id}/module-edge-sites",
               src_module=src_module, dst_module=dst_module, limit=10)
    return [{"from": r["src_name"], "to": r["dst_name"], "kind": r["kind"],
             "site": f"{r['path']}:{r['line']}" if r["path"] else "?"}
            for r in rows]


# ----------------------------------------------------------------- change

@mcp.tool()
def structural_diff(repo: str, base: str, head: str = "latest") -> dict:
    """What changed structurally between two snapshots: dependency edges and
    symbols added/removed, matched across commits by stable identity.
    base/head: snapshot id, commit-sha prefix, 'latest', or 'previous'."""
    b, h = resolve(repo, base), resolve(repo, head)
    d = get("/v1/diff", base=b["id"], head=h["id"])
    fmt = lambda e: {"from": short(e["src"]), "to": short(e["dst"]),
                     "kind": e["kind"]}
    return {
        "base": {"snapshot_id": b["id"], "commit": b["commit_sha"][:12]},
        "head": {"snapshot_id": h["id"], "commit": h["commit_sha"][:12]},
        "edges_added": [fmt(e) for e in d["edges_added"][:50]],
        "edges_removed": [fmt(e) for e in d["edges_removed"][:50]],
        "symbols_added": [short(s) for s in d["symbols_added"][:50]],
        "symbols_removed": [short(s) for s in d["symbols_removed"][:50]],
    }


@mcp.tool()
def dead_code(repo: str, ref: str = "latest", limit: int = 50) -> dict:
    """Symbols unreachable from any detected entry point (HTTP routes, CLI
    commands, main guards). Conservative: operator-dispatched dunders and
    heuristic-precision files are excluded from claims."""
    snap = resolve(repo, ref)
    data = get(f"/v1/snapshots/{snap['id']}/deadcode", limit=limit)
    return {**_prov(snap), "entry_points": data["entry_points"],
            "candidates": [
                {"name": c["name"], "kind": c["kind"],
                 "location": f"{c['path']}:{c['line']}"}
                for c in data["candidates"]]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
