# avro — Project status snapshot

**Last touched:** 2026-05-14. Read this first when you come back.

## TL;DR

V1 is built and tested. The data collector is running on the Droplet. You
are *not* voting with real AERO yet — by design. Walk away for 3-4 weeks,
then come back to build the backtest + trained dilution model on the
collected data, and only then decide on capital.

## What's running right now (don't touch)

- **Droplet:** `root@nai-droplet`, install at `/root/opt/avro/`.
- **Snapshot cron** (`crontab -l` to confirm):
  - Every 3 hours, every day → baseline coverage.
  - Every 15 min on Wednesday 20:00-22:59 UTC → density near `epochVoteEnd`.
- **DB:** `/root/opt/avro/data/snapshots.sqlite`. Grows ~241 rows per fire.
- **Log:** `/var/log/avro.log`. One status line per fire. Rotate if you care; benign if you don't.

### Schema currently being captured (verified against live Sugar)

Per row (one row per (captured_at, epoch_ts, pool_address)):

| column            | type     | purpose                                                |
|-------------------|----------|--------------------------------------------------------|
| captured_at       | int      | poll timestamp — gives intra-epoch evolution           |
| epoch_ts          | int      | Aerodrome epoch this snapshot belongs to               |
| pool_address      | text     | LP address, lowercased                                 |
| symbol            | text     | e.g. `vAMM-WETH/OVER`                                  |
| is_cl, is_stable  | int      | pool-type features                                     |
| votes_raw         | text     | current veAERO weight (uint256, may exceed 2^63)       |
| gross_reward_usd  | real     | sum of fees+incentives USD at capture time             |
| fees_json         | text     | per-token: address, decimals, raw amount, price, listed |
| incentives_json   | text     | same shape as fees                                     |
| reserves_json     | text     | both pool sides — enables strong-paired-liquidity recompute |
| emissions_raw     | text     | AERO emissions for this pool this epoch (uint256)      |
| pool_fee          | real     | LP swap fee tier as Sugar reports                      |

The persisted data is lossless. In V4 we can recompute strong-paired
liquidity, reclassify tiers under any policy, reprice rewards with a
secondary oracle, and train the dilution model — all without re-fetching
history. Verified end-to-end with `tests/test_pipeline.py::test_snapshot_persists_every_field_we_need`.

Sanity check command:
```bash
ssh root@nai-droplet 'ls -lh /root/opt/avro/data/snapshots.sqlite; tail -n 5 /var/log/avro.log'
```

If the file is growing and the log shows timestamps from the last few hours, the collector is healthy. Stop reading and come back later.

## What's built (V1 — the recommender)

```
src/avro/
  config.py     env + tiered haircuts + token allowlist
  sugar.py      sugar-sdk wrapper → PoolSnapshot dataclasses (incl. `listed` flag)
  pricing.py    strong-paired-liquidity tiering; value_pool() with haircuts
  forecast.py   3 dilution models: flat / bucket / inverse (NO trained model yet)
  ranking.py    score = adjustedRewardUSD / forecastVotes; exact payout formula
  allocate.py   winner-take-all and top-N weighted split
  voter.py      Voter read-only: gauges, isAlive, epochVoteEnd, maxVotingNum, weights
  snapshot.py   SQLite store; `avro snapshot` writer
  cli.py        `avro recommend` + `avro snapshot` entry points
```

CLI:
- `avro recommend --power N [--dilution-model {bucket,inverse,flat}] [--mode {winner,top-n}] [--allow-junk] [--skip-onchain]`
- `avro snapshot`

Defaults reflect last decisions made:
- `--dilution-model bucket` (spec §10 thresholds; heuristic, unvalidated)
- Junk-tier reward pools are dropped (`--allow-junk` to keep them)
- Strong allowlist = USDC, WETH, cbBTC, AERO (Base addresses)

Tests: 21/21 pass. Run with `.venv/bin/pytest -q`.

## What's NOT built yet (in the order I'd build them)

1. **Backtest harness.** Replay past epochs from Sugar's `get_pool_epochs()` and score each strategy (winner / top-N / dilution models) against realized end-of-epoch state. **Cannot start until we have multi-week data.** This is your real "is this strategy good?" test.

