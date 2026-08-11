# Experiment 001 — does anything beat doing nothing, on real mail?

*August 2026.*

## Why

A product review pointed the app at an imported mailbox and it recommended
**Hold** — do nothing. Its reasoning was sound: chasing eight stale threads
bought an expected 0.24 replies. You cannot build a company on a screen that
says *don't bother*.

The review traced the cause to three lines of code. `synthetic.py` emits seven
kinds of event; an mbox and an ICS export between them produce four. The three
missing — `money.subscription_observed`, `money.charged`, `calendar.moved` — are
the only inputs to both scoring columns that separate one plan from another, and
to two of the three mandates. So the demo and the product were different
programs, and the demo was the one that looked good.

That is a claim about ingestion, not about the idea. It can be settled.

## The design

Take a generated world that is *known* to have value, export it to the formats a
real mailbox actually gives you, import it back through the real ingestion path,
and rehearse both. Any difference is ingestion loss, measured against an answer
key — which is the one thing a real mailbox can never provide.

```
seed(200 days, seed 5) ──► export_mbox()  ──► 653 messages
                       └─► export_ics() ×13 ──► weekly calendar snapshots
                                              │
                                    import ───┘──► rehearse ──► compare
```

Two ingestion paths had to be written first, because neither existed:

- **`read_receipts`** — subscriptions recovered from the receipts they send you.
  A mailbox has no `money.charged` in it; receipts are the only trace a standing
  charge leaves. Keyed on biller domain *and* amount, because Stripe and Paddle
  bill for hundreds of products from one address and domain alone merges them.
  Two charges minimum: one charge is a purchase, not a subscription.
- **`calendar_moves`** — meetings that moved, recovered by diffing successive
  exports. One snapshot shows where a meeting ended up, never that it was
  somewhere else first.

## Result

**Doing nothing is beaten, decisively.**

| | native world | imported via mbox + ICS |
|---|---|---|
| contacts | 7 | 7 |
| open threads | 105 | 105 |
| subscriptions / monthly burn | 4 / €97.00 | 4 / €97.00 |
| **best plan** | Chase who answers + prune + defend, **+3.94** | Chase who answers + prune, **+4.98** |
| Hold | −1.09 | 0.00 |

Threads, contacts, subscriptions and burn survive the round trip exactly. The
recommendation is a real plan, not silence, and it beats Hold by five points.

**The first answer was wrong for an interesting reason.** Before one fix, the
imported world produced *no* `prune` plan at all, despite having all four
subscriptions. Receipts were being ingested as conversations as well as charges,
and "cut what I don't use" looks for subscriptions nothing has mentioned in
ninety days — a subscription's own receipts mention it every month. Every
merchant looked actively discussed, so nothing was ever idle, and the mandate
silently proposed nothing. Receipts are now excluded from conversation
ingestion; that also brought contacts from 11 back to the correct 7.

## What did not survive

**Calendar moves.** The native world records 19 of 57 meetings as having moved.
Weekly exports recovered 5.

Recovery depends entirely on export cadence, because a move is only visible if
two snapshots straddle it *and* the meeting existed in the earlier one. Meetings
here are scheduled 3–9 days out and moved a median of 1 day before they start:

| export cadence | moves recovered, in a 60-day window |
|---|---|
| daily | 100% |
| every 3 days | 100% |
| weekly | 50% |
| fortnightly | 25% |

**The consequence is worse than a missing mandate.** Under-counting moves drives
every plan's late-surprise figure toward zero, so the imported twin reported
`0.00 late surprises` on every plan where the native world reported 1.09. A
confident zero reads as *your calendar is safe*. It meant *nothing here has been
watched closely enough to know*. The rehearsal payload now says which one it is,
and refuses to present thin evidence as a measurement.

## Verdict

The premise survives, with the scope corrected.

- **Chase and prune work on a mailbox export today.** A Takeout is enough, and
  the recommendation on real data is an action worth taking.
- **Defend cannot be bootstrapped from an import at all.** It needs calendar
  snapshots taken more often than people reschedule — which means the product
  has to capture them itself, weekly at minimum and daily to be honest. That is
  not an onboarding step, it is a background job, and the mandate should be
  presented as something that becomes available after the product has been
  watching for a while rather than as something a new user gets.
- **The review's headline finding was correct and is now fixed.** The demo and
  the product were different programs. They are the same program on two of three
  mandates, and the third is honest about what it does not yet know.

## What this does not settle

The world round-tripped here is one I generated, with reply rates I chose. It
proves ingestion fidelity — that the twin you get from a mailbox is the twin the
mailbox described — and nothing about whether real people behave like this one.
The second review finding, that value decays after the first run because the
subscription cleanup is one-time, is untouched by this experiment and remains
the more dangerous of the two.

## Reproducing it

```bash
python3 - <<'PY'
from preflight import synthetic
from preflight.store import EventStore
s = EventStore("source.db"); synthetic.seed_world(s, days=200, seed=5)
now = s.now()
synthetic.export_mbox(s, "mail.mbox")
for k in range(12, -1, -1):
    synthetic.export_ics(s, f"w{k:02d}.ics", now - k*7*86400)
PY

preflight --db round.db import --mbox mail.mbox --me me@example.net \
    $(for f in w*.ics; do echo --ics $f; done)
preflight --db round.db rehearse
preflight --db source.db rehearse
```
