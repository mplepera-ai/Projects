"""
core/numerical_solver.py

Robust root-finding used to solve the continuity equation at each time
step. Bisection is preferred over Newton's method here because
structure discharge functions can have kinks (weirs/orifices turning
on, wells switching via hysteresis) that make derivatives unreliable.
Numerical reliability > speed (Section 33).
"""

from __future__ import annotations
from typing import Callable, Optional


class SolverError(Exception):
    pass


def bisection(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    tol: float = 1e-6,
    max_iter: int = 200,
    hard_lo: Optional[float] = None,
    hard_hi: Optional[float] = None,
) -> float:
    """Find x in [lo, hi] such that f(x) ~= 0. Requires f(lo) and f(hi)
    to have opposite signs (or one of them ~= 0).

    hard_lo/hard_hi, when given, are physical limits f() must never be
    evaluated outside of (e.g. a stage-storage curve's defined range,
    where f() itself raises if queried past it). If the initial [lo, hi]
    doesn't bracket a root, the bracket is expanded outward but clamped
    to [hard_lo, hard_hi]; if no root is found even at those limits,
    this raises SolverError instead of ever calling f() past them."""
    f_lo = f(lo)
    f_hi = f(hi)

    if abs(f_lo) < tol:
        return lo
    if abs(f_hi) < tol:
        return hi

    if f_lo * f_hi > 0:
        # Expand the bracket outward a bounded number of times before
        # giving up -- this handles cases where the initial guess for
        # the search range was too tight (e.g. rapidly rising stage).
        span = hi - lo
        for _ in range(40):
            lo -= span
            hi += span
            if hard_lo is not None:
                lo = max(lo, hard_lo)
            if hard_hi is not None:
                hi = min(hi, hard_hi)
            f_lo = f(lo)
            f_hi = f(hi)
            if f_lo * f_hi <= 0:
                break
            if hard_lo is not None and hard_hi is not None and lo <= hard_lo and hi >= hard_hi:
                # Already at the physical limits in both directions and
                # still no sign change -- expanding further can't help.
                raise SolverError(
                    f"No root within the physical limits [{hard_lo}, {hard_hi}]: "
                    f"f({lo})={f_lo}, f({hi})={f_hi}."
                )
        else:
            raise SolverError(
                f"Could not bracket a root: f({lo})={f_lo}, f({hi})={f_hi}"
            )

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if abs(f_mid) < tol or (hi - lo) / 2.0 < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def solve_bounded_continuity(
    residual: Callable[[float], float],
    min_stage: float,
    max_stage: float,
    tol: float = 1e-6,
    preferred_stage: Optional[float] = None,
) -> float:
    """Solve a level-pool continuity residual (monotonic non-decreasing in
    stage: more stage means more storage and -- for our structure types --
    outflow that never decreases with stage) over a basin's full physical
    stage range, handling the two ways a normal bisection can legitimately
    fail to bracket a root:

    - residual(min_stage) > 0 too: even at the basin's lowest defined
      stage, the water demanded by continuity is more than the curve can
      represent going any lower. Physically this means a structure (e.g.
      a fixed-capacity well) is asking to discharge more water than the
      basin actually holds plus what's flowing in -- the basin simply runs
      dry partway through the step. The correct stage is the floor
      (min_stage); the *reported* discharge for that step is then
      supply-limited rather than the structure's full rated capacity, but
      solving that refinement is a further step -- for now this returns
      the physically sound stage rather than crashing or running away.

    - residual(max_stage) < 0 too: the reverse case -- storage demanded
      exceeds what the curve defines even at its highest point. This is a
      genuine "storm overwhelms the defined pond" condition and should be
      surfaced clearly, not silently clamped.

    preferred_stage, when given (the sweep's incoming stage estimate --
    typically the previous time step's converged stage), is checked FIRST:
    if it already satisfies the residual within tol, it's returned as-is
    instead of running bisection at all. This matters whenever the curve
    has a flat or near-flat storage region (storage barely changes over a
    stage range) -- there, many different stages all satisfy continuity
    equally well, and plain bisection's tie-break is an arbitrary artifact
    of where it happens to sample the bracket, not a physically meaningful
    choice. A well or pump whose turn-on/turn-off thresholds sit inside
    such a region can otherwise flip on and off every single time step as
    consecutive solves land on different-but-equally-valid tied stages,
    producing a sustained spurious average discharge that the downstream
    "available water" conservation cap cannot catch, because that cap is
    itself derived from the same (self-consistent, just not physically
    meaningful) residual equation. Preferring minimal movement from the
    previous stage is the correct tie-break: nothing in the physics
    justifies jumping to a different tied stage when the current one still
    balances continuity, so a stable pond level (and with it, whatever
    on/off state hysteresis already committed to) is what's reported."""
    if preferred_stage is not None and min_stage <= preferred_stage <= max_stage:
        if abs(residual(preferred_stage)) < tol:
            return preferred_stage

    f_lo = residual(min_stage)
    f_hi = residual(max_stage)

    if f_lo * f_hi <= 0:
        return bisection(residual, min_stage, max_stage, tol=tol,
                          hard_lo=min_stage, hard_hi=max_stage)

    if f_lo > 0:
        # Demanded storage is below the curve's floor at every stage --
        # the basin/structures want to remove more water than exists.
        return min_stage

    raise SolverError(
        "CRITICAL ERROR: Storm demands more storage than the defined "
        f"stage-storage curve provides, even at its highest point "
        f"(max defined stage={max_stage:.3f}). Extend the curve or check "
        "why inflow is so large relative to the pond and outlet capacity."
    )
