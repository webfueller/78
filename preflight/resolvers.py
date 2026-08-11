"""The questions this product lets itself be judged on.

A resolver reads the world as it actually turned out and answers one yes/no
question about a claim that was made before it. Adding one is how the product
takes on new risk: a claim with no resolver cannot be scored, and a claim that
cannot be scored is marketing.
"""

from __future__ import annotations

from typing import Dict

from rehearsal.ledger import Resolver

from .world import World


def reply_within(w: World, rec: dict) -> bool:
    t = w.threads.get(rec["params"]["thread"])
    if t is None:
        return False
    return t.reply_after(
        rec["params"]["contact"], rec["made_at"], rec["resolve_by"]
    ) is not None


def thread_dies(w: World, rec: dict) -> bool:
    return not reply_within(w, rec)


def meeting_moves(w: World, rec: dict) -> bool:
    m = w.meetings.get(rec["params"]["meeting"])
    if m is None:
        return False
    return any(rec["made_at"] < mv["ts"] <= rec["resolve_by"] for mv in m.moves)


def charge_recurs(w: World, rec: dict) -> bool:
    s = w.subscriptions.get(rec["params"]["subscription"])
    if s is None:
        return False
    return any(rec["made_at"] < ts <= rec["resolve_by"] for ts in s.charges)


RESOLVERS: Dict[str, Resolver] = {
    "reply_within": reply_within,
    "thread_dies": thread_dies,
    "meeting_moves": meeting_moves,
    "charge_recurs": charge_recurs,
}
