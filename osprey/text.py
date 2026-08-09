"""Small shared text helpers.

The dash characters are written as unicode escapes on purpose: a literal
em-dash in this file would be rewritten by a house-style dash sweep, which
is exactly what once broke the scrub that lives on top of this.
"""

from __future__ import annotations

import re

_EM = "\u2014"     # em-dash
_EN = "\u2013"     # en-dash
_DASH_RUN = re.compile(f"[{_EN}{_EM}]{{2,}}")


def no_dashes(text: str) -> str:
    """Enforce house style: no em/en-dashes in output.

    A run of two or more dashes (a markdown table delimiter or horizontal
    rule) collapses to the same number of plain hyphens, so GFM tables
    still parse. A lone spaced dash becomes ' - '; a tight one becomes '-'.
    Plain ASCII hyphens are left untouched.
    """
    text = _DASH_RUN.sub(lambda m: "-" * len(m.group()), text)
    return (text.replace(f" {_EM} ", " - ")
                .replace(_EM, "-").replace(_EN, "-"))
