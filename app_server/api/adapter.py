"""
api/adapter.py

Translates between the browser app's JSON project shape (metadata,
storms, basins-with-plain-dict-structures) and the real engine objects
(Project, BasinNetwork, Basin, DrainageWell/Orifice/RectangularWeir).

This is the ONLY place that shape-shifting happens. Nothing here
computes anything -- it builds engine objects and hands off to
project.model / qa.validation / reports.*, exactly like the CLI demos
do. That's deliberate: the server must not become a second place that
reimplements calculation logic, or we're right back to the two-engines
problem this whole rebuild was meant to avoid.

Current scope: each condition is a full BasinNetwork -- one or more
basins plus interbasin links (Orifice/Weir connecting two basins),
matching hydraulics/network.py. This is the multi-basin support that
was previously missing from the app (single "SITE" basin only);
Excel/PDF exports that inherently assume one basin (the calc-sheet
style reports) still use the FIRST basin per condition -- see the
docstring on those export functions in server.py for that narrower
limitation.
"""

from __future__ import annotations
from typing import Any, Dict

from core.interpolation import StageStorageCurve
from hydraulics.structures import DrainageWell, Pump, Orifice, RectangularWeir, DestinationClassification
from hydraulics.basin import Basin
from hydraulics.network import BasinNetwork, InterbasinLink
from regulatory.profiles import RegulatoryProfile, RequiredEvent
from project.model import (
    Project, ProjectMetadata, Condition, Scenario,
    run_all_scenarios, compare_existing_vs_proposed,
)
from qa.validation import run_qa, Finding
from reports.generator import ReportOptions


class AdapterError(Exception):
    pass


def _req_float(d: Dict[str, Any], key: str, label: str) -> float:
    """Reads a required numeric field with a clear error message instead
    of a raw Python exception when it's missing or blank -- the app's
    Storage & Water Quality inputs default to blank, so this is the
    normal way a user will first hit a validation error, not an edge
    case."""
    val = d.get(key, "")
    if val in (None, ""):
        raise AdapterError(f"Please enter a value for '{label}'.")
    try:
        return float(val)
    except (TypeError, ValueError):
        raise AdapterError(f"'{label}' must be a number (got '{val}').")


_EVENT_DEFS = [
    # (code, label, purpose, return_period_years, app_storm_key)
    ("5Y-1D", "5-Year / 24-Hour", "Parking lot protection", 5, "y5"),
    ("25Y-3D", "25-Year / 72-Hour", "Attenuation / pre-vs-post", 25, "y25"),
    ("100Y-3D", "100-Year / 72-Hour", "Flood protection, zero off-site discharge", 100, "y100"),
]


def _structure_from_dict(d: Dict[str, Any]):
    kind = d.get("kind")
    name = d.get("name", "Unnamed")
    destination = DestinationClassification(d.get("destination", "OFFSITE_DISCHARGE"))
    enabled = bool(d.get("enabled", True))

    if kind == "well":
        return DrainageWell(
            name=name, capacity_gpm=float(d["capacityGpm"]),
            turn_on_stage=float(d["turnOnStage"]), turn_off_stage=float(d["turnOffStage"]),
            destination=destination, enabled=enabled,
        )
    if kind == "pump":
        return Pump(
            name=name, capacity_cfs=float(d["capacityCfs"]),
            turn_on_stage=float(d["turnOnStage"]), turn_off_stage=float(d["turnOffStage"]),
            destination=destination, enabled=enabled,
        )
    if kind == "orifice":
        return Orifice(
            name=name, invert_elevation_ft=float(d["invertElevationFt"]),
            area_sqft=float(d["areaSqft"]), discharge_coefficient=float(d.get("cd", 0.6)),
            destination=destination, enabled=enabled,
        )
    if kind == "weir":
        return RectangularWeir(
            name=name, crest_elevation_ft=float(d["crestElevationFt"]),
            length_ft=float(d["lengthFt"]), discharge_coefficient=float(d.get("c", 3.1)),
            destination=destination, enabled=enabled,
        )
    raise AdapterError(f"Unknown structure kind '{kind}' for structure '{name}'.")


