"""Osprey Docs synthesis pipeline (ARCHITECTURE.md §18.3).

outline (deterministic) -> synthesize (grounded LLM, server-side diagrams)
-> verify (citations checked against the graph) -> persist + embed.

The LLM writes prose only. Structure comes from the graph; diagrams are
compiled from real edges; every `path:line` citation is validated against
the snapshot before a page can be 'verified'.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg

from osprey.api.providers import get_provider
from osprey.config import settings

CITE_RE = re.compile(r"\b([\w./-]+\.[A-Za-z]{1,4}):(\d{1,6})\b")
MAX_SOURCE_CHARS = 6000
MODULE_PAGES = 6


@dataclass
class PageSpec:
    slug: str
    title: str
    kind: str                      # overview|architecture|module|entries|hot
    position: int
    parent_slug: str | None = None
    module: str | None = None      # for kind == 'module'


@dataclass
class GenStats:
    pages: int = 0
    verified: int = 0
    failed_cites_removed: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- outline

def outline(conn, snap: int, persona: str) -> list[PageSpec]:
    """The doc tree is a computation over the graph, never an LLM guess.
    Which pages exist depends on the persona's job, not on the model."""
    wanted = PERSONAS[persona]["pages"]
    titles = {"overview": "Overview", "architecture": "Architecture",
              "entries": "Entry points", "hot": "Key functions"}
    if persona == "sre":
        titles.update({"entries": "Where execution enters",
                       "hot": "Widest blast radius",
                       "architecture": "Failure domains"})
    if persona == "tester":
        titles.update({"hot": "Regression risk",
                       "entries": "Surfaces to exercise",
                       "architecture": "Integration seams"})
    pages: list[PageSpec] = []
    pos = 0
    for kind in wanted:
        if kind == "modules":
            mods = conn.execute("""
                SELECT COALESCE(regexp_replace(f.path,'/[^/]+$',''),'')
                       AS module, sum(f.loc)::int AS loc
                FROM files f WHERE f.snapshot_id=%s
                  AND f.path !~ '(^|/)(tests?|__tests__|runtime-tests|benchmarks?)(/|$)'
                GROUP BY 1 ORDER BY 2 DESC LIMIT %s
                """, (snap, MODULE_PAGES)).fetchall()
            for m in mods:
                mod = m["module"] or "(root)"
                slug = "modules/" + (m["module"].replace("/", "-") or "root")
                pos += 1
                pages.append(PageSpec(slug, mod, "module", 10 + pos,
                                      parent_slug="architecture",
                                      module=m["module"]))
            continue
        if kind == "entries":
            n = conn.execute(
                "SELECT count(*) AS n FROM symbols WHERE snapshot_id=%s"
                " AND entry_kind IS NOT NULL", (snap,)).fetchone()["n"]
            if not n:
                continue
        pos += 10
        slug = {"overview": "overview", "architecture": "architecture",
                "entries": "entry-points", "hot": "key-functions"}[kind]
        pages.append(PageSpec(slug, titles[kind], kind, pos))
    return pages


# ------------------------------------------------------------------ facts

