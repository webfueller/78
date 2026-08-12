"""The prediction ledger, pointed at mail.

The machinery -- writing a claim, resolving it when its moment arrives, scoring
the lot against a leave-one-out base rate -- is `takeback.ledger`. What is here
is this domain's four questions and the module-level shape the rest of the app
calls.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from takeback.ledger import LEDGER, brier, leave_one_out_base_rates  # noqa: F401
from takeback.store import EventStore

from .kernel import KERNEL
from .resolvers import RESOLVERS  # noqa: F401  (re-exported: the questions we answer)

_LEDGER = KERNEL.ledger


def prediction_id(origin: str, resolver: str, params: dict, made_at: int) -> str:
    return _LEDGER.prediction_id(origin, resolver, params, made_at)


def record(store: EventStore, **kw) -> str:
    return _LEDGER.record(store, **kw)


def ledger(store: EventStore) -> Dict[str, dict]:
    return _LEDGER.all(store)


def resolve_due(store: EventStore, now: Optional[int] = None) -> List[dict]:
    return _LEDGER.resolve_due(store, now=now)


def score(store: EventStore, predictor: Optional[str] = None) -> dict:
    return _LEDGER.score(store, predictor=predictor)
