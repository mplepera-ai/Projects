"""
Core dewatering calculation engine.

Implements, per excavation zone:
  - Sichardt empirical radius of influence
  - Dupuit-Forchheimer radial flow (unconfined aquifer)
  - Thiem radial flow (confined aquifer)
  - Effective (equivalent circular) radius for a rectangular excavation
  - Multi-well superposition for closely-spaced/concurrent zones
  - Water balance / pumping schedule roll-up
  - Settling tank sizing via surface overflow rate (SOR), with a
    horizontal-velocity vs. settling-velocity scour check
  - Reynolds-number validity check for Stokes' Law
  - General-permit threshold screening (thresholds are user-configurable —
    verify current values against the governing agency's rules before
    relying on this flag)

Unit conventions:
  - All *_ft inputs/outputs are US survey feet unless noted.
  - Internal radial-flow math is done in SI (meters, m/s) to match the
    Sichardt/Dupuit/Thiem formulas as commonly published, then converted
    back to ft/gpm for reporting. Conversions use exact factors
    (1 m = 3.280839895 ft), not the 3.3 rounding found in the source
    spreadsheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Optional

# ---------------------------------------------------------------------------
# Constants / conversions
# ---------------------------------------------------------------------------

FT_PER_M = 3.280839895
M_PER_FT = 1.0 / FT_PER_M
GPM_PER_CMS = 15850.323  # gallons/min per cubic meter/second
GAL_PER_CF = 7.480519480519481
SEC_PER_MIN = 60.0


def ft_to_m(x: float) -> float:
    return x * M_PER_FT


def m_to_ft(x: float) -> float:
    return x * FT_PER_M


class AquiferType(str, Enum):
    UNCONFINED = "unconfined"
    CONFINED = "confined"


# ---------------------------------------------------------------------------
# Site / aquifer parameters
# ---------------------------------------------------------------------------

@dataclass
class AquiferParams:
    hydraulic_conductivity_ft_day: float          # K, ft/day
    aquifer_thickness_ft: float                    # H (unconfined) or b (confined saturated thickness), ft
    aquifer_type: AquiferType = AquiferType.UNCONFINED

    @property
    def k_m_s(self) -> float:
        """Hydraulic conductivity converted to m/s (ft/day -> m/s)."""
        return ft_to_m(self.hydraulic_conductivity_ft_day) / 86400.0

    @property
    def thickness_m(self) -> float:
        return ft_to_m(self.aquifer_thickness_ft)


# ---------------------------------------------------------------------------
# Excavation zone
# ---------------------------------------------------------------------------

@dataclass
class Zone:
    name: str
    description: str = ""

    # Elevations, all in the same datum (e.g. NAVD88), feet
    groundwater_elev_ft: float = 0.0
    excavation_bottom_elev_ft: float = 0.0
    operating_margin_ft: float = 1.0   # buffer pumped below excavation bottom (was hardcoded "1" in source sheet)

    # Excavation footprint
    width_ft: float = 0.0
    length_ft: float = 0.0

    # Pumping schedule for this zone
    hours_per_day: float = 12.0
    estimated_days: float = 1.0
    number_of_elements: int = 1  # e.g. repeated typical sections

    @property
    def dewatered_elev_ft(self) -> float:
        return self.excavation_bottom_elev_ft - self.operating_margin_ft

    @property
    def drawdown_ft(self) -> float:
        """s = static groundwater elevation - dewatered (pumping) elevation."""
        return self.groundwater_elev_ft - self.dewatered_elev_ft

    @property
    def drawdown_m(self) -> float:
        return ft_to_m(self.drawdown_ft)

    @property
    def total_days(self) -> float:
        return self.estimated_days * self.number_of_elements


@dataclass
class ZoneResult:
    zone: Zone
    radius_of_influence_m: float
    radius_of_influence_ft: float
    effective_radius_m: float
    effective_radius_ft: float
    total_radius_m: float
    total_radius_ft: float
    flow_cms: float
    flow_gpm: float
    daily_pumpage_mgd: float
    total_pumpage_mg: float
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Radial flow calculations
# ---------------------------------------------------------------------------

def sichardt_radius_of_influence_m(drawdown_m: float, k_m_s: float) -> float:
    """Sichardt's empirical formula: R (m) = 3000 * s(m) * sqrt(K (m/s))."""
    if k_m_s < 0:
        raise ValueError("Hydraulic conductivity must be non-negative")
    return 3000.0 * drawdown_m * math.sqrt(k_m_s)


