"""Fetch stage: materialize (git_url, ref) as a shallow working tree."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: "
                           f"{proc.stderr.strip()[-500:]}")
    return proc.stdout.strip()


def fetch_repo(git_url: str, ref: str, workdir: Path) -> Path:
    """Shallow-fetch `ref` (branch, tag, or sha) of `git_url` into workdir.

    Uses init+fetch rather than clone so an arbitrary sha works the same as
    a branch name on servers that allow reachable-sha fetches.
    """
    dest = workdir / "repo"
    dest.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], dest)
    _git(["remote", "add", "origin", git_url], dest)
    _git(["fetch", "-q", "--depth", "1", "origin", ref], dest)
    _git(["checkout", "-q", "FETCH_HEAD"], dest)
    return dest
