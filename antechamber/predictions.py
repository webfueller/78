"""The prediction ledger -- the asset that is not in anybody's mailbox.

A rehearsal is only worth something if it made a claim that reality could later
refute. Every claim is written down with a resolver and a due date; when the due
date passes, the resolver reads what actually happened on the trunk and scores
it. The accumulated (claim, outcome) pairs are the compounding asset. This file
is why the week 1-2 build is not just plumbing.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Optional, Sequence

from . import events as E
from .store import TRUNK, EventStore
from .world import World, project

LEDGER = "ledger"

Resolver = Callable[[World, dict], bool]


def _reply_within(w: World, rec: dict) -> bool:
    t = w.threads.get(rec["params"]["thread"])
    if t is None:
        return False
    return t.reply_after(rec["params"]["contact"], rec["made_at"], rec["resolve_by"]) is not None


def _thread_dies(w: World, rec: dict) -> bool:
    return not _reply_within(w, rec)


def _meeting_moves(w: World, rec: dict) -> bool:
    m = w.meetings.get(rec["params"]["meeting"])
    if m is None:
        return False
    return any(rec["made_at"] < mv["ts"] <= rec["resolve_by"] for mv in m.moves)


def _charge_recurs(w: World, rec: dict) -> bool:
    s = w.subscriptions.get(rec["params"]["subscription"])
    if s is None:
        return False
    return any(rec["made_at"] < ts <= rec["resolve_by"] for ts in s.charges)


RESOLVERS: Dict[str, Resolver] = {
    "reply_within": _reply_within,
    "thread_dies": _thread_dies,
    "meeting_moves": _meeting_moves,
    "charge_recurs": _charge_recurs,
}


def prediction_id(origin: str, resolver: str, params: dict, made_at: int) -> str:
    raw = E.canonical([origin, resolver, params, made_at])
    return "p_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record(
    store: EventStore,
    *,
    origin_branch: str,
    resolver: str,
    params: dict,
    p: float,
    claim: str,
    made_at: int,
    resolve_by: int,
    predictor: str,
) -> str:
    """Write a falsifiable claim to the ledger.

    The ledger is its own root branch: a claim is an assertion *about* reality,
    not part of it, so it must not contaminate the world the twin projects.
    """
    if resolver not in RESOLVERS:
        raise ValueError(f"unknown resolver: {resolver}")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p}")
    if resolve_by <= made_at:
        raise ValueError("a claim must resolve in the future or it is not a claim")

    store.root(LEDGER, note="falsifiable claims and their outcomes")
    pid = prediction_id(origin_branch, resolver, params, made_at)
    store.append(
        branch=LEDGER,
        kind=E.PREDICTION_MADE,
        entity=pid,
        actor=E.ACTOR_AGENT,
        ts=made_at,
        allow_backdate=True,
        payload={
            "claim": claim,
            "resolver": resolver,
            "params": params,
            "p": round(float(p), 6),
            "predictor": predictor,
            "origin_branch": origin_branch,
            "resolve_by": resolve_by,
        },
    )
    return pid


def ledger(store: EventStore) -> Dict[str, dict]:
    if store.branch(LEDGER) is None:
        return {}
    return project(store.read(LEDGER)).predictions


def resolve_due(store: EventStore, now: Optional[int] = None) -> List[dict]:
    """Score every claim whose moment of truth has passed. Reality is the trunk."""
    if store.branch(LEDGER) is None:
        return []
    now = store.now(TRUNK) if now is None else now
    reality = project(store.read(TRUNK))
    pending = [
        r
        for r in ledger(store).values()
        if r["outcome"] is None and r["resolve_by"] <= now
    ]
    pending.sort(key=lambda r: r["resolve_by"])

    settled = []
    for rec in pending:
        outcome = RESOLVERS[rec["resolver"]](reality, rec)
        store.append(
            branch=LEDGER,
            kind=E.PREDICTION_RESOLVED,
            entity=rec["id"],
            actor=E.ACTOR_WORLD,
            ts=rec["resolve_by"],
            allow_backdate=True,
            payload={"outcome": bool(outcome), "resolver": rec["resolver"]},
        )
        settled.append({**rec, "outcome": bool(outcome)})
    return settled


# ------------------------------------------------------------------ scoreboard


def brier(pairs: Sequence[tuple]) -> float:
    if not pairs:
        return float("nan")
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in pairs) / len(pairs)


def _leave_one_out_base_rates(outcomes: Sequence[bool]) -> List[float]:
    """The trivial baseline, computed without letting it peek at its own answer."""
    n = len(outcomes)
    if n <= 1:
        return [0.5] * n
    total = sum(1 for o in outcomes if o)
    return [(total - (1 if o else 0)) / (n - 1) for o in outcomes]


def score(store: EventStore, predictor: Optional[str] = None) -> dict:
    """The kill criterion, made mechanical.

    Beat the leave-one-out base rate or the twin is theatre. `lift` is the
    fraction of the baseline's Brier score removed; it is the only number in
    this repository that decides whether the company is real.
    """
    recs = [
        r
        for r in ledger(store).values()
        if r["outcome"] is not None and (predictor is None or r["predictor"] == predictor)
    ]
    out: dict = {"predictor": predictor or "all", "n": len(recs)}
    if not recs:
        out["verdict"] = "no resolved predictions yet"
        return out

    recs.sort(key=lambda r: (r["resolver"], r["made_at"], r["id"]))

    by_resolver: Dict[str, List[dict]] = {}
    for r in recs:
        by_resolver.setdefault(r["resolver"], []).append(r)

    model_pairs, base_pairs = [], []
    per_resolver = {}
    for name, group in by_resolver.items():
        outcomes = [bool(r["outcome"]) for r in group]
        bases = _leave_one_out_base_rates(outcomes)
        mp = [(r["p"], o) for r, o in zip(group, outcomes)]
        bp = list(zip(bases, outcomes))
        model_pairs += mp
        base_pairs += bp
        per_resolver[name] = {
            "n": len(group),
            "base_rate": round(sum(outcomes) / len(outcomes), 4),
            "brier": round(brier(mp), 4),
            "baseline_brier": round(brier(bp), 4),
        }

    b_model = brier(model_pairs)
    b_base = brier(base_pairs)
    out.update(
        {
            "brier": round(b_model, 4),
            "baseline_brier": round(b_base, 4),
            "lift": round((b_base - b_model) / b_base, 4) if b_base > 0 else 0.0,
            "accuracy": round(
                sum(1 for p, o in model_pairs if (p >= 0.5) == o) / len(model_pairs), 4
            ),
            "per_resolver": per_resolver,
            "calibration": _calibration(model_pairs),
        }
    )
    out["verdict"] = "beats baseline" if b_model < b_base else "does not beat baseline"
    return out


def _calibration(pairs: Sequence[tuple], bins: int = 5) -> List[dict]:
    buckets: List[List[tuple]] = [[] for _ in range(bins)]
    for p, o in pairs:
        idx = min(int(p * bins), bins - 1)
        buckets[idx].append((p, o))
    rows = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        rows.append(
            {
                "band": f"{i / bins:.1f}-{(i + 1) / bins:.1f}",
                "n": len(b),
                "predicted": round(sum(p for p, _ in b) / len(b), 3),
                "observed": round(sum(1 for _, o in b if o) / len(b), 3),
            }
        )
    return rows