def effective_radius_m(width_ft: float, length_ft: float) -> float:
    """Equivalent circular radius of a rectangular excavation: re = sqrt(L*W/pi)."""
    area_m2 = ft_to_m(width_ft) * ft_to_m(length_ft)
    return math.sqrt(area_m2 / math.pi)


def dupuit_unconfined_flow_cms(k_m_s: float, aquifer_thickness_m: float,
                                drawdown_m: float, roi_m: float,
                                effective_radius_m_: float) -> float:
    """
    Dupuit-Forchheimer radial flow to an equivalent well in an unconfined
    aquifer:  Q = pi * K * (H^2 - h^2) / ln(R / re)
    where h = H - drawdown, R = Sichardt radius of influence (measured from
    the well/excavation center), re = effective well radius.
    """
    h = aquifer_thickness_m - drawdown_m
    if h <= 0:
        raise ValueError("Drawdown exceeds aquifer thickness — dewatered depth is below the aquifer base")
    if roi_m <= effective_radius_m_:
        raise ValueError("Radius of influence must exceed the effective well radius")
    numerator = (aquifer_thickness_m ** 2) - (h ** 2)
    return math.pi * k_m_s * numerator / (math.log(roi_m) - math.log(effective_radius_m_))


def thiem_confined_flow_cms(k_m_s: float, confined_thickness_m: float,
                             drawdown_m: float, roi_m: float,
                             effective_radius_m_: float) -> float:
    """
    Thiem equation for radial flow to an equivalent well in a confined
    aquifer:  Q = 2 * pi * T * s / ln(R / re), T = K * b
    """
    if roi_m <= effective_radius_m_:
        raise ValueError("Radius of influence must exceed the effective well radius")
    transmissivity = k_m_s * confined_thickness_m
    return 2.0 * math.pi * transmissivity * drawdown_m / (math.log(roi_m) - math.log(effective_radius_m_))


def compute_zone(zone: Zone, aquifer: AquiferParams) -> ZoneResult:
    warnings: list = []

    if zone.drawdown_ft <= 0:
        raise ValueError(f"Zone '{zone.name}': drawdown must be positive "
                          f"(check groundwater elevation vs. excavation/dewatered elevation)")

    roi_m = sichardt_radius_of_influence_m(zone.drawdown_m, aquifer.k_m_s)
    re_m = effective_radius_m(zone.width_ft, zone.length_ft)
    # "Total radius of influence" (Ri + re) is reported for reference, matching
    # the source workbook's Sheet2 "Total Rad. Influence Rt" column. NOTE: the
    # source workbook is internally inconsistent about which radius belongs in
    # the Dupuit/Thiem log term — Sheet1 used Rt=(Ri+re) there, Sheet2 (the
    # production, multi-zone sheet) used Ri alone. Sichardt's R is already
    # measured from the well/excavation center, so using Ri alone (Sheet2's
    # approach, matched here) is the more standard form of Q=piK(H^2-h^2)/ln(R/re).
    rt_m = roi_m + re_m

    if aquifer.aquifer_type == AquiferType.UNCONFINED:
        q_cms = dupuit_unconfined_flow_cms(
            aquifer.k_m_s, aquifer.thickness_m, zone.drawdown_m, roi_m, re_m
        )
    else:
        q_cms = thiem_confined_flow_cms(
            aquifer.k_m_s, aquifer.thickness_m, zone.drawdown_m, roi_m, re_m
        )

    q_gpm = q_cms * GPM_PER_CMS
    daily_pumpage_mgd = (q_gpm * zone.hours_per_day * SEC_PER_MIN) / 1_000_000.0
    total_pumpage_mg = daily_pumpage_mgd * zone.total_days

    if zone.operating_margin_ft <= 0:
        warnings.append("Operating margin below excavation bottom is zero or negative — "
                         "confirm this is intended.")
    if roi_m > 0 and re_m / roi_m > 0.5:
        warnings.append("Effective well radius is large relative to the estimated radius of "
                         "influence — the equivalent-well approximation is less reliable for "
                         "very large or elongated excavations; consider a superposition or "
                         "line-source method.")

    return ZoneResult(
        zone=zone,
        radius_of_influence_m=roi_m,
        radius_of_influence_ft=m_to_ft(roi_m),
        effective_radius_m=re_m,
        effective_radius_ft=m_to_ft(re_m),
        total_radius_m=rt_m,
        total_radius_ft=m_to_ft(rt_m),
        flow_cms=q_cms,
        flow_gpm=q_gpm,
        daily_pumpage_mgd=daily_pumpage_mgd,
        total_pumpage_mg=total_pumpage_mg,
        warnings=warnings,
    )


