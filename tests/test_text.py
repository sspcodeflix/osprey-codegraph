"""no_dashes: house-style dash removal that never breaks GFM tables or
mangles plain hyphens (regression for the sweep that once did both).

Dash test data uses unicode escapes so a future dash sweep cannot neuter it.
"""

from osprey.text import no_dashes

EM = "\u2014"
EN = "\u2013"


def test_plain_hyphens_untouched():
    # the bug: a broken scrub turned every '-' into ' - '
    assert no_dashes("a-b-c") == "a-b-c"
    assert no_dashes("mlflow/tracking/fluent.py:185") \
        == "mlflow/tracking/fluent.py:185"


def test_table_delimiter_stays_valid():
    md = f"| a | b |\n|{EM * 4}|{EM * 3}|\n| 1 | 2 |"
    out = no_dashes(md)
    assert EM not in out and EN not in out
    # the delimiter row must remain contiguous hyphens so GFM still parses
    assert "|----|---|" in out


def test_spaced_em_dash_becomes_spaced_hyphen():
    assert no_dashes(f"changes ripple {EM} widely") == "changes ripple - widely"


def test_tight_dashes_become_hyphen():
    assert no_dashes(f"a{EM}b") == "a-b"
    assert no_dashes(f"1{EN}5") == "1-5"


def test_no_dashes_remain():
    assert EM not in no_dashes(f"x {EM} y {EM}{EM} z")
