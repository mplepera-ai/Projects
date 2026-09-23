"""
api/dewatering_adapter.py

Translates between the browser app's dewatering JSON shape (aquifer
params, a list of zones, optional permit thresholds, optional settling
tank inputs) and the dewatering calculation engine
(dewatering/calculations.py). Same division of labor as api/adapter.py:
this file only shape-shifts; it never computes anything itself.
"""

from __future__ import annotations
from typing import Any, Dict

from dewatering.calculations import (
    AquiferParams, AquiferType, Zone, compute_zone, check_zone_overlap,
    summarize_water_balance, PermitThresholds, screen_permit_thresholds,
    TankInputs, design_settling_tank, MinTankInputs, size_minimum_settling_tank,
    SelectedTankSystem, check_selected_tank_system,
)
from storage.exfiltration import ExfiltrationTrench, H2Method, ExfiltrationError


class DewateringAdapterError(Exception):
    pass


def _req_float(d: Dict[str, Any], key: str, label: str) -> float:
    val = d.get(key, "")
    if val in (None, ""):
        raise DewateringAdapterError(f"Please enter a value for '{label}'.")
    try:
        return float(val)
    except (TypeError, ValueError):
        raise DewateringAdapterError(f"'{label}' must be a number (got '{val}').")


def _opt_float(d: Dict[str, Any], key: str):
    val = d.get(key, "")
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        raise DewateringAdapterError(f"'{key}' must be a number (got '{val}').")


def build_aquifer(data: Dict[str, Any]) -> AquiferParams:
    aquifer_type = AquiferType(data.get("aquiferType", "unconfined"))
    return AquiferParams(
        hydraulic_conductivity_ft_day=_req_float(data, "kFtDay", "Hydraulic Conductivity (K)"),
        aquifer_thickness_ft=_req_float(data, "aquiferThicknessFt", "Aquifer Thickness"),
        aquifer_type=aquifer_type,
    )


def build_zones(zones_data: list) -> list:
    zones = []
    for i, z in enumerate(zones_data):
        label = z.get("name") or f"Zone {i + 1}"
        zones.append(Zone(
            name=label,
            description=z.get("description", ""),
            groundwater_elev_ft=_req_float(z, "gwElevFt", f"{label}: Groundwater Elevation"),
            excavation_bottom_elev_ft=_req_float(z, "excBottomElevFt", f"{label}: Excavation Bottom Elevation"),
            operating_margin_ft=_req_float(z, "operatingMarginFt", f"{label}: Operating Margin"),
            width_ft=_req_float(z, "widthFt", f"{label}: Width"),
            length_ft=_req_float(z, "lengthFt", f"{label}: Length"),
            hours_per_day=_req_float(z, "hoursPerDay", f"{label}: Hours Pumping/Day"),
            estimated_days=_req_float(z, "estimatedDays", f"{label}: Estimated Days"),
            number_of_elements=int(_req_float(z, "numberOfElements", f"{label}: Number of Elements")),
        ))
    return zones


