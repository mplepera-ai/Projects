"""
hydraulics/network.py

Multi-basin routing. This is genuinely different from single-basin
routing (routing.py), not just a loop over basins: an interbasin
structure's flow depends on the stages of BOTH basins it connects, so
all basin stages within a network must be solved simultaneously at
each time step, not one after another.

Approach: Gauss-Seidel / block relaxation. At each time step, sweep
through all basins repeatedly, solving each basin's continuity
equation while holding every OTHER basin's stage fixed at its most
recent iterate, until the whole network's stages stop moving between
sweeps. This is the standard, numerically robust way to solve coupled
reservoir/basin networks without building a full Jacobian.

zero_offsite_discharge here disables OFFSITE_DISCHARGE structures
NETWORK-WIDE -- Section 3.3's rule applies per structure regardless of
which basin it happens to sit on, and Section 37's compliance check
("Offsite Discharge = 0") is a network-total statement, not a
per-basin one.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from core.numerical_solver import solve_bounded_continuity
from core.units import CFS_HOUR_TO_ACRE_FT
from hydraulics.basin import Basin
from hydraulics.structures import Structure, DestinationClassification


class NetworkError(Exception):
    pass


@dataclass
class InterbasinLink:
    """A structure (orifice, weir, culvert-like rating) that transfers
    flow from one basin to another. The structure object itself does
    the hydraulics (discharge(headwater, tailwater)); this wrapper just
    records which basins it connects for network bookkeeping."""
    link_id: str
    from_basin_id: str
    to_basin_id: str
    structure: Structure

    def flow_cfs(self, stage_by_basin: Dict[str, float]) -> float:
        if not self.structure.enabled:
            return 0.0
        headwater = stage_by_basin[self.from_basin_id]
        tailwater = stage_by_basin[self.to_basin_id]
        return self.structure.discharge(headwater, tailwater)


@dataclass
class BasinNetwork:
    basins: Dict[str, Basin]
    links: List[InterbasinLink] = field(default_factory=list)

    def __post_init__(self):
        basin_ids = set(self.basins.keys())
        for link in self.links:
            if link.from_basin_id not in basin_ids or link.to_basin_id not in basin_ids:
                raise NetworkError(
                    f"Link '{link.link_id}' references an unknown basin "
                    f"({link.from_basin_id} -> {link.to_basin_id})."
                )
            if link.from_basin_id == link.to_basin_id:
                raise NetworkError(f"Link '{link.link_id}' connects a basin to itself.")


@dataclass
class BasinTimeStepResult:
    time_hours: float
    stage_ft: float
    storage_acre_ft: float
    external_inflow_cfs: float
    interbasin_inflow_cfs: float
    interbasin_outflow_cfs: float
    local_offsite_discharge_cfs: float
    local_onsite_disposal_cfs: float
    structure_discharge_cfs: Dict[str, float] = field(default_factory=dict)


@dataclass
class BasinMassBalance:
    external_inflow_acre_ft: float
    interbasin_inflow_acre_ft: float
    interbasin_outflow_acre_ft: float
    onsite_disposal_acre_ft: float
    offsite_discharge_acre_ft: float
    initial_storage_acre_ft: float
    final_storage_acre_ft: float
    residual_acre_ft: float
    residual_pct_of_inflow: float


@dataclass
class BasinNetworkResult:
    time_series: Dict[str, List[BasinTimeStepResult]]
    mass_balance: Dict[str, BasinMassBalance]
    peak_stage_ft: Dict[str, float]
    peak_stage_time_hours: Dict[str, float]
    network_total_offsite_discharge_acre_ft: float
    network_interbasin_conservation_check_acre_ft: float  # should be ~0


def route_network(
    network: BasinNetwork,
    external_inflow_time_hours: List[float],
    external_inflow_cfs: Dict[str, List[float]],
    time_step_hours: float,
    zero_offsite_discharge: bool = False,
    max_sweeps_per_step: int = 60,
    convergence_tol_ft: float = 1e-5,
) -> BasinNetworkResult:
    basin_ids = list(network.basins.keys())
    n_steps = len(external_inflow_time_hours)
    for bid in basin_ids:
        if len(external_inflow_cfs[bid]) != n_steps:
            raise NetworkError(f"External inflow series for basin '{bid}' has wrong length.")

    # Reset stateful structures (well hysteresis) on both basin-local
    # structures and interbasin link structures.
    for b in network.basins.values():
        for s in b.structures:
            if hasattr(s, "reset_state"):
                s.reset_state()
    for link in network.links:
        if hasattr(link.structure, "reset_state"):
            link.structure.reset_state()

    # Apply network-wide zero-offsite-discharge toggle; restore after.
    original_enabled: Dict[int, bool] = {}
    all_structures: List[Structure] = []
    for b in network.basins.values():
        all_structures.extend(b.structures)
    all_structures.extend(link.structure for link in network.links)
    for s in all_structures:
        original_enabled[id(s)] = s.enabled
    if zero_offsite_discharge:
        for s in all_structures:
            if s.destination == DestinationClassification.OFFSITE_DISCHARGE:
                s.enabled = False

    try:
        stage = {bid: network.basins[bid].initial_stage_ft for bid in basin_ids}
        storage = {
            bid: network.basins[bid].stage_storage.storage_at_stage(stage[bid])
            for bid in basin_ids
        }

        # Commit hysteresis for the initial stage before it's used anywhere.
        # discharge() itself is a pure read of on/off memory (see
        # hydraulics/structures.py) -- it must never be mutated from inside
        # a Gauss-Seidel sweep's trial evaluations, only here, against an
        # accepted stage, exactly like the single-basin routing.py solver.
        _commit_hysteresis(network, stage)

        time_series: Dict[str, List[BasinTimeStepResult]] = {bid: [] for bid in basin_ids}
        totals = {
            bid: {"ext_in": 0.0, "inter_in": 0.0, "inter_out": 0.0, "onsite": 0.0, "offsite": 0.0}
            for bid in basin_ids
        }

        # t = 0 record
        prev_local = {bid: _local_discharge(network.basins[bid], stage[bid]) for bid in basin_ids}
        prev_link_flow = {link.link_id: link.flow_cfs(stage) for link in network.links}
        for bid in basin_ids:
            time_series[bid].append(_record(
                external_inflow_time_hours[0], stage[bid], storage[bid],
                external_inflow_cfs[bid][0],
                _sum_links_in(network, bid, prev_link_flow),
                _sum_links_out(network, bid, prev_link_flow),
                prev_local[bid],
            ))

        # Number of sub-divisions used to re-solve a macro time step when the
        # whole-step (fast path) solve implies a hysteresis transition
        # happened somewhere inside it. Discretely committing a transition
        # only at whole-step boundaries smears it over the full
        # time_step_hours; re-solving at this finer resolution catches it
        # close to when it actually occurs and is what closes most of the
        # remaining mass-balance residual left over after Bugs 3/4 above.
        SUBSTEP_REFINEMENT = 20

        for i in range(1, n_steps):
            t = external_inflow_time_hours[i]
            prev_stage = dict(stage)
            prev_storage = dict(storage)

            ext1 = {bid: external_inflow_cfs[bid][i - 1] for bid in basin_ids}
            ext2 = {bid: external_inflow_cfs[bid][i] for bid in basin_ids}

            try:
                fast = _advance_one_step(
                    network, basin_ids, prev_stage, prev_storage, prev_local,
                    prev_link_flow, ext1, ext2, time_step_hours,
                    max_sweeps_per_step, convergence_tol_ft, commit=False,
                )
            except NetworkError as e:
                raise NetworkError(f"{e} (macro step at t={t:.2f} hr)") from e

            if not fast.transitioned:
                # Fast path -- no transition inside this step, so the
                # whole-step solve is exact. Commit hysteresis now (the
                # detection solve above deliberately left it uncommitted).
                _commit_hysteresis(network, fast.stage)
                stage = fast.stage
                storage = fast.storage
                local_now = fast.local
                local_for_next = fast.local_next
                link_flow_final = fast.link_flow
                step_totals = fast.totals
            else:
                # Discard the whole-step result and re-solve the same
                # interval as SUBSTEP_REFINEMENT smaller steps, each with
                # linearly interpolated external inflow (consistent with
                # the trapezoidal treatment continuity_residual already
                # assumes across a step), committing hysteresis at each
                # sub-step boundary.
                sub_dt = time_step_hours / SUBSTEP_REFINEMENT
                sub_stage = dict(prev_stage)
                sub_storage = dict(prev_storage)
                sub_local = dict(prev_local)
                sub_link_flow = dict(prev_link_flow)
                sub_local_display = sub_local
                step_totals = {
                    bid: {"ext_in": 0.0, "inter_in": 0.0, "inter_out": 0.0,
                          "onsite": 0.0, "offsite": 0.0}
                    for bid in basin_ids
                }
                for k in range(1, SUBSTEP_REFINEMENT + 1):
                    frac1 = (k - 1) / SUBSTEP_REFINEMENT
                    frac2 = k / SUBSTEP_REFINEMENT
                    sub_ext1 = {bid: ext1[bid] + frac1 * (ext2[bid] - ext1[bid]) for bid in basin_ids}
                    sub_ext2 = {bid: ext1[bid] + frac2 * (ext2[bid] - ext1[bid]) for bid in basin_ids}
                    try:
                        sub = _advance_one_step(
                            network, basin_ids, sub_stage, sub_storage, sub_local,
                            sub_link_flow, sub_ext1, sub_ext2, sub_dt,
                            max_sweeps_per_step, convergence_tol_ft, commit=True,
                        )
                    except NetworkError as e:
                        raise NetworkError(
                            f"{e} (sub-step {k}/{SUBSTEP_REFINEMENT} within macro "
                            f"step at t={t:.2f} hr)"
                        ) from e
                    for bid in basin_ids:
                        for key in step_totals[bid]:
                            step_totals[bid][key] += sub.totals[bid][key]
                    sub_stage = sub.stage
                    sub_storage = sub.storage
                    # Chain local_next (not local) into the next sub-step's
                    # prev_local -- see _StepResult's docstring on why
                    # reusing the pre-commit `local` here would smear a
                    # transition's instantaneous rate across whatever this
                    # sub-step's own duration is.
                    sub_local = sub.local_next
                    sub_local_display = sub.local
                    sub_link_flow = sub.link_flow

                stage = sub_stage
                storage = sub_storage
                local_now = sub_local_display
                local_for_next = sub_local
                link_flow_final = sub_link_flow

            for bid in basin_ids:
                for key in totals[bid]:
                    totals[bid][key] += step_totals[bid][key]

                inter_in2 = _sum_links_in(network, bid, link_flow_final)
                inter_out2 = _sum_links_out(network, bid, link_flow_final)
                time_series[bid].append(_record(
                    t, stage[bid], storage[bid], ext2[bid], inter_in2, inter_out2, local_now[bid],
                ))

            # Feed local_next forward as the NEXT macro step's prev_local --
            # see _StepResult's docstring: reusing the pre-commit `local`
            # here would carry this step's closing (possibly transitional)
            # rate into the next step's trapezoidal integral instead of the
            # rate its own (possibly just-updated) state actually implies.
            prev_local = local_for_next
            prev_link_flow = link_flow_final

        mass_balance: Dict[str, BasinMassBalance] = {}
        peak_stage_ft: Dict[str, float] = {}
        peak_stage_time: Dict[str, float] = {}

        for bid in basin_ids:
            initial_storage = network.basins[bid].initial_storage_acre_ft()
            final_storage = time_series[bid][-1].storage_acre_ft
            t = totals[bid]
            residual = (
                t["ext_in"] + t["inter_in"] + initial_storage
                - t["inter_out"] - t["onsite"] - t["offsite"] - final_storage
            )
            total_in = t["ext_in"] + t["inter_in"]
            residual_pct = abs(residual) / total_in * 100.0 if total_in > 0 else 0.0

            mass_balance[bid] = BasinMassBalance(
                external_inflow_acre_ft=t["ext_in"],
                interbasin_inflow_acre_ft=t["inter_in"],
                interbasin_outflow_acre_ft=t["inter_out"],
                onsite_disposal_acre_ft=t["onsite"],
                offsite_discharge_acre_ft=t["offsite"],
                initial_storage_acre_ft=initial_storage,
                final_storage_acre_ft=final_storage,
                residual_acre_ft=residual,
                residual_pct_of_inflow=residual_pct,
            )

            peak_step = max(time_series[bid], key=lambda r: r.stage_ft)
            peak_stage_ft[bid] = peak_step.stage_ft
            peak_stage_time[bid] = peak_step.time_hours

        network_offsite_total = sum(mb.offsite_discharge_acre_ft for mb in mass_balance.values())
        total_inter_in = sum(mb.interbasin_inflow_acre_ft for mb in mass_balance.values())
        total_inter_out = sum(mb.interbasin_outflow_acre_ft for mb in mass_balance.values())
        conservation_check = total_inter_in - total_inter_out  # should be ~0

        return BasinNetworkResult(
            time_series=time_series,
            mass_balance=mass_balance,
            peak_stage_ft=peak_stage_ft,
            peak_stage_time_hours=peak_stage_time,
            network_total_offsite_discharge_acre_ft=network_offsite_total,
            network_interbasin_conservation_check_acre_ft=conservation_check,
        )
    finally:
        for s in all_structures:
            s.enabled = original_enabled[id(s)]


# ---- helpers ----------------------------------------------------------

def _commit_hysteresis(network: BasinNetwork, stage_by_basin: Dict[str, float]) -> None:
    """Apply on/off transitions for every hysteretic structure (wells,
    pumps) in the network -- basin-local AND interbasin links -- using an
    ACCEPTED stage per basin. Must only be called against a converged
    time-step result (or the initial stage), never from inside a
    Gauss-Seidel sweep's trial evaluations."""
    for basin in network.basins.values():
        for s in basin.structures:
            if hasattr(s, "update_hysteresis"):
                s.update_hysteresis(stage_by_basin[basin.basin_id])
    for link in network.links:
        if hasattr(link.structure, "update_hysteresis"):
            link.structure.update_hysteresis(stage_by_basin[link.from_basin_id])


