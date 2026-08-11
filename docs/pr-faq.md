# Preflight — PR/FAQ

*Working-backwards review. Written after using the product, not after reading it.*

Reviewer: PM (bar raiser). Branch `claude/viral-app-concept-2026-88bggv`.
Everything cited below is a number I produced by running the thing, on the demo
seed and on a synthetic-but-realistic 403-message / 13-contact / 6-month mailbox
I generated and imported through the documented `preflight import` path.

---

## 0. The finding that outranks the rest

**I could not write an honest, compelling press release for "rehearse a week of
email and calendar work."** Not because the engineering is weak — it is the
strongest part — but because when I pointed the product at a mailbox shaped like
a real one, **it recommended doing nothing**, and it was right to.

```
Realistic imported mailbox: 403 threads, 13 contacts, 198 open threads, 20 meetings

recommended: hold
  Hold                     act=0   util= 0.000   replies=0.00
  Chase everything         act=8   util=-1.760   replies=0.24
  Chase who answers        act=4   util=-0.880   replies=0.12
```

A press release needs a customer who is better off. Here the product's own
scoring function says the customer is best off closing the laptop. You cannot
write "Preflight gave me my week back" over a screen that says *Hold*.

The demo does not say Hold. The demo says +3.94 and recommends a nine-action
plan. The gap between those two screens is not tuning. It is structural, and
Section 3 of the internal FAQ shows the three lines of code where it lives.

So this document does three things: it explains why the intended press release
cannot be written; it writes the press release that the built product *can*
truthfully support, which is for a narrower and less exciting company; and it
says what would have to change for the original one to become writable.

**What is genuinely good here should not get lost in that.** The event store,
the fork semantics, the commit receipt, the undo, the `sim:` quarantine, and the
privacy posture are better than most shipped products. The problem is not
craft. The problem is that the craft is pointed at a job nobody has.

---

## 1. The press release that cannot be written

Here is the draft the strategy memo implies, with the parts that failed
verification struck through in commentary.

> **Preflight lets you live your week twice.**
> *Rehearse a week of email and calendar work on a forked copy of your own data,
> see how it probably goes, and commit the future you want.*
>
> "I used to open Monday with 200 unread threads and no idea which five
> mattered. Now I see the week before I live it." — *a customer*

Three sentences, three problems.

**"See how it probably goes."** On imported mail, chasing eight stale threads
produces an expected **0.24 replies**. The honest version of the sentence is
"see that it probably goes nowhere." That is arguably a valuable thing to learn.
It is not a thing you subscribe to learning every week.

**"Commit the future you want."** Committing does not send anything. There is no
SMTP client, no network egress, no send path anywhere in the package — I grepped
for it, and `commit()` moves rows between branches in a local SQLite file. The
UI says *"9 actions would run for real."* Nothing runs. This is consistent with
the roadmap (the memo puts real execution at weeks 7–9), but it means the
sentence in the README — *"only then does anything touch the world"* — describes
a capability that does not exist yet, and every hard question about trust is
still entirely ahead.

**"Which five mattered."** The product cannot answer this, and it is the actual
job. Its score is `1×replies − 0.25×actions + 0.5×(saved÷€10) − 1×late
surprises`. There is no term for *what the thread is worth*. I pasted a landlord
withholding a deposit after three ignored chases; the recommendation was
**Hold**. I pasted "still on for padel Saturday?"; the recommendation was
identical, to three decimal places. The model answers *"will they reply?"* The
customer is asking *"does it matter if they don't?"*

---

## 2. The press release the built product can support

*Clearly labelled: this is for a narrower product than the one in the memo. It is
the one I would actually ship.*

