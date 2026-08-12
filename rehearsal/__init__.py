"""rehearsal — let an agent rehearse, then commit, with a receipt and an undo.

Nothing an agent does here is real until it is committed. Before that it lives on
a fork of an append-only, hash-chained log, where it can be read, scored and
thrown away. After it, it is one atomic transaction with a receipt, a withdrawal
window, and a state hash proving the undo put things back exactly.

The kernel supplies six things and knows nothing about any particular domain:

  store        an append-only hash-chained log that forks and rewinds
  projection   deterministic fold from events to state, with a stable hash
  commits      promote a fork to the trunk, atomically; take it back
  ledger       claims with resolvers and due dates, scored against a baseline
  preferences  scoring weights learned from what was actually committed
  futures      exact enumeration of what could happen

A domain provides a `Projection` subclass, a resolver per question it wants to be
held to, and a prior over the features it scores plans on. `Kernel` binds them:

    kernel = Kernel(projection=World, resolvers=RESOLVERS, prior=PRIOR)
    kernel.commit(store, branch)

The kernel names no domain, on purpose, and a test enforces it: the moment this
package knows which product it is serving, it has stopped being a kernel.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Type

from .anchor import Anchor, AnchorError
from .commits import UNDO_WINDOW, Commits, already_promoted, promotable
from .events import (
    ACTOR_AGENT,
    ACTOR_USER,
    ACTOR_WORLD,
    GENESIS,
    REAL_ACTORS,
    SIM_PREFIX,
    Event,
    canonical,
    digest,
    is_simulated,
)
from .futures import count_distribution, enumerate_futures
from .ledger import LEDGER, Ledger, Resolver, brier
from .preferences import PREFERENCES, Preferences
from .projection import Projection
from .store import TRUNK, EventStore, StoreError

__version__ = "0.1.0"

__all__ = [
    "ACTOR_AGENT", "ACTOR_USER", "ACTOR_WORLD", "Anchor", "AnchorError",
    "Commits", "Event", "EventStore",
    "GENESIS", "Kernel", "LEDGER", "Ledger", "PREFERENCES", "Preferences",
    "Projection", "REAL_ACTORS", "Resolver", "SIM_PREFIX", "StoreError", "TRUNK",
    "UNDO_WINDOW", "already_promoted", "brier", "canonical", "count_distribution",
    "digest", "enumerate_futures", "is_simulated", "promotable",
]


class Kernel:
    """One domain's binding of the six parts."""

    def __init__(
        self,
        projection: Type[Projection],
        resolvers: Optional[Mapping[str, Resolver]] = None,
        prior: Optional[Dict[str, float]] = None,
        undo_window: int = UNDO_WINDOW,
        anchor=None,
    ):
        self.projection = projection
        self.ledger = Ledger(projection, resolvers or {})
        self.preferences = Preferences(prior) if prior else None
        self.commits = Commits(projection, self.preferences, undo_window=undo_window,
                               anchor=anchor)

    def project(self, evs, include_simulated: bool = True) -> Projection:
        return self.projection.fold(evs, include_simulated=include_simulated)

    def commit(self, store: EventStore, branch: str, at_ts: Optional[int] = None) -> dict:
        return self.commits.commit(store, branch, at_ts=at_ts)

    def undo(self, store: EventStore, commit_id: str, at_ts: Optional[int] = None) -> dict:
        return self.commits.undo(store, commit_id, at_ts=at_ts)