@dataclass
class _StepResult:
    """Everything one Gauss-Seidel-solved interval produces: the
    converged stage/storage, discharge reported against the still-frozen
    (pre-commit) hysteresis state, the mass-balance contribution of just
    this interval, and whether any hysteretic structure's solved stage
    implies an on/off transition happened somewhere inside it.

    local vs. local_next: at a time boundary where a transition is
    committed, there are two physically different discharge readings at
    the SAME instant -- the rate that governed the interval just ending
    (pre-commit, frozen state -- this is `local`, and is what closes out
    THIS interval's own mass balance and what gets reported/displayed for
    it) and the rate that governs the interval about to start (post-commit,
    updated state -- this is `local_next`). Callers MUST pass local_next,
    not local, as the next call's prev_local: reusing `local` there would
    carry the old interval's frozen-state rate into the new interval's
    trapezoidal integral, smearing a single instant's transitional rate
    across whatever (possibly much larger) duration the next interval
    spans. When commit=False (or nothing actually transitioned), the two
    are identical -- nothing changed, so there's nothing to distinguish."""
    stage: Dict[str, float]
    storage: Dict[str, float]
    local: Dict[str, Tuple[float, float, float, Dict[str, float]]]
    local_next: Dict[str, Tuple[float, float, float, Dict[str, float]]]
    link_flow: Dict[str, float]
    totals: Dict[str, Dict[str, float]]
    transitioned: bool


