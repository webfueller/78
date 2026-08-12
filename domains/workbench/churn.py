"""How likely is this file to need touching again?

One module, because the product and the scoreboard must be asking the same
question with the same code. The mail product learned that twice, expensively:
its backtest measured "does a reply come within 48 hours of sending" while the
live claim asked "none has come yet -- does one come next", and the two agreed
about nothing. Here `estimate` is what the preview shows and what
`experiment-004` scored, and there is no second implementation to drift from it.

The models, worst to best-behaved:

  global          one rate for the whole repository
  per-dir         the top-level directory's rate
  per-path        this file's own rate, shrunk toward the repository's
  per-path-burst  per-path, plus whether this file is in the middle of a flurry
  hierarchical    the file, shrunk toward its directory, shrunk toward the whole
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

DAY = 24 * 3600
CHURN_HORIZON = 7 * DAY
SHRINK_N = 4.0

Predictor = Callable[[List[dict], str, int], float]


def _shrink(hits: int, n: int, toward: float, weight: float = SHRINK_N) -> float:
    """A rate, pulled toward the population it belongs to.

    The first version of this pulled every rate toward a fixed 0.25, which is a
    reasonable-looking number and was wrong in a way the synthetic repository
    made obvious: where nine edits in ten are followed by another within the
    week, a file with no history is not a quarter likely to churn. Shrinking
    toward the running rate rather than a constant is the difference between a
    per-path model that beats the base rate and one that loses to it by 25%.
    """
    return (hits + toward * weight) / (n + weight)


def _top(path: str) -> str:
    return path.split("/")[0] if "/" in path else ""


# ---------------------------------------------------------------- the models


def global_rate(past: List[dict], path: str, horizon: int) -> float:
    hits = sum(1 for w in past if w["churned"])
    return _shrink(hits, len(past), toward=0.5, weight=2.0)


def per_dir(past: List[dict], path: str, horizon: int) -> float:
    top = _top(path)
    mine = [w for w in past if _top(w["path"]) == top]
    hits = sum(1 for w in mine if w["churned"])
    return _shrink(hits, len(mine), toward=global_rate(past, path, horizon))


def per_path(past: List[dict], path: str, horizon: int) -> float:
    mine = [w for w in past if w["path"] == path]
    hits = sum(1 for w in mine if w["churned"])
    return _shrink(hits, len(mine), toward=global_rate(past, path, horizon))


def per_path_burst(past: List[dict], path: str, horizon: int) -> float:
    """Per-path, plus how recently this file was last touched.

    A file edited three times this week is in the middle of something; a file
    edited once, six months ago, is not. The same shape as the mail product's
    `per-contact-age`, which was the one predictor there that beat its own
    per-contact baseline.
    """
    mine = [w for w in past if w["path"] == path]
    if not mine:
        return per_dir(past, path, horizon)
    base = per_path(past, path, horizon)
    recent = [w for w in mine if w["at"] >= mine[-1]["at"] - horizon]
    if len(recent) < 2:
        return base
    burst = _shrink(sum(1 for w in recent if w["churned"]), len(recent), toward=base)
    return round((burst + base) / 2.0, 6)


def hierarchical(past: List[dict], path: str, horizon: int) -> float:
    """The file, shrunk toward its directory, shrunk toward the repository.

    Both real repositories in experiment 004 say the directory carries more
    signal than the file: `src/` churns, `docs/` does not, and which of two files
    in `src/` you picked matters less than that. The synthetic repository says
    the opposite, because its directories were assigned round-robin and carry
    nothing.

    Shrinking through both levels is right in both worlds rather than tuned to
    either -- where directories are informative it inherits them, and where they
    are noise the directory estimate simply equals the global one and nothing is
    lost. It is not the best model on either real repository. It is the one that
    is never the worst, which is what a model pointed at an unknown repository
    should be.
    """
    mine = [w for w in past if w["path"] == path]
    hits = sum(1 for w in mine if w["churned"])
    return _shrink(hits, len(mine), toward=per_dir(past, path, horizon))


REGISTRY: Dict[str, Predictor] = {
    "global": global_rate,
    "per-dir": per_dir,
    "per-path": per_path,
    "per-path-burst": per_path_burst,
    "hierarchical": hierarchical,
}

DEFAULT = "hierarchical"


# ------------------------------------------------------------------ the walk


def churned(writes: Sequence[dict], path: str, after: int, horizon: int) -> bool:
    return any(after < w["ts"] <= after + horizon and w["path"] == path for w in writes)


def moments(tree, horizon: int = CHURN_HORIZON) -> List[dict]:
    """Every edit that can be both predicted and marked, in order.

    An edit is usable when the whole window after it lies inside the history --
    otherwise "was it touched again" is not a fact about the world, it is a fact
    about where the export stopped.

    Note what this does *not* do: filter by warm-up. An early edit is poor
    material to be scored on and perfectly good material to learn from, and the
    first version of the backtest conflated the two -- it withheld the opening
    fifth of the history from the predictors as well as from the scoreboard, then
    reported that per-path knowledge was worth about three percent. Most of what
    it knew had been thrown away before it was asked.
    """
    writes = sorted(tree.writes, key=lambda w: (w["ts"], w["path"]))
    if not writes:
        return []
    end = writes[-1]["ts"]
    return [
        {"at": w["ts"], "path": w["path"],
         "churned": churned(writes, w["path"], w["ts"], horizon)}
        for w in writes
        if w["ts"] + horizon <= end
    ]


def estimate(
    tree,
    path: str,
    at: Optional[int] = None,
    horizon: int = CHURN_HORIZON,
    model: str = DEFAULT,
) -> float:
    """The number the preview shows, from the history available at `at`.

    Only edits whose own window has closed are evidence. An edit made yesterday
    has not had a week to be revised, and letting it vote "no churn" would drag
    every rate toward zero -- the censoring bug the mail product's backtest had
    to be rebuilt to avoid.
    """
    now = tree.clock if at is None else at
    past = [m for m in moments(tree, horizon) if m["at"] + horizon <= now]
    return REGISTRY[model](past, path, horizon)
