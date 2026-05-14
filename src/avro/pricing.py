"""USD valuation of reward bundles with tiered haircuts.

Tier assignment for a reward token:
  1. token in strong_allowlist                   → strong
  2. token in junk_blocklist                     → junk
  3. no Sugar price                              → junk
  4. unlisted (Sugar curated flag = False) and   → junk
     not in strong_allowlist
  5. strong-paired liquidity ≥ mid_floor_usd     → mid
  6. strong-paired liquidity ≥ weak_floor_usd    → weak
  7. otherwise                                   → junk

"Strong-paired liquidity" = the USD value of strong-tier (USDC/WETH/cbBTC/AERO)
reserves this token is paired against across Aerodrome pools. This proxies
"how much I could realistically exit into a real asset," which is what we
actually care about for reward valuation.

A pool's reward bundle is then `Σ reward.usd_value × tier_haircut`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Literal

from .config import HaircutTiers
from .sugar import PoolSnapshot, RewardAmount

Tier = Literal["strong", "mid", "weak", "junk"]


@dataclass(frozen=True)
class TokenTier:
    address: str
    symbol: str
    tier: Tier
    haircut: float
    strong_paired_usd: float
    listed: bool


def strong_paired_liquidity(
    snapshots: Iterable[PoolSnapshot],
    strong_allowlist: FrozenSet[str],
) -> Dict[str, float]:
    """For each non-strong token, sum the USD value of strong-tier reserves it's
    paired against across all pools. This is the realistic-exit-liquidity proxy.

    Example: USDC/STAR with $50k USDC + $50k STAR reserves
      → STAR.strong_paired_usd += $50k (the USDC side)
      → USDC is in strong_allowlist already, so we don't credit it.
    """
    out: Dict[str, float] = {}
    for s in snapshots:
        items = list(s.reserves_usd_by_token.items())
        if len(items) != 2:
            continue
        (a_addr, a_usd), (b_addr, b_usd) = items
        a_low, b_low = a_addr.lower(), b_addr.lower()
        a_strong = a_low in strong_allowlist
        b_strong = b_low in strong_allowlist
        if a_strong and not b_strong:
            out[b_low] = out.get(b_low, 0.0) + a_usd
        elif b_strong and not a_strong:
            out[a_low] = out.get(a_low, 0.0) + b_usd
    return out


def classify_token(
    address: str,
    symbol: str,
    strong_paired_usd: float,
    tiers: HaircutTiers,
    has_price: bool,
    listed: bool,
) -> TokenTier:
    addr = address.lower()
    if addr in tiers.junk_blocklist:
        return TokenTier(addr, symbol, "junk", tiers.junk, strong_paired_usd, listed)
    if addr in tiers.strong_allowlist:
        return TokenTier(addr, symbol, "strong", tiers.strong, strong_paired_usd, listed)
    if not has_price:
        return TokenTier(addr, symbol, "junk", tiers.junk, strong_paired_usd, listed)
    if not listed:
        return TokenTier(addr, symbol, "junk", tiers.junk, strong_paired_usd, listed)
    if strong_paired_usd >= tiers.mid_floor_usd:
        return TokenTier(addr, symbol, "mid", tiers.mid, strong_paired_usd, listed)
    if strong_paired_usd >= tiers.weak_floor_usd:
        return TokenTier(addr, symbol, "weak", tiers.weak, strong_paired_usd, listed)
    return TokenTier(addr, symbol, "junk", tiers.junk, strong_paired_usd, listed)


def build_token_tiers(
    snapshots: Iterable[PoolSnapshot],
    tiers: HaircutTiers,
) -> Dict[str, TokenTier]:
    """Classify every token that appears as a fee/incentive across all snapshots."""
    snapshots = list(snapshots)
    paired = strong_paired_liquidity(snapshots, tiers.strong_allowlist)
    seen: Dict[str, RewardAmount] = {}
    for s in snapshots:
        for r in [*s.fees, *s.incentives]:
            seen.setdefault(r.token_address.lower(), r)
    out: Dict[str, TokenTier] = {}
    for addr, r in seen.items():
        out[addr] = classify_token(
            address=addr,
            symbol=r.symbol,
            strong_paired_usd=paired.get(addr, 0.0),
            tiers=tiers,
            has_price=r.usd_per_token > 0,
            listed=r.listed,
        )
    return out


@dataclass(frozen=True)
class PoolValuation:
    snapshot: PoolSnapshot
    gross_usd: float
    adjusted_usd: float
    fee_usd_adjusted: float
    incentive_usd_adjusted: float
    # addr → (symbol, raw_usd, adj_usd, tier)
    contributions: dict
    # True if any reward token in the bundle is junk-tier (informational filter).
    contains_junk: bool


def value_pool(
    snapshot: PoolSnapshot,
    token_tiers: Dict[str, TokenTier],
) -> PoolValuation:
    contributions: dict = {}
    fee_adj = 0.0
    incentive_adj = 0.0
    contains_junk = False

    def add(amounts: list[RewardAmount], is_fee: bool) -> None:
        nonlocal fee_adj, incentive_adj, contains_junk
        for r in amounts:
            tier = token_tiers.get(r.token_address.lower())
            haircut = tier.haircut if tier else 0.0
            tier_name = tier.tier if tier else "junk"
            if tier_name == "junk" and r.usd_value > 0:
                contains_junk = True
            raw = r.usd_value
            adj = raw * haircut
            if is_fee:
                fee_adj += adj
            else:
                incentive_adj += adj
            prev = contributions.get(r.token_address.lower(), (r.symbol, 0.0, 0.0, tier_name))
            contributions[r.token_address.lower()] = (
                r.symbol,
                prev[1] + raw,
                prev[2] + adj,
                prev[3],
            )

    add(snapshot.fees, is_fee=True)
    add(snapshot.incentives, is_fee=False)

    return PoolValuation(
        snapshot=snapshot,
        gross_usd=snapshot.gross_reward_usd,
        adjusted_usd=fee_adj + incentive_adj,
        fee_usd_adjusted=fee_adj,
        incentive_usd_adjusted=incentive_adj,
        contributions=contributions,
        contains_junk=contains_junk,
    )
