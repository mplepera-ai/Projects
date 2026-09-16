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
from core.numerical_solver import bisection
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

        for i in range(1, n_steps):
            t = external_inflow_time_hours[i]
            prev_stage = dict(stage)
            prev_storage = dict(storage)

            ext1 = {bid: external_inflow_cfs[bid][i - 1] for bid in basin_ids}
            ext2 = {bid: external_inflow_cfs[bid][i] for bid in basin_ids}

            guess = dict(stage)  # start from previous converged stage

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
                        ) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                        return lhs - rhs

                    lo = max(basin.stage_storage.min_stage, guess[bid] - 5.0)
                    hi = min(basin.stage_storage.max_stage, guess[bid] + 5.0)
                    if hi <= lo:
                        hi = lo + 1.0
                    new_stage = bisection(continuity_residual, lo, hi, tol=1e-6)

                    max_delta = max(max_delta, abs(new_stage - guess[bid]))
                    guess[bid] = new_stage
                    link_flow_now = {link.link_id: link.flow_cfs(guess) for link in network.links}

                if max_delta < convergence_tol_ft:
                    break
            else:
                raise NetworkError(
                    f"Network routing failed to converge at t={t:.2f} hr "
                    f"after {max_sweeps_per_step} sweeps (last delta={max_delta:.2e} ft). "
                    "Check for an unstable or overly stiff interbasin structure."
                )

            stage = guess
            storage = {
                bid: network.basins[bid].stage_storage.storage_at_stage(stage[bid])
                for bid in basin_ids
            }
            local_now = {bid: _local_discharge(network.basins[bid], stage[bid]) for bid in basin_ids}
            link_flow_final = {link.link_id: link.flow_cfs(stage) for link in network.links}

            for bid in basin_ids:
                inter_in1 = _sum_links_in(network, bid, prev_link_flow)
                inter_in2 = _sum_links_in(network, bid, link_flow_final)
                inter_out1 = _sum_links_out(network, bid, prev_link_flow)
                inter_out2 = _sum_links_out(network, bid, link_flow_final)

                step_ext = 0.5 * (ext1[bid] + ext2[bid]) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                step_inter_in = 0.5 * (inter_in1 + inter_in2) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                step_inter_out = 0.5 * (inter_out1 + inter_out2) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                step_onsite = 0.5 * (prev_local[bid][1] + local_now[bid][1]) * time_step_hours * CFS_HOUR_TO_ACRE_FT
                step_offsite = 0.5 * (prev_local[bid][2] + local_now[bid][2]) * time_step_hours * CFS_HOUR_TO_ACRE_FT

                totals[bid]["ext_in"] += step_ext
                totals[bid]["inter_in"] += step_inter_in
                totals[bid]["inter_out"] += step_inter_out
                totals[bid]["onsite"] += step_onsite
                totals[bid]["offsite"] += step_offsite

                time_series[bid].append(_record(
                    t, stage[bid], storage[bid], ext2[bid], inter_in2, inter_out2, local_now[bid],
                ))

            prev_local = local_now
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
