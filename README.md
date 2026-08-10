# Antechamber

Rehearse the week before you live it.

Your accounts, forked into a shadow copy. An agent runs a week of real work
inside the fork. You get a branch map of futures, commit one, and only then does
anything touch the world -- with a receipt and a hard undo window behind it.

**This repository is weeks 1-2: the twin.** There is no model in it. That is the
point -- the environment has to be trustworthy before anything intelligent is
allowed to act in it.

Strategy memo, including the four concepts that were considered and rejected:
[`docs/field-note-001.md`](docs/field-note-001.md).

---

## What is here

| | |
|---|---|
| **Append-only log** | Every fact is an immutable, hash-chained event. Tampering is detectable; nothing is ever updated in place. |
| **The fork** | A branch sees its ancestors' history *up to the instant it forked* and nothing after, so a rehearsal stays reproducible forever while the trunk moves on. |
| **Deterministic replay** | Any branch projects to a world state with a stable hash. Same events, same hash, always. |
| **Commit and undo** | Promoting a fork writes a receipt and opens a 24h window. An undo appends; the projection stops applying the commit. History of what was almost done survives. |
| **The prediction ledger** | Every rehearsal records falsifiable claims. When the due date passes, reality settles them and the scoreboard scores them. |
| **The backtest** | Replay a held-out stretch of the past, predict from what was knowable then, score against what happened. |

## What is deliberately not here

No model, no LLM, no mailbox integration, no UI. Real ingestion lands in weeks
3-4 behind the same event kinds; the branch map is weeks 5-6; real execution is
weeks 7-9. A seeded synthetic life stands in for a mailbox so every claim below
can be tested against ground truth a real mailbox could never provide.

---

## Run it

Standard library only, no dependencies. Tested on Python 3.11.

```bash
python3 -m unittest discover -s tests      # 25 tests, ~26s

export DB=/tmp/antechamber.db
A="python3 -m antechamber.cli --db $DB"

$A seed --days 200 --seed 3                # write a synthetic life onto the trunk
$A status                                  # project it
```

### Rehearse, commit, undo

```bash
$A fork week --note rehearsal
$A propose week --cancel-subscription sub_atlas --send th_0001 --body "Closing this out."
$A status week      # the fork has changed
$A status           # the trunk has not
$A commit week      # receipt: state_before, state_after, undo_until
$A undo c_...       # restored: true
$A replay trunk     # chain still verifies
```

The commit's `state_after` is the same hash the fork projected before committing.
What you rehearsed is bit for bit what happened.

### The scoreboard

```bash
$A backtest --predictor global          --holdout-days 120
$A backtest --predictor per-contact     --holdout-days 120
$A backtest --predictor per-contact-age --holdout-days 120
$A score
```

---

## Why the ledger is the whole point

The obvious moat -- "we model your counterparties from your mail history" -- is
not one. That history arrives with the customer. A competitor launching in month
eighteen gets the same raw material on day one of every signup, with no
cold-start penalty. Anything computed purely from data the user carries with them
is a feature.

What cannot be copied is the record of claims that reality later judged. A
prediction/outcome pair exists nowhere in anyone's mailbox; it exists only
because someone ran the counterfactual and then watched. Those labels resolve on
human timescales -- days and weeks -- so a rival cannot buy their way past the
clock. That is the asset, and it accumulates only if it is instrumented from the
first commit. Hence: it is in weeks 1-2, before the part that makes demos.

## What the backtest found

Three predictors, ordered by how well specified they are. The synthetic world
gives every contact a different reply rate and latency, so the correct ranking is
known in advance and the harness has to recover it.

| predictor | Brier | vs. baseline | verdict |
|---|---|---|---|
| `global` — one rate for everyone | 0.2341 | −3.4% | does not beat baseline |
| `per-contact` — who you are waiting on | 0.1502 | **+33.7%** | beats baseline |
| `per-contact-age` — who, and how long already | 0.1416 | **+37.5%** | beats baseline |

Baseline is the leave-one-out base rate: a constant, scored without letting it
see its own answer. `global` failing to beat it is the correct result -- it is
the same idea wearing a different hat, and a scoreboard that credited it would be
rewarding noise.

Two real bugs surfaced on the way to those numbers, both of the kind that make a
backtest quietly lie:

**The conditional was wrong.** Historical stats measured "does a reply arrive
within 48h of sending". The claim asks "no reply has come yet, and it is now
Tuesday -- does one arrive by Thursday". Waiting burns through the part of the
distribution where a fast replier would already have answered, so the first
version over-predicted badly: 0.69 predicted against 0.28 observed. History is
now walked under exactly the eligibility rule the live claim uses.

**The sampling was wrong.** Predicting on a daily grid never observes a thread
that opened at two and was answered by seven, so the evaluation set filled with
the slow and the silent and every predictor scored the same. Claims are now made
at the moments the question is really asked -- when a message goes out, when a
meeting is two days off.

The second finding is worth keeping in mind for weeks 3-4: conditioning on
"still no reply" *inverts* the naive ranking. An open thread with someone who
always answers within the day is evidence something is wrong; an open thread with
someone who takes a week is just Tuesday. A counterparty model without elapsed
time is mis-specified no matter how much history it has.

## The rule that is code, not policy

Simulated counterparties are quarantined by actor (`sim:`), and the store refuses
to write one to the trunk. Commits promote only events the agent itself authored.
There is no flag, no override, no admin path. A simulated person's words cannot
leave the fork they were invented in.

## Layout

```
antechamber/
  events.py       immutable event, canonical form, hash chain
  store.py        append-only log, branches, forks, integrity check
  world.py        projection: events in, world out, stable state hash
  commits.py      promotion, receipts, undo window
  predictions.py  the ledger: claims, resolvers, Brier scoring, calibration
  predictors.py   three deliberately dumb predictors, no model
  backtest.py     rewind, predict, score against what happened
  synthetic.py    a seeded life, so the tests have ground truth
  cli.py
tests/test_twin.py
```

## Kill criteria

From the memo, unchanged and still the whole bet:

- Counterparty backtest does not beat the baseline by week four → stop. Without
  that number the twin is theatre. `antechamber score` prints it.
- Fewer than one in four people who see a branch map make a second one within
  seven days → the artifact is not an artifact.
- Inference cost per rehearsal exceeds monthly price ÷ expected runs with no line
  of sight to closing it → the unit economics never arrive.
