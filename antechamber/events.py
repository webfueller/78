"""Events are the only thing that is true.

Everything else in this system is a projection. An event is immutable, ordered
within its branch, and chained by hash to the event before it -- so a replay
either reproduces the recorded chain exactly or it fails loudly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Optional

GENESIS = "0" * 64

# World-state kinds. Anything an agent proposes in a fork uses the same kinds a
# real observation would, so a rehearsed week and a lived week project through
# identical code.
MESSAGE_RECEIVED = "message.received"
MESSAGE_SENT = "message.sent"
CONTACT_OBSERVED = "contact.observed"
CALENDAR_SCHEDULED = "calendar.scheduled"
CALENDAR_MOVED = "calendar.moved"
CALENDAR_CANCELLED = "calendar.cancelled"
SUBSCRIPTION_OBSERVED = "money.subscription_observed"
SUBSCRIPTION_CHARGED = "money.charged"
SUBSCRIPTION_CANCELLED = "money.cancelled"

# Ledger kinds.
PREDICTION_MADE = "prediction.made"
PREDICTION_RESOLVED = "prediction.resolved"

# Commit kinds.
COMMIT_OPENED = "commit.opened"
COMMIT_SEALED = "commit.sealed"
COMMIT_UNDONE = "commit.undone"

# Actors.
ACTOR_WORLD = "world"
ACTOR_USER = "user"
ACTOR_AGENT = "agent"
SIM_PREFIX = "sim:"


def is_simulated(actor: str) -> bool:
    """Simulated counterparties are quarantined by actor, not by convention.

    A `sim:` event may never reach the trunk. This is the single mechanical
    guarantee behind the rule that a simulated person's words never escape the
    owner's private preview.
    """
    return actor.startswith(SIM_PREFIX)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(
    prev: str,
    branch: str,
    seq: int,
    ts: int,
    kind: str,
    entity: str,
    actor: str,
    payload: dict,
) -> str:
    body = canonical(
        {
            "prev": prev,
            "branch": branch,
            "seq": seq,
            "ts": ts,
            "kind": kind,
            "entity": entity,
            "actor": actor,
            "payload": payload,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Event:
    branch: str
    seq: int
    ts: int
    kind: str
    entity: str
    actor: str
    payload: dict
    prev: str
    hash: str
    gid: int = 0
    commit_id: Optional[str] = None

    @property
    def simulated(self) -> bool:
        return is_simulated(self.actor)

    def recompute(self) -> str:
        return digest(
            self.prev,
            self.branch,
            self.seq,
            self.ts,
            self.kind,
            self.entity,
            self.actor,
            self.payload,
        )