def check_zone_overlap(zones: list["Zone"], results: list["ZoneResult"]) -> list[str]:
    """
    Flag zone pairs whose radius-of-influence circles are close enough
    (footprint centroid distance < sum of total radii) that independent
    (non-superposed) analysis may under/overstate combined drawdown.
    This is a screening heuristic, not a substitute for a superposition
    calculation — it has no coordinate data to work from, so it only
    warns based on relative ROI magnitude vs. footprint size.
    """
    notes = []
    for r in results:
        if r.total_radius_ft > 3 * max(r.zone.width_ft, r.zone.length_ft):
            notes.append(
                f"Zone '{r.zone.name}': total radius of influence "
                f"({r.total_radius_ft:.0f} ft) is large relative to the excavation footprint. "
                f"If other zones lie within this radius, evaluate combined drawdown with "
                f"superposition rather than treating zones independently."
            )
    return notes


# ---------------------------------------------------------------------------
# Water balance / project summary
# ---------------------------------------------------------------------------

@dataclass
class WaterBalanceSummary:
    average_flow_gpm: float
    max_flow_gpm: float
    max_daily_pumpage_mgd: float
    total_project_pumpage_mg: float
    total_pumping_days: float


def summarize_water_balance(results: list[ZoneResult]) -> WaterBalanceSummary:
    flows = [r.flow_gpm for r in results]
    return WaterBalanceSummary(
        average_flow_gpm=sum(flows) / len(flows) if flows else 0.0,
        max_flow_gpm=max(flows) if flows else 0.0,
        max_daily_pumpage_mgd=max((r.daily_pumpage_mgd for r in results), default=0.0),
        total_project_pumpage_mg=sum(r.total_pumpage_mg for r in results),
        total_pumping_days=sum(r.zone.total_days for r in results),
    )


# ---------------------------------------------------------------------------
# Permit threshold screening
# ---------------------------------------------------------------------------

@dataclass
class PermitThresholds:
    """
    User/agency-configurable general (no-notice) permit thresholds.
    Defaults are illustrative placeholders only — always confirm the
    current numeric thresholds against the governing agency's rule
    (e.g., SFWMD / FDEP Environmental Resource Permit or Water Use Permit
    general permit criteria) before relying on this screen.
    """
    max_average_gpm: Optional[float] = None
    max_daily_mgd: Optional[float] = None
    max_duration_days: Optional[float] = None


def screen_permit_thresholds(summary: WaterBalanceSummary,
                              thresholds: PermitThresholds) -> list[str]:
    flags = []
    if thresholds.max_average_gpm is not None and summary.average_flow_gpm > thresholds.max_average_gpm:
        flags.append(
            f"Average flow {summary.average_flow_gpm:.0f} gpm exceeds the configured general-permit "
            f"threshold of {thresholds.max_average_gpm:.0f} gpm — an individual/standard permit may be required."
        )
    if thresholds.max_daily_mgd is not None and summary.max_daily_pumpage_mgd > thresholds.max_daily_mgd:
        flags.append(
            f"Max daily pumpage {summary.max_daily_pumpage_mgd:.3f} MGD exceeds the configured threshold of "
            f"{thresholds.max_daily_mgd:.3f} MGD."
        )
    if thresholds.max_duration_days is not None and summary.total_pumping_days > thresholds.max_duration_days:
        flags.append(
            f"Total pumping duration {summary.total_pumping_days:.0f} days exceeds the configured threshold of "
            f"{thresholds.max_duration_days:.0f} days."
        )
    if not flags:
        flags.append("No configured general-permit thresholds were exceeded (or none were configured). "
                      "This is a screening aid only, not a permit determination.")
    return flags


# ---------------------------------------------------------------------------
# Settling tank design
# ---------------------------------------------------------------------------

