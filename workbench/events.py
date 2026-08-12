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

EDITS = frozenset({FILE_WRITTEN, FILE_DELETED})
DOMAIN_KINDS = frozenset({FILE_OBSERVED, FILE_WRITTEN, FILE_DELETED, CHECK_REPORTED})
