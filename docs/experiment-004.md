# Experiment 004 — does the workbench's preview know anything?

**Question.** The pitch for selling the layer rather than the mailbox is that the
preview is *calibrated* — that when it says a file is likely to need revising, it
is more often right than a coin that knows the average. The mail product has that
number ([+24% to +42%](../README.md#what-the-backtest-found) over a leave-one-out
base rate). The workbench shipped with none: its risk figures were a smoothed
rate with two priors picked in an afternoon.

**Answer, in one line.** The signal is real and it is roughly a third the size of
the mail product's: knowing which file is being edited is worth **+3.5% median,
up to +16.7%**, off the Brier score of a model that knows only the repository —
positive in 19 of 21 arms. And the label it predicts turns out to measure
*activity* at least as much as it measures wrongness, which caps what any model
of it can be worth.

## What was measured

The claim `rewritten_within`: this file is being edited now — will it be edited
again within the window? It is settled from the log alone, so ground truth is
free and abundant, which is why it and not `check_fails` is the one that can be
scored without CI history.

Walk-forward, event-driven, on four arms:

| arm | source | edits | files | span |
|---|---|---|---|---|
| rbenv | real, on disk | 1,140 | 105 | 14.6 years |
| ruby-build | real, on disk | 9,675 | 840 | 14.7 years |
| synthetic ×5 seeds | generated, known answer key | ~1,100 each | 24 | 800 days |
| this repository | real | 168 | 86 | **4.2 days** |

The last one is listed to be dismissed: this repository was written in a handful
of sittings, so every horizon worth asking about covers its entire history. It
cannot answer the question and its numbers are not reported.

Five predictors, all causal — each sees only edits whose own outcome window had
already closed at the moment it was asked:

`global` (one rate for the repository) · `per-dir` · `per-path` ·
`per-path-burst` (per-path plus whether the file is in a flurry) ·
`hierarchical` (the file, shrunk toward its directory, shrunk toward the whole).

## Two baselines, because one of them is misleading

`lift` is against the leave-one-out base rate — a constant, but one computed
*over the period being scored*. It knows that period's average without having had
to wait for it. Over fourteen years of a repository whose habits changed, that is
not a fair fight, and it shows: the running global rate loses to it by 42% on
rbenv. That is not a broken predictor, it is a non-stationary base rate.

`lift_vs_global` is against the running repository-wide rate — the same
information every model had, at the same moment. That is the honest form of the
only question the product asks: **does knowing which file tell you anything
beyond knowing the repository?**

## Results

Shipped model (`hierarchical`), against the running global rate:

| arm | 7 days | 14 days | 30 days |
|---|---|---|---|
| rbenv | +4.4% | +4.4% | +4.6% |
| ruby-build | +7.9% | +12.7% | +16.7% |
| synthetic-3 | +5.3% | +3.0% | +1.4% |
| synthetic-5 | +2.2% | +2.0% | +4.9% |
| synthetic-7 | +2.3% | +1.9% | −0.4% |
| synthetic-11 | +6.2% | +3.5% | −0.2% |
| synthetic-13 | +4.3% | +2.9% | +1.6% |

Median **+3.5%**, range −0.4% to +16.7%, positive in **19 of 21**.

The two negatives are both synthetic at a 30-day horizon, where the base rate has
risen past 0.80 and there is very little left to explain.

### The harness passes a test it wrote itself

On the synthetic repository, where every file's churn rate was chosen in advance
and directories were assigned round-robin:

- `global` lands on the baseline (|lift| < 2%) — correct: a constant scored
  against a constant.
- `per-path` beats it — correct: per-path rates are what was built in.
- `per-dir` scores −0.5% to −1.5% — **correct, and the point of including it**:
  there is no directory signal to find, and a model that reported one would be
  reporting noise.

## The finding that changed the product

**On both real repositories, the directory beats the file.** `per-dir` scores
+6.7% to +14.1% against the running global rate; `per-path` scores +2.9% to
+14.4% and loses to it at every horizon on rbenv and at 7 and 14 days on
ruby-build. Which directory a file is in — `src/` versus `docs/` — says more
about whether it will be revised than which file it is.

On the synthetic repository the opposite is true, by construction.

So neither is the model to ship. `hierarchical` shrinks the file toward its
directory and the directory toward the repository: it inherits directory
structure where directories are informative, and degrades to the per-path model
where they are noise. It is **not the best model on either real repository** — it
gives up about three points to `per-dir` on rbenv — and it is never the worst,
which is the right property for a model pointed at a repository it has never
seen.

## What this does not settle, and one thing it undermines

**The label is contaminated.** In the synthetic repository the *true* churn
parameter — the number the generator used — scores **worse than the base rate**
(−0.7% to −8.9%), while the *observed* per-file rate scores +5.8% to +10.7%. The
reason is that "edited again within seven days" fires whenever a file is busy,
not only when an edit was wrong. A cold file with a true revision rate of 3%
shows an observed rate near 20% purely from ordinary activity.

That has a blunt consequence: `rewritten_within` is a proxy for *churn*, and
churn is a proxy for *wrongness* with a large activity term in it. The preview is
entitled to say "files like this one get revised again about this often". It is
not entitled to say "this edit is probably wrong", and the wording in the product
should not drift toward the second.

**The build-risk claim is still unmeasured.** `check_fails` — the number the
score actually weights at −3.0 — needs a history of check outcomes, and neither
repository on disk carries one. It remains a guess, and the honest place for that
is written down rather than implied.

**Two repositories is two repositories.** Both are Ruby tooling by overlapping
authors. The direction of the finding (directory > file, modest lift) is
consistent across them and across horizons, and it is two draws.

## Reproducing it

```bash
workbench --db syn.db seed-repo --days 800 --paths 24 --seed 3
workbench --db syn.db backtest

workbench --db rb.db --root /path/to/any/git/repo git-import
workbench --db rb.db backtest --horizon-days 14
```

## What it means for the pitch

The claim that survives is narrower than the one I would have liked to make:
**the preview carries measurable information, worth single-digit percentages of
the Brier score on real repositories, and the honest headline is the mechanism —
atomic, previewed, reversible, receipted — with calibrated risk as a supporting
feature rather than the lead.**

That is a smaller claim than the mail product's ledger supports, and it is the
one the evidence supports. A scoring UI that leans hard on these numbers would be
overselling a +3.5% median.