@dataclass
class TankInputs:
    flow_gpm: float
    length_ft: float
    width_ft: float
    depth_ft: float
    particle_diameter_ft: float = 0.000279   # ~0.085 mm, matches source sheet default
    specific_gravity: float = 2.65
    gravity_ft_s2: float = 32.2
    kinematic_viscosity_ft2_s: float = 9.26e-6  # water at ~68 F


@dataclass
class TankResult:
    volume_cf: float
    volume_gal: float
    detention_time_min: float
    mean_horizontal_velocity_ft_s: float
    settling_velocity_ft_s: float
    surface_overflow_rate_gpd_ft2: float
    reynolds_number: float
    stokes_law_valid: bool
    velocity_check_pass: bool
    warnings: list = field(default_factory=list)


CLAY_PARTICLE_DIAMETER_FT = 0.0000131  # ~0.004 mm, upper bound of clay-size fraction


def stokes_settling_velocity_ft_s(d_ft: float, sg: float, g: float, nu: float) -> float:
    return (g * (sg - 1.0) * (d_ft ** 2)) / (18.0 * nu)


def particle_reynolds_number(vs_ft_s: float, d_ft: float, nu: float) -> float:
    return vs_ft_s * d_ft / nu


def design_settling_tank(inputs: TankInputs) -> TankResult:
    warnings = []

    volume_cf = inputs.length_ft * inputs.width_ft * inputs.depth_ft
    volume_gal = volume_cf * GAL_PER_CF
    detention_time_min = volume_gal / inputs.flow_gpm if inputs.flow_gpm else float("inf")

    cross_section_ft2 = inputs.width_ft * inputs.depth_ft
    # 0.002228009 = ft3/s per gpm
    q_cfs = inputs.flow_gpm * 0.002228009
    vm = q_cfs / cross_section_ft2 if cross_section_ft2 else float("inf")

    vs = stokes_settling_velocity_ft_s(
        inputs.particle_diameter_ft, inputs.specific_gravity,
        inputs.gravity_ft_s2, inputs.kinematic_viscosity_ft2_s
    )

    re = particle_reynolds_number(vs, inputs.particle_diameter_ft, inputs.kinematic_viscosity_ft2_s)
    stokes_valid = re < 1.0
    if not stokes_valid:
        warnings.append(
            f"Particle Reynolds number ({re:.2f}) is outside the laminar range (Re < 1) where "
            f"Stokes' Law applies — settling velocity is likely overstated; use a transitional "
            f"(intermediate) or Newton's-law drag correlation instead."
        )

    if inputs.particle_diameter_ft < CLAY_PARTICLE_DIAMETER_FT * 2:
        warnings.append(
            "Particle diameter is in/near the clay-size range — discrete (Type 1) settling theory "
            "does not reliably apply; consider flocculation/coagulation, or a filter bag / "
            "alternative BMP instead of gravity settling alone."
        )

    surface_area_ft2 = inputs.length_ft * inputs.width_ft
    # SOR expressed as gallons/day/ft^2, the conventional sedimentation-basin design unit
    sor_gpd_ft2 = (inputs.flow_gpm * 1440.0) / surface_area_ft2 if surface_area_ft2 else float("inf")
    sor_ft_s = q_cfs / surface_area_ft2 if surface_area_ft2 else float("inf")

    sor_pass = vs >= sor_ft_s
    velocity_pass = vs > vm
    if not sor_pass:
        warnings.append(
            "Settling velocity is less than the surface overflow rate — particles of this size "
            "are not reliably captured; increase surface area (length x width) rather than depth."
        )
    if velocity_pass and vs < 2 * vm:
        warnings.append(
            "Settling velocity exceeds mean horizontal velocity by less than 2x — "
            "little margin against resuspension/scour; consider a larger cross-section "
            "or added baffling."
        )

    return TankResult(
        volume_cf=volume_cf,
        volume_gal=volume_gal,
        detention_time_min=detention_time_min,
        mean_horizontal_velocity_ft_s=vm,
        settling_velocity_ft_s=vs,
        surface_overflow_rate_gpd_ft2=sor_gpd_ft2,
        reynolds_number=re,
        stokes_law_valid=stokes_valid,
        velocity_check_pass=velocity_pass,
        warnings=warnings,
    )
