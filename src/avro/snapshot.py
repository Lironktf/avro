"""Local SQLite store for intra-epoch snapshots.

Captures the state Sugar gives us at each polling interval so we can later
train the C-tier dilution model on (current_votes_at_T → final_votes_at_close).

Schema is one row per (epoch_ts, pool_address, captured_at). A weekly poll
schedule (Thursday → Wednesday, every 2-3h, every 15m in the last 3h) is
recommended in spec §15.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .sugar import PoolSnapshot

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
    fees_json        TEXT    NOT NULL,   -- list of {token, symbol, amount_raw, usd}
    incentives_json  TEXT    NOT NULL,
    PRIMARY KEY (captured_at, epoch_ts, pool_address)
);

CREATE INDEX IF NOT EXISTS idx_snap_pool_epoch
  ON pool_epoch_snapshots (pool_address, epoch_ts, captured_at);

CREATE INDEX IF NOT EXISTS idx_snap_epoch
  ON pool_epoch_snapshots (epoch_ts, captured_at);
"""


def default_db_path() -> Path:
    return Path("data") / "snapshots.sqlite"


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _rewards_json(rs) -> str:
    return json.dumps(
        [
            {
                "token": r.token_address,
                "symbol": r.symbol,
                "decimals": r.decimals,
                "amount_raw": str(r.amount_raw),
                "usd_per_token": r.usd_per_token,
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
            _rewards_json(s.fees),
            _rewards_json(s.incentives),
        )
        for s in snapshots
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO pool_epoch_snapshots
        (captured_at, epoch_ts, pool_address, symbol, is_cl, is_stable,
         votes_raw, gross_reward_usd, fees_json, incentives_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
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
