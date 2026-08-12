# takeback

**Your agent can't touch anything until you've seen it — and you can take back anything it did.**

You would not let a new colleague run a script on your laptop without watching.
An agent is the same, and "are you sure? [y/N]" is not watching — it tells you a
thing is about to happen, not what it will do, not what else was considered, and
it gives you nothing to check afterwards.

This is the missing half. Everything an agent proposes happens first on a fork of
an append-only log, where it can be read, scored and thrown away. Only a commit
makes it real, and a commit is one transaction with a receipt, a withdrawal
window, and a hash proving the undo put things back exactly.

```bash
pip install takeback
```

No dependencies. Standard library only, Python 3.9+.

---

## The whole idea in thirty lines

```python
import time
from takeback import Kernel, Projection, EventStore, TRUNK

class Tickets(Projection):
    def apply(self, ev):                     # your events, your rules
        if ev.kind == "ticket.closed":
            self.closed = getattr(self, "closed", set()) | {ev.entity}

    def shape(self):                         # what "the same state" means
        return {"closed": sorted(getattr(self, "closed", []))}

kernel = Kernel(projection=Tickets)
store = EventStore("tickets.db")
now = int(time.time())

# The agent proposes, on a fork. Nothing real has happened.
store.fork("cleanup", TRUNK, note="close the stale ones")
for t in ("t_19", "t_204", "t_88"):
    store.append(branch="cleanup", kind="ticket.closed", entity=t,
                 actor="agent", ts=now, payload={"reason": "no reply in 90 days"})

# A human reads it, then:
receipt = kernel.commit(store, "cleanup")
# {'commit_id': 'c_9f2…', 'actions': 3, 'state_before': '…', 'state_after': '…',
#  'undo_until': 1786616401}

kernel.undo(store, receipt["commit_id"])
# {'restored': True}  ← the state hash matches what it was before, exactly
```

That is the entire contract. What an event *means* is yours; that it is
previewable, atomic, auditable and reversible is the engine's.

## What you get

| | |
|---|---|
| **An append-only log** | Hash-chained. In-place edits and mid-chain deletions are detected. Nothing is ever updated in place, so "what did it do in March" is answerable in September. |
| **Forks that rewind** | A branch sees its ancestors' history *up to the instant it forked* and nothing after — so a preview stays reproducible forever while the world moves on. |
| **Deterministic state** | Any branch folds to a state with a stable hash. Same events, same hash, always. |
| **Commit and undo** | Promotion is one transaction with a receipt and a window. An undo appends; the projection stops applying the commit. The record of what was almost done survives. |
| **A prediction ledger** | Claims with resolvers and due dates, scored against a leave-one-out base rate. An agent that says "this is low risk" can be marked. |
| **Learned weights** | Scoring weights fitted to what a person actually commits — and refused, out loud, until they beat the hand-picked guess on choices they have not seen. |
| **Exact futures** | Poisson-binomial enumeration over what could happen. Not sampling: two people running the same preview get the same picture. |

## The audit trail

`pip install takeback` also gives you a command. It is read-only on purpose —
the tool you check the record with should not be able to alter it.

```console
$ takeback --db wb.db audit

AUDIT — trunk — 1 committed, 1 undone

2026-08-12 15:03  c_b8ba8cd99cb254eb  UNDONE
    chosen over: hold
    · file.written  NOTES.md
    · file.written  src.py
    state 99f1caa → 5185ed6
    undone 2026-08-12 15:03 — the state hash above was restored exactly

2026-08-12 15:03  c_33975ecdd53d0770  COMMITTED
    chosen over: hold
    · file.written  src.py
    state 99f1caa → c534833
    undo open until 2026-08-13 15:03

Predictions: 7 made, none settled yet.
Chain: 10 events verified.
```

`--html audit.html` writes the same thing as a self-contained page: no scripts,
no fonts, nothing fetched. It is going to be read by somebody deciding whether to
trust an agent with their filesystem, and a page that phones home while making
that argument would be answering the question the wrong way.

Note the line that is hard to get any other way: **chosen over**. Not just what
the agent did — what it considered and rejected.

## Two things built on it

**[`workbench`](docs/workbench.md)** — an agent editing files in a directory, and
the reason the engine is not a toy: committing writes to disk and the log in one
transaction, and the undo restores the exact previous bytes. It is also an MCP
server, so any agent that speaks the standard tool protocol can drive it:

```bash
workbench --db wb.db --root repo mcp --check "pytest -q"
```

`propose` writes nothing. `commit` is the only call that touches the filesystem.
Neither the root directory nor the check command can be chosen by the model — a
test reads every tool schema and fails if one grows a directory argument.

**[`preflight`](docs/preflight.md)** — the mail twin the engine was carved out
of. Rehearse a week of your inbox and calendar before you live it. No longer the
headline: three experiments established that it is a large one-time cleanup
rather than a subscription. It stays because it is the proof the engine is not
secretly mail-shaped.

