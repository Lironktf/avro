"""Local SQLite store for intra-epoch snapshots.

Captures the state Sugar gives us at each polling interval so we can later
train the V4 dilution model on (current_votes_at_T → final_votes_at_close)
and backtest strategies against realized end-of-epoch outcomes.

Schema is one row per (captured_at, epoch_ts, pool_address). Repeat runs at
the same wall-clock second overwrite by primary key (idempotent). Schema
migration is handled by `_migrate()` and runs every `open_db()` — new
columns are ALTER-ADDed if missing, preserving rows already collected on
older deployments.

The persisted shape is lossless: raw on-chain amounts + Sugar's price-at-
capture-time + the `listed` flag for every token. That means in V4 we can
recompute strong-paired liquidity, tier classification, and USD valuation
under any policy variation — even policies that didn't exist at capture
time — without re-fetching history.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List

from .sugar import PoolSnapshot, RewardAmount

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_epoch_snapshots (
    captured_at      INTEGER NOT NULL,   -- unix seconds when this row was written
    epoch_ts         INTEGER NOT NULL,   -- the Aerodrome epoch this snapshot belongs to
    pool_address     TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    is_cl            INTEGER NOT NULL,
    is_stable        INTEGER NOT NULL,
    votes_raw        TEXT    NOT NULL,   -- store as text; raw uint can exceed 2^63
    gross_reward_usd REAL    NOT NULL,
    fees_json        TEXT    NOT NULL,   -- list of {token, symbol, decimals, amount_raw, usd_per_token, listed}
    incentives_json  TEXT    NOT NULL,
    reserves_json    TEXT    NOT NULL DEFAULT '[]',
    emissions_raw    TEXT    NOT NULL DEFAULT '0',
    pool_fee         REAL    NOT NULL DEFAULT 0.0,
    PRIMARY KEY (captured_at, epoch_ts, pool_address)
);

CREATE INDEX IF NOT EXISTS idx_snap_pool_epoch
  ON pool_epoch_snapshots (pool_address, epoch_ts, captured_at);

CREATE INDEX IF NOT EXISTS idx_snap_epoch
  ON pool_epoch_snapshots (epoch_ts, captured_at);
"""

# Columns added after V1's initial schema. Detected and ALTER-ADDed if missing.
_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("reserves_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("emissions_raw", "TEXT NOT NULL DEFAULT '0'"),
    ("pool_fee", "REAL NOT NULL DEFAULT 0.0"),
]


def default_db_path() -> Path:
    return Path("data") / "snapshots.sqlite"


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring the schema up to current. Returns the list of columns added.

    Safe to call repeatedly. Older rows get the column DEFAULT — that's fine
    for V1 captures (pre-migration), they just won't have reserves/emissions
    populated. New captures will."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(pool_epoch_snapshots)")}
    if not existing:
        return []  # fresh DB, the CREATE TABLE above already had the new columns
    added: list[str] = []
    for name, ddl in _MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE pool_epoch_snapshots ADD COLUMN {name} {ddl}")
            added.append(name)
    return added


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _amounts_json(rs: Iterable[RewardAmount]) -> str:
    return json.dumps(
        [
            {
                "token": r.token_address,
                "symbol": r.symbol,
                "decimals": r.decimals,
                "amount_raw": str(r.amount_raw),
                "usd_per_token": r.usd_per_token,
                "listed": bool(r.listed),
            }
            for r in rs
        ],
        separators=(",", ":"),
    )


def write_snapshots(
    conn: sqlite3.Connection,
    snapshots: Iterable[PoolSnapshot],
    captured_at: int | None = None,
) -> int:
    captured_at = captured_at or int(time.time())
    rows = [
        (
            captured_at,
            s.epoch_ts,
            s.pool_address.lower(),
            s.symbol,
            int(s.is_cl),
            int(s.is_stable),
            str(s.votes_raw),
            s.gross_reward_usd,
            _amounts_json(s.fees),
            _amounts_json(s.incentives),
            _amounts_json(s.reserves),
            str(s.emissions_raw),
            s.pool_fee,
        )
        for s in snapshots
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO pool_epoch_snapshots
        (captured_at, epoch_ts, pool_address, symbol, is_cl, is_stable,
         votes_raw, gross_reward_usd, fees_json, incentives_json,
         reserves_json, emissions_raw, pool_fee)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM pool_epoch_snapshots").fetchone()[0]


def distinct_epoch_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT epoch_ts) FROM pool_epoch_snapshots"
    ).fetchone()[0]


def decode_amounts(json_str: str) -> List[RewardAmount]:
    """Reverse of `_amounts_json`. Tolerates old rows that may lack `listed`."""
    out: List[RewardAmount] = []
    for item in json.loads(json_str):
        out.append(RewardAmount(
            token_address=item["token"],
            symbol=item["symbol"],
            decimals=int(item["decimals"]),
            amount_raw=int(item["amount_raw"]),
            usd_per_token=float(item["usd_per_token"]),
            listed=bool(item.get("listed", False)),
        ))
    return out
