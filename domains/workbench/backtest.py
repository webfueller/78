"""Does the preview know anything?

Walk a repository's history forward. At each edit, predict from what was
knowable *at that moment only* whether the file will need touching again inside
the window, then let history answer. Score against the leave-one-out base rate,
which is the same kill criterion the mail product is held to: beat a constant or
the number on the screen is decoration.

The models being scored are the ones the product uses, imported from `churn` and
not reimplemented here. That is deliberate and it is the single most expensive
lesson in this repository: a backtest with its own copy of the model measures a
program nobody ships.
"""

from __future__ import annotations

from typing import Dict

from takeback.ledger import brier, calibration, leave_one_out_base_rates
from takeback.store import TRUNK, EventStore

from .churn import CHURN_HORIZON, REGISTRY, moments
from .state import Tree


def run(
    store: EventStore,
    predictor: str = "hierarchical",
    horizon: int = CHURN_HORIZON,
    warmup_frac: float = 0.2,
    branch: str = TRUNK,
) -> dict:
    """Score one predictor over a repository's history."""
    if predictor not in REGISTRY:
        raise ValueError(f"unknown predictor: {predictor}")
    model = REGISTRY[predictor]

    tree = Tree.fold(store.read(branch))
    writes = sorted(tree.writes, key=lambda w: (w["ts"], w["path"]))
    if not writes:
        return {"predictor": predictor, "n": 0, "verdict": "no history"}

    span = writes[-1]["ts"] - writes[0]["ts"]
    cutoff = writes[0]["ts"] + int(span * warmup_frac)
    events = moments(tree, horizon=horizon)
    if not events:
        return {"predictor": predictor, "n": 0,
                "verdict": "no edit in this history has both a prediction and an answer"}

    pairs = []
    for i, e in enumerate(events):
        if e["at"] < cutoff:
            continue  # too early to be a fair test; still evidence for what follows
        # Only what had already been *settled* at the moment of the claim. An
        # edit made two days before this one has not finished its own window and
        # cannot be part of the evidence, or the predictor is reading the future.
        past = [p for p in events[:i] if p["at"] + horizon <= e["at"]]
        p = model(past, e["path"], horizon)
        pairs.append((min(max(p, 0.001), 0.999), e["churned"]))

    if not pairs:
        return {"predictor": predictor, "n": 0,
                "verdict": "nothing left to score after the warm-up"}

    outcomes = [o for _, o in pairs]
    base_pairs = list(zip(leave_one_out_base_rates(outcomes), outcomes))
    b_model, b_base = brier(pairs), brier(base_pairs)

    return {
        "predictor": predictor,
        "n": len(pairs),
        "horizon_days": round(horizon / 86400, 2),
        "base_rate": round(sum(1 for o in outcomes if o) / len(outcomes), 4),
        "brier": round(b_model, 4),
        "baseline_brier": round(b_base, 4),
        "lift": round((b_base - b_model) / b_base, 4) if b_base > 0 else 0.0,
        "accuracy": round(sum(1 for p, o in pairs if (p >= 0.5) == o) / len(pairs), 4),
        "calibration": calibration(pairs),
        "verdict": "beats baseline" if b_model < b_base else "does not beat baseline",
    }


def compare(store: EventStore, horizon: int = CHURN_HORIZON, **kw) -> Dict[str, dict]:
    """Every predictor, against the baseline and against each other.

    Two lifts, because they answer different questions and reporting only the
    first hides the more useful answer.

    `lift` is against the leave-one-out base rate: a constant, but a constant
    computed over the very period being scored, so it knows that period's average
    without having had to wait for it. On a repository whose habits changed over
    fourteen years that is a genuinely hard benchmark, and a causal predictor can
    lose to it badly while still being the best thing available on the day.

    `lift_vs_global` is against the running repository-wide rate -- the same
    information every one of these models had, at the same moment. That is the
    honest test of the only question the product actually asks: does knowing
    *which file* is being edited tell you anything beyond knowing the repository?
    """
    results = {name: run(store, predictor=name, horizon=horizon, **kw) for name in REGISTRY}
    reference = results.get("global", {}).get("brier")
    for out in results.values():
        if reference and out.get("brier") is not None:
            out["lift_vs_global"] = round((reference - out["brier"]) / reference, 4)
    return results