def build_receiving_checks(receiving_data: list, design_flow_gpm: float) -> list:
    """
    Optional "does the discharge point have capacity" check (permit
    denial item (a)): compares the dewatering design flow against the
    steady-state hydraulic capacity of each receiving component. Only
    exfiltration-trench components are supported so far -- that
    capacity (exfiltration_capacity_gpm) is a function of trench
    geometry/K/head ONLY, independent of the property's rainfall/
    water-quality treatment volume, so it can be checked against a
    continuous dewatering discharge exactly as-is, reusing the same
    ExfiltrationTrench engine object StormRoute uses for WQ sizing.
    Swale components are not yet supported here (Swale only models
    static storage volume, not a flow-through/percolation rate) --
    entries with type "swale" are skipped with a note rather than
    silently given a fabricated number.
    """
    checks = []
    for i, rc in enumerate(receiving_data or []):
        rc_type = rc.get("type", "exfiltrationTrench")
        label = rc.get("name") or f"Receiving System {i + 1}"
        if rc_type == "exfiltrationTrench":
            try:
                trench = ExfiltrationTrench(
                    name=label,
                    control_elevation_ft=_req_float(rc, "controlElevationFt", f"{label}: Control Elevation"),
                    design_water_table_ft=_req_float(rc, "designWaterTableFt", f"{label}: Design Water Table"),
                    trench_top_elevation_ft=_req_float(rc, "trenchTopElevationFt", f"{label}: Trench Top Elevation"),
                    trench_bottom_elevation_ft=_req_float(rc, "trenchBottomElevationFt", f"{label}: Trench Bottom Elevation"),
                    pipe_invert_elevation_ft=_req_float(rc, "pipeInvertElevationFt", f"{label}: Pipe Invert Elevation"),
                    lowest_overflow_elevation_ft=_req_float(rc, "lowestOverflowElevationFt", f"{label}: Lowest Overflow Elevation"),
                    design_water_surface_ft=_req_float(rc, "designWaterSurfaceFt", f"{label}: Design Water Surface"),
                    trench_width_ft=_req_float(rc, "trenchWidthFt", f"{label}: Trench Width"),
                    actual_trench_length_ft=_req_float(rc, "actualTrenchLengthFt", f"{label}: Trench Length"),
                    hydraulic_conductivity_k=_req_float(rc, "hydraulicConductivityK", f"{label}: Hydraulic Conductivity (K)"),
                    factor_of_safety=_opt_float(rc, "factorOfSafety") or 2.0,
                    h2_method=H2Method(rc.get("h2Method", "SFWMD_STANDARD")),
                    user_defined_h2_ft=_opt_float(rc, "userDefinedH2Ft"),
                    effective_head_ft=_opt_float(rc, "effectiveHeadFt"),
                    percent_wq_required=1.0,
                )
                capacity_gpm = trench.exfiltration_capacity_gpm()
                status = "PASS" if capacity_gpm >= design_flow_gpm else "FAIL"
                notes = rc.get("notes", "")
                warnings = trench.validity_warnings()
                if warnings:
                    notes = (notes + " " if notes else "") + "; ".join(warnings)
                checks.append({"name": label, "capacity_gpm": capacity_gpm, "status": status, "notes": notes})
            except (ExfiltrationError, DewateringAdapterError) as e:
                checks.append({"name": label, "capacity_gpm": 0.0, "status": "ERROR", "notes": str(e)})
        elif rc_type == "swale":
            checks.append({
                "name": label, "capacity_gpm": 0.0, "status": "NOT EVALUATED",
                "notes": "Swale flow-through/percolation capacity is not modeled -- the existing swale "
                         "calculator only computes static storage volume, not a steady discharge rate. "
                         "Evaluate manually or specify a percolation rate before relying on this component.",
            })
        else:
            checks.append({
                "name": label, "capacity_gpm": 0.0, "status": "NOT EVALUATED",
                "notes": f"Receiving system type '{rc_type}' is not modeled.",
            })
    return checks


