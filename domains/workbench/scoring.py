"""What this workbench thinks a good change set looks like.

Four numbers, all guesses, all replaced by weights fitted to what somebody
actually commits once there are enough commits to learn from. `check_risk` at
-3.0 says one expected red build costs about three applied edits, which is a
starting point and an argument, not a measurement.
"""

from __future__ import annotations

from typing import Dict

PRIOR: Dict[str, float] = {
    "applied": 1.0,
    "check_risk": -3.0,
    "churn": -0.5,
    "per_action": -0.1,
}


def features(expected: dict, actions: int) -> Dict[str, float]:
    return {
        "applied": float(expected["applied"]),
        "check_risk": float(expected["check_risk"]),
        "churn": float(expected["churn"]),
        "per_action": float(actions),
    }
