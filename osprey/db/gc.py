"""Retention/GC (ARCHITECTURE.md §12): keep the last N ready snapshots per
repo, sweep failed/stuck snapshots and old finished jobs. Cascade deletes are
O(rows) thanks to the FK indexes (the M0 lesson)."""

from __future__ import annotations

import psycopg

from osprey.config import settings

SWEEP_SQL = """
SELECT s.id, r.name, s.commit_sha, s.status,
       now() - s.created_at AS age
FROM snapshots s JOIN repos r ON r.id = s.repo_id
WHERE
  -- ready snapshots beyond the per-repo retention window
  (s.status = 'ready' AND s.id IN (
     SELECT id FROM (
       SELECT id, row_number() OVER (PARTITION BY repo_id
                                     ORDER BY id DESC) AS rn
       FROM snapshots WHERE status = 'ready'
     ) ranked WHERE rn > %(keep)s))
  -- failed snapshots older than a day
  OR (s.status = 'failed' AND s.created_at < now() - interval '1 day')
  -- snapshots stuck in queued/indexing for over a day (crashed worker)
  OR (s.status IN ('queued', 'indexing')
      AND s.created_at < now() - interval '1 day')
ORDER BY s.id
"""


def gc(keep: int | None = None, dry_run: bool = False) -> list[tuple]:
    keep = keep if keep is not None else settings.retention_keep
    with psycopg.connect(settings.db_dsn) as conn:
        victims = conn.execute(SWEEP_SQL, {"keep": keep}).fetchall()
        if not dry_run and victims:
            ids = [v[0] for v in victims]
            # batched cascade delete; one snapshot per statement keeps each
            # transaction's lock footprint small
            for sid in ids:
                conn.execute("DELETE FROM snapshots WHERE id = %s", (sid,))
            conn.execute(
                "DELETE FROM jobs WHERE status IN ('done','failed')"
                " AND created_at < now() - interval '30 days'")
        return victims
