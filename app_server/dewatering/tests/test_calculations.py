"""
Validation tests: reproduce Sheet2 (the 9-zone + 2 typical-section
production sheet) from Dewatering_Calculations.xlsx to confirm the Python
engine matches the original spreadsheet's flow-rate results (within the
small tolerance expected from the ft/m conversion-factor fix — see the
review notes: source sheet used 3.3, this engine uses the exact factor).
"""
import math
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dewatering.calculations import (
    AquiferParams, AquiferType, Zone, compute_zone,
    summarize_water_balance, TankInputs, design_settling_tank,
    MinTankInputs, size_minimum_settling_tank,
)

AQUIFER = AquiferParams(hydraulic_conductivity_ft_day=25, aquifer_thickness_ft=220,
                         aquifer_type=AquiferType.UNCONFINED)

# (name, gw_elev, exc_bottom_elev, width, length, expected_flow_gpm_from_excel)
ZONES = [
    ("Zone 1", 2.5, -6.07, 18, 25, 539.61),
    ("Zone 2", 2.5, -5.30, 10, 40, 501.10),
    ("Zone 3", 2.5, -4.50, 10, 17, 412.05),
    ("Zone 4", 2.5, -4.50, 10, 40, 470.92),
    ("Zone 5", 2.5, -1.53, 10, 40, 352.79),
    ("Zone 6", 2.5, -1.26, 13, 28, 335.12),
    ("Zone 7", 2.5, -1.26, 10, 20, 299.57),
    ("Zone 8", 2.5, -3.52, 10, 20, 386.31),
    ("Zone 9", 2.5, -3.52, 17, 35, 465.33),
    ("TYP A",  2.5, -6.07, 6, 40, 490.15),
    ("TYP B",  2.5, -2.46, 6, 40, 357.14),
]


def test_zone_flow_rates_match_spreadsheet_within_tolerance():
    for name, gw, exc, w, l, expected_gpm in ZONES:
        zone = Zone(name=name, groundwater_elev_ft=gw, excavation_bottom_elev_ft=exc,
                    operating_margin_ft=1.0, width_ft=w, length_ft=l,
                    hours_per_day=12, estimated_days=10)
        result = compute_zone(zone, AQUIFER)
        pct_diff = abs(result.flow_gpm - expected_gpm) / expected_gpm * 100
        # Expect close agreement; the source sheet's 3.3 rounding (vs. exact
        # 3.280839895 ft/m here) accounts for the small residual difference.
        assert pct_diff < 1.0, f"{name}: got {result.flow_gpm:.2f} gpm, expected ~{expected_gpm} gpm ({pct_diff:.2f}% off)"


# Per-zone (days, elements) exactly as entered in Sheet2 H45:I55
SCHEDULE = {
    "Zone 1": (10, 1), "Zone 2": (20, 1), "Zone 3": (10, 1), "Zone 4": (10, 1),
    "Zone 5": (10, 1), "Zone 6": (10, 1), "Zone 7": (10, 1), "Zone 8": (10, 1),
    "Zone 9": (10, 1), "TYP A": (1, 2), "TYP B": (0.5, 8),
}


def test_water_balance_matches_spreadsheet_order_of_magnitude():
    results = []
    for name, gw, exc, w, l, _ in ZONES:
        days, elements = SCHEDULE[name]
        zone = Zone(name=name, groundwater_elev_ft=gw, excavation_bottom_elev_ft=exc,
                    operating_margin_ft=1.0, width_ft=w, length_ft=l,
                    hours_per_day=12, estimated_days=days, number_of_elements=elements)
        results.append(compute_zone(zone, AQUIFER))

    summary = summarize_water_balance(results)
    # Excel: average 419.1 gpm, max daily 0.3885 MGD, total 32.43 MG, 106 days
    assert abs(summary.average_flow_gpm - 419.1) / 419.1 < 0.01
    assert abs(summary.max_daily_pumpage_mgd - 0.3885) / 0.3885 < 0.01
    assert abs(summary.total_project_pumpage_mg - 32.43) / 32.43 < 0.01
    assert summary.total_pumping_days == 106


def test_settling_tank_matches_spreadsheet_sheet2():
    # Sheet2 tank: 35 x 10 x 8 ft, max flow 539.61 gpm
    inputs = TankInputs(flow_gpm=539.61, length_ft=35, width_ft=10, depth_ft=8)
    result = design_settling_tank(inputs)
    assert abs(result.volume_gal - 20945.45) / 20945.45 < 0.01
    assert abs(result.detention_time_min - 38.82) / 38.82 < 0.02
    assert abs(result.mean_horizontal_velocity_ft_s - 0.015028) / 0.015028 < 0.01
    assert abs(result.settling_velocity_ft_s - 0.024812) / 0.024812 < 0.01
    assert result.settling_velocity_ft_s > result.mean_horizontal_velocity_ft_s  # matches Excel's TRUE check


def test_size_minimum_settling_tank_satisfies_sor_and_scour():
    # Same flow as the Sheet2 max-flow zone (539.61 gpm), default particle.
    inputs = MinTankInputs(flow_gpm=539.61, depth_ft=4.0, length_to_width_ratio=2.0, size_increment_ft=1.0)
    result = size_minimum_settling_tank(inputs)
    # Required area A_min = Q_cfs / Vs must match Q/Vs directly.
    q_cfs = 539.61 * 0.002228009
    expected_area = q_cfs / result.settling_velocity_ft_s
    assert abs(result.required_surface_area_ft2 - expected_area) / expected_area < 1e-6
    # Rounded dims must be >= the exact minimum (never undersized).
    assert result.width_ft >= result.min_width_ft
    assert result.length_ft >= result.min_length_ft
    # The re-run tank result must actually pass both the SOR and scour checks
    # at the returned dimensions -- "smallest required tank" has to be a tank
    # that actually works, not just an area number.
    assert result.tank.settling_velocity_ft_s >= (
        (539.61 * 0.002228009) / (result.width_ft * result.length_ft)
    )
    assert result.tank.mean_horizontal_velocity_ft_s <= result.tank.settling_velocity_ft_s + 1e-9


def test_size_minimum_settling_tank_smaller_than_oversized_manual_tank():
    # The auto-sized tank should never be larger (by volume) than an
    # arbitrarily oversized manually-specified tank for the same duty.
    auto = size_minimum_settling_tank(MinTankInputs(flow_gpm=227.5566, depth_ft=4.0))
    manual_oversized = design_settling_tank(TankInputs(flow_gpm=227.5566, length_ft=40, width_ft=20, depth_ft=10))
    assert auto.tank.volume_cf < manual_oversized.volume_cf


def test_zero_or_negative_drawdown_raises():
    zone = Zone(name="bad", groundwater_elev_ft=1.0, excavation_bottom_elev_ft=5.0,
                operating_margin_ft=1.0, width_ft=10, length_ft=10)
    try:
        compute_zone(zone, AQUIFER)
        assert False, "expected ValueError for non-positive drawdown"
    except ValueError:
        pass
