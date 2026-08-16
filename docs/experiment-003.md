# Experiment 003 — does knowing what a thread is worth create value?

*August 2026. Follow-on to [002](experiment-002.md).*

## The hypothesis

Experiment 002 measured that steady-state value is 5–16% of the first week, and
that everything surviving is the reply term — a unit nobody buys. The diagnosis
was that the scoring has no idea what anything is *for*: a landlord withholding a
€4,000 deposit and "still on for padel Saturday?" score identically to three
decimal places.

So: give the model stakes, and the recurring number should stop being "replies"
and start being "money and deadlines handled". That was the hypothesis. It is
wrong, and the way it is wrong is the useful part.

## What was built

`stakes.py` reads three things out of a thread, deterministically, with no
language model:

- **Money named in it.** People write amounts down when amounts matter.
- **A deadline named in it.** "by Friday", "end of the month", an explicit date,
  resolved against when it was written.
- **How many times you already chased it** — not an inference at all, but your
  own revealed preference sitting in your own outbox.

Two features (`value_at_risk_k`, `deadline_pressure`) join the scoring with
weights fitted from committed choices like the other four. And a new plan,
**Chase what is at stake**, shortlists threads by expected value rather than by
who answers reliably — because a scoring change with no matching plan changes
what the product *says* and not what it *does*.

## The measurement, and the two ways I got it wrong first

**First attempt:** score both arms with their own weights. The stakes-aware arm
came out +379%. That is meaningless — each arm was measured with its own ruler,
and the aware ruler has two extra positive terms in it.

**Second attempt:** define a user whose true preferences include stakes, and
score both arms on *that*. The aware arm came out **worse**, −47%. Also wrong:
that average covered weeks 3–10 only, which is exactly the window that discards
value the aware arm may have collected earlier.

**Third attempt, and the honest one:** total value delivered over all ten weeks,
measured on the user's true preferences, for a user who genuinely cares about
money and deadlines.

## Result

| seed | stakes-blind | stakes-aware | change |
|---|---|---|---|
| 3 | 21.7 | 20.7 | −5% |
| 5 | 11.9 | 11.6 | −3% |
| 7 | 13.4 | 13.7 | +2% |
| **mean** | **15.7** | **15.3** | **−2%** |

**No difference.** Well inside the seed-to-seed noise.

But the weekly series is not the same at all. Seed 3:

```
blind    6.8  -0.7  15.7   0.0   0.0   0.0   0.0   0.0   0.0   0.0
aware   14.9   3.4   2.6  -0.0   0.0   0.0   0.0   0.0   0.0  -0.1
```

The blind product stumbles onto the big thread in week 3. The aware product
finds it in week 1. Same threads, same total, different order. (The long runs of
zeros are weeks where the recommendation was Hold — the product correctly saying
nothing needs doing.)

## What this settles

**The stakes term does not create value. It re-orders it.**

That is worth something — a deposit chased in week 1 rather than week 3 is two
weeks less exposure, and a deadline you meet is categorically different from one
you miss. But it is not the recurring-revenue fix the hypothesis proposed, and
pretending otherwise would have been easy: the first two measurements I ran both
said it was, in opposite directions.

**The deeper finding is that this is the same result as 002 wearing different
clothes.** Subscriptions are a finite stock, exhausted in two weeks. Valuable
threads are a finite stock too — chasing them *better* just empties the queue
sooner. Twice now, a change that looked like it should raise the recurring
number has instead raised the rate at which a fixed backlog is consumed.

So the honest description of this product is: **a backlog cleaner.** Its value is
proportional to the mess you arrive with and inversely proportional to how well
you were already coping. That shape is one-time by construction, and no scoring
change reaches it — the fix would have to be a product that *generates* something
each week rather than harvesting a stock that was already there.

## What that means for the business

Two of three candidate answers from the [decay experiment](experiment-002.md)
are now closed off. Giving the recurring side a better unit does not work,
because the problem was never the unit — it was the stock.

What remains:

- **Sell the one-time clean-up honestly.** It is large and verifiable: €1,080/yr
  found, plus the backlog cleared in the first fortnight. Price it once. This is
  a real business and it is not a subscription.
- **Sell the layer.** Preview, receipt, undo, and a ledger that scores its own
  predictions — the parts three reviews validated, none of which depend on the
  consumer value curve at all.
- **Or find a recurring stock.** Something that replenishes weekly at a rate the
  user cannot keep up with on their own. Inbound volume does replenish; the
  measurement says the product's edge on it is worth about +0.5 utility a week,
  which is real and small.

## What this does not settle

The stakes signals are regex-shallow. A thread whose importance is obvious to a
human but names no figure and no date reads as worth nothing here — and an
LLM would read it correctly. That would change the *quality* of the ordering.
Nothing measured here suggests it would change the total, because the total is
set by what is in the backlog, not by how well it is sorted.

The world is still mine, with stakes I distributed: 70% of threads carry nothing,
2% carry between €5,000 and €50,000. A real mailbox with a fatter tail would
raise every number in the table without changing their ratio.
