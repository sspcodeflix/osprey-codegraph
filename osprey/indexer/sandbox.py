"""Execution backends for indexer stages (ARCHITECTURE.md §11.1).

LocalExecutor runs tools on the host — for trusted repos and dev loops.
ContainerExecutor runs each stage in a rootless-friendly container:
  - the index stage always gets --network=none
  - the deps stage (proxied mode only) gets network egress
  - repo mounted read-only for indexing, rw only for the deps stage
  - cpu/memory caps, wall-clock timeout with hard kill
"""

from __future__ import annotations

import shlex
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from osprey.config import settings


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str


class LocalExecutor:
    """Direct subprocess execution. No isolation — trusted input only."""

    def run(self, cmd: str, cwd: Path, timeout_s: int,
            args: list[str] | None = None, *, network: bool = True,
            writable_repo: bool = False,
            mounts: dict[str, str] | None = None,
            env: dict[str, str] | None = None) -> ExecResult:
        import os
        argv = shlex.split(cmd) + (args or [])
        run_env = {**os.environ, **env} if env else None
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout_s, env=run_env)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)


class ContainerExecutor:
    """Runs stages in `settings.indexer_image` via docker/podman.

    The repo is mounted at /src (ro unless writable_repo); extra mounts map
    host paths to container paths read-write (used for the .scip output dir).
    """

    def __init__(self, runtime: str | None = None):
        self.runtime = runtime or settings.container_runtime

    def run(self, cmd: str, cwd: Path, timeout_s: int,
            args: list[str] | None = None, *, network: bool = False,
            writable_repo: bool = False,
            mounts: dict[str, str] | None = None,
            env: dict[str, str] | None = None) -> ExecResult:
        name = f"osprey-idx-{uuid.uuid4().hex[:12]}"
        repo_mode = "rw" if writable_repo else "ro"
        argv = [
            self.runtime, "run", "--rm", "--name", name,
            "--network", "bridge" if network else "none",
            "--memory", settings.container_memory,
            "--cpus", str(settings.container_cpus),
            # in-memory /tmp *inside the container*, not a host temp path:
            # gives the sandbox a size-capped writable scratch area
            "--tmpfs", "/tmp:rw,size=1g",  # nosec B108
            "-e", "HOME=/tmp",
            "-e", "npm_config_cache=/tmp/.npm",
            "-v", f"{cwd.resolve()}:/src:{repo_mode}",
            "-w", "/src",
        ]
        for key, val in (env or {}).items():
            argv += ["-e", f"{key}={val}"]
        for host, container in (mounts or {}).items():
            argv += ["-v", f"{Path(host).resolve()}:{container}:rw"]
        argv.append(settings.indexer_image)
        argv += shlex.split(cmd) + (args or [])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout_s)
        except subprocess.TimeoutExpired:
            subprocess.run([self.runtime, "rm", "-f", name],
                           capture_output=True)
            return ExecResult(124, "", f"timeout after {timeout_s}s")
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)


def get_executor():
    if settings.executor == "container":
        return ContainerExecutor()
    return LocalExecutor()
