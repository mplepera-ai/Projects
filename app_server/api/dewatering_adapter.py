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
    TankInputs, design_settling_tank,
)


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


def run_dewatering(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    data = {
      aquifer: {kFtDay, aquiferThicknessFt, aquiferType},
      zones: [{name, description, gwElevFt, excBottomElevFt, operatingMarginFt,
                widthFt, lengthFt, hoursPerDay, estimatedDays, numberOfElements}, ...],
      permitThresholds: {maxAverageGpm?, maxDailyMgd?, maxDurationDays?},
      tank: {lengthFt, widthFt, depthFt, flowGpmOverride?, particleDiameterFt?, specificGravity?}
    }
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
    permit_flags = screen_permit_thresholds(summary, thresholds) if summary else []

    tank_payload = None
    tank_data = data.get("tank")
    if tank_data and tank_data.get("lengthFt") not in (None, ""):
        flow_override = _opt_float(tank_data, "flowGpmOverride")
        flow_gpm = flow_override if flow_override is not None else (summary.max_flow_gpm if summary else 0.0)
        tank_inputs = TankInputs(
            flow_gpm=flow_gpm,
            length_ft=_req_float(tank_data, "lengthFt", "Tank Length"),
            width_ft=_req_float(tank_data, "widthFt", "Tank Width"),
            depth_ft=_req_float(tank_data, "depthFt", "Tank Depth"),
            particle_diameter_ft=_opt_float(tank_data, "particleDiameterFt") or 0.000279,
            specific_gravity=_opt_float(tank_data, "specificGravity") or 2.65,
        )
        tank_result = design_settling_tank(tank_inputs)
        tank_payload = {
            "flowGpm": tank_inputs.flow_gpm,
            "volumeCf": tank_result.volume_cf,
            "volumeGal": tank_result.volume_gal,
            "detentionTimeMin": tank_result.detention_time_min,
            "meanHorizontalVelocityFtS": tank_result.mean_horizontal_velocity_ft_s,
            "settlingVelocityFtS": tank_result.settling_velocity_ft_s,
            "surfaceOverflowRateGpdFt2": tank_result.surface_overflow_rate_gpd_ft2,
            "reynoldsNumber": tank_result.reynolds_number,
            "stokesLawValid": tank_result.stokes_law_valid,
            "velocityCheckPass": tank_result.velocity_check_pass,
            "warnings": tank_result.warnings,
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
        "permitFlags": permit_flags,
        "tank": tank_payload,
    }
