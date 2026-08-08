"""osprey-gate: enforce osprey.rules.yaml against snapshots, in CI.

    osprey-gate check --repo hono --base <sha|id|previous> [--head latest]
                      [--rules osprey.rules.yaml] [--api http://...]
                      [--format text|markdown] [--fail-closed]

Exit codes: 0 pass (or fail-open on API unreachable), 1 error-severity
violation, 2 usage/API error in --fail-closed mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from osprey.gate.engine import evaluate, render_markdown, render_text
from osprey.gate.rules import parse_rules


class Api:
    def __init__(self, base_url: str, token: str):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url, headers=headers,
                                   timeout=30)

    def get(self, path: str, **params):
        res = self.client.get(path, params=params or None)
        res.raise_for_status()
        return res.json()


def resolve_snapshot(snaps: list[dict], ref: str) -> dict:
    ready = [s for s in snaps if s["status"] == "ready"]
    if ref == "latest":
        if not ready:
            raise SystemExit("no ready snapshots")
        return ready[0]
    if ref == "previous":
        if len(ready) < 2:
            raise SystemExit("need two ready snapshots for 'previous'")
        return ready[1]
    if ref.isdigit():
        for s in ready:
            if s["id"] == int(ref):
                return s
    for s in ready:
        if s["commit_sha"].startswith(ref):
            return s
    raise SystemExit(f"no ready snapshot matches {ref!r}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="osprey-gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="evaluate rules against a snapshot pair")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="previous",
                   help="snapshot id, commit sha prefix, or 'previous'")
    p.add_argument("--head", default="latest")
    p.add_argument("--rules", type=Path, default=Path("osprey.rules.yaml"))
    p.add_argument("--api", default="http://127.0.0.1:8800")
    p.add_argument("--token", default="")
    p.add_argument("--format", choices=["text", "markdown"], default="text")
    p.add_argument("--fail-closed", action="store_true",
                   help="exit 2 when the API is unreachable (default: warn "
                        "and pass, so a gate outage never blocks merges)")
    args = ap.parse_args(argv)

    if not args.rules.exists():
        raise SystemExit(f"rules file not found: {args.rules}")
    rules = parse_rules(args.rules)

    api = Api(args.api, args.token)
    try:
        snaps = api.get(f"/v1/repos/{args.repo}/snapshots")
        base = resolve_snapshot(snaps, args.base)
        head = resolve_snapshot(snaps, args.head)
        head_edges = api.get(f"/v1/snapshots/{head['id']}/modules")["edges"]
        base_cycles = api.get(
            f"/v1/snapshots/{base['id']}/modules/cycles")["cycles"]
        head_cycles = api.get(
            f"/v1/snapshots/{head['id']}/modules/cycles")["cycles"]

        def fetch_sites(src: str, dst: str):
            return api.get(f"/v1/snapshots/{head['id']}/module-edge-sites",
                           src_module=src, dst_module=dst, limit=3)

        violations = evaluate(rules, head_edges, base_cycles, head_cycles,
                              fetch_sites)
    except (httpx.HTTPError, OSError) as exc:
        if args.fail_closed:
            print(f"osprey-gate: API unreachable ({exc})", file=sys.stderr)
            raise SystemExit(2) from exc
        print(f"osprey-gate: WARNING: API unreachable, passing open "
              f"({exc})", file=sys.stderr)
        raise SystemExit(0) from exc

    base_label = f"#{base['id']} {base['commit_sha'][:8]}"
    head_label = f"#{head['id']} {head['commit_sha'][:8]}"
    render = render_markdown if args.format == "markdown" else render_text
    print(render(violations, base_label, head_label))

    if any(v.severity == "error" for v in violations):
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
