"""Projection: events in, world out.

Deterministic and total -- the same events always produce the same world, and
`state_hash` proves it. Undone commits are not deleted; they are simply not
applied, which is the only honest way to undo an append-only log.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Dict, List, Optional, Sequence

from . import events as E
from .events import Event, canonical


@dataclasses.dataclass
class Thread:
    id: str
    subject: str = ""
    counterparty: str = ""
    messages: List[dict] = dataclasses.field(default_factory=list)

    @property
    def last_ts(self) -> int:
        return self.messages[-1]["ts"] if self.messages else 0

    @property
    def awaiting_reply_from(self) -> Optional[str]:
        """Who owes us an answer, if anyone."""
        if not self.messages:
            return None
        last = self.messages[-1]
        return self.counterparty if last["direction"] == "out" else None

    def reply_after(self, contact: str, after_ts: int, until_ts: int) -> Optional[int]:
        for m in self.messages:
            if (
                m["direction"] == "in"
                and m["sender"] == contact
                and after_ts < m["ts"] <= until_ts
            ):
                return m["ts"]
        return None


@dataclasses.dataclass
class Meeting:
    id: str
    title: str = ""
    start: int = 0
    end: int = 0
    attendees: List[str] = dataclasses.field(default_factory=list)
    state: str = "scheduled"
    moves: List[dict] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Subscription:
    id: str
    merchant: str = ""
    amount_cents: int = 0
    period: str = "monthly"
    state: str = "active"
    charges: List[int] = dataclasses.field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return self.amount_cents * len(self.charges)


@dataclasses.dataclass
class World:
    contacts: Dict[str, dict] = dataclasses.field(default_factory=dict)
    threads: Dict[str, Thread] = dataclasses.field(default_factory=dict)
    meetings: Dict[str, Meeting] = dataclasses.field(default_factory=dict)
    subscriptions: Dict[str, Subscription] = dataclasses.field(default_factory=dict)
    predictions: Dict[str, dict] = dataclasses.field(default_factory=dict)
    commits: Dict[str, dict] = dataclasses.field(default_factory=dict)
    applied: int = 0
    skipped: int = 0
    clock: int = 0

    def open_threads(self) -> List[Thread]:
        return [t for t in self.threads.values() if t.awaiting_reply_from]

    def state_hash(self) -> str:
        shape = {
            "contacts": {k: v for k, v in sorted(self.contacts.items())},
            "threads": {
                k: {
                    "subject": t.subject,
                    "counterparty": t.counterparty,
                    "messages": t.messages,
                }
                for k, t in sorted(self.threads.items())
            },
            "meetings": {k: dataclasses.asdict(m) for k, m in sorted(self.meetings.items())},
            "subscriptions": {
                k: dataclasses.asdict(s) for k, s in sorted(self.subscriptions.items())
            },
            "predictions": {k: v for k, v in sorted(self.predictions.items())},
        }
        # Commits are bookkeeping *about* the world, not the world. Excluding them
        # is what lets an undo restore a state hash bit for bit.
        return hashlib.sha256(canonical(shape).encode("utf-8")).hexdigest()

    def summary(self) -> dict:
        open_subs = [s for s in self.subscriptions.values() if s.state == "active"]
        resolved = [p for p in self.predictions.values() if p.get("outcome") is not None]
        return {
            "clock": self.clock,
            "contacts": len(self.contacts),
            "threads": len(self.threads),
            "open_threads": len(self.open_threads()),
            "meetings": len([m for m in self.meetings.values() if m.state == "scheduled"]),
            "active_subscriptions": len(open_subs),
            "monthly_burn_cents": sum(s.amount_cents for s in open_subs),
            "predictions": len(self.predictions),
            "predictions_resolved": len(resolved),
            "commits": len(self.commits),
            "events_applied": self.applied,
            "events_skipped": self.skipped,
            "state_hash": self.state_hash(),
        }


def project(evs: Sequence[Event], include_simulated: bool = True) -> World:
    """Fold events into a world. Two passes: learn what was undone, then apply."""
    undone = {
        ev.payload["commit_id"] for ev in evs if ev.kind == E.COMMIT_UNDONE
    }

    w = World()
    for ev in evs:
        if ev.commit_id is not None and ev.commit_id in undone:
            w.skipped += 1
            continue
        if ev.simulated and not include_simulated:
            w.skipped += 1
            continue
        _apply(w, ev)
        w.applied += 1
        w.clock = max(w.clock, ev.ts)
    return w


def _apply(w: World, ev: Event) -> None:
    p = ev.payload
    k = ev.kind

    if k == E.CONTACT_OBSERVED:
        w.contacts.setdefault(
            ev.entity, {"name": p.get("name", ev.entity), "address": p.get("address", "")}
        )

    elif k in (E.MESSAGE_RECEIVED, E.MESSAGE_SENT):
        t = w.threads.get(ev.entity)
        if t is None:
            t = Thread(id=ev.entity, subject=p.get("subject", ""), counterparty=p.get("counterparty", ""))
            w.threads[ev.entity] = t
        if not t.counterparty:
            t.counterparty = p.get("counterparty", "")
        t.messages.append(
            {
                "ts": ev.ts,
                "direction": "in" if k == E.MESSAGE_RECEIVED else "out",
                "sender": p.get("sender", t.counterparty if k == E.MESSAGE_RECEIVED else "me"),
                "actor": ev.actor,
                "simulated": ev.simulated,
                "body": p.get("body", ""),
            }
        )

    elif k == E.CALENDAR_SCHEDULED:
        w.meetings[ev.entity] = Meeting(
            id=ev.entity,
            title=p.get("title", ""),
            start=p["start"],
            end=p["end"],
            attendees=list(p.get("attendees", [])),
        )

    elif k == E.CALENDAR_MOVED:
        m = w.meetings.get(ev.entity)
        if m is not None:
            m.moves.append({"ts": ev.ts, "from": m.start, "to": p["start"]})
            m.start, m.end = p["start"], p["end"]

    elif k == E.CALENDAR_CANCELLED:
        m = w.meetings.get(ev.entity)
        if m is not None:
            m.state = "cancelled"

    elif k == E.SUBSCRIPTION_OBSERVED:
        w.subscriptions[ev.entity] = Subscription(
            id=ev.entity,
            merchant=p.get("merchant", ""),
            amount_cents=p["amount_cents"],
            period=p.get("period", "monthly"),
        )

    elif k == E.SUBSCRIPTION_CHARGED:
        s = w.subscriptions.get(ev.entity)
        if s is not None and s.state == "active":
            s.charges.append(ev.ts)

    elif k == E.SUBSCRIPTION_CANCELLED:
        s = w.subscriptions.get(ev.entity)
        if s is not None:
            s.state = "cancelled"

    elif k == E.PREDICTION_MADE:
        w.predictions[ev.entity] = {
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
        rec = w.predictions.get(ev.entity)
        if rec is not None:
            rec["outcome"] = bool(p["outcome"])
            rec["resolved_at"] = ev.ts

    elif k == E.COMMIT_OPENED:
        w.commits[ev.entity] = {
            "id": ev.entity,
            "branch": p["branch"],
            "opened_at": ev.ts,
            "state": "open",
            "actions": p["actions"],
            "sealed_at": None,
            "undone_at": None,
        }

    elif k == E.COMMIT_SEALED:
        c = w.commits.get(ev.entity)
        if c is not None:
            c["state"] = "sealed"
            c["sealed_at"] = ev.ts
            c["receipt"] = p

    elif k == E.COMMIT_UNDONE:
        c = w.commits.get(p["commit_id"])
        if c is not None:
            c["state"] = "undone"
            c["undone_at"] = ev.ts