def _facts(conn, snap: int, spec: PageSpec, repo: str) -> dict:
    f: dict = {"repo": repo}
    if spec.kind in ("overview", "architecture"):
        f["totals"] = conn.execute(
            "SELECT count(*)::int AS files, COALESCE(sum(loc),0)::int AS loc"
            " FROM files WHERE snapshot_id=%s", (snap,)).fetchone()
        f["languages"] = {r["language"]: r["loc"] for r in conn.execute(
            "SELECT language, sum(loc)::int AS loc FROM files"
            " WHERE snapshot_id=%s GROUP BY 1 ORDER BY 2 DESC", (snap,))}
        f["module_dependencies"] = conn.execute("""
            SELECT src_module, dst_module, kind, weight FROM module_edges
            WHERE snapshot_id=%s ORDER BY weight DESC LIMIT 25
            """, (snap,)).fetchall()
    if spec.kind == "module" and spec.module is not None:
        f["files"] = conn.execute("""
            SELECT path, loc FROM files WHERE snapshot_id=%(s)s
              AND COALESCE(regexp_replace(path,'/[^/]+$',''),'') = %(m)s
            ORDER BY loc DESC""", {"s": snap, "m": spec.module}).fetchall()
        f["public_symbols"] = conn.execute("""
            SELECT s.name, s.kind, fl.path, s.start_line+1 AS line
            FROM symbols s JOIN files fl ON fl.id=s.file_id
            WHERE s.snapshot_id=%(s)s AND NOT s.is_external
              AND s.kind IN ('function','class','method')
              AND s.name NOT LIKE '\\_%%'
              AND COALESCE(regexp_replace(fl.path,'/[^/]+$',''),'') = %(m)s
            ORDER BY s.kind, fl.path, s.start_line LIMIT 30
            """, {"s": snap, "m": spec.module}).fetchall()
        f["uses"] = conn.execute("""
            SELECT dst_module, kind, weight FROM module_edges
            WHERE snapshot_id=%(s)s AND src_module=%(m)s
            ORDER BY weight DESC LIMIT 10""",
            {"s": snap, "m": spec.module}).fetchall()
        f["used_by"] = conn.execute("""
            SELECT src_module, kind, weight FROM module_edges
            WHERE snapshot_id=%(s)s AND dst_module=%(m)s
            ORDER BY weight DESC LIMIT 10""",
            {"s": snap, "m": spec.module}).fetchall()
    if spec.kind == "entries":
        f["entry_points"] = conn.execute("""
            SELECT s.name, s.entry_kind, fl.path, s.start_line+1 AS line
            FROM symbols s JOIN files fl ON fl.id=s.file_id
            WHERE s.snapshot_id=%s AND s.entry_kind IS NOT NULL
            ORDER BY s.entry_kind, fl.path LIMIT 60""", (snap,)).fetchall()
    if spec.kind == "hot":
        f["hotspots"] = conn.execute("""
            SELECT s.name, s.kind, fl.path, s.start_line+1 AS line,
                   count(*)::int AS callers
            FROM edges e JOIN symbols s ON s.id=e.dst_id
            JOIN files fl ON fl.id=s.file_id
            WHERE e.snapshot_id=%s AND e.kind='CALLS' AND NOT s.is_external
              AND s.kind IN ('function','method')
            GROUP BY s.id, s.name, s.kind, fl.path, s.start_line
            ORDER BY callers DESC LIMIT 12""", (snap,)).fetchall()
    return f


def _source_slices(source_root: Path | None, facts: dict) -> str:
    if source_root is None:
        return "(source unavailable: write from the graph facts only)"
    paths: list[str] = []
    for key in ("files", "public_symbols", "entry_points", "hotspots"):
        for row in facts.get(key, [])[:8]:
            if row.get("path") and row["path"] not in paths:
                paths.append(row["path"])
    out, budget = [], MAX_SOURCE_CHARS
    for p in paths[:8]:
        fp = source_root / p
        if not fp.exists():
            continue
        text = fp.read_text(errors="replace")[: min(1500, budget)]
        budget -= len(text)
        out.append(f"--- {p} (first lines) ---\n{text}")
        if budget <= 0:
            break
    return "\n".join(out) or "(no source slices)"


# --------------------------------------------------------------- diagrams

def _diagram(conn, snap: int, spec: PageSpec) -> str | None:
    """Mermaid compiled from real edges — the LLM never draws an arrow."""
    if spec.kind == "module" and spec.module is not None:
        rows = conn.execute("""
            SELECT src_module, dst_module, weight FROM module_edges
            WHERE snapshot_id=%(s)s AND kind IN ('CALLS','IMPORTS')
              AND (src_module=%(m)s OR dst_module=%(m)s)
            ORDER BY weight DESC LIMIT 12""",
            {"s": snap, "m": spec.module}).fetchall()
    elif spec.kind in ("overview", "architecture"):
        rows = conn.execute("""
            SELECT src_module, dst_module, weight FROM module_edges
            WHERE snapshot_id=%s AND kind IN ('CALLS','IMPORTS')
              AND src_module !~ '(^|/)tests?(/|$)'
              AND dst_module !~ '(^|/)tests?(/|$)'
            ORDER BY weight DESC LIMIT 14""", (snap,)).fetchall()
    else:
        return None
    if not rows:
        return None
    ident: dict[str, str] = {}

    def nid(m: str) -> str:
        if m not in ident:
            ident[m] = f"m{len(ident)}"
        return ident[m]

    lines = ["```mermaid", "flowchart LR"]
    seen = set()
    for r in rows:
        s, d = r["src_module"] or "(root)", r["dst_module"] or "(root)"
        if (s, d) in seen:
            continue
        seen.add((s, d))
        lines.append(f'  {nid(s)}["{s}"] --> {nid(d)}["{d}"]')
    lines.append("```")
    return "\n".join(lines)


