# Running `avro snapshot` on a Droplet

## What it does and why

`avro snapshot` polls Sugar once and appends one row per pool to
`data/snapshots.sqlite`. Each row is keyed by `(captured_at, epoch_ts, pool)`
so re-running at the same minute is a no-op rewrite. The DB grows by ~241
rows per run (one per current voteable pool).

Why bother: the V4 dilution model needs to learn how votes *grow* through an
epoch. Sugar only stores end-of-epoch state, so the only way to get
intra-epoch evolution is to collect it ourselves. (Or read `Voted` events
from chain via `eth_getLogs`, which is more work — deferred.)

## How long it needs to run

| weeks of data    | what it unlocks                                                   |
|------------------|-------------------------------------------------------------------|
| 0–2 weeks        | Nothing useful for modeling. The heuristics (`bucket`/`inverse`) are what you run on. |
| 3–4 weeks        | First passable trained model. Per-pool dilution priors are usable. Backtest plausible. |
| 8–12 weeks       | Reasonable confidence in the trained model. Captures seasonality across multiple incentive cycles. |
| 6+ months        | Real edge. Enough data to model token-specific behavior and detect regime shifts. |

**Set it up once, let it run indefinitely.** It costs ~1MB/week of disk and a
few RPC calls per run.

## Droplet setup

Assuming Ubuntu 22.04+ on a small ($5/mo) Droplet:

```bash
# one-time
ssh root@your-droplet
apt update && apt install -y python3-venv git

# clone your repo (or scp the source)
git clone <your-fork-or-bundle> /opt/avro
cd /opt/avro
python3 -m venv .venv
.venv/bin/pip install -e .

# put your Alchemy key in /opt/avro/.env (same SUGAR_RPC_URI_8453 as local)
cp .env.example .env
vim .env   # paste your full Base mainnet key

# smoke test
.venv/bin/avro snapshot
# → should print: "Wrote 241 pool rows. DB now holds 241 rows across 1 distinct epochs."
```

## Cron schedule

Spec §15 recommends denser polling near the cutoff. This crontab does that;
edit with `crontab -e`:

```cron
# Avro snapshot — paths assume /opt/avro install above.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
AVRO=/opt/avro/.venv/bin/avro
CD=cd /opt/avro &&

# Every 3 hours, every day. Cheap baseline coverage.
0 */3 * * *               $CD $AVRO snapshot >> /var/log/avro.log 2>&1

# Wednesday final ~3 hours before the typical epochVoteEnd (Wed 23:00 UTC).
# Tightens cadence to every 15 minutes in that window.
*/15 20-22 * * 3          $CD $AVRO snapshot >> /var/log/avro.log 2>&1
```

Aerodrome's epoch ends Wednesday at 23:00 UTC weekly (confirm via the
`epochVoteEnd:` line in `avro recommend` output — that's the authoritative
on-chain value, not a constant in this repo).

## Operational hygiene

- **Log size:** the snapshot prints one line per run. Rotate `/var/log/avro.log`
  with `logrotate` or just `> /var/log/avro.log` periodically.
- **DB backups:** SQLite is one file. `scp` it home weekly, or just rsync.
- **Failure mode:** if Alchemy 5xxs, the run exits non-zero and cron moves on.
  Missing one or two snapshots a week is fine. Don't add retries.
- **Watch RPC usage:** every snapshot pulls all 241 pools + price oracle. On
  Alchemy free tier that's ~5k CUs per run; ~80k CUs/day with the schedule
  above. Well under the 25M/day free tier cap.

## Pulling the DB back for analysis

```bash
# from your laptop
rsync -avz root@your-droplet:/opt/avro/data/snapshots.sqlite ./data/
# then run sqlite3 ./data/snapshots.sqlite locally to query
```

You won't have a backtest or trained model yet — that's V4 work — but at any
point you can inspect with:

```sql
SELECT epoch_ts, pool_address, symbol, votes_raw, gross_reward_usd, captured_at
FROM pool_epoch_snapshots
WHERE pool_address = '0x...'
ORDER BY captured_at;
```

…to see how votes and rewards evolved over the epoch.
