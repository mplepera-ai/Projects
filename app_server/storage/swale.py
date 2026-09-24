"""
storage/swale.py

Integrated swale-storage calculator (Section 18). Supports triangular,
trapezoidal, rectangular, and irregular stage-area cross-sections.
Produces a stage-storage table that can be merged into a basin's
authoritative stage-storage curve (Section 17) -- callers are
responsible for NOT double-counting a swale that is both entered here
AND manually included in the basin curve (Section 81 acceptance test).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from core.units import CUFT_TO_ACRE_FT, CUFT_TO_ACRE_IN
from core.interpolation import StageStorageCurve


class SwaleError(Exception):
    pass


@dataclass
class Swale:
    name: str
    bottom_elevation_ft: float
    top_elevation_ft: float
    length_ft: float

    # Trapezoidal / triangular / rectangular params (ignored if
    # irregular_stage_area is provided instead)
    bottom_width_ft: float = 0.0          # 0 for triangular
    left_side_slope_h_per_v: float = 3.0  # "H:V" -- horizontal run per 1 vertical
    right_side_slope_h_per_v: float = 3.0

    # For irregular cross-sections: list of (depth_ft, area_sqft) pairs,
    # depth measured from the swale bottom. Overrides geometry params above.
    irregular_depth_area: Optional[List[Tuple[float, float]]] = None

    include_in_basin_storage: bool = True

    def __post_init__(self):
        if self.top_elevation_ft <= self.bottom_elevation_ft:
            raise SwaleError(
                f"Swale '{self.name}': top elevation must exceed bottom elevation."
            )
        if self.length_ft <= 0:
            raise SwaleError(f"Swale '{self.name}': length must be > 0.")
        if self.irregular_depth_area is None:
            if self.bottom_width_ft < 0:
                raise SwaleError(f"Swale '{self.name}': bottom width cannot be negative.")
            if self.left_side_slope_h_per_v < 0 or self.right_side_slope_h_per_v < 0:
                raise SwaleError(f"Swale '{self.name}': side slopes cannot be negative.")

    @property
    def max_depth_ft(self) -> float:
        return self.top_elevation_ft - self.bottom_elevation_ft

    def cross_sectional_area_sqft(self, depth_ft: float) -> float:
        """A(h). Handles triangular (b=0), trapezoidal, rectangular
        (z1=z2=0), and irregular stage-area cross-sections."""
        if depth_ft <= 0:
            return 0.0

        if self.irregular_depth_area is not None:
            pts = sorted(self.irregular_depth_area, key=lambda p: p[0])
            if depth_ft <= pts[0][0]:
                return pts[0][1] if depth_ft == pts[0][0] else 0.0
            if depth_ft >= pts[-1][0]:
                return pts[-1][1]
            for i in range(1, len(pts)):
                d1, a1 = pts[i - 1]
                d2, a2 = pts[i]
                if d1 <= depth_ft <= d2:
                    frac = (depth_ft - d1) / (d2 - d1) if d2 != d1 else 0.0
                    return a1 + frac * (a2 - a1)
            return pts[-1][1]

        b = self.bottom_width_ft
        z1 = self.left_side_slope_h_per_v
        z2 = self.right_side_slope_h_per_v
        h = depth_ft
        # General trapezoid: A(h) = b*h + 0.5*(z1+z2)*h^2
        # z1=z2=0 -> rectangular; b=0 -> triangular (symmetric or not)
        return b * h + 0.5 * (z1 + z2) * h * h

    def storage_cuft(self, depth_ft: float) -> float:
        return self.cross_sectional_area_sqft(depth_ft) * self.length_ft

    def storage_acre_ft(self, depth_ft: float) -> float:
        return self.storage_cuft(depth_ft) * CUFT_TO_ACRE_FT

    def stage_storage_table(self, n_points: int = 11) -> List[Tuple[float, float]]:
        """Generates (stage_ft, storage_acre_ft) pairs from bottom to top
        elevation, suitable for merging into a basin's stage-storage curve."""
        if n_points < 2:
            raise SwaleError("n_points must be >= 2")
        table = []
        for i in range(n_points):
            depth = self.max_depth_ft * i / (n_points - 1)
            stage = self.bottom_elevation_ft + depth
            table.append((stage, self.storage_acre_ft(depth)))
        return table

    def summary(self) -> dict:
        max_storage_cuft = self.storage_cuft(self.max_depth_ft)
        return {
            "name": self.name,
            "bottom_elevation_ft": self.bottom_elevation_ft,
            "top_elevation_ft": self.top_elevation_ft,
            "max_depth_ft": self.max_depth_ft,
            "length_ft": self.length_ft,
            "max_storage_cuft": max_storage_cuft,
            "max_storage_acre_ft": max_storage_cuft * CUFT_TO_ACRE_FT,
            "max_storage_acre_in": max_storage_cuft * CUFT_TO_ACRE_IN,
        }


