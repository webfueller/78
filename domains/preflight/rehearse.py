"""The rehearsal: run the week before you live it.

A mandate becomes a small set of plans. Each plan becomes real agent-authored
events inside a fork. Each plan then branches on what the people involved do --
and those responses are drawn from the calibrated per-contact model, not from
imagination, so the probabilities on the branch map are the same numbers the
backtest scored.

Futures are enumerated, not sampled. The uncertain events in a plan are
independent binary outcomes with known probabilities, so the joint outcomes can
be ranked exactly and the top few shown with an honest coverage figure. Two
people running the same rehearsal against the same twin get the same map.

On simulated people: a counterparty event records a *response class* -- replied,
stayed silent, moved the meeting -- with its probability and expected timing. It
never fabricates a named person's words. The body text is a visible placeholder,
the actor is prefixed `sim:`, and the store refuses to promote it. That is the
live wire from the memo, wired shut.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Dict, List, Optional, Sequence, Tuple

from takeback import futures as F

from . import events as E
from . import predictions as P
from . import preferences as P_PREF
from . import stakes as S
from .predictors import REGISTRY, PerContactAge, Predictor
from takeback.store import TRUNK, EventStore
from .world import Thread, World, project

HOUR = 3600
DAY = 24 * HOUR

# How the recommendation is scored. The numbers here are a guess; `preferences`
# replaces them with weights fitted to what you actually commit, once there are
# enough commits and the fit beats the guess on choices it has not seen. Either
# way the weights and their provenance go out in the payload, because a
# recommendation whose scoring is hidden is just an assertion.
WEIGHTS = dict(P_PREF.PRIOR)

MAX_ACTIONS = 8       # a plan a person can read
MAX_BRANCHES = 5      # futures shown per plan
LATE = DAY            # a change inside this window before the event is a surprise
MOVE_EVIDENCE = 8     # meetings-with-a-move below which move rates mean little


@dataclasses.dataclass
class Action:
    kind: str
    entity: str
    payload: dict
    describe: str
    offset: int  # seconds after the rehearsal starts


@dataclasses.dataclass
class Uncertainty:
    """A binary thing a person does, with the probability the model gives it.

    `at` is when it would happen; `discovered_at` is when you would find out.
    Those are the same thing until a plan does something to separate them, which
    is the entire value of pre-confirming a meeting: it cannot stop a
    counterparty from moving it, and claiming otherwise would be inventing an
    effect the data does not support. What it can do is surface the move a week
    early instead of on the morning.
    """
    resolver: str
    params: dict
    p: float
    describe: str
    entity: str
    contact: str
    at: int
    resolve_by: int
    discovered_at: int = 0
    deadline: int = 0  # when it would bite: a meeting's start

    def __post_init__(self):
        if not self.discovered_at:
            self.discovered_at = self.at

    @property
    def late(self) -> bool:
        return bool(self.deadline) and self.discovered_at > self.deadline - LATE


@dataclasses.dataclass
class Plan:
    id: str
    name: str
    rationale: str
    actions: List[Action]
    uncertain: List[Uncertainty]


# ------------------------------------------------------------------- observing


def median_latency(w: World, contact: str, default: int = 36 * HOUR) -> int:
    seen: List[int] = []
    for t in w.threads.values():
        if t.counterparty != contact:
            continue
        for i, m in enumerate(t.messages):
            if m["direction"] != "out":
                continue
            for n in t.messages[i + 1 :]:
                if n["direction"] == "out":
                    break
                if n["sender"] == contact:
                    seen.append(n["ts"] - m["ts"])
                    break
    return sorted(seen)[len(seen) // 2] if seen else default


def _name(w: World, cid: str) -> str:
    return w.contacts.get(cid, {}).get("name", cid)


def _stale_threads(w: World, now: int, min_age: int, take: int = MAX_ACTIONS) -> List[Thread]:
    open_ = [t for t in w.open_threads() if now - t.last_ts >= min_age]
    open_.sort(key=lambda t: t.last_ts)  # the ones rotting longest, first
    return open_[:take]


def _idle_subscriptions(w: World, now: int, quiet_for: int = 90 * DAY) -> List[str]:
    """Subscriptions with no conversation touching their merchant in a long time."""
    spoken = {
        word.lower()
        for t in w.threads.values()
        if t.last_ts >= now - quiet_for
        for word in t.subject.split()
    }
    idle = [
        s for s in w.subscriptions.values()
        if s.state == "active" and s.merchant.split()[0].lower() not in spoken
    ]
    idle.sort(key=lambda s: -s.amount_cents)
    return [s.id for s in idle[:3]]


# --------------------------------------------------------------------- mandate


def build_plans(w: World, now: int, mandate: Sequence[str], horizon: int,
                model: Optional[Predictor] = None, min_stale: int = 2 * DAY) -> List[Plan]:
    """Turn a mandate into a handful of plans worth comparing."""
    model = model or REGISTRY["per-contact-age"]
    quotes = {c["params"].get("thread") or c["params"].get("meeting"): c
              for c in model.predict(w, at_ts=now, horizon=horizon)}

    def reply_odds(t: Thread, at: int) -> float:
        """The model's number for 'they answer a fresh nudge within the window'."""
        fresh = model.predict(_nudged(w, t, at), at_ts=at, horizon=horizon)
        for c in fresh:
            if c["params"].get("thread") == t.id:
                return c["p"]
        return 0.2

    plans: List[Plan] = []

    # Always compare against doing nothing. It is not committable -- it proposes
    # no action -- but a plan that cannot beat silence should not be run.
    plans.append(Plan("hold", "Hold", "Change nothing. The baseline every other plan has to beat.", [], []))

    # A wider pool than any one plan will use: chasing the oldest threads and
    # chasing the ones that matter are different shortlists, and the second is
    # only possible if the candidates were not already truncated by age.
    pool = _stale_threads(w, now, min_age=min_stale, take=MAX_ACTIONS * 3)
    stale = pool[:MAX_ACTIONS]
    if "chase" in mandate and stale:
        actions, uncertain = [], []
        for i, t in enumerate(stale):
            at = now + (i + 1) * HOUR
            actions.append(Action(
                kind=E.MESSAGE_SENT, entity=t.id,
                payload={"sender": "me", "counterparty": t.counterparty,
                         "subject": t.subject,
                         "body": f"Following up on {t.subject} — do you have a view this week?"},
                describe=f"Nudge {_name(w, t.counterparty)} on “{t.subject}”",
                offset=at - now,
            ))
            uncertain.append(_reply_uncertainty(w, t, at, horizon, reply_odds(t, at)))
        one = len(stale) == 1
        plans.append(Plan(
            "chase",
            "Send the follow-up" if one else "Chase everything",
            "Nudge the one thread that has gone quiet." if one else
            f"Nudge all {len(stale)} threads that have gone quiet. Maximum movement, maximum noise.",
            actions, uncertain,
        ))

        # The same plan, restricted to the people who actually answer. Fewer
        # messages sent, most of the replies still collected.
        likely = sorted(stale, key=lambda t: -reply_odds(t, now + HOUR))[: max(2, len(stale) // 2)]
        actions, uncertain = [], []
        for i, t in enumerate(likely):
            at = now + (i + 1) * HOUR
            actions.append(Action(
                kind=E.MESSAGE_SENT, entity=t.id,
                payload={"sender": "me", "counterparty": t.counterparty,
                         "subject": t.subject,
                         "body": f"Following up on {t.subject} — do you have a view this week?"},
                describe=f"Nudge {_name(w, t.counterparty)} on “{t.subject}”",
                offset=at - now,
            ))
            uncertain.append(_reply_uncertainty(w, t, at, horizon, reply_odds(t, at)))
        plans.append(Plan(
            "chase_likely", "Chase who answers",
            f"Nudge only the {len(likely)} "
            f"{'person' if len(likely) == 1 else 'people'} the model expects to reply. "
            "Same week, less noise.",
            actions, uncertain,
        ))

    if "prune" in mandate:
        idle = _idle_subscriptions(w, now)
        if idle:
            base = plans[-1] if plans[-1].id.startswith("chase") else plans[0]
            actions = list(base.actions)
            for j, sid in enumerate(idle):
                s = w.subscriptions[sid]
                actions.append(Action(
                    kind=E.SUBSCRIPTION_CANCELLED, entity=sid,
                    payload={"reason": "no related activity in 90 days",
                             "merchant": s.merchant, "amount_cents": s.amount_cents},
                    describe=f"Cancel {s.merchant} ({s.amount_cents / 100:.2f}/mo)",
                    offset=(len(actions) + 1) * HOUR,
                ))
            plans.append(Plan(
                "prune", f"{base.name} + prune",
                f"{base.rationale.rstrip('.')}, and cancel {len(idle)} "
                f"{'subscription' if len(idle) == 1 else 'subscriptions'} "
                "nothing has referred to in 90 days.",
                actions, list(base.uncertain),
            ))

        # And the shortlist a person would actually write themselves: the threads
        # carrying money or a clock, whether or not the other party is a reliable
        # correspondent. One answer on a deposit is not one answer about padel.
        def worth(t: Thread) -> float:
            st = S.read(t)
            return reply_odds(t, now + HOUR) * (st.money_k + st.pressure(now, horizon))

        valuable = [t for t in sorted(pool, key=worth, reverse=True) if worth(t) > 0][:MAX_ACTIONS]
        if valuable:
            actions, uncertain = [], []
            for i, t in enumerate(valuable):
                at = now + (i + 1) * HOUR
                actions.append(Action(
                    kind=E.MESSAGE_SENT, entity=t.id,
                    payload={"sender": "me", "counterparty": t.counterparty,
                             "subject": t.subject,
                             "body": f"Following up on {t.subject} — do you have a view this week?"},
                    describe=f"Nudge {_name(w, t.counterparty)} on “{t.subject}”",
                    offset=at - now,
                ))
                uncertain.append(_reply_uncertainty(w, t, at, horizon, reply_odds(t, at)))
            carried = sum(S.read(t).money_cents for t in valuable) / 100.0
            plans.append(Plan(
                "chase_valuable", "Chase what is at stake",
                f"Nudge the {len(valuable)} quiet threads carrying money or a deadline "
                f"— about {carried:,.0f} in play — regardless of who answers reliably.",
                actions, uncertain,
            ))

    # Meetings move whether or not you do anything, so the risk belongs to every
    # plan. Loading it onto the one plan that addresses it would make the plan
    # that addresses it look the worst.
    at_risk = [
        (m, quotes[m.id]["p"]) for m in w.meetings.values()
        if m.id in quotes and quotes[m.id]["p"] >= 0.25
    ]
    exposure = [
        Uncertainty(
            resolver="meeting_moves", params={"meeting": m.id}, p=p,
            describe=f"“{m.title}” moves", entity=m.id,
            contact=next((a for a in m.attendees if a != "me"), ""),
            at=m.start - 12 * HOUR, resolve_by=m.start, deadline=m.start,
        )
        for m, p in at_risk
    ]
    for plan in plans:
        plan.uncertain.extend(exposure)

    if "defend" in mandate and at_risk:
        base = plans[-1]
        actions = list(base.actions)
        confirmed = []
        for i, (m, p) in enumerate(at_risk):
            other = next((a for a in m.attendees if a != "me"), "")
            at = now + (len(actions) + 1) * HOUR
            actions.append(Action(
                kind=E.MESSAGE_SENT, entity=f"th_confirm_{m.id}",
                payload={"sender": "me", "counterparty": other,
                         "subject": f"Confirming: {m.title}",
                         "body": "Still good for this? Happy to move it now rather than on the day."},
                describe=f"Pre-confirm “{m.title}” with {_name(w, other)}",
                offset=at - now,
            ))
            # Same move, same odds -- found out a week earlier.
            confirmed.append(dataclasses.replace(
                exposure[i], discovered_at=min(at + median_latency(w, other), m.start)
            ))
        plans.append(Plan(
            "defend", f"{base.name} + defend calendar",
            f"{base.rationale.rstrip('.')}, and pre-confirm {len(at_risk)} "
            f"{'meeting' if len(at_risk) == 1 else 'meetings'} the model flags as likely to "
            "move — the move still happens, you just stop finding out on the day.",
            actions,
            [u for u in base.uncertain if u.resolver != "meeting_moves"] + confirmed,
        ))

    # Two plans that do exactly the same things are one plan. On a twin with a
    # single thread, "chase everything" and "chase who answers" collapse, and
    # showing both would suggest a choice that is not there.
    seen, unique = set(), []
    for plan in plans:
        signature = tuple(sorted((a.kind, a.entity) for a in plan.actions))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(plan)
    return unique


def _nudged(w: World, t: Thread, at: int) -> World:
    """The world as it would be a moment after we send the follow-up."""
    shadow = project([])
    shadow.contacts = w.contacts
    shadow.threads = dict(w.threads)
    shadow.meetings = w.meetings
    shadow.subscriptions = w.subscriptions
    fresh = Thread(id=t.id, subject=t.subject, counterparty=t.counterparty,
                   messages=t.messages + [{"ts": at, "direction": "out", "sender": "me",
                                           "actor": E.ACTOR_AGENT, "simulated": False, "body": ""}])
    shadow.threads[t.id] = fresh
    shadow.clock = at
    return shadow


def _reply_uncertainty(w: World, t: Thread, at: int, horizon: int, p: float) -> Uncertainty:
    return Uncertainty(
        resolver="reply_within",
        params={"thread": t.id, "contact": t.counterparty},
        p=p,
        describe=f"{_name(w, t.counterparty)} replies on “{t.subject}”",
        entity=t.id,
        contact=t.counterparty,
        at=at + median_latency(w, t.counterparty),
        resolve_by=at + horizon,
    )


# ------------------------------------------------------------------- enumerate


def enumerate_futures(unc: Sequence[Uncertainty], keep: int = MAX_BRANCHES) -> List[dict]:
    """How many of these land, and which -- exactly, from the kernel.

    The uncertainties carry a great deal of mail-specific baggage; the
    enumeration needs one number from each of them and nothing else, which is
    why the arithmetic lives in `takeback.futures` and this is the unwrapping.
    """
    return F.enumerate_futures([u.p for u in unc], keep=keep)


# --------------------------------------------------------------------- execute


def rehearse(
    store: EventStore,
    mandate: Sequence[str] = ("chase", "prune", "defend"),
    horizon_days: int = 7,
    now: Optional[int] = None,
    base: str = TRUNK,
    reply_prior: Optional[float] = None,
    move_prior: Optional[float] = None,
    min_stale: int = 2 * DAY,
) -> dict:
    """Run the week. Returns the branch map."""
    now = store.now(base) if now is None else now
    horizon = horizon_days * DAY
    w = project(store.read(base))
    model = PerContactAge(reply_prior=reply_prior, move_prior=move_prior)
    rid = "r_" + hashlib.sha256(
        E.canonical([base, now, sorted(mandate), horizon_days, reply_prior, min_stale]).encode()
    ).hexdigest()[:12]

    plans_out = []
    for plan in build_plans(w, now, mandate, horizon, model=model, min_stale=min_stale):
        # The rehearsal id is a hash of its inputs, so running the same rehearsal
        # again must read the existing one back rather than write it twice --
        # otherwise a page refresh sends every follow-up a second time.
        plan_branch = f"{rid}_{plan.id}"
        if store.branch(plan_branch) is None:
            store.fork(plan_branch, base, note=f"rehearsal {rid}: {plan.name}")

            for a in sorted(plan.actions, key=lambda a: a.offset):
                store.append(branch=plan_branch, kind=a.kind, entity=a.entity,
                             actor=E.ACTOR_AGENT, ts=now + a.offset, payload=a.payload)

            # The claims this plan is making, on the ledger, now -- not after
            # anyone has seen whether they were right.
            for u in plan.uncertain:
                P.record(store, origin_branch=plan_branch, resolver=u.resolver,
                         params=u.params, p=u.p, claim=u.describe, made_at=now,
                         resolve_by=u.resolve_by, predictor="takeback/per-contact-age")

        branches = []
        for k, future in enumerate(enumerate_futures(plan.uncertain)):
            bname = f"{plan_branch}_{k}"
            if store.branch(bname) is None:
                store.fork(bname, plan_branch, note=f"future {k}")
                pending = []
                for u, hit in zip(plan.uncertain, future["outcomes"]):
                    if not hit:
                        continue
                    if u.resolver == "reply_within":
                        pending.append((u.at, E.MESSAGE_RECEIVED, u.entity, f"{E.SIM_PREFIX}{u.contact}",
                                        {"sender": u.contact, "counterparty": u.contact,
                                         "subject": "", "body": "[simulated: a reply lands here]"}))
                    elif u.resolver == "meeting_moves":
                        m = w.meetings[u.entity]
                        pending.append((u.at, E.CALENDAR_MOVED, u.entity, f"{E.SIM_PREFIX}{u.contact}",
                                        {"start": m.start + 2 * DAY, "end": m.end + 2 * DAY}))
                for ts, kind, entity, actor, payload in sorted(pending):
                    store.append(branch=bname, kind=kind, entity=entity, actor=actor,
                                 ts=max(ts, store.now(bname)), payload=payload)
            branches.append({
                "id": bname,
                "p": round(future["p"], 4),
                "outcomes": future["outcomes"],
                "metrics": _metrics(store, bname, w, plan, future["outcomes"]),
                "state_hash": project(store.read(bname)).state_hash(),
            })

        plans_out.append({
            "id": plan.id,
            "branch": plan_branch,
            "name": plan.name,
            "rationale": plan.rationale,
            "actions": [{"describe": a.describe, "kind": a.kind, "entity": a.entity,
                         "at": now + a.offset} for a in sorted(plan.actions, key=lambda a: a.offset)],
            "uncertain": [{"describe": u.describe, "p": round(u.p, 3), "at": u.at,
                           "contact": u.contact} for u in plan.uncertain],
            "branches": branches,
            "coverage": round(sum(b["p"] for b in branches), 4),
            "expected": _expected(plan, w, now, horizon),
        })

    supported = _mandate_support(w, mandate)
    weights, provenance = P_PREF.effective_weights(store)
    for p_ in plans_out:
        p_["features"] = P_PREF.features(p_["expected"], len(p_["actions"]))
        p_["utility"] = round(_utility(p_["expected"], len(p_["actions"]), weights), 3)
    best = max(plans_out, key=lambda p_: p_["utility"])
    sensitivity = _sensitivity(plans_out, weights, best["id"])

    # What was on the table, recorded now. A commit later turns this into one
    # observation of what this person actually values.
    P_PREF.record_offer(store, rid, [
        {"branch": p_["branch"], "plan": p_["id"], "features": p_["features"]}
        for p_ in plans_out
    ], at=now)

    return {
        "rehearsal": rid,
        "base": base,
        "now": now,
        "horizon_days": horizon_days,
        "mandate": list(mandate),
        "weights": weights,
        "weights_from": provenance,
        "plans": plans_out,
        "recommended": best["id"],
        "sensitivity": sensitivity,
        "mandate_support": supported,
    }


def _metrics(store: EventStore, bname: str, before: World, plan: Plan, outcomes: Sequence[bool]) -> dict:
    after = project(store.read(bname))
    hits = [u for u, hit in zip(plan.uncertain, outcomes) if hit]
    burn_before = sum(s.amount_cents for s in before.subscriptions.values() if s.state == "active")
    burn_after = sum(s.amount_cents for s in after.subscriptions.values() if s.state == "active")
    return {
        "replies": sum(1 for u in hits if u.resolver == "reply_within"),
        "meetings_moved": sum(1 for u in hits if u.resolver == "meeting_moves"),
        "late_surprises": sum(1 for u in hits if u.late),
        "threads_open": len(after.open_threads()),
        "burn_saved_cents": burn_before - burn_after,
        "actions": len(plan.actions),
    }


def _expected(plan: Plan, before: World, now: int, horizon: int) -> dict:
    """Expectations from marginals, which is exact.

    Averaging over the five displayed futures would not be: those are the modal
    composition per count, and a plan carrying more open questions gets a
    representative future with more hits of every kind. That artefact made the
    do-nothing plan look calmer than it is. A sum of probabilities has no such
    problem and needs no coverage caveat.
    """
    replies = sum(u.p for u in plan.uncertain if u.resolver == "reply_within")

    # Weight each expected reply by what its thread is carrying. A plan that
    # collects one answer on a deposit is not the same plan as one that collects
    # one answer about padel, and until now the score could not tell them apart.
    seen: Dict[str, S.Stakes] = {}
    value = pressure = 0.0
    for u in plan.uncertain:
        if u.resolver != "reply_within" or u.entity not in before.threads:
            continue
        st = seen.get(u.entity) or seen.setdefault(u.entity, S.read(before.threads[u.entity]))
        value += u.p * st.money_k
        pressure += u.p * st.pressure(now, horizon)
    burn = sum(
        a.payload.get("amount_cents", 0) for a in plan.actions
        if a.kind == E.SUBSCRIPTION_CANCELLED
    )
    # A plan can open threads as well as close them: pre-confirming a meeting
    # starts a new conversation. Counting only the replies made the summary
    # disagree with the futures printed next to it.
    opened = len({
        a.entity for a in plan.actions
        if a.kind == E.MESSAGE_SENT and a.entity not in before.threads
    })
    return {
        "replies": round(replies, 3),
        "meetings_moved": round(sum(u.p for u in plan.uncertain if u.resolver == "meeting_moves"), 3),
        "late_surprises": round(sum(u.p for u in plan.uncertain if u.late), 3),
        "threads_open": round(len(before.open_threads()) + opened - replies, 3),
        "burn_saved_cents": burn,
        "value_at_risk_k": round(value, 4),
        "deadline_pressure": round(pressure, 3),
    }


def _mandate_support(w: World, mandate: Sequence[str]) -> List[dict]:
    """Which parts of the mandate this twin can actually act on.

    The synthetic world emits seven kinds of event; an mbox and an ICS export
    between them produce four. The three that are missing -- subscriptions
    observed, subscriptions charged, meetings moved -- are exactly what "cut what
    I don't use" needs and what gives "defend the calendar" anything to measure.
    So on real imported mail a mandate can quietly do nothing, and the interface
    used to show that as a shorter list of plans rather than as an answer.
    """
    have_subs = any(s.state == "active" for s in w.subscriptions.values())
    moved = sum(1 for m in w.meetings.values() if m.moves)
    have_meetings = bool(w.meetings)

    reasons = {
        "chase": (bool(w.open_threads()), "no thread is waiting on anyone"),
        "prune": (have_subs, "this twin has no subscriptions in it — mbox and ICS "
                             "imports carry mail and meetings, not charges"),
        "defend": (have_meetings, "this twin has no meetings in it"),
    }
    out = []
    for name in mandate:
        ok, why = reasons.get(name, (True, ""))
        note = "" if ok else why
        # A twin built from occasional calendar exports sees only the moves that
        # happened between two of them. Under-counting moves does not merely
        # weaken this mandate -- it drives every plan's late-surprise figure
        # toward zero, and a confident 0.00 reads as "your calendar is safe"
        # when it means "nothing here has been watched closely enough to know".
        if name == "defend" and ok and moved < MOVE_EVIDENCE:
            note = (
                f"only {moved} meeting{'' if moved == 1 else 's'} in this history "
                f"{'is' if moved == 1 else 'are'} recorded as having moved, too thin to "
                f"measure anyone's "
                f"habits — the late-surprise figures below are under-evidenced and "
                f"read lower than the truth. Recovering moves needs calendar exports "
                f"taken more often than meetings are rescheduled."
            )
        out.append({"mandate": name, "usable": ok, "note": note})
    return out


def _sensitivity(plans: List[dict], weights: Dict[str, float], winner: str) -> dict:
    """How much of this recommendation is the weights rather than the week.

    One of these weights is worth a whole predicted reply per cancelled
    subscription, and it was a guess. If halving or doubling it changes the
    answer, the person deserves to know that before they act on it -- a margin
    that thin is a coin toss, not a finding.
    """
    ranked = sorted(plans, key=lambda p: -p["utility"])
    margin = round(ranked[0]["utility"] - ranked[1]["utility"], 3) if len(ranked) > 1 else None

    flips = []
    for key in sorted(weights):
        for factor, label in ((0.1, "a tenth"), (0.5, "half"), (2.0, "double")):
            probe = dict(weights)
            probe[key] = weights[key] * factor
            alt = max(plans, key=lambda p: P_PREF.utility(p["features"], probe))
            if alt["id"] != winner:
                flips.append({"weight": key, "at": label, "instead": alt["name"]})
                break

    return {
        "margin": margin,
        "runner_up": ranked[1]["name"] if len(ranked) > 1 else None,
        "flips_under": flips,
        "verdict": (
            "the recommendation turns on a weight that is a guess"
            if flips else "no single weight decides this one"
        ),
    }


def _utility(exp: dict, actions: int, weights: Dict[str, float]) -> float:
    return P_PREF.utility(P_PREF.features(exp, actions), weights)
