"""Projection: events in, state out.

Deterministic and total -- the same events always produce the same state, and
`state_hash` proves it. Undone commits are not deleted; they are simply not
applied, which is the only honest way to undo an append-only log.

A domain subclasses `Projection`, implements `apply` for its own event kinds and
`shape` for the part of its state that has to hash, and gets claims, commits and
undo folded in for free. Those three are the kernel's business and every domain
needs them identically.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Sequence

from . import events as E
from .events import Event, canonical


class Projection:
    """The kernel's half of any domain's state."""

    def __init__(self) -> None:
        self.predictions: Dict[str, dict] = {}
        self.commits: Dict[str, dict] = {}
        self.applied = 0
        self.skipped = 0
        self.clock = 0

    # ------------------------------------------------------------------- hooks

    def apply(self, ev: Event) -> None:
        """Fold one domain event. Kernel kinds never reach here."""

    def shape(self) -> dict:
        """The part of the domain's state that identity depends on.

        Anything omitted is invisible to `state_hash`, which means an undo is
        allowed to leave it changed. Omit bookkeeping, not state.
        """
        return {}

    # -------------------------------------------------------------------- fold

    @classmethod
    def fold(cls, evs: Sequence[Event], include_simulated: bool = True, **kwargs):
        """Two passes: learn what was undone, then apply what survives."""
        undone = {ev.payload["commit_id"] for ev in evs if ev.kind == E.COMMIT_UNDONE}

        self = cls(**kwargs)
        for ev in evs:
            if ev.commit_id is not None and ev.commit_id in undone:
                self.skipped += 1
                continue
            if ev.simulated and not include_simulated:
                self.skipped += 1
                continue
            if ev.kind in E.KERNEL_KINDS:
                self._apply_kernel(ev)
            else:
                self.apply(ev)
            self.applied += 1
            self.clock = max(self.clock, ev.ts)
        return self

    def state_hash(self) -> str:
        shape = dict(self.shape())
        # Claims are state: what the twin believed is part of what it was. But
        # commits are bookkeeping *about* the state, not the state -- excluding
        # them is what lets an undo restore a hash bit for bit.
        shape["predictions"] = {k: v for k, v in sorted(self.predictions.items())}
        return hashlib.sha256(canonical(shape).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ kernel

    def _apply_kernel(self, ev: Event) -> None:
        p = ev.payload
        k = ev.kind

        if k == E.PREDICTION_MADE:
            self.predictions[ev.entity] = {
                "id": ev.entity,
                "branch": ev.branch,
                "made_at": ev.ts,
                "claim": p["claim"],
                "resolver": p["resolver"],
                "params": p["params"],
                "p": p["p"],
                "predictor": p.get("predictor", "unknown"),
                "resolve_by": p["resolve_by"],
                "outcome": None,
                "resolved_at": None,
            }

        elif k == E.PREDICTION_RESOLVED:
            rec = self.predictions.get(ev.entity)
            if rec is not None:
                rec["outcome"] = bool(p["outcome"])
                rec["resolved_at"] = ev.ts

        elif k == E.COMMIT_OPENED:
            self.commits[ev.entity] = {
                "id": ev.entity,
                "branch": p["branch"],
                "opened_at": ev.ts,
                "state": "open",
                "actions": p["actions"],
                "sealed_at": None,
                "undone_at": None,
            }

        elif k == E.COMMIT_SEALED:
            c = self.commits.get(ev.entity)
            if c is not None:
                c["state"] = "sealed"
                c["sealed_at"] = ev.ts
                c["receipt"] = p

        elif k == E.COMMIT_UNDONE:
            c = self.commits.get(p["commit_id"])
            if c is not None:
                c["state"] = "undone"
                c["undone_at"] = ev.ts
