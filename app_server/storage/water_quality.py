"""
storage/water_quality.py

Water quality treatment volume calculator, kept deliberately separate
from the flood-routing engine (Section 25's explicit requirement).
Supports the legacy SFWMD volumetric method now; the architecture
leaves room for a 2024 performance-based method and fully custom
required volumes without touching this class's public interface.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from core.units import ACRE_IN_TO_CUFT, CUFT_TO_ACRE_IN


class WaterQualityMethod(Enum):
    LEGACY_SFWMD_VOLUMETRIC = "LEGACY_SFWMD_VOLUMETRIC"
    PERFORMANCE_BASED_2024 = "PERFORMANCE_BASED_2024"
    CUSTOM = "CUSTOM"


@dataclass
class LegacyVolumetricInputs:
    project_area_acres: float
    impervious_fraction: float  # 0-1
    dry_detention_credit_ac_in: float = 0.0
    retention_credit_ac_in: float = 0.0
    pretreatment_volume_ac_in: float = 0.0
    # The "2.5 inches x impervious fraction" criterion is standard SFWMD,
    # but local ordinances vary (e.g. a City of Miami Section 20.3 project
    # instead requires the full design-storm rainfall depth over the site,
    # which an engineer can reproduce here by entering that rainfall depth
    # as the multiplier with impervious_fraction set to 1.0). Rather than
    # hardcoding per-jurisdiction logic, the multiplier itself is an input
    # -- editable per project, defaulting to the SFWMD standard.
    impervious_multiplier_in: float = 2.5

    def __post_init__(self):
        if not (0.0 <= self.impervious_fraction <= 1.0):
            raise ValueError("impervious_fraction must be between 0 and 1.")
        if self.project_area_acres <= 0:
            raise ValueError("project_area_acres must be > 0.")
        if self.impervious_multiplier_in < 0:
            raise ValueError("impervious_multiplier_in must be >= 0.")


@dataclass
class WaterQualityResult:
    method: WaterQualityMethod
    first_inch_volume_ac_in: float
    impervious_based_volume_ac_in: float
    impervious_multiplier_in: float
    governing_gross_required_volume_ac_in: float
    dry_detention_credit_ac_in: float
    retention_credit_ac_in: float
    pretreatment_volume_ac_in: float
    net_required_volume_ac_in: float
    provided_volume_ac_in: float
    status: str


def calculate_legacy_volumetric(
    inputs: LegacyVolumetricInputs,
    provided_volume_ac_in: float,
) -> WaterQualityResult:
    """
    Legacy SFWMD method: required volume is the GREATER of

        1 inch over the project area (first_inch_volume), or
        impervious_multiplier_in x impervious fraction over the project area
        (multiplier defaults to the SFWMD-standard 2.5 inches, but is an
        input -- see LegacyVolumetricInputs.impervious_multiplier_in -- so
        a project under a different local ordinance can use its own value
        instead of this module hardcoding jurisdiction-specific logic)

    Credits (dry detention, retention, pretreatment) reduce the net
    volume the engineer must actually provide, but the gross required
    volume is always reported alongside the net so nothing is hidden
    (same "never silently alter" principle as gross/net rainfall,
    Section 12).
    """
    first_inch = 1.0 * inputs.project_area_acres          # ac-in
    impervious_based = (
        inputs.impervious_multiplier_in * inputs.impervious_fraction * inputs.project_area_acres
    )  # ac-in

    gross_required = max(first_inch, impervious_based)

    net_required = gross_required - (
        inputs.dry_detention_credit_ac_in
        + inputs.retention_credit_ac_in
        + inputs.pretreatment_volume_ac_in
    )
    net_required = max(net_required, 0.0)

    status = "PASS" if provided_volume_ac_in >= net_required else "REVIEW REQUIRED"

    return WaterQualityResult(
        method=WaterQualityMethod.LEGACY_SFWMD_VOLUMETRIC,
        first_inch_volume_ac_in=first_inch,
        impervious_based_volume_ac_in=impervious_based,
        impervious_multiplier_in=inputs.impervious_multiplier_in,
        governing_gross_required_volume_ac_in=gross_required,
        dry_detention_credit_ac_in=inputs.dry_detention_credit_ac_in,
        retention_credit_ac_in=inputs.retention_credit_ac_in,
        pretreatment_volume_ac_in=inputs.pretreatment_volume_ac_in,
        net_required_volume_ac_in=net_required,
        provided_volume_ac_in=provided_volume_ac_in,
        status=status,
    )


def calculate_custom(
    required_volume_ac_in: float,
    provided_volume_ac_in: float,
) -> WaterQualityResult:
    status = "PASS" if provided_volume_ac_in >= required_volume_ac_in else "REVIEW REQUIRED"
    return WaterQualityResult(
        method=WaterQualityMethod.CUSTOM,
        first_inch_volume_ac_in=0.0,
        impervious_based_volume_ac_in=0.0,
        impervious_multiplier_in=0.0,
        governing_gross_required_volume_ac_in=required_volume_ac_in,
        dry_detention_credit_ac_in=0.0,
        retention_credit_ac_in=0.0,
        pretreatment_volume_ac_in=0.0,
        net_required_volume_ac_in=required_volume_ac_in,
        provided_volume_ac_in=provided_volume_ac_in,
        status=status,
    )
