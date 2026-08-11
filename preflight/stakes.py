"""What a thread is worth, and when it stops being worth anything.

The scoring counted replies. A landlord sitting on a four-thousand-euro deposit
and "still on for padel Saturday?" scored identically to three decimal places,
which is a fair description of a product that does not know what anything is for.

Three signals are recoverable from mail without a language model, and all three
are things the sender put there on purpose:

  *Money named in the thread.* People write amounts down when amounts matter.
  *A deadline named in the thread.* A date is a clock, and a clock is stakes.
  *How many times you have already chased it.* That one is not inference at all
  -- it is your own revealed preference, recorded in your own outbox.

None of these is a measure of importance. They are correlates of it, cheap and
deterministic, and the weight the product puts on each is learned from what you
actually commit rather than asserted here.
"""

from __future__ import annotations

import calendar
import dataclasses
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .world import Thread

DAY = 24 * 3600

# Amounts, in the shapes people write them: "€4,000", "EUR 4000", "4000 EUR",
# "$1.2k". Bare numbers are excluded on purpose -- an invoice number is not money.
# `\d[\d.,]*` looks right and is not: at the end of a sentence it swallows the
# full stop, and "EUR 4,000.00." then fails to parse as a number and is dropped
# in silence. Amounts at the end of sentences are most amounts.
NUM = r"\d[\d,]*(?:\.\d{1,2})?"
AMOUNT = re.compile(
    rf"(?:(?P<sym>[€$£])\s?(?P<a>{NUM})(?P<ka>k\b)?)"
    rf"|(?:(?P<a2>{NUM})(?P<kb>k\b)?\s?(?P<code>EUR|USD|GBP)\b)"
    rf"|(?:(?P<code2>EUR|USD|GBP)\s?(?P<a3>{NUM})(?P<kc>k\b)?)",
    re.I,
)

WEEKDAYS = {n.lower(): i for i, n in enumerate(calendar.day_name)}
WEEKDAYS.update({n.lower(): i for i, n in enumerate(calendar.day_abbr)})

BY_WEEKDAY = re.compile(r"\b(?:by|before|on|due)\s+(?:next\s+)?(" +
                        "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", re.I)
BY_DATE = re.compile(r"\b(?:by|before|due|deadline[:\s])\s*(\d{4}-\d{2}-\d{2})\b", re.I)
END_OF = re.compile(r"\bby\s+(?:the\s+)?end\s+of\s+(?:the\s+)?(week|month|day)\b", re.I)
TOMORROW = re.compile(r"\b(?:by\s+)?tomorrow\b", re.I)
URGENT = re.compile(r"\b(deadline|urgent|final notice|last chance|expires?)\b", re.I)


@dataclasses.dataclass
class Stakes:
    money_cents: int = 0
    deadline: Optional[int] = None
    chased: int = 0

    @property
    def money_k(self) -> float:
        """Money at risk in thousands, which is the unit the weight is fitted in."""
        return self.money_cents / 100_000.0

    def pressure(self, now: int, horizon: int) -> float:
        """1.0 if the clock runs out inside the horizon, tapering after."""
        if self.deadline is None:
            return 0.0
        if self.deadline <= now:
            return 1.0  # already missed; the pressure did not go away
        return 1.0 if self.deadline <= now + horizon else 0.0


def _amount_cents(text: str) -> int:
    """The largest amount named anywhere in the thread."""
    best = 0
    for m in AMOUNT.finditer(text or ""):
        raw = m.group("a") or m.group("a2") or m.group("a3") or ""
        thousands = bool(m.group("ka") or m.group("kb") or m.group("kc"))
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        best = max(best, int(round(value * (1000 if thousands else 1) * 100)))
    return best


def _deadline(text: str, sent_at: int) -> Optional[int]:
    """A date the writer named, resolved against when they wrote it."""
    text = text or ""
    when = datetime.fromtimestamp(sent_at, timezone.utc)

    m = BY_DATE.search(text)
    if m:
        try:
            return int(datetime.strptime(m.group(1), "%Y-%m-%d")
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass

    m = END_OF.search(text)
    if m:
        unit = m.group(1).lower()
        if unit == "day":
            return int((when.replace(hour=23, minute=59)).timestamp())
        if unit == "week":
            return int((when + timedelta(days=(6 - when.weekday()) % 7 or 7)).timestamp())
        last = calendar.monthrange(when.year, when.month)[1]
        return int(when.replace(day=last, hour=23, minute=59).timestamp())

    m = BY_WEEKDAY.search(text)
    if m:
        target = WEEKDAYS[m.group(1).lower()]
        ahead = (target - when.weekday()) % 7 or 7
        return int((when + timedelta(days=ahead)).timestamp())

    if TOMORROW.search(text):
        return int((when + timedelta(days=1)).timestamp())
    return None


def read(thread: Thread) -> Stakes:
    """Everything the thread says about how much it matters."""
    text = " ".join([thread.subject] + [m.get("body", "") for m in thread.messages])
    deadline = None
    for m in thread.messages:
        found = _deadline(f"{thread.subject} {m.get('body', '')}", m["ts"])
        if found is not None:
            # The most recent statement of the date wins: people move deadlines.
            deadline = found

    # A run of messages from you with no answer between them. Sending three times
    # is not a guess about importance, it is a measurement of it.
    chased = 0
    for m in reversed(thread.messages):
        if m["direction"] != "out":
            break
        chased += 1

    money = _amount_cents(text)
    if not money and URGENT.search(text):
        # Urgency without a number still separates a final notice from a chat,
        # so it is floored rather than dropped. Deliberately small: a word is
        # weaker evidence than a figure.
        money = 25_000
    return Stakes(money_cents=money, deadline=deadline, chased=max(0, chased - 1))
