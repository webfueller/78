"""Committing a week, and taking it back.

The rules -- only agent-authored events promote, nothing is ever deleted, all of
it or none of it -- are the kernel's, and enforced in `rehearsal.commits`. This
module is the mail-shaped door onto them.
"""

from __future__ import annotations

from typing import Optional

from rehearsal.commits import UNDO_WINDOW, already_promoted, promotable  # noqa: F401
from rehearsal.store import EventStore

from .kernel import KERNEL


def commit(store: EventStore, branch: str, at_ts: Optional[int] = None) -> dict:
    return KERNEL.commit(store, branch, at_ts=at_ts)


def undo(store: EventStore, commit_id: str, at_ts: Optional[int] = None) -> dict:
    return KERNEL.undo(store, commit_id, at_ts=at_ts)