> ### Preflight ships the missing safety layer for agents that act on your behalf
>
> **A local, auditable preview-and-undo layer for irreversible actions — so you
> can let an agent touch your accounts without hoping.**
>
> Today, letting an agent send email or move meetings is a leap of faith. You
> approve a plan written in prose, the agent runs, and the first real evidence of
> what it did is the consequence. There is no preview that is guaranteed to match
> execution, no receipt, and no way back.
>
> Preflight is the ground the agent stands on. Point it at an mbox and an ICS
> export — no OAuth, no credentials, nothing leaves your machine — and it builds
> an append-only, hash-chained twin of your mail and calendar. An agent proposes
> actions into a *fork* of that twin. You inspect exactly what would happen, hour
> by hour. When you commit, the receipt carries the state hash before, the state
> hash after, and a verdict: **"matches — every real action landed exactly as
> previewed."** Not a promise. An equality check between two hashes.
>
> Simulated counterparties are quarantined by construction. The trunk accepts
> writes from an allowlist of three real actors; no spelling, casing, or unicode
> trick on a `sim:` actor can reach the record. A simulated person's words
> cannot escape the fork they were invented in — enforced by the store, not by
> policy, with no override path.
>
> "I have shipped agent features and the review step was always the weak link —
> we showed people a summary and hoped it described the diff. A receipt that says
> the executed state hash equals the previewed state hash is the first version of
> this I would put in front of a security team." — *platform engineer, hypothetical
> but the kind of thing this actually earns*
>
> Preflight runs on loopback, holds no credentials, and re-importing a fresh
> export is idempotent. It is open, about 3,300 lines, and the privacy claims are
> small enough to read in an afternoon.

**Why this one is writable:** every sentence is something I verified. The receipt
really does compare hashes and really did say *matches*. The undo really did
restore the prior state exactly while leaving the commit in the log. The trunk
allowlist really is an allowlist. The exported card really does carry no names,
subjects, or message bodies — I read the SVG.

**Why it is a smaller company:** the customer is a developer or a platform team,
not a consumer. The artifact does not travel. The pricing is infrastructure
pricing. And it is a feature that Anthropic, OpenAI, and every agent framework
will ship inside their own runtime, because it belongs there.

---

## 3. Customer FAQ

**What does Preflight actually do?**
It builds a private, local copy of your mail and calendar from file exports, lets
an agent propose a week of work inside a throwaway branch of that copy, shows you
the branching outcomes with probabilities, and lets you commit one — with a
receipt proving the committed result matches what you previewed, and a 24-hour
undo.

**Do I have to give it my Google password?**
No, and there is no way to. It has no OAuth, no stored credentials, and no
outbound network connection. You feed it a Gmail Takeout mbox and an ICS file.
The server binds to loopback only.

**How do I get my mail in?**
Google Takeout. Request Mail (mbox) and Calendar (ICS). **Google typically takes
several hours and sometimes more than a day to prepare it**, then emails you a
link to a multi-gigabyte archive split into 2GB parts. You download, unzip, and
point the CLI at `All mail.mbox`. Import itself is fast: I measured ~640
messages/second, so a 200,000-message mailbox imports in about five minutes.
Re-importing a newer export is idempotent — on a repeat import of the same file,
403 messages written became 415 skipped and 1 written.

**How long until I see something useful?**
On the paste page, about six seconds. On your real mail, realistically **one to
three days**, almost all of it waiting on Google.

**Can I try it without an account?**
Yes — `/paste`. Paste one thread, get a branch map, nothing is stored. Be aware
that one thread is thin evidence and the page says so plainly: with fewer than
about three observed exchanges the odds you see are a stated population figure of
45%, not a measurement of your person.

**Will it send email as me?**
Not in this version. Committing writes to your local twin. There is no send path
in the software.

**Does it write emails in my voice, or invent what people said?**
No, deliberately. There is no language model anywhere in it. A simulated reply
records that a reply *lands*, when, and with what probability — the body is a
visible placeholder. The follow-ups it drafts are a fixed template: *"Following
up on X — do you have a view this week?"*

**Is the picture safe to post?**
Yes. I checked the exported SVG: it contains plan names, counts, and
probabilities and nothing else. No contact name, no subject line, no message
text.

**Why does it keep telling me its own numbers are guesses?**
Because they are, for your first eight commits. The four scoring weights are
hand-picked placeholders until you have made eight choices it can learn from —
at one rehearsal a week, about two months.

---

## 4. Internal FAQ — the hard questions

