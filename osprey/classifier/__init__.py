"""Per-language syntax classifiers (ARCHITECTURE.md §6).

Each language answers three questions about a source position:
  - is this identifier in call position?           -> CALLS vs REFERENCES
  - is it inside an import statement?              -> IMPORTS
  - is it inside a class's base/heritage clause?   -> INHERITS

Everything else about a symbol comes from SCIP; this is deliberately the
only place Osprey interprets syntax itself.
"""

from osprey.classifier.base import FileFacts, language_for_path

__all__ = ["FileFacts", "language_for_path"]
