"""
hydraulics/routing.py

Single-basin level-pool routing engine implementing the continuity
solve described in Section 33:

    S2 - S1 = [(I1+I2)/2 - (O1+O2)/2] * dt

solved for the new stage S2 at each time step via bisection (robust
against kinks from wells switching on/off, weirs starting to flow,
etc. -- Section 33's "numerical reliability over speed" directive).

This module handles ONE basin at a time with a single external inflow
series. Multi-basin interconnection (Section 33 step 3, "interbasin
inflows") is a planned extension once the single-basin engine is
validated against Cascade regression cases (Section 72) -- adding it
now would risk conflating two sources of error during validation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict
from core.numerical_solver import solve_bounded_continuity
from core.units import CFS_HOUR_TO_ACRE_FT, acre_ft_to_cuft
from hydraulics.basin import Basin
from hydraulics.structures import DestinationClassification


@dataclass
class TimeStepResult:
    time_hours: float
    inflow_cfs: float
    stage_ft: float
    storage_acre_ft: float
    structure_discharge_cfs: Dict[str, float]
    offsite_discharge_cfs: float
    onsite_disposal_cfs: float


@dataclass
class MassBalance:
    total_runoff_acre_ft: float
    external_inflow_acre_ft: float
    interbasin_inflow_acre_ft: float
    onsite_disposal_acre_ft: float
    offsite_discharge_acre_ft: float
    initial_storage_acre_ft: float
    final_storage_acre_ft: float
    residual_acre_ft: float
    residual_pct_of_inflow: float


@dataclass
class RoutingResult:
    basin_id: str
    time_series: List[TimeStepResult]
    mass_balance: MassBalance
    peak_stage_ft: float
    peak_stage_time_hours: float
    peak_offsite_discharge_cfs: float
    peak_offsite_discharge_time_hours: float


def route_basin(
    basin: Basin,
    inflow_time_hours: List[float],
    inflow_cfs: List[float],
    time_step_hours: float,
    zero_offsite_discharge: bool = False,
    mass_balance_tolerance_pct: float = 0.1,
) -> RoutingResult:
    """
    Runs level-pool routing for a single basin over the given inflow
    hydrograph and returns full time series + mass balance (Section 35).

    zero_offsite_discharge: when True, all structures classified
    OFFSITE_DISCHARGE are forced off for this run ONLY (their .enabled
    flag is restored afterward); ONSITE_DISPOSAL and INTERNAL_TRANSFER
    structures are left untouched, per Sections 3.3 and 37.
    """
    if len(inflow_time_hours) != len(inflow_cfs):
        raise ValueError("inflow_time_hours and inflow_cfs must be the same length")

    # Reset any stateful structures (well hysteresis) before the run.
    for s in basin.structures:
        if hasattr(s, "reset_state"):
            s.reset_state()

    # Apply zero-offsite-discharge toggle, tracking originals to restore.
    original_enabled = {id(s): s.enabled for s in basin.structures}
    if zero_offsite_discharge:
        for s in basin.structures:
            if s.destination == DestinationClassification.OFFSITE_DISCHARGE:
                s.enabled = False

    try:
        stage = basin.initial_stage_ft
        storage = basin.stage_storage.storage_at_stage(stage)
        min_storage = basin.stage_storage.storage_at_stage(basin.stage_storage.min_stage)
        results: List[TimeStepResult] = []

        total_offsite_acre_ft = 0.0
        total_onsite_acre_ft = 0.0
        total_inflow_acre_ft = 0.0

        prev_inflow = inflow_cfs[0]
        prev_stage = stage
        prev_storage = storage

        # Commit hysteresis state for the initial stage before anything
        # else reads it (t=0 record, and the first time step's trials).
        _commit_hysteresis(basin, stage)

        # record t=0
        d0 = _total_discharge(basin, stage, tailwater_ft=0.0)
        prev_total_out, _prev_by_struct, prev_offsite_cfs, prev_onsite_cfs = d0
        results.append(_make_step(0, inflow_time_hours[0], prev_inflow, stage, storage, d0))

        for i in range(1, len(inflow_time_hours)):
            t = inflow_time_hours[i]
            I1, I2 = inflow_cfs[i - 1], inflow_cfs[i]
            S1 = prev_storage

            def continuity_residual(trial_stage: float) -> float:
                total_out, _by_struct, _off, _on = _total_discharge(
                    basin, trial_stage, tailwater_ft=0.0
                )
                trial_storage = basin.stage_storage.storage_at_stage(trial_stage)
                lhs = trial_storage - S1
                rhs = (
                    0.5 * (I1 + I2) - 0.5 * (prev_total_out + total_out)
                ) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                return lhs - rhs

            # Search the basin's FULL physical stage range, not a narrow
            # window around the previous stage -- see solve_bounded_continuity
            # for why (a fixed-capacity well/pump can legitimately ask to
            # drain more water than the basin holds, which a narrow window
            # plus naive bracket expansion would turn into a runaway search
            # past the curve's defined max stage).
            new_stage = solve_bounded_continuity(
                continuity_residual,
                basin.stage_storage.min_stage,
                basin.stage_storage.max_stage,
                tol=1e-5,
                preferred_stage=prev_stage,
            )
            new_storage = basin.stage_storage.storage_at_stage(new_stage)

            # Report this step's discharge using the SAME (still frozen,
            # pre-commit) hysteresis state that continuity_residual used to
            # solve for new_stage -- not whatever state a transition would
            # put it in. If a well's on/off memory were updated first, a
            # transition that only takes effect starting NEXT step (that's
            # what commit-after-solve means) would get credited to THIS
            # step's reported/mass-balance discharge despite never having
            # been part of the equation that produced this step's storage
            # change -- silently manufacturing water that was never
            # actually moved. Commit the transition only after this read.
            total_out, by_struct, offsite_cfs, onsite_cfs = _total_discharge(
                basin, new_stage, tailwater_ft=0.0
            )
            _commit_hysteresis(basin, new_stage)
            # If that commit just flipped a structure's on/off memory, the
            # rate that will govern the NEXT interval (read against the
            # now-updated state) differs from the rate that governed THIS
            # interval (total_out/onsite_cfs/offsite_cfs above, read
            # pre-commit) -- both are physically real readings at the same
            # instant, on either side of a discontinuous switch. Carrying
            # the pre-commit reading forward as the next interval's
            # boundary rate would smear this instant's transitional value
            # across that whole next interval's trapezoidal integral, the
            # same ordering bug this module's committing-before-or-after
            # comment above already fixed for the CURRENT interval -- this
            # is its mirror image for the NEXT one.
            total_out_next, _by_struct_next, offsite_next, onsite_next = _total_discharge(
                basin, new_stage, tailwater_ft=0.0
            )
            total_out, by_struct, offsite_cfs, onsite_cfs = _cap_outflow_to_available_water(
                min_storage, new_storage, S1, I1, I2,
                prev_total_out, total_out, by_struct, offsite_cfs, onsite_cfs,
                time_step_hours,
            )

            # Use the previous step's ACTUAL (already capped, if capping
            # applied) offsite/onsite flows here, not a fresh recompute
            # from prev_stage -- recomputing would silently ignore the
            # availability cap above and overstate the trapezoidal average
            # whenever a structure was supply-limited on the prior step.
            step_offsite_acre_ft = 0.5 * (
                prev_offsite_cfs + offsite_cfs
            ) * time_step_hours * CFS_HOUR_TO_ACRE_FT
            step_onsite_acre_ft = 0.5 * (
                prev_onsite_cfs + onsite_cfs
            ) * time_step_hours * CFS_HOUR_TO_ACRE_FT
            step_inflow_acre_ft = 0.5 * (I1 + I2) * time_step_hours * CFS_HOUR_TO_ACRE_FT

            total_offsite_acre_ft += step_offsite_acre_ft
            total_onsite_acre_ft += step_onsite_acre_ft
            total_inflow_acre_ft += step_inflow_acre_ft

            results.append(_make_step(i, t, I2, new_stage, new_storage,
                                       (total_out, by_struct, offsite_cfs, onsite_cfs)))

            prev_stage = new_stage
            prev_storage = new_storage
            prev_total_out = total_out_next
            prev_offsite_cfs = offsite_next
            prev_onsite_cfs = onsite_next
            prev_inflow = I2

        initial_storage = basin.initial_storage_acre_ft()
        final_storage = results[-1].storage_acre_ft
        residual = (
            total_inflow_acre_ft + initial_storage
            - total_onsite_acre_ft - total_offsite_acre_ft - final_storage
        )
        residual_pct = (
            abs(residual) / total_inflow_acre_ft * 100.0 if total_inflow_acre_ft > 0 else 0.0
        )

        mb = MassBalance(
            total_runoff_acre_ft=total_inflow_acre_ft,
            external_inflow_acre_ft=total_inflow_acre_ft,
            interbasin_inflow_acre_ft=0.0,
            onsite_disposal_acre_ft=total_onsite_acre_ft,
            offsite_discharge_acre_ft=total_offsite_acre_ft,
            initial_storage_acre_ft=initial_storage,
            final_storage_acre_ft=final_storage,
            residual_acre_ft=residual,
            residual_pct_of_inflow=residual_pct,
        )

        peak = max(results, key=lambda r: r.stage_ft)
        peak_off = max(results, key=lambda r: r.offsite_discharge_cfs)

        return RoutingResult(
            basin_id=basin.basin_id,
            time_series=results,
            mass_balance=mb,
            peak_stage_ft=peak.stage_ft,
            peak_stage_time_hours=peak.time_hours,
            peak_offsite_discharge_cfs=peak_off.offsite_discharge_cfs,
            peak_offsite_discharge_time_hours=peak_off.time_hours,
        )
    finally:
        # Always restore original enabled state -- disabling for a
        # zero-offsite run must never permanently mutate the model.
        for s in basin.structures:
            s.enabled = original_enabled[id(s)]


def _commit_hysteresis(basin: Basin, accepted_stage_ft: float) -> None:
    """Apply on/off transitions for hysteretic structures (wells, pumps)
    using an ACCEPTED stage only -- never a bisection trial value. See
    Structure.discharge()/update_hysteresis() docstrings for why this
    must stay separate from the discharge() calls used during solving."""
    for s in basin.structures:
        if hasattr(s, "update_hysteresis"):
            s.update_hysteresis(accepted_stage_ft)


def _cap_outflow_to_available_water(
    min_storage_acre_ft: float,
    new_storage_acre_ft: float,
    prev_storage_acre_ft: float,
    I1: float,
    I2: float,
    prev_total_out: float,
    total_out: float,
    by_struct: Dict[str, float],
    offsite_cfs: float,
    onsite_cfs: float,
    time_step_hours: float,
):
    """When solving continuity lands a step at (or effectively at) the
    basin's minimum STORAGE -- which, on a curve with a flat/zero-storage
    region, can happen at more than one stage, not only the curve's lowest
    defined stage -- the structures' nominal discharge (e.g. a well's full
    rated capacity) is not physically realizable: there isn't that much
    water in the pond plus this step's inflow to give. Reported discharge
    for that step is scaled down to exactly what conservation allows (each
    structure's share of the total preserved, only the total capped), so
    mass-balance and per-structure hydrographs never claim more water
    leaving the basin than actually flowed through it."""
    if new_storage_acre_ft > min_storage_acre_ft + 1e-9 or total_out <= 0:
        return total_out, by_struct, offsite_cfs, onsite_cfs

    storage_diff = new_storage_acre_ft - prev_storage_acre_ft
    available_total_out = (
        (I1 + I2) - prev_total_out
        - 2.0 * storage_diff / (time_step_hours * CFS_HOUR_TO_ACRE_FT)
    )
    available_total_out = max(0.0, available_total_out)
    if available_total_out >= total_out:
        return total_out, by_struct, offsite_cfs, onsite_cfs

    factor = available_total_out / total_out
    capped_by_struct = {name: q * factor for name, q in by_struct.items()}
    return available_total_out, capped_by_struct, offsite_cfs * factor, onsite_cfs * factor


def _total_discharge(basin: Basin, stage_ft: float, tailwater_ft: float):
    by_struct: Dict[str, float] = {}
    offsite = 0.0
    onsite = 0.0
    total = 0.0
    for s in basin.structures:
        q = s.discharge(stage_ft, tailwater_ft) if s.enabled else 0.0
        by_struct[s.name] = q
        total += q
        if s.destination == DestinationClassification.OFFSITE_DISCHARGE:
            offsite += q
        elif s.destination == DestinationClassification.ONSITE_DISPOSAL:
            onsite += q
        # INTERNAL_TRANSFER counted in total but not on/off site here;
        # multi-basin accounting handles that at the network level.
    return total, by_struct, offsite, onsite


def _offsite_of(_total_out_unused, basin: Basin, stage_ft: float) -> float:
    _, _, offsite, _ = _total_discharge(basin, stage_ft, 0.0)
    return offsite


def _onsite_of(_total_out_unused, basin: Basin, stage_ft: float) -> float:
    _, _, _, onsite = _total_discharge(basin, stage_ft, 0.0)
    return onsite


def _make_step(i, t, inflow, stage, storage, discharge_tuple) -> TimeStepResult:
    total_out, by_struct, offsite, onsite = discharge_tuple
    return TimeStepResult(
        time_hours=t,
        inflow_cfs=inflow,
        stage_ft=stage,
        storage_acre_ft=storage,
        structure_discharge_cfs=by_struct,
        offsite_discharge_cfs=offsite,
        onsite_disposal_cfs=onsite,
    )
