"""A set of proposed edits becomes plans, and plans become futures.

An agent hands over what it wants to write. Nothing is written. Each edit gets a
risk number measured from this repository's own history -- how often edits to
that file were followed by a red build -- and those numbers become claims on the
ledger, made before anyone knows the answer.

The plans are deliberately few, because the decision is nearly always the same
one: all of it, the safe part of it, or none of it.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Dict, List, Optional, Sequence

from rehearsal import futures as F
from rehearsal.events import canonical
from rehearsal.store import TRUNK, EventStore

from . import churn, disk
from . import events as E
from .churn import CHURN_HORIZON
from .kernel import KERNEL
from .scoring import features
from .state import Tree

DAY = 24 * 3600

# A file with no history at all is assumed a bit risky rather than safe: four
# imaginary edits, of which about half of one went red. The alternative -- a
# fresh file scoring 0.00 -- would make the riskiest change in any change set
# look like the safest.
PRIOR_FAILS = 0.6
PRIOR_N = 4.0

# The build goes red for reasons that have nothing to do with you: somebody
# else's commit, a flaky test, an expired token. Until there is history to
# measure it from, call it one check in twenty.
BG_FAILS = 0.25
BG_N = 5.0

SAFE_MAX = 0.25   # what "the safe part of it" means
MAX_ACTIONS = 40


@dataclasses.dataclass
class Edit:
    path: str
    content: Optional[str]      # None means delete

    @property
    def kind(self) -> str:
        return E.FILE_DELETED if self.content is None else E.FILE_WRITTEN

    def payload(self) -> dict:
        if self.content is None:
            return {"prev_sha256": ""}
        return {"sha256": disk.sha(self.content), "content": self.content}

    def describe(self, tree: Tree) -> str:
        known = tree.files.get(self.path)
        if self.content is None:
            return f"Delete {self.path}"
        if known is None:
            return f"Create {self.path}"
        return f"Rewrite {self.path}"


def risk(tree: Tree, path: str) -> float:
    """How often an edit to this file was followed by a red build.

    Measured on this repository's own history, smoothed toward the population
    guess, and reported as it is -- there is no attempt to attribute a failure to
    one file in a change set that touched six. It is a base rate for "changes
    here go wrong", which is what it says.
    """
    fails = n = 0
    for w in tree.writes:
        if w["path"] != path:
            continue
        after = [c for c in tree.checks if c["ts"] > w["ts"]]
        if not after:
            continue
        n += 1
        fails += 0 if after[0]["ok"] else 1
    return (fails + PRIOR_FAILS) / (n + PRIOR_N)


def churn_risk(tree: Tree, path: str, at: Optional[int] = None,
               horizon: int = CHURN_HORIZON) -> float:
    """How likely this file is to need touching again, soon.

    A different question from `risk`, and it used to be answered with the same
    number -- which was wrong in a way nothing would have caught, because the
    probability that a file breaks the build is not the probability that it needs
    revising. The model is `churn.estimate`, which is the same code
    `experiment-004` scored and the only implementation there is.
    """
    return churn.estimate(tree, path, at=at, horizon=horizon)


def background_risk(tree: Tree) -> float:
    """How often the checks go red when nobody has changed anything.

    Without this, doing nothing scores as a guaranteed green build and every
    plan that touches a file is charged for a risk the do-nothing plan carries
    too. That is the same mistake as blaming a meeting for moving on the only
    plan that tried to protect it: the risk belongs to everyone or the
    comparison is rigged.
    """
    reds = n = 0
    previous = -1
    for c in tree.checks:
        if not any(previous < w["ts"] <= c["ts"] for w in tree.writes):
            n += 1
            reds += 0 if c["ok"] else 1
        previous = c["ts"]
    return (reds + BG_FAILS) / (n + BG_N)


def combined(risks: Sequence[float]) -> float:
    """Any of them going red. Independent, which is generous to the plan."""
    out = 1.0
    for r in risks:
        out *= 1.0 - r
    return round(1.0 - out, 6)


@dataclasses.dataclass
class Plan:
    id: str
    name: str
    rationale: str
    edits: List[Edit]


def build_plans(tree: Tree, edits: Sequence[Edit]) -> List[Plan]:
    plans = [Plan("hold", "Hold", "Write nothing. The baseline every other plan has to beat.", [])]
    if not edits:
        return plans

    plans.append(Plan(
        "apply", "Apply everything",
        f"Write all {len(edits)} " + ("file." if len(edits) == 1 else "files."),
        list(edits),
    ))

    safe = [e for e in edits if risk(tree, e.path) <= SAFE_MAX]
    if safe and len(safe) < len(edits):
        plans.append(Plan(
            "apply_safe", "Apply the quiet ones",
            f"Write the {len(safe)} of {len(edits)} whose files have not been breaking the "
            f"build, and leave the rest for a human.",
            safe,
        ))

    seen, unique = set(), []
    for plan in plans:
        signature = tuple(sorted((e.kind, e.path) for e in plan.edits))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(plan)
    return unique


def rehearse(
    store: EventStore,
    edits: Sequence[Edit],
    horizon_days: int = 1,
    now: Optional[int] = None,
    base: str = TRUNK,
) -> dict:
    """Fork, write the proposals into the fork, claim what could go wrong."""
    if len(edits) > MAX_ACTIONS:
        raise ValueError(
            f"{len(edits)} edits in one change set; {MAX_ACTIONS} is the most a person "
            "can actually read before committing, which is the only reason to preview at all"
        )
    now = store.now(base) if now is None else now
    horizon = horizon_days * DAY
    tree = Tree.fold(store.read(base))
    rid = "w_" + hashlib.sha256(
        canonical([base, now, horizon_days, sorted((e.path, e.content or "") for e in edits)])
        .encode("utf-8")
    ).hexdigest()[:12]

    background = background_risk(tree)
    plans_out = []
    for plan in build_plans(tree, edits):
        risks = [risk(tree, e.path) for e in plan.edits]
        red = combined([background] + risks)

        claims = [{
            "resolver": "check_fails", "params": {"plan": plan.id}, "p": red,
            "describe": "the checks go red after this",
        }]
        churn = [churn_risk(tree, e.path, at=now) for e in plan.edits]
        for e, c in zip(plan.edits, churn):
            claims.append({
                "resolver": "rewritten_within", "params": {"path": e.path}, "p": c,
                "describe": f"{e.path} needs touching again",
            })

        branch = f"{rid}_{plan.id}"
        if store.branch(branch) is None:
            store.fork(branch, base, note=f"change set {rid}: {plan.name}")
            for i, e in enumerate(plan.edits):
                store.append(branch=branch, kind=e.kind, entity=e.path,
                             actor=E.ACTOR_AGENT, ts=now + i + 1, payload=e.payload())
            for c in claims:
                KERNEL.ledger.record(
                    store, origin_branch=branch, resolver=c["resolver"], params=c["params"],
                    p=c["p"], claim=c["describe"], made_at=now, resolve_by=now + horizon,
                    predictor="workbench/per-path",
                )

        branches = []
        for k, future in enumerate(F.enumerate_futures([c["p"] for c in claims])):
            branches.append({
                "id": f"{branch}_{k}",
                "p": round(future["p"], 4),
                "count": future["count"],
                "outcomes": future["outcomes"],
                "reads": _reads(claims, future["outcomes"]),
            })

        expected = {
            "applied": len(plan.edits),
            "check_risk": red,
            "churn": round(sum(churn), 3),
        }
        plans_out.append({
            "id": plan.id,
            "branch": branch,
            "name": plan.name,
            "rationale": plan.rationale,
            "actions": [
                {"describe": e.describe(tree), "kind": e.kind, "entity": e.path,
                 "bytes": len(e.content.encode("utf-8")) if e.content else 0}
                for e in plan.edits
            ],
            "uncertain": [
                {"describe": c["describe"], "p": round(c["p"], 3)} for c in claims
            ],
            "branches": branches,
            "coverage": round(sum(b["p"] for b in branches), 4),
            "expected": expected,
            "state_hash": Tree.fold(store.read(branch)).state_hash(),
        })

    weights, provenance = KERNEL.preferences.effective_weights(store)
    for p_ in plans_out:
        p_["features"] = features(p_["expected"], len(p_["actions"]))
        p_["utility"] = round(KERNEL.preferences.utility(p_["features"], weights), 3)
    best = max(plans_out, key=lambda p_: p_["utility"])

    KERNEL.preferences.record_offer(store, rid, [
        {"branch": p_["branch"], "plan": p_["id"], "features": p_["features"]}
        for p_ in plans_out
    ], at=now)

    return {
        "change_set": rid,
        "base": base,
        "now": now,
        "horizon_days": horizon_days,
        "background_risk": round(background, 4),
        "weights": weights,
        "weights_from": provenance,
        "plans": plans_out,
        "recommended": best["id"],
    }


def _reads(claims: Sequence[dict], outcomes: Sequence[bool]) -> List[str]:
    hits = [c["describe"] for c, hit in zip(claims, outcomes) if hit]
    return hits or ["nothing goes wrong"]