```bash
pip install -e . && pip install -e domains   # engine, then the two domains
```

## What has been measured

Nothing in here is asserted where it could be tested.

| | |
|---|---|
| [001](docs/experiment-001.md) | The mail twin works on imported mail, not just the demo. Round-trip preserves contacts, threads, subscriptions and burn exactly. |
| [002](docs/experiment-002.md) | The mail twin's value decays to **5–16% of week one** once the backlog is clear. Measured over 30 weeks, five seeds. |
| [003](docs/experiment-003.md) | Teaching it what a thread is worth **re-orders value without creating any** (−2%). The problem was the stock, not the sort. This is why the engine is the product. |
| [004](docs/experiment-004.md) | The workbench's risk numbers, backtested against two real 14-year-old repositories and five generated ones: knowing which file is being edited is worth **+3.5% median, up to +16.7%**. Real, and about a third the mail model's size. |

The kill criterion is the same everywhere and it is mechanical: beat a
leave-one-out base rate or the number on the screen is decoration.

## What it refuses to pretend

**Simulated participants never escape their fork.** A preview may invent that
somebody replies, or that a deploy pages you. Those events carry a `sim:` actor,
and the store will not write one to the trunk. There is no flag, no override, no
admin path.

**A commit reproduces the preview minus its simulations.** The invented part is
exactly the part that does not come true, and the receipt compares against that
hash and says so.

**The chain alone proves things only to itself.** Recomputing detects a rewritten
payload and a deleted event — but somebody who rewrites a payload and then
recomputes every hash after it produces a chain that verifies, and whoever can
write the events table can write the checkpoints table in the same breath.

So the head also goes where the log cannot reach:

```bash
takeback --db wb.db anchor --init --key ~/.config/takeback.key
export TAKEBACK_ANCHOR_KEY=~/.config/takeback.key   # that is the whole setup
```

Every commit now stamps `(branch, count, head hash, time)` into an append-only
file, authenticated with a key held outside the database. Forging history needs
the key as well as write access. `takeback verify` checks both and exits 2 if
either disagrees:

```console
$ takeback --db wb.db verify
{ "chain_ok": true,            ← the forgery is invisible to the chain
  "anchor_ok": false,
  "anchor_why": "trunk has the same number of events as the anchor but a
                 different head: history has been rewritten" }
```

Two limits, stated rather than left to be discovered. It is **not a public-key
signature** — HMAC is symmetric, so the anchor proves integrity to whoever holds
the key and does not let a third party verify your log without it; the stdlib has
no Ed25519 and this package has no dependencies. And it does **not survive a
matched rollback**: truncate the log *and* remove the anchor lines written after
that point, and what remains verifies. Keep a copy of the anchor somewhere the
agent cannot write and that becomes visible. There is a test that performs the
rollback and asserts it goes undetected, so nobody has to take the paragraph's
word for it.

**An undo is only as reversible as the action.** Files can be put back. A sent
message cannot, which is why nothing here sends one yet.

## Layout

```
takeback/          the engine — pip install takeback
  store.py          append-only log, branches, forks, integrity check
  events.py         immutable event, canonical form, hash chain
  projection.py     events in, state out, stable state hash
  commits.py        promotion, receipts, undo window, no double-execution
  ledger.py         claims, resolvers, Brier scoring, calibration
  preferences.py    weights fitted to real choices, and the honesty gate
  futures.py        exact enumeration of what could happen
  audit.py          what happened, in a form a person can read
  anchor.py         the head, stamped where the log cannot reach it
  cli.py            read-only: audit, verify, anchor, branches, log

domains/            what is built on it — pip install -e domains
  workbench/        an agent editing files; MCP server; the risk model
  preflight/        the mail twin, and where this came from

tests/              210 tests, ~160s
  test_takeback.py    the engine, driven by a domain that is not mail
  test_audit.py       the account, and whether it is true
  test_anchor.py      the forgery the chain cannot see, performed and caught
  test_workbench.py   the first commit that leaves the database
  test_churn.py       the risk numbers, and whether they know anything
  test_mcp.py         the agent-facing server, and what it will not allow
  test_twin.py test_app.py test_mvp.py test_preferences.py test_qa.py
```

```bash
python3 -m unittest discover -s tests
```

Two directions, one rule: `domains/` imports `takeback`, never the reverse. Two
tests enforce it — one greps every engine module for a domain's name, one imports
the engine in a clean interpreter and asserts no domain reaches `sys.modules`.

## Design notes

[`docs/kernel.md`](docs/kernel.md) — the contract, and a whole second domain
implemented in forty lines.
[`docs/workbench.md`](docs/workbench.md) — the file tool, its refusals, its limits.
[`docs/field-note-001.md`](docs/field-note-001.md) — the original strategy memo,
including the four concepts considered and rejected.