def _advance_one_step(
    network: BasinNetwork,
    basin_ids: List[str],
    prev_stage: Dict[str, float],
    prev_storage: Dict[str, float],
    prev_local: Dict[str, Tuple[float, float, float, Dict[str, float]]],
    prev_link_flow: Dict[str, float],
    ext1: Dict[str, float],
    ext2: Dict[str, float],
    dt_hours: float,
    max_sweeps_per_step: int,
    convergence_tol_ft: float,
    commit: bool,
) -> "_StepResult":
    """Solve every basin's continuity equation over one interval of
    length dt_hours via Gauss-Seidel sweeps (the same algorithm
    route_network always used, just factored out so it can be called
    either once per macro time step or repeatedly at finer sub-step
    resolution), then report discharge, detect whether any hysteretic
    structure's solved stage implies an on/off transition happened
    somewhere inside this interval, optionally commit that transition,
    and cap reported local discharge to what's physically available.

    commit=False is used for a macro step's first "fast path" attempt:
    it solves and reports whether a transition would occur, but leaves
    hysteresis state untouched, so the caller can discard the result and
    re-solve the same interval as several smaller sub-steps if a
    transition is detected -- without the structures having already
    flipped based on the coarse (and, per Bugs 3/4 above, provably
    inaccurate right at a transition) whole-step solve. commit=True is
    used once the caller has decided this interval's result is final
    (either because it had no transition, or because it IS one of the
    smaller sub-steps of a refined solve)."""
    guess = dict(prev_stage)
    max_delta = 0.0

    for sweep in range(max_sweeps_per_step):
        max_delta = 0.0
        link_flow_now = {link.link_id: link.flow_cfs(guess) for link in network.links}

        for bid in basin_ids:
            basin = network.basins[bid]
            S1 = prev_storage[bid]

            inter_in1 = _sum_links_in(network, bid, prev_link_flow)
            inter_out1 = _sum_links_out(network, bid, prev_link_flow)
            local1 = prev_local[bid]

            def continuity_residual(trial_stage: float, bid=bid, basin=basin) -> float:
                trial_guess = dict(guess)
                trial_guess[bid] = trial_stage
                link_flow_trial = {
                    link.link_id: link.flow_cfs(trial_guess)
                    for link in network.links
                    if link.from_basin_id == bid or link.to_basin_id == bid
                }
                merged = dict(link_flow_now)
                merged.update(link_flow_trial)

                inter_in2 = _sum_links_in(network, bid, merged)
                inter_out2 = _sum_links_out(network, bid, merged)
                local2 = _local_discharge(basin, trial_stage)

                trial_storage = basin.stage_storage.storage_at_stage(trial_stage)
                lhs = trial_storage - S1
                rhs = (
                    0.5 * (ext1[bid] + ext2[bid])
                    + 0.5 * (inter_in1 + inter_in2)
                    - 0.5 * (local1[0] + local2[0])
                    - 0.5 * (inter_out1 + inter_out2)
                ) * dt_hours * CFS_HOUR_TO_ACRE_FT
                return lhs - rhs

            new_stage = solve_bounded_continuity(
                continuity_residual,
                basin.stage_storage.min_stage,
                basin.stage_storage.max_stage,
                tol=1e-6,
                preferred_stage=guess[bid],
            )

            max_delta = max(max_delta, abs(new_stage - guess[bid]))
            guess[bid] = new_stage
            link_flow_now = {link.link_id: link.flow_cfs(guess) for link in network.links}

        if max_delta < convergence_tol_ft:
            break
    else:
        raise NetworkError(
            f"Network routing failed to converge within a {dt_hours:.4f} hr "
            f"interval after {max_sweeps_per_step} sweeps (last delta="
            f"{max_delta:.2e} ft). Check for an unstable or overly stiff "
            "interbasin structure."
        )

    stage = guess
    storage = {
        bid: network.basins[bid].stage_storage.storage_at_stage(stage[bid])
        for bid in basin_ids
    }
    # Report discharge using the SAME (still frozen, pre-commit) hysteresis
    # state the sweeps above solved with -- committing before this read
    # would silently credit this interval with water that was never part
    # of the equation that produced its storage change.
    local_now = {bid: _local_discharge(network.basins[bid], stage[bid]) for bid in basin_ids}
    link_flow_final = {link.link_id: link.flow_cfs(stage) for link in network.links}

    transitioned = False
    for bid in basin_ids:
        for s in network.basins[bid].structures:
            if hasattr(s, "would_transition") and s.would_transition(stage[bid]):
                transitioned = True
    for link in network.links:
        if hasattr(link.structure, "would_transition") and link.structure.would_transition(
            stage[link.from_basin_id]
        ):
            transitioned = True

    if commit:
        _commit_hysteresis(network, stage)

    local_next = local_now
    if commit and transitioned:
        # The on/off state actually changed at this boundary -- the rate
        # governing the interval about to START differs from the rate
        # that governed the interval just CLOSING, even though both are
        # read at the same stage/instant (see the local/local_next
        # docstring on _StepResult). Not capped here: capping is specific
        # to closing out an interval against ITS OWN boundary conditions,
        # and local_next will get that treatment on its own terms once
        # it's used to solve the next interval.
        local_next = {bid: _local_discharge(network.basins[bid], stage[bid]) for bid in basin_ids}

    # Cap basin-local structures' reported discharge to what conservation
    # actually allows. This is applied UNCONDITIONALLY rather than gated
    # on "storage is at/near the curve's floor": a curve with a flat or
    # near-flat storage region (storage roughly constant over a stage
    # range) makes continuity_residual's dependence on trial_stage
    # vanish across that whole region, so bisection can converge to
    # *any* stage within it -- including one just past the flat region's
    # edge, where storage is a hair above the true floor and a
    # floor-proximity check misses it even though the solved discharge
    # is still not physically realizable. Checking conservation directly
    # (available water vs. demanded discharge) instead of inferring it
    # from where the solve happened to land is correct in every case,
    # and is a no-op whenever the underlying solve was already exact
    # (available_total_l ends up >= total_l, so nothing changes). NOTE:
    # covers basin-local structures only, not interbasin link flow (not
    # needed for this project's single-basin drainage-well scenario).
    for bid in basin_ids:
        basin = network.basins[bid]
        total_l, onsite_l, offsite_l, per_struct_l = local_now[bid]
        if total_l <= 0:
            continue
        inter_in1 = _sum_links_in(network, bid, prev_link_flow)
        inter_in2 = _sum_links_in(network, bid, link_flow_final)
        inter_out1 = _sum_links_out(network, bid, prev_link_flow)
        inter_out2 = _sum_links_out(network, bid, link_flow_final)
        storage_diff = storage[bid] - prev_storage[bid]
        available_total_l = (
            (ext1[bid] + ext2[bid]) + (inter_in1 + inter_in2) - (inter_out1 + inter_out2)
            - prev_local[bid][0]
            - 2.0 * storage_diff / (dt_hours * CFS_HOUR_TO_ACRE_FT)
        )
        available_total_l = max(0.0, available_total_l)
        if available_total_l < total_l:
            factor = available_total_l / total_l
            local_now[bid] = (
                available_total_l, onsite_l * factor, offsite_l * factor,
                {n: q * factor for n, q in per_struct_l.items()},
            )

    totals: Dict[str, Dict[str, float]] = {}
    for bid in basin_ids:
        inter_in1 = _sum_links_in(network, bid, prev_link_flow)
        inter_in2 = _sum_links_in(network, bid, link_flow_final)
        inter_out1 = _sum_links_out(network, bid, prev_link_flow)
        inter_out2 = _sum_links_out(network, bid, link_flow_final)

        totals[bid] = {
            "ext_in": 0.5 * (ext1[bid] + ext2[bid]) * dt_hours * CFS_HOUR_TO_ACRE_FT,
            "inter_in": 0.5 * (inter_in1 + inter_in2) * dt_hours * CFS_HOUR_TO_ACRE_FT,
            "inter_out": 0.5 * (inter_out1 + inter_out2) * dt_hours * CFS_HOUR_TO_ACRE_FT,
            "onsite": 0.5 * (prev_local[bid][1] + local_now[bid][1]) * dt_hours * CFS_HOUR_TO_ACRE_FT,
            "offsite": 0.5 * (prev_local[bid][2] + local_now[bid][2]) * dt_hours * CFS_HOUR_TO_ACRE_FT,
        }

    return _StepResult(
        stage=stage, storage=storage, local=local_now, local_next=local_next,
        link_flow=link_flow_final, totals=totals, transitioned=transitioned,
    )


