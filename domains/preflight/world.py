"""Projection: events in, world out.

Mail, meetings and money, folded from the log. Claims, commits and undo are the
kernel's half of this and live in `takeback.projection`; what is here is only
what makes this domain *this* domain.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Sequence

from takeback.events import Event
from takeback.projection import Projection

from . import events as E


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


class World(Projection):
    def __init__(self) -> None:
        super().__init__()
        self.contacts: Dict[str, dict] = {}
        self.threads: Dict[str, Thread] = {}
        self.meetings: Dict[str, Meeting] = {}
        self.subscriptions: Dict[str, Subscription] = {}

    def open_threads(self) -> List[Thread]:
        return [t for t in self.threads.values() if t.awaiting_reply_from]

    def shape(self) -> dict:
        """What being this world means. The kernel adds the claims and hashes it."""
        return {
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
        }

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

    def apply(self, ev: Event) -> None:
        p = ev.payload
        k = ev.kind

        if k == E.CONTACT_OBSERVED:
            self.contacts.setdefault(
                ev.entity, {"name": p.get("name", ev.entity), "address": p.get("address", "")}
            )

        elif k in (E.MESSAGE_RECEIVED, E.MESSAGE_SENT):
            t = self.threads.get(ev.entity)
            if t is None:
                t = Thread(
                    id=ev.entity,
                    subject=p.get("subject", ""),
                    counterparty=p.get("counterparty", ""),
                )
                self.threads[ev.entity] = t
            if not t.counterparty:
                t.counterparty = p.get("counterparty", "")
            t.messages.append(
                {
                    "ts": ev.ts,
                    "direction": "in" if k == E.MESSAGE_RECEIVED else "out",
                    "sender": p.get(
                        "sender", t.counterparty if k == E.MESSAGE_RECEIVED else "me"
                    ),
                    "actor": ev.actor,
                    "simulated": ev.simulated,
                    "body": p.get("body", ""),
                }
            )

        elif k == E.CALENDAR_SCHEDULED:
            self.meetings[ev.entity] = Meeting(
                id=ev.entity,
                title=p.get("title", ""),
                start=p["start"],
                end=p["end"],
                attendees=list(p.get("attendees", [])),
            )

        elif k == E.CALENDAR_MOVED:
            m = self.meetings.get(ev.entity)
            if m is not None:
                m.moves.append({"ts": ev.ts, "from": m.start, "to": p["start"]})
                m.start, m.end = p["start"], p["end"]

        elif k == E.CALENDAR_CANCELLED:
            m = self.meetings.get(ev.entity)
            if m is not None:
                m.state = "cancelled"

        elif k == E.SUBSCRIPTION_OBSERVED:
            self.subscriptions[ev.entity] = Subscription(
                id=ev.entity,
                merchant=p.get("merchant", ""),
                amount_cents=p["amount_cents"],
                period=p.get("period", "monthly"),
            )

        elif k == E.SUBSCRIPTION_CHARGED:
            s = self.subscriptions.get(ev.entity)
            if s is not None and s.state == "active":
                s.charges.append(ev.ts)

        elif k == E.SUBSCRIPTION_CANCELLED:
            s = self.subscriptions.get(ev.entity)
            if s is not None:
                s.state = "cancelled"


def project(evs: Sequence[Event], include_simulated: bool = True) -> World:
    return World.fold(evs, include_simulated=include_simulated)
