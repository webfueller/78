"""Learning the scoring weights from what you actually commit.

The recommendation is a weighted sum, and until now the weights were four
numbers I picked. Every commit is a revealed preference -- this plan, out of the
five that were on the table -- which is exactly the data a discrete-choice model
eats. So the weights stop being an opinion and become a measurement.

The shape of it is a conditional logit: for a rehearsal offering options S where
option c was committed,

    P(c) = exp(w·x_c) / sum_j exp(w·x_j)

fitted by gradient ascent on the log-likelihood with a ridge pulling w toward the
hand-picked prior. The ridge is not decoration. With three commits you cannot fit
four weights, and a model that will happily tell you it has learned something
from three observations is worse than the guess it replaced.

Two rules keep it honest, and they are the same two the backtest uses:

  Nothing is learned until there are enough choices to learn from.
  Learned weights are used only if they beat the hand-picked ones at predicting
  choices they were not fitted on -- leave-one-out, every time, no exceptions.

Fail either and the product keeps the guess and says it is a guess.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import events as E
from .store import EventStore

PREFERENCES = "preferences"

OFFERED = "choice.offered"
CHOSEN = "choice.made"

# The features a plan is judged on, and the numbers I guessed for them. These
# stay as the prior the fit is pulled toward, so the model degrades to my guess
# rather than to nonsense.
PRIOR: Dict[str, float] = {
    "reply": 1.0,
    "per_action": -0.25,
    "burn_saved_per_1000c": 0.5,
    "late_surprise": -1.0,
    # A reply is not a reply. These two say which replies were worth having:
    # money the thread names, and whether it has a clock on it. Both are guesses
    # like the four above and both are fitted from what you commit. Setting
    # value_at_risk_k to 1.0 says a thousand euros in play is worth about one
    # answered nudge, which is a starting point and nothing more.
    "value_at_risk_k": 1.0,
    "deadline_pressure": 0.75,
}
KEYS: Tuple[str, ...] = tuple(sorted(PRIOR))

# Ridge strength, chosen so a single choice cannot move a weight appreciably and
# roughly ten start to. The gate below is what actually protects the user; this
# only keeps the fit from thrashing on the way there.
RIDGE = 2.0
MIN_CHOICES = 8


def features(expected: dict, actions: int) -> Dict[str, float]:
    """A plan as the four numbers the scoring cares about."""
    return {
        "reply": float(expected["replies"]),
        "per_action": float(actions),
        "burn_saved_per_1000c": float(expected["burn_saved_cents"]) / 1000.0,
        "late_surprise": float(expected["late_surprises"]),
        "value_at_risk_k": float(expected.get("value_at_risk_k", 0.0)),
        "deadline_pressure": float(expected.get("deadline_pressure", 0.0)),
    }


def utility(f: Dict[str, float], weights: Dict[str, float]) -> float:
    return sum(weights.get(k, 0.0) * f.get(k, 0.0) for k in KEYS)


# --------------------------------------------------------------------- writing


def record_offer(store: EventStore, rehearsal: str, options: Sequence[dict], at: int) -> bool:
    """What was on the table. Written when the rehearsal is produced, not later.

    Recording the alternatives at offer time is the whole point: a choice is only
    evidence relative to what was rejected, and reconstructing that afterwards
    from a committed branch would mean guessing at what the person was looking at.
    """
    store.root(PREFERENCES, note="what was offered, and what was chosen")
    if any(e.entity == rehearsal and e.kind == OFFERED for e in store.read(PREFERENCES)):
        return False  # the same rehearsal, offered again; it is one offer
    store.append(
        branch=PREFERENCES, kind=OFFERED, entity=rehearsal, actor=E.ACTOR_AGENT,
        ts=at, allow_backdate=True,
        payload={"options": [
            {"branch": o["branch"], "plan": o["plan"], "features": o["features"]}
            for o in options
        ]},
    )
    return True


def record_choice(store: EventStore, branch: str, at: int) -> Optional[str]:
    """Note that a future was committed, if it belongs to a rehearsal we offered."""
    if store.branch(PREFERENCES) is None:
        return None
    row = store.branch(branch)
    candidates = {branch, row["parent"] if row else None} - {None}

    events = store.read(PREFERENCES)
    offers = {e.entity: e.payload for e in events if e.kind == OFFERED}
    already = {e.entity for e in events if e.kind == CHOSEN}

    for rehearsal, payload in offers.items():
        if rehearsal in already:
            continue
        for option in payload["options"]:
            if option["branch"] in candidates:
                store.append(
                    branch=PREFERENCES, kind=CHOSEN, entity=rehearsal, actor=E.ACTOR_USER,
                    ts=at, allow_backdate=True,
                    payload={"branch": option["branch"], "plan": option["plan"]},
                )
                return rehearsal
    return None


def decline(store: EventStore, branch: str, at: Optional[int] = None) -> dict:
    """Record that the week was left alone, on purpose.

    Deciding to do nothing is a choice, and often the most informative one -- but
    it cannot be committed, because there is nothing to execute. Without a way to
    record it the ledger only ever sees the weeks somebody acted, and weights
    fitted to that would drift toward action no matter what the person actually
    wants. This is that path.
    """
    at = store.now() if at is None else at
    rehearsal = record_choice(store, branch, at)
    if rehearsal is None:
        raise ValueError(f"{branch} is not an option in any rehearsal on record")
    store.set_status(branch, "declined")
    return {"declined": branch, "rehearsal": rehearsal, "at": at}


def choices(store: EventStore) -> List[dict]:
    """Every completed (what was offered, what was taken) pair."""
    if store.branch(PREFERENCES) is None:
        return []
    events = store.read(PREFERENCES)
    offers = {e.entity: e.payload["options"] for e in events if e.kind == OFFERED}
    out = []
    for e in events:
        if e.kind != CHOSEN or e.entity not in offers:
            continue
        options = offers[e.entity]
        taken = next((i for i, o in enumerate(options) if o["branch"] == e.payload["branch"]), None)
        if taken is None or len(options) < 2:
            continue  # a choice between one thing is not a choice
        out.append({"rehearsal": e.entity, "options": options, "chosen": taken, "at": e.ts})
    return out


# --------------------------------------------------------------------- fitting


def _softmax(scores: Sequence[float]) -> List[float]:
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def fit(
    data: Sequence[dict],
    prior: Optional[Dict[str, float]] = None,
    ridge: float = RIDGE,
    steps: int = 600,
    rate: float = 0.05,
) -> Dict[str, float]:
    """Conditional logit by gradient ascent. Four parameters; this is not deep."""
    prior = dict(prior or PRIOR)
    w = dict(prior)
    if not data:
        return w

    for _ in range(steps):
        grad = {k: -ridge * (w[k] - prior[k]) for k in KEYS}
        for row in data:
            xs = [o["features"] for o in row["options"]]
            probs = _softmax([utility(x, w) for x in xs])
            picked = xs[row["chosen"]]
            for k in KEYS:
                grad[k] += picked.get(k, 0.0) - sum(p * x.get(k, 0.0) for p, x in zip(probs, xs))
        norm = math.sqrt(sum(g * g for g in grad.values())) or 1.0
        scale = rate * min(1.0, 4.0 / norm)  # a long step on a steep slope diverges
        for k in KEYS:
            w[k] += scale * grad[k]
    return w


def log_loss(data: Sequence[dict], weights: Dict[str, float]) -> float:
    if not data:
        return float("nan")
    total = 0.0
    for row in data:
        probs = _softmax([utility(o["features"], weights) for o in row["options"]])
        total -= math.log(max(probs[row["chosen"]], 1e-12))
    return total / len(data)


def top1(data: Sequence[dict], weights: Dict[str, float]) -> float:
    if not data:
        return float("nan")
    hits = 0
    for row in data:
        scores = [utility(o["features"], weights) for o in row["options"]]
        hits += scores.index(max(scores)) == row["chosen"]
    return hits / len(data)


def _norm(w: Dict[str, float]) -> float:
    return math.sqrt(sum(w[k] * w[k] for k in KEYS))


def chance_top1(data: Sequence[dict]) -> float:
    """What guessing would score, given how many options each choice had."""
    if not data:
        return float("nan")
    return sum(1.0 / len(row["options"]) for row in data) / len(data)


def evaluate(data: Sequence[dict], prior: Optional[Dict[str, float]] = None) -> dict:
    """Leave-one-out: refit without each choice, then predict it.

    Scoring the fit on the choices it was fitted to would show the learned
    weights winning from the first commit, which is precisely the lie this is
    built to avoid.
    """
    prior = dict(prior or PRIOR)
    n = len(data)
    if n < 2:
        return {"n": n, "learned_log_loss": float("nan"), "prior_log_loss": float("nan"),
                "learned_top1": float("nan"), "prior_top1": float("nan"), "beats_prior": False}

    learned_loss = prior_loss = 0.0
    learned_hits = prior_hits = 0
    for i in range(n):
        held = [data[i]]
        rest = data[:i] + data[i + 1:]
        w = fit(rest, prior=prior)
        learned_loss += log_loss(held, w)
        prior_loss += log_loss(held, prior)
        learned_hits += top1(held, w)
        prior_hits += top1(held, prior)

    return {
        "n": n,
        "learned_log_loss": round(learned_loss / n, 4),
        "prior_log_loss": round(prior_loss / n, 4),
        "learned_top1": round(learned_hits / n, 4),
        "prior_top1": round(prior_hits / n, 4),
        "chance_top1": round(chance_top1(data), 4),
        "beats_prior": learned_loss < prior_loss,
    }


def effective_weights(store: EventStore) -> Tuple[Dict[str, float], dict]:
    """The weights to actually score with, and an honest account of where from."""
    data = choices(store)
    provenance = {
        "source": "hand-picked",
        "choices": len(data),
        "needed": MIN_CHOICES,
        "why": "",
    }

    if len(data) < MIN_CHOICES:
        provenance["why"] = (
            f"{len(data)} of {MIN_CHOICES} commits recorded — not enough to learn from yet"
        )
        return dict(PRIOR), provenance

    verdict = evaluate(data)
    provenance.update({k: v for k, v in verdict.items() if k != "n"})
    if not verdict["beats_prior"]:
        provenance["why"] = (
            "learned weights did not beat the hand-picked ones on choices they had "
            "not seen, so they are not being used"
        )
        return dict(PRIOR), provenance

    learned = fit(data)
    # Beating the guess two ways looks identical in the log-loss and means
    # opposite things. Finding a preference is one. Discovering the four numbers
    # do not explain this person at all -- and so flattening toward indifference
    # rather than staying confidently wrong -- is the other. Saying "learned your
    # preference" for the second would be the most flattering lie in the product.
    confidence = _norm(learned) / (_norm(PRIOR) or 1.0)
    informative = verdict["learned_top1"] > verdict["chance_top1"] and confidence >= 0.5

    provenance.update({
        "source": "learned",
        "character": "preference" if informative else "indifference",
        "confidence": round(confidence, 3),
        "prior": dict(PRIOR),
        "moved": {k: round(learned[k] - PRIOR[k], 3) for k in KEYS},
        "why": (
            f"fitted to {len(data)} decisions; picks the plan you took "
            f"{round(verdict['learned_top1'] * 100)}% of the time against "
            f"{round(verdict['prior_top1'] * 100)}% for the hand-picked weights"
        ) if informative else (
            f"your {len(data)} decisions are not well explained by these four numbers, so the "
            f"scoring has been flattened toward indifference rather than left confidently wrong — "
            f"it now picks your plan {round(verdict['learned_top1'] * 100)}% of the time against "
            f"{round(verdict['chance_top1'] * 100)}% for a coin toss"
        ),
    })
    return {k: round(v, 4) for k, v in learned.items()}, provenance
