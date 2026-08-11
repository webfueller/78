"""The cold start: one pasted thread, no account, nothing stored.

Almost nobody exports a mailbox to try a product. Everybody can paste the thread
they are dreading. This turns that paste into a throwaway twin held in memory,
rehearses it, and returns a branch map -- then forgets it, because the database
is `:memory:` and dies with the request.

Two paste shapes cover nearly everything people actually have to hand: the
quoted reply chain a mail client builds ("On Mon, 4 Aug 2026 at 09:12, Ana Reyes
<ana@...> wrote:"), and the raw headers from "show original". Both give
timestamps and a sender, which is all the model needs. What neither gives is
much history, so the diagnostics returned alongside say plainly how thin the
evidence is -- one thread is a handful of observations, and the honest thing is
to lean on a prior and admit it.
"""

from __future__ import annotations

import email
import email.utils
import hashlib
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import events as E
from . import rehearse as R
from .predictors import POPULATION_MOVE_PRIOR, POPULATION_REPLY_PRIOR
from .store import TRUNK, EventStore
from .world import project

THREAD = "th_pasted"

# "On Mon, Aug 4, 2026 at 9:12 AM Ana Reyes <ana@example.com> wrote:"
# "On 4 Aug 2026, at 09:12, Ana Reyes <ana@example.com> wrote:"
# One quantifier over the leading run, not two overlapping ones. `\s*[>\s]*`
# could split a run of whitespace between the two in quadratically many ways, so
# a line of spaces that never reaches "On" cost 100 seconds of backtracking --
# reachable by anyone who could POST to /api/paste.
QUOTE = re.compile(
    r"^[>\s]*On[ \t]+(?P<blob>.{6,140}?)[ \t]*"
    r"<(?P<addr>[^<>\s@]+@[^<>\s]+)>[ \t]*wrote:[ \t]*$",
    re.MULTILINE,
)

# Splitting the date from the name is the whole trick, and the clock is the seam:
# every client writes the time last, right before the sender.
BLOB = re.compile(r"^(?P<when>.*?\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]\.?)?)\s*,?\s*(?P<name>.*)$")

_FORMATS = (
    "%a, %b %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p",
    "%a, %d %b %Y %H:%M", "%d %b %Y %H:%M",
    "%a, %d %b %Y, %H:%M", "%d %b %Y, %H:%M",
    "%a, %b %d, %Y, %I:%M %p", "%Y-%m-%d %H:%M",
)


def _parse_when(raw: str) -> Optional[int]:
    """Mail clients write dates a dozen ways; try the common ones, then give up."""
    text = re.sub(r"\s+", " ", raw.replace(" at ", " ")).strip().strip(",")
    for fmt in _FORMATS:
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    try:
        dt = email.utils.parsedate_to_datetime(text)
        return int(dt.timestamp()) if dt else None
    except (TypeError, ValueError):
        return None


def _clean(text: str, limit: int = 300) -> str:
    lines = [ln.lstrip("> ").strip() for ln in text.splitlines()]
    body = " ".join(ln for ln in lines if ln and not QUOTE.match(ln))
    return re.sub(r"\s+", " ", body)[:limit]


def _from_headers(text: str) -> List[dict]:
    """A raw message (or several) pasted from 'show original'."""
    # A new message starts at a `From:` that follows a blank line -- splitting on
    # every header line instead leaves each block holding one header and matching
    # nothing.
    blocks = re.split(r"\n\s*\n(?=From:\s)", text.strip())
    seen: List[dict] = []
    for block in blocks:
        if not re.search(r"^From:\s", block, re.M) or not re.search(r"^Date:\s", block, re.M):
            continue
        msg = email.message_from_string(block)
        people = email.utils.getaddresses([msg.get("From") or ""])
        try:
            when = email.utils.parsedate_to_datetime(msg.get("Date"))
        except (TypeError, ValueError):
            when = None
        if not people or not people[0][1] or when is None:
            continue
        name, addr = people[0]
        seen.append({
            "ts": int(when.timestamp()),
            "name": name or addr,
            "addr": addr.lower(),
            "subject": (msg.get("Subject") or "").strip()[:200],
            "body": _clean(msg.get_payload() if isinstance(msg.get_payload(), str) else ""),
        })
    return seen


