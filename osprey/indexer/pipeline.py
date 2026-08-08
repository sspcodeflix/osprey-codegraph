"""Indexing pipeline (ARCHITECTURE.md §5): detect -> index -> normalize ->
load -> publish. Rows load against a snapshot in status 'indexing'; publish
is the status flip to 'ready'. Read paths only see 'ready' snapshots, so
partial data is never visible.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg

from osprey.config import settings
from osprey.indexer.normalize import GraphData, normalize
from osprey.indexer.sandbox import ContainerExecutor, get_executor
from osprey.scip.reader import read_index, stable_symbol

_VENDOR_DIRS = {"node_modules", ".git", "vendor", "dist", "build",
                ".venv", "venv"}


def _has_python(repo: Path) -> bool:
    if any((repo / m).exists() for m in
           ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        return True
    # stray vendored .py files (node-gyp etc.) must not trigger scip-python
    return any(_VENDOR_DIRS.isdisjoint(p.relative_to(repo).parts[:-1])
               for p in repo.rglob("*.py"))


INDEXERS = {
    "python": _has_python,
    "typescript": lambda repo: (repo / "package.json").exists()
    or (repo / "tsconfig.json").exists(),
}


def git_sha(repo: Path, ref: str = "HEAD") -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def _python_requirements(repo: Path) -> list[str]:
    reqs: list[str] = []
    req_file = repo / "requirements.txt"
    if req_file.exists():
        reqs += [line.strip() for line in req_file.read_text().splitlines()
                 if line.strip() and not line.strip().startswith(("#", "-"))]
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        import tomllib
        try:
            data = tomllib.loads(pyproject.read_text())
            reqs += data.get("project", {}).get("dependencies", [])
        except tomllib.TOMLDecodeError:
            pass
    return reqs


def install_deps(repo: Path, executor, workdir: Path,
                 in_container: bool) -> None:
    """Deps stage (`proxied` mode). Package scripts never run: npm gets
    --ignore-scripts, pip gets --only-binary :all: (wheels only — no sdist
    builds, so no setup.py execution). Python installs are per-requirement
    best-effort; misses degrade that package to external-stub resolution."""
    if (repo / "package.json").exists():
        sub = "ci" if (repo / "package-lock.json").exists() else "install"
        args = [sub, "--ignore-scripts", "--no-audit", "--no-fund"]
        if settings.npm_registry:
            args += ["--registry", settings.npm_registry]
        res = executor.run("npm", repo, settings.index_timeout_s, args,
                           network=True, writable_repo=True)
        if res.returncode != 0:
            raise RuntimeError(f"npm {sub} failed: {res.stderr[-2000:]}")

    reqs = _python_requirements(repo)
    if reqs:
        out = "/out" if in_container else str(workdir)
        (workdir / "reqs.txt").write_text("\n".join(reqs) + "\n")
        index_flag = (f" --index-url {settings.pip_index_url}"
                      if settings.pip_index_url else "")
        (workdir / "deps.sh").write_text(f"""\
set -u
python3 -m venv "{out}/venv"
"{out}/venv/bin/pip" install --quiet --upgrade pip
while IFS= read -r req; do
  [ -z "$req" ] && continue
  "{out}/venv/bin/pip" install --quiet --only-binary :all:{index_flag} \
