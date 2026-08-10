"""Committing a branch, and taking it back.

Two rules are enforced here rather than documented:
  1. Only events the agent authored are promoted. Simulated counterparties never
     leave their fork -- there is no flag, no override, no admin path.
  2. Nothing is ever deleted. An undo appends, and the projection stops applying
     the commit's events. The record of what was almost done survives.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

from . import events as E
from .store import TRUNK, EventStore, StoreError
from .world import project

UNDO_WINDOW = 24 * 3600


def _commit_id(branch: str, hashes: List[str]) -> str:
    raw = E.canonical([branch, hashes])
    return "c_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def promotable(store: EventStore, branch: str) -> List[E.Event]:
    """The agent's proposals, and only those."""
    fork_row = store.require_branch(branch)
    own = [ev for ev in store.read(branch) if ev.branch == branch and ev.gid > fork_row["fork_gid"]]
    return [ev for ev in own if ev.actor == E.ACTOR_AGENT and not ev.simulated]


def commit(store: EventStore, branch: str, at_ts: Optional[int] = None) -> dict:
    if branch == TRUNK:
        raise StoreError("the trunk is not a proposal")
    row = store.require_branch(branch)
    if row["status"] != "open":
        raise StoreError(f"branch {branch} is already {row['status']}")

    actions = promotable(store, branch)
    if not actions:
        raise StoreError(f"branch {branch} proposes nothing the agent authored")

    ts = store.now(TRUNK) if at_ts is None else at_ts
    before = project(store.read(TRUNK)).state_hash()
    cid = _commit_id(branch, [a.hash for a in actions])

    store.append(
        branch=TRUNK, kind=E.COMMIT_OPENED, entity=cid, actor=E.ACTOR_AGENT, ts=ts,
        payload={
            "branch": branch,
            "actions": [
                {"kind": a.kind, "entity": a.entity, "source_hash": a.hash} for a in actions
            ],
            "state_before": before,
        },
    )

    # A proposal carries timestamps from the fork's own timeline, which may sit
    # ahead of the trunk. Executing it can move the world clock forward; it can
    # never move it back.
    promoted = []
    clock = ts
    for a in actions:
        clock = max(clock, a.ts)
        ev = store.append(
            branch=TRUNK, kind=a.kind, entity=a.entity, actor=E.ACTOR_AGENT,
            ts=clock, payload=a.payload, commit_id=cid,
        )
        promoted.append(ev.hash)

    after = project(store.read(TRUNK)).state_hash()
    store.append(
        branch=TRUNK, kind=E.COMMIT_SEALED, entity=cid, actor=E.ACTOR_AGENT, ts=clock,
        payload={
            "branch": branch,
            "source_hashes": [a.hash for a in actions],
            "promoted_hashes": promoted,
            "state_before": before,
            "state_after": after,
            "undo_until": clock + UNDO_WINDOW,
        },
    )
    store.set_status(branch, "committed")
    return {
        "commit_id": cid,
        "branch": branch,
        "actions": len(actions),
        "state_before": before,
        "state_after": after,
        "sealed_at": clock,
        "undo_until": clock + UNDO_WINDOW,
    }


def undo(store: EventStore, commit_id: str, at_ts: Optional[int] = None) -> dict:
    w = project(store.read(TRUNK))
    c = w.commits.get(commit_id)
    if c is None:
        raise StoreError(f"no such commit: {commit_id}")
    if c["state"] == "undone":
        raise StoreError(f"{commit_id} was already undone")
    if c["state"] != "sealed":
        raise StoreError(f"{commit_id} is {c['state']}; only a sealed commit can be undone")

    ts = store.now(TRUNK) if at_ts is None else at_ts
    deadline = c["receipt"]["undo_until"]
    if ts > deadline:
        raise StoreError(
            f"undo window closed for {commit_id} at {deadline}; it is now {ts}"
        )

    store.append(
        branch=TRUNK, kind=E.COMMIT_UNDONE, entity=commit_id, actor=E.ACTOR_USER, ts=ts,
        payload={"commit_id": commit_id},
    )
    after = project(store.read(TRUNK)).state_hash()
    return {
        "commit_id": commit_id,
        "restored": after == c["receipt"]["state_before"],
        "state_hash": after,
        "expected": c["receipt"]["state_before"],
    }