def _from_quotes(text: str) -> List[dict]:
    """A quoted reply chain: newest on top, each marker introducing an older one."""
    marks = list(QUOTE.finditer(text))
    if not marks:
        return []
    found: List[dict] = []
    for i, m in enumerate(marks):
        parts = BLOB.match(m.group("blob").strip())
        ts = _parse_when(parts.group("when")) if parts else None
        if ts is None:
            continue
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        found.append({
            "ts": ts,
            "name": (parts.group("name") or "").strip().strip(",") or m.group("addr"),
            "addr": m.group("addr").lower(),
            "subject": "",
            "body": _clean(body),
        })
    if not found:
        return []  # markers present but no date was readable; nothing to place

    # Everything above the first marker is the newest message. Its author is not
    # written down anywhere -- the caller says who it was.
    top = _clean(text[: marks[0].start()])
    if top:
        found.append({"ts": max(f["ts"] for f in found) + 3600, "name": "", "addr": "",
                      "subject": "", "body": top, "_top": True})
    return found


def parse_thread(text: str, me: str = "") -> Tuple[List[dict], dict]:
    """Turn a paste into dated messages, plus an honest account of what was read."""
    me = (me or "").strip().lower()
    parsed = _from_headers(text)
    shape = "headers"
    if len(parsed) < 2:
        quoted = _from_quotes(text)
        if len(quoted) > len(parsed):
            parsed, shape = quoted, "quoted reply chain"

    parsed.sort(key=lambda m: m["ts"])
    for m in parsed:
        m["mine"] = bool(m.pop("_top", False)) or (bool(me) and m["addr"] == me)

    others = [m["addr"] for m in parsed if not m["mine"] and m["addr"]]
    counterparty = max(set(others), key=others.count) if others else ""
    name = next((m["name"] for m in parsed if m["addr"] == counterparty), counterparty)
    subject = next((m["subject"] for m in parsed if m["subject"]), "this thread")

    theirs = [m for m in parsed if m["addr"] == counterparty]
    mine = [m for m in parsed if m["mine"]]
    replies = sum(
        1 for a in mine
        if any(b["ts"] > a["ts"] for b in theirs)
    )

    return parsed, {
        "shape": shape,
        "messages": len(parsed),
        "from_them": len(theirs),
        "from_you": len(mine),
        "observed_replies": replies,
        "counterparty": counterparty,
        "counterparty_name": name,
        "subject": subject,
        "span_days": round((parsed[-1]["ts"] - parsed[0]["ts"]) / 86400, 1) if len(parsed) > 1 else 0,
        "thin": replies < 3,
    }


def rehearse_paste(text: str, me: str = "", horizon_days: int = 7) -> dict:
    """Build a throwaway twin, rehearse it, and never write it down."""
    parsed, diag = parse_thread(text, me)
    if len(parsed) < 2:
        raise ValueError(
            "Could not read that as a thread. Paste a reply chain that still has its "
            "\"On … wrote:\" lines, or the raw message from your client's "
            "\"show original\"."
        )
    if not diag["counterparty"]:
        raise ValueError(
            "Everything in that paste looks like it came from you. Add the other "
            "person's address, or paste a thread that includes their replies."
        )

    store = EventStore(":memory:")
    subject = diag["subject"]
    cp = diag["counterparty"]
    store.append(branch=TRUNK, kind=E.CONTACT_OBSERVED, entity=cp, actor=E.ACTOR_WORLD,
                 ts=parsed[0]["ts"], payload={"name": diag["counterparty_name"], "address": cp})
    for m in parsed:
        outbound = m["mine"]
        store.append(
            branch=TRUNK,
            kind=E.MESSAGE_SENT if outbound else E.MESSAGE_RECEIVED,
            entity=THREAD,
            actor=E.ACTOR_USER if outbound else E.ACTOR_WORLD,
            ts=m["ts"],
            payload={"sender": "me" if outbound else cp, "counterparty": cp,
                     "subject": subject or "this thread", "body": m["body"]},
        )

    w = project(store.read(TRUNK))
    thread = w.threads.get(THREAD)
    waiting = thread is not None and thread.awaiting_reply_from == cp

    # One thread is a handful of observations. Shrink hard toward a stated prior
    # rather than letting two data points sound like a measurement.
    result = R.rehearse(
        store, mandate=("chase",), horizon_days=horizon_days,
        reply_prior=POPULATION_REPLY_PRIOR, move_prior=POPULATION_MOVE_PRIOR,
        min_stale=0,
    )
    result["diagnostics"] = {
        **diag,
        "waiting_on_them": waiting,
        "prior_used": POPULATION_REPLY_PRIOR,
        "stored": False,
    }
    store.close()
    return result
