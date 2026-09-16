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
from typing import List
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

    def initial_storage_acre_ft(self) -> float:
        return self.stage_storage.storage_at_stage(self.initial_stage_ft)

    def enabled_structures(self) -> List[Structure]:
        return [s for s in self.structures if s.enabled]
