"""Events are the only thing that is true.

Everything else in this system is a projection. An event is immutable, ordered
within its branch, and chained by hash to the event before it -- so a replay
either reproduces the recorded chain exactly or it fails loudly.

Nothing here knows what an event is *about*. The kinds defined below are the
kernel's own bookkeeping -- claims, commits, choices. A domain brings its own
kinds and the log neither validates nor interprets them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Optional

GENESIS = "0" * 64

# Ledger kinds.
PREDICTION_MADE = "prediction.made"
PREDICTION_RESOLVED = "prediction.resolved"

# Commit kinds.
COMMIT_OPENED = "commit.opened"
COMMIT_SEALED = "commit.sealed"
COMMIT_UNDONE = "commit.undone"

# Preference kinds.
CHOICE_OFFERED = "choice.offered"
CHOICE_MADE = "choice.made"

KERNEL_KINDS = frozenset({
    PREDICTION_MADE, PREDICTION_RESOLVED,
    COMMIT_OPENED, COMMIT_SEALED, COMMIT_UNDONE,
    CHOICE_OFFERED, CHOICE_MADE,
})

# Actors.
ACTOR_WORLD = "world"
ACTOR_USER = "user"
ACTOR_AGENT = "agent"
SIM_PREFIX = "sim:"

# What the trunk will accept. A whitelist rather than a `sim:` blacklist: the
# quarantine should not depend on nobody ever writing "SIM:" or a Cyrillic s.
REAL_ACTORS = frozenset({ACTOR_WORLD, ACTOR_USER, ACTOR_AGENT})


def is_simulated(actor: str) -> bool:
    """Simulated counterparties are quarantined by actor, not by convention.

    A `sim:` event may never reach the trunk. This is the single mechanical
    guarantee behind the rule that a simulated person's words never escape the
    owner's private preview.
    """
    # Normalised, because `promotable` and the projection both lean on this and
    # "SIM:ana" or " sim:ana" reading as real would be a quiet disaster. The
    # actual guarantee is the REAL_ACTORS whitelist on the trunk above; this is
    # the second lock, not the first.
    cleaned = actor.replace("\u200b", "").replace("\ufeff", "").strip().casefold()
    return cleaned.startswith(SIM_PREFIX)


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
