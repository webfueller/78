# Field Note 001 — Five things to build, one to actually build

*August 2026. Written as a founder's memo, not a survey.*

---

## 1. The read

**Capability stopped being the constraint. Duration became it.**
Frontier agents finish tasks at 50% reliability on a horizon measured in *hours*. On hard
long-horizon benchmarks, state of the art sits under 20% pass. The failure modes published
this year are not intelligence failures — they are bookkeeping failures: subplan drift,
catastrophic forgetting, losing constraints revealed mid-task, declaring done prematurely,
and no habit of verifying anything. That is not a model problem you should wait out. It is a
*missing environment* problem, and environments are buildable by one person.

**The infrastructure land grab is already over.**
ACP (OpenAI/Stripe), UCP (Google/Shopify), AP2, A2A, Visa TAP. Agent identity, agent
payments, agent authorization — all being standardized in 2026 by companies with balance
sheets and distribution. Do not open a toll booth next to Stripe's toll booth.

**Distribution inverted.**
The consumer AI products that actually broke out were single-purpose, and their *output* was
the advertisement. Nobody watched a demo. They saw a friend's artifact and wanted one.

---

## 2. The filter

Five tests. A concept has to pass all five or it is not a one-person company.

1. **The artifact test.** The thing the product produces must be worth posting. If growth
   requires a referral program, it is already dead.
2. **The escape-velocity test.** It must improve with use in a way a competitor cannot copy
   by reading the landing page. Prompts are not a moat. Accumulated environment state is.
3. **The zero-ops test.** No sales calls, no onboarding, no support queue that grows with
   revenue. One person means the product sells, teaches, and debugs itself.
4. **The frontier test.** *Barely* possible in 2026, comfortable in 2027. Too easy and there
   are forty clones by Christmas. Too hard and you are a research lab with no revenue.
5. **The consequence test.** It has to touch money, time, a deadline, or a relationship.
   Toys spike and die.

**Rejected without further thought:** agent app stores (no defensibility), agent identity and
payment rails (see above), another coding agent (capital-intensive knife fight), AI
companions (retention rented from a model provider), and anything phrased "ChatGPT for X"
where X is a feature.

---

## 3. The five

### 01 · LOGBOOK — the agent that does not forget

An agent that owns one goal for a year. The innovation is not the agent; it is the **world
file** — a single human-readable dossier of your situation that the agent rewrites every
day and re-reads before acting. Wake, read, take one to five real actions, write back. The
dossier is the fix for drift, and it is also the product: you can read your own life as a
serialized document.

- **Edge case attacked:** catastrophic forgetting, state drift, premature completion.
- **Viral object:** the dossier. "Day 214 of my agent getting me out of debt."
- **Money:** subscription, priced against the outcome, not the tokens.
- **Moat:** the accumulated world file. Switching costs compound daily.
- **How it dies:** the gap between day 1 and day 60. Nothing visible happens in week two.

### 02 · ANTECHAMBER — rehearse the week before you live it *(the pick — see §4)*

Your accounts, forked into a shadow copy. Your agent runs a week of real work inside the
fork — including **simulated replies from the people involved**, modeled from your actual
history with them. You get a branch map of futures. You commit one branch. Only then does
anything touch the real world, with a signed receipt and a hard undo window.

- **Edge case attacked:** planning and subplanning failure, irreversibility, verification.
- **Viral object:** the branch map. Also: "my agent simulated my landlord and he said no in
  every timeline."
- **Money:** consumer subscription, then per-seat wherever outbound work has stakes.
- **Moat:** twin fidelity plus per-relationship counterparty models. Unclonable from outside.
- **How it dies:** the simulated people are unconvincing, and it becomes an expensive toy.

### 03 · THE PACT — agents with teeth

Not a habit tracker. You sign a mandate with real stakes — money, a public disclosure, a
message that sends itself — and an agent with genuine account access enforces it. The novel
technical problem: **the principal is the adversary.** The user will spend the month trying
to jailbreak their own agent, and the agent has to hold.

- **Edge case attacked:** irreversible action under an adversarial principal; authority that
  survives its owner's second thoughts.
- **Viral object:** consequences. Enforcement is inherently content.
- **Money:** take a cut of the stake; escrow float.
- **Moat:** trust and public track record. Also brutal to bootstrap.
- **How it dies:** one bad enforcement, one press cycle. Regulatory and reputational edge.

### 04 · STAMP — the adversarial verifier