def _basin_from_dict(d: Dict[str, Any], basin_id: str) -> Basin:
    points = [(float(p[0]), float(p[1])) for p in d["stagePoints"]]
    structures = [_structure_from_dict(s) for s in d.get("structures", [])]
    return Basin(
        basin_id=basin_id, name=d.get("name", basin_id),
        area_acres=float(d["areaAcres"]), ground_storage_inches=float(d["groundStorageIn"]),
        time_of_concentration_hours=float(d["tcHours"]), initial_stage_ft=float(d["initialStageFt"]),
        stage_storage=StageStorageCurve(points=points), structures=structures,
        berm_elevation_ft=float(d["bermElevationFt"]) if d.get("bermElevationFt") not in (None, "") else None,
    )


def _network_from_dict(d: Dict[str, Any]) -> BasinNetwork:
    """d has shape: {"basins": {basinId: basinDict, ...}, "links": [linkDict, ...]}
    (or, for backward compatibility with older single-basin project files,
    the old flat basin-fields shape, in which case it's treated as one
    basin named "SITE" with no links)."""
    if "basins" in d:
        basins = {bid: _basin_from_dict(bd, bid) for bid, bd in d["basins"].items()}
        if not basins:
            raise AdapterError("A condition must have at least one basin.")
        links = []
        for ld in d.get("links", []):
            structure = _structure_from_dict(ld["structure"])
            links.append(InterbasinLink(
                link_id=ld.get("id", f"{ld['fromBasinId']}->{ld['toBasinId']}"),
                from_basin_id=ld["fromBasinId"], to_basin_id=ld["toBasinId"], structure=structure,
            ))
        return BasinNetwork(basins=basins, links=links)
    # legacy single-basin shape
    return BasinNetwork(basins={"SITE": _basin_from_dict(d, "SITE")})


def build_project_from_app_json(data: Dict[str, Any]) -> Project:
    m = data.get("metadata", {})
    metadata = ProjectMetadata(
        project_name=m.get("name", "Untitled Project"),
        project_address=m.get("address", ""),
        project_number=m.get("number", ""),
        client=m.get("client", ""),
        engineer_name=m.get("engineer", ""),
        pe_license_number=m.get("pe", ""),
        engineering_firm=m.get("firm", ""),
        regulatory_agency=m.get("agency", ""),
        report_date=m.get("date", ""),
        fema_community_panel=m.get("femaCommunityPanel", ""),
        fema_flood_zone=m.get("femaFloodZone", ""),
        fema_bfe_navd=m.get("femaBfeNavd", ""),
        broward_flood_criteria_navd=m.get("browardFloodCriteriaNavd", ""),
        design_water_table_source=m.get("waterTableSource", ""),
        system_narrative=m.get("systemNarrative", ""),
    )

    storms = data.get("storms", {})
    required_events = []
    depth_overrides = {}
    for code, label, purpose, return_period, app_key in _EVENT_DEFS:
        app_storm = storms.get(app_key, {})
        duration = float(app_storm.get("dur", 24 if code == "5Y-1D" else 72))
        depth = float(app_storm.get("depth", 0.0))
        required_events.append(RequiredEvent(
            code=code, label=label, purpose=purpose, return_period_years=return_period,
            duration_hours=duration, default_selected=True,
            zero_offsite_discharge=(code == "100Y-3D"),
        ))
        depth_overrides[code] = depth

    profile = RegulatoryProfile(name="Broward County SWM (project-specific)", version="app", required_events=required_events)

    project = Project(metadata=metadata, regulatory_profile=profile)

    basins = data.get("basins", {})
    if "existing" not in basins or "proposed" not in basins:
        raise AdapterError("Project JSON must include both 'existing' and 'proposed' basins.")

    project.add_condition(Condition(name="existing", network=_network_from_dict(basins["existing"])))
    project.add_condition(Condition(name="proposed", network=_network_from_dict(basins["proposed"])))

    scenarios = []
    for event in required_events:
        for cond_name in ("existing", "proposed"):
            sid = f"{'EX' if cond_name == 'existing' else 'PR'}-{event.code}"
            zod = event.zero_offsite_discharge if cond_name == "proposed" else False
            scenarios.append(Scenario(
                scenario_id=sid, condition_name=cond_name, event_code=event.code,
                time_step_hours=0.2, zero_offsite_discharge=zod,
                rainfall_depth_override_inches=depth_overrides[event.code],
            ))
    project.scenarios = scenarios

    return project


