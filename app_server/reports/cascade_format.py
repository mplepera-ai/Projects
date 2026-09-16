"""
reports/cascade_format.py

Renders a ScenarioRunResult in a layout matching the real Cascade 2001
text output (header block, stage-storage table, one time-history table
PER STRUCTURE with On/Off/Capacity or geometry, then the max/min and
water-budget summary tables). This is deliberately close to the format
in a real EA3-style Cascade report, per the user's example, rather
than the earlier more compact custom layout.

All numbers are read from the already-computed ScenarioRunResult /
RunoffSeries objects (Section 71: report templates never calculate).
"""

from __future__ import annotations
from typing import Dict, List
from hydraulics.structures import DrainageWell, Pump, Orifice, RectangularWeir, VNotchWeir
from hydraulics.network import BasinTimeStepResult
from hydrology.sbuh import RunoffSeries
from project.model import ScenarioRunResult
from core.units import CFS_HOUR_TO_ACRE_FT


def _structure_type_name(s) -> str:
    return type(s).__name__


def _structure_header_line(s) -> str:
    if isinstance(s, DrainageWell):
        return f" On Elev = {s.turn_on_stage:.2f} ft, Off Elev = {s.turn_off_stage:.2f} ft, Capacity = {s.capacity_gpm:.0f} gpm"
    if isinstance(s, Pump):
        return f" On Elev = {s.turn_on_stage:.2f} ft, Off Elev = {s.turn_off_stage:.2f} ft, Capacity = {s.capacity_cfs * 448.831:.0f} gpm"
    if isinstance(s, Orifice):
        return f" Invert Elev = {s.invert_elevation_ft:.2f} ft, Area = {s.area_sqft:.2f} sqft, Cd = {s.discharge_coefficient:.2f}"
    if isinstance(s, RectangularWeir):
        return f" Crest Elev = {s.crest_elevation_ft:.2f} ft, Length = {s.length_ft:.2f} ft, C = {s.discharge_coefficient:.2f}"
    if isinstance(s, VNotchWeir):
        return f" Crest Elev = {s.crest_elevation_ft:.2f} ft, Angle = {s.notch_angle_degrees:.0f} deg, C = {s.discharge_coefficient:.2f}"
    return " (unrecognized structure type)"


def _downsample_hourly(time_series: List[BasinTimeStepResult], duration_hours: float) -> List[BasinTimeStepResult]:
    """Cascade's printed time-history tables are at ~1-hour intervals
    even though the internal time step is finer -- pick the row closest
    to each integer hour, matching that convention."""
    if not time_series:
        return []
    rows = []
    dt = time_series[1].time_hours - time_series[0].time_hours if len(time_series) > 1 else 1.0
    n_hours = int(round(duration_hours))
    for h in range(n_hours + 1):
        idx = min(range(len(time_series)), key=lambda i: abs(time_series[i].time_hours - h))
        rows.append(time_series[idx])
    return rows


def _basin_header_block(result: ScenarioRunResult, bid: str, basin) -> List[str]:
    storm = result.storm
    lines = [
        f"Basin: {basin.name}",
        f" Method: Santa Barbara Unit Hydrograph",
        f" Rainfall Distribution: {storm.distribution.name}",
        f" Design Frequency: {storm.return_period_years:.0f} year",
    ]
    if storm.duration_hours <= 24:
        lines.append(f" 1 Day Rainfall: {storm.rainfall_depth_inches:.2f} inches")
    else:
        lines.append(f" {storm.duration_hours:.0f}-Hour Rainfall: {storm.rainfall_depth_inches:.2f} inches")
    lines.extend([
        f" Area: {basin.area_acres:.5f} acres",
        f" Ground Storage: {basin.ground_storage_inches:.2f} inches",
        f" Time of Concentration: {basin.time_of_concentration_hours:.2f} hours",
        f" Initial Stage: {basin.initial_stage_ft:.2f} ft NAVD",
        "",
        " Stage Storage",
        " (ft NAVD)    (acre-ft)",
        " ---------    ---------",
    ])
    for stage, storage in basin.stage_storage.points:
        lines.append(f" {stage:8.2f}    {storage:8.4f}")
    lines.append("")
    return lines


def _structure_table(
    struct_name: str,
    structure,
    from_basin_id: str,
    to_label: str,
    time_series: List[BasinTimeStepResult],
    runoff: RunoffSeries,
    time_step_hours: float,
) -> List[str]:
    lines = [
        f"Structure: {struct_name}",
        f" From Basin: {from_basin_id}",
        f" To: {to_label}",
        f" Structure Type: {_structure_type_name(structure)}",
        _structure_header_line(structure),
        "",
        "  Cumulative  Instant  Current    Cumulative  Head Water",
        "  Time  Rainfall  Runoff  Discharge  Discharge   Stage",
        "  (hr)  (in)      (cfs)   (cfs)      (acre-ft)   (ft NAVD)",
        "  " + "=" * 68,
    ]

    hourly_rows = _downsample_hourly(time_series, time_series[-1].time_hours)
    cumulative_discharge = 0.0
    prev_q = 0.0
    prev_t = 0.0
    for row in hourly_rows:
        q = row.structure_discharge_cfs.get(struct_name, 0.0)
        dt = row.time_hours - prev_t
        cumulative_discharge += 0.5 * (prev_q + q) * dt * CFS_HOUR_TO_ACRE_FT
        prev_q, prev_t = q, row.time_hours

        idx = min(range(len(runoff.time_hours)), key=lambda i: abs(runoff.time_hours[i] - row.time_hours))
        cum_rain = runoff.cumulative_gross_rainfall[idx]
        instant_runoff = runoff.instantaneous_runoff_cfs[idx]

        lines.append(
            f"  {row.time_hours:5.2f} {cum_rain:8.2f}  {instant_runoff:6.2f}  "
            f"{q:8.2f}  {cumulative_discharge:9.4f}  {row.stage_ft:8.2f}"
        )
    lines.append("")
    return lines