def _run_core(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Shared computation used by both run_dewatering() (JSON for the browser)
    and build_report_objects() (raw dataclasses for the backup-formulas PDF)
    -- keeps a single source of truth for how the payload is interpreted so
    the report can never show different numbers than the app does.

    data = {
      aquifer: {kFtDay, aquiferThicknessFt, aquiferType},
      zones: [{name, description, gwElevFt, excBottomElevFt, operatingMarginFt,
                widthFt, lengthFt, hoursPerDay, estimatedDays, numberOfElements}, ...],
      permitThresholds: {maxAverageGpm?, maxDailyMgd?, maxDurationDays?},
      tank: {mode: "manual"|"auto", lengthFt, widthFt, depthFt, flowGpmOverride?,
             particleDiameterFt?, specificGravity?,
             autoDepthFt?, autoLengthToWidthRatio?, autoSizeIncrementFt?,
             autoIncreaseDepthForScour?}
    }
    Returns raw objects: aquifer, zone_results, zone_errors, overlap_notes,
    summary, permit_flags, tank_mode, tank_inputs, tank_result, min_tank_result.
    """
    aquifer = build_aquifer(data.get("aquifer", {}))
    zones = build_zones(data.get("zones", []))

    if not zones:
        raise DewateringAdapterError("Add at least one excavation zone before running the calculation.")

    zone_results = []
    zone_errors = []
    for z in zones:
        try:
            zone_results.append(compute_zone(z, aquifer))
        except ValueError as e:
            zone_errors.append(str(e))

    overlap_notes = check_zone_overlap(zones, zone_results) if zone_results else []
    summary = summarize_water_balance(zone_results) if zone_results else None

    pt = data.get("permitThresholds", {}) or {}
    thresholds = PermitThresholds(
        max_average_gpm=_opt_float(pt, "maxAverageGpm"),
        max_daily_mgd=_opt_float(pt, "maxDailyMgd"),
        max_duration_days=_opt_float(pt, "maxDurationDays"),
    )
    # None (not an empty list) means "nothing configured" -- the report
    # omits the screening section entirely rather than printing an
    # ambiguous "nothing exceeded (or nothing configured)" statement.
    permit_screening = screen_permit_thresholds(summary, thresholds) if summary else None

    tank_data = data.get("tank")
    tank_mode = (tank_data or {}).get("mode", "manual")
    tank_inputs = None
    tank_result = None
    min_result = None
    selected_check = None

    if tank_data:
        flow_override = _opt_float(tank_data, "flowGpmOverride")
        flow_gpm = flow_override if flow_override is not None else (summary.max_flow_gpm if summary else 0.0)
        particle_diameter_ft = _opt_float(tank_data, "particleDiameterFt") or 0.000279
        specific_gravity = _opt_float(tank_data, "specificGravity") or 2.65
        freeboard_frac = _opt_float(tank_data, "freeboardFraction") or 0.0

        # The calculated MINIMUM is always computed when a flow exists,
        # in both modes -- this is what "required" means throughout the
        # report, whether the tank actually being used is that exact
        # size (auto mode) or a separately entered/selected one (manual
        # mode, or an explicit "selected system" override in auto mode).
        if flow_gpm:
            min_inputs = MinTankInputs(
                flow_gpm=flow_gpm,
                depth_ft=_opt_float(tank_data, "autoDepthFt") or 4.0,
                length_to_width_ratio=_opt_float(tank_data, "autoLengthToWidthRatio") or 2.0,
                size_increment_ft=_opt_float(tank_data, "autoSizeIncrementFt") or 1.0,
                auto_increase_depth_for_scour=bool(tank_data.get("autoIncreaseDepthForScour", True)),
                particle_diameter_ft=particle_diameter_ft,
                specific_gravity=specific_gravity,
                freeboard_and_sediment_fraction=freeboard_frac,
            )
            min_result = size_minimum_settling_tank(min_inputs)

        if tank_mode == "auto" and min_result is not None:
            tank_result = min_result.tank
            tank_inputs = TankInputs(
                flow_gpm=flow_gpm, length_ft=min_result.length_ft,
                width_ft=min_result.width_ft, depth_ft=min_result.depth_ft,
                particle_diameter_ft=particle_diameter_ft, specific_gravity=specific_gravity,
                freeboard_and_sediment_fraction=freeboard_frac,
            )
        elif tank_data.get("lengthFt") not in (None, ""):
            tank_inputs = TankInputs(
                flow_gpm=flow_gpm,
                length_ft=_req_float(tank_data, "lengthFt", "Tank Length"),
                width_ft=_req_float(tank_data, "widthFt", "Tank Width"),
                depth_ft=_req_float(tank_data, "depthFt", "Tank Depth"),
                particle_diameter_ft=particle_diameter_ft, specific_gravity=specific_gravity,
                freeboard_and_sediment_fraction=freeboard_frac,
            )
            tank_result = design_settling_tank(tank_inputs)

        # Optional selected/provided system (e.g. a stock 25 CY tank),
        # checked against whichever "required" volume was calculated
        # above -- kept distinct from the calculated-minimum tank/dims
        # throughout the report (audit items 6 and 9).
        selected_data = tank_data.get("selectedSystem")
        if selected_data and selected_data.get("providedVolumeGal") not in (None, "") and min_result is not None:
            selected = SelectedTankSystem(
                name=selected_data.get("name") or "Selected System",
                provided_volume_gal=_req_float(selected_data, "providedVolumeGal", "Selected System Volume"),
                notes=selected_data.get("notes", ""),
            )
            selected_check = check_selected_tank_system(
                selected, flow_gpm, required_volume_gal=min_result.tank.effective_volume_gal,
            )

    receiving_checks = build_receiving_checks(
        data.get("receivingSystems", []), summary.max_flow_gpm if summary else 0.0,
    )

    return {
        "aquifer": aquifer,
        "zone_results": zone_results,
        "zone_errors": zone_errors,
        "overlap_notes": overlap_notes,
        "summary": summary,
        "permit_screening": permit_screening,
        "tank_mode": tank_mode,
        "tank_inputs": tank_inputs,
        "tank_result": tank_result,
        "min_result": min_result,
        "selected_check": selected_check,
        "receiving_checks": receiving_checks,
    }


def _tank_result_json(tank_result) -> Dict[str, Any]:
    return {
        "volumeCf": tank_result.volume_cf,
        "volumeGal": tank_result.volume_gal,
        "detentionTimeMin": tank_result.detention_time_min,
        "effectiveDepthFt": tank_result.effective_depth_ft,
        "effectiveVolumeCf": tank_result.effective_volume_cf,
        "effectiveVolumeGal": tank_result.effective_volume_gal,
        "effectiveDetentionTimeMin": tank_result.effective_detention_time_min,
        "meanHorizontalVelocityFtS": tank_result.mean_horizontal_velocity_ft_s,
        "settlingVelocityFtS": tank_result.settling_velocity_ft_s,
        "surfaceOverflowRateGpdFt2": tank_result.surface_overflow_rate_gpd_ft2,
        "reynoldsNumber": tank_result.reynolds_number,
        "stokesLawValid": tank_result.stokes_law_valid,
        "captureCheckPass": tank_result.capture_check_pass,
        "velocityCheckPass": tank_result.velocity_check_pass,
        "warnings": tank_result.warnings,
    }


def run_dewatering(data: Dict[str, Any]) -> Dict[str, Any]:
    core = _run_core(data)
    zone_results, zone_errors, overlap_notes = core["zone_results"], core["zone_errors"], core["overlap_notes"]
    summary, permit_screening = core["summary"], core["permit_screening"]
    tank_mode, tank_inputs, tank_result, min_result, selected_check, receiving_checks = (
        core["tank_mode"], core["tank_inputs"], core["tank_result"], core["min_result"],
        core["selected_check"], core["receiving_checks"],
    )

    tank_payload = None
    if tank_result is not None and tank_mode == "auto" and min_result is not None:
        tank_payload = {
            "mode": "auto",
            "flowGpm": tank_inputs.flow_gpm,
            "requiredSurfaceAreaFt2": min_result.required_surface_area_ft2,
            "minWidthFt": min_result.min_width_ft,
            "minLengthFt": min_result.min_length_ft,
            "lengthFt": min_result.length_ft,
            "widthFt": min_result.width_ft,
            "depthFt": min_result.depth_ft,
            "requestedDepthFt": min_result.requested_depth_ft,
            "depthWasIncreased": min_result.depth_was_increased,
            "minDepthForScourFt": min_result.min_depth_for_scour_ft,
            **_tank_result_json(tank_result),
            "warnings": min_result.warnings + [w for w in tank_result.warnings if w not in min_result.warnings],
        }
    elif tank_result is not None and tank_inputs is not None:
        tank_payload = {
            "mode": "manual",
            "flowGpm": tank_inputs.flow_gpm,
            "lengthFt": tank_inputs.length_ft,
            "widthFt": tank_inputs.width_ft,
            "depthFt": tank_inputs.depth_ft,
            **_tank_result_json(tank_result),
        }
        if min_result is not None:
            tank_payload["requiredVolumeGal"] = min_result.tank.effective_volume_gal
            tank_payload["requiredDimensionsFt"] = {
                "lengthFt": min_result.length_ft, "widthFt": min_result.width_ft, "depthFt": min_result.depth_ft,
            }

    selected_payload = None
    if selected_check is not None:
        selected_payload = {
            "name": selected_check.name,
            "providedVolumeGal": selected_check.provided_volume_gal,
            "requiredVolumeGal": selected_check.required_volume_gal,
            "capacityRatio": selected_check.capacity_ratio,
            "detentionTimeMin": selected_check.detention_time_min,
            "status": selected_check.status,
            "notes": selected_check.notes,
        }

    return {
        "zones": [
            {
                "name": r.zone.name,
                "description": r.zone.description,
                "drawdownFt": r.zone.drawdown_ft,
                "radiusOfInfluenceFt": r.radius_of_influence_ft,
                "effectiveRadiusFt": r.effective_radius_ft,
                "totalRadiusFt": r.total_radius_ft,
                "flowGpm": r.flow_gpm,
                "dailyPumpageMgd": r.daily_pumpage_mgd,
                "totalPumpageMg": r.total_pumpage_mg,
                "totalDays": r.zone.total_days,
                "warnings": r.warnings,
            }
            for r in zone_results
        ],
        "zoneErrors": zone_errors,
        "overlapNotes": overlap_notes,
        "summary": (
            {
                "averageFlowGpm": summary.average_flow_gpm,
                "maxFlowGpm": summary.max_flow_gpm,
                "maxDailyPumpageMgd": summary.max_daily_pumpage_mgd,
                "totalProjectPumpageMg": summary.total_project_pumpage_mg,
                "totalPumpingDays": summary.total_pumping_days,
            }
            if summary else None
        ),
        "permitScreening": (
            [
                {
                    "criterion": row.criterion, "valueLabel": row.value_label,
                    "thresholdLabel": row.threshold_label, "status": row.status,
                }
                for row in permit_screening
            ]
            if permit_screening is not None else None
        ),
        "tank": tank_payload,
        "selectedTankSystem": selected_payload,
        "receivingSystemChecks": [
            {"name": rc["name"], "capacityGpm": rc["capacity_gpm"], "status": rc["status"], "notes": rc["notes"]}
            for rc in receiving_checks
        ],
    }


def build_report_objects(data: Dict[str, Any]) -> Dict[str, Any]:
    """Same computation as run_dewatering(), returned as raw dataclasses for
    reports/dewatering_calc_pdf.py instead of the browser's JSON shape."""
    return _run_core(data)
