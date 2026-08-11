"""The week-4 question, asked with week-1 tools.

Replay a held-out stretch of the past. At each moment where the product would
really have something to say -- a message going out, a meeting two days off --
rewind the twin to that instant, let the predictor claim what happens next using
only what was knowable then, and score it against what the trunk records.

Two things here were learned the hard way and are worth keeping:

*Sample at the moments that exist, not on a grid.* A daily grid never sees a
thread that opened at two and was answered by seven, so the evaluation set fills
up with the slow and the silent and every predictor looks the same. The moment a
message is sent is the moment the question is actually asked.

*Only score what reality has settled.* A claim whose window has not closed is
not evidence, and counting it is how a backtest flatters itself.

This is the harness the real counterparty model drops into. Running it now with
deliberately dumb predictors proves it can rank them correctly before there is
anything worth ranking.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import events as E
from . import predictions as P
from .predictors import REGISTRY
from .store import TRUNK, EventStore
from .world import project

DAY = 24 * 3600
AGES = 3  # a send is asked about on the day, and for two days after


def moments(store: EventStore, t0: int, now: int, horizon: int) -> List[Tuple[int, str, str]]:
    """Every instant in the holdout where the product would have made a claim."""
    found: List[Tuple[int, str, str]] = []

    # Sampling only at the instant a message goes out means every scored question
    # has age zero, and a predictor named for elapsed time is never asked about
    # it. Each send is also revisited on the following days while it is still
    # unanswered -- which is exactly when a person looks at a thread and wonders.
    world = project(store.read(TRUNK))
    for ev in store.read(TRUNK):
        if ev.kind != E.MESSAGE_SENT or ev.ts < t0:
            continue
        thread = world.threads.get(ev.entity)
        for age in range(0, AGES):
            at = ev.ts + age * DAY
            if at + horizon > now:
                break
            if age and thread is not None and thread.reply_after(
                thread.counterparty, ev.ts, at
            ) is not None:
                break  # answered already; the question is no longer open
            found.append((at, "thread", ev.entity))

    # Same problem as threads had: one lead time meant one bucket, and "how long
    # until it starts" is the other half of the variable being claimed for.
    for m in world.meetings.values():
        start = m.moves[0]["from"] if m.moves else m.start  # as first known
        if start > now:
            continue
        # A lead longer than the horizon is not a question the predictor is
        # allowed to answer -- `_claims` only speaks about meetings inside it.
        for lead in range(1, min(AGES, max(1, horizon // DAY)) + 1):
            at = start - lead * DAY
            if at < t0:
                break
            if any(mv["ts"] <= at for mv in m.moves):
                break  # already moved; no longer an open question
            found.append((at, "meeting", m.id))

    found.sort()
    return found


def run(
    store: EventStore,
    predictor: str = "per-contact-age",
    holdout_days: int = 30,
    horizon_hours: int = 48,
    now: Optional[int] = None,
) -> dict:
    if predictor not in REGISTRY:
        raise ValueError(f"unknown predictor: {predictor} (have: {', '.join(REGISTRY)})")

    now = store.now(TRUNK) if now is None else now
    t0 = now - holdout_days * DAY
    horizon = horizon_hours * 3600
    model = REGISTRY[predictor]

    # A branch pinned at the start of the holdout, so the record shows where this
    # evaluation began and what it was allowed to know.
    branch = f"backtest_{predictor}_{holdout_days}d"
    if store.branch(branch) is None:
        store.fork(branch, TRUNK, at_ts=t0, note=f"holdout {holdout_days}d, {predictor}")

    made = 0
    for at, kind, entity in moments(store, t0, now, horizon):
        # Rewinding the twin to `at`: bounding world time on a trunk read is the
        # same operation the fork performs, done in place.
        past = project(store.read(TRUNK, until_ts=at))
        for c in model.predict(past, at_ts=at, horizon=horizon):
            if c["params"].get(kind) != entity or c["resolve_by"] > now:
                continue
            P.record(
                store,
                origin_branch=branch,
                resolver=c["resolver"],
                params=c["params"],
                p=c["p"],
                claim=c["claim"],
                made_at=at,
                resolve_by=c["resolve_by"],
                predictor=predictor,
            )
            made += 1

    settled = P.resolve_due(store, now=now)
    return {
        "predictor": predictor,
        "holdout_from": t0,
        "now": now,
        "moments": len(moments(store, t0, now, horizon)),
        "claims_made": made,
        "claims_settled": len(settled),
        "score": P.score(store, predictor=predictor),
    }
