"""Telling the log what is on the disk.

Reconciliation is a deliberate act, never a silent one. When a managed file has
changed underneath the workbench, `observe` is how a person says "yes, that was
me, take it as the new truth" -- and until they do, the drift check refuses to
commit over it.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

from takeback.store import TRUNK, EventStore

from . import disk
from . import events as E
from .state import Tree


def clock(store: EventStore, at: Optional[int] = None) -> int:
    """World time that never runs backwards, seeded from the wall clock."""
    if at is not None:
        return at
    return max(int(time.time()), store.now(TRUNK))


def observe(
    store: EventStore,
    root: str,
    ignore: Iterable[str] = (),
    at: Optional[int] = None,
    branch: str = TRUNK,
) -> Dict[str, List[str]]:
    """Record the tree as it is now. Returns what changed."""
    tree = Tree.fold(store.read(branch))
    found = disk.scan(root, ignore=ignore)
    ts = clock(store, at)

    added, changed, gone = [], [], []
    with store.transaction():
        for path in sorted(found):
            content = found[path]
            known = tree.files.get(path)
            digest = disk.sha(content)
            if known is not None and known["sha256"] == digest:
                continue
            store.append(
                branch=branch, kind=E.FILE_OBSERVED, entity=path, actor=E.ACTOR_WORLD,
                ts=ts, payload={"sha256": digest, "content": content},
            )
            (changed if known is not None else added).append(path)

        for path in sorted(tree.files):
            if path not in found:
                store.append(
                    branch=branch, kind=E.FILE_DELETED, entity=path, actor=E.ACTOR_WORLD,
                    ts=ts, payload={"prev_sha256": tree.files[path]["sha256"]},
                )
                gone.append(path)

    return {"added": added, "changed": changed, "gone": gone}