### 4.1 Who is the customer, precisely?

The product does not have one yet, and the demo persona is a decoy.

The synthetic life is a solo operator: a landlord, four subscriptions, seven
contacts, 105 open threads. Read as a person, that is a freelancer or a small
consultancy owner. Their recurring pain — *"threads have gone quiet and I don't
know which to chase"* — is real, frequent, and mildly painful.

It is not unsolved. **Gmail shipped Nudge in 2018.** *"Sent 5 days ago. Follow
up?"* — free, zero setup, in the inbox, in front of two billion people. It
covers most of the "chase what's gone quiet" mandate. The strategy memo has a
section on the incumbent question and it is about Google's *unwillingness to
simulate colleagues*. It never mentions that Google already shipped the base
feature eight years ago. That is the most consequential omission in the memo.

The segment where the pain is severe enough to pay is **outbound sales and
recruiting** — people whose income depends on reply rates across hundreds of
threads. That segment is served by Outreach, Salesloft, Apollo, HubSpot
Sequences and Lavender, all of which do reply-rate prediction with vastly more
data, and all of which integrate with the mailbox instead of asking for a
Takeout.

**Verdict: no customer identified. Pick one and re-derive the product, or accept
that this is infrastructure and sell it to developers (Section 2).**

### 4.2 What job is this hired for?

"Rehearse my week" is not a job anyone has. Nobody wakes up wanting a
counterfactual. The adjacent jobs people *do* hire for are:

- *"Tell me which of these 200 threads I have to deal with today."* — triage.
- *"Write the email I'm dreading so I stop avoiding it."* — drafting.
- *"Don't let me forget this one."* — reminders.

Preflight does none of the three. It **cannot** do the second, by design: no
language model, so the thing it commits is four copies of the same template.

What they do today instead: star, snooze, flag, Gmail Nudge, a Superhuman
reminder, or nothing. Why would they stop? On the evidence I collected: they
would not.

The one job it uniquely serves is *"let me approve an irreversible action without
hoping."* That is a real job with real dread attached — and it is the job in
Section 2's press release.

### 4.3 Time to first value

**Paste path** — 2 steps, ~6 seconds. But the value is near zero, because the
output is close to a constant. I sent three semantically opposite threads through
the API:

| thread | recommendation | Hold | Chase |
|---|---|---|---|
| Contract lapses on the 14th, legal silent | chase | 0.00 | **+0.20** |
| "Still on for padel Saturday?" | chase | 0.00 | **+0.20** |
| Fourth chase for a withheld deposit, threatening the deposit scheme | chase | 0.00 | **+0.20** |

Identical. The page reads the thread to extract a name and count messages; the
number it shows you is the 45% population prior. To its credit the page *says*
it is a prior. But the top of the funnel — the memo's "zero-friction top of
funnel" and the entire growth mechanic — is a near-constant function.

To be fair, it is not *always* constant: with more observed exchanges in one
thread it does move, and correctly (0.45 → 0.80 → 0.95 → 0.97 for a fast
replier). But the threads people paste are the ones they are *dreading*, and
those are short and one-sided — exactly the region where it returns the prior.

**Real-mail path** — count the steps honestly:

1. Hear about it
2. Have Python 3.11 and a terminal
3. `git clone` and `pip install -e .`
4. Request a Google Takeout (Mail as mbox + Calendar as ICS)
5. **Wait hours to two days**
6. Download a multi-GB archive, in 2GB parts
7. Unzip (a 20GB mbox wants ~40GB free)
8. Run the import with the right `--me` flag
9. `preflight serve`, open localhost, choose a mandate, rehearse
10. Read the result

Ten steps, a multi-day wait, a command line — and per Section 0 the reward at
step 10 is a recommendation to do nothing. **This is the worst time-to-value
curve I have reviewed in a consumer product.** The mbox import is not the
bottleneck (it is genuinely fast and idempotent); Google's export queue and the
terminal requirement are.

### 4.4 Is the branch map decision-useful, or a beautiful object?

It is a beautiful object. It is the best-executed artifact in the repo. It is
not decision-useful, for four reasons I measured.

