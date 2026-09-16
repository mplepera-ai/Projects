"""
hydrology/rainfall.py

Rainfall distributions and the gross -> net rainfall pipeline.
Section 12 is explicit: never silently alter regulatory rainfall.
Gross rainfall, ground storage, and applied (excess) rainfall must
all be stored and reportable separately.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple
from core.interpolation import validate_distribution


@dataclass
class RainfallDistribution:
    name: str
    # (time_hours, cumulative_fraction) pairs, fraction in [0, 1]
    points: List[Tuple[float, float]]

    def __post_init__(self):
        times = [p[0] for p in self.points]
        fracs = [p[1] for p in self.points]
        validate_distribution(times, fracs)

    def cumulative_fraction_at(self, t_hours: float) -> float:
        pts = self.points
        if t_hours <= pts[0][0]:
            return pts[0][1]
        if t_hours >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            t1, f1 = pts[i - 1]
            t2, f2 = pts[i]
            if t1 <= t_hours <= t2:
                if t2 == t1:
                    return f2
                frac = (t_hours - t1) / (t2 - t1)
                return f1 + frac * (f2 - f1)
        raise RuntimeError("unreachable")


@dataclass
class StormEvent:
    name: str
    return_period_years: float
    duration_hours: float
    rainfall_depth_inches: float
    distribution: RainfallDistribution
    source_reference: str = ""

    def cumulative_rainfall_at(self, t_hours: float) -> float:
        """Gross regulatory cumulative rainfall (inches) at time t."""
        return self.rainfall_depth_inches * self.distribution.cumulative_fraction_at(t_hours)


# ---- Built-in library placeholders ---------------------------------------
# NOTE: these are illustrative simplified distributions (roughly front/
# center-loaded, SCS-like shapes). The real SFWMD 24-hr and 3-day mass
# curves must be substituted from the REG-003 documentation before this
# is used for anything but engine testing -- see Section 74, Cascade
# Compatibility Mode, and the regression library in Section 72.

SFWMD_24HR_PLACEHOLDER = RainfallDistribution(
    name="SFWMD 24-Hour (PLACEHOLDER - replace with verified curve)",
    points=[
        (0.0, 0.000), (4.0, 0.080), (8.0, 0.200), (10.0, 0.330),
        (11.0, 0.500), (11.5, 0.650), (12.0, 0.760), (13.0, 0.850),
        (16.0, 0.920), (20.0, 0.970), (24.0, 1.000),
    ],
)

SFWMD_3DAY_PLACEHOLDER = RainfallDistribution(
    name="SFWMD 3-Day (PLACEHOLDER - replace with verified curve)",
    points=[
        (0.0, 0.000), (24.0, 0.200), (48.0, 0.450),
        (58.0, 0.650), (60.0, 0.760), (62.0, 0.850),
        (66.0, 0.920), (72.0, 1.000),
    ],
)


def ground_storage_excess(
    cumulative_gross_inches_series: List[float],
    ground_storage_inches: float,
) -> List[float]:
    """
    Section 13.1: cumulative ground-storage abstraction.

        P_excess_cumulative(t) = max(P_cumulative(t) - GroundStorage, 0)

    Returns the cumulative EXCESS series (same length as input), from
    which incremental excess is later differenced by the SBUH module.
    """
    return [max(p - ground_storage_inches, 0.0) for p in cumulative_gross_inches_series]
