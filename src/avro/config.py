"""Runtime config: env loading, addresses, haircut tiers, token policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet

from dotenv import load_dotenv

load_dotenv()

# Aerodrome Voter on Base. Well-known address; overridable via env.
# Source: https://aerodrome.finance / Aerodrome deployment docs.
DEFAULT_VOTER_ADDR = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"

# Strong-tier allowlist: addresses we trust at maximum haircut regardless of TVL.
# All lowercased for comparison.
STRONG_TOKENS: FrozenSet[str] = frozenset(
    a.lower()
    for a in [
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC (Base, native)
        "0x4200000000000000000000000000000000000006",  # WETH (Base)
        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",  # cbBTC (Base)
        "0x940181a94A35A4569E4529A3CDfB74e38FD98631",  # AERO (Base)
    ]
)


@dataclass(frozen=True)
class HaircutTiers:
    """Per-tier haircut factors and liquidity thresholds (USD).

    Tier assignment, in order:
      1. token address in `strong_allowlist`            → strong
      2. token address in `junk_blocklist`              → junk
      3. token TVL across Aerodrome pools ≥ mid_floor   → mid
      4. token TVL across Aerodrome pools ≥ weak_floor  → weak
      5. otherwise                                       → junk
    """

    strong: float = 0.95
    mid: float = 0.70
    weak: float = 0.20
    junk: float = 0.00

    mid_floor_usd: float = 250_000.0
    weak_floor_usd: float = 25_000.0

    strong_allowlist: FrozenSet[str] = field(default_factory=lambda: STRONG_TOKENS)
    junk_blocklist: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Config:
    rpc_uri: str
    voter_addr: str
    venft_id: int | None
    simulated_power: float
    haircuts: HaircutTiers
    dilution_buffer: float  # MVP: forecastVotes = currentVotes * (1 + buffer)

    @classmethod
    def from_env(
        cls,
        venft_id: int | None = None,
        simulated_power: float | None = None,
        dilution_buffer: float = 0.10,
    ) -> "Config":
        rpc = os.environ.get("SUGAR_RPC_URI_8453")
        if not rpc:
            raise RuntimeError(
                "SUGAR_RPC_URI_8453 not set. Copy .env.example to .env and fill it in."
            )
        if venft_id is None:
            v = os.environ.get("VENFT_ID", "").strip()
            venft_id = int(v) if v else None
        if simulated_power is None:
            simulated_power = float(os.environ.get("SIMULATED_VEAERO_POWER", "10"))
        return cls(
            rpc_uri=rpc,
            voter_addr=os.environ.get("AERODROME_VOTER_ADDR", DEFAULT_VOTER_ADDR),
            venft_id=venft_id,
            simulated_power=simulated_power,
            haircuts=HaircutTiers(),
            dilution_buffer=dilution_buffer,
        )