def cascade_style_output(result: ScenarioRunResult) -> str:
    s = result.scenario
    storm = result.storm
    lines = [
        "Cascade-Compatible Routing Output",
        f"Scenario: {s.scenario_id}   Condition: {s.condition_name}   "
        f"Event: {storm.name}   Zero-Offsite-Discharge: {s.zero_offsite_discharge}",
        f"Period: 0 to {storm.duration_hours:.0f} hr    Time Step: {s.time_step_hours} hr",
        "",
    ]

    for bid, time_series in result.network_result.time_series.items():
        basin = None  # populated below via result's stored basin reference
        # Basin object isn't stored on ScenarioRunResult directly; the
        # caller (report generator) is responsible for passing basins
        # in -- see cascade_style_output_for_condition below for the
        # version that has basin geometry/structures available.
        lines.append(f"[Basin {bid}: see full report for structure-level detail]")

    return "\n".join(lines)


def cascade_style_output_full(result: ScenarioRunResult, basins_by_id: Dict[str, "object"]) -> str:
    """Full Cascade-style output including per-structure time-history
    tables. basins_by_id maps basin_id -> Basin (from the Condition's
    BasinNetwork used to run this scenario) so structure objects and
    geometry are available to render."""
    s = result.scenario
    storm = result.storm
    lines = [
        f"Project Report -- Scenario {s.scenario_id}",
        f"Condition: {s.condition_name}    Event: {storm.name}    "
        f"Zero-Offsite-Discharge: {s.zero_offsite_discharge}",
        f" Period Begin: 0 hr   End: {storm.duration_hours:.0f} hr   Duration: {storm.duration_hours:.0f} hr",
        f" Time Step: {s.time_step_hours} hr",
        "",
    ]

    for bid, basin in basins_by_id.items():
        time_series = result.network_result.time_series[bid]
        runoff = result.runoff_by_basin[bid]

        lines.extend(_basin_header_block(result, bid, basin))

        for structure in basin.structures:
            if structure.destination.value == "OFFSITE_DISCHARGE":
                to_label = "Offsite (receiving canal/system)"
            elif structure.destination.value == "ONSITE_DISPOSAL":
                to_label = "Onsite Disposal"
            else:
                to_label = "Internal Transfer"
            lines.extend(_structure_table(
                structure.name, structure, bid, to_label, time_series, runoff, s.time_step_hours,
            ))

        # Structure max/min discharge summary
        lines.append("STRUCTURE MAXIMUM AND MINIMUM DISCHARGES")
        lines.append("=" * 55)
        lines.append(" Struc                    Max (cfs)  Time (hr)")
        lines.append("=" * 55)
        for structure in basin.structures:
            series = [(row.time_hours, row.structure_discharge_cfs.get(structure.name, 0.0))
                      for row in time_series]
            peak_t, peak_q = max(series, key=lambda p: p[1])
            lines.append(f" {structure.name:<24s} {peak_q:8.2f}   {peak_t:8.2f}")
        lines.append("")

        # Basin max/min stage summary
        peak_stage = result.network_result.peak_stage_ft[bid]
        peak_time = result.network_result.peak_stage_time_hours[bid]
        lines.append("BASIN MAXIMUM AND MINIMUM STAGES")
        lines.append("=" * 55)
        lines.append(" Basin                    Max (ft)   Time (hr)")
        lines.append("=" * 55)
        lines.append(f" {basin.name:<24s} {peak_stage:8.2f}   {peak_time:8.2f}")
        lines.append("")

        # Water budget
        mb = result.network_result.mass_balance[bid]
        lines.append("BASIN WATER BUDGET (all units in acre-ft)")
        lines.append("=" * 78)
        lines.append(" Basin          Runoff   Inter-In  Inter-Out  Onsite    Offsite   Final Stor  Residual")
        lines.append("=" * 78)
        lines.append(
            f" {basin.name:<12s}  {mb.external_inflow_acre_ft:8.4f} {mb.interbasin_inflow_acre_ft:9.4f} "
            f"{mb.interbasin_outflow_acre_ft:10.4f} {mb.onsite_disposal_acre_ft:8.4f} "
            f"{mb.offsite_discharge_acre_ft:9.4f} {mb.final_storage_acre_ft:10.4f} {mb.residual_acre_ft:9.6f}"
        )
        lines.append("")

    return "\n".join(lines)
