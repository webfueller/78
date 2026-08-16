# Experiment 002 — does the value survive the second week?

*August 2026. Follow-on to [experiment 001](experiment-001.md).*

## The claim being tested

A product review ran the app five times in a row and reported that from the
second run onward every plan scored **below zero** — worse than doing nothing.
If true, the consumer product is a one-night stand: a large first hit, then a
subscription you are paying for nothing.

The mechanism is obvious once stated. Experiment 001 measured that 90% of the
first run's score is `burn_saved` — cancelling unused subscriptions. You can
only cancel Atlas Analytics once.

## The design

One world, generated once, 400 days long. The user starts on day 200 and lives
forward: rehearse, commit the recommended plan (or decline it if that plan is
Hold), then reveal the next seven days of history. The future was written before
any of the user's choices, so nothing downstream is tuned to what they did.

The measured quantity is **margin over Hold**, not absolute score. Absolute
utility is not interpretable on its own, because Hold is not zero — doing nothing
carries the late-surprise risk of every meeting you failed to confirm. What a
person is deciding is whether acting beats not acting, and that is the
difference.

Three seeds, ten weeks each.

## Result

**Margin over Hold, by week:**

| seed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | week 1 | weeks 3–10 | ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | +6.28 | +0.76 | 0.00 | +0.52 | +0.37 | +0.34 | +0.77 | 0.00 | 0.00 | +0.60 | **+6.28** | +0.32 | **5%** |
| 5 | +5.02 | +1.12 | +0.66 | +0.95 | +0.54 | +0.56 | +0.18 | +0.08 | +2.35 | +1.06 | **+5.02** | +0.80 | **16%** |
| 7 | +5.74 | +0.94 | +0.69 | +0.29 | +0.03 | +0.67 | +0.46 | +0.37 | +0.15 | 0.00 | **+5.74** | +0.33 | **6%** |

**Money found, every seed, identically:** €90 in week 1, €7 in week 2, **€0
from week 3 onward.** Four subscriptions, then one, then none.

## What this settles

**The review's mechanism is confirmed.** The large first-week number is a
one-time subscription cleanup and it is gone by week three. Steady-state value is
**5–16% of the first run**.

**The review's conclusion is too strong.** Value does not go negative. Margin
over Hold was zero or better in **26 of 30 weeks**, and the four zeroes are weeks
where the recommendation *was* Hold — the product correctly telling you to do
nothing, not failing. In steady state it stays weakly better than inaction, at a
mean margin of +0.3 to +0.8.

**And "weakly better" is the whole problem.** Once the money is gone, every point
of remaining value is the reply term: roughly 1.2 expected replies a week, for
about four messages sent. That is real, and it is denominated in a unit nobody
has ever had a budget line for. Nobody buys replies.

## What it means for pricing

| | evidence |
|---|---|
| **One-time value** | €1,080/year of cancelled spend, found in weeks 1–2. Large, verifiable, chargeable **once**. |
| **Recurring value** | ~1.2 expected replies per week. At €20/month that is about €5 a week for one extra answered nudge. |

So a consumer subscription is not supported by what this measures. Two ways
forward, and they are not equivalent:

1. **Give the recurring side a unit worth buying.** Today a landlord withholding
   a €4,000 deposit and "still on for padel?" score identically to three decimal
   places, because there is no stakes term. Price against money at risk or
   blindsides avoided and the recurring number stops being "replies". This is a
   real change to the model, not a pricing exercise.
2. **Sell the layer instead.** The preview / receipt / undo machinery is the part
   three reviews validated and none of it depends on the consumer value curve
   above.

## What this does not settle

The numbers are the model's own expected utility, not realised outcomes — the
product's self-assessment, on a world whose reply rates I chose. The backtest
says the model beats its baseline, so this is the best available estimate, but it
is an estimate of a simulation.

It also assumes the mandate set is fixed. A product that found a *new* kind of
one-time cleanup every few weeks — dormant subscriptions, then unclaimed
refunds, then expiring warranties — would show this same curve per category and a
flat one in aggregate. That is a plausible product and it is not this one.

## Reproducing it

The harness is twenty lines: seed a 400-day world, replay its first 200 days into
a working store, then loop `rehearse → commit → reveal the next seven days`.
Recorded in `scratchpad/decay/` and summarised above.
