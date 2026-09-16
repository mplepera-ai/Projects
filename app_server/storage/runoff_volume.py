"""
storage/runoff_volume.py

The SCS/NRCS storage-based runoff equation, used in South Florida
practice to size the INCREASE in retention/exfiltration volume needed
between existing and proposed conditions (distinct from the SBUH flood
-routing method in hydrology/sbuh.py, which computes a full hydrograph
for stage routing). This method instead answers "how much runoff
volume results from a given rainfall depth over a given soil storage
capacity" as a single number -- exactly the calculation South Florida
drainage reports use to size a swale/exfiltration trench against the
net increase in runoff volume caused by adding impervious area.

    S = (Pervious Area / Site Area) * Compacted Soil Storage
    R = (P - 0.2*S)^2 / (P + 0.8*S)      [inches of runoff]
    V = Area * R / 12                     [cubic feet, Area in sqft]

Both S and P must be in the same units (inches here).
"""

from __future__ import annotations
from dataclasses import dataclass


class RunoffVolumeError(Exception):
    pass


def effective_soil_storage_inches(pervious_area_sqft: float, site_area_sqft: float,
                                   compacted_soil_storage_inches: float) -> float:
    if site_area_sqft <= 0:
        raise RunoffVolumeError("site_area_sqft must be > 0")
    if pervious_area_sqft < 0 or pervious_area_sqft > site_area_sqft:
        raise RunoffVolumeError("pervious_area_sqft must be between 0 and site_area_sqft")
    return (pervious_area_sqft / site_area_sqft) * compacted_soil_storage_inches


def scs_storage_runoff_inches(rainfall_depth_inches: float, soil_storage_inches: float) -> float:
    """R = (P - 0.2S)^2 / (P + 0.8S). Returns 0 if P <= 0.2S (no runoff
    generated below the initial abstraction)."""
    P, S = rainfall_depth_inches, soil_storage_inches
    if P <= 0.2 * S:
        return 0.0
    return (P - 0.2 * S) ** 2 / (P + 0.8 * S)


@dataclass
class RunoffVolumeResult:
    site_area_sqft: float
    pervious_area_sqft: float
    rainfall_depth_inches: float
    compacted_soil_storage_inches: float
    effective_storage_inches: float
    runoff_inches: float
    runoff_volume_cuft: float


def compute_runoff_volume(
    site_area_sqft: float,
    pervious_area_sqft: float,
    rainfall_depth_inches: float,
    compacted_soil_storage_inches: float,
) -> RunoffVolumeResult:
    S = effective_soil_storage_inches(pervious_area_sqft, site_area_sqft, compacted_soil_storage_inches)
    R = scs_storage_runoff_inches(rainfall_depth_inches, S)
    V = site_area_sqft * R / 12.0
    return RunoffVolumeResult(
        site_area_sqft=site_area_sqft, pervious_area_sqft=pervious_area_sqft,
        rainfall_depth_inches=rainfall_depth_inches,
        compacted_soil_storage_inches=compacted_soil_storage_inches,
        effective_storage_inches=S, runoff_inches=R, runoff_volume_cuft=V,
    )
