"""What can happen to a tree of files.

Four kinds. Three of them are things an agent can propose and the kernel can
therefore promote, undo, and refuse to run twice. The fourth is reality
answering back: the checks ran, and here is what they said.

Structure -- what an event is, how it is hashed, who may author one -- belongs to
`rehearsal`.
"""

from __future__ import annotations

from rehearsal.events import (  # noqa: F401  (this module is the domain's event namespace)
    ACTOR_AGENT,
    ACTOR_USER,
    ACTOR_WORLD,
    SIM_PREFIX,
    Event,
    canonical,
)

FILE_OBSERVED = "file.observed"
FILE_WRITTEN = "file.written"
FILE_DELETED = "file.deleted"
CHECK_REPORTED = "check.reported"

# History without bytes. A repository's git log says this file was edited then,
# and reconstructing every historical version to go with it would cost thousands
# of `git show` calls to answer a question that only needs the timestamps. So a
# touch is evidence for the risk model and nothing else: it is never a restore
# point, and the workbench will not claim it can put one back.
FILE_TOUCHED = "file.touched"

EDITS = frozenset({FILE_WRITTEN, FILE_DELETED})
OWNS = frozenset({FILE_OBSERVED, FILE_WRITTEN, FILE_DELETED})
DOMAIN_KINDS = frozenset({
    FILE_OBSERVED, FILE_WRITTEN, FILE_DELETED, FILE_TOUCHED, CHECK_REPORTED,
})
