"""
core/interpolation.py

Stage-storage relationship: a single authoritative curve per basin
(Section 16 of the spec). Continuous linear interpolation between
points; no extrapolation above the max stage without an explicit
override.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple


class StageStorageError(Exception):
    """Raised for invalid stage-storage tables or out-of-range lookups."""


@dataclass
class StageStorageCurve:
    # list of (stage_ft, storage_acre_ft), must be strictly increasing in
    # both stage and storage (non-decreasing storage is allowed at a
    # vertical wall, but stage must strictly increase).
    points: List[Tuple[float, float]]
    allow_extrapolation: bool = False

    def __post_init__(self):
        if len(self.points) < 2:
            raise StageStorageError("Stage-storage curve requires at least 2 points.")
        pts = sorted(self.points, key=lambda p: p[0])
        for i in range(1, len(pts)):
            if pts[i][0] <= pts[i - 1][0]:
                raise StageStorageError(
                    f"Stage values must strictly increase: {pts[i-1][0]} -> {pts[i][0]}"
                )
            if pts[i][1] < pts[i - 1][1]:
                raise StageStorageError(
                    f"Storage may not decrease with stage: "
                    f"{pts[i-1][1]} at {pts[i-1][0]} -> {pts[i][1]} at {pts[i][0]}"
                )
        self.points = pts

    @property
    def min_stage(self) -> float:
        return self.points[0][0]

    @property
    def max_stage(self) -> float:
        return self.points[-1][0]

    def storage_at_stage(self, stage_ft: float) -> float:
        """Linear interpolation. Raises if above max_stage unless
        allow_extrapolation is set (then extrapolates the last segment)."""
        pts = self.points

        if stage_ft <= pts[0][0]:
            if stage_ft < pts[0][0] and not self.allow_extrapolation:
                # Below the bottom of the curve: treat as zero-storage floor,
                # this is normal (basin below its lowest defined stage).
                return pts[0][1]
            return pts[0][1]

        if stage_ft >= pts[-1][0]:
            if stage_ft > pts[-1][0] and not self.allow_extrapolation:
                raise StageStorageError(
                    "CRITICAL ERROR: Calculated water surface exceeds "
                    f"defined storage range (stage={stage_ft:.3f}, "
                    f"max defined stage={pts[-1][0]:.3f})."
                )
            # extrapolate using the last segment's slope
            (s1, v1), (s2, v2) = pts[-2], pts[-1]
            slope = (v2 - v1) / (s2 - s1)
            return v2 + slope * (stage_ft - s2)

        # normal interpolation
        for i in range(1, len(pts)):
            s1, v1 = pts[i - 1]
            s2, v2 = pts[i]
            if s1 <= stage_ft <= s2:
                if s2 == s1:
                    return v2
                frac = (stage_ft - s1) / (s2 - s1)
                return v1 + frac * (v2 - v1)

        raise StageStorageError("Unreachable: stage interpolation fell through.")

    def stage_at_storage(self, storage_acre_ft: float, tol: float = 1e-9) -> float:
        """Inverse lookup via bisection (curve is monotonic non-decreasing)."""
        pts = self.points
        if storage_acre_ft <= pts[0][1]:
            return pts[0][0]
        if storage_acre_ft >= pts[-1][1]:
            if not self.allow_extrapolation:
                raise StageStorageError(
                    "CRITICAL ERROR: Required storage exceeds defined "
                    f"storage range (storage={storage_acre_ft:.4f} ac-ft, "
                    f"max defined storage={pts[-1][1]:.4f} ac-ft)."
                )
            (s1, v1), (s2, v2) = pts[-2], pts[-1]
            slope = (s2 - s1) / (v2 - v1) if v2 != v1 else 0.0
            return s2 + slope * (storage_acre_ft - v2)

        lo, hi = pts[0][0], pts[-1][0]
        for _ in range(200):
            mid = (lo + hi) / 2.0
            v = self.storage_at_stage(mid)
            if abs(v - storage_acre_ft) < tol:
                return mid
            if v < storage_acre_ft:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0


def validate_distribution(times: List[float], cum_fractions: List[float]) -> None:
    """QA checks from Section 11: first=0, last=1, non-decreasing, in range."""
    if len(times) != len(cum_fractions):
        raise StageStorageError("Distribution time/fraction length mismatch.")
    if abs(cum_fractions[0] - 0.0) > 1e-9:
        raise StageStorageError("Rainfall distribution must start at cumulative fraction 0.")
    if abs(cum_fractions[-1] - 1.0) > 1e-6:
        raise StageStorageError("Rainfall distribution must end at cumulative fraction 1.000.")
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            raise StageStorageError("Distribution times must strictly increase.")
        if cum_fractions[i] < cum_fractions[i - 1]:
            raise StageStorageError("Distribution cumulative fraction may not decrease.")
    for f in cum_fractions:
        if f < 0.0 or f > 1.0:
            raise StageStorageError("Distribution fraction out of [0,1] range.")