**(a) The variety is largely cosmetic.** In the demo, three of the five plans
produce *identical* future rows — `2/▲1/31%, 2/▲2/28%, 2/—/18%, 3/▲2/14%,
1/—/6%` appears three times. The tree draws visual richness that the underlying
model does not contain.

**(b) The margin is below the product's own stated noise floor.** The winning
plan beats the runner-up by 0.05 on placeholder weights, and the product says so
outright: *"a coin toss, not a finding."* That is admirable honesty about a
recommendation that therefore carries no information.

**(c) The whole spread is one non-recurring action.** The demo's headline +3.94
is dominated by `0.5 × (€9000 ÷ €10) = +4.5` from cancelling three subscriptions.
Remove that and every plan is negative. It is a one-time inventory cleanup
wearing a weekly product's clothes. I confirmed the decay by committing and
re-rehearsing:

| run | best plan | Hold | spread | note |
|---|---|---|---|---|
| 1 | **+3.94** | −1.09 | 5.03 | €90/mo of subscriptions to cancel |
| 2 | −0.43 | −0.98 | 0.55 | €7/mo left |
| 3 | −0.43 | −0.98 | 0.55 | |
| 4 | −0.23 | −0.98 | 0.75 | prune plan gone; nothing left to cancel |
| 5 | −0.25 | −0.98 | 0.73 | |

By the second run **every plan scores below zero**, and "Chase everything" is
consistently *worse than doing nothing*. The product's own numbers describe a
three-week retention curve — precisely the honest bear case the memo wrote down.

**(d) It answers the wrong question.** See 4.1: there is no term for what a
thread is worth.

**What a real person would need instead:** not a distribution over how the week
lands, but a ranked list of *the three threads where silence is expensive*, with
the reason. That requires modelling stakes, which requires reading the thread,
which requires the language model the product has ruled out.

### 4.5 Trust — would anyone let this send email?

Today the question is untestable, because nothing sends. When it does:

**No, not as currently designed** — and the blocker is not trust in the
probabilities, it is the content. The committed plan I inspected was four
byte-identical emails, *"Following up on X — do you have a view this week?"*,
scheduled at 12:54, 01:54, 02:54 and 03:54 in the morning. No adult sends that to
four colleagues. The refusal to use a language model is correct for *simulating
counterparties* and wrong for *drafting the user's own outbound*. The product has
generalised one good rule into a place it does not apply.

**What has to be true first**, in order:
1. The outbound text is something the user would have written. Today it is not.
2. The actions are scheduled at times a human would send them.
3. The user can edit any action before committing. I found no edit path.
4. A dry-run against a real provider that proves the executed diff equals the
   previewed diff — the receipt already does this for the twin; it needs to do it
   for the world.

**Is the 24-hour undo reassuring, or an admission?** Neither — it is the wrong
promise, and this is the most important trust finding in the review. The undo
restores a **local SQLite projection's state hash**. Email does not work like
that. Once a message is delivered you cannot recall it; the human on the other
end has already read *"Following up on Q3 numbers"* at 1am. The undo window is
easy to implement precisely because it undoes the thing that was never
irreversible. The product's central answer to *"why should you trust me with
irreversible actions"* is a mechanism that only works on reversible ones.

A truthful version is a **hold window**: queue the sends, do nothing for 24
hours, let the user cancel. That is genuinely reassuring and genuinely hard —
and it costs the product its "the week already happened" framing.

### 4.6 The honesty — trust-building or self-indulgent?

Mixed, and the line between the two is sharp.

**Where it is right, and rare, and should be protected:**
- *"Every plan's shown futures cover at least 94% of what could happen."*
  Quantified coverage of a truncated distribution. Almost nobody ships this.
- *"A margin of 0.05 on weights that are a guess is a coin toss, not a finding —
  if that trade is not one you would make, take X instead."* This is a product
  arguing against its own recommendation. It is the single best sentence in the
  interface.
- The paste page naming its 45% figure as a prior rather than rounding it into
  confidence.
- The README documenting two backtest bugs that would have made its own headline
  number a lie. That is a serious person's instinct.

