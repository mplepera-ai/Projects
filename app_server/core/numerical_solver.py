"""
core/numerical_solver.py

Robust root-finding used to solve the continuity equation at each time
step. Bisection is preferred over Newton's method here because
structure discharge functions can have kinks (weirs/orifices turning
on, wells switching via hysteresis) that make derivatives unreliable.
Numerical reliability > speed (Section 33).
"""

from __future__ import annotations
from typing import Callable


class SolverError(Exception):
    pass


def bisection(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    tol: float = 1e-6,
    max_iter: int = 200,
) -> float:
    """Find x in [lo, hi] such that f(x) ~= 0. Requires f(lo) and f(hi)
    to have opposite signs (or one of them ~= 0)."""
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
            f_lo = f(lo)
            f_hi = f(hi)
            if f_lo * f_hi <= 0:
                break
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