"$req" || echo "SKIP $req"
done < "{out}/reqs.txt"
""")
        mounts = {str(workdir): "/out"} if in_container else None
        res = executor.run("sh", repo, settings.index_timeout_s,
                           [f"{out}/deps.sh"], network=True, mounts=mounts)
        if res.returncode != 0:
            raise RuntimeError(f"python deps failed: {res.stderr[-2000:]}")
        skipped = [line for line in res.stdout.splitlines()
                   if line.startswith("SKIP ")]
        if skipped:
            print(f"deps: {len(skipped)} requirement(s) skipped "
                  f"(no wheel): {', '.join(s[5:] for s in skipped[:5])}")


def run_indexers(repo: Path, workdir: Path, deps_mode: str = "none",
                 force_container: bool = False) -> tuple[list[Path], dict]:
    executor = ContainerExecutor() if force_container else get_executor()
    in_container = isinstance(executor, ContainerExecutor)
    if deps_mode == "proxied":
        install_deps(repo, executor, workdir, in_container)

    # in the container the output dir is a dedicated rw mount at /out;
    # locally it is just the workdir path
    out_dir = "/out" if in_container else str(workdir)
    mounts = {str(workdir): "/out"} if in_container else None
    py_cmd = (settings.container_scip_python_cmd if in_container
              else settings.scip_python_cmd)
    ts_cmd = (settings.container_scip_typescript_cmd if in_container
              else settings.scip_typescript_cmd)
    # a proxied-mode venv (created by the deps stage) must be first on PATH
    # so scip-python resolves against the installed dependencies
    py_env = None
    if deps_mode == "proxied" and (workdir / "venv").exists():
        base_path = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                     "/sbin:/bin") if in_container \
            else os.environ.get("PATH", "")
        py_env = {"PATH": f"{out_dir}/venv/bin:{base_path}"}

    outputs: list[Path] = []
    versions: dict[str, str] = {}
    if INDEXERS["python"](repo):
        res = executor.run(py_cmd, repo, settings.index_timeout_s,
                           ["index", ".", "--project-name", repo.name,
                            "--output", f"{out_dir}/python.scip"],
                           network=False, mounts=mounts, env=py_env)
        if res.returncode != 0:
            raise RuntimeError(f"scip-python failed: {res.stderr[-2000:]}")
        outputs.append(workdir / "python.scip")
        versions["scip-python"] = "0.6.6"
    if INDEXERS["typescript"](repo):
        res = executor.run(ts_cmd, repo, settings.index_timeout_s,
                           ["index", "--output", f"{out_dir}/typescript.scip"],
                           network=False, mounts=mounts)
        if res.returncode != 0:
            raise RuntimeError(f"scip-typescript failed: {res.stderr[-2000:]}")
        outputs.append(workdir / "typescript.scip")
        versions["scip-typescript"] = "0.4.0"
    if not outputs:
        raise RuntimeError("no indexer matched this repository")
    versions["sandbox"] = "container" if in_container else "local"
    return outputs, versions


def module_of(path: str) -> str:
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


def load_graph(conn: psycopg.Connection, snapshot_id: int,
               g: GraphData) -> dict:
    cur = conn.cursor()
    with cur.copy("COPY files (snapshot_id, path, language, loc)"
                  " FROM STDIN") as cp:
        for path, (lang, loc) in g.files.items():
            cp.write_row((snapshot_id, path, lang, loc))
    cur.execute("SELECT path, id FROM files WHERE snapshot_id = %s",
                (snapshot_id,))
    file_ids = dict(cur.fetchall())

    with cur.copy("COPY symbols (snapshot_id, scip_symbol, stable_symbol,"
                  " kind, name, file_id, start_line, end_line, is_external,"
                  " entry_kind) FROM STDIN") as cp:
        for sym, row in g.symbols.items():
            cp.write_row((snapshot_id, sym, stable_symbol(sym), row.kind,
                          row.name, file_ids.get(row.path), row.line,
                          row.end, row.external, row.entry_kind))
    cur.execute("SELECT scip_symbol, id FROM symbols WHERE snapshot_id = %s",
                (snapshot_id,))
    sym_ids = dict(cur.fetchall())

    with cur.copy("COPY occurrences (snapshot_id, symbol_id, file_id,"
                  " start_line, start_char, role, enclosing_symbol_id)"
                  " FROM STDIN") as cp:
        for sym, path, line, char, role, encl in g.occurrences:
            cp.write_row((snapshot_id, sym_ids[sym], file_ids[path], line,
                          char, role, sym_ids.get(encl)))

    with cur.copy("COPY edges (snapshot_id, src_id, dst_id, kind, weight,"
                  " first_file_id, first_line) FROM STDIN") as cp:
        for (src, dst, kind), (w, path, line) in g.edges.items():
            cp.write_row((snapshot_id, sym_ids[src], sym_ids[dst], kind, w,
                          file_ids.get(path), line))

    # module_edges: aggregate symbol edges by source-file directory
    cur.execute("""
        INSERT INTO module_edges (snapshot_id, src_module, dst_module, kind,
                                  weight)
        SELECT e.snapshot_id,
               COALESCE(regexp_replace(fs.path, '/[^/]+$', ''), ''),
               COALESCE(regexp_replace(fd.path, '/[^/]+$', ''), ''),
               e.kind, sum(e.weight)
        FROM edges e
        JOIN symbols ss ON ss.id = e.src_id JOIN files fs ON fs.id = ss.file_id
        JOIN symbols sd ON sd.id = e.dst_id JOIN files fd ON fd.id = sd.file_id
        WHERE e.snapshot_id = %s
          AND regexp_replace(fs.path, '/[^/]+$', '')
              IS DISTINCT FROM regexp_replace(fd.path, '/[^/]+$', '')
        GROUP BY 1, 2, 3, 4
        """, (snapshot_id,))

    # fresh statistics immediately: without this, the first query against a
    # just-loaded snapshot races autoanalyze and can hit the statement
    # timeout on a planner flying blind (observed on mlflow: >5s -> 80ms)
    cur.execute("ANALYZE files, symbols, occurrences, edges, module_edges")

    return {"files": len(g.files), "symbols": len(g.symbols),
            "occurrences": len(g.occurrences), "edges": len(g.edges)}


def index_repository(repo_path: Path, repo_name: str | None = None,
                     org: str = "default", deps_mode: str = "none",
                     force_container: bool = False) -> int:
    """Run the full pipeline; returns the published snapshot id."""
    t0 = time.time()
    repo_path = repo_path.resolve()
    repo_name = repo_name or repo_path.name
    sha = git_sha(repo_path)

    with psycopg.connect(settings.db_dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM orgs WHERE name = %s", (org,))
        org_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO repos (org_id, name) VALUES (%s, %s)"
            " ON CONFLICT (org_id, name) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id", (org_id, repo_name))
        repo_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO snapshots (repo_id, commit_sha, status, deps_mode)"
            " VALUES (%s, %s, 'indexing', %s) RETURNING id",
            (repo_id, sha, deps_mode))
        snapshot_id = cur.fetchone()[0]
        conn.commit()

        try:
            with tempfile.TemporaryDirectory() as td:
                outputs, versions = run_indexers(repo_path, Path(td),
                                                 deps_mode, force_container)
                indexes = [read_index(p) for p in outputs]
            g = normalize(repo_path, indexes)
            stats = load_graph(conn, snapshot_id, g)
            stats["seconds"] = round(time.time() - t0, 1)
            cur.execute(
                "UPDATE snapshots SET status='ready', ready_at=now(),"
                " indexer_versions=%s, stats=%s WHERE id=%s",
                (json.dumps(versions), json.dumps(stats), snapshot_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            cur.execute("UPDATE snapshots SET status='failed', error=%s"
                        " WHERE id=%s", (str(exc)[:4000], snapshot_id))
            conn.commit()
            raise
    return snapshot_id