2. **Trained dilution model.** Replace the bucket/inverse heuristics with a model trained on `pool_epoch_snapshots`: predict `final_votes_at_close` from `(current_votes_at_T, hours_to_close, pool_features)`. Needs ≥3 weeks of data.

3. **V2 — transaction builder.** Generate calldata for `Voter.vote(tokenId, pools, weights)`, simulate on a Base fork, print "paste this into your wallet." No signing. Eliminates copy-paste-from-CLI-into-Aerodrome-UI error.

4. **V3 — autonomous signer.** Holds a hot-wallet private key. Submits the vote on schedule. Requires deciding the wallet-security model from prompt.txt §13 (hot wallet vs approved executor vs recommendation-only). **Don't build this without explicit deliberation about key handling.**

5. **Reward claimer.** `Voter.claimBribes(...)` / `claimFees(...)` once accrued > gas × safety multiple.

6. **Better haircuts.** Replace strong-paired-liquidity with an actual depth-of-book swap simulation: "swap $100 of token X into USDC, what fraction comes out?" That's the real haircut. Probably uses `chain.get_quote()` from sugar-sdk.

## Open questions / decisions deferred

- **AERO price + lock curve.** Before locking any AERO, do the unit math: at current AERO price, how much capital does N veAERO cost to lock for K years? Compare to expected weekly reward from `avro recommend --power N`. The algorithm only matters if the APR clears your hurdle rate.
- **Mid-tier haircut calibration.** 70% is a guess. Validate against backtest data once available. May need to be 30-50% for thinly-traded tokens.
- **Dilution model verdict.** Three heuristics ship; none validated. Pick a winner empirically once you have backtest data.
- **Wallet security model.** If/when V3 is built — read prompt.txt §13 and decide between hot wallet, approved executor, or recommendation-only.

## Concrete next steps when you return

1. **Check the collector is alive.**
   ```bash
   ssh root@nai-droplet 'sqlite3 /root/opt/avro/data/snapshots.sqlite "SELECT COUNT(*), COUNT(DISTINCT epoch_ts), MIN(captured_at), MAX(captured_at) FROM pool_epoch_snapshots"'
   ```
   You want: row count in the tens of thousands, ≥3 distinct epochs, MAX captured_at within the last few hours.

2. **Pull the DB local.**
   ```bash
   rsync -avz root@nai-droplet:/root/opt/avro/data/snapshots.sqlite ./data/
   ```

3. **Build the backtest harness.** Module `src/avro/backtest.py`:
   - Load past N epochs via Sugar `get_pool_epochs()` per pool.
   - For each past epoch, take state-at-start-of-epoch, run avro's ranking, record predicted picks + payouts.
   - Compare to actual end-of-epoch outcomes.
   - Report: mean absolute error of predicted payout, % of weeks avro picked a top-decile pool, vAPR realized vs predicted.

4. **Train v0 of the dilution model** from `pool_epoch_snapshots`. Start simple: a ratio-by-bucket regression — "for pools with X current votes and Y hours remaining, what was the mean inflow multiplier observed?" Compare against bucket/inverse heuristics in the backtest.

5. **Decide on capital.** Only after step 3 says the algorithm has a real edge.

## Things that could break while you're away (low priority)

- **Alchemy key revoked or rate-limited.** Symptom: `/var/log/avro.log` shows 403/429. Fix: get a new key, update `.env` on the Droplet, restart cron (cron picks up new env automatically on next fire).
- **`SUGAR_RPC_URI_8453` deprecated.** Sugar SDK version pinned to 0.3.1 in `pyproject.toml`, so this shouldn't drift.
- **Aerodrome Voter contract upgrade.** Address changes invalidate `voter.py`. Override via `AERODROME_VOTER_ADDR` env var.
- **Droplet rebooted.** Cron survives reboots. Nothing to do.

## File map

```
/Users/lironkatsif/Desktop/SWE/Personal/avro/
  README.md                       — quickstart
  docs/
    STATUS.md                     — this file
    prompt.txt                    — the original full project spec (V1-V4)
    ARCHITECTURE.md               — module map + data flow
    RUNNING.md                    — Droplet setup + cron + log rotation
  src/avro/                       — all source
  tests/test_pipeline.py          — math + dilution + snapshot tests (21 cases)
  .env / .env.example             — local Alchemy key (not committed)
  data/snapshots.sqlite           — local copy (Droplet is source of truth)
```
