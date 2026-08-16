"""Committing a branch, and taking it back.

Two rules are enforced here rather than documented:
  1. Only events the agent authored are promoted. Simulated counterparties never
     leave their fork -- there is no flag, no override, no admin path.
  2. Nothing is ever deleted. An undo appends, and the projection stops applying
     the commit's events. The record of what was almost done survives.

Neither rule mentions mail, money or calendars, which is why this lives in the
kernel. What a promoted event *does* is the domain's business; that it happens
once, atomically, and can be withdrawn is the kernel's.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, List, Optional, Type

from . import events as E
from .preferences import Preferences
from .projection import Projection
from .store import TRUNK, EventStore, StoreError

UNDO_WINDOW = 24 * 3600


def commit_id(branch: str, hashes: List[str]) -> str:
    raw = E.canonical([branch, hashes])
    return "c_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def promotable(store: EventStore, branch: str) -> List[E.Event]:
    """The agent's proposals, and only those.

    Anything off the trunk that the agent authored counts, including actions
    inherited from a parent fork -- a rehearsal writes its plan once and then
    forks per outcome, so the actions live one level up from the future you pick.
    """
    return [
        ev
        for ev in store.read(branch)
        if ev.branch != TRUNK and ev.actor == E.ACTOR_AGENT and not ev.simulated
    ]


def already_promoted(store: EventStore) -> set:
    """Source hashes the trunk has executed before, undone or not.

    Reads OPENED as well as SEALED. A commit that died between the two still
    sent real messages, and a guard that only looks at completed commits would
    cheerfully send them again.
    """
    seen = set()
    for ev in store.read(TRUNK):
        if ev.kind == E.COMMIT_SEALED:
            seen.update(ev.payload.get("source_hashes", []))
        elif ev.kind == E.COMMIT_OPENED:
            seen.update(a["source_hash"] for a in ev.payload.get("actions", []))
    return seen


class Commits:
    """Promotion and withdrawal, against a domain's projection."""

    def __init__(
        self,
        projection: Type[Projection],
        preferences: Optional[Preferences] = None,
        undo_window: int = UNDO_WINDOW,
        execute: Optional[Callable[[EventStore, List[E.Event]], None]] = None,
        anchor=None,
    ):
        self.projection = projection
        self.preferences = preferences
        self.undo_window = undo_window
        # Stamped after the transaction closes, never inside it: an anchor is a
        # statement about a log that already exists, and one written for a commit
        # that then rolled back would be a lie in the one file whose job is to be
        # true. A commit that lands without its stamp shows up as "behind", which
        # is visible and recoverable.
        self.anchor = anchor
        # Where a domain hangs its side effects. Nothing calls it yet -- this
        # product writes no mail -- and the signature is here so that when
        # something does, it runs inside the same transaction as the promotion
        # and cannot half-happen.
        self.execute = execute

    def _hash(self, store: EventStore, branch: str = TRUNK) -> str:
        return self.projection.fold(store.read(branch)).state_hash()

    def _stamp(self, store: EventStore) -> bool:
        """Record the new head outside the log, if an anchor is configured."""
        if self.anchor is None:
            return False
        self.anchor.record(store, TRUNK)
        return True

    def commit(self, store: EventStore, branch: str, at_ts: Optional[int] = None) -> dict:
        """Execute a rehearsed plan for real. All of it, or none of it.

        Everything below happens inside one IMMEDIATE transaction, including the
        check for actions that already ran. Reading the guard outside the write
        was a race with a very concrete consequence: two sibling futures
        committed at the same moment both passed the check and both sent the same
        eight messages.
        """
        if branch == TRUNK:
            raise StoreError("the trunk is not a proposal")

        with store.transaction():
            row = store.require_branch(branch)
            if row["status"] != "open":
                raise StoreError(f"branch {branch} is already {row['status']}")

            actions = promotable(store, branch)
            if not actions:
                raise StoreError(
                    f"branch {branch} proposes nothing to execute — if the intent is to "
                    "leave the week alone, decline it instead so the choice is still recorded"
                )

            # Sibling futures share their plan's actions. Picking a second future
            # must not send the same message twice.
            done = already_promoted(store)
            clash = [a for a in actions if a.hash in done]
            if clash:
                raise StoreError(
                    f"{len(clash)} of these actions were already executed by an earlier "
                    f"commit (first: {clash[0].kind} on {clash[0].entity})"
                )

            ts = store.now(TRUNK) if at_ts is None else at_ts
            before = self._hash(store)
            cid = commit_id(branch, [a.hash for a in actions])
            sealed_wall = int(time.time())

            store.append(
                branch=TRUNK, kind=E.COMMIT_OPENED, entity=cid, actor=E.ACTOR_AGENT, ts=ts,
                payload={
                    "branch": branch,
                    "actions": [
                        {"kind": a.kind, "entity": a.entity, "source_hash": a.hash}
                        for a in actions
                    ],
                    "state_before": before,
                },
            )

            if self.execute is not None:
                self.execute(store, actions)

            # A proposal carries timestamps from the fork's own timeline, which may
            # sit ahead of the trunk. Executing it can move the world clock forward;
            # it can never move it back.
            promoted = []
            clock = ts
            for a in actions:
                clock = max(clock, a.ts)
                ev = store.append(
                    branch=TRUNK, kind=a.kind, entity=a.entity, actor=E.ACTOR_AGENT,
                    ts=clock, payload=a.payload, commit_id=cid,
                )
                promoted.append(ev.hash)

            after = self._hash(store)
            store.append(
                branch=TRUNK, kind=E.COMMIT_SEALED, entity=cid, actor=E.ACTOR_AGENT, ts=clock,
                payload={
                    "branch": branch,
                    "source_hashes": [a.hash for a in actions],
                    "promoted_hashes": promoted,
                    "state_before": before,
                    "state_after": after,
                    "sealed_wall": sealed_wall,
                    "undo_until": sealed_wall + self.undo_window,
                },
            )
            store.set_status(branch, "committed")

            # Committing this future rather than its siblings is the only
            # unambiguous statement of preference the product ever gets. Record it.
            chosen_for = (
                self.preferences.record_choice(store, branch, at=clock)
                if self.preferences is not None else None
            )

        stamped = self._stamp(store)

        return {
            "learned_from": chosen_for,
            "anchored": stamped,
            "commit_id": cid,
            "branch": branch,
            "actions": len(actions),
            "state_before": before,
            "state_after": after,
            "sealed_at": clock,
            "sealed_wall": sealed_wall,
            "undo_until": sealed_wall + self.undo_window,
        }

    def undo(self, store: EventStore, cid: str, at_ts: Optional[int] = None) -> dict:
        w = self.projection.fold(store.read(TRUNK))
        c = w.commits.get(cid)
        if c is None:
            raise StoreError(f"no such commit: {cid}")
        if c["state"] == "undone":
            raise StoreError(f"{cid} was already undone")
        if c["state"] != "sealed":
            raise StoreError(f"{cid} is {c['state']}; only a sealed commit can be undone")

        # The window is measured against the wall clock, not the world clock.
        # World time is whatever the last event says it is: it does not advance
        # while the twin sits idle, and one imported message can jump it forward
        # by months. A receipt that promises "24 hours" has to mean the reader's
        # 24 hours.
        now_wall = int(time.time()) if at_ts is None else at_ts
        deadline = c["receipt"].get("undo_until")
        if deadline is not None and now_wall > deadline:
            raise StoreError(
                f"undo window closed for {cid} at {deadline}; it is now {now_wall}"
            )
        ts = store.now(TRUNK)

        store.append(
            branch=TRUNK, kind=E.COMMIT_UNDONE, entity=cid, actor=E.ACTOR_USER, ts=ts,
            payload={"commit_id": cid},
        )
        after = self._hash(store)
        # An undo is history too. Leaving it unstamped would make the log look
        # like it had grown past its anchor for no reason anyone could check.
        self._stamp(store)
        return {
            "commit_id": cid,
            "restored": after == c["receipt"]["state_before"],
            "state_hash": after,
            "expected": c["receipt"]["state_before"],
        }
