"""A repository with a known answer key.

Real git history tells you what a predictor scored. It cannot tell you what the
right answer was, because nobody knows the true churn rate of `server.py`. So the
harness is also run against a generated repository where every file's rate was
chosen in advance -- if the backtest cannot recover a ranking it was handed, its
verdict on real history is not worth reading.

The same reasoning, and the same seeded-world trick, as the mail product's
synthetic life.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from rehearsal.store import TRUNK, EventStore

from . import events as E

DAY = 24 * 3600

# Three kinds of file, because a repository is not homogeneous and a predictor
# that cannot tell them apart is the one being tested for.
SHAPES = [
    ("hot", 0.70, 0.30),    # in flux: touched often, revised almost every time
    ("warm", 0.30, 0.45),
    ("cold", 0.05, 0.25),   # settled: rarely touched, rarely revised
]

DIRS = ["src", "lib", "tests", "docs"]


def design(paths: int = 24, seed: int = 3) -> Dict[str, dict]:
    """The answer key: each path's true churn rate and how active it is."""
    rng = random.Random(seed)
    out: Dict[str, dict] = {}
    for i in range(paths):
        kind, churn, activity = SHAPES[i % len(SHAPES)]
        d = DIRS[i % len(DIRS)]
        out[f"{d}/mod_{i:02d}.py"] = {
            "kind": kind,
            "churn": round(min(0.95, max(0.02, rng.gauss(churn, 0.06))), 4),
            "activity": activity,
        }
    return out


def seed_repo(
    store: EventStore,
    days: int = 400,
    paths: int = 24,
    seed: int = 3,
    horizon: int = 7 * DAY,
    start: int = 1_600_000_000,
    branch: str = TRUNK,
) -> Dict[str, dict]:
    """Write a generated edit history onto a branch. Returns the answer key."""
    rng = random.Random(seed * 7919)
    key = design(paths=paths, seed=seed)
    weights = [key[p]["activity"] for p in key]
    names = list(key)

    # (timestamp, path) pairs, built ahead of time so a scheduled follow-up can
    # land wherever it belongs rather than only at the end.
    touches: List[tuple] = []
    t = start
    end = start + days * DAY
    while t < end:
        # About one edit a day across the whole repository. Deliberately sparse:
        # at three commits a day over twenty-four files, every file gets touched
        # again inside a week whatever its churn rate, the label saturates at 89%
        # and the experiment measures nothing but how busy the repository is.
        t += int(rng.expovariate(1.0 / DAY)) + 1
        if t >= end:
            break
        for path in rng.choices(names, weights=weights, k=1):
            touches.append((t, path))
            if rng.random() < key[path]["churn"]:
                # A revision, inside the window. This is the signal the backtest
                # is supposed to find.
                touches.append((t + rng.randint(DAY // 2, horizon - DAY // 2), path))

    touches = sorted((ts, p) for ts, p in touches if ts < end)
    with store.transaction():
        clock = store.now(branch)
        for ts, path in touches:
            ts = max(ts, clock)
            clock = ts
            store.append(
                branch=branch, kind=E.FILE_TOUCHED, entity=path, actor=E.ACTOR_WORLD,
                ts=ts, payload={"commit": "synthetic", "author": "synthetic"},
            )
    return key
