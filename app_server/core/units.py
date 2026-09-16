"""
core/units.py

Central location for all unit conversions used by the routing engine.
No conversion factor should be hard-coded anywhere else in the codebase.
"""

# ---- Flow / volume conversions -------------------------------------------

CFS_HOUR_TO_ACRE_FT = 0.0826446   # 1 cfs-hr = 0.0826446 acre-ft
ACRE_FT_TO_CFS_HOUR = 1.0 / CFS_HOUR_TO_ACRE_FT

ACRE_FT_TO_CUFT = 43560.0
CUFT_TO_ACRE_FT = 1.0 / ACRE_FT_TO_CUFT

ACRE_IN_TO_CUFT = 3630.0
CUFT_TO_ACRE_IN = 1.0 / ACRE_IN_TO_CUFT

ACRE_FT_TO_ACRE_IN = 12.0
ACRE_IN_TO_ACRE_FT = 1.0 / ACRE_FT_TO_ACRE_IN

GPM_TO_CFS = 1.0 / 448.831        # 1 cfs = 448.831 gpm
CFS_TO_GPM = 448.831

SECONDS_PER_HOUR = 3600.0
MINUTES_PER_HOUR = 60.0


def cfs_to_acre_ft(flow_cfs: float, duration_hours: float) -> float:
    """Volume (acre-ft) produced by a constant flow (cfs) over duration_hours."""
    return flow_cfs * duration_hours * CFS_HOUR_TO_ACRE_FT


def acre_ft_to_cfs(volume_acre_ft: float, duration_hours: float) -> float:
    """Average flow (cfs) required to produce volume_acre_ft over duration_hours."""
    if duration_hours == 0:
        raise ValueError("duration_hours must be nonzero")
    return volume_acre_ft / (duration_hours * CFS_HOUR_TO_ACRE_FT)


def gpm_to_cfs(flow_gpm: float) -> float:
    return flow_gpm * GPM_TO_CFS


def cfs_to_gpm(flow_cfs: float) -> float:
    return flow_cfs * CFS_TO_GPM


def acre_ft_to_cuft(volume_acre_ft: float) -> float:
    return volume_acre_ft * ACRE_FT_TO_CUFT


def cuft_to_acre_ft(volume_cuft: float) -> float:
    return volume_cuft * CUFT_TO_ACRE_FT


def acre_in_to_acre_ft(volume_acre_in: float) -> float:
    return volume_acre_in * ACRE_IN_TO_ACRE_FT


def acre_ft_to_acre_in(volume_acre_ft: float) -> float:
    return volume_acre_ft * ACRE_FT_TO_ACRE_IN
