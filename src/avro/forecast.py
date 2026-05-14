"""Dilution forecast models.

We model the denominator of the payout formula:
    forecastVotes = currentVotes + expectedRemainingInflow

V1 ships three heuristics. None of them use historical data yet — that's V4,
once `avro snapshot` has collected enough samples.

  flat:    forecastVotes = max(currentVotes, FLOOR) * (1 + buffer)
           Uniform multiplier. Cheap, ignores pool sparseness.

  bucket:  buffer is chosen by depth bucket (per spec §10):
             <10k   veAERO → 0.50  (very sparse — late snipers can dominate)
             <100k         → 0.25
             <1M           → 0.10
             ≥1M           → 0.05
           Transparent and tunable by inspection.

  inverse: buffer = clamp(SCALE / currentVotes, MIN, MAX)
           Continuous version of bucket. No cliffs at thresholds. Same spirit.

All three honor a floor (min_floor_veaero) to avoid divide-by-zero on
zero-vote pools, and that floor is applied *before* the buffer multiplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DilutionModel = Literal["flat", "bucket", "inverse"]

DEFAULT_MIN_FLOOR_VEAERO = 1_000.0


@dataclass(frozen=True)
class ForecastedVotes:
    pool_address: str
    current_votes_veaero: float
    forecast_votes_veaero: float
    buffer_used: float           # the effective multiplier for visibility
    model: DilutionModel

    @property
    def expected_inflow_veaero(self) -> float:
        return self.forecast_votes_veaero - self.current_votes_veaero


def _bucket_buffer(current_votes_veaero: float) -> float:
    if current_votes_veaero < 10_000:
        return 0.50
    if current_votes_veaero < 100_000:
        return 0.25
    if current_votes_veaero < 1_000_000:
        return 0.10
    return 0.05


def _inverse_buffer(
    current_votes_veaero: float,
    scale: float = 50_000.0,
    floor: float = 0.05,
    ceiling: float = 1.00,
) -> float:
    """`buffer = clamp(scale / votes, floor, ceiling)`.

    With scale=50k: pools with 50k votes get +100% buffer; 500k → +10%; 5M → +1% (clamped to 5%).
    """
    if current_votes_veaero <= 0:
        return ceiling
    raw = scale / current_votes_veaero
    return max(floor, min(ceiling, raw))


def forecast_votes(
    pool_address: str,
    current_votes_veaero: float,
    dilution_buffer: float,
    model: DilutionModel = "flat",
    min_floor_veaero: float = DEFAULT_MIN_FLOOR_VEAERO,
) -> ForecastedVotes:
    base = max(current_votes_veaero, min_floor_veaero)
    if model == "flat":
        buffer = dilution_buffer
    elif model == "bucket":
        buffer = _bucket_buffer(current_votes_veaero)
    elif model == "inverse":
        buffer = _inverse_buffer(current_votes_veaero)
    else:
        raise ValueError(f"unknown dilution model: {model!r}")
    return ForecastedVotes(
        pool_address=pool_address,
        current_votes_veaero=current_votes_veaero,
        forecast_votes_veaero=base * (1.0 + buffer),
        buffer_used=buffer,
        model=model,
    )
