# Antechamber

Rehearse the week before you live it.

Your accounts, forked into a shadow copy. An agent runs a week of real work
inside the fork. You get a branch map of futures, commit one, and only then does
anything touch the world -- with a receipt and a hard undo window behind it.

**The app runs.** `pip install -e . && antechamber demo` opens a local page where
you pick a mandate, rehearse the week, read the branch map, save it as an image,
and commit one future with a receipt and an undo window. It works on your real
mail via an mbox/ICS import -- no OAuth, no credentials, nothing leaves the
machine -- and on a single pasted thread with no account at all.

There is still no language model anywhere in it. Probabilities come from a
per-contact model measured against your own history and scored by the backtest;
simulated counterparties record a *response class*, never a named person's words.

Strategy memo, including the four concepts that were considered and rejected:
[`docs/field-note-001.md`](docs/field-note-001.md).

---

## What is here

| | |
|---|---|
| **Append-only log** | Every fact is an immutable, hash-chained event. In-place edits and mid-chain deletions are detected; nothing is ever updated in place. |
| **The fork** | A branch sees its ancestors' history *up to the instant it forked* and nothing after, so a rehearsal stays reproducible forever while the trunk moves on. |
| **Deterministic replay** | Any branch projects to a world state with a stable hash. Same events, same hash, always. |
| **Commit and undo** | Promoting a fork writes a receipt and opens a 24h window. An undo appends; the projection stops applying the commit. History of what was almost done survives. |
| **The prediction ledger** | Every rehearsal records falsifiable claims. When the due date passes, reality settles them and the scoreboard scores them. |
| **The backtest** | Replay a held-out stretch of the past, predict from what was knowable then, score against what happened. |
| **The rehearsal** | A mandate becomes plans, plans become real agent-authored events in a fork, and each plan branches on what the people involved do. |
| **The branch map** | The futures, ranked, with the probability mass they cover. Clicking one shows the week hour by hour and offers to commit it. |
| **Ingestion** | mbox and ICS in, same event kinds out. Everything downstream cannot tell imported data from generated data. |
| **One pasted thread** | `/paste` turns a reply chain into a throwaway twin held in memory for one request. No account, nothing written down. |
| **The shareable card** | The map exports as PNG or SVG, self-contained, carrying no names, no subjects and no message text. |

## Two things a review caught that the README was quiet about

**"Commit executes for real" does not send email.** There is no SMTP client in
this repository and no egress anywhere. Committing writes the actions onto the
trunk as things that happened, which is what makes the receipt, the state hash
and the undo meaningful — but the mail connection that would make it literally
true is not built. The interface now says so at the commit button. Wiring real
delivery is a deliberate, separate step, and the undo window has to be rethought
when it happens: you can withdraw a row from a local log, and you cannot
withdraw a message someone has already read.

**The demo and an imported mailbox were not the same product.** `synthetic.py`
emits seven kinds of event; an mbox and an ICS export produced four. The missing
three — `money.subscription_observed`, `money.charged`, `calendar.moved` — were
the only inputs separating one plan from another, so on real mail the app
recommended doing nothing.

Two of the three are closed: subscriptions are now recovered from the receipts
they send you, and calendar moves from diffing successive exports. Round-tripping
a known world through mbox and ICS now preserves contacts, threads,
subscriptions and burn exactly, and the recommendation on imported data beats
doing nothing by five points — [`docs/experiment-001.md`](docs/experiment-001.md)
has the design and the numbers.

The third is a scope correction rather than a fix. Recovering meeting moves needs
calendar exports taken more often than people reschedule — daily recovers all of
them, weekly half, fortnightly a quarter. That is a background job, not an
onboarding step, so **defend cannot be bootstrapped from an import** and becomes
available only after the product has been watching for a while. Worse, the thin
evidence made every plan report `0.00 late surprises`, which reads as *your
calendar is safe* and meant *nobody has been watching*. The payload now says
which one it is.

## What is deliberately not here

No language model. No live mail connection -- imports are file-based on purpose,
so version one is never a custodian of anyone's credentials. No hosting: the
server binds to loopback because the twin holds somebody's mail, and a
multi-tenant version is a different program with a different threat model.

A seeded synthetic life ships alongside, because it gives the tests ground truth
a real mailbox never could -- every contact's true reply rate is known, so the
scoreboard can be checked against an answer key.

---

## Run it

Standard library only, no dependencies. Tested on Python 3.11.

```bash
python3 -m unittest discover -s tests      # 113 tests, ~150s

export DB=/tmp/antechamber.db
A="python3 -m antechamber.cli --db $DB"

$A seed --days 200 --seed 3                # write a synthetic life onto the trunk
$A status                                  # project it
```

### The app

```bash
pip install -e .
antechamber demo                           # seeds a synthetic life, opens :8787
```

Pick a mandate, press **Rehearse the week**. You get plans scored side by side,
a branch map of how each one goes, and — on any future — the week hour by hour
with a commit button. Committing prints a receipt; the undo stays live for 24h.
**Save image** downloads the map as a card you can post.

### One thread, no account

`http://127.0.0.1:8787/paste` — paste a reply chain that still has its
"On … wrote:" lines, or the raw message from your client's *show original*. Both
Gmail-style and Apple-Mail-style dates parse. You get the same branch map for
that one thread.

Nothing is stored: the twin is built in an in-memory database that dies with the
request. With one thread the evidence is thin — usually one or two observed
replies — so the odds fall back to a stated population prior and the page says so
in as many words. That prior is a placeholder for the cross-user number this
product is supposed to learn, which is the whole moat argument arriving at the
one place a new user can feel it.

