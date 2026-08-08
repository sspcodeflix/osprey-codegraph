"""The staleness decision (D1): which doc pages must be rewritten after a
structural diff, and which carry forward at zero token cost."""

from osprey.docs.pipeline import StalenessSignals, page_is_stale

QUIET = StalenessSignals(frozenset(), frozenset(), False, False, False)


def sig(**kw) -> StalenessSignals:
    base = {"changed_syms": frozenset(), "modules_touched": frozenset(),
            "module_edges_changed": False, "entries_changed": False,
            "hotspots_changed": False}
    base.update(kw)
    return StalenessSignals(**base)


def test_no_change_nothing_stale():
    for kind in ("overview", "architecture", "entries", "hot"):
        assert not page_is_stale(kind, None, {"a", "b"}, QUIET)
    assert not page_is_stale("module", "src/core", {"a"}, QUIET)


def test_cited_symbol_changed_page_is_stale():
    s = sig(changed_syms=frozenset({"pkg/mod.fn"}))
    assert page_is_stale("module", "other/place", {"pkg/mod.fn"}, s)
    # a page citing only untouched symbols stays fresh
    assert not page_is_stale("module", "other/place", {"pkg/mod.other"}, s)


def test_overview_and_architecture_follow_module_edges():
    s = sig(module_edges_changed=True)
    assert page_is_stale("overview", None, set(), s)
    assert page_is_stale("architecture", None, set(), s)
    assert not page_is_stale("hot", None, set(), s)


def test_module_page_follows_its_folder():
    s = sig(modules_touched=frozenset({"src/api"}))
    assert page_is_stale("module", "src/api", set(), s)
    assert not page_is_stale("module", "src/db", set(), s)


def test_entries_and_hot_follow_their_sets():
    assert page_is_stale("entries", None, set(), sig(entries_changed=True))
    assert not page_is_stale("entries", None, set(),
                             sig(hotspots_changed=True))
    assert page_is_stale("hot", None, set(), sig(hotspots_changed=True))


def test_unknown_kind_fails_safe():
    assert page_is_stale("mystery", None, set(), QUIET)
