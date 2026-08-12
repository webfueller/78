"""workbench -- an agent edits your files; you read it first, and you can take it back.

The first domain in this repository where committing does something outside the
database. Proposed edits live on a fork where they can be read, scored and
thrown away. Committing writes them to disk and the log in one transaction, with
a receipt. Undo puts the bytes back, from the log, exactly.

    observe → propose → (read the preview) → commit → check → undo, if you must

The machinery is `rehearsal`. What is here is what a file is, what can go wrong
with one, and how much that is worth.
"""

from __future__ import annotations

from .commits import commit, materialise, undo
from .kernel import KERNEL
from .propose import Edit, rehearse, risk
from .state import Tree, project

# `observe` is deliberately not re-exported: the module is called `observe` and so
# is its one function, and binding the function here would shadow the module for
# anyone doing `from workbench import observe`.

__all__ = [
    "Edit", "KERNEL", "Tree", "commit", "materialise", "project",
    "rehearse", "risk", "undo",
]
__version__ = "0.1.0"