# ------------------------------------------------------------- synthesize

SHARED_RULES = """
HARD RULES:
- Base every claim on the provided FACTS and SOURCE. Do not invent files,
  functions, or behavior.
- Cite locations inline as `path:line` (exactly as they appear in FACTS).
  Every section must carry at least one citation when facts include
  locations.
- Do NOT draw diagrams or write mermaid; a verified diagram is inserted
  separately.
- Never mention the internal FACTS field names (totals, languages,
  module_dependencies, public_symbols, files, uses, used_by, entry_points,
  hotspots) in your prose: they are data plumbing, not part of the
  codebase. Cite only real file paths and symbols.
- Never use em-dashes. Use '-' or ':' instead.
- No preamble, no meta-commentary. Output markdown body only (no H1;
  the title is added by the system)."""

PERSONAS: dict[str, dict] = {
    "onboarding": {
        "label": "Developer",
        "voice": "You write onboarding documentation for engineers new to "
            "the codebase '{repo}'. Audience: a capable engineer on day "
            "one. Plain, direct language; short sections with ## headings; "
            "explain what things are FOR, not just what they are.",
        "pages": ["overview", "architecture", "modules", "entries", "hot"],
        "briefs": {
            "overview": "Write the 'what is this codebase' page: purpose "
                "(infer carefully from names/source), size and languages, "
                "how the pieces fit at a glance, and what to read next.",
            "architecture": "Explain the module structure and the main "
                "dependency directions: which parts are foundations, which "
                "are consumers.",
            "module": "Document this module for a newcomer: its job, its "
                "key public symbols (cite each), what it uses and what "
                "uses it.",
            "entries": "Explain where execution enters this codebase (HTTP "
                "routes, CLI commands, main guards) and what each entry "
                "area is for.",
            "hot": "Describe the most-called functions: what each does and "
                "why so much depends on it. These are the functions to "
                "read first.",
        },
    },
    "sre": {
        "label": "SRE / On-call",
        "voice": "You write operational documentation for SREs and on-call "
            "engineers responsible for '{repo}' in production. Audience: "
            "the person paged at 3am who did not write this code. Focus on "
            "where execution enters, what is most load-bearing, and how "
            "failures would propagate. Be direct and practical; never "
            "invent runbooks or infrastructure that the code does not show.",
        "pages": ["overview", "entries", "hot", "architecture"],
        "briefs": {
            "overview": "Operational overview: what this system does, its "
                "major moving parts, and which parts are most load-bearing "
                "(judge by dependency weight, not guesswork).",
            "entries": "Where execution enters this codebase: routes, CLI "
                "commands, main scripts. During an incident these are the "
                "first places to look; say what each entry area serves.",
            "hot": "The most-depended-on functions: a failure or "
                "regression here has the widest blast radius. For each, "
                "say what it does and what would be affected if it "
                "misbehaved.",
            "architecture": "Failure domains: which parts depend on which, "
                "so an on-call engineer can reason about how an outage in "
                "one area propagates to others.",
        },
    },
    "tester": {
        "label": "QA / Tester",
        "voice": "You write test-planning documentation for QA engineers "
            "approaching '{repo}'. Audience: a tester deciding where to "
            "spend limited testing time. Focus on risk: what has the most "
            "dependents (regression risk), what surfaces users hit (entry "
            "points), and where integration seams are. You have NO "
            "coverage data - never claim something is tested or untested; "
            "reason only from structure.",
        "pages": ["overview", "hot", "entries", "architecture"],
        "briefs": {
            "overview": "Testing overview: size and shape of the codebase, "
                "and where structural risk concentrates (judge by "
                "dependency weight).",
            "hot": "Highest regression-risk functions: the most-called "
                "code - a defect here surfaces everywhere. Prioritize "
                "these paths for regression tests; say what behavior to "
                "pin for each.",
            "entries": "The user-facing surfaces (routes, CLI commands, "
                "main scripts) to exercise end-to-end; say what flows "
                "through each.",
            "architecture": "Dependency structure as integration-test "
                "seams: which module boundaries carry the most traffic "
                "and deserve contract/integration tests.",
        },
    },
}


