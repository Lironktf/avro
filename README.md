# avro — Aerodrome veAERO Vote Optimizer

V1: a recommendation CLI. No signing. No claiming. No scheduling.

Reads voting opportunities on Aerodrome (Base), prices fees + incentives in USD,
applies tiered reward-token haircuts, forecasts dilution conservatively, and
recommends how to allocate your veAERO across pools.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, and
[docs/prompt.txt](docs/prompt.txt) for the broader project spec.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# edit .env — put your Alchemy Base RPC into SUGAR_RPC_URI_8453
```

## Use

```bash
# Simulate as if you held 10 veAERO.
avro recommend --power 10

# Compare dilution models against the same live data.
avro recommend --power 10 --dilution-model bucket    # default: depth-bucketed
avro recommend --power 10 --dilution-model inverse   # continuous, scales 1/votes
avro recommend --power 10 --dilution-model flat --dilution-buffer 0.10

# Top-N split instead of winner-take-all.
avro recommend --power 10 --mode top-n --top-n 3

# Use your real veNFT (V1 still uses --power for sizing).
avro recommend --venft-id 1234
```

## Collecting snapshot data for the V4 model

The eventual trained dilution model needs intra-epoch vote history. Sugar
only stores end-of-epoch state, so we have to poll and store ourselves.

```bash
avro snapshot           # one capture; idempotent at the same minute
```

For setup on a long-running host (e.g. a DigitalOcean Droplet) including
recommended cron schedule, log rotation, and how long to let it run before
the data is useful, see [docs/RUNNING.md](docs/RUNNING.md).
# avro
