"""Vote allocation strategies.

V1 supports:
  - winner-take-all: 100% to the top-ranked pool.
  - top-N weighted: split across the top N pools proportional to their scores,
    subject to a min-weight floor and a max-pool-count cap from the Voter
    contract (maxVotingNum).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .ranking import RankedPool


@dataclass(frozen=True)
class Allocation:
    pool: RankedPool
    weight_pct: float  # 0..100


def winner_take_all(ranked: List[RankedPool]) -> List[Allocation]:
    if not ranked:
        return []
    return [Allocation(pool=ranked[0], weight_pct=100.0)]


def top_n_weighted(
    ranked: List[RankedPool],
    n: int,
    max_pools: int,
    min_weight_pct: float = 5.0,
) -> List[Allocation]:
    """Score-weighted split across the top min(n, max_pools, len(ranked)) pools.

    Drops any pool whose proportional share would fall under min_weight_pct
    (wasted vote on the contract, since weights get normalized to integers
    anyway). Re-normalizes after the cut.
    """
    if not ranked or n <= 0:
        return []
    take = min(n, max_pools, len(ranked))
    picks = ranked[:take]
    total = sum(p.score for p in picks)
    if total <= 0:
        return []
    allocs = [Allocation(pool=p, weight_pct=100.0 * p.score / total) for p in picks]
    survivors = [a for a in allocs if a.weight_pct >= min_weight_pct]
    if not survivors:
        return [Allocation(pool=picks[0], weight_pct=100.0)]
    s = sum(a.weight_pct for a in survivors)
    return [Allocation(pool=a.pool, weight_pct=100.0 * a.weight_pct / s) for a in survivors]