def _no_em_dashes(md: str) -> str:
    """House style bans em/en-dashes; enforce it even when the model
    ignores the prompt rule."""
    return md.replace(" — ", " - ").replace("—", " - ").replace("–", "-")


def synthesize_page(provider, conn, snap: int, spec: PageSpec, repo: str,
                    source_root: Path | None, stats: GenStats,
                    persona: str) -> str:
    facts = _facts(conn, snap, spec, repo)
    persona_cfg = PERSONAS[persona]
    prompt = (
        f"PAGE: {spec.title}\nBRIEF: {persona_cfg['briefs'][spec.kind]}\n\n"
        f"FACTS (from the verified code graph):\n"
        f"{json.dumps(facts, default=str)[:9000]}\n\n"
        f"SOURCE EXCERPTS:\n{_source_slices(source_root, facts)}"
    )
    messages = [
        {"role": "system",
         "content": persona_cfg["voice"].format(repo=repo) + "\n"
         + SHARED_RULES},
        {"role": "user", "content": prompt},
    ]
    resp = provider.chat(messages, [])
    stats.prompt_tokens += resp.usage.get("prompt_tokens", 0)
    stats.completion_tokens += resp.usage.get("completion_tokens", 0)
    body = _no_em_dashes(resp.text.strip())

    ok, bad = _check_citations(conn, snap, body)
    if bad:
        stats.retries += 1
        fix = provider.chat(messages + [
            {"role": "assistant", "content": body},
            {"role": "user", "content":
             "These citations do not exist in the snapshot and MUST be "
             f"corrected or removed (do not invent replacements): {bad}. "
             "Return the full corrected markdown body."}], [])
        stats.prompt_tokens += fix.usage.get("prompt_tokens", 0)
        stats.completion_tokens += fix.usage.get("completion_tokens", 0)
        body = _no_em_dashes(fix.text.strip()) or body
        ok, bad = _check_citations(conn, snap, body)
        if bad:
            body = _strip_bad_citations(body, bad)
            stats.failed_cites_removed += len(bad)

    diagram = _diagram(conn, snap, spec)
    if diagram:
        body += ("\n\n## How it connects\n\n*(diagram compiled from the "
                 "dependency graph, not drawn by the model)*\n\n" + diagram)
    return body


# ----------------------------------------------------------------- verify

def _check_citations(conn, snap: int, md: str):
    cites = set(CITE_RE.findall(md))
    if not cites:
        return [], []
    files = {r["path"]: r["loc"] for r in conn.execute(
        "SELECT path, loc FROM files WHERE snapshot_id=%s", (snap,))}
    ok, bad = [], []
    for path, line in cites:
        if path in files and int(line) <= max(files[path], 1):
            ok.append(f"{path}:{line}")
        else:
            bad.append(f"{path}:{line}")
    return ok, bad


def _strip_bad_citations(md: str, bad: list[str]) -> str:
    for cite in bad:
        md = md.replace(f"`{cite}`", "").replace(cite, "")
    return md


def _doc_refs(conn, snap: int, page_id: int, md: str) -> None:
    cites = set(CITE_RE.findall(md))
    for path, line in cites:
        row = conn.execute("""
            SELECT s.stable_symbol FROM symbols s JOIN files f ON f.id=s.file_id
            WHERE s.snapshot_id=%(s)s AND f.path=%(p)s
              AND s.start_line IS NOT NULL AND s.end_line IS NOT NULL
              AND s.start_line <= %(l)s AND s.end_line >= %(l)s
            ORDER BY s.end_line - s.start_line LIMIT 1
            """, {"s": snap, "p": path, "l": int(line) - 1}).fetchone()
        if row:
            conn.execute(
                "INSERT INTO doc_refs (page_id, stable_symbol, kind)"
                " VALUES (%s, %s, 'cites')", (page_id, row["stable_symbol"]))


