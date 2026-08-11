# The kernel

`rehearsal` is the part of this repository that is not about email.

An agent that takes irreversible actions on someone's behalf needs six things,
and none of them are the actions: a log of what it saw, a fork to try things in,
an exact account of what could happen, a receipt, an undo, and a record of its
own claims that reality later judged. Those were built here for a mailbox. They
were never about mailboxes.

Three experiments say why this is the interesting half.
[002](experiment-002.md) measured that the consumer value decays to 5–16% of
week one once the backlog is clear; [003](experiment-003.md) established that no
scoring change reaches that, because the problem is the stock, not the sort. The
machinery underneath does not decay: every agent action needs a preview,
forever.

So it is now its own package, with its own tests, and a test that fails if it
ever learns what product it is serving.

## The six parts

| module | what it owns |
|---|---|
| `store` | append-only, hash-chained log; branches; forks that rewind; truncation detection |
| `projection` | deterministic fold from events to state, with a stable `state_hash` |
| `commits` | promote a fork to the trunk atomically, with a receipt; withdraw it |
| `ledger` | claims with resolvers and due dates, scored against a leave-one-out base rate |
| `preferences` | scoring weights fitted to what was actually committed, and refused when unearned |
| `futures` | exact enumeration of what could happen (Poisson binomial over counts) |

Everything else in `preflight/` is a domain: mail, meetings, money, and the
plans and predictors that go with them.

## What a domain has to bring

Three things.

**A projection.** Subclass `Projection`, fold your own event kinds in `apply`,
and return the part of your state that identity depends on from `shape`. Claims
and commits are folded for you, and `state_hash` is computed over your shape plus
the claims — deliberately *not* over the commits, which is what lets an undo
restore a hash bit for bit.

**Resolvers.** A resolver reads a projected state and answers one yes/no question
about a claim made earlier. This is how a domain takes on risk: a claim with no
resolver cannot be recorded, and a claim that cannot be scored is marketing.

**A prior.** The feature names your plans are scored on and the numbers you are
guessing. The kernel fits weights to the choices someone actually makes, and
keeps your guess unless the fit beats it on choices it has not seen.

## A whole domain, in about forty lines

This is real code — it is the fixture in `tests/test_rehearsal.py`, which runs
the same commit, undo, quarantine and ledger tests against it that the mail
product gets.

```python
from rehearsal import Kernel, Projection

SERVICE_SEEN, RELEASE_CUT, DEPLOYED, PAGED = (
    "fleet.service_seen", "fleet.release_cut", "fleet.deployed", "fleet.paged")

class Fleet(Projection):
    def __init__(self):
        super().__init__()
        self.services = {}

    def apply(self, ev):
        p = ev.payload
        if ev.kind == SERVICE_SEEN:
            self.services.setdefault(ev.entity, {"running": p["running"], "pages": []})
        elif ev.kind == DEPLOYED:
            self.services[ev.entity]["running"] = p["release"]
        elif ev.kind == PAGED:
            self.services[ev.entity]["pages"].append(ev.ts)

    def shape(self):
        return {"services": {k: v for k, v in sorted(self.services.items())}}

def pages_within(fleet, rec):
    s = fleet.services.get(rec["params"]["service"])
    return bool(s) and any(rec["made_at"] < ts <= rec["resolve_by"] for ts in s["pages"])

KERNEL = Kernel(
    projection=Fleet,
    resolvers={"pages_within": pages_within},
    prior={"shipped": 1.0, "pages": -2.0},
)
```

From there:

```python
store.fork("plan", TRUNK, note="ship v2")
store.append(branch="plan", kind=DEPLOYED, entity="checkout",
             actor="agent", ts=3000, payload={"release": "v2"})

receipt = KERNEL.commit(store, "plan")     # atomic, with state_before/after
KERNEL.undo(store, receipt["commit_id"])   # restored: True, hash matches exactly
```

An agent may write `actor="sim:oncall"` inside the fork to rehearse a page it
does not control. That event can be read, scored and thrown away, and the store
will refuse to promote it to the trunk — the same guarantee that keeps a
simulated person's words out of the mail product's history.

## What the kernel refuses to know

It does not know what an event means, what an action does, or what a feature is
called. It validates no domain payloads. It has no opinion about mail.

Two tests hold that line: one greps every module in the package for the product's
name, and one imports the kernel in a clean interpreter and asserts the product
never appears in `sys.modules`. They are cheap and they are the whole point — a
kernel that has quietly grown a dependency on its first customer is not a kernel,
it is a refactor that was abandoned halfway.

## What is deliberately still missing

**Side effects.** `Commits` takes an `execute` hook that runs inside the same
transaction as the promotion, and nothing passes one. This product sends no mail,
and a kernel whose first side-effect implementation is also its first test is not
something to ship. When something does pass one, the undo window has to be
rethought: you can withdraw a row from a local log; you cannot withdraw a message
someone has read.

**Concurrency across machines.** One SQLite file, `BEGIN IMMEDIATE`, one writer
at a time. That is correct and it is not distributed.

**Signing.** The chain detects in-place edits and mid-chain deletions. It does
not survive an adversary with write access to the file, because there is no
anchor outside the events table. A signed head hash would close it.

## Running its tests

```bash
python3 -m unittest discover -s tests -p "test_rehearsal.py"
```

14 tests, well under a second. They use no mail, no synthetic life, and no part
of `preflight`.
