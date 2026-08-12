"""Preflight -- rehearse the week before you live it.

A life made of mail, meetings and money, projected from an append-only log,
forked so an agent can run a week inside the copy, and committed one branch at a
time with a receipt and an undo window behind it.

The log, the fork, the receipt, the undo and the prediction ledger are not this
package -- they are `takeback`, and they know nothing about mail. What is here
is the domain: what an event means, what a plan is worth, and which claims this
product allows itself to be judged on.

There is no language model in here. That is the point.
"""

from takeback.store import TRUNK, EventStore, StoreError
from .world import World, project

__all__ = ["EventStore", "StoreError", "TRUNK", "World", "project"]
__version__ = "0.1.0"
