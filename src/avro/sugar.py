"""Thin wrapper over sugar-sdk BaseChain.

Returns plain dataclasses instead of leaking sugar's types into the rest of the
codebase, so the ranking/pricing math is testable with synthetic inputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, List

from sugar.chains import BaseChain


@dataclass(frozen=True)
class RewardAmount:
    token_address: str
    symbol: str
    decimals: int
    amount_raw: int       # on-chain integer units
    usd_per_token: float  # from Sugar's price oracle; 0.0 if unpriceable
    listed: bool = False  # Sugar's curated-list flag; treat unlisted as suspect

    @property
    def amount_human(self) -> float:
        return self.amount_raw / (10 ** self.decimals)

    @property
    def usd_value(self) -> float:
        return self.amount_human * self.usd_per_token


@dataclass(frozen=True)
class PoolSnapshot:
    pool_address: str          # the LP/pool address used by Voter.gauges(pool)
    symbol: str
    is_cl: bool
    is_stable: bool
    epoch_ts: int
    votes_raw: int             # current veAERO weight on this pool, 18 decimals
    fees: List[RewardAmount] = field(default_factory=list)
    incentives: List[RewardAmount] = field(default_factory=list)
    # Aggregate Aerodrome-side reserves USD per token in *this* pool. Used to
    # build a per-token TVL map for haircut tiering. Keyed by token address.
    reserves_usd_by_token: dict = field(default_factory=dict)

    @property
    def votes_veaero(self) -> float:
        # veAERO uses 18 decimals (same as AERO).
        return self.votes_raw / 1e18

    @property
    def gross_reward_usd(self) -> float:
        return sum(r.usd_value for r in self.fees) + sum(r.usd_value for r in self.incentives)


def _to_reward_amount(amt) -> RewardAmount:
    """Convert sugar.pool.Amount → RewardAmount."""
    return RewardAmount(
        token_address=amt.token.token_address,
        symbol=amt.token.symbol,
        decimals=amt.token.decimals,
        amount_raw=int(amt.amount),
        usd_per_token=float(getattr(amt.price, "price", 0.0) or 0.0),
        listed=bool(getattr(amt.token, "listed", False)),
    )


def _pool_reserves_usd(pool) -> dict:
    """Compute USD value of each side of this pool's reserves, keyed by address."""
    out: dict[str, float] = {}
    for side in (pool.reserve0, pool.reserve1):
        addr = side.token.token_address
        decimals = side.token.decimals
        price = float(getattr(side.price, "price", 0.0) or 0.0)
        if price <= 0:
            continue
        human = side.amount / (10 ** decimals) if isinstance(side.amount, int) else float(side.amount)
        out[addr] = out.get(addr, 0.0) + human * price
    return out


@contextmanager
def base_chain(rpc_uri: str) -> Iterator[BaseChain]:
    """Open a BaseChain context. Sugar reads SUGAR_RPC_URI_8453 from env, but
    we pass it explicitly to avoid surprises."""
    with BaseChain(rpc_uri=rpc_uri) as chain:
        yield chain


def fetch_snapshots(chain: BaseChain) -> List[PoolSnapshot]:
    """Pull current epoch state for every Aerodrome pool with a gauge.

    Sugar's get_latest_pool_epochs returns one LiquidityPoolEpoch per pool with
    a gauge. Pools without gauges (no voting) are not returned.
    """
    epochs = chain.get_latest_pool_epochs()
    snapshots: List[PoolSnapshot] = []
    for ep in epochs:
        pool = ep.pool
        snapshots.append(
            PoolSnapshot(
                pool_address=pool.lp,
                symbol=pool.symbol,
                is_cl=bool(pool.is_cl),
                is_stable=bool(pool.is_stable),
                epoch_ts=int(ep.ts),
                votes_raw=int(ep.votes),
                fees=[_to_reward_amount(a) for a in ep.fees],
                incentives=[_to_reward_amount(a) for a in ep.incentives],
                reserves_usd_by_token=_pool_reserves_usd(pool),
            )
        )
    return snapshots
