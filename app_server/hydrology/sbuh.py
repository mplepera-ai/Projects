"""
hydrology/sbuh.py

Santa Barbara Unit Hydrograph runoff generation, matching Section 14
of the spec exactly:

    I(t) = 60.5 * R(t) * A / dt        (instantaneous runoff, cfs)
    Q(t+1) = Q(t) + w * [I(t) + I(t+1) - 2*Q(t)]
    w = dt / (2*Tc + dt)

R(t) = incremental rainfall excess (inches) over the interval
A    = basin area (acres)
dt   = time step (minutes)
Tc   = time of concentration (hours)

Full precision is kept internally; rounding is a display-only concern
(handled in the reporting layer, never here).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List
from hydrology.rainfall import StormEvent, ground_storage_excess


@dataclass
class RunoffSeries:
    time_hours: List[float]           # time stamps, hours from period start
    cumulative_gross_rainfall: List[float]   # inches
    cumulative_excess_rainfall: List[float]  # inches, after ground storage
    instantaneous_runoff_cfs: List[float]    # I(t)
    routed_runoff_cfs: List[float]           # Q(t) -- SBUH-routed inflow to the basin


def generate_sbuh_runoff(
    storm: StormEvent,
    area_acres: float,
    ground_storage_inches: float,
    time_of_concentration_hours: float,
    time_step_hours: float,
    recession_threshold_fraction: float = 0.01,
    max_recession_hours: float = None,
) -> RunoffSeries:
    """
    Builds the full runoff hydrograph for one basin under one storm,
    per the Cascade-compatible SBUH method (Sections 13-15).

    IMPORTANT: the hydrograph does not stop at storm.duration_hours.
    Rainfall stops there, but the SBUH routing (a linear-reservoir-style
    attenuation) continues to release water afterward -- cutting the
    series off exactly at the storm duration throws away the recession
    tail and silently discards runoff volume (confirmed by regression
    test: doing so understated total volume by ~5.5% in one test case,
    because the routed hydrograph was still at its plateau, not yet
    receding, at the moment rainfall stopped). Instead, computation
    continues with zero further rainfall input until the routed flow
    recedes to recession_threshold_fraction of its peak (default 1%),
    or max_recession_hours elapses (default: 10x Tc, floor of 6 hours),
    whichever comes first.
    """
    if time_of_concentration_hours <= 0:
        raise ValueError("time_of_concentration_hours must be > 0")
    if time_step_hours <= 0:
        raise ValueError("time_step_hours must be > 0")
    if max_recession_hours is None:
        max_recession_hours = max(10.0 * time_of_concentration_hours, 6.0)

    dt_hr = time_step_hours
    dt_min = dt_hr * 60.0
    Tc = time_of_concentration_hours
    w = dt_hr / (2.0 * Tc + dt_hr)

    n_storm_steps = int(round(storm.duration_hours / dt_hr))
    times = [i * dt_hr for i in range(n_storm_steps + 1)]
    cum_gross = [storm.cumulative_rainfall_at(t) for t in times]
    cum_excess = ground_storage_excess(cum_gross, ground_storage_inches)
    incr_excess = [cum_excess[0]] + [
        cum_excess[i] - cum_excess[i - 1] for i in range(1, len(cum_excess))
    ]
    instantaneous = [60.5 * r * area_acres / dt_min for r in incr_excess]

    routed = [0.0] * len(times)
    routed[0] = instantaneous[0] * w
    for i in range(1, len(times)):
        routed[i] = routed[i - 1] + w * (
            instantaneous[i - 1] + instantaneous[i] - 2.0 * routed[i - 1]
        )
        if routed[i] < 0.0:
            routed[i] = 0.0

    # Recession phase: rainfall has stopped (incremental excess = 0),
    # but keep routing until the hydrograph actually recedes.
    peak = max(routed)
    extra_hours = 0.0
    while extra_hours < max_recession_hours:
        t = times[-1] + dt_hr
        times.append(t)
        cum_gross.append(cum_gross[-1])       # no further rainfall
        cum_excess.append(cum_excess[-1])
        instantaneous.append(0.0)
        new_q = routed[-1] + w * (instantaneous[-2] + instantaneous[-1] - 2.0 * routed[-1])
        if new_q < 0.0:
            new_q = 0.0
        routed.append(new_q)
        extra_hours += dt_hr
        if peak > 0 and new_q <= recession_threshold_fraction * peak:
            break

    return RunoffSeries(
        time_hours=times,
        cumulative_gross_rainfall=cum_gross,
        cumulative_excess_rainfall=cum_excess,
        instantaneous_runoff_cfs=instantaneous,
        routed_runoff_cfs=routed,
    )


def total_runoff_volume_acre_ft(series: RunoffSeries, time_step_hours: float) -> float:
    """Trapezoidal integration of the routed hydrograph, converted to acre-ft."""
    from core.units import CFS_HOUR_TO_ACRE_FT
    vol_cfs_hr = 0.0
    q = series.routed_runoff_cfs
    for i in range(1, len(q)):
        vol_cfs_hr += 0.5 * (q[i - 1] + q[i]) * time_step_hours
    return vol_cfs_hr * CFS_HOUR_TO_ACRE_FT