**Where it is self-indulgent:**
- **It confesses to the wrong sins.** The disclosed uncertainty is about
  *weights* and *coverage*. The undisclosed problems are that two of four
  mandates cannot fire on real data, that the recommendation collapses to noise
  after one run, and that "commit executes for real" does not execute. Honesty
  about the small thing, while the large thing goes unsaid, reads as
  sophistication rather than candour — and it is more dangerous than silence,
  because it buys credibility that the unstated problems then spend.
- **It confesses instead of fixing.** *"Nobody has measured what an unanswered
  thread is worth to you"* is true and is a research plan, not a caption. Two
  sliders and a prompt would turn it from a disclosure into a feature.
- **It costs the customer something.** Being told for eight weeks that the
  recommendation rests on made-up numbers, in order to choose between plans that
  differ by less than the stated error bar, is not calibration. It is asking the
  user to carry the founder's uncertainty.

**Net:** the instinct is a genuine asset and the highest-integrity thing in the
project. It is currently pointed at the second-order problem.

### 4.7 Willingness to pay

The product's own quantified value in the best case it can construct: **€90/month
of cancelled subscriptions and 2.2 expected replies.** After the first run: €7,
then zero.

- The €90 is a one-time cleanup. Rocket Money does it continuously, from a bank
  connection, for a cut of what it saves — no Takeout, no terminal.
- The 2.2 replies are the real unit, and the buyer is only someone whose income
  depends on replies. That is a salesperson, and the price of a reply for them is
  already set by a mature category.
- **The unit of value the customer thinks they are buying is "a calmer Monday."**
  Nothing in the product measures or delivers that.

**My estimate: $0 as a consumer subscription.** Nobody sets up a Takeout, waits
two days, and pays monthly to be told to hold. If the Section 2 product is built
instead — auditable preview-and-undo for agent actions — the buyer is a platform
team and the number is a five-figure annual contract for a handful of design
partners, with the strong caveat that it is a feature agent runtimes will
absorb.

### 4.8 The default is already "do nothing" — what makes this worth a new habit?

Nothing yet, and the product proves it against itself: **on realistic imported
mail, its own recommendation is Hold.** It agrees with the default.

Worse, the habit it asks for is unusually expensive. The twin's clock only
advances by events written into it — there is no live connection — so keeping the
rehearsal grounded means re-exporting from Google every week. That is a
multi-hour wait attached to a weekly ritual. To its credit, re-import is
idempotent, so the mechanics work. The economics do not.

For a new habit to beat "do nothing" it must be *cheaper than the pain it
removes*. Today it is more expensive.

### 4.9 The moat — the load-bearing claim, and I think it is wrong

The memo's central argument is that the calibration corpus — prediction/outcome
pairs — is generated by *use*, exists nowhere else, and cannot be bought past
because the labels resolve on human timescales.

**Those pairs are the core dataset of a mature category.** Outreach, Salesloft,
Apollo, HubSpot, Mixmax and Lavender have been recording "message sent → reply or
no reply, with latency, per recipient, per sequence" at enormous scale since the
mid-2010s. The asset the memo calls uncopyable is a decade of someone else's
production telemetry, orders of magnitude larger, already labelled by the same
clock.

The memo's moat test is a good test. It was applied to the wrong noun. The
scarce thing is not the pairs; it is the *counterfactual* — "what would have
happened had you not sent" — and this product does not collect it either,
because it only records claims for the plan you committed.

**A genuinely uncopyable asset is available here and is being left on the floor:**
run the rehearsal, then record what happened on the plans the user *rejected*.
That requires deliberately holding out a control arm. Nobody in the sales-tooling
category does this, because their customers will not tolerate not-sending. This
product's entire framing — futures you did not commit — makes the control arm
natural. It is the one thing here I would build a company on.

### 4.10 Is the week-4 kill criterion actually met?

**No. It is reported as met and is untested.**

On the synthetic seed the backtest reproduces the README exactly:
`per-contact-age` Brier 0.153 vs baseline 0.244, **+37.3% lift**, n=177.

