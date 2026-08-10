"""A plausible working life, generated deterministically.

Real ingestion (mail, calendar) lands in weeks 3-4 behind the same event kinds.
Until then this exists so the twin, the fork, and the scoreboard can be exercised
end to end without asking anyone for mailbox access -- and because a seeded
generator gives the tests ground truth that a real mailbox never could.
"""

from __future__ import annotations

import random
from typing import List

from . import events as E
from .store import TRUNK, EventStore

HOUR = 3600
DAY = 24 * HOUR

# Each contact has a temperament. The per-contact predictor exists to find these
# numbers; the global predictor is forbidden from seeing them. That gap is the
# lift the scoreboard has to be able to measure.
CONTACTS = [
    # id,             name,             reply_p, mean_latency_h, reschedule_p
    ("ana.reyes", "Ana Reyes", 0.90, 5, 0.10),
    ("tom.brennan", "Tom Brennan", 0.25, 60, 0.55),
    ("priya.nandi", "Priya Nandi", 0.80, 20, 0.15),
    ("marek.villa", "Marek Villa", 0.50, 40, 0.40),
    ("dana.oyelaran", "Dana Oyelaran", 0.70, 12, 0.20),
    ("landlord", "R. Kestner (landlord)", 0.35, 90, 0.05),
    ("billing.notices", "Billing Notices", 0.02, 200, 0.00),
]

SUBJECTS = [
    "Q3 numbers", "the Halstead contract", "next week", "invoice 2291",
    "moving the standup", "deposit return", "renewal terms", "the draft",
    "handover notes", "budget line 4", "access request", "site visit",
]

MERCHANTS = [
    ("sub_relay", "Relay Storage", 1200),
    ("sub_atlas", "Atlas Analytics", 4900),
    ("sub_kiln", "Kiln CI", 2900),
    ("sub_paper", "Paper Weekly", 700),
]


def seed_world(store: EventStore, days: int = 120, seed: int = 7, start_ts: int = 1_735_689_600) -> int:
    """Write `days` of history onto the trunk. Returns the final world time."""
    rng = random.Random(seed)
    now = start_ts

    for cid, name, *_ in CONTACTS:
        store.append(
            branch=TRUNK, kind=E.CONTACT_OBSERVED, entity=cid, actor=E.ACTOR_WORLD, ts=now,
            payload={"name": name, "address": f"{cid}@example.net"},
        )

    for sid, merchant, cents in MERCHANTS:
        store.append(
            branch=TRUNK, kind=E.SUBSCRIPTION_OBSERVED, entity=sid, actor=E.ACTOR_WORLD, ts=now,
            payload={"merchant": merchant, "amount_cents": cents, "period": "monthly"},
        )

    pending: List[dict] = []   # replies owed to us, already decided but not yet due
    meetings: List[dict] = []  # scheduled meetings, with their fate already rolled
    thread_n = 0

    for day in range(days):
        day_start = start_ts + day * DAY
        weekday = (day % 7) < 5

        # --- deliver everything that came due today, in order -----------------
        due = sorted(
            [p for p in pending if p["at"] <= day_start + DAY] +
            [m for m in meetings if m.get("move_at") and m["move_at"] <= day_start + DAY],
            key=lambda x: x.get("at") or x["move_at"],
        )
        for item in due:
            if "thread" in item:
                pending.remove(item)
                now = max(now, item["at"])
                store.append(
                    branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity=item["thread"],
                    actor=E.ACTOR_WORLD, ts=now,
                    payload={
                        "sender": item["contact"],
                        "counterparty": item["contact"],
                        "subject": item["subject"],
                        "body": item["body"],
                    },
                )
            else:
                now = max(now, item["move_at"])
                item["start"] += rng.choice([DAY, 2 * DAY, 3 * DAY])
                store.append(
                    branch=TRUNK, kind=E.CALENDAR_MOVED, entity=item["id"],
                    actor=E.ACTOR_WORLD, ts=now,
                    payload={"start": item["start"], "end": item["start"] + HOUR},
                )
                item["move_at"] = None

        if not weekday:
            continue

        # --- new threads ------------------------------------------------------
        for _ in range(rng.randint(1, 3)):
            cid, name, reply_p, latency, _ = rng.choice(CONTACTS)
            thread_n += 1
            tid = f"th_{thread_n:04d}"
            subject = rng.choice(SUBJECTS)
            now = max(now, day_start + rng.randint(8 * HOUR, 17 * HOUR))
            store.append(
                branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity=tid, actor=E.ACTOR_WORLD, ts=now,
                payload={"sender": cid, "counterparty": cid, "subject": subject,
                         "body": f"Following up on {subject}."},
            )
            # We answer most, but not all, of what arrives.
            if rng.random() < 0.75:
                now += rng.randint(HOUR, 8 * HOUR)
                store.append(
                    branch=TRUNK, kind=E.MESSAGE_SENT, entity=tid, actor=E.ACTOR_USER, ts=now,
                    payload={"sender": "me", "counterparty": cid, "subject": subject,
                             "body": "Thanks -- here's where that stands."},
                )
                if rng.random() < reply_p:
                    pending.append({
                        "thread": tid, "contact": cid, "subject": subject,
                        "at": now + int(rng.expovariate(1 / (latency * HOUR))) + HOUR,
                        "body": "Understood, coming back to you.",
                    })

        # --- meetings ---------------------------------------------------------
        if rng.random() < 0.45:
            cid, name, _, _, reschedule_p = rng.choice(CONTACTS[:6])
            mid = f"mt_{day:03d}"
            start = day_start + rng.randint(3, 9) * DAY + rng.randint(9, 16) * HOUR
            now = max(now, day_start + 9 * HOUR)
            store.append(
                branch=TRUNK, kind=E.CALENDAR_SCHEDULED, entity=mid, actor=E.ACTOR_USER, ts=now,
                payload={"title": f"{name} / {rng.choice(SUBJECTS)}", "start": start,
                         "end": start + HOUR, "attendees": ["me", cid]},
            )
            m = {"id": mid, "start": start, "move_at": None}
            if rng.random() < reschedule_p:
                m["move_at"] = start - rng.randint(HOUR, 2 * DAY)
            meetings.append(m)

        # --- money ------------------------------------------------------------
        if day % 30 == 12:
            for sid, _, _ in MERCHANTS:
                now = max(now, day_start + 6 * HOUR)
                store.append(
                    branch=TRUNK, kind=E.SUBSCRIPTION_CHARGED, entity=sid,
                    actor=E.ACTOR_WORLD, ts=now, payload={},
                )

    return now
