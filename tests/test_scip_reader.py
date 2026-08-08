"""Symbol-grammar tests — every rule here is an M0-measured fact about real
indexer output, not spec reading."""

from osprey.scip import reader as R


def test_stable_symbol_normalizes_version():
    sym = "scip-python python code-graph-rag 0.0.561 `pkg.mod`/f()."
    assert R.stable_symbol(sym) == "scip-python python code-graph-rag _ `pkg.mod`/f()."


def test_kinds():
    assert R.kind_of("x y z v `m`/Class#") == "class"
    assert R.kind_of("x y z v `m`/Class#method().") == "method"
    assert R.kind_of("x y z v `m`/func().") == "function"
    assert R.kind_of("x y z v `m`/CONST.") == "variable"
    assert R.kind_of("x y z v `pkg.mod`/__init__:") == "module"
    assert R.kind_of("x y z v `src/f.ts`/") == "module"


def test_params_filtered():
    assert R.is_param("x y z v `m`/f().(arg)")
    assert R.is_param("x y z v `m`/C#[T]")
    assert not R.is_param("x y z v `m`/f().")


def test_names_parsed_from_grammar_not_display_name():
    # scip-python leaves display_name empty (M0); names come from the symbol
    assert R.name_from_symbol("x y z v `m`/Class#method().") == "method"
    assert R.name_from_symbol("x y z v `m`/Class#") == "Class"
    assert R.name_from_symbol("x y z v `pkg.mod`/__init__:") == "pkg.mod"
    assert R.name_from_symbol("x y z v `m`/_load_hash_cache().") == "_load_hash_cache"


def test_merged_documents_groups_by_path():
    # scip-typescript emits one Document per (file, tsconfig) pair (M0)
    from osprey.scip.scip_pb2 import Document, Index
    idx = Index()
    idx.documents.append(Document(relative_path="src/a.ts"))
    idx.documents.append(Document(relative_path="src/b.ts"))
    idx.documents.append(Document(relative_path="src/a.ts"))
    merged = list(R.merged_documents(idx))
    assert [p for p, _ in merged] == ["src/a.ts", "src/b.ts"]
    assert len(merged[0][1]) == 2