But the synthetic world declares seven contacts with hand-chosen reply rates of
0.90, 0.25, 0.80, 0.50, 0.70, 0.35 and 0.02, and a base rate of 0.43 — close to
maximum entropy, the most favourable possible conditions for demonstrating lift.
The per-contact predictor's job is to recover seven constants that the generator
wrote. It recovers them. **That is a parameter-recovery test proving the harness
is not broken. It is not evidence that real humans have stable, learnable
per-contact reply rates conditional on elapsed time — which is the actual bet.**

The README says this plainly and deserves credit for it. The memo does not: it
calls week 4 "the whole bet," and the kill-criteria section implies `preflight
score` settles it. It does not.

On my imported mailbox all three predictors returned **identical results with
zero lift** (baseline Brier 0.0). Some of that is my generator, which does not
produce late replies. But the mechanism is real and important: on real mail, a
thread still open after several days is overwhelmingly a thread that will never
be answered. The base rate collapses toward zero, a constant "no" is nearly
perfect, and there is no lift left to sell. The favourable regime the backtest
was validated in may be the rarest one.

**This is the highest-priority open question in the project**, and it is
answerable this month with 20 real mailboxes and no new product.

### 4.11 Why does the demo work when real data does not?

Three lines. `synthetic.py` emits nine event kinds. `ingest.py` emits four.

| event kind | synthetic | mbox/ICS import |
|---|---|---|
| `message.sent` / `message.received` | yes | yes |
| `calendar.scheduled` | yes | yes |
| `contact.observed` | yes | yes |
| **`calendar.moved`** | **yes** | **never** |
| **`money.subscription_observed`** | **yes** | **never** |
| **`money.charged`** | **yes** | **never** |

Those three missing kinds are the sole inputs to the two scoring columns that
create the entire visible spread between plans, and to two of the four mandates.
On imported mail, permanently and by construction:

- **"Cut what I don't use"** can never produce a plan — an mbox has no
  subscription events, and I confirmed it: I imported twelve Atlas Analytics and
  Kiln CI receipt emails with "EUR 49.00" in the subject line and got
  `active_subscriptions: 0`.
- **"Defend the calendar"** can never fire — an ICS export contains the current
  state of your calendar, not a history of meetings having *moved*. Late
  surprises are 0.00 for every plan, and the defend plan does not appear at all.
- The `saved / month` column is permanently blank; `late surprises` is
  permanently zero.

Half the mandates and the dominant scoring term are demo-only. This is not a
bug — the ingest code is correct for what it can see. It means **the demo and the
product are two different programs**, and every judgement made from the demo
(including, I suspect, the decision to build this) was made about the wrong one.

### 4.12 What is genuinely excellent and must survive any pivot

I want this on the record, because the verdict below is harsh and this part is
not.

- **The trunk write gate is an allowlist, not a `sim:` denylist.** `store.append`
  refuses any actor not in `{world, user, agent}` on the trunk. No casing,
  whitespace or unicode trick on a simulated actor can reach the record. The
  README calls this "code, not policy" and it is the correct design; most teams
  would have written the denylist.
- **The receipt is a real equality check.** `state_before` / `state_after`
  hashes, and *"matches — every real action landed exactly as previewed."* I
  committed and undid, and the world was restored to the exact prior hash with
  the commit left in the log.
- **The privacy posture is the best I have seen at this stage.** Loopback-only,
  no credentials, file-based import, `/paste` in an in-memory database, and an
  exported card that I verified carries no names, subjects or bodies.
- **Import is fast and idempotent.** ~640 msg/s; a repeat import skipped 415 and
  wrote 1.
- **80 of the product's own tests pass** (`test_twin`, `test_app`, `test_mvp`,
  `test_preferences`). The 13 current failures are all in `tests/test_qa.py`,
  which another agent is actively writing. Notably, its
  `test_recommendation_survives_the_placeholder_burn_weight` fails —
  independently confirming 4.4(c), that the recommendation is a hostage to the
  made-up burn weight.

---

## 5. Verdict

