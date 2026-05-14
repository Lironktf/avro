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
    # Raw pool reserves (one entry per side). Lossless: keeps token addresses,
    # raw amounts, and Sugar's price-at-capture-time so we can reprice or
    # recompute strong-paired liquidity later under different policies.
    reserves: List[RewardAmount] = field(default_factory=list)
    # On-chain AERO emissions accrued to this pool this epoch (raw 18-decimal int).
    emissions_raw: int = 0
    # LP swap fee in basis-point-ish units as Sugar reports (e.g. 0.003 = 30 bps).
    pool_fee: float = 0.0

    @property
    def votes_veaero(self) -> float:
        # veAERO uses 18 decimals (same as AERO).
        return self.votes_raw / 1e18

    @property
    def gross_reward_usd(self) -> float:
        return sum(r.usd_value for r in self.fees) + sum(r.usd_value for r in self.incentives)

    @property
    def reserves_usd_by_token(self) -> dict:
        """Per-token USD reserves derived from `reserves`. Strong-paired liquidity
        computation reads this. Kept as a property (not a stored field) so the
        underlying raw data is always the source of truth."""
        out: dict = {}
        for r in self.reserves:
            if r.usd_per_token <= 0:
                continue
            out[r.token_address] = out.get(r.token_address, 0.0) + r.usd_value
        return out


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


def _pool_reserves(pool) -> List[RewardAmount]:
    """Capture both pool sides as RewardAmount records: token + raw amount + price.

    Lossless — preserves enough to reprice with a different oracle later, or
    recompute strong-paired liquidity under a different allowlist.
    """
    out: List[RewardAmount] = []
    for side in (pool.reserve0, pool.reserve1):
        # `side.amount` is a float in Sugar's Amount; convert to raw int.
        decimals = int(side.token.decimals)
        amt = side.amount
        amount_raw = int(round(float(amt) * (10 ** decimals))) if not isinstance(amt, int) else int(amt)
        out.append(RewardAmount(
            token_address=side.token.token_address,
            symbol=side.token.symbol,
            decimals=decimals,
            amount_raw=amount_raw,
            usd_per_token=float(getattr(side.price, "price", 0.0) or 0.0),
            listed=bool(getattr(side.token, "listed", False)),
        ))
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
                reserves=_pool_reserves(pool),
                emissions_raw=int(getattr(ep, "emissions", 0) or 0),
                pool_fee=float(getattr(pool, "pool_fee", 0.0) or 0.0),
            )
        )
    return snapshots
