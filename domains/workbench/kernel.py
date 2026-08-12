"""Where this domain meets the kernel."""

from __future__ import annotations

from rehearsal import Kernel

from .resolvers import RESOLVERS
from .scoring import PRIOR
from .state import Tree

KERNEL = Kernel(projection=Tree, resolvers=RESOLVERS, prior=PRIOR)