**The premise does not hold as stated.** "Rehearse the week" is a solution
without a job. The strongest evidence is not an opinion: pointed at a realistic
mailbox, the product recommends *Hold*, and by its own scoring it is correct.
The demo says otherwise only because it is fed three event kinds that no real
export contains.

There is a real company in this repository. It is not the one in the memo. It is
the preview-receipt-undo layer, and it is worth more than the branch map.

### Build next — ranked by impact on the customer

1. **Validate the counterparty model on 20 real mailboxes before anything else.**
   Zero new product. Ship a script that imports a Takeout and prints the
   backtest. If per-contact-age does not beat baseline on real mail, the memo's
   own kill criterion fires and everything below is moot. *This is the whole bet
   and it has not been run.*
2. **Make the twin's ingest match its synthetic.** Detect subscriptions from
   receipt emails; reconstruct meeting-move history from `SEQUENCE`/`DTSTAMP` and
   from "moved"/"rescheduled" mail. Until this lands, half the product is
   demo-only. Two of four mandates are currently dead on arrival for every real
   user.
3. **Add stakes to the score.** One term for what a thread is worth — even a
   crude one (money mentioned, deadline named, thread age, is-it-a-customer).
   Without it the product ranks a padel invite level with a withheld deposit,
   which I verified it does. This is the difference between "will they reply" and
   "does it matter."
4. **Let a language model draft the user's own outbound, and let the user edit
   it before committing.** Keep the no-LLM rule exactly where it belongs — on
   simulated counterparties. Nobody will ever commit four identical template
   emails timed for 1am.
5. **Replace the 24-hour undo with a 24-hour hold.** Queue, don't send; let the
   user cancel. Truthful, and it is the only version of the promise that survives
   contact with real email.
6. **Instrument the rejected plans.** Record what happened on futures the user
   did *not* commit. This is the one asset in 4.9 that the sales-tooling
   incumbents structurally cannot collect. It is the real moat and it costs
   almost nothing to start.

### Cut

- **The five-plan branch map, in its current form.** Three of five rows are
  identical, the winning margin is below the product's own noise floor, and the
  spread comes from a one-time cleanup. Replace it with a ranked list of the
  three threads where silence is expensive. Keep the tree for two or three plans
  where the futures genuinely differ.
- **"Rehearse the week" as the positioning.** Nobody has that job.
- **The claim that committing "executes for real."** It does not. Say "stages"
  until it does.
- **The `/paste` page as a growth mechanic.** It returns the same answer to a
  contract deadline and a padel invite. Either make it thread-aware or stop
  calling it the top of the funnel.
- **The `/paste` card as the viral object.** The memo's hook was *"my agent
  simulated my landlord and he said no in every timeline."* That is a great
  artifact — and the privacy rule that makes the card safe to post is exactly the
  rule that forbids the sentence worth posting. The safety rule should win. But
  nothing replaced the growth mechanic when it did, and "2.2 replies expected,
  0.5 late surprises" is not a thing anyone reposts.

### What would have to be true

1. Per-contact-age reply prediction beats baseline **on real mailboxes**, not
   only on a world whose parameters the author chose. *(Testable this month.
   Everything depends on it.)*
2. There exists a segment for whom an unanswered thread has enough measurable
   value that a 5–10% improvement in reply yield is worth a subscription. *(Most
   likely outbound sales — which means competing with Outreach and Apollo on
   their turf, without their mailbox integration.)*
3. Someone will let software send email on their behalf when the text is
   generated and the preview is proven. *(Untested — nothing sends yet.)*
4. The prediction/outcome corpus is defensible despite a decade of the same
   labels sitting in sales-engagement platforms. *(I believe this is false as
   argued, and rescuable only via the rejected-plan counterfactual in 4.9.)*
5. Mail access can be made to cost less than a two-day Google Takeout without
   becoming a credential custodian. *(The memo rules this out on principle; the
   principle is admirable and is currently the largest single drag on adoption.)*

If (1) fails, stop — the memo says so and it is right.
If (1) holds and (2) fails, ship the Section 2 product to developers.
If (1) and (2) hold, the branch map is still the wrong interface, but there is a
company.
