"""Unit tests for the math, using synthetic PoolSnapshot inputs."""

from __future__ import annotations

import math

from avro.allocate import top_n_weighted, winner_take_all
from avro.config import HaircutTiers
from avro.forecast import forecast_votes
from avro.pricing import build_token_tiers, classify_token, value_pool
from avro.ranking import rank_pools
from avro.sugar import PoolSnapshot, RewardAmount

USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
WETH = "0x4200000000000000000000000000000000000006"
RANDO = "0x0000000000000000000000000000000000000abc"  # unknown token


def make_reward(
    addr: str, sym: str, amount_human: float, usd: float, listed: bool = False
) -> RewardAmount:
    return RewardAmount(
        token_address=addr,
        symbol=sym,
        decimals=6,
        amount_raw=int(amount_human * 10**6),
        usd_per_token=usd,
        listed=listed,
    )


def make_snapshot(
    address: str,
    symbol: str,
    votes_veaero: float,
    fees: list[RewardAmount],
    incentives: list[RewardAmount],
    reserves_usd: dict | None = None,
) -> PoolSnapshot:
    return PoolSnapshot(
        pool_address=address,
        symbol=symbol,
        is_cl=False,
        is_stable=False,
        epoch_ts=0,
        votes_raw=int(votes_veaero * 1e18),
        fees=fees,
        incentives=incentives,
        reserves_usd_by_token=reserves_usd or {},
    )


# ---------- classify_token ----------

def test_strong_allowlist_overrides_everything():
    """An allowlisted token is strong even if unlisted, unpaired, or with no price."""
    tiers = HaircutTiers()
    t = classify_token(USDC, "USDC", strong_paired_usd=0.0, tiers=tiers,
                       has_price=True, listed=False)
    assert t.tier == "strong"
    assert t.haircut == tiers.strong


def test_strong_paired_buckets_for_unknown_listed_token():
    tiers = HaircutTiers()
    mid = classify_token(RANDO, "X", strong_paired_usd=300_000, tiers=tiers,
                         has_price=True, listed=True)
    weak = classify_token(RANDO, "X", strong_paired_usd=50_000, tiers=tiers,
                          has_price=True, listed=True)
    junk = classify_token(RANDO, "X", strong_paired_usd=1_000, tiers=tiers,
                          has_price=True, listed=True)
    assert (mid.tier, weak.tier, junk.tier) == ("mid", "weak", "junk")


def test_unlisted_unknown_token_is_junk_regardless_of_pairing():
    """The STAR case: even with $1M strong-paired liquidity, if Sugar hasn't listed
    the token we treat it as junk by default."""
    tiers = HaircutTiers()
    t = classify_token(RANDO, "STAR", strong_paired_usd=1_000_000, tiers=tiers,
                       has_price=True, listed=False)
    assert t.tier == "junk"


def test_unpriceable_is_junk_even_when_deep():
    tiers = HaircutTiers()
    t = classify_token(RANDO, "X", strong_paired_usd=10_000_000, tiers=tiers,
                       has_price=False, listed=True)
    assert t.tier == "junk"


# ---------- strong_paired_liquidity ----------

def test_strong_paired_credits_only_strong_side():
    """USDC/RANDO pool with $50k USDC + $50k RANDO ⇒ RANDO has $50k strong-paired,
    USDC isn't credited because it's already strong-allowlisted."""
    from avro.pricing import strong_paired_liquidity
    tiers = HaircutTiers()
    pool = make_snapshot(
        "0xP", "USDC/RANDO", votes_veaero=1,
        fees=[], incentives=[],
        reserves_usd={USDC: 50_000, RANDO: 50_000},
    )
    out = strong_paired_liquidity([pool], tiers.strong_allowlist)
    assert out == {RANDO.lower(): 50_000}


