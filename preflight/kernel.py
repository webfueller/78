"""Where this domain meets the kernel.

One object, built once: the world it projects, the questions it can be scored
on, and the numbers it scores plans with. Everything the product does to the log
goes through here, and everything here is `rehearsal` code with mail poured in.
"""

from __future__ import annotations

from rehearsal import Kernel

from .resolvers import RESOLVERS
from .scoring import PRIOR
from .world import World

KERNEL = Kernel(projection=World, resolvers=RESOLVERS, prior=PRIOR)
