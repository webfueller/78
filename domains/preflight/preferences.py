"""Weights learned from what you actually commit, pointed at mail.

The conditional logit, the ridge, the leave-one-out gate and the refusal to
claim a preference it has not earned are all `takeback.preferences`. What is
here is this domain's feature names, its starting guesses, and the module-level
shape the rest of the app calls.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from takeback.preferences import (  # noqa: F401
    CHOSEN,
    MIN_CHOICES,
    OFFERED,
    PREFERENCES,
    RIDGE,
    chance_top1,
)
from takeback.store import EventStore

from .kernel import KERNEL
from .scoring import PRIOR, features  # noqa: F401  (re-exported: what a plan is judged on)

_PREFS = KERNEL.preferences

KEYS: Tuple[str, ...] = _PREFS.keys


def utility(f: Dict[str, float], weights: Dict[str, float]) -> float:
    return _PREFS.utility(f, weights)


def record_offer(store: EventStore, rehearsal: str, options: Sequence[dict], at: int) -> bool:
    return _PREFS.record_offer(store, rehearsal, options, at)


def record_choice(store: EventStore, branch: str, at: int) -> Optional[str]:
    return _PREFS.record_choice(store, branch, at)


def decline(store: EventStore, branch: str, at: Optional[int] = None) -> dict:
    return _PREFS.decline(store, branch, at=at)


def choices(store: EventStore) -> List[dict]:
    return _PREFS.choices(store)


def fit(data: Sequence[dict], prior: Optional[Dict[str, float]] = None, **kw) -> Dict[str, float]:
    return _PREFS.fit(data, prior=prior, **kw)


def log_loss(data: Sequence[dict], weights: Dict[str, float]) -> float:
    return _PREFS.log_loss(data, weights)


def top1(data: Sequence[dict], weights: Dict[str, float]) -> float:
    return _PREFS.top1(data, weights)


def evaluate(data: Sequence[dict], prior: Optional[Dict[str, float]] = None) -> dict:
    return _PREFS.evaluate(data, prior=prior)


def effective_weights(store: EventStore) -> Tuple[Dict[str, float], dict]:
    return _PREFS.effective_weights(store)


def _norm(w: Dict[str, float]) -> float:
    return _PREFS._norm(w)