def test_strong_paired_ignores_pure_junk_pools():
    """A pool with two non-strong tokens credits neither — no exit liquidity here."""
    from avro.pricing import strong_paired_liquidity
    tiers = HaircutTiers()
    JUNK2 = "0x000000000000000000000000000000000000beef"
    pool = make_snapshot(
        "0xP", "RANDO/JUNK2", votes_veaero=1,
        fees=[], incentives=[],
        reserves_usd={RANDO: 50_000, JUNK2: 50_000},
    )
    out = strong_paired_liquidity([pool], tiers.strong_allowlist)
    assert out == {}


def test_strong_paired_accumulates_across_pools():
    """The same token paired against USDC and against WETH sums both sides."""
    from avro.pricing import strong_paired_liquidity
    tiers = HaircutTiers()
    p1 = make_snapshot("0xA", "USDC/X", 1, [], [], {USDC: 30_000, RANDO: 30_000})
    p2 = make_snapshot("0xB", "WETH/X", 1, [], [], {WETH: 70_000, RANDO: 70_000})
    out = strong_paired_liquidity([p1, p2], tiers.strong_allowlist)
    assert out == {RANDO.lower(): 100_000}


# ---------- value_pool with mixed tiers ----------

def test_value_pool_applies_per_token_haircuts():
    tiers = HaircutTiers()
    s = make_snapshot(
        "0xpoolA",
        "USDC/RANDO",
        votes_veaero=1_000_000,
        fees=[make_reward(USDC, "USDC", amount_human=100, usd=1.0)],          # $100 strong
        incentives=[make_reward(RANDO, "X", amount_human=100, usd=1.0)],      # $100 unknown
        reserves_usd={USDC: 500_000, RANDO: 1_000},  # RANDO is junk by TVL
    )
    token_tiers = build_token_tiers([s], tiers)
    val = value_pool(s, token_tiers)
    # strong=0.95, junk=0.0 → adjusted = 100*0.95 + 100*0.0 = 95
    assert math.isclose(val.gross_usd, 200.0, rel_tol=1e-9)
    assert math.isclose(val.adjusted_usd, 95.0, rel_tol=1e-9)


def test_value_pool_mid_tier_haircut():
    """RANDO listed AND paired against $300k of USDC ⇒ mid tier, 70% haircut."""
    tiers = HaircutTiers()
    s = make_snapshot(
        "0xpoolB",
        "USDC/MID",
        votes_veaero=1.0,
        fees=[make_reward(RANDO, "MID", amount_human=1, usd=1.0, listed=True)],
        incentives=[],
        reserves_usd={USDC: 300_000, RANDO: 100_000},
    )
    val = value_pool(s, build_token_tiers([s], tiers))
    assert math.isclose(val.adjusted_usd, 0.70, rel_tol=1e-9)
    assert not val.contains_junk


# ---------- ranking + payout ----------

def test_ranking_orders_by_score_descending():
    tiers = HaircutTiers()
    # Pool A: $100 rewards, 10k votes → score = 100/(10k * 1.1) ≈ 0.00909
    # Pool B: $100 rewards, 1k votes  → score = 100/(1k * 1.1)  ≈ 0.0909
    a = make_snapshot(
        "0xA", "A", votes_veaero=10_000,
        fees=[make_reward(USDC, "USDC", 100, 1.0)],
        incentives=[],
        reserves_usd={USDC: 1_000_000},
    )
    b = make_snapshot(
        "0xB", "B", votes_veaero=1_000,
        fees=[make_reward(USDC, "USDC", 100, 1.0)],
        incentives=[],
        reserves_usd={USDC: 1_000_000},
    )
    token_tiers = build_token_tiers([a, b], tiers)
    valuations = [value_pool(a, token_tiers), value_pool(b, token_tiers)]
    forecasts = {
        v.snapshot.pool_address: forecast_votes(v.snapshot.pool_address, v.snapshot.votes_veaero, 0.10)
        for v in valuations
    }
    ranked = rank_pools(valuations, forecasts)
    assert [r.valuation.snapshot.symbol for r in ranked] == ["B", "A"]
    # B's score is ~10x A's
    assert ranked[0].score > 9 * ranked[1].score


