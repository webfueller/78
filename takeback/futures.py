"""Enumerating what could happen, exactly.

The uncertain things in a plan are independent binary outcomes with known
probabilities. That is a Poisson binomial, and its distribution is computable
exactly, so a branch map does not need sampling and two people running the same
rehearsal get the same picture.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

MAX_BRANCHES = 5


def count_distribution(ps: Sequence[float]) -> List[float]:
    """P(exactly k of these happen), for k in 0..n. Exact, O(n²)."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for k, pk in enumerate(dist):
            nxt[k] += pk * (1 - p)
            nxt[k + 1] += pk * p
        dist = nxt
    return dist


def enumerate_futures(ps: Sequence[float], keep: int = MAX_BRANCHES) -> List[Dict]:
    """Branch on *how many* things land, not on which exact combination.

    Enumerating combinations looks rigorous and produces a useless map: with
    eight open questions there are 256 futures and the top five cover a quarter
    of the probability. Nobody wants to know the odds that these three specific
    people answer and those five do not. They want to know how the week goes.

    Five counts typically cover most of the mass. For each count, the
    highest-probability composition is the one that assumes the most likely
    things are the ones that happened -- provably, since ranking by p also ranks
    by p/(1-p) -- so the representative future shown is the modal one for that
    count, not a guess.
    """
    n = len(ps)
    if n == 0:
        return [{"outcomes": [], "p": 1.0, "count": 0}]

    dist = count_distribution(ps)
    likeliest = sorted(range(n), key=lambda i: -ps[i])

    futures = []
    for count in sorted(range(n + 1), key=lambda k: -dist[k])[:keep]:
        hits = set(likeliest[:count])
        futures.append({
            "outcomes": [i in hits for i in range(n)],
            "p": dist[count],
            "count": count,
        })
    futures.sort(key=lambda f: -f["p"])
    return futures
