"""osprey CLI: db-init | index | enqueue | worker | api"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="osprey")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("db-init", help="apply schema to the configured database")

    p_index = sub.add_parser("index", help="index a repository now")
    p_index.add_argument("path", type=Path)
    p_index.add_argument("--name", default=None)
    p_index.add_argument("--deps", choices=["none", "proxied"],
                         default="none")

    p_add = sub.add_parser("repo-add", help="register a repo (with git URL)")
    p_add.add_argument("name")
    p_add.add_argument("--git-url", default="")

    p_enq = sub.add_parser("enqueue", help="queue an indexing job")
    p_enq.add_argument("repo")
    p_enq.add_argument("--ref", default="HEAD")

    p_worker = sub.add_parser("worker", help="run the indexer worker")
    p_worker.add_argument("--repo-root", type=Path, default=Path.cwd())
    p_worker.add_argument("--once", action="store_true")

    p_api = sub.add_parser("api", help="serve the query API")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8800)

    p_gc = sub.add_parser("gc", help="apply the snapshot retention policy")
    p_gc.add_argument("--keep", type=int, default=None)
    p_gc.add_argument("--dry-run", action="store_true")

    p_req = sub.add_parser("requests",
                           help="list / resolve demo repo requests")
    p_req.add_argument("--all", action="store_true",
                       help="include resolved requests")
    p_req.add_argument("--mark", type=int, metavar="ID",
                       help="request id to resolve")
    p_req.add_argument("--status", choices=["indexed", "declined"],
                       help="resolution for --mark")

    args = ap.parse_args(argv)

    if args.cmd == "db-init":
        from osprey.db import init_db
        init_db()
        print("schema applied")
    elif args.cmd == "index":
        from osprey.indexer.pipeline import index_repository
        snapshot_id = index_repository(args.path, args.name,
                                       deps_mode=args.deps)
        print(f"snapshot {snapshot_id} ready")
    elif args.cmd == "repo-add":
        import psycopg

        from osprey.config import settings
        with psycopg.connect(settings.db_dsn) as conn:
            conn.execute(
                "INSERT INTO repos (org_id, name, git_url)"
                " SELECT id, %s, %s FROM orgs WHERE name='default'"
                " ON CONFLICT (org_id, name)"
                " DO UPDATE SET git_url = EXCLUDED.git_url",
                (args.name, args.git_url))
        print(f"repo {args.name} registered")
    elif args.cmd == "enqueue":
        from osprey.indexer.worker import enqueue
        print(f"job {enqueue(args.repo, args.ref)} queued")
    elif args.cmd == "worker":
        from osprey.indexer.worker import run_worker
        run_worker(args.repo_root, once=args.once)
    elif args.cmd == "api":
        import uvicorn
        uvicorn.run("osprey.api.main:app", host=args.host, port=args.port)
    elif args.cmd == "gc":
        from osprey.db.gc import gc
        victims = gc(keep=args.keep, dry_run=args.dry_run)
        verb = "would delete" if args.dry_run else "deleted"
        for sid, repo, sha, status, age in victims:
            print(f"{verb} snapshot {sid} ({repo}@{sha[:8]}, {status}, "
                  f"age {age})")
        print(f"{verb}: {len(victims)} snapshot(s)")
    elif args.cmd == "requests":
        from osprey.db import connect
        with connect(autocommit=True) as conn:
            if args.mark is not None:
                if not args.status:
                    ap.error("--mark requires --status")
                n = conn.execute(
                    "UPDATE repo_requests SET status=%s WHERE id=%s",
                    (args.status, args.mark)).rowcount
                print(f"request {args.mark}: "
                      + (args.status if n else "not found"))
            else:
                where = "" if args.all else " WHERE status='pending'"
                rows = conn.execute(
                    "SELECT id, status, git_url, ref, contact, note, "
                    "created_at FROM repo_requests" + where
                    + " ORDER BY id").fetchall()
                for r in rows:
                    rid, status, url, ref, contact, note, at = r
                    print(f"#{rid} [{status}] {url}@{ref}  <{contact}>  "
                          f"{at:%Y-%m-%d %H:%M}"
                          + (f"\n    note: {note}" if note else ""))
                print(f"{len(rows)} request(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