Agents claim; nobody checks. Build the checker. An independent adversary that takes an
agent's claimed work and hunts for proof it actually happened, then issues a receipt.
Consumer skin: paste any claim, get a verdict.

- **Edge case attacked:** the single most-cited failure dimension — verification and
  reflection.
- **Viral object:** the verdict stamp becomes a format people quote at each other.
- **Money:** per-verification, then embedded in other people's products.
- **Moat:** the accumulated corpus of what was verified and how.
- **How it dies:** negative-sum posture, thin willingness to pay, and everyone's agent
  vendor ships a self-check for free.

### 05 · THE COMMONS — a world other people's agents live in

A persistent world whose inhabitants are agents belonging to real humans, with real scarcity,
real reputation, real accumulated history. You write your agent's charter; it lives there
around the clock and sends you dispatches.

- **Edge case attacked:** multi-agent, adversarial, long-horizon, emergent negotiation.
- **Viral object:** the dispatches. Short-form native.
- **Money:** subscription plus scarcity.
- **Moat:** the world itself. Nobody clones three years of lore and 200,000 inhabitants.
- **How it dies:** retention. Inference cost per idle inhabitant. It is a game, and games
  are hit-driven.

---

## 4. The pick: ANTECHAMBER

**Why this one.** Every other concept here is downstream of the same 2026 fact: agents can
act, and nobody dares let them, because you cannot preview a consequence. Solve preview and
you are not selling an agent — you are selling the ground every agent stands on. That is the
position with a 2027 unicorn shape, and it is the only one on this list where the hard part
compounds into something a competitor cannot read off your homepage.

It also passes the frontier test precisely. Simulating a plausible week of your working life,
with counterparties who behave like the actual humans, is *barely* achievable right now. In
eighteen months it is table stakes — which is exactly the window a solo founder wants.

### The v1 wedge

Not "a sandbox for everything." One domain, cheap to mirror, high stakes, high frequency:
**outbound communication and money leaving your account.**

1. Connect mail and calendar, read-only. Fork a shadow copy as an event-sourced store with
   full replay.
2. Give an agent a mandate. *Clear the backlog. Reschedule what's slipping. Cancel what I
   don't use.*
3. It runs seven simulated days inside the fork. Counterparties reply — modeled from your
   real history with each of them.
4. You get a **branch map**: committed path, simulated paths, dead paths.
5. You commit one branch. It executes for real, produces a receipt, and stays undoable for
   24 hours.

The counterparty models are the whole company. They are the hard part, the moat, and the
share.

### 90 days

| Weeks | Build | The question it answers |
|---|---|---|
| 1–2 | The twin: event-sourced shadow of mail + calendar, full replay, zero AI | Can I fork a life and roll it back? |
| 3–4 | Counterparty models, validated by **backtest** — hold out 60 days, predict real replies | Are the simulated people real enough to bet on? |
| 5–6 | The branch map. Exportable as image and short video from day one | Is the artifact good enough to be the marketing? |
| 7–9 | Commit-and-replay: real execution, signed receipt, hard undo window | Does anyone trust it with a real send? |
| 10–12 | Launch on the artifact, not the product | Does the branch map travel? |

**Launch mechanic, no OAuth required:** paste the hardest email you have to send this week.
Get a branch map of how it goes. Free, no account, synthetic counterparty inferred from the
pasted thread alone. Zero-friction top of funnel; the account comes later, when someone wants
the fork of their real week.

### Kill criteria — write these down before you start

- Counterparty backtest does not beat a trivial baseline by week 4 → **kill it.** Without
  that, the twin is theater and you are selling a mood.
- Fewer than 25% of people who see a branch map make a second one within 7 days → the
  artifact is not an artifact. Fix or kill.
- Inference cost per rehearsal exceeds monthly price ÷ expected runs, with no line of sight
  to closing it → the unit economics never arrive.

### The thing that will bite you

Simulating real, named people is the product's power and its live wire. Two rules from day
one, not retrofitted after the first complaint: model **response classes and likelihoods**,
not a named person's voice as quotable output; and never let simulated content leave the
owner's private preview in a form that could be mistaken for something a real person
actually said. Get this wrong once and the story about your company writes itself.

### The honest bear case

Twin fidelity may plateau below the level where anyone changes a decision because of it. If
the branch map is merely interesting rather than *decisive*, this is a beautiful demo with a
three-week retention curve. Week 4's backtest is the whole bet. Run it early, and believe it.
