"""The Ask tool-loop (ARCHITECTURE.md §10): question -> model picks typed
tools -> tools hit the query API -> answer with citations.

The model never writes queries; it fills validated arguments on a fixed tool
menu. Tools are pre-bound to the active snapshot so a small local model
juggles one or two arguments, not repo/ref bookkeeping. Every call lands in
the trace shown to the user — a chat answer is auditable evidence, not vibes.
"""

from __future__ import annotations

import json

import httpx

from osprey.config import settings
from osprey.api.providers import get_provider

_client: httpx.Client | None = None


def _get(path: str, **params):
    global _client
    if _client is None:
        headers = ({"Authorization": f"Bearer {settings.api_token}"}
                   if settings.api_token else {})
        _client = httpx.Client(base_url=settings.api_url, headers=headers,
                               timeout=30)
    res = _client.get(path, params=params or None)
    if res.status_code >= 400:
        # surface the API's own explanation to the model — a 404 with
        # "use symbol search first" is corrective feedback it can act on
        detail = res.json().get("detail", res.text[:200]) \
            if res.headers.get("content-type", "").startswith(
                "application/json") else res.text[:200]
        raise RuntimeError(detail)
    return res.json()


def _fn(name: str, description: str, props: dict, required: list[str]):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props,
                       "required": required}}}


TOOL_DEFS = [
    _fn("search_symbols",
        "Find functions/classes/methods by name substring. Returns "
        "symbol_id values for the other tools.",
        {"query": {"type": "string"},
         "kind": {"type": "string",
                  "enum": ["", "function", "method", "class"]}},
        ["query"]),
    _fn("get_callers",
        "Who calls this symbol, transitively up to depth hops.",
        {"symbol_id": {"type": "integer"},
         "depth": {"type": "integer", "minimum": 1, "maximum": 5}},
        ["symbol_id"]),
    _fn("get_callees",
        "What this symbol calls, transitively up to depth hops.",
        {"symbol_id": {"type": "integer"},
         "depth": {"type": "integer", "minimum": 1, "maximum": 5}},
        ["symbol_id"]),
    _fn("blast_radius",
        "Everything affected if this symbol changes (callers + importers "
        "+ subclasses). THE tool for 'what breaks if...' questions.",
        {"symbol_id": {"type": "integer"},
         "depth": {"type": "integer", "minimum": 1, "maximum": 5}},
        ["symbol_id"]),
    _fn("module_graph",
        "Folder-level dependency graph with weights.",
        {"kind": {"type": "string",
                  "enum": ["CALLS", "IMPORTS", "INHERITS"]}}, []),
    _fn("find_cycles",
        "Folder-level circular dependencies (architecture erosion signal).",
        {}, []),
    _fn("dead_code",
        "Code unreachable from any entry point: deletion candidates.",
        {}, []),
    _fn("hotspots",
        "Most-called functions: where changes ripple widest.",
        {}, []),
    _fn("list_entry_points",
        "List detected entry points: HTTP route handlers, CLI commands, "
        "main guards. Use this for questions about endpoints/routes/APIs "
        "and how they're registered.",
        {}, []),
    _fn("search_docs",
        "Semantic search over this repo's generated documentation. Use for "
        "conceptual/why/how questions before falling back to symbol tools.",
        {"query": {"type": "string"}}, ["query"]),
]


def make_tools(snap: int):
    def search_symbols(query: str, kind: str = ""):
        rows = _get(f"/v1/snapshots/{snap}/symbols", q=query, kind=kind,
                    limit=15)
        return [{"symbol_id": r["id"], "name": r["name"], "kind": r["kind"],
                 "location": f"{r['path']}:{r['line']}" if r["path"]
                 else "external"} for r in rows]

    def _traverse(direction, symbol_id: int, depth: int = 2):
        d = _get(f"/v1/snapshots/{snap}/symbols/{int(symbol_id)}/{direction}",
                 depth=depth)
        return {"count": d["count"], direction: [
            {"hops": r["depth"], "name": r["name"],
             "location": f"{r['path']}:{r.get('line')}" if r["path"]
             else "external"} for r in (d.get(direction) or [])[:40]]}

    def blast_radius(symbol_id: int, depth: int = 3):
        d = _get(f"/v1/snapshots/{snap}/impact", symbol_id=int(symbol_id),
                 depth=depth)
        return {"affected": d["count"], "sample": [
            {"hops": r["depth"], "name": r["name"], "location": r["path"]}
            for r in d["impacted"][:40]]}

    def module_graph(kind: str = "CALLS"):
        d = _get(f"/v1/snapshots/{snap}/modules", kind=kind)
        return [{"from": e["src_module"], "to": e["dst_module"],
                 "uses": e["weight"]} for e in d["edges"][:40]]

    def find_cycles():
        return _get(f"/v1/snapshots/{snap}/modules/cycles")

    def dead_code():
        d = _get(f"/v1/snapshots/{snap}/deadcode", limit=40)
        return {"entry_points": d["entry_points"], "candidates": [
            {"name": c["name"], "location": f"{c['path']}:{c['line']}"}
            for c in d["candidates"]]}

    def hotspots():
        return [{"symbol_id": h["symbol_id"], "name": h["name"],
                 "callers": h["inbound"],
                 "location": f"{h['path']}:{h['line']}"}
                for h in _get(f"/v1/snapshots/{snap}/hotspots", limit=10)]

    def list_entry_points():
        rows = _get(f"/v1/snapshots/{snap}/endpoints")
        if not rows:
            return {"entry_points": [], "note": "no HTTP routes, CLI "
                    "commands, or main guards were detected - this may be a "
                    "library, or use a framework Osprey doesn't yet detect"}
        return [{"name": r["name"], "type": r["entry_kind"],
                 "location": f"{r['path']}:{r['line']}"} for r in rows[:60]]

    return {
        "search_symbols": search_symbols,
        "get_callers": lambda symbol_id, depth=2:
            _traverse("callers", symbol_id, depth),
        "get_callees": lambda symbol_id, depth=2:
            _traverse("callees", symbol_id, depth),
        "blast_radius": blast_radius,
        "module_graph": module_graph,
        "find_cycles": find_cycles,
        "dead_code": dead_code,
        "hotspots": hotspots,
        "list_entry_points": list_entry_points,
        "search_docs": lambda query: _get(
            f"/v1/snapshots/{snap}/docs-search", q=query),
    }


