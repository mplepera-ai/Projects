"""
storage/pond.py

Pond (wet or dry retention/detention) footprint sizing helper -- a
quick "how big does the pond need to be" planning check, companion to
the exfiltration trench width/length suggestion in
storage/exfiltration.py. Requested as future-improvement item #4
("Pond/Trench Size auto suggestion").

Uses the standard prismoidal (Simpson's-rule) formula for a pond with
uniform side slopes, the same formula used for a truncated-pyramid
excavation volume:

    V = D/6 * (A_bottom + A_top + 4*A_mid)

Given a bottom length/width ratio, uniform side slope z
(horizontal:vertical), and target depth, the top and mid dimensions
grow by 2*z*D and z*D respectively off the bottom dimensions. This
module solves (by bisection on bottom width) for the bottom footprint
that provides at least the required volume.

This is a preliminary/planning-level footprint estimate, not a
substitute for the project's actual stage-storage curve -- once a
footprint is chosen, it should still be modeled as a real basin (or a
curve import) for the routing engine to see it.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict


class PondSizingError(Exception):
    pass


def pond_volume_cuft(
    bottom_length_ft: float, bottom_width_ft: float, depth_ft: float, side_slope_h_per_v: float,
) -> float:
    """Volume of a truncated-pyramid pond with uniform side slope z
    (horizontal:vertical), via the prismoidal formula."""
    if bottom_length_ft <= 0 or bottom_width_ft <= 0:
        raise PondSizingError("Pond bottom dimensions must be > 0.")
    if depth_ft <= 0:
        raise PondSizingError("Pond depth must be > 0.")
    if side_slope_h_per_v < 0:
        raise PondSizingError("Side slope must be >= 0.")

    z = side_slope_h_per_v
    top_l = bottom_length_ft + 2 * z * depth_ft
    top_w = bottom_width_ft + 2 * z * depth_ft
    mid_l = bottom_length_ft + z * depth_ft
    mid_w = bottom_width_ft + z * depth_ft

    a_bottom = bottom_length_ft * bottom_width_ft
    a_top = top_l * top_w
    a_mid = mid_l * mid_w
    return depth_ft / 6.0 * (a_bottom + a_top + 4 * a_mid)


@dataclass
class PondSizeSuggestion:
    bottom_length_ft: float
    bottom_width_ft: float
    top_length_ft: float
    top_width_ft: float
    mid_length_ft: float
    mid_width_ft: float
    depth_ft: float
    side_slope_h_per_v: float
    length_to_width_ratio: float
    required_volume_cuft: float
    volume_provided_cuft: float
    top_footprint_sqft: float
    bottom_footprint_sqft: float

    def as_dict(self) -> dict:
        return asdict(self)


def suggest_pond_footprint(
    required_volume_cuft: float,
    depth_ft: float,
    side_slope_h_per_v: float = 4.0,
    length_to_width_ratio: float = 1.5,
    max_iterations: int = 200,
) -> PondSizeSuggestion:
    """
    Solves for the bottom footprint (bottom length and width, holding
    the length/width ratio fixed) that provides at least
    required_volume_cuft at the given depth and side slope, via
    bisection on bottom width. Always returns a footprint whose
    provided volume is >= required_volume_cuft (never undersized).
    """
    if required_volume_cuft <= 0:
        raise PondSizingError("Required volume must be > 0.")
    if depth_ft <= 0:
        raise PondSizingError("Depth must be > 0.")
    if side_slope_h_per_v < 0:
        raise PondSizingError("Side slope must be >= 0.")
    if length_to_width_ratio <= 0:
        raise PondSizingError("Length/width ratio must be > 0.")

    def volume_at_width(w0: float) -> float:
        l0 = w0 * length_to_width_ratio
        return pond_volume_cuft(l0, w0, depth_ft, side_slope_h_per_v)

    # Bracket a width whose volume exceeds the target, doubling from a
    # small starting point.
    lo, hi = 0.1, 10.0
    while volume_at_width(hi) < required_volume_cuft:
        hi *= 2.0
        if hi > 1.0e6:
            raise PondSizingError(
                "Could not find a pond footprint large enough for this required volume at the "
                "given depth -- check the inputs (a shallower target volume, or a greater depth, "
                "will produce a smaller/solvable footprint)."
            )

    # Invariant: volume_at_width(lo) < required <= volume_at_width(hi).
    # Narrow the bracket until it's tight enough, then take hi -- this
    # keeps the "never undersized" guarantee exact regardless of where
    # the tolerance check would otherwise land relative to required
    # (an early-exit on |v - required| <= tolerance can accept a mid
    # that's slightly UNDER required, which would silently undersize
    # the pond -- narrowing the bracket and always taking hi avoids
    # that).
    for _ in range(max_iterations):
        if hi - lo <= 1e-6:
            break
        mid = (lo + hi) / 2.0
        v = volume_at_width(mid)
        if v < required_volume_cuft:
            lo = mid
        else:
            hi = mid

    w0 = hi
    l0 = w0 * length_to_width_ratio
    v_final = volume_at_width(w0)
    z = side_slope_h_per_v
    top_l = l0 + 2 * z * depth_ft
    top_w = w0 + 2 * z * depth_ft
    mid_l = l0 + z * depth_ft
    mid_w = w0 + z * depth_ft

    return PondSizeSuggestion(
        bottom_length_ft=l0,
        bottom_width_ft=w0,
        top_length_ft=top_l,
        top_width_ft=top_w,
        mid_length_ft=mid_l,
        mid_width_ft=mid_w,
        depth_ft=depth_ft,
        side_slope_h_per_v=z,
        length_to_width_ratio=length_to_width_ratio,
        required_volume_cuft=required_volume_cuft,
        volume_provided_cuft=v_final,
        top_footprint_sqft=top_l * top_w,
        bottom_footprint_sqft=l0 * w0,
    )
