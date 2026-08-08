"""Queue-driven indexer worker: one Postgres-backed queue, SKIP LOCKED."""

from __future__ import annotations

import socket
import tempfile
import time
from pathlib import Path

import psycopg

from osprey.config import settings
from osprey.indexer.fetch import fetch_repo
from osprey.indexer.pipeline import index_repository


def enqueue(repo_name: str, ref: str = "HEAD", org: str = "default") -> int:
    with psycopg.connect(settings.db_dsn) as conn:
        row = conn.execute(
            "INSERT INTO jobs (repo_id, ref)"
            " SELECT r.id, %s FROM repos r JOIN orgs o ON o.id = r.org_id"
            " WHERE o.name = %s AND r.name = %s RETURNING id",
            (ref, org, repo_name)).fetchone()
        if row is None:
            raise ValueError(f"unknown repo {org}/{repo_name}")
        return row[0]


def claim_job(conn: psycopg.Connection) -> tuple | None:
    return conn.execute(
        "UPDATE jobs SET status='running', attempts=attempts+1,"
        " locked_by=%s, locked_at=now()"
        " WHERE id = (SELECT id FROM jobs WHERE status='queued'"
        "             ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)"
        " RETURNING id, ref,"
        "  (SELECT git_url FROM repos WHERE id=repo_id),"
        "  (SELECT name FROM repos WHERE id=repo_id),"
        "  kind, payload",
        (socket.gethostname(),)).fetchone()


def _source_root_for_snapshot(repo_root: Path, name: str, git_url: str,
                              snapshot_id: int, tmp: Path) -> Path | None:
    """Materialize the snapshot's source for doc synthesis. Local checkouts
    are used as-is; remote repos are re-fetched at the snapshot commit."""
    if not git_url:
        local = repo_root / name
        return local if local.exists() else None
    with psycopg.connect(settings.db_dsn) as conn:
        row = conn.execute("SELECT commit_sha FROM snapshots WHERE id=%s",
                           (snapshot_id,)).fetchone()
    if row is None:
        return None
    try:
        return fetch_repo(git_url, row[0], tmp)
    except RuntimeError:
        return None


def _run_docs_job(repo_root: Path, name: str, git_url: str,
                  payload: dict, job_id: int) -> str:
    import json

    from osprey.docs.pipeline import generate_docs
    snapshot_id = int(payload["snapshot_id"])
    persona = payload.get("persona", "onboarding")
    with tempfile.TemporaryDirectory(prefix="osprey-docs-") as td:
        source_root = _source_root_for_snapshot(
            repo_root, name, git_url, snapshot_id, Path(td))
        stats = generate_docs(snapshot_id, persona, source_root)
    # persist generation economics on the job — D0's exit metric
    with psycopg.connect(settings.db_dsn) as conn:
        conn.execute(
            "UPDATE jobs SET payload = payload || %s::jsonb WHERE id=%s",
            (json.dumps({"stats": stats}), job_id))
    return f"docs generated: {stats}"


def _dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*")
               if f.is_file()) / 1e6


def _run_job(repo_root: Path, name: str, git_url: str, ref: str) -> int:
    if git_url:
        # remote repos are untrusted input: size-capped, always container-
        # sandboxed, never installing dependencies (§11)
        with tempfile.TemporaryDirectory(prefix="osprey-fetch-") as td:
            repo = fetch_repo(git_url, ref, Path(td))
            size = _dir_size_mb(repo)
            if size > settings.max_repo_mb:
                raise RuntimeError(
                    f"repository is {size:.0f} MB, over the "
                    f"{settings.max_repo_mb} MB limit for pasted URLs")
            return index_repository(
                repo, name, deps_mode="none",
                force_container=settings.remote_force_container)
    return index_repository(repo_root / name, name)


def run_worker(repo_root: Path, once: bool = False) -> None:
    """Poll for jobs. Repos with a git_url are shallow-fetched per job;
    repos without one are dev checkouts under repo_root, named after the
    repo."""
    while True:
        with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
            job = claim_job(conn)
            if job is None:
                if once:
                    return
                time.sleep(settings.worker_poll_s)
                continue
            job_id, ref, git_url, name, kind, payload = job
            try:
                if kind == "docs":
                    outcome = _run_docs_job(repo_root, name, git_url or "",
                                            payload or {}, job_id)
                else:
                    snapshot_id = _run_job(repo_root, name, git_url or "",
                                           ref)
                    # remember which tag/branch produced this snapshot —
                    # commit_sha alone is meaningless to most readers
                    conn.execute(
                        "UPDATE snapshots SET stats = stats || %s"
                        " WHERE id=%s",
                        (json.dumps({"ref": ref}), snapshot_id))
                    outcome = f"snapshot {snapshot_id} ready"
                conn.execute("UPDATE jobs SET status='done' WHERE id=%s",
                             (job_id,))
                print(f"job {job_id}: {outcome}")
            except Exception as exc:  # noqa: BLE001 — job isolation boundary
                conn.execute(
                    "UPDATE jobs SET status='failed', error=%s WHERE id=%s",
                    (str(exc)[:4000], job_id))
                print(f"job {job_id}: FAILED: {exc}")
        if once:
            return