def test_expected_payout_uses_exact_dilution_formula():
    """For your_power on the same scale as forecast votes, marginal dilution matters.

    Use 100_000 votes so we're well above the min_floor_veaero used by forecast.
    """
    tiers = HaircutTiers()
    s = make_snapshot(
        "0xA", "A", votes_veaero=100_000,
        fees=[make_reward(USDC, "USDC", 100, 1.0)],   # $95 adjusted
        incentives=[],
        reserves_usd={USDC: 1_000_000},
    )
    val = value_pool(s, build_token_tiers([s], tiers))
    fc = forecast_votes("0xA", 100_000, 0.10)  # → 110_000
    ranked = rank_pools([val], {"0xA": fc})[0]
    # add 100_000 veAERO: share = 100_000 / 210_000, payout = 95 * 100_000 / 210_000
    payout = ranked.expected_payout_usd(100_000)
    assert math.isclose(payout, 95.0 * 100_000 / 210_000, rel_tol=1e-9)


def test_zero_vote_pool_uses_min_floor():
    fc = forecast_votes("0x", current_votes_veaero=0, dilution_buffer=0.10, min_floor_veaero=1000)
    # base = max(0, 1000) = 1000; forecast = 1100
    assert math.isclose(fc.forecast_votes_veaero, 1100.0)
    assert fc.model == "flat"
    assert fc.buffer_used == 0.10


# ---------- dilution models ----------

def test_bucket_model_penalizes_sparse_pools():
    sparse = forecast_votes("0x", 5_000, 0.10, model="bucket", min_floor_veaero=1.0)
    deep = forecast_votes("0x", 5_000_000, 0.10, model="bucket", min_floor_veaero=1.0)
    assert sparse.buffer_used == 0.50
    assert deep.buffer_used == 0.05
    # Sparse pools have a much larger relative inflation.
    assert (sparse.forecast_votes_veaero / sparse.current_votes_veaero) > \
           (deep.forecast_votes_veaero / deep.current_votes_veaero)


def test_bucket_thresholds_at_boundaries():
    # Walk the §10 thresholds.
    assert forecast_votes("0x", 9_999, 0.10, model="bucket").buffer_used == 0.50
    assert forecast_votes("0x", 10_000, 0.10, model="bucket").buffer_used == 0.25
    assert forecast_votes("0x", 99_999, 0.10, model="bucket").buffer_used == 0.25
    assert forecast_votes("0x", 100_000, 0.10, model="bucket").buffer_used == 0.10
    assert forecast_votes("0x", 999_999, 0.10, model="bucket").buffer_used == 0.10
    assert forecast_votes("0x", 1_000_000, 0.10, model="bucket").buffer_used == 0.05


def test_inverse_model_is_monotonic_in_votes():
    """As votes grow, the inverse buffer should monotonically decrease."""
    sizes = [1_000, 10_000, 100_000, 1_000_000, 10_000_000]
    buffers = [forecast_votes("0x", v, 0.10, model="inverse").buffer_used for v in sizes]
    # weakly decreasing, since the floor and ceiling can clamp at extremes
    for a, b in zip(buffers, buffers[1:]):
        assert a >= b


def test_inverse_model_clamps():
    """Tiny pool clamps to ceiling=1.0; huge pool clamps to floor=0.05."""
    tiny = forecast_votes("0x", 100, 0.10, model="inverse").buffer_used
    huge = forecast_votes("0x", 100_000_000, 0.10, model="inverse").buffer_used
    assert tiny == 1.00
    assert huge == 0.05


def test_unknown_dilution_model_raises():
    import pytest
    with pytest.raises(ValueError):
        forecast_votes("0x", 1000, 0.10, model="bogus")  # type: ignore[arg-type]


# ---------- snapshot store ----------