# ------------------------------------------------------------------ embed

def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    try:
        res = httpx.post(f"{settings.ollama_url}/api/embed", json={
            "model": "nomic-embed-text", "input": texts}, timeout=120)
        res.raise_for_status()
        return res.json()["embeddings"]
    except Exception:  # noqa: BLE001 — embeddings are optional
        return None


def _chunk(md: str, source: str) -> list[tuple[str, str]]:
    parts = re.split(r"\n(?=## )", md)
    return [(source, p.strip()[:1500]) for p in parts if p.strip()]


def embed_pages(conn, snap: int, persona: str, stats: GenStats) -> None:
    rows = conn.execute(
        "SELECT slug, content_md FROM doc_pages"
        " WHERE snapshot_id=%s AND persona=%s", (snap, persona)).fetchall()
    chunks: list[tuple[str, str]] = []
    for r in rows:
        chunks += _chunk(r["content_md"], f"doc:{persona}/{r['slug']}")
    if not chunks:
        return
    embs = _embed_texts([c[1] for c in chunks])
    if embs is None:
        stats.notes.append("embeddings unavailable (ollama/nomic-embed-text"
                           " not reachable) - doc search disabled")
        return
    conn.execute("DELETE FROM doc_chunks WHERE snapshot_id=%s"
                 " AND source LIKE %s", (snap, f"doc:{persona}/%"))
    with conn.cursor().copy(
            "COPY doc_chunks (snapshot_id, source, content, embedding)"
            " FROM STDIN") as cp:
        for (source, content), emb in zip(chunks, embs):
            cp.write_row((snap, source, content, json.dumps(emb)))


# -------------------------------------------------------------- top level

def generate_docs(snapshot_id: int, persona: str,
                  source_root: Path | None) -> dict:
    provider = get_provider()
    with psycopg.connect(settings.db_dsn,
                         row_factory=psycopg.rows.dict_row) as conn:
        snap_row = conn.execute(
            "SELECT s.id, s.commit_sha, r.name AS repo FROM snapshots s"
            " JOIN repos r ON r.id=s.repo_id WHERE s.id=%s"
            " AND s.status='ready'", (snapshot_id,)).fetchone()
        if snap_row is None:
            raise RuntimeError(f"snapshot {snapshot_id} not ready")
        repo = snap_row["repo"]
        stats = GenStats()

        conn.execute("DELETE FROM doc_pages WHERE snapshot_id=%s"
                     " AND persona=%s", (snapshot_id, persona))
        if persona not in PERSONAS:
            raise RuntimeError(f"unknown persona {persona!r}; "
                               f"available: {sorted(PERSONAS)}")
        for spec in outline(conn, snapshot_id, persona):
            body = synthesize_page(provider, conn, snapshot_id, spec, repo,
                                   source_root, stats, persona)
            _, bad = _check_citations(conn, snapshot_id, body)
            status = "verified" if not bad else "draft"
            page = conn.execute(
                "INSERT INTO doc_pages (snapshot_id, persona, slug, title,"
                " position, parent_slug, content_md, status, meta)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (snapshot_id, persona, spec.slug, spec.title, spec.position,
                 spec.parent_slug, body, status,
                 json.dumps({"kind": spec.kind}))).fetchone()
            _doc_refs(conn, snapshot_id, page["id"], body)
            stats.pages += 1
            stats.verified += (status == "verified")
            print(f"docs: {spec.slug} [{status}]")
        embed_pages(conn, snapshot_id, persona, stats)
        conn.commit()
        return {
            "pages": stats.pages, "verified": stats.verified,
            "retries": stats.retries,
            "bad_cites_removed": stats.failed_cites_removed,
            "prompt_tokens": stats.prompt_tokens,
            "completion_tokens": stats.completion_tokens,
            "notes": stats.notes,
        }