SYSTEM = """You are Osprey, a code-analysis assistant. You answer questions
about the codebase '{repo}' (analyzed at commit {commit}) using tools that
return verified facts from a compiler-grade dependency graph.

SCOPE (hard rule, overrides politeness):
- You ONLY answer questions about this codebase: its structure, symbols,
  dependencies, risks, docs, and behavior as shown by the tools.
- If a request is anything else (general programming help, writing new
  programs, homework, math, translations, chit-chat, advice, or any
  other topic), REFUSE. Do not fulfill it "as a bonus", do not include
  code for it, do not answer it partially. Reply with one short sentence:
  this assistant only analyzes '{repo}', plus ONE example question the
  user could ask about it. Nothing more.
- Writing code is out of scope. The only code you may show is code that
  already exists in this repository, quoted from tool results.

Rules:
- Ground every claim in a tool result; if you haven't looked, look.
- Cite locations (path:line) from tool results next to each claim.
- symbol_id values come ONLY from search_symbols or hotspots results;
  never invent one. Always search before get_callers/blast_radius.
- Plain language, short answers. Say "the graph doesn't show that" rather
  than guessing. Never invent symbols, numbers, or locations."""


def run_ask(snap: int, repo: str, commit: str, question: str,
            history: list[dict]) -> dict:
    provider = get_provider()
    tools = make_tools(snap)
    # defense in depth: only user/assistant turns are ever replayed, so a
    # caller cannot smuggle a 'system' turn to override the scope guardrail
    # or fabricate 'tool' results, regardless of how history was built
    safe_history = [
        {"role": m["role"], "content": str(m["content"])[:8000]}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    messages = [
        {"role": "system",
         "content": SYSTEM.format(repo=repo, commit=commit[:8])},
        *safe_history,
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    text = ""
    seen_calls: dict[tuple, object] = {}   # dedupe identical tool calls
    exhausted = True
    for _ in range(settings.chat_max_steps):
        resp = provider.chat(messages, TOOL_DEFS)
        text = resp.text
        if not resp.tool_calls:
            exhausted = False
            break
        messages.append(resp.raw_message)
        for call in resp.tool_calls:
            key = (call.name, json.dumps(call.arguments, sort_keys=True))
            if key in seen_calls:
                # the model is repeating itself (observed: 29 identical
                # searches) — return the prior result and tell it to move on
                result = {"note": "you already ran this exact call; use the "
                          "earlier result and either try a DIFFERENT tool or "
                          "give your final answer now", "result": seen_calls[key]}
            else:
                fn = tools.get(call.name)
                if fn is None:
                    result = {"error": f"unknown tool {call.name}"}
                else:
                    try:
                        result = fn(**call.arguments)
                    except Exception as exc:  # noqa: BLE001 — surface to model
                        result = {"error": str(exc)[:300]}
                seen_calls[key] = result
            trace.append({"tool": call.name, "args": call.arguments})
            messages.append(provider.tool_result_message(call, result))

    # never return the model's "let me look…" preamble as the answer: if it
    # ran out of tool budget still mid-investigation, force one final call
    # with no tools so it must synthesize from what it gathered
    if exhausted:
        messages.append({
            "role": "user",
            "content": "Stop investigating and answer now from the tool "
                       "results above. If they're incomplete, give the best "
                       "answer you can and note what's uncertain. Cite "
                       "path:line where you have it."})
        final = provider.chat(messages, [])
        text = final.text or text

    answer = text or ("I couldn't find enough in the graph to answer "
                      "that - try naming a specific function or file.")
    # house style: no em-dashes in anything shown to the user
    answer = answer.replace(" — ", " - ").replace("—", " - ").replace("–", "-")
    return {"answer": answer, "trace": trace}