### Your real mail

Gmail Takeout gives you an mbox; most calendars export ICS.

```bash
$A import --mbox ~/Takeout/Mail/All\ mail.mbox --ics ~/cal.ics --me you@example.com
$A serve
```

### Rehearse, commit, undo, from the terminal

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
What you rehearsed is bit for bit what happened. (A fork from a full rehearsal
also carries simulated replies, so there the comparison is against the fork
projected *without* them — see "What the rehearsal will not do".)

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

| seed | claims | `global` | `per-contact` | `per-contact-age` |
|---|---|---|---|---|
| 3 | 999 | −5.1% | +32.8% | **+35.1%** |
| 5 | 1023 | +0.1% | +26.0% | **+33.7%** |
| 7 | 849 | +0.8% | +29.3% | **+39.4%** |
| 11 | 858 | −0.2% | +16.0% | **+23.9%** |
| 13 | 1026 | −0.6% | +32.1% | **+42.1%** |

Baseline is the leave-one-out base rate: a constant, scored without letting it
see its own answer. `global` failing to beat it is the correct result -- it is
the same idea wearing a different hat, and a scoreboard that credited it would be
rewarding noise.

**Two caveats, because an earlier version of this table did not carry them.**

*The number is a range, not a number.* This README used to quote a single
+37.5%. The lift runs +24% to +42% across seeds. The *ranking* holds on every
seed, holdout (30–120 days) and history length (120–365 days) tried, but the
magnitude is one draw and quoting it alone was overclaiming.

*`global` losing is not guaranteed.* It lands between −5% and +1% here, and on
some configurations it edges past the baseline. That is the expected behaviour
of a constant scored against a constant, and any reading of the table that
treats "global must lose" as a law is reading too much into noise.

**The earlier version of this measurement did not test what it claimed to.**
Claims used to be sampled only at the instant a message went out, so every
scored question had `age = 0` — and a predictor named for elapsed time was never
once asked about elapsed time. `per-contact-age` won because it was the only
predictor whose rates were measured on the cohort it was asked about; a
per-contact model restricted to that same cohort, with no age term at all,
scored within a point of it. The backtest now revisits each send on the two
following days while it is still unanswered, so the age buckets actually
exercised are 0, 1 and 2 days and roughly a third of the claims are about a
thread that has already been waiting. The numbers above are from that version.

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

## What the rehearsal will not do

Three things it refuses, because each would be a lie the interface tells:

**It does not write in anyone's voice.** A simulated counterparty records that a
reply *lands*, when, and with what probability. The body is a visible
placeholder. Response classes are what the data supports; a named person's
sentences are not.

**It does not claim it stopped a meeting from moving.** Pre-confirming cannot
change how often people reschedule, and there is no evidence in the data that it
does. What it changes is *when you find out* — so the metric the plan improves is
late surprises, not moves. Calendar risk is carried by every plan including the
one that ignores it, or the plan that addresses it would score worst.

**It does not claim a commit reproduced the rehearsal.** It reproduces the
rehearsal *minus its simulated replies* — the invented part is exactly the part
that does not come true. The receipt compares against that hash and says so.

## The card is the growth mechanic, so what it may contain is a rule

The exported map carries plan names, counts and probabilities. No contact is
named, no subject line appears, no message text is quoted. That is what makes it
safe to post without thinking about it, and it is enforced by a test that reads
the renderer and fails if it ever starts touching a contact, a subject or a body.

Colours in the card are literals, not CSS variables: an exported SVG has no
stylesheet to inherit from, and a map that renders correctly in the app and grey
on someone's timeline is worse than no export at all.

## What the integrity check does and does not catch

`replay --verify` recomputes the whole chain. It detects a rewritten payload and
a deleted event in the middle. It does **not** detect the tail being truncated,
or a forged event followed by recomputing every hash after it — there is no
signature and no anchor outside the events table, so an attacker with write
access to the file can produce a chain that verifies.

That matters because the threat model here is somebody's mail on a laptop, where
an adversary with file access is exactly the relevant one. Truncation is also the
cheapest useful attack, since it is what erases a commit receipt. A signed head
hash and event count, kept outside the table, would close it. Until that exists
the honest claim is the one above: accidental corruption and in-place edits, not
a determined adversary.

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
  commits.py      promotion, receipts, undo window, no double-execution
  predictions.py  the ledger: claims, resolvers, Brier scoring, calibration
  predictors.py   three deliberately dumb predictors, no model
  backtest.py     rewind, predict, score against what happened
  rehearse.py     mandate to plans to futures; the branch map
  ingest.py       mbox and ICS into the same event kinds
  paste.py        one pasted thread into a throwaway in-memory twin
  server.py       JSON API over the twin
  web/app.html    the app
  web/paste.html  the no-account entry point
  web/map.js      the branch map, live and as a shareable card
  synthetic.py    a seeded life, so the tests have ground truth
  cli.py
tests/test_twin.py   the engine
tests/test_app.py    the product
tests/test_mvp.py    the cold start and the card
```

65 tests. `python3 -m unittest discover -s tests`

## Kill criteria

From the memo, unchanged and still the whole bet:

- Counterparty backtest does not beat the baseline by week four → stop. Without
  that number the twin is theatre. `antechamber score` prints it.
- Fewer than one in four people who see a branch map make a second one within
  seven days → the artifact is not an artifact.
- Inference cost per rehearsal exceeds monthly price ÷ expected runs with no line
  of sight to closing it → the unit economics never arrive.
