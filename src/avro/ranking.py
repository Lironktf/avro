"""Per-pool score and expected payout.

score_i = adjustedRewardUSD_i / forecastVotes_i

Expected payout for your veAERO allocation x_i into pool i:
    P_i(x_i) = adjustedRewardUSD_i * x_i / (forecastVotes_i + x_i)

For small x_i (your veAERO ≪ pool votes), this collapses to:
    P_i ≈ score_i * x_i

Both are computed; the CLI prefers the exact form so it stays correct for
large veNFTs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .forecast import ForecastedVotes
from .pricing import PoolValuation


@dataclass(frozen=True)
class RankedPool:
    valuation: PoolValuation
    forecast: ForecastedVotes
    score: float                 # USD per veAERO of forecast votes

    @property
    def pool_address(self) -> str:
        return self.valuation.snapshot.pool_address

    def expected_payout_usd(self, your_veaero: float) -> float:
        if your_veaero <= 0:
            return 0.0
        denom = self.forecast.forecast_votes_veaero + your_veaero
        if denom <= 0:
            return 0.0
        return self.valuation.adjusted_usd * your_veaero / denom


def rank_pools(
    valuations: Iterable[PoolValuation],
    forecasts: dict,  # pool_address -> ForecastedVotes
) -> List[RankedPool]:
    ranked: List[RankedPool] = []
    for v in valuations:
        f = forecasts[v.snapshot.pool_address]
        if v.adjusted_usd <= 0:
            continue
        if f.forecast_votes_veaero <= 0:
            continue
        score = v.adjusted_usd / f.forecast_votes_veaero
        ranked.append(RankedPool(valuation=v, forecast=f, score=score))
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked
