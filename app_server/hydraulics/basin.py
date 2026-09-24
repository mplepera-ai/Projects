"""
hydraulics/basin.py

A Basin ties together: its stage-storage curve, its inflow hydrograph
(from SBUH), and the structures attached to it. Interbasin connections
are represented at the routing-engine level (Section 33 step 3), not
inside the Basin object itself, to keep the basin a pure data/interface
container per Section 71 (don't mix calculation and presentation, and
by extension, don't mix single-basin and multi-basin concerns).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from core.interpolation import StageStorageCurve
from hydraulics.structures import Structure


@dataclass
class Basin:
    basin_id: str
    name: str
    area_acres: float
    ground_storage_inches: float
    time_of_concentration_hours: float
    initial_stage_ft: float
    stage_storage: StageStorageCurve
    structures: List[Structure] = field(default_factory=list)
    # Optional perimeter berm/containment elevation for this basin. Not a
    # hydraulic structure -- doesn't route flow or affect the water
    # budget -- it's a separate site-containment check: is the peak
    # stage kept below the berm crest. No universal regulatory minimum
    # freeboard was found for this (Miami-Dade DERM's berm/perimeter
    # containment requirement doesn't specify one, and it varies by
    # jurisdiction/reviewer), so this is reported as a plain output
    # value (berm elevation minus peak stage) rather than checked
    # against a required minimum; the only automated check is whether
    # the berm is overtopped at all (freeboard < 0). Left None, no
    # freeboard is computed or reported for this basin.
    berm_elevation_ft: Optional[float] = None

    def initial_storage_acre_ft(self) -> float:
        return self.stage_storage.storage_at_stage(self.initial_stage_ft)

    def enabled_structures(self) -> List[Structure]:
        return [s for s in self.structures if s.enabled]
