"""MCP server helper tests (HTTP-free)."""

import pytest

from osprey.mcp import server as S


class TestShort:
    def test_scip_symbol_shortened(self):
        assert S.short(
            "scip-python python code-graph-rag 0.0.561 `pkg.mod`/f()."
        ) == "`pkg.mod`/f()."

    def test_non_scip_passthrough(self):
        assert S.short("plain") == "plain"


class TestResolve:
    @pytest.fixture(autouse=True)
    def fake_api(self, monkeypatch):
        snaps = [
            {"id": 11, "commit_sha": "e5eb9bfabc", "status": "ready"},
            {"id": 10, "commit_sha": "e5eb9bfabc", "status": "failed"},
            {"id": 5, "commit_sha": "aa7c9e5def", "status": "ready"},
        ]
        monkeypatch.setattr(S, "get", lambda path, **kw: snaps)

    def test_latest_skips_failed(self):
        assert S.resolve("hono", "latest")["id"] == 11

    def test_previous(self):
        assert S.resolve("hono", "previous")["id"] == 5

    def test_by_id_and_sha_prefix(self):
        assert S.resolve("hono", "5")["id"] == 5
        assert S.resolve("hono", "aa7c9e5")["id"] == 5

    def test_no_match_raises(self):
        with pytest.raises(ValueError, match="no ready snapshot"):
            S.resolve("hono", "deadbeef")


def test_all_tools_registered():
    import asyncio
    tools = asyncio.run(S.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_repos", "list_snapshots", "search_symbols", "get_callers",
        "get_callees", "blast_radius", "module_graph", "find_cycles",
        "edge_evidence", "structural_diff", "dead_code",
    }
    # every tool must carry a docstring — it IS the model-facing contract
    assert all(t.description for t in tools)
