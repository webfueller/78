"""Trivial predictors. No model, on purpose.

Weeks 1-2 ship the instrument, not the intelligence. Three deliberately dumb
predictors exist so the scoreboard can be validated before anything smart is
plugged in. They are ordered by how well specified they are, and the harness has
to rank them in that order -- if it cannot, it will not judge a real model
either.

The middle one is the interesting case. Knowing *who* you are waiting on sounds
like it must help, and it barely does, because conditioning on "still no reply"
already burns through the part of the distribution where a fast replier would
have answered. An open thread with Ana, who always answers within the day, is
evidence something is wrong; an open thread with Tom, who takes a week, is
Tuesday. Age is the variable that matters, and a model without it is
mis-specified no matter how much contact history it has.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .world import World

HOUR = 3600
DAY = 24 * HOUR

Counts = Dict[str, Tuple[int, int]]


def _tally(stats: Counts, key: str, hit: bool) -> None:
    got, chances = stats.get(key, (0, 0))
    stats[key] = (got + (1 if hit else 0), chances + 1)


def reply_stats(
    w: World, window: int, until_ts: int, step: int = DAY
) -> Tuple[Counts, Counts]:
    """Per contact, and per (contact, age): replies that came, chances counted.

    History is walked exactly the way the backtest walks it: from each message we
    sent, at each step it was still unanswered, ask whether the next `window`
    produced a reply. Same conditional, same eligibility rule, so a rate measured
    here answers the question actually asked at prediction time.
    """
    flat: Counts = {}
    aged: Counts = {}
    for t in w.threads.values():
        cp = t.counterparty
        if not cp:
            continue
        for i, m in enumerate(t.messages):
            if m["direction"] != "out":
                continue
            reply_ts = None
            for n in t.messages[i + 1 :]:
                if n["direction"] == "out":
                    break  # we wrote again; this chance is superseded
                if n["sender"] == cp:
                    reply_ts = n["ts"]
                    break
            observed = m["ts"]
            while observed - m["ts"] <= window:
                if observed + window > until_ts:
                    break  # the window has not elapsed; the answer is not known yet
                if reply_ts is not None and reply_ts <= observed:
                    break  # already answered; the thread is no longer open
                hit = reply_ts is not None and observed < reply_ts <= observed + window
                age = (observed - m["ts"]) // step
                _tally(flat, cp, hit)
                _tally(aged, f"{cp}@{age}", hit)
                observed += step
    return flat, aged


def move_stats(
    w: World, until_ts: int, window: int, step: int = DAY
) -> Tuple[Counts, Counts]:
    """Per attendee, and per (attendee, days until it starts): the same shape."""
    flat: Counts = {}
    aged: Counts = {}
    for m in w.meetings.values():
        others = [a for a in m.attendees if a != "me"]
        if not others:
            continue
        start = m.moves[0]["from"] if m.moves else m.start  # as first known
        if start > until_ts:
            continue
        observed = start - window
        while observed < start:
            if any(mv["ts"] <= observed for mv in m.moves):
                break  # already moved; no longer an open question
            hit = any(observed < mv["ts"] <= start for mv in m.moves)
            left = (start - observed) // step
            for a in others:
                _tally(flat, a, hit)
                _tally(aged, f"{a}@{left}", hit)
            observed += step
    return flat, aged


def _rate(pair: Optional[Tuple[int, int]], prior: float, strength: float = 3.0) -> float:
    """Smooth a sparse rate toward its prior so one observation cannot own a claim."""
    if not pair or pair[1] == 0:
        return prior
    got, chances = pair
    return (prior * strength + got) / (strength + chances)


def _pooled(stats: Counts) -> float:
    chances = sum(c for _, c in stats.values())
    return sum(g for g, _ in stats.values()) / chances if chances else 0.5


def _clamp(p: float) -> float:
    return min(0.97, max(0.03, p))


class Predictor:
    name = "abstract"

    def predict(self, w: World, at_ts: int, horizon: int) -> List[dict]:
        raise NotImplementedError


class GlobalBaseRate(Predictor):
    """Everyone behaves like the average of everyone. The number to beat."""

    name = "global"

    def predict(self, w: World, at_ts: int, horizon: int) -> List[dict]:
        rflat, _ = reply_stats(w, horizon, at_ts)
        mflat, _ = move_stats(w, at_ts, horizon)
        rp, mp = _pooled(rflat), _pooled(mflat)
        return _claims(w, at_ts, horizon, lambda c, age: rp, lambda a, left: mp)


class PerContact(Predictor):
    """Who you are waiting on, and nothing else. Sounds sufficient. Is not."""

    name = "per-contact"

    def predict(self, w: World, at_ts: int, horizon: int) -> List[dict]:
        rflat, _ = reply_stats(w, horizon, at_ts)
        mflat, _ = move_stats(w, at_ts, horizon)
        rp, mp = _pooled(rflat), _pooled(mflat)
        return _claims(
            w, at_ts, horizon,
            lambda c, age: _rate(rflat.get(c), rp),
            lambda a, left: _rate(mflat.get(a), mp),
        )


class PerContactAge(Predictor):
    """Who, and how long they have already kept you waiting.

    Rates are smoothed up the hierarchy -- (contact, age) toward contact, contact
    toward the global rate -- so a thin cell borrows from its parent instead of
    inventing a certainty.
    """

    name = "per-contact-age"

    def predict(self, w: World, at_ts: int, horizon: int) -> List[dict]:
        rflat, raged = reply_stats(w, horizon, at_ts)
        mflat, maged = move_stats(w, at_ts, horizon)
        rp, mp = _pooled(rflat), _pooled(mflat)

        def reply_p(c: str, age: int) -> float:
            return _rate(raged.get(f"{c}@{age}"), _rate(rflat.get(c), rp))

        def move_p(a: str, left: int) -> float:
            return _rate(maged.get(f"{a}@{left}"), _rate(mflat.get(a), mp))

        return _claims(w, at_ts, horizon, reply_p, move_p)


def _claims(w: World, at_ts: int, horizon: int, reply_p, move_p, step: int = DAY) -> List[dict]:
    """Claims about live threads and imminent meetings only.

    A thread that went quiet three months ago is a different question with a
    different base rate; mixing it in is how a backtest ends up scoring something
    nobody asked. Eligibility here matches the eligibility the stats were counted
    under, exactly.
    """
    claims: List[dict] = []

    for t in w.open_threads():
        waited = at_ts - t.last_ts
        if not 0 <= waited <= horizon:
            continue
        contact = t.awaiting_reply_from
        name = w.contacts.get(contact, {}).get("name", contact)
        claims.append(
            {
                "resolver": "reply_within",
                "params": {"thread": t.id, "contact": contact},
                "p": _clamp(reply_p(contact, waited // step)),
                "claim": f"{name} replies on '{t.subject}' within {horizon // HOUR}h",
                "resolve_by": at_ts + horizon,
            }
        )

    for m in w.meetings.values():
        if m.state != "scheduled" or not (at_ts < m.start <= at_ts + horizon):
            continue
        others = [a for a in m.attendees if a != "me"]
        if not others:
            continue
        left = (m.start - at_ts) // step
        claims.append(
            {
                "resolver": "meeting_moves",
                "params": {"meeting": m.id},
                "p": _clamp(max(move_p(a, left) for a in others)),
                "claim": f"'{m.title}' gets moved before it happens",
                "resolve_by": m.start,
            }
        )

    return claims


REGISTRY = {p.name: p for p in (GlobalBaseRate(), PerContact(), PerContactAge())}
