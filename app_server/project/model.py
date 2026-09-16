"""
project/model.py

The core data model tying everything else together: one project holds
an existing condition and a proposed condition (each a BasinNetwork),
a regulatory profile, and a set of scenarios. Section 2's architecture
rule applies directly: one engineering model, run against many storm
scenarios, never a separate copy of the site per storm.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from hydraulics.network import BasinNetwork, route_network, BasinNetworkResult
from hydrology.sbuh import generate_sbuh_runoff, RunoffSeries
from hydrology.rainfall import StormEvent
from regulatory.profiles import RegulatoryProfile, RequiredEvent, build_storm_event


@dataclass
class ProjectMetadata:
    project_name: str
    project_address: str = ""
    municipality: str = ""
    county: str = ""
    project_number: str = ""
    client: str = ""
    engineer_name: str = ""
    pe_license_number: str = ""
    engineering_firm: str = ""
    vertical_datum: str = "NAVD 88"
    regulatory_agency: str = ""
    permit_number: str = ""
    report_date: str = ""


@dataclass
class Condition:
    """Either the 'existing' or 'proposed' state of the site, as a
    basin network (a single basin with no links is a valid network)."""
    name: str  # "existing" or "proposed"
    network: BasinNetwork


@dataclass
class Scenario:
    scenario_id: str
    condition_name: str       # must match a key in Project.conditions
    event_code: str           # must match a RequiredEvent.code in the profile
    time_step_hours: float = 0.2
    zero_offsite_discharge: Optional[bool] = None  # None -> inherit from RequiredEvent
    enabled: bool = True
    rainfall_depth_override_inches: Optional[float] = None


@dataclass
class ScenarioRunResult:
    scenario: Scenario
    storm: StormEvent
    network_result: BasinNetworkResult
    runoff_by_basin: Dict[str, "RunoffSeries"] = field(default_factory=dict)


@dataclass
class Project:
    metadata: ProjectMetadata
    regulatory_profile: RegulatoryProfile
    conditions: Dict[str, Condition] = field(default_factory=dict)
    scenarios: List[Scenario] = field(default_factory=list)

    def add_condition(self, condition: Condition) -> None:
        self.conditions[condition.name] = condition

    def default_scenarios_from_profile(self, time_step_hours: float = 0.2) -> None:
        """Populates Section 6's default scenario matrix: every
        default-selected required event, run against both existing and
        proposed conditions (existing typically skipped for events that
        don't need a pre/post comparison, but keeping both is harmless
        and the user can disable scenarios they don't need)."""
        self.scenarios = []
        for event in self.regulatory_profile.selected_by_default():
            for cond_name in self.conditions.keys():
                sid = f"{cond_name.upper()[:2]}-{event.code}"
                zod = event.zero_offsite_discharge if cond_name == "proposed" else False
                self.scenarios.append(Scenario(
                    scenario_id=sid,
                    condition_name=cond_name,
                    event_code=event.code,
                    time_step_hours=time_step_hours,
                    zero_offsite_discharge=zod,
                ))


def run_scenario(project: Project, scenario: Scenario) -> ScenarioRunResult:
    condition = project.conditions[scenario.condition_name]
    required_event = project.regulatory_profile.event_by_code(scenario.event_code)
    storm = build_storm_event(required_event, scenario.rainfall_depth_override_inches)

    zod = scenario.zero_offsite_discharge
    if zod is None:
        zod = required_event.zero_offsite_discharge

    external_inflow: Dict[str, List[float]] = {}
    time_hours: List[float] = []
    runoff_by_basin: Dict[str, RunoffSeries] = {}

    # All basins in a network must share one time axis for route_network
    # to work, but generate_sbuh_runoff's recession phase length depends
    # on each basin's own Tc and decay rate. Force a single shared
    # recession window (driven by the largest Tc in the network) with no
    # early stop, so every basin's series comes out the same length --
    # otherwise two basins with different Tc would silently produce
    # mismatched-length arrays the first time someone used differing Tc
    # values in a multi-basin project.
    shared_max_recession_hours = max(
        max(10.0 * b.time_of_concentration_hours, 6.0)
        for b in condition.network.basins.values()
    )

    for bid, basin in condition.network.basins.items():
        runoff = generate_sbuh_runoff(
            storm=storm,
            area_acres=basin.area_acres,
            ground_storage_inches=basin.ground_storage_inches,
            time_of_concentration_hours=basin.time_of_concentration_hours,
            time_step_hours=scenario.time_step_hours,
            recession_threshold_fraction=0.0,
            max_recession_hours=shared_max_recession_hours,
        )
        external_inflow[bid] = runoff.routed_runoff_cfs
        runoff_by_basin[bid] = runoff
        time_hours = runoff.time_hours  # identical length across basins now guaranteed

    network_result = route_network(
        network=condition.network,
        external_inflow_time_hours=time_hours,
        external_inflow_cfs=external_inflow,
        time_step_hours=scenario.time_step_hours,
        zero_offsite_discharge=zod,
    )
    return ScenarioRunResult(scenario=scenario, storm=storm, network_result=network_result,
                              runoff_by_basin=runoff_by_basin)


def run_all_scenarios(project: Project) -> Dict[str, ScenarioRunResult]:
    """RUN SELECTED SCENARIOS (Section 41). Skips scenarios marked
    enabled=False without touching the ones that are."""
    results: Dict[str, ScenarioRunResult] = {}
    for scenario in project.scenarios:
        if not scenario.enabled:
            continue
        results[scenario.scenario_id] = run_scenario(project, scenario)
    return results


# ---- Existing vs proposed comparison engine (Section 36) -----------------

@dataclass
class ComparisonRow:
    event_code: str
    basin_id: str
    existing_peak_stage_ft: Optional[float]
    proposed_peak_stage_ft: Optional[float]
    difference_ft: Optional[float]
    result: str  # "PASS", "REVIEW REQUIRED", or "N/A"


def compare_existing_vs_proposed(results: Dict[str, ScenarioRunResult]) -> List[ComparisonRow]:
    by_event: Dict[str, Dict[str, ScenarioRunResult]] = {}
    for r in results.values():
        by_event.setdefault(r.scenario.event_code, {})[r.scenario.condition_name] = r

    rows: List[ComparisonRow] = []
    for event_code, by_cond in by_event.items():
        existing = by_cond.get("existing")
        proposed = by_cond.get("proposed")
        if proposed is None:
            continue
        basin_ids = proposed.network_result.peak_stage_ft.keys()
        for bid in basin_ids:
            prop_peak = proposed.network_result.peak_stage_ft.get(bid)
            exist_peak = existing.network_result.peak_stage_ft.get(bid) if existing else None
            if exist_peak is None:
                rows.append(ComparisonRow(event_code, bid, None, prop_peak, None, "N/A"))
                continue
            diff = prop_peak - exist_peak
            result = "PASS" if diff <= 0 else "REVIEW REQUIRED"
            rows.append(ComparisonRow(event_code, bid, exist_peak, prop_peak, diff, result))
    return rows
