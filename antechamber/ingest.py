"""Real data in, without asking anyone for an OAuth token.

Gmail and most calendars export mbox and ICS. That is enough to build a real
twin today, on a laptop, with the archive already sitting in a Takeout zip --
and it keeps the first version of this product free of the one integration that
would make it a custodian of live credentials.

The event kinds are the same ones the synthetic generator writes, so everything
downstream -- projection, forks, predictors, the backtest -- cannot tell the
difference and does not get a chance to care.
"""

from __future__ import annotations

import contextlib
import email.utils
import hashlib
import mailbox
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import events as E
from .store import TRUNK, EventStore

HOUR = 3600


def _cid(addr: str) -> str:
    addr = (addr or "").strip().lower()
    return re.sub(r"[^a-z0-9._@+-]", "", addr) or "unknown"


def _people(raw: Optional[str]) -> List[Tuple[str, str]]:
    """(display name, address) pairs from any address header."""
    return [(n.strip(), a.strip().lower())
            for n, a in email.utils.getaddresses([raw or ""]) if a and "@" in a]


def _when(msg) -> Optional[int]:
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        return None
    return int(dt.timestamp()) if dt else None


def _thread_key(msg) -> str:
    """Group by conversation root, falling back to a normalised subject."""
    refs = (msg.get("References") or "").split()
    root = refs[0] if refs else (msg.get("In-Reply-To") or msg.get("Message-ID") or "")
    root = root.strip()
    if not root:
        root = re.sub(r"^\s*((re|fwd|fw|aw)\s*:\s*)+", "", msg.get("Subject") or "", flags=re.I)
    return "th_" + hashlib.sha256(root.encode("utf-8", "replace")).hexdigest()[:14]


def _body(msg, limit: int = 400) -> str:
    try:
        part = msg.get_body(preferencelist=("plain",)) if hasattr(msg, "get_body") else None
        text = part.get_content() if part else None
    except Exception:  # noqa: BLE001 - malformed mail is normal mail
        text = None
    if text is None:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            text = payload.decode("utf-8", "replace")
        elif isinstance(payload, str):
            text = payload
        else:
            text = ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _message_record(msg, mine: set) -> Optional[dict]:
    """One mail becomes one event, or nothing if it cannot be placed."""
    ts = _when(msg)
    if ts is None:
        return None  # a message with no date cannot go on a timeline
    senders = _people(msg.get("From"))
    if not senders:
        return None
    sender_name, sender_addr = senders[0]
    outbound = sender_addr in mine

    if outbound:
        others = [(n, a) for n, a in _people(msg.get("To")) + _people(msg.get("Cc"))
                  if a not in mine]
    else:
        others = [(sender_name, sender_addr)]
    if not others:
        return None  # a note to yourself has no counterparty to model

    other_name, other_addr = others[0]
    cp = _cid(other_addr)
    return {
        "ts": ts,
        "kind": E.MESSAGE_SENT if outbound else E.MESSAGE_RECEIVED,
        "entity": _thread_key(msg),
        "actor": E.ACTOR_USER if outbound else E.ACTOR_WORLD,
        "payload": {
            "sender": "me" if outbound else cp,
            "counterparty": cp,
            "subject": (msg.get("Subject") or "").strip()[:200],
            "body": _body(msg),
        },
        "_contact": (cp, other_name or other_addr, other_addr),
    }


def read_mbox(path: str, me: Sequence[str]) -> List[dict]:
    """An mbox becomes messages sent and received. Bodies are kept, truncated."""
    mine = {a.strip().lower() for a in me}
    with contextlib.closing(mailbox.mbox(path)) as box:
        records = [_message_record(msg, mine) for msg in box]
    return [r for r in records if r is not None]


_ICS_TS = re.compile(r"^(\d{4})(\d{2})(\d{2})T?(\d{2})?(\d{2})?(\d{2})?Z?$")


def _ics_time(value: str, tzid: str = "") -> Optional[int]:
    """An ICS timestamp, honouring its zone.

    A bare `DTSTART:20260804T090000` is floating local time, a trailing Z is UTC,
    and `DTSTART;TZID=America/New_York:...` is neither. Treating all three as UTC
    put every meeting from a normal calendar hours away from where it belongs,
    which then moved the claims keyed to its start.
    """
    import calendar as _cal
    from datetime import datetime, timezone

    raw = value.strip()
    m = _ICS_TS.match(raw)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())

    if raw.endswith("Z") or not tzid:
        return _cal.timegm((y, mo, d, h, mi, s, 0, 0, 0))
    try:
        from zoneinfo import ZoneInfo

        return int(datetime(y, mo, d, h, mi, s, tzinfo=ZoneInfo(tzid)).timestamp())
    except Exception:  # noqa: BLE001 - an unknown zone is not a reason to lose the event
        return _cal.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def read_ics(path: str) -> List[dict]:
    """VEVENTs become scheduled meetings.

    A single export is a snapshot, so it shows where meetings ended up, not that
    they moved. Move history needs either repeated exports or a live feed; until
    then `meeting_moves` has nothing to learn from imported calendars and the
    predictor will correctly fall back to its prior.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    raw = re.sub(r"\r?\n[ \t]", "", raw)  # unfold

    out: List[dict] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        fields: Dict[str, str] = {}
        zones: Dict[str, str] = {}
        attendees: List[str] = []
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            name = key.split(";")[0].upper()
            if name == "ATTENDEE":
                attendees.append(_cid(value.replace("mailto:", "")))
            else:
                fields[name] = value.strip()
                zone = re.search(r"TZID=([^;:]+)", key)
                if zone:
                    zones[name] = zone.group(1).strip()
        start = _ics_time(fields.get("DTSTART", ""), zones.get("DTSTART", ""))
        if start is None:
            continue
        end = _ics_time(fields.get("DTEND", ""), zones.get("DTEND", "")) or start + HOUR
        uid = fields.get("UID") or f"{start}"
        out.append({
            "ts": start - 7 * 24 * HOUR,  # it was on the calendar before it happened
            "kind": E.CALENDAR_SCHEDULED,
            "entity": "mt_" + hashlib.sha256(uid.encode()).hexdigest()[:14],
            "actor": E.ACTOR_USER,
            "payload": {
                "title": fields.get("SUMMARY", "(untitled)")[:200],
                "start": start, "end": end,
                "attendees": ["me"] + attendees[:6],
            },
        })
    return out


def ingest(store: EventStore, records: Iterable[dict], branch: str = TRUNK) -> dict:
    """Sort everything by world time and append. Order is the whole contract."""
    rows = sorted(records, key=lambda r: r["ts"])
    seen: set = set()
    written = skipped = contacts_skipped = 0

    for r in rows:
        contact = r.pop("_contact", None)
        if contact and contact[0] not in seen:
            seen.add(contact[0])
            try:
                # Importing a second, older archive backdates the first contact
                # it meets. That is a reason to skip one bookkeeping event, not
                # to throw away the whole import.
                store.append(branch=branch, kind=E.CONTACT_OBSERVED, entity=contact[0],
                             actor=E.ACTOR_WORLD, ts=r["ts"],
                             payload={"name": contact[1], "address": contact[2]})
            except Exception:  # noqa: BLE001
                contacts_skipped += 1  # a record is still a record; this is not one
        try:
            store.append(branch=branch, **r)
            written += 1
        except Exception:  # noqa: BLE001 - one bad record must not lose the import
            skipped += 1

    return {"written": written, "skipped": skipped,
            "contacts": len(seen) - contacts_skipped, "contacts_skipped": contacts_skipped,
            "span": [rows[0]["ts"], rows[-1]["ts"]] if rows else None}
