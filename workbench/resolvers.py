"""The two questions this workbench lets itself be judged on.

Both are answerable from the log alone, which is the bar: a claim nobody can
settle without asking a human is not a claim, it is a mood.
"""

from __future__ import annotations

from typing import Dict

from rehearsal.ledger import Resolver

from .state import Tree


def check_fails(t: Tree, rec: dict) -> bool:
    """Did the checks go red in the window after this was applied?"""
    return t.check_failed_between(rec["made_at"], rec["resolve_by"])


def rewritten_within(t: Tree, rec: dict) -> bool:
    """Did this file need touching again soon after?

    Churn is the honest proxy for "the edit was not right". It cannot tell an
    incomplete change from a fashionable one, and it does not need to: what it
    measures is that the file was not finished, which is what the person asking
    for a preview wants to know.
    """
    return t.rewritten_between(rec["params"]["path"], rec["made_at"], rec["resolve_by"])


RESOLVERS: Dict[str, Resolver] = {
    "check_fails": check_fails,
    "rewritten_within": rewritten_within,
}