def area_based_depth_volume_table(
    bottom_area_sqft: float, top_area_sqft: float, max_depth_ft: float, n_points: int = 11,
) -> List[Tuple[float, float]]:
    """
    The "average end area" method used in real South Florida swale
    takeoffs (confirmed against a submitted report):

        V = (top_area + bottom_area) / 2 * depth

    That formula alone only gives the TOTAL volume at full depth, not
    a stage-storage curve. To get a usable partial-depth table (needed
    to merge into a basin's stage-storage curve, not just report one
    number), this assumes the swale's plan-view area grows linearly
    with depth between bottom_area and top_area, then integrates that:

        Area(d) = bottom_area + (top_area - bottom_area) * (d / max_depth)
        V(d)    = integral of Area from 0 to d
                = bottom_area*d + (top_area - bottom_area)*d^2/(2*max_depth)

    This reduces to exactly the average-end-area formula at d=max_depth
    (verified: bottom*D + (top-bottom)*D/2 = D*(top+bottom)/2), so the
    total volume matches the report's method exactly; the intermediate
    points are a reasonable engineering interpolation, not independently
    verified against a report (no source gives partial-depth swale
    volumes to check against).

    Triangular swales (bottom_area = 0) are just this formula's
    bottom_area=0 case: V(d) = top_area * d^2 / (2*max_depth), matching
    V = 0.5 * top_area * depth at full depth.
    """
    if max_depth_ft <= 0:
        raise SwaleError("max_depth_ft must be > 0")
    table = []
    for i in range(n_points):
        d = max_depth_ft * i / (n_points - 1)
        volume = bottom_area_sqft * d + (top_area_sqft - bottom_area_sqft) * d * d / (2 * max_depth_ft)
        table.append((d, volume))
    return table


def make_area_based_swale(
    name: str, bottom_elevation_ft: float, top_elevation_ft: float,
    top_area_sqft: float, bottom_area_sqft: float = 0.0,
    include_in_basin_storage: bool = True, n_points: int = 11,
) -> "Swale":
    """Builds a Swale using the average-end-area (trapezoidal) or
    triangular (bottom_area_sqft=0) plan-view method instead of a
    cross-section-times-length calculation. Internally reuses Swale's
    irregular_depth_area mechanism with length_ft=1.0, so the "area"
    values ARE the volumes directly (Swale.storage_cuft = area *
    length, and length=1 makes that identity hold)."""
    max_depth = top_elevation_ft - bottom_elevation_ft
    table = area_based_depth_volume_table(bottom_area_sqft, top_area_sqft, max_depth, n_points)
    return Swale(
        name=name, bottom_elevation_ft=bottom_elevation_ft, top_elevation_ft=top_elevation_ft,
        length_ft=1.0, irregular_depth_area=table, include_in_basin_storage=include_in_basin_storage,
    )


def merge_swales_into_basin_curve(
    base_curve: StageStorageCurve,
    swales: List[Swale],
    n_points: int = 25,
) -> StageStorageCurve:
    """
    Combines one or more swales' storage with an existing basin
    stage-storage curve by summing storage at a common set of stages
    spanning the full range of all inputs (Section 17: TOTAL STORAGE
    must reflect every contributing component without duplication).

    Only swales with include_in_basin_storage=True are merged.
    """
    active = [s for s in swales if s.include_in_basin_storage]
    if not active:
        return base_curve

    lo = min([base_curve.min_stage] + [s.bottom_elevation_ft for s in active])
    hi = max([base_curve.max_stage] + [s.top_elevation_ft for s in active])

    stages = [lo + (hi - lo) * i / (n_points - 1) for i in range(n_points)]
    combined = []
    for stage in stages:
        total = base_curve.storage_at_stage(min(stage, base_curve.max_stage)) \
            if stage <= base_curve.max_stage else base_curve.storage_at_stage(base_curve.max_stage)
        for s in active:
            depth = stage - s.bottom_elevation_ft
            depth = max(0.0, min(depth, s.max_depth_ft))
            total += s.storage_acre_ft(depth)
        combined.append((stage, total))

    # enforce non-decreasing storage (numerical noise guard)
    for i in range(1, len(combined)):
        if combined[i][1] < combined[i - 1][1]:
            combined[i] = (combined[i][0], combined[i - 1][1])

    return StageStorageCurve(points=combined)