def test_snapshot_store_roundtrip(tmp_path):
    from avro.snapshot import open_db, write_snapshots, row_count, distinct_epoch_count

    s = make_snapshot(
        "0xABC", "USDC/X", 10_000,
        fees=[make_reward(USDC, "USDC", 5, 1.0)],
        incentives=[make_reward(RANDO, "X", 100, 0.01)],
        reserves_usd={USDC: 1_000_000},
    )
    db = tmp_path / "snap.sqlite"
    with open_db(db) as conn:
        n = write_snapshots(conn, [s], captured_at=1_700_000_000)
        assert n == 1
        assert row_count(conn) == 1
        assert distinct_epoch_count(conn) == 1
        # Second write at a *different* captured_at should append, not replace.
        write_snapshots(conn, [s], captured_at=1_700_003_600)
        assert row_count(conn) == 2
        # Same captured_at + pool should replace (primary key collision).
        write_snapshots(conn, [s], captured_at=1_700_003_600)
        assert row_count(conn) == 2


# ---------- allocation ----------

def test_winner_take_all():
    tiers = HaircutTiers()
    a = make_snapshot("0xA", "A", 1_000,
        fees=[make_reward(USDC, "USDC", 100, 1.0)], incentives=[],
        reserves_usd={USDC: 1_000_000})
    b = make_snapshot("0xB", "B", 10_000,
        fees=[make_reward(USDC, "USDC", 100, 1.0)], incentives=[],
        reserves_usd={USDC: 1_000_000})
    tt = build_token_tiers([a, b], tiers)
    ranked = rank_pools(
        [value_pool(a, tt), value_pool(b, tt)],
        {p.pool_address: forecast_votes(p.pool_address, p.votes_veaero, 0.10) for p in [a, b]},
    )
    allocs = winner_take_all(ranked)
    assert len(allocs) == 1
    assert allocs[0].weight_pct == 100.0
    assert allocs[0].pool.valuation.snapshot.symbol == "A"  # smaller votes ⇒ higher score


def test_top_n_weighted_sums_to_100():
    tiers = HaircutTiers()
    pools = [
        make_snapshot(f"0x{i}", f"P{i}", 1_000 * (i + 1),
            fees=[make_reward(USDC, "USDC", 100, 1.0)], incentives=[],
            reserves_usd={USDC: 1_000_000})
        for i in range(5)
    ]
    tt = build_token_tiers(pools, tiers)
    ranked = rank_pools(
        [value_pool(p, tt) for p in pools],
        {p.pool_address: forecast_votes(p.pool_address, p.votes_veaero, 0.10) for p in pools},
    )
    allocs = top_n_weighted(ranked, n=3, max_pools=10)
    assert len(allocs) == 3
    assert math.isclose(sum(a.weight_pct for a in allocs), 100.0, rel_tol=1e-9)


def test_top_n_drops_below_min_weight():
    """If one pool's natural share is below min_weight_pct, drop it and renormalize."""
    tiers = HaircutTiers()
    # Two pools where one is ~99x better; the weaker gets <5% and should be dropped.
    a = make_snapshot("0xA", "A", 1,
        fees=[make_reward(USDC, "USDC", 100, 1.0)], incentives=[],
        reserves_usd={USDC: 1_000_000})
    b = make_snapshot("0xB", "B", 99,
        fees=[make_reward(USDC, "USDC", 1, 1.0)], incentives=[],
        reserves_usd={USDC: 1_000_000})
    tt = build_token_tiers([a, b], tiers)
    ranked = rank_pools(
        [value_pool(a, tt), value_pool(b, tt)],
        {p.pool_address: forecast_votes(p.pool_address, p.votes_veaero, 0.10) for p in [a, b]},
    )
    allocs = top_n_weighted(ranked, n=2, max_pools=10, min_weight_pct=5.0)
    assert len(allocs) == 1
    assert allocs[0].pool.valuation.snapshot.symbol == "A"
    assert allocs[0].weight_pct == 100.0