def run_project(data: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the project, runs every scenario, runs QA, and returns a
    plain-JSON-serializable results payload for the frontend. Each
    scenario now reports ALL basins in its network (multi-basin
    support), not a single hardcoded basin id."""
    project = build_project_from_app_json(data)
    results = run_all_scenarios(project)

    scenario_payload = []
    for sid, r in sorted(results.items()):
        basin_ids = list(r.network_result.peak_stage_ft.keys())
        condition_basins = project.conditions[r.scenario.condition_name].network.basins
        basins_out = []
        for bid in basin_ids:
            mb = r.network_result.mass_balance[bid]
            peak_stage = r.network_result.peak_stage_ft[bid]
            berm_elevation = condition_basins[bid].berm_elevation_ft
            freeboard_ft = (berm_elevation - peak_stage) if berm_elevation is not None else None
            steps = r.network_result.time_series[bid]
            basins_out.append({
                "basinId": bid,
                "peakStageFt": peak_stage,
                "peakStageTimeHours": r.network_result.peak_stage_time_hours[bid],
                "bermElevationFt": berm_elevation,
                "freeboardFt": freeboard_ft,
                # Full time series for the hydrograph visualization (#7):
                # inflow (basin runoff), the two ways flow leaves the
                # basin (offsite discharge, onsite disposal), and stage,
                # all against the same time axis -- exactly what
                # hydraulics/network.py already computes and stores per
                # step, just not previously exposed over the API.
                "timeSeries": {
                    "tHours": [s.time_hours for s in steps],
                    "stageFt": [s.stage_ft for s in steps],
                    "inflowCfs": [s.external_inflow_cfs for s in steps],
                    "offsiteCfs": [s.local_offsite_discharge_cfs for s in steps],
                    "onsiteCfs": [s.local_onsite_disposal_cfs for s in steps],
                    "interbasinInflowCfs": [s.interbasin_inflow_cfs for s in steps],
                    "interbasinOutflowCfs": [s.interbasin_outflow_cfs for s in steps],
                },
                "massBalance": {
                    "externalInflowAF": mb.external_inflow_acre_ft,
                    "interbasinInflowAF": mb.interbasin_inflow_acre_ft,
                    "interbasinOutflowAF": mb.interbasin_outflow_acre_ft,
                    "onsiteAF": mb.onsite_disposal_acre_ft,
                    "offsiteAF": mb.offsite_discharge_acre_ft,
                    "finalStorage": mb.final_storage_acre_ft,
                    "residualAF": mb.residual_acre_ft,
                    "residualPct": mb.residual_pct_of_inflow,
                },
            })
        scenario_payload.append({
            "scenarioId": sid,
            "condition": r.scenario.condition_name,
            "event": r.scenario.event_code,
            "eventLabel": r.storm.name,
            "zod": r.scenario.zero_offsite_discharge,
            "networkTotalOffsiteAF": r.network_result.network_total_offsite_discharge_acre_ft,
            "basins": basins_out,
        })

    comparison_payload = [
        {
            "event": row.event_code, "basin": row.basin_id,
            "existingFt": row.existing_peak_stage_ft, "proposedFt": row.proposed_peak_stage_ft,
            "diffFt": row.difference_ft, "result": row.result,
        }
        for row in compare_existing_vs_proposed(results)
    ]

    findings = run_qa(project, results)
    findings_payload = [{"level": f.level.value, "message": f.message, "objectId": f.object_id} for f in findings]

    return {"scenarios": scenario_payload, "comparison": comparison_payload, "findings": findings_payload}


def build_report_options(opts: Dict[str, Any]) -> ReportOptions:
    return ReportOptions(
        include_cover=bool(opts.get("cover", True)),
        include_permit_criteria=bool(opts.get("criteria", True)),
        include_final_summary_table=bool(opts.get("summary", True)),
        include_comparison_table=bool(opts.get("comparison", True)),
        include_qa_summary=bool(opts.get("qa", True)),
        include_design_narrative=bool(opts.get("designNarrative", False)),
        cascade_detail_scenario_ids=set() if not opts.get("detail", False) else None,
    )


# ---------------------------------------------------------------------------
# Storage & Water Quality calculators (swale, exfiltration, SCS runoff
# volume, legacy volumetric water quality) -- self-contained calculators
# that don't need a full Project/scenario run, so they get their own
# lightweight adapter functions rather than routing through Project.
# ---------------------------------------------------------------------------

from storage.runoff_volume import compute_runoff_volume
from storage.swale import Swale, make_area_based_swale, merge_swales_into_basin_curve
from storage.exfiltration import ExfiltrationTrench, H2Method
from storage.water_quality import LegacyVolumetricInputs, calculate_legacy_volumetric, calculate_custom
from core.units import CUFT_TO_ACRE_IN


def _swale_from_dict(d: Dict[str, Any]) -> Swale:
    method = d.get("method", "cross_section")

    if method == "trapezoidal_area":
        return make_area_based_swale(
            name=d.get("name", "Swale"),
            bottom_elevation_ft=_req_float(d, "bottomElevationFt", "Swale Bottom Elevation (ft)"),
            top_elevation_ft=_req_float(d, "topElevationFt", "Swale Top Elevation (ft)"),
            top_area_sqft=_req_float(d, "topAreaSqft", "Swale Top Area (SF)"),
            bottom_area_sqft=_req_float(d, "bottomAreaSqft", "Swale Bottom Area (SF)"),
            include_in_basin_storage=bool(d.get("includeInBasinStorage", True)),
        )
    if method == "triangular_area":
        return make_area_based_swale(
            name=d.get("name", "Swale"),
            bottom_elevation_ft=_req_float(d, "bottomElevationFt", "Swale Bottom Elevation (ft)"),
            top_elevation_ft=_req_float(d, "topElevationFt", "Swale Top Elevation (ft)"),
            top_area_sqft=_req_float(d, "topAreaSqft", "Swale Top Area (SF)"),
            bottom_area_sqft=0.0,
            include_in_basin_storage=bool(d.get("includeInBasinStorage", True)),
        )

    # default: cross-section (bottom width + side slopes) method
    kwargs = dict(
        name=d.get("name", "Swale"),
        bottom_elevation_ft=_req_float(d, "bottomElevationFt", "Swale Bottom Elevation (ft)"),
        top_elevation_ft=_req_float(d, "topElevationFt", "Swale Top Elevation (ft)"),
        length_ft=_req_float(d, "lengthFt", "Swale Length (ft)"),
        include_in_basin_storage=bool(d.get("includeInBasinStorage", True)),
    )
    if d.get("irregular"):
        kwargs["irregular_depth_area"] = [(float(p[0]), float(p[1])) for p in d["irregular"]]
    else:
        kwargs["bottom_width_ft"] = float(d.get("bottomWidthFt", 0.0))
        kwargs["left_side_slope_h_per_v"] = float(d.get("leftSlope", 3.0))
        kwargs["right_side_slope_h_per_v"] = float(d.get("rightSlope", 3.0))
    return Swale(**kwargs)


def _exfiltration_from_dict(d: Dict[str, Any]) -> ExfiltrationTrench:
    method = H2Method(d.get("h2Method", "SFWMD_STANDARD"))
    return ExfiltrationTrench(
        name=d.get("name", "Exfiltration Trench"),
        control_elevation_ft=_req_float(d, "controlElevationFt", "Control Elevation (ft)"),
        design_water_table_ft=_req_float(d, "designWaterTableFt", "Design Water Table (ft)"),
        trench_top_elevation_ft=_req_float(d, "trenchTopElevationFt", "Trench Top Elevation (ft)"),
        trench_bottom_elevation_ft=_req_float(d, "trenchBottomElevationFt", "Trench Bottom Elevation (ft)"),
        pipe_invert_elevation_ft=_req_float(d, "pipeInvertElevationFt", "Pipe Invert Elevation (ft)"),
        lowest_overflow_elevation_ft=_req_float(d, "lowestOverflowElevationFt", "Lowest Overflow Elevation (ft)"),
        design_water_surface_ft=_req_float(d, "designWaterSurfaceFt", "Design Water Surface (ft)"),
        trench_width_ft=_req_float(d, "trenchWidthFt", "Trench Width, W (ft)"),
        actual_trench_length_ft=_req_float(d, "actualTrenchLengthFt", "Provided Trench Length (ft)"),
        hydraulic_conductivity_k=_req_float(d, "hydraulicConductivityK", "K (cfs/ft²-ft)"),
        pipe_diameter_in=float(d["pipeDiameterIn"]) if d.get("pipeDiameterIn") not in (None, "") else None,
        factor_of_safety=float(d.get("factorOfSafety", 2.0)),
        h2_method=method,
        user_defined_h2_ft=float(d["userDefinedH2Ft"]) if d.get("userDefinedH2Ft") not in (None, "") else None,
        effective_head_ft=float(d["effectiveHeadFt"]) if d.get("effectiveHeadFt") not in (None, "") else None,
        percent_wq_required=float(d.get("percentWqRequired", 1.0)),
    )


def _blank(v) -> bool:
    return v is None or v == ""


def build_storage_objects(data: Dict[str, Any]):
    """Builds the real engine objects (not JSON) from the same
    storageWQ payload shape -- shared by the JSON calculator endpoint
    and the Excel/PDF export endpoints so there's exactly one place
    that interprets this payload.

    Each section (site areas/soil storage, swales, water quality,
    exfiltration) is independent: if a section's key fields are left
    blank, it's skipped entirely rather than failing the whole
    calculation. Only a PARTIALLY filled-in section (some fields
    entered, others left blank) raises a clear error -- that's the
    difference between "I haven't gotten to this section yet" and
    "I'm missing something in the section I'm working on."."""
    sa = data.get("siteAreas", {})
    ss = data.get("soilStorage", {})
    existing_runoff = proposed_runoff = None
    if not _blank(ss.get("rainfallIn")):
        existing_runoff = compute_runoff_volume(
            _req_float(sa, "existingSiteSqft", "Existing Site Area (SF)"),
            _req_float(sa, "existingPerviousSqft", "Existing Pervious Area (SF)"),
            _req_float(ss, "rainfallIn", "Rainfall, P (in)"),
            _req_float(ss, "existingCompactedIn", "Existing Compacted Soil Storage (in)"),
        )
        proposed_runoff = compute_runoff_volume(
            _req_float(sa, "proposedSiteSqft", "Proposed Site Area (SF)"),
            _req_float(sa, "proposedPerviousSqft", "Proposed Pervious Area (SF)"),
            _req_float(ss, "rainfallIn", "Rainfall, P (in)"),
            _req_float(ss, "proposedCompactedIn", "Proposed Compacted Soil Storage (in)"),
        )

    swales = [_swale_from_dict(s) for s in data.get("swales", [])]
    total_swale_cuft = sum(sw.summary()["max_storage_cuft"] for sw in swales if sw.include_in_basin_storage)

    wq_result = None
    wq_required_cuft = None
    wq_data = data.get("waterQuality")
    wq_active = wq_data and (
        (wq_data.get("method") == "custom" and not _blank(wq_data.get("customRequiredVolumeAcIn")))
        or (wq_data.get("method") != "custom" and not _blank(wq_data.get("projectAreaAcres")))
    )
    if wq_active:
        if wq_data.get("method") == "custom":
            wq_result = calculate_custom(
                _req_float(wq_data, "customRequiredVolumeAcIn", "Custom Required Volume (ac-in)"),
                float(wq_data.get("providedVolumeAcIn", 0.0) or 0.0),
            )
        else:
            inputs = LegacyVolumetricInputs(
                project_area_acres=_req_float(wq_data, "projectAreaAcres", "Project Area (acres)"),
                impervious_fraction=_req_float(wq_data, "imperviousFraction", "Impervious Fraction"),
                dry_detention_credit_ac_in=float(wq_data.get("dryDetentionCreditAcIn", 0.0) or 0.0),
                retention_credit_ac_in=float(wq_data.get("retentionCreditAcIn", 0.0) or 0.0),
                pretreatment_volume_ac_in=float(wq_data.get("pretreatmentVolumeAcIn", 0.0) or 0.0),
            )
            wq_result = calculate_legacy_volumetric(inputs, float(wq_data.get("providedVolumeAcIn", 0.0) or 0.0))
        wq_required_cuft = wq_result.net_required_volume_ac_in * 3630.0

    trench = None
    required_for_trench_cuft = 0.0
    basis_label = "Proposed Runoff Volume"
    basis_volume_before_credit = 0.0
    exf_data = data.get("exfiltration")
    if exf_data and not _blank(exf_data.get("controlElevationFt")):
        trench = _exfiltration_from_dict(exf_data)
        basis = exf_data.get("requiredVolumeBasis", "waterQuality")
        if basis == "runoffVolume":
            if proposed_runoff is None:
                raise AdapterError(
                    "Exfiltration trench's Required Volume Basis is set to 'Proposed runoff volume', "
                    "but the Site Areas / Soil Storage section is empty -- there's no runoff volume to "
                    "use. Fill in Site Areas and Soil Storage, or change Required Volume Basis to "
                    "'Water quality required volume'."
                )
            basis_label = "Proposed Runoff Volume"
            base_cuft = proposed_runoff.runoff_volume_cuft
        else:
            if wq_required_cuft is None:
                raise AdapterError(
                    "Exfiltration trench's Required Volume Basis is set to 'Water quality required "
                    "volume', but the Water Quality section is empty -- there's no required volume to "
                    "use (this previously silently defaulted to 0, producing a false PASS -- now it "
                    "stops and tells you instead). Fill in Water Quality, or change Required Volume "
                    "Basis to 'Proposed runoff volume'."
                )
            basis_label = "Water Quality Required Volume"
            base_cuft = wq_required_cuft
        basis_volume_before_credit = base_cuft
        required_for_trench_cuft = max(base_cuft - total_swale_cuft, 0.0)

    return {
        "existing_runoff": existing_runoff, "proposed_runoff": proposed_runoff,
        "swales": swales, "total_swale_cuft": total_swale_cuft,
        "wq_result": wq_result, "trench": trench,
        "required_for_trench_cuft": required_for_trench_cuft,
        "required_volume_basis_label": basis_label,
        "required_volume_before_swale_credit_cuft": basis_volume_before_credit,
    }


def run_storage_calcs(data: Dict[str, Any]) -> Dict[str, Any]:
    """Runs the SCS runoff-volume, swale, water-quality, and exfiltration
    calculators together, matching the flow a real drainage calc sheet
    follows: runoff volume -> credited swale storage -> remaining
    volume routed to the exfiltration trench. Returns JSON-safe output."""
    objs = build_storage_objects(data)
    out: Dict[str, Any] = {}

    if objs["existing_runoff"] is not None:
        out["runoffVolume"] = {
            "existing": vars(objs["existing_runoff"]), "proposed": vars(objs["proposed_runoff"]),
            "netIncreaseCuft": objs["proposed_runoff"].runoff_volume_cuft - objs["existing_runoff"].runoff_volume_cuft,
        }

    if objs["swales"]:
        out["swales"] = [sw.summary() for sw in objs["swales"]]
        out["totalSwaleStorageCuft"] = objs["total_swale_cuft"]

    if objs["wq_result"] is not None:
        r = objs["wq_result"]
        out["waterQuality"] = {k: (v.value if hasattr(v, "value") else v) for k, v in vars(r).items()}

    if objs["trench"] is not None:
        report = objs["trench"].report(objs["required_for_trench_cuft"])
        report["requiredVolumeAfterSwaleCreditCuft"] = objs["required_for_trench_cuft"]
        report["requiredVolumeBasisLabel"] = objs["required_volume_basis_label"]
        report["requiredVolumeBeforeSwaleCreditCuft"] = objs["required_volume_before_swale_credit_cuft"]

        # Equivalent-rainfall-reduction credit: converts the trench's
        # treatment volume to an equivalent depth of rainfall over the
        # PROPOSED site area (ac-in of volume / site acres = inches).
        # This is the same conversion the firm's own submitted, permitted
        # reports use to document "exfiltration trench storage has been
        # converted to an amount of rainfall in inches to be subtracted
        # from the design storm events in the Proposed condition" --
        # confirmed against a real Broward SWM submittal. It is reported
        # here as an informational value only: this software does not
        # automatically subtract it from any storm's rainfall depth,
        # since which storms it's applied to (and whether at all) is an
        # engineering/permitting judgment call the engineer of record
        # makes per project, the same way it would be hand-applied in
        # Cascade.
        sa = data.get("siteAreas", {}) or {}
        proposed_sqft = sa.get("proposedSiteSqft")
        if not _blank(proposed_sqft):
            site_acres = float(proposed_sqft) / 43560.0
            if site_acres > 0:
                credit_ac_in = objs["required_for_trench_cuft"] * CUFT_TO_ACRE_IN
                report["equivalentRainfallReductionIn"] = credit_ac_in / site_acres
                report["equivalentRainfallReductionAcIn"] = credit_ac_in

        out["exfiltration"] = report

    return out


def build_narrative_context(data: Dict[str, Any]) -> Dict[str, Any]:
    """A handful of storage/water-quality numbers the optional design-
    narrative report sections (reports/generator.py's
    design_narrative_intro_sections / design_narrative_conclusion_section)
    fold into boilerplate text. Reuses build_storage_objects so these
    numbers can never drift from what the Storage & Water Quality tab
    itself shows -- this function only re-packages already-computed
    results, never recalculates (same rule as the report renderers)."""
    ctx: Dict[str, Any] = {}
    if not data:
        return ctx

    objs = build_storage_objects(data)

    sa = data.get("siteAreas", {}) or {}
    proposed_sqft = sa.get("proposedSiteSqft")
    site_acres = float(proposed_sqft) / 43560.0 if not _blank(proposed_sqft) else None

    if objs["trench"] is not None:
        report = objs["trench"].report(objs["required_for_trench_cuft"])
        ctx["wqProvidedAcFt"] = report["provided_capacity_ac_ft"]
        ctx["wqProvidedSource"] = "Exfiltration Trench(es)"
        ctx["exfiltrationTrenchLengthProvidedFt"] = report["provided_length_ft"]
        ctx["exfiltrationTrenchStatus"] = report["status"]
        if site_acres:
            credit_ac_in = objs["required_for_trench_cuft"] * CUFT_TO_ACRE_IN
            ctx["equivalentRainfallReductionIn"] = credit_ac_in / site_acres
    elif objs["wq_result"] is not None:
        ctx["wqProvidedAcFt"] = objs["wq_result"].provided_volume_ac_in / 12.0
        ctx["wqProvidedSource"] = "on-site storage"

    if objs["wq_result"] is not None:
        ctx["wqRequiredAcFt"] = objs["wq_result"].net_required_volume_ac_in / 12.0

    if site_acres:
        ctx["pretreatmentRequiredAcFt"] = 0.5 / 12.0 * site_acres
        if "wqProvidedAcFt" in ctx:
            ctx["pretreatmentProvidedAcFt"] = ctx["wqProvidedAcFt"]

    if objs.get("proposed_runoff") is not None:
        ctx["soilStorageProposedIn"] = objs["proposed_runoff"].effective_storage_inches
    if objs.get("existing_runoff") is not None:
        ctx["soilStorageExistingIn"] = objs["existing_runoff"].effective_storage_inches

    return ctx


def suggest_trench_options(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Future-improvement #4 ("Trench size auto suggestion"): holds every
    exfiltration input fixed except width, tries a range of widths, and
    reports the required length and rock volume at each -- so a
    width/length combination can be chosen that meets the required
    treatment volume with the least rock fill. Reuses build_storage_objects
    so this sees exactly the same required-volume-after-swale-credit
    figure the main Calculate button uses (one place that interprets
    the storageWQ payload, per this module's own rule).
    """
    objs = build_storage_objects(data)
    trench = objs["trench"]
    if trench is None:
        raise AdapterError(
            "Fill in the Exfiltration Trench section (elevations, width, K, ...) before requesting "
            "width/length suggestions -- the suggestion holds those inputs fixed and only varies width."
        )

    exf_data = data.get("exfiltration", {}) or {}
    width_options = exf_data.get("suggestWidthOptionsFt")
    if width_options:
        width_options = [float(w) for w in width_options]
    else:
        min_w = float(exf_data.get("suggestMinWidthFt", 2.0) or 2.0)
        max_w = float(exf_data.get("suggestMaxWidthFt", 12.0) or 12.0)
        step = float(exf_data.get("suggestWidthStepFt", 0.5) or 0.5)
        if min_w <= 0 or step <= 0 or max_w < min_w:
            raise AdapterError("Suggestion width range must have min > 0, step > 0, and max >= min.")
        width_options = []
        w = min_w
        while w <= max_w + 1e-9:
            width_options.append(round(w, 4))
            w += step

    available_length = exf_data.get("suggestAvailableLengthFt")
    available_length = float(available_length) if available_length not in (None, "") else None

    rows = trench.suggest_trench_widths(
        objs["required_for_trench_cuft"],
        width_options_ft=width_options,
        available_length_ft=available_length,
    )
    return {
        "options": rows,
        "requiredVolumeCuft": objs["required_for_trench_cuft"],
        "requiredVolumeBasisLabel": objs["required_volume_basis_label"],
        "availableLengthFt": available_length,
    }


def suggest_pond_options(data: Dict[str, Any]) -> Dict[str, Any]:
    """Future-improvement #4's pond counterpart -- a quick footprint
    estimate (prismoidal formula, storage/pond.py) for a target storage
    volume, depth, and side slope. A planning aid, not a stage-storage
    curve; the chosen footprint should still be modeled as a real basin
    for the routing engine to see it."""
    from storage.pond import suggest_pond_footprint, PondSizingError

    try:
        result = suggest_pond_footprint(
            required_volume_cuft=_req_float(data, "requiredVolumeCuft", "Required Volume (CF)"),
            depth_ft=_req_float(data, "depthFt", "Depth (ft)"),
            side_slope_h_per_v=float(data.get("sideSlopeHPerV", 4.0) or 4.0),
            length_to_width_ratio=float(data.get("lengthToWidthRatio", 1.5) or 1.5),
        )
    except PondSizingError as e:
        raise AdapterError(str(e))
    return result.as_dict()


def merge_swale_storage_into_basin(basin_stage_points: list, swales_data: list) -> list:
    """
    Merges the currently-defined swales' storage into an existing
    basin's stage-storage curve (Section 17's "TOTAL STORAGE" concept:
    every contributing component reflected without duplication).

    This directly answers "does this site even need a well" -- a basin
    curve that already accounts for its swale storage may turn out to
    have enough capacity on its own, which was invisible before this
    existed since swale/exfiltration storage never touched the routing
    model's basin curve.

    Returns the merged [[stage, storage], ...] points, ready to
    replace the basin's stagePoints in the app -- this function does
    not mutate anything itself, so the caller decides whether/how to
    apply it (kept explicit rather than automatic, per the "never hide
    a calculation" principle already used for H2).
    """
    base_curve = StageStorageCurve(points=[(float(p[0]), float(p[1])) for p in basin_stage_points])
    swales = [_swale_from_dict(s) for s in swales_data]
    if not swales:
        raise AdapterError("No swales are defined to merge -- add at least one swale first.")
    merged = merge_swales_into_basin_curve(base_curve, swales)
    return [[round(s, 4), round(v, 6)] for s, v in merged.points]
