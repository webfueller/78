"""Antechamber -- the twin.

Weeks 1-2: an append-only, hash-chained log of a life; forks that rewind it;
deterministic replay; commits that promote only what the agent authored, with a
receipt and an undo window; and a prediction ledger that scores every claim
against what actually happened.

There is no model in here. That is the point.
"""

from .store import TRUNK, EventStore, StoreError
from .world import World, project

__all__ = ["EventStore", "StoreError", "TRUNK", "World", "project"]
__version__ = "0.1.0"
