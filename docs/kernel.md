# The kernel

`rehearsal` is the engine, and as of now the product. This document is the
contract; [the README](../README.md) is the front door.

An agent that takes irreversible actions on someone's behalf needs a handful of
things, and none of them are the actions: a log of what it saw, a fork to try
things in, an exact account of what could happen, a receipt, an undo, a record of
its own claims that reality later judged, and a way for somebody to check all of
that afterwards. Those were built here for a mailbox. They
were never about mailboxes.

Three experiments say why this is the interesting half.
[002](experiment-002.md) measured that the consumer value decays to 5–16% of
week one once the backlog is clear; [003](experiment-003.md) established that no
scoring change reaches that, because the problem is the stock, not the sort. The
machinery underneath does not decay: every agent action needs a preview,
forever.

So it is its own package and its own distribution — `pip install rehearsal` gets
a log, a fork, a receipt and an undo, and no opinions about mail — with its own
tests, and a test that fails if it ever learns what is built on top of it.

## The parts

| module | what it owns |
|---|---|
| `store` | append-only, hash-chained log; branches; forks that rewind; truncation detection |
| `projection` | deterministic fold from events to state, with a stable `state_hash` |
| `commits` | promote a fork to the trunk atomically, with a receipt; withdraw it |
| `ledger` | claims with resolvers and due dates, scored against a leave-one-out base rate |
| `preferences` | scoring weights fitted to what was actually committed, and refused when unearned |
| `futures` | exact enumeration of what could happen (Poisson binomial over counts) |
| `audit` | what happened, in a form a person can read — and a read-only CLI |
| `anchor` | the head, stamped outside the log, so a rewrite cannot hide behind recomputed hashes |

Everything in `domains/` is built on that: `workbench` (an agent editing files)
and `preflight` (the mail twin this was carved out of).

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
product gets. For a domain that does real work, see
[`workbench`](workbench.md): an agent editing files, previewed and reversible.

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

**Side effects that cannot be undone.** `Commits` takes an `execute` hook that
runs inside the same transaction as the promotion.
[`workbench`](workbench.md) passes one: it writes files to disk, and its undo
puts the bytes back from the log. That works because a file *can* be put back.
Mail cannot, and until the undo window is rethought for actions that are gone the
moment they happen, this kernel should not be asked to govern one.

**Concurrency across machines.** One SQLite file, `BEGIN IMMEDIATE`, one writer
at a time. That is correct and it is not distributed.

**Public verifiability.** The head is now anchored: `anchor.py` stamps
`(branch, count, head hash, time)` into an append-only file authenticated with an
HMAC key held outside the database, which closes the rewrite-and-recompute attack
the chain could not see. What it does not give you is a signature a *third party*
can check without your key — HMAC is symmetric. `_mac` is the one function that
would change if that were worth taking a dependency for.

**Rollback with a matching anchor.** Truncate the log and remove the anchor lines
written after that point, and the remainder verifies. Detecting it needs memory
outside both files — a backup of the anchor, or a witness that remembers the
count. `tests/test_anchor.py` performs the attack and asserts it is missed, so
the limit stays honest.

## Running its tests

```bash
python3 -m unittest discover -s tests -p "test_rehearsal.py"
```

14 tests, well under a second. They use no mail, no synthetic life, and no part
of `preflight`.
