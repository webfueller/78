# Getting started

Five minutes, and you never have to hand over a password.

## 1. Install it

You need Python 3.11 or newer. Nothing else — no accounts, no API keys, no
services to sign up for.

```bash
git clone https://github.com/webfueller/78 preflight
cd preflight
pip install -e .
```

## 2. See what it does, with made-up data

```bash
preflight demo
```

Open **http://127.0.0.1:8787**. You get a fictional person's inbox and calendar,
already full. Press **Rehearse the week**.

You'll see five plans side by side, a map of how each one probably goes, and a
recommendation. Click any ending on the map to read that week hour by hour.
**Commit** it and you get a receipt; **Undo** puts everything back.

Nothing here is yours and nothing is kept. Delete the file it made when you're
bored of it.

## 3. Try it on one real conversation

Go to **http://127.0.0.1:8787/paste**.

Paste an email thread you're actually dreading — the kind with several
*"On Monday, Ana wrote:"* lines in it — put your own email address in the box,
and press **Rehearse it**.

No account. Nothing is saved: the copy it builds lives in memory for one second
and is gone before the page finishes loading.

With one thread it doesn't know much about the person yet, and it says so
plainly rather than pretending.

## 4. Use your own mail

Two exports, both things you already have a right to.

**Your mail.** Go to [Google Takeout](https://takeout.google.com), select Mail
only, and download. You'll get a file ending in `.mbox`. Other providers have
the same thing under "export".

**Your calendar.** In Google Calendar: Settings → Import & export → Export.
You'll get a `.ics` file.

Then:

```bash
preflight --db mine.db import \
    --mbox ~/Downloads/All\ mail.mbox \
    --ics  ~/Downloads/calendar.ics \
    --me   you@example.com

preflight --db mine.db serve
```

Your mail never leaves your computer. There is no server to send it to.

**One thing worth doing every week:** export your calendar again and keep the old
files. A single export shows where meetings are; two show that one *moved*, and
that's the only way it can learn who reschedules on you.

```bash
preflight --db mine.db import --ics week1.ics --ics week2.ics --ics week3.ics
```

## 5. The weekly habit

1. `preflight --db mine.db serve`
2. Pick what you want it to think about — chase what's gone quiet, cut what you
   don't use, defend the calendar.
3. **Rehearse the week.**
4. Read the plans. Take one, or take none — *Hold* is a real answer and it is
   often the right one.
5. Commit. Read the receipt. You have 24 hours to undo.

## Where your things are

Everything is in the one `.db` file you named. Delete it and the product knows
nothing about you. There is no cloud copy, because there is no cloud.

---

## Two honest warnings

**It doesn't send email yet.** Committing writes down what you decided and makes
the receipt and the undo real. The actual mail connection isn't built. When it
is, the undo has to change — you can take a row out of a file, and you cannot
take back a message someone has read.

**Most of the value arrives in the first fortnight.** It finds the subscriptions
you forgot and clears the threads that went quiet, and that's a backlog, not a
tap. After that it's worth about one extra answered message a week. That is
measured, not guessed — see [experiment 002](experiment-002.md) — and you should
know it before you build a habit around it.
