"""`avro recommend` — print the §18-style table + a suggested allocation."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import click
from rich.console import Console
from rich.table import Table

from .allocate import Allocation, top_n_weighted, winner_take_all
from .config import Config
from .forecast import forecast_votes
from .pricing import build_token_tiers, value_pool
from .ranking import RankedPool, rank_pools
from .snapshot import (
    default_db_path,
    distinct_epoch_count,
    open_db,
    row_count,
    write_snapshots,
)
from .sugar import base_chain, fetch_snapshots
from .voter import VoterClient

console = Console()


@click.group()
def cli() -> None:
    """Aerodrome veAERO vote optimizer (V1: recommender only)."""


@cli.command()
@click.option("--venft-id", type=int, default=None, help="Your veAERO NFT id.")
@click.option(
    "--power",
    type=float,
    default=None,
    help="Simulate this much veAERO voting power instead of reading a veNFT.",
)
@click.option(
    "--mode",
    type=click.Choice(["winner", "top-n"]),
    default="winner",
    help="Allocation strategy.",
)
@click.option("--top-n", "top_n", type=int, default=3, help="Pool count for top-n mode.")
@click.option(
    "--dilution-model",
    type=click.Choice(["flat", "bucket", "inverse"]),
    default="bucket",
    help="How forecast votes are inflated over current votes. "
    "bucket: depth-bucketed buffer (default). "
    "inverse: continuous, scales with 1/votes. "
    "flat: uniform --dilution-buffer multiplier.",
)
@click.option(
    "--dilution-buffer",
    type=float,
    default=0.10,
    help="Buffer used by the 'flat' model only (e.g. 0.10 = +10%).",
)
@click.option("--limit", type=int, default=15, help="How many ranked pools to show.")
@click.option(
    "--allow-junk",
    is_flag=True,
    help="Keep pools whose reward bundle includes a junk-tier token. "
    "Default: drop them. Junk = unlisted, unpriceable, or below weak-tier liquidity.",
)
@click.option(
    "--skip-onchain",
    is_flag=True,
    help="Skip Voter contract verification (faster, but no gauge-alive check).",
)
def recommend(
    venft_id: int | None,
    power: float | None,
    mode: str,
    top_n: int,
    dilution_model: str,
    dilution_buffer: float,
    limit: int,
    allow_junk: bool,
    skip_onchain: bool,
) -> None:
    """Rank pools and recommend a vote allocation."""
    cfg = Config.from_env(
        venft_id=venft_id,
        simulated_power=power,
        dilution_buffer=dilution_buffer,
    )
    your_power = cfg.simulated_power if cfg.venft_id is None else cfg.simulated_power
    # NOTE: when VENFT_ID is set, V1 still uses simulated_power. Reading actual
    # voting power off-chain from the veAERO contract is a V2 task.

    console.log(f"Loading Sugar snapshots from {cfg.rpc_uri[:40]}…")
    t0 = time.time()
    with base_chain(cfg.rpc_uri) as chain:
        snapshots = fetch_snapshots(chain)
    console.log(f"Got {len(snapshots)} pool epochs in {time.time() - t0:.1f}s")

    token_tiers = build_token_tiers(snapshots, cfg.haircuts)
    valuations = [value_pool(s, token_tiers) for s in snapshots]

    if not allow_junk:
        before = len(valuations)
        valuations = [v for v in valuations if not v.contains_junk]
        dropped = before - len(valuations)
        if dropped:
            console.log(f"Dropped {dropped} pools containing junk-tier reward tokens "
                        f"(pass --allow-junk to keep).")
    forecasts = {
        v.snapshot.pool_address: forecast_votes(
            pool_address=v.snapshot.pool_address,
            current_votes_veaero=v.snapshot.votes_veaero,
            dilution_buffer=cfg.dilution_buffer,
            model=dilution_model,
        )
        for v in valuations
    }
    console.log(
        f"Forecast model: [bold]{dilution_model}[/]"
        + (f" (buffer={cfg.dilution_buffer:.0%})" if dilution_model == "flat" else "")
    )
    ranked = rank_pools(valuations, forecasts)

    voter_state = None
    if not skip_onchain and ranked:
        console.log("Verifying top pools on-chain…")
        client = VoterClient(cfg.rpc_uri, cfg.voter_addr)
        voter_state = client.state()
        head = ranked[: max(limit, top_n * 2)]
        statuses = client.gauge_status([r.pool_address for r in head])
        alive_addrs = {p for p, st in statuses.items() if st.alive and st.has_gauge}
        before = len(ranked)
        ranked = [r for r in ranked if r.pool_address.lower() in alive_addrs or
                  r.pool_address.lower() not in statuses]
        dropped = before - len(ranked)
        if dropped:
            console.log(f"[yellow]Dropped {dropped} pools after on-chain alive check.[/]")

    if not ranked:
        console.print("[red]No rankable pools found.[/]")
        return

    _render_table(ranked[:limit], your_power, token_tiers)

    if mode == "winner":
        allocs = winner_take_all(ranked)
    else:
        max_pools = voter_state.max_voting_num if voter_state else top_n
        allocs = top_n_weighted(ranked, n=top_n, max_pools=max_pools)

    _render_recommendation(allocs, your_power, voter_state)


def _render_table(rows: List[RankedPool], your_power: float, token_tiers) -> None:
    # Force a wide console so numeric columns aren't ellipsis-truncated on
    # narrower terminals. Rich will overflow horizontally if the actual
    # terminal is smaller, which is the right tradeoff for tabular data.
    wide = Console(width=160)
    t = Table(
        title="Aerodrome voting opportunities (ranked)",
        show_lines=False,
        pad_edge=False,
    )
    t.add_column("#", justify="right", style="dim", no_wrap=True)
    t.add_column("Pool", no_wrap=True, max_width=28)
    t.add_column("Type", no_wrap=True)
    t.add_column("Gross $", justify="right", no_wrap=True)
    t.add_column("Adj $", justify="right", no_wrap=True)
    t.add_column("Votes (veAERO)", justify="right", no_wrap=True)
    t.add_column("Buf", justify="right", no_wrap=True)
    t.add_column("Score ($/veAERO)", justify="right", no_wrap=True)
    t.add_column(f"Weekly @{your_power:g} ($)", justify="right", no_wrap=True)
    t.add_column("Top reward tokens", no_wrap=True, max_width=40)
    for i, r in enumerate(rows, 1):
        snap = r.valuation.snapshot
        type_ = ("CL" if snap.is_cl else "v2") + ("·s" if snap.is_stable else "")
        top_tokens = " ".join(
            f"{sym}[{tier[0]}]" for _, (sym, _, _, tier) in
            sorted(r.valuation.contributions.items(), key=lambda kv: -kv[1][2])[:3]
        )
        t.add_row(
            str(i),
            snap.symbol,
            type_,
            f"{r.valuation.gross_usd:,.0f}",
            f"{r.valuation.adjusted_usd:,.0f}",
            f"{snap.votes_veaero:,.0f}",
            f"{r.forecast.buffer_used:.0%}",
            f"{r.score:.6f}",
            f"{r.expected_payout_usd(your_power):.4f}",
            top_tokens,
        )
    wide.print(t)


def _render_recommendation(allocs: List[Allocation], your_power: float, voter_state) -> None:
    if not allocs:
        console.print("[red]No allocation produced.[/]")
        return
    console.print("\n[bold]Recommendation[/]")
    total_payout = 0.0
    for a in allocs:
        share = your_power * a.weight_pct / 100.0
        payout = a.pool.expected_payout_usd(share)
        total_payout += payout
        console.print(
            f"  {a.weight_pct:5.1f}%  {a.pool.valuation.snapshot.symbol}  "
            f"(score {a.pool.score:.6f}, est ${payout:.4f})"
        )
    console.print(f"\n[bold]Expected weekly reward:[/] ${total_payout:.4f} "
                  f"(at {your_power:g} veAERO, adjusted for token haircuts)")

    if voter_state:
        end = datetime.fromtimestamp(voter_state.epoch_vote_end_ts, tz=timezone.utc)
        remaining = voter_state.epoch_vote_end_ts - int(time.time())
        hrs = remaining / 3600
        console.print(
            f"\n[dim]epochVoteEnd: {end.isoformat()}  "
            f"(in {hrs:+.1f}h)   maxVotingNum: {voter_state.max_voting_num}[/]"
        )


@cli.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="SQLite path (default: data/snapshots.sqlite).",
)
def snapshot(db_path: Path | None) -> None:
    """Capture the current Aerodrome epoch state into SQLite.

    Run this on a cron schedule (e.g. every 2-3 hours Thu→Tue, every 15-30
    minutes in the final 3 hours before epochVoteEnd). Each run appends one
    row per pool at the current `captured_at` timestamp. The eventual V4
    dilution model will train on this table.
    """
    cfg = Config.from_env()
    db_path = db_path or default_db_path()
    console.log(f"Snapshotting → {db_path}")
    with base_chain(cfg.rpc_uri) as chain:
        snaps = fetch_snapshots(chain)
    with open_db(db_path) as conn:
        n = write_snapshots(conn, snaps)
        total = row_count(conn)
        epochs = distinct_epoch_count(conn)
    console.print(
        f"[green]Wrote {n} pool rows.[/] "
        f"DB now holds {total:,} rows across {epochs} distinct epochs."
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
