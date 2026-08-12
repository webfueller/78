"""Commit, and take it back -- with the filesystem attached.

This is the first place in the repository where committing does something the
log cannot take back on its own. The kernel's guarantees still hold, and one new
one is added here: the disk moves inside the same transaction as the log, or
neither moves.

Order of events on a commit:

  1. the kernel opens the transaction and writes `commit.opened`
  2. this module checks the disk still matches the log, and refuses if not
  3. this module writes the files, journalling what it replaced
  4. the kernel promotes the actions and seals the receipt
  5. the transaction commits

A failure at 2 or 3 raises, which rolls back the log and puts the files back.
A failure at 4 or 5 rolls back the log and the journal in `disk.apply` has
already been discarded -- so step 3 is deliberately the last thing that can fail
in a way the log would not notice, and the drift check at step 2 of the *next*
commit is the backstop that says so out loud.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from rehearsal.commits import Commits
from rehearsal.events import Event
from rehearsal.store import TRUNK, EventStore, StoreError

from . import disk
from . import events as E
from .kernel import KERNEL
from .state import Tree


def _changes(actions: Sequence[Event]) -> List[tuple]:
    return [
        (a.entity, None if a.kind == E.FILE_DELETED else a.payload["content"])
        for a in actions
        if a.kind in E.EDITS
    ]


def _executor(root: str):
    def execute(store: EventStore, actions: Sequence[Event]) -> None:
        changes = _changes(actions)
        if not changes:
            return
        tree = Tree.fold(store.read(TRUNK))
        bad = disk.drift(root, tree, [p for p, _ in changes])
        if bad:
            first = bad[0]
            raise StoreError(
                f"refusing to commit: {len(bad)} file(s) changed on disk since this was "
                f"rehearsed (first: {first['path']} — {first['why']}). The preview you are "
                "looking at was computed against different bytes; rescan and rehearse again."
            )
        disk.apply(root, changes)

    return execute


def commit(store: EventStore, branch: str, root: str, at_ts: Optional[int] = None) -> dict:
    """Promote a rehearsed change set, and put it on disk, atomically."""
    engine = Commits(
        KERNEL.projection,
        KERNEL.preferences,
        execute=_executor(root),
    )
    receipt = engine.commit(store, branch, at_ts=at_ts)
    receipt["root"] = root
    receipt["files"] = sorted({
        a.entity for a in _promoted(store, receipt["commit_id"])
    })
    return receipt


def _promoted(store: EventStore, commit_id: str) -> List[Event]:
    return [e for e in store.read(TRUNK) if e.commit_id == commit_id and e.kind in E.EDITS]


def undo(store: EventStore, commit_id: str, root: str, at_ts: Optional[int] = None) -> dict:
    """Take the commit back, and put the files back with it.

    Wrapped in a transaction the kernel's own undo does not open: if the files
    cannot be restored, the `commit.undone` event is rolled back too, so the log
    never claims a restoration that did not reach the disk.
    """
    with store.transaction():
        out = KERNEL.undo(store, commit_id, at_ts=at_ts)
        tree = Tree.fold(store.read(TRUNK))
        # The undone commit's own paths have to be named explicitly. Once the
        # projection stops applying them, a file the commit *created* is in
        # neither the tree nor its history, so `managed()` cannot see it and the
        # undo would leave it sitting on disk -- restored everywhere except the
        # one place the user is looking.
        touched = sorted({e.entity for e in _promoted(store, commit_id)} | set(tree.managed()))
        restored = materialise(root, tree, touched)
    out["root"] = root
    out["files_restored"] = restored
    out["disk_matches"] = not disk.drift(root, tree)
    return out


def materialise(root: str, tree: Tree, paths: Optional[Sequence[str]] = None) -> List[str]:
    """Make the disk say what the log says, for managed paths only.

    Only paths the log has touched are considered. A file the workbench has never
    seen is somebody else's, and a tool that tidies up other people's files while
    restoring its own is not one you would let near a repository.
    """
    wanted = list(paths) if paths is not None else tree.managed()
    changes = []
    for path in wanted:
        want = tree.files.get(path)
        try:
            have = disk.read(root, path)
        except disk.DiskError:
            have = None
        if want is None and have is not None:
            changes.append((path, None))
        elif want is not None and (have is None or disk.sha(have) != want["sha256"]):
            changes.append((path, want["content"]))
    if changes:
        disk.apply(root, changes)
    return [p for p, _ in changes]