def _local_discharge(basin: Basin, stage_ft: float) -> Tuple[float, float, float, Dict[str, float]]:
    """Returns (total_local, onsite, offsite, per_structure_cfs) from a
    basin's OWN structures only (not interbasin links, which are
    handled at the network level). per_structure_cfs lets reports show
    an individual time-history table per well/pump/weir, matching
    Cascade's per-structure output format."""
    total = 0.0
    onsite = 0.0
    offsite = 0.0
    per_structure: Dict[str, float] = {}
    for s in basin.structures:
        q = s.discharge(stage_ft, 0.0) if s.enabled else 0.0
        per_structure[s.name] = q
        total += q
        if s.destination == DestinationClassification.ONSITE_DISPOSAL:
            onsite += q
        elif s.destination == DestinationClassification.OFFSITE_DISCHARGE:
            offsite += q
    return total, onsite, offsite, per_structure


def _sum_links_in(network: BasinNetwork, basin_id: str, flows: Dict[str, float]) -> float:
    return sum(flows[l.link_id] for l in network.links if l.to_basin_id == basin_id)


def _sum_links_out(network: BasinNetwork, basin_id: str, flows: Dict[str, float]) -> float:
    return sum(flows[l.link_id] for l in network.links if l.from_basin_id == basin_id)


def _record(t, stage, storage, ext_in, inter_in, inter_out, local_tuple) -> BasinTimeStepResult:
    _total, onsite, offsite, per_structure = local_tuple
    return BasinTimeStepResult(
        time_hours=t,
        stage_ft=stage,
        storage_acre_ft=storage,
        external_inflow_cfs=ext_in,
        interbasin_inflow_cfs=inter_in,
        interbasin_outflow_cfs=inter_out,
        local_offsite_discharge_cfs=offsite,
        local_onsite_disposal_cfs=onsite,
        structure_discharge_cfs=per_structure,
    )
