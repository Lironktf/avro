# avro V1 architecture

V1 is a read-only CLI. It does not sign transactions. The signing/automation path
(V2-V4 in `docs/prompt.txt`) is deferred until V1 numbers prove the strategy is
worth running on real veAERO.

## Module map

```
src/avro/
  config.py     loads .env, defines token haircut tiers and policy
  sugar.py      thin wrapper around sugar-sdk BaseChain — pools, epochs, prices
  pricing.py    USD valuation of reward bundles + tiered haircut application
  forecast.py   dilution models: flat / bucket / inverse  (V4 will add 'historical')
  ranking.py    per-pool score = adjustedRewardUSD / forecastVotes
  allocate.py   winner-take-all and top-N weighted split
  voter.py      direct on-chain reads from Aerodrome Voter contract
  snapshot.py   SQLite store for intra-epoch poll captures (feeds V4 model)
  cli.py        `avro recommend` + `avro snapshot` entry points
```

## Data flow

```
Sugar SDK ─┬─ get_pools()              → live pool list (incl. votes, gauge alive)
           ├─ get_latest_pool_epochs() → per-pool fees + incentives this epoch
           └─ get_prices(tokens)       → USD prices for reward tokens

         ↓

pricing.py: for each pool, sum (amount × price × haircut) across fee + incentive tokens
forecast.py: forecastVotes_i = currentVotes_i × (1 + dilutionBuffer)
ranking.py:  score_i = adjustedRewardUSD_i / forecastVotes_i
allocate.py: pick top pool(s), produce {pool: weight%}

         ↓

voter.py (verification only in V1):
  Voter.gauges[pool], Voter.isAlive[gauge], Voter.maxVotingNum, Voter.epochVoteEnd()
  Drop pools where on-chain state disagrees with Sugar.

         ↓

cli.py: render the §18 table + recommendation + expected weekly payout.
```

## Haircuts: tiered model

Each reward token is bucketed by Aerodrome-side liquidity (its deepest TVL across
pools where it appears) and a manual override list:

| tier   | haircut | how a token gets here                                  |
|--------|---------|--------------------------------------------------------|
| strong | 0.95    | manual allowlist: USDC, WETH, cbBTC, AERO              |
| mid    | 0.70    | total TVL ≥ $250k AND a Sugar price exists             |
| weak   | 0.20    | total TVL ≥ $25k AND a Sugar price exists              |
| junk   | 0.00    | below $25k, unpriceable, or manual blocklist           |

Thresholds and the strong allowlist live in `config.py` and are overridable
per-run via CLI flags. The tier function is deterministic so the same pool ranks
the same way across runs given identical inputs.

## Dilution forecast models

`forecast.py` ships three strategies, selectable via `--dilution-model`:

| model      | formula                                                | notes                                          |
|------------|--------------------------------------------------------|------------------------------------------------|
| `flat`     | `votes × (1 + --dilution-buffer)`                      | uniform multiplier; the V1 baseline            |
| `bucket`   | buffer ∈ {0.50, 0.25, 0.10, 0.05} by depth thresholds  | follows spec §10 numbers; **CLI default**      |
| `inverse`  | `buffer = clamp(50_000 / votes, 0.05, 1.0)`            | continuous, no cliffs                          |

A future `historical` model will replace these once `snapshot.py`'s SQLite
store has 3+ epochs of intra-week poll data.

## Snapshot store (preparing for the V4 model)

`avro snapshot` writes one row per pool per call into `data/snapshots.sqlite`.
Run it on cron (every 2-3h early in the week, every 15-30m in the last 3h
before `epochVoteEnd`). Schema is keyed by `(captured_at, epoch_ts, pool)` so
repeat runs are idempotent. The eventual trained model uses this to learn
`final_votes_at_close = f(current_votes_at_T, hours_to_close, pool_features)`.

For backfilling intra-epoch evolution from *before* we started snapshotting,
we'd need to read Voter `Voted` events from chain via `eth_getLogs`. That's
explicitly deferred — Sugar's `get_pool_epochs(pool)` only gives end-of-epoch
state, which is fine for backtesting strategy *outcomes* but not for training
dilution forecasts.

## What V1 explicitly does NOT do

- No `Voter.vote(...)` submission. No calldata generation.
- No reward claiming.
- No trained dilution model. The three shipped heuristics are all
  zero-data — they don't read the snapshot DB. The trained model is V4.
- No marginal-return optimizer. For small holders, winner-take-all or a fixed
  top-N split is sufficient; the marginal solver only matters for large veNFTs.

## Open questions tracked for V2+

- Approval semantics for `Voter.vote` from a delegated EOA (§13B of prompt.txt).
- Whether to use a secondary price source alongside Sugar's oracle.
- Snapshot cadence (cost vs. forecast accuracy).
