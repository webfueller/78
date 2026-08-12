"""What this domain thinks a good week looks like.

The feature names below are the only place the kernel's weight-learning touches
mail: it fits and evaluates whatever numbers it is handed and never asks what
they mean. These are the numbers, and the guesses they start from.
"""

from __future__ import annotations

from typing import Dict

# The features a plan is judged on, and the numbers I guessed for them. These
# stay as the prior the fit is pulled toward, so the model degrades to my guess
# rather than to nonsense.
PRIOR: Dict[str, float] = {
    "reply": 1.0,
    "per_action": -0.25,
    "burn_saved_per_1000c": 0.5,
    "late_surprise": -1.0,
    # A reply is not a reply. These two say which replies were worth having:
    # money the thread names, and whether it has a clock on it. Both are guesses
    # like the four above and both are fitted from what you commit. Setting
    # value_at_risk_k to 1.0 says a thousand euros in play is worth about one
    # answered nudge, which is a starting point and nothing more.
    "value_at_risk_k": 1.0,
    "deadline_pressure": 0.75,
}


def features(expected: dict, actions: int) -> Dict[str, float]:
    """A plan as the numbers the scoring cares about."""
    return {
        "reply": float(expected["replies"]),
        "per_action": float(actions),
        "burn_saved_per_1000c": float(expected["burn_saved_cents"]) / 1000.0,
        "late_surprise": float(expected["late_surprises"]),
        "value_at_risk_k": float(expected.get("value_at_risk_k", 0.0)),
        "deadline_pressure": float(expected.get("deadline_pressure", 0.0)),
    }
